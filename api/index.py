from http.server import BaseHTTPRequestHandler
import os, json, urllib.request, urllib.parse, secrets, html
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

TOKEN = os.getenv('BOT_TOKEN', '')
OWNER = int(os.getenv('OWNER_TELEGRAM_ID', '0') or 0)
RURL = (os.getenv('KV_REST_API_URL') or os.getenv('UPSTASH_REDIS_REST_URL') or '').rstrip('/')
RTOK = os.getenv('KV_REST_API_TOKEN') or os.getenv('UPSTASH_REDIS_REST_TOKEN') or ''
TZ = os.getenv('DEFAULT_TIMEZONE', 'Europe/Moscow')

Q = [
    'Не были ли мы в течение дня злобными?',
    'Не были ли мы в течение дня эгоистичными?',
    'Не были ли мы в течение дня нечестными?',
    'Испытывали ли мы в течение дня страх?',
    'Должны ли мы извиниться перед кем-то?',
    'Может быть, мы что-то затаили про себя?',
    'Есть ли что-то, что следует обсудить с кем-либо?',
    'Проявляли ли мы в течение дня любовь и доброту ко всем окружающим?',
    'Что мы могли бы сделать лучше?',
    'Думали ли мы в течение дня в основном только о себе?',
    'Думали ли мы о том, что можем сделать для других, о нашем вкладе в общее течение жизни?',
    'Благодарности Богу за сегодня — перечисли не меньше 10 примеров.'
]


def req(url, data=None, headers=None):
    body = None if data is None else json.dumps(data).encode()
    request = urllib.request.Request(url, data=body, headers=headers or {'Content-Type': 'application/json'})
    with urllib.request.urlopen(request, timeout=20) as response:
        return json.loads(response.read().decode())


def tg(method, payload):
    return req(f'https://api.telegram.org/bot{TOKEN}/{method}', payload)


def send(chat_id, text, keyboard=None):
    payload = {'chat_id': chat_id, 'text': text, 'parse_mode': 'HTML'}
    if keyboard:
        payload['reply_markup'] = keyboard
    return tg('sendMessage', payload)


def rc(*parts):
    if not RURL or not RTOK:
        raise RuntimeError('Redis not configured')
    url = RURL + '/' + '/'.join(urllib.parse.quote(str(x), safe='') for x in parts)
    result = req(url, headers={'Authorization': 'Bearer ' + RTOK})
    return result.get('result')


def gj(key, default=None):
    value = rc('GET', key)
    return default if value is None else json.loads(value)


def sj(key, value, ttl=None):
    args = ['SET', key, json.dumps(value, ensure_ascii=False)]
    if ttl:
        args += ['EX', ttl]
    return rc(*args)


def user_key(uid):
    return f'u:{uid}'


def session_key(uid):
    return f's:{uid}'


def local_now(user=None):
    tz_name = (user or {}).get('timezone', TZ)
    return datetime.now(ZoneInfo(tz_name))


def ensure(user, sponsor=None):
    key = user_key(user['id'])
    old = gj(key, {}) or {}
    current = {
        **old,
        'id': user['id'],
        'name': ' '.join(x for x in [user.get('first_name', ''), user.get('last_name', '')] if x).strip() or str(user['id']),
        'username': user.get('username'),
        'timezone': old.get('timezone', TZ),
        'joined_at': old.get('joined_at', datetime.now(timezone.utc).isoformat()),
    }
    if sponsor:
        current['sponsor_id'] = int(sponsor)
    sj(key, current)
    rc('SADD', 'users', user['id'])
    return current


def menu(uid):
    rows = [
        [{'text': '🌙 Вечерняя часть'}],
        [{'text': '⏰ Напоминание'}, {'text': '🕰 Часовой пояс'}]
    ]
    if uid == OWNER:
        rows += [
            [{'text': '🛠 Админка'}],
            [{'text': '👥 Подопечные'}, {'text': '📊 Сегодня'}],
            [{'text': '🔗 Пригласить подопечного'}, {'text': '📣 Напомнить неответившим'}]
        ]
    return {'keyboard': rows, 'resize_keyboard': True}


def admin_keyboard():
    return {'inline_keyboard': [
        [{'text': '📊 Кто ответил сегодня', 'callback_data': 'admin:today'}],
        [{'text': '👥 Список подопечных', 'callback_data': 'admin:users'}],
        [{'text': '📣 Напомнить неответившим', 'callback_data': 'admin:remind'}],
        [{'text': '📩 Последние отчёты', 'callback_data': 'admin:last'}]
    ]}


def ask(uid, index):
    send(uid, f'<b>Вопрос {index + 1} из {len(Q)}</b>\n\n{html.escape(Q[index])}', {
        'inline_keyboard': [[{'text': 'Отменить', 'callback_data': 'cancel'}]]
    })


def begin(uid):
    sj(session_key(uid), {'mode': 'evening', 'index': 0, 'answers': []}, 86400)
    send(uid, '🌙 <b>Вечерняя часть 11-го Шага</b>\n\nКогда мы ложимся спать, мы конструктивно пересматриваем прожитый день.')
    ask(uid, 0)


def completion_key(uid, date):
    return f'done:{date}:{uid}'


def save_report(uid, report):
    user = gj(user_key(uid), {}) or {}
    now = local_now(user)
    date = now.strftime('%Y-%m-%d')
    stamp = now.strftime('%d.%m.%Y %H:%M')
    payload = {'user_id': uid, 'name': user.get('name', str(uid)), 'date': date, 'time': stamp, 'answers': report['answers']}
    sj(f'report:{uid}:{date}', payload, 60 * 60 * 24 * 180)
    sj(completion_key(uid, date), {'time': stamp}, 60 * 60 * 24 * 180)
    rc('LPUSH', 'reports:latest', json.dumps(payload, ensure_ascii=False))
    rc('LTRIM', 'reports:latest', 0, 99)
    return payload


def format_report(payload):
    lines = [f"<b>📩 Вечерний отчёт — {html.escape(payload.get('name', ''))}</b>", f"<i>{html.escape(payload.get('time', ''))}</i>", '']
    for i, (question, answer) in enumerate(zip(Q, payload.get('answers', [])), 1):
        lines += [f'<b>{i}. {html.escape(question)}</b>', html.escape(answer or '—'), '']
    return '\n'.join(lines)


def finish(uid, session):
    payload = save_report(uid, {'answers': session['answers']})
    text = format_report(payload)
    user = gj(user_key(uid), {}) or {}
    recipients = set()
    if user.get('sponsor_id'):
        recipients.add(int(user['sponsor_id']))
    if OWNER and uid != OWNER:
        recipients.add(OWNER)
    for recipient in recipients:
        send(recipient, text, {'inline_keyboard': [[{'text': '💬 Написать подопечному', 'url': f'tg://user?id={uid}'}]]})
    send(uid, 'Спасибо. Вечерняя часть завершена. 🙏\n\nОтветы сохранены и отправлены спонсору.')
    rc('DEL', session_key(uid))


def sponsee_ids():
    result = []
    for item in rc('SMEMBERS', 'users') or []:
        user = gj(user_key(item), {}) or {}
        if user.get('sponsor_id') == OWNER or (OWNER and int(item) != OWNER):
            result.append(int(item))
    return sorted(set(result))


def today_status():
    now = datetime.now(ZoneInfo(TZ))
    date = now.strftime('%Y-%m-%d')
    answered, missing = [], []
    for uid in sponsee_ids():
        user = gj(user_key(uid), {}) or {}
        item = {'id': uid, 'name': user.get('name', str(uid)), 'username': user.get('username')}
        done = gj(completion_key(uid, date))
        if done:
            item['time'] = done.get('time', '')
            answered.append(item)
        else:
            missing.append(item)
    return date, answered, missing


def admin_today(uid):
    date, answered, missing = today_status()
    lines = [f'<b>📊 Сегодня — {date}</b>', '', f'✅ Ответили: <b>{len(answered)}</b>']
    lines += [f"• {html.escape(x['name'])} — {html.escape(x.get('time', ''))}" for x in answered]
    lines += ['', f'🕓 Не ответили: <b>{len(missing)}</b>']
    lines += [f"• {html.escape(x['name'])}" for x in missing]
    send(uid, '\n'.join(lines))


def admin_users(uid):
    users = []
    for sid in sponsee_ids():
        user = gj(user_key(sid), {}) or {}
        username = '@' + user['username'] if user.get('username') else 'без username'
        users.append(f"• <b>{html.escape(user.get('name', str(sid)))}</b> — {html.escape(username)}\n  <code>{sid}</code>")
    send(uid, '<b>👥 Подопечные</b>\n\n' + ('\n'.join(users) if users else 'Пока никого.'))


def remind_missing(uid):
    _, _, missing = today_status()
    sent = 0
    for item in missing:
        try:
            send(item['id'], '🌙 Напоминание от спонсора: самое время спокойно пройти вечернюю часть.', {
                'inline_keyboard': [[{'text': 'Начать', 'callback_data': 'begin'}]]
            })
            sent += 1
        except Exception:
            pass
    send(uid, f'📣 Напоминание отправлено: <b>{sent}</b>.')


def admin_last(uid):
    raw = rc('LRANGE', 'reports:latest', 0, 9) or []
    if not raw:
        return send(uid, 'Отчётов пока нет.')
    lines = ['<b>📩 Последние отчёты</b>', '']
    for value in raw:
        item = json.loads(value)
        lines.append(f"• {html.escape(item.get('name', ''))} — {html.escape(item.get('time', ''))}")
    send(uid, '\n'.join(lines))


def handle_text(message):
    uid = message['from']['id']
    text_value = message.get('text', '').strip()
    ensure(message['from'])
    session = gj(session_key(uid))

    if session and session.get('mode') == 'evening' and not text_value.startswith('/'):
        session['answers'].append(text_value)
        session['index'] += 1
        if session['index'] >= len(Q):
            return finish(uid, session)
        sj(session_key(uid), session, 86400)
        return ask(uid, session['index'])

    if session and session.get('mode') == 'reminder':
        try:
            datetime.strptime(text_value, '%H:%M')
        except ValueError:
            return send(uid, 'Формат времени: <code>22:30</code>.')
        user = gj(user_key(uid), {}) or {}
        user.update({'reminder_time': text_value, 'reminder_enabled': True})
        sj(user_key(uid), user)
        rc('DEL', session_key(uid))
        return send(uid, f'✅ Напоминание установлено на <b>{text_value}</b>.')

    if text_value.startswith('/start'):
        parts = text_value.split(maxsplit=1)
        sponsor = None
        if len(parts) > 1 and parts[1].startswith('invite_'):
            token = parts[1][7:]
            sponsor = rc('GET', 'invite:' + token)
            if sponsor:
                rc('DEL', 'invite:' + token)
        ensure(message['from'], int(sponsor) if sponsor else None)
        return send(uid, 'Привет. Я помогу пройти вечернюю часть 11-го Шага.', menu(uid))

    if text_value in ('/evening', '🌙 Вечерняя часть'):
        return begin(uid)
    if text_value in ('/reminder', '⏰ Напоминание'):
        sj(session_key(uid), {'mode': 'reminder'}, 3600)
        return send(uid, 'Во сколько напоминать? Формат: <code>22:30</code>.')
    if text_value in ('/timezone', '🕰 Часовой пояс'):
        zones = ['Europe/Moscow', 'Europe/Helsinki', 'Asia/Yekaterinburg', 'Asia/Novosibirsk', 'Asia/Vladivostok']
        return send(uid, 'Выбери часовой пояс:', {'inline_keyboard': [[{'text': z, 'callback_data': 'tz:' + z}] for z in zones]})
    if text_value in ('/invite', '🔗 Пригласить подопечного') and uid == OWNER:
        token = secrets.token_urlsafe(8)
        rc('SET', 'invite:' + token, uid, 'EX', 604800)
        username = tg('getMe', {})['result']['username']
        return send(uid, f'<code>https://t.me/{username}?start=invite_{token}</code>')
    if text_value in ('/admin', '🛠 Админка') and uid == OWNER:
        return send(uid, '<b>🛠 Панель спонсора</b>\n\nВыбери действие:', admin_keyboard())
    if text_value in ('/today', '📊 Сегодня') and uid == OWNER:
        return admin_today(uid)
    if text_value in ('/sponsees', '👥 Подопечные') and uid == OWNER:
        return admin_users(uid)
    if text_value in ('/remind_missing', '📣 Напомнить неответившим') and uid == OWNER:
        return remind_missing(uid)
    if text_value == '/last' and uid == OWNER:
        return admin_last(uid)
    return send(uid, 'Выбери действие.', menu(uid))


def handle_callback(callback):
    uid = callback['from']['id']
    data = callback.get('data', '')
    tg('answerCallbackQuery', {'callback_query_id': callback['id']})
    if data == 'cancel':
        rc('DEL', session_key(uid))
        return send(uid, 'Опрос отменён.')
    if data == 'begin':
        return begin(uid)
    if data.startswith('tz:'):
        user = gj(user_key(uid), {}) or {}
        user['timezone'] = data[3:]
        sj(user_key(uid), user)
        return send(uid, '✅ Часовой пояс сохранён.')
    if uid == OWNER and data == 'admin:today':
        return admin_today(uid)
    if uid == OWNER and data == 'admin:users':
        return admin_users(uid)
    if uid == OWNER and data == 'admin:remind':
        return remind_missing(uid)
    if uid == OWNER and data == 'admin:last':
        return admin_last(uid)


class handler(BaseHTTPRequestHandler):
    def out(self, code=200, obj=None):
        self.send_response(code)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.end_headers()
        self.wfile.write(json.dumps(obj or {'ok': True}, ensure_ascii=False).encode())

    def do_GET(self):
        query = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        try:
            if 'setup' in query:
                host = self.headers.get('x-forwarded-host') or self.headers.get('host')
                return self.out(200, tg('setWebhook', {'url': 'https://' + host + '/api/index'}))
            if 'health' in query:
                return self.out(200, {'ok': True, 'token': bool(TOKEN), 'owner': bool(OWNER), 'redis': bool(RURL and RTOK)})
            return self.out(200, {'ok': True, 'service': 'EveryEveningBot'})
        except Exception as exc:
            return self.out(500, {'ok': False, 'error': str(exc)})

    def do_POST(self):
        try:
            length = int(self.headers.get('content-length', '0'))
            update = json.loads(self.rfile.read(length) or '{}')
            if 'message' in update:
                handle_text(update['message'])
            elif 'callback_query' in update:
                handle_callback(update['callback_query'])
            return self.out()
        except Exception as exc:
            return self.out(500, {'ok': False, 'error': str(exc)})
