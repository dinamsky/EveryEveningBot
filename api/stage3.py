from http.server import BaseHTTPRequestHandler
import os,json,urllib.request,urllib.parse,html,secrets
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
def send(cid,text,k=None):
 p={'chat_id':cid,'text':text,'parse_mode':'HTML'}
 if k:p['reply_markup']=k
 return tg('sendMessage',p)
def rc(*parts):
 u=RURL+'/'+ '/'.join(urllib.parse.quote(str(x),safe='') for x in parts)
 return request(u,headers={'Authorization':'Bearer '+RTOK}).get('result')
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
 if uid==OWNER:rows += [[{'text':'🛠 Админка'}],[{'text':'👥 Подопечные'},{'text':'📊 Сегодня'}],[{'text':'📩 Последние отчёты'},{'text':'📣 Напомнить'}]]
 return {'keyboard':rows,'resize_keyboard':True}
def ik(rows):return {'inline_keyboard':rows}

def start_flow(uid,mode,questions,title):
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
  sj(skey(uid),{'mode':'sobriety_date'},3600);return send(uid,'Введи дату трезвости в формате <code>ДД.ММ.ГГГГ</code>.')
 try:d=date.fromisoformat(sd);days=(date.today()-d).days
 except:return send(uid,'Дата трезвости сохранена неверно. Отправь /sobriety_reset.')
 send(uid,f'🎂 <b>Трезвость</b>\n\nДата: <b>{d.strftime("%d.%m.%Y")}</b>\nСегодня: <b>{days} дней</b>.')

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
 ds=[]
 for i in range(30):
  d=(date.today()-timedelta(days=i)).isoformat()
  if gj(f'done:evening:{d}:{uid}'):ds.append(d)
 streak=0
 for i in range(365):
  d=(date.today()-timedelta(days=i)).isoformat()
  if gj(f'done:evening:{d}:{uid}'):streak+=1
  else:break
 return len([d for d in ds if d>= (date.today()-timedelta(days=6)).isoformat()]),len(ds),streak

def admin(uid):
 us=sponsees();today=date.today().isoformat();answered=sum(1 for u in us if gj(f'done:evening:{today}:{u["id"]}'))
 send(uid,f'<b>🛠 Панель спонсора</b>\n\n👥 Подопечных: <b>{len(us)}</b>\n✅ Ответили сегодня: <b>{answered}</b>\n🕓 Не ответили: <b>{len(us)-answered}</b>',ik([[{'text':'👥 Подопечные','callback_data':'a:users'}],[{'text':'📊 Сегодня','callback_data':'a:today'}],[{'text':'📩 Последние отчёты','callback_data':'a:last'}],[{'text':'📣 Напомнить неответившим','callback_data':'a:remind'}]]))
def admin_users(uid):
 lines=['<b>👥 Подопечные</b>','']
 for u in sponsees():
  w,m,st=stats(u['id']);lines.append(f"• <b>{html.escape(u.get('name',''))}</b> — 7 дней: {w}/7, 30 дней: {m}/30, серия: {st}")
 send(uid,'\n'.join(lines) if len(lines)>2 else 'Подопечных пока нет.')
def admin_today(uid):
 d=date.today().isoformat();a=[];m=[]
 for u in sponsees():(a if gj(f'done:evening:{d}:{u["id"]}') else m).append(u)
 send(uid,'<b>📊 Сегодня</b>\n\n✅ Ответили:\n'+('\n'.join('• '+html.escape(x['name']) for x in a) or '—')+'\n\n🕓 Не ответили:\n'+('\n'.join('• '+html.escape(x['name']) for x in m) or '—'))
def admin_last(uid):
 raw=rc('LRANGE','entries:latest',0,9) or [];lines=['<b>📩 Последние записи</b>','']
 for x in raw:
  p=json.loads(x);lines.append(f"• {html.escape(p['name'])} — {p['mode']} — {p['time']}")
 send(uid,'\n'.join(lines) if raw else 'Записей пока нет.')
def remind(uid):
 d=date.today().isoformat();n=0
 for u in sponsees():
  if not gj(f'done:evening:{d}:{u["id"]}'):
   try:send(u['id'],'🌙 Напоминание от спонсора: пора пройти вечернюю часть.',ik([[{'text':'Начать','callback_data':'flow:evening'}]]));n+=1
   except:pass
 send(uid,f'Напоминание отправлено: <b>{n}</b>.')

def handle_text(m):
 uid=m['from']['id'];t=m.get('text','').strip();ensure(m['from']);s=gj(skey(uid))
 if s and s.get('mode') in ('morning','evening','tenth') and not t.startswith('/'):
  s['answers'].append(t);s['i']+=1;qs=qset(s['mode'])
  if s['i']>=len(qs):return finish(uid,s)
  sj(skey(uid),s,86400);return ask(uid,qs,s['i'])
 if s and s.get('mode')=='sobriety_date':
  try:d=datetime.strptime(t,'%d.%m.%Y').date();u=gj(ukey(uid),{}) or {};u['sobriety_date']=d.isoformat();sj(ukey(uid),u);rc('DEL',skey(uid));return sobriety(uid)
  except:return send(uid,'Формат даты: <code>ДД.ММ.ГГГГ</code>.')
 if s and s.get('mode')=='task_add':
  arr=gj(f'tasks:{uid}',[]) or [];arr.append({'text':t,'done':False});sj(f'tasks:{uid}',arr);rc('DEL',skey(uid));return tasks(uid)
 if t.startswith('/start'):
  p=t.split(maxsplit=1);sp=None
  if len(p)>1 and p[1].startswith('invite_'):sp=rc('GET','invite:'+p[1][7:]);rc('DEL','invite:'+p[1][7:]) if sp else None
  ensure(m['from'],sp);return send(uid,'Привет. Я помогу работать с 10-м и 11-м Шагами.',menu(uid))
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
 return send(uid,'Выбери действие.',menu(uid))

def callback(c):
 uid=c['from']['id'];d=c.get('data','');tg('answerCallbackQuery',{'callback_query_id':c['id']})
 if d=='cancel':rc('DEL',skey(uid));return send(uid,'Отменено.',menu(uid))
 if d.startswith('flow:'):
  mode=d.split(':')[1];return start_flow(uid,mode,qset(mode),{'morning':'🌅 Утренний 11-й Шаг','evening':'🌙 Вечерний 11-й Шаг','tenth':'✍️ Текущий 10-й Шаг'}[mode])
 if d=='task:add':sj(skey(uid),{'mode':'task_add'},3600);return send(uid,'Напиши новое задание.')
 if d=='task:done':
  arr=gj(f'tasks:{uid}',[]) or []
  for x in arr:
   if not x.get('done'):x['done']=True;break
  sj(f'tasks:{uid}',arr);return tasks(uid)
 if uid==OWNER:
  if d=='a:users':return admin_users(uid)
  if d=='a:today':return admin_today(uid)
  if d=='a:last':return admin_last(uid)
  if d=='a:remind':return remind(uid)

def setup(host):return tg('setWebhook',{'url':'https://'+host+'/api/index'})

class handler(BaseHTTPRequestHandler):
 def out(self,c=200,o=None):self.send_response(c);self.send_header('Content-Type','application/json');self.end_headers();self.wfile.write(json.dumps(o or {'ok':True}).encode())
 def do_GET(self):
  q=urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
  try:
   if 'setup' in q:return self.out(200,setup(self.headers.get('x-forwarded-host') or self.headers.get('host')))
   return self.out(200,{'ok':True,'service':'EveryEveningBot Stage 3'})
  except Exception as e:return self.out(500,{'ok':False,'error':str(e)})
 def do_POST(self):
  try:
   n=int(self.headers.get('content-length','0'));u=json.loads(self.rfile.read(n) or '{}')
   if 'message' in u:handle_text(u['message'])
   elif 'callback_query' in u:callback(u['callback_query'])
   return self.out()
  except Exception as e:return self.out(500,{'ok':False,'error':str(e)})
