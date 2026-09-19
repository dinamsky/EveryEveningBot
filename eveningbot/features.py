"""Read-only history and small settings. Dependencies are explicit; no monkey patching."""
import hashlib
import html
import json
import re
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from eveningbot import library

FLOW_MODES = ('morning', 'evening', 'tenth')
PAGE_SIZE = 6
TITLES = {'morning': 'Утро', 'evening': 'Вечер', 'tenth': '10-й Шаг'}
ZONES = ('Europe/Moscow', 'Europe/Helsinki', 'Europe/Kaliningrad', 'Europe/Samara',
         'Asia/Yekaterinburg', 'Asia/Novosibirsk', 'Asia/Irkutsk', 'Asia/Vladivostok')


def fingerprint(payload: dict) -> str:
    data = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
    return hashlib.sha256(data.encode()).hexdigest()[:20]


def records(api, uid: int) -> list[dict]:
    """Bounded read of the existing list. No migration, TTL change, or DB writes."""
    items, seen = [], set()
    for raw in api.rc('LRANGE', f'entries:{uid}', 0, 199) or []:
        try:
            entry = json.loads(raw)
            if not isinstance(entry, dict) or int(entry.get('uid', 0)) != uid:
                continue
            if entry.get('mode') not in FLOW_MODES or not isinstance(entry.get('answers'), list):
                continue
            key = fingerprint(entry)
            if key not in seen:
                seen.add(key)
                items.append(entry)
        except (ValueError, TypeError):
            continue
    return items


def can_read(api, viewer: int, subject: int) -> bool:
    if viewer == subject:
        return True
    if viewer != api.OWNER or not api.OWNER:
        return False
    user = api.gj(api.ukey(subject), {}) or {}
    try:
        return int(user.get('sponsor_id', 0)) == api.OWNER
    except (TypeError, ValueError):
        return False


def history(api, viewer: int, subject: int | None = None, page: int = 0):
    subject = viewer if subject is None else subject
    if not can_read(api, viewer, subject):
        return api.send(viewer, 'Нет доступа к этим записям. Подопечный должен быть подключён по приглашению.')
    entries = records(api, subject)
    if not entries:
        return api.send(viewer, 'Сохранённых записей пока нет.')
    max_page = (len(entries) - 1) // PAGE_SIZE
    page = max(0, min(page, max_page))
    subset = entries[page * PAGE_SIZE:(page + 1) * PAGE_SIZE]
    rows = [[{'text': f"{p.get('time', p.get('date', ''))} · {TITLES[p['mode']]}",
              'callback_data': f'h:entry:{subject}:{fingerprint(p)}'}] for p in subset]
    nav = []
    if page > 0:
        nav.append({'text': '← Новее', 'callback_data': f'h:page:{subject}:{page - 1}'})
    if page < max_page:
        nav.append({'text': 'Старее →', 'callback_data': f'h:page:{subject}:{page + 1}'})
    if nav:
        rows.append(nav)
    return api.send(viewer, f'<b>📚 История ответов</b>\n\nСтраница {page + 1}/{max_page + 1}. '
                    'Показаны записи, сохранившиеся в истории (до 200).', api.ik(rows))


def resume(api, uid: int):
    session = api.gj(api.skey(uid))
    if not session or session.get('mode') not in FLOW_MODES:
        return api.send(uid, 'Незаконченного опроса нет.', api.menu(uid))
    index = session.get('i')
    answers = session.get('answers')
    qs = api.qset(session['mode'])
    if not isinstance(index, int) or not isinstance(answers, list) or index != len(answers) or not 0 <= index < len(qs):
        return api.send(uid, 'Не удалось продолжить эту сессию. /cancel отменит только незаконченный опрос.')
    api.rc('DEL', api.skey(uid) + ':settings')
    return api.ask(uid, qs, index)


def show_prayers(api, uid: int):
    text, keyboard = library.overview('prayers')
    return api.send(uid, text, keyboard)


def show_book(api, uid: int):
    text, keyboard = library.overview('book')
    return api.send(uid, text, keyboard)


def handle_text(api, uid: int, text: str) -> bool:
    routes = {
        '/prayers': show_prayers, '🙏 Молитвы': show_prayers,
        '/bigbook': show_book, '/bk': show_book, '📘 Большая книга': show_book,
        '/history': history, '📚 Мои ответы': history,
        '/resume': resume, '▶️ Продолжить': resume,
    }
    if text in routes:
        routes[text](api, uid)
        return True
    if text == '/cancel':
        api.rc('DEL', api.skey(uid), api.skey(uid) + ':settings')
        api.send(uid, 'Текущий диалог отменён. Завершённые отчёты не удалены.', api.menu(uid))
        return True
    if text in ('⏰ Напоминание', '/reminder'):
        api.sj(api.skey(uid) + ':settings', {'mode': 'reminder'}, 3600)
        api.send(uid, 'Введи время, например <code>22:30</code>.\n\n'
                 'Сейчас сохраняется только настройка: автоматический планировщик ещё не подключён. '
                 'Ручные напоминания от спонсора работают.')
        return True
    if text in ('/stop_reminder',):
        user = api.gj(api.ukey(uid), {}) or {}
        user['reminder_enabled'] = False
        api.sj(api.ukey(uid), user)
        api.rc('DEL', api.skey(uid) + ':settings')
        api.send(uid, 'Напоминание выключено.')
        return True
    if text in ('🕰 Часовой пояс', '/timezone'):
        api.send(uid, 'Выбери часовой пояс:', api.ik([
            [{'text': z, 'callback_data': 'tz:' + z}] for z in ZONES]))
        return True
    if text == '/sobriety_reset':
        api.sj(api.skey(uid) + ':settings', {'mode': 'sobriety_date'}, 3600)
        api.send(uid, 'Введи новую дату трезвости: <code>ДД.ММ.ГГГГ</code>. Старая дата сохранится до подтверждения новой.')
        return True
    return False


def consume_setting(api, uid: int, text: str) -> bool:
    session = api.gj(api.skey(uid) + ':settings')
    if not session or text.startswith('/'):
        return False
    if session.get('mode') == 'reminder':
        if not re.fullmatch(r'(?:[01]\d|2[0-3]):[0-5]\d', text):
            api.send(uid, 'Формат времени: <code>22:30</code>. /cancel — отменить.')
            return True
        user = api.gj(api.ukey(uid), {}) or {}
        user.update(reminder_time=text, reminder_enabled=True)
        api.sj(api.ukey(uid), user)
        api.rc('DEL', api.skey(uid) + ':settings')
        api.send(uid, f"Время <b>{text}</b> ({html.escape(user.get('timezone', api.TZ))}) сохранено. "
                 'Автоматическая отправка начнётся только после подключения планировщика.')
        return True
    if session.get('mode') == 'task_add':
        tasks = api.gj(f'tasks:{uid}', []) or []
        tasks.append({'text': text, 'done': False})
        api.sj(f'tasks:{uid}', tasks)
        api.rc('DEL', api.skey(uid) + ':settings')
        api.tasks(uid)
        return True
    if session.get('mode') == 'sobriety_date':
        api.set_sobriety_date(uid, text, api.skey(uid) + ':settings')
        return True
    return False


def handle_callback(api, uid: int, data: str) -> bool:
    if data.startswith(('p:', 'bk:')):
        text, keyboard = library.item(data)
        api.send(uid, text, keyboard)
        return True
    if data in ('library:prayers', 'library:book'):
        (show_prayers if data.endswith('prayers') else show_book)(api, uid)
        return True
    if data == 'resume':
        resume(api, uid)
        return True
    if data.startswith('tz:'):
        zone = data[3:]
        try:
            ZoneInfo(zone)
        except (ZoneInfoNotFoundError, ValueError):
            api.send(uid, 'Неизвестный часовой пояс.')
            return True
        user = api.gj(api.ukey(uid), {}) or {}
        user['timezone'] = zone
        api.sj(api.ukey(uid), user)
        api.send(uid, f'Часовой пояс: <code>{html.escape(zone)}</code>.')
        return True
    if data.startswith('h:'):
        try:
            _, action, raw_subject, value = data.split(':', 3)
            subject = int(raw_subject)
            if not can_read(api, uid, subject):
                api.send(uid, 'Нет доступа к этим записям.')
            elif action == 'page':
                history(api, uid, subject, int(value))
            elif action == 'entry':
                found = next((p for p in records(api, subject) if fingerprint(p) == value), None)
                if found:
                    api.send(uid, api.fmt(found), api.ik([[{
                        'text': '← К истории', 'callback_data': f'h:page:{subject}:0'}]]))
                else:
                    api.send(uid, 'Запись больше не найдена в сохранённой истории.')
            else:
                api.send(uid, 'Открой историю заново.')
        except (ValueError, TypeError):
            api.send(uid, 'Открой историю заново.')
        return True
    if data == 'task:done':
        tasks = api.gj(f'tasks:{uid}', []) or []
        rows = [[{'text': f"{i + 1}. {task['text'][:50]}", 'callback_data': f'task:finish:{i}'}]
                for i, task in enumerate(tasks) if not task.get('done')]
        api.send(uid, 'Выбери выполненное задание:' if rows else 'Все задания выполнены.', api.ik(rows) if rows else None)
        return True
    if data.startswith('task:finish:'):
        tasks = api.gj(f'tasks:{uid}', []) or []
        try:
            index = int(data.rsplit(':', 1)[1])
            if not 0 <= index < len(tasks):
                raise ValueError()
        except ValueError:
            api.send(uid, 'Задание не найдено.')
            return True
        tasks[index]['done'] = True  # Explicit choice; repeated tap never toggles it back.
        api.sj(f'tasks:{uid}', tasks)
        api.tasks(uid)
        return True
    return False
