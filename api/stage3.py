from http.server import BaseHTTPRequestHandler
import os,json,urllib.request,urllib.parse,urllib.error,html,secrets,sys
from eveningbot import features
from eveningbot.messages import message_parts
from eveningbot.webhook import WebhookHandler
from datetime import datetime,date,timezone,timedelta
from zoneinfo import ZoneInfo

TOKEN=os.getenv('BOT_TOKEN',''); OWNER=int((os.getenv('OWNER_TELEGRAM_ID','0') or '0').strip())
RURL=(os.getenv('KV_REST_API_URL') or os.getenv('UPSTASH_REDIS_REST_URL') or '').rstrip('/')
RTOK=os.getenv('KV_REST_API_TOKEN') or os.getenv('UPSTASH_REDIS_REST_TOKEN') or ''
TZ=os.getenv('DEFAULT_TIMEZONE','Europe/Moscow')

EVENING=['Не были ли мы в течение дня злобными?','Не были ли мы в течение дня эгоистичными?','Не были ли мы в течение дня нечестными?','Испытывали ли мы в течение дня страх?','Должны ли мы извиниться перед кем-то?','Может быть, мы что-то затаили про себя?','Есть ли что-то, что следует обсудить с кем-либо?','Проявляли ли мы любовь и доброту ко всем окружающим?','Что мы могли бы сделать лучше?','Думали ли мы в основном только о себе?','Думали ли мы о том, что можем сделать для других?','Благодарности Богу за сегодня — не меньше 10 примеров.']
MORNING=['О чём я прошу Бога сегодня?','Какова воля Бога для меня сегодня, насколько я её понимаю?','Что сегодня может помешать мне быть полезным другим?','Кому я могу сегодня помочь?','Какие духовные принципы мне особенно нужны сегодня?']
TENTH=['Что произошло?','Что я чувствую сейчас?','Где была моя ответственность?','Нужно ли мне признать ошибку или извиниться?','Какой духовный принцип я могу применить прямо сейчас?','Что полезного я могу сделать следующим действием?']
READINGS=['Только сегодня я буду жить одним днём.','Честность, открытость и готовность — основа выздоровления.','Мы не стремимся к совершенству; мы стремимся к духовному росту.','Сегодня я могу быть полезным другому человеку.','Пусть мои действия сегодня будут продиктованы любовью, а не страхом.']


def request(url,data=None,headers=None):
 b=None if data is None else json.dumps(data).encode(); r=urllib.request.Request(url,data=b,headers=headers or {'Content-Type':'application/json'})
 with urllib.request.urlopen(r,timeout=20) as x:return json.loads(x.read().decode())
def tg(m,p):return request(f'https://api.telegram.org/bot{TOKEN}/{m}',p)
def send(cid, text, k=None):
 parts = message_parts(text)
 result = None
 for index, part in enumerate(parts):
  payload = {'chat_id': cid, **part}
  if k and index == len(parts) - 1:
   payload['reply_markup'] = k
  result = tg('sendMessage', payload)
 return result

def rc(*parts):
 if not RURL or not RTOK:
  raise RuntimeError('Redis is not configured')
 result = request(RURL, list(parts), {'Authorization': 'Bearer ' + RTOK, 'Content-Type': 'application/json'})
 if 'error' in result:
  raise RuntimeError('Redis command failed')
 return result.get('result')

def gj(k,d=None):
 v=rc('GET',k);return d if v is None else json.loads(v)
def sj(k,v,ttl=None):
 a=['SET',k,json.dumps(v,ensure_ascii=False)];a+=['EX',ttl] if ttl else []
 return rc(*a)
def ukey(uid):return f'u:{uid}'
def skey(uid):return f's:{uid}'
def now(u=None):return datetime.now(ZoneInfo((u or {}).get('timezone',TZ)))

def ensure(fr,sponsor=None):
 old=gj(ukey(fr['id']),{}) or {}; u={**old,'id':fr['id'],'name':(' '.join(x for x in [fr.get('first_name',''),fr.get('last_name','')] if x).strip() or str(fr['id'])),'username':fr.get('username'),'timezone':old.get('timezone',TZ),'joined_at':old.get('joined_at',datetime.now(timezone.utc).isoformat())}
 if sponsor:u['sponsor_id']=int(sponsor)
 sj(ukey(fr['id']),u);rc('SADD','users',fr['id']);return u

def menu(uid):
 rows=[[{'text':'🌅 Утренний 11 шаг'},{'text':'🌙 Вечерний 11 шаг'}],[{'text':'✍️ Текущий 10 шаг'},{'text':'📖 Чтение дня'}],[{'text':'🎂 Моя трезвость'},{'text':'📋 Задания'}],[{'text':'🆘 Мне тяжело'}],[{'text':'⏰ Напоминание'},{'text':'🕰 Часовой пояс'}]]
 rows.insert(2, [{'text': '🙏 Молитвы'}, {'text': '📘 Большая книга'}])
 rows.append([{'text': '📚 Мои ответы'}, {'text': '▶️ Продолжить'}])
 if uid==OWNER:rows += [[{'text':'🛠 Админка'}],[{'text':'👥 Подопечные'},{'text':'📊 Сегодня'}],[{'text':'📩 Последние отчёты'},{'text':'📣 Напомнить'}]]
 return {'keyboard':rows,'resize_keyboard':True}
def ik(rows):return {'inline_keyboard':rows}

def start_flow(uid,mode,questions,title):
 current = gj(skey(uid))
 if current:
  if current.get('mode') == mode:
   return features.resume(sys.modules[__name__], uid)
  return send(uid, 'Есть незаконченный диалог. /resume — продолжить опрос; /cancel — отменить его перед новым.')
 sj(skey(uid),{'mode':mode,'i':0,'answers':[]},86400);send(uid,f'<b>{title}</b>');ask(uid,questions,0)
def ask(uid,qs,i):send(uid,f'<b>Вопрос {i+1} из {len(qs)}</b>\n\n{html.escape(qs[i])}',ik([[{'text':'Отменить','callback_data':'cancel'}]]))
def qset(mode):return MORNING if mode=='morning' else TENTH if mode=='tenth' else EVENING

def save_entry(uid,mode,answers):
 u=gj(ukey(uid),{}) or {}; n=now(u); d=n.strftime('%Y-%m-%d'); payload={'uid':uid,'name':u.get('name',str(uid)),'mode':mode,'date':d,'time':n.strftime('%d.%m.%Y %H:%M'),'answers':answers}
 sj(f'entry:{mode}:{uid}:{d}',payload,15552000);rc('LPUSH',f'entries:{uid}',json.dumps(payload,ensure_ascii=False));rc('LTRIM',f'entries:{uid}',0,199);rc('LPUSH','entries:latest',json.dumps(payload,ensure_ascii=False));rc('LTRIM','entries:latest',0,199);sj(f'done:{mode}:{d}:{uid}',{'time':payload['time']},15552000);return payload

def fmt(p):
 titles={'morning':'🌅 Утренний 11 шаг','evening':'🌙 Вечерний 11 шаг','tenth':'✍️ Текущий 10 шаг'};qs=qset(p['mode']);lines=[f"<b>{titles[p['mode']]} — {html.escape(p['name'])}</b>",f"<i>{p['time']}</i>",'']
 for i,(q,a) in enumerate(zip(qs,p['answers']),1):lines += [f'<b>{i}. {html.escape(q)}</b>',html.escape(a or '—'),'']
 return '\n'.join(lines)
def finish(uid,s):
 p=save_entry(uid,s['mode'],s['answers']);u=gj(ukey(uid),{}) or {};rec=set()
 if u.get('sponsor_id'):rec.add(int(u['sponsor_id']))
 if OWNER and uid!=OWNER:rec.add(OWNER)
 for r in rec:send(r,fmt(p),ik([[{'text':'💬 Написать','url':f'tg://user?id={uid}'}]]))
 rc('DEL',skey(uid));send(uid,'Спасибо. Запись сохранена. 🙏',menu(uid))

def sobriety(uid):
 u=gj(ukey(uid),{}) or {}; sd=u.get('sobriety_date')
 if not sd:
  sj(skey(uid)+':settings',{'mode':'sobriety_date'},3600);return send(uid,'Введи дату трезвости в формате <code>ДД.ММ.ГГГГ</code>.')
 try:d=date.fromisoformat(sd);days=(now(u).date()-d).days
 except:return send(uid,'Дата трезвости сохранена неверно. Отправь /sobriety_reset.')
 send(uid,f'🎂 <b>Трезвость</b>\n\nДата: <b>{d.strftime("%d.%m.%Y")}</b>\nСегодня: <b>{days} дней</b>.')

def set_sobriety_date(uid, value, state_key=None):
 user = gj(ukey(uid), {}) or {}
 try:
  parsed = datetime.strptime(value, '%d.%m.%Y').date()
  if parsed > now(user).date():
   raise ValueError('future date')
 except ValueError:
  return send(uid, 'Нужна дата не позднее сегодняшнего дня: <code>ДД.ММ.ГГГГ</code>.')
 user['sobriety_date'] = parsed.isoformat()
 sj(ukey(uid), user)
 rc('DEL', state_key or skey(uid))
 return sobriety(uid)

def tasks(uid):
 arr=gj(f'tasks:{uid}',[]) or []
 txt='\n'.join(f"{'✅' if x.get('done') else '▫️'} {i+1}. {html.escape(x['text'])}" for i,x in enumerate(arr)) or 'Заданий пока нет.'
 send(uid,'<b>📋 Задания по программе</b>\n\n'+txt,ik([[{'text':'➕ Добавить','callback_data':'task:add'}],[{'text':'✅ Отметить выполненным','callback_data':'task:done'}]]))
def reading(uid):
 idx=date.today().toordinal()%len(READINGS);send(uid,f'📖 <b>Чтение дня</b>\n\n<i>{html.escape(READINGS[idx])}</i>')
def sos(uid):
 u=gj(ukey(uid),{}) or {};send(OWNER,f"🆘 <b>{html.escape(u.get('name',str(uid)))}</b> просит связаться.\n\n<a href='tg://user?id={uid}'>Написать сейчас</a>") if OWNER else None;send(uid,'Я сообщил спонсору. Пожалуйста, не оставайся один: позвони спонсору или другому члену АА прямо сейчас.')

def sponsees():
 out=[]
 for x in rc('SMEMBERS','users') or []:
  i=int(x);u=gj(ukey(i),{}) or {}
  if i!=OWNER and (u.get('sponsor_id')==OWNER or OWNER):out.append(u)
 return out

def stats(uid):
 user = gj(ukey(uid), {}) or {}
 today = now(user).date()
 dates = [(today - timedelta(days=i)).isoformat() for i in range(180)]
 keys = [f'done:evening:{day}:{uid}' for day in dates] + [f'done:{day}:{uid}' for day in dates]
 values = rc('MGET', *keys) or [None] * len(keys)
 flags = [bool(values[i] or values[180 + i]) for i in range(180)]
 streak = 0
 for done in flags[0 if flags[0] else 1:]:
  if not done:
   break
  streak += 1
 return sum(flags[:7]), sum(flags[:30]), streak


def completed_today(user):
 day = now(user).date().isoformat()
 return bool(gj(f'done:evening:{day}:{user["id"]}') or gj(f'done:{day}:{user["id"]}'))

def admin(uid):
 us=sponsees();answered=sum(1 for u in us if completed_today(u))
 send(uid,f'<b>🛠 Панель спонсора</b>\n\n👥 Подопечных: <b>{len(us)}</b>\n✅ Ответили сегодня: <b>{answered}</b>\n🕓 Не ответили: <b>{len(us)-answered}</b>',ik([[{'text':'👥 Подопечные','callback_data':'a:users'}],[{'text':'📊 Сегодня','callback_data':'a:today'}],[{'text':'📩 Последние отчёты','callback_data':'a:last'}],[{'text':'📣 Напомнить неответившим','callback_data':'a:remind'}]]))
def admin_users(uid):
 lines=['<b>👥 Подопечные</b>','']
 for u in sponsees():
  w,m,st=stats(u['id']);lines.append(f"• <b>{html.escape(u.get('name',''))}</b> — 7 дней: {w}/7, 30 дней: {m}/30, серия: {st}")
 rows = [[{'text': u.get('name', str(u['id']))[:50], 'callback_data': f'card:{u["id"]}'}]
         for u in sponsees() if features.can_read(sys.modules[__name__], uid, u['id'])]
 send(uid,'\n'.join(lines) if len(lines)>2 else 'Подопечных пока нет.', ik(rows) if rows else None)
def sponsee_card(viewer, subject):
 if not features.can_read(sys.modules[__name__], viewer, subject) or viewer != OWNER:
  return send(viewer, 'Нет доступа. Используй персональное приглашение для подключения подопечного.')
 user = gj(ukey(subject), {}) or {}
 return send(viewer, '<b>👤 ' + html.escape(user.get('name', str(subject))) + '</b>\n\n'
             + 'Часовой пояс: ' + html.escape(user.get('timezone', TZ)), ik([
   [{'text': '📚 История ответов', 'callback_data': f'h:page:{subject}:0'}],
   [{'text': '💬 Написать', 'url': f'tg://user?id={subject}'}]]))

def admin_today(uid):
 d=date.today().isoformat();a=[];m=[]
 for u in sponsees():(a if completed_today(u) else m).append(u)
 send(uid,'<b>📊 Сегодня</b>\nПо местному дню каждого участника.\n\n✅ Ответили:\n'+('\n'.join('• '+html.escape(x['name']) for x in a) or '—')+'\n\n🕓 Не ответили:\n'+('\n'.join('• '+html.escape(x['name']) for x in m) or '—'))
def admin_last(uid):
 raw=rc('LRANGE','entries:latest',0,9) or [];lines=['<b>📩 Последние записи</b>','']
 for x in raw:
  p=json.loads(x);lines.append(f"• {html.escape(p['name'])} — {p['mode']} — {p['time']}")
 send(uid,'\n'.join(lines) if raw else 'Записей пока нет.')
def remind(uid):
 d=date.today().isoformat();n=0
 for u in sponsees():
  if not completed_today(u):
   try:send(u['id'],'🌙 Напоминание от спонсора: пора пройти вечернюю часть.',ik([[{'text':'Начать','callback_data':'flow:evening'}]]));n+=1
   except:pass
 send(uid,f'Напоминание отправлено: <b>{n}</b>.')

def handle_text(m):
 if m.get('chat', {}).get('type') != 'private' or m.get('from', {}).get('is_bot'):
  return
 uid=m['from']['id'];t=m.get('text','').strip()
 if not t:
  return send(uid, 'Пожалуйста, отправь ответ текстом. Опрос остался на прежнем вопросе.')
 if t.startswith('/'):
  command, *rest = t.split(maxsplit=1)
  t = command.split('@', 1)[0].lower() + ((' ' + rest[0]) if rest else '')
 ensure(m['from']);s=gj(skey(uid))
 if features.handle_text(sys.modules[__name__], uid, t):
  return
 if t.split(maxsplit=1)[0] == '/start':
  p=t.split(maxsplit=1);sp=None
  if len(p)>1 and p[1].startswith('invite_'):sp=rc('GETDEL','invite:'+p[1][7:])
  ensure(m['from'],sp)
  send(uid, 'Привет. Я помогу работать с 10-м и 11-м Шагами. Завершённые ответы сохраняются в базе и передаются владельцу бота и подключённому спонсору. Не указывай лишние персональные данные.', menu(uid))
  if s and s.get('mode') in features.FLOW_MODES:
   send(uid, 'Твой незаконченный опрос сохранён.', ik([[{'text': 'Продолжить', 'callback_data': 'resume'}]]))
  return
 if t in ('🌅 Утренний 11 шаг','/morning'):return start_flow(uid,'morning',MORNING,'🌅 Утренний 11-й Шаг')
 if t in ('🌙 Вечерний 11 шаг','🌙 Вечерняя часть','/evening'):return start_flow(uid,'evening',EVENING,'🌙 Вечерний 11-й Шаг')
 if t in ('✍️ Текущий 10 шаг','/tenth'):return start_flow(uid,'tenth',TENTH,'✍️ Текущий 10-й Шаг')
 if t in ('📖 Чтение дня','/reading'):return reading(uid)
 if t in ('🎂 Моя трезвость','/sobriety'):return sobriety(uid)
 if t in ('📋 Задания','/tasks'):return tasks(uid)
 if t in ('🆘 Мне тяжело','/sos'):return sos(uid)
 if t in ('🛠 Админка','/admin') and uid==OWNER:return admin(uid)
 if t in ('👥 Подопечные','/sponsees') and uid==OWNER:return admin_users(uid)
 if t in ('📊 Сегодня','/today') and uid==OWNER:return admin_today(uid)
 if t in ('📩 Последние отчёты','/last') and uid==OWNER:return admin_last(uid)
 if t in ('📣 Напомнить','/remind_missing') and uid==OWNER:return remind(uid)
 if t in ('🔗 Пригласить подопечного','/invite') and uid==OWNER:
  tok=secrets.token_urlsafe(8);rc('SET','invite:'+tok,uid,'EX',604800);name=tg('getMe',{})['result']['username'];return send(uid,f'<code>https://t.me/{name}?start=invite_{tok}</code>')
 if features.consume_setting(sys.modules[__name__], uid, t):
  return
 if t.startswith('/'):
  return send(uid, 'Неизвестная команда. /cancel — отменить диалог; /resume — продолжить опрос.')
 if s and s.get('mode') in ('morning','evening','tenth') and not t.startswith('/'):
  s['answers'].append(t);s['i']+=1;qs=qset(s['mode'])
  if s['i']>=len(qs):return finish(uid,s)
  sj(skey(uid),s,86400);return ask(uid,qs,s['i'])
 if s and s.get('mode')=='sobriety_date':
  return set_sobriety_date(uid, t)
 if s and s.get('mode')=='task_add':
  arr=gj(f'tasks:{uid}',[]) or [];arr.append({'text':t,'done':False});sj(f'tasks:{uid}',arr);rc('DEL',skey(uid));return tasks(uid)
 return send(uid,'Выбери действие.',menu(uid))

def callback(c):
 if c.get('message', {}).get('chat', {}).get('type') != 'private' or c.get('from', {}).get('is_bot'):
  return
 uid=c['from']['id'];d=c.get('data','')
 try:
  tg('answerCallbackQuery',{'callback_query_id':c['id']})
 except urllib.error.HTTPError as error:
  if error.code != 400:
   raise
 if features.handle_callback(sys.modules[__name__], uid, d):
  return
 if d.startswith('card:'):
  try:
   subject = int(d[5:])
  except ValueError:
   return send(uid, 'Карточка не найдена.')
  return sponsee_card(uid, subject)
 if d=='cancel':rc('DEL',skey(uid),skey(uid)+':settings');return send(uid,'Отменено.',menu(uid))
 if d.startswith('flow:'):
  mode=d.split(':')[1]
  if mode not in features.FLOW_MODES:
   return send(uid, 'Эта кнопка устарела. Открой /start.')
  return start_flow(uid,mode,qset(mode),{'morning':'🌅 Утренний 11-й Шаг','evening':'🌙 Вечерний 11-й Шаг','tenth':'✍️ Текущий 10-й Шаг'}[mode])
 if d=='task:add':sj(skey(uid)+':settings',{'mode':'task_add'},3600);return send(uid,'Напиши новое задание.')
 if uid==OWNER:
  if d=='a:users':return admin_users(uid)
  if d=='a:today':return admin_today(uid)
  if d=='a:last':return admin_last(uid)
  if d=='a:remind':return remind(uid)

 return send(uid, 'Эта кнопка устарела или недоступна. Открой /start.')

class handler(WebhookHandler):
 """Vercel entrypoint remains api.stage3:handler; no wrapper imports."""
 def dispatch(self, update):
  if 'message' in update:
   handle_text(update['message'])
  elif 'callback_query' in update:
   callback(update['callback_query'])

 def configure(self, url, secret):
  return tg('setWebhook', {'url': url, 'secret_token': secret, 'max_connections': 1,
                           'allowed_updates': ['message', 'callback_query'],
                           'drop_pending_updates': False})
