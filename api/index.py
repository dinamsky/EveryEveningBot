from http.server import BaseHTTPRequestHandler
import os,json,urllib.request,urllib.parse,secrets
from datetime import datetime
from zoneinfo import ZoneInfo

TOKEN=os.getenv('BOT_TOKEN','')
OWNER=int(os.getenv('OWNER_TELEGRAM_ID','0') or 0)
RURL=(os.getenv('KV_REST_API_URL') or os.getenv('UPSTASH_REDIS_REST_URL') or '').rstrip('/')
RTOK=os.getenv('KV_REST_API_TOKEN') or os.getenv('UPSTASH_REDIS_REST_TOKEN') or ''
TZ=os.getenv('DEFAULT_TIMEZONE','Europe/Moscow')
Q=['Не были ли мы в течение дня злобными?','Не были ли мы в течение дня эгоистичными?','Не были ли мы в течение дня нечестными?','Испытывали ли мы в течение дня страх?','Должны ли мы извиниться перед кем-то?','Может быть, мы что-то затаили про себя?','Есть ли что-то, что следует обсудить с кем-либо?','Проявляли ли мы в течение дня любовь и доброту ко всем окружающим?','Что мы могли бы сделать лучше?','Думали ли мы в течение дня в основном только о себе?','Думали ли мы о том, что можем сделать для других, о нашем вкладе в общее течение жизни?','Благодарности Богу за сегодня — перечисли не меньше 10 примеров.']

def req(url,data=None,headers=None):
 b=None if data is None else json.dumps(data).encode(); r=urllib.request.Request(url,data=b,headers=headers or {'Content-Type':'application/json'})
 with urllib.request.urlopen(r,timeout=15) as x:return json.loads(x.read().decode())
def tg(m,p):return req(f'https://api.telegram.org/bot{TOKEN}/{m}',p)
def send(cid,text,kb=None):
 p={'chat_id':cid,'text':text,'parse_mode':'HTML'}
 if kb:p['reply_markup']=kb
 return tg('sendMessage',p)
def rc(*a):
 if not RURL or not RTOK:raise RuntimeError('Redis not configured')
 u=RURL+'/'+('/'.join(urllib.parse.quote(str(x),safe='') for x in a));return req(u,headers={'Authorization':'Bearer '+RTOK})['result']
def gj(k,d=None):
 v=rc('GET',k);return d if v is None else json.loads(v)
def sj(k,v,ttl=None):
 a=['SET',k,json.dumps(v,ensure_ascii=False)];a+=['EX',ttl] if ttl else [];return rc(*a)
def ensure(u,sponsor=None):
 k='u:'+str(u['id']);x=gj(k,{})|{'id':u['id'],'name':u.get('first_name',''),'username':u.get('username'),'timezone':gj(k,{}).get('timezone',TZ)}
 if sponsor:x['sponsor_id']=sponsor
 sj(k,x);rc('SADD','users',u['id']);return x
def menu(uid):
 rows=[[{'text':'🌙 Вечерняя часть'}],[{'text':'⏰ Напоминание'},{'text':'🕰 Часовой пояс'}]]
 if uid==OWNER:rows.append([{'text':'🔗 Пригласить подопечного'},{'text':'👥 Подопечные'}])
 return {'keyboard':rows,'resize_keyboard':True}
def ask(uid,i):send(uid,f'<b>Вопрос {i+1} из {len(Q)}</b>\n\n{Q[i]}',{'inline_keyboard':[[{'text':'Отменить','callback_data':'cancel'}]]})
def begin(uid):sj('s:'+str(uid),{'m':'e','i':0,'a':[]},86400);send(uid,'🌙 <b>Вечерняя часть 11-го Шага</b>\n\nКогда мы ложимся спать, мы конструктивно пересматриваем прожитый день.');ask(uid,0)
def finish(uid,s):
 u=gj('u:'+str(uid),{});lines=[f"<b>Вечерний отчёт — {u.get('name','')}</b>",'']
 for i,(q,a) in enumerate(zip(Q,s['a']),1):lines += [f'<b>{i}. {q}</b>',a,'']
 rep='\n'.join(lines);sp=u.get('sponsor_id')
 send(uid,'Спасибо. Вечерняя часть завершена. 🙏')
 if sp:send(sp,'📩 <b>Отчёт подопечного</b>\n\n'+rep);send(uid,'Ответы отправлены спонсору.')
 else:send(uid,'Спонсор пока не подключён.')
 rc('DEL','s:'+str(uid))
def text(m):
 uid=m['from']['id'];t=m.get('text','').strip();ensure(m['from']);s=gj('s:'+str(uid))
 if s and s.get('m')=='e' and not t.startswith('/'):
  s['a'].append(t);s['i']+=1
  if s['i']>=len(Q):return finish(uid,s)
  sj('s:'+str(uid),s,86400);return ask(uid,s['i'])
 if s and s.get('m')=='r':
  try:datetime.strptime(t,'%H:%M')
  except:return send(uid,'Формат времени: <code>22:30</code>.')
  u=gj('u:'+str(uid),{});u|={'reminder_time':t,'reminder_enabled':True};sj('u:'+str(uid),u);rc('DEL','s:'+str(uid));return send(uid,f'✅ Напоминание: <b>{t}</b>.')
 if t.startswith('/start'):
  p=t.split(maxsplit=1);sp=None
  if len(p)>1 and p[1].startswith('invite_'):
   z=p[1][7:];sp=rc('GET','invite:'+z)
   if sp:rc('DEL','invite:'+z)
  ensure(m['from'],int(sp) if sp else None);return send(uid,'Привет. Я помогу пройти вечернюю часть 11-го Шага.',menu(uid))
 if t in ('/evening','🌙 Вечерняя часть'):return begin(uid)
 if t in ('/reminder','⏰ Напоминание'):sj('s:'+str(uid),{'m':'r'},3600);return send(uid,'Во сколько напоминать? Формат: <code>22:30</code>.')
 if t in ('/timezone','🕰 Часовой пояс'):
  z=['Europe/Moscow','Europe/Helsinki','Asia/Yekaterinburg','Asia/Novosibirsk','Asia/Vladivostok'];return send(uid,'Выбери часовой пояс:',{'inline_keyboard':[[{'text':x,'callback_data':'tz:'+x}] for x in z]})
 if t in ('/invite','🔗 Пригласить подопечного') and uid==OWNER:
  z=secrets.token_urlsafe(8);rc('SET','invite:'+z,uid,'EX',604800);me=tg('getMe',{})['result']['username'];return send(uid,f'<code>https://t.me/{me}?start=invite_{z}</code>')
 if t in ('/sponsees','👥 Подопечные') and uid==OWNER:
  out=[]
  for x in rc('SMEMBERS','users') or []:
   u=gj('u:'+str(x),{})
   if u.get('sponsor_id')==uid:out.append('• '+u.get('name',str(x)))
  return send(uid,'<b>Подопечные:</b>\n'+('\n'.join(out) if out else 'Пока никого.'))
 return send(uid,'Выбери действие.',menu(uid))
def cb(c):
 uid=c['from']['id'];d=c.get('data','');tg('answerCallbackQuery',{'callback_query_id':c['id']})
 if d=='cancel':rc('DEL','s:'+str(uid));return send(uid,'Опрос отменён.')
 if d=='begin':return begin(uid)
 if d.startswith('tz:'):
  u=gj('u:'+str(uid),{});u['timezone']=d[3:];sj('u:'+str(uid),u);return send(uid,'✅ Часовой пояс сохранён.')
def cron():
 n=0
 for x in rc('SMEMBERS','users') or []:
  u=gj('u:'+str(x),{})
  if not u.get('reminder_enabled'):continue
  now=datetime.now(ZoneInfo(u.get('timezone',TZ)))
  if now.strftime('%H:%M')==u.get('reminder_time') and rc('SET',f"sent:{x}:{now:%Y%m%d%H%M}",'1','NX','EX',3600):send(int(x),'🌙 Время вечерней части.',{'inline_keyboard':[[{'text':'Начать','callback_data':'begin'}]]});n+=1
 return n
class handler(BaseHTTPRequestHandler):
 def out(self,c=200,o=None):self.send_response(c);self.send_header('Content-Type','application/json');self.end_headers();self.wfile.write(json.dumps(o or {'ok':True}).encode())
 def do_GET(self):
  q=urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
  try:
   if 'setup' in q:
    h=self.headers.get('x-forwarded-host') or self.headers.get('host');return self.out(200,tg('setWebhook',{'url':'https://'+h+'/api/index'}))
   if 'cron' in q:return self.out(200,{'ok':True,'sent':cron()})
   return self.out(200,{'ok':True,'service':'EveryEveningBot'})
  except Exception as e:return self.out(500,{'ok':False,'error':str(e)})
 def do_POST(self):
  try:
   n=int(self.headers.get('content-length','0'));u=json.loads(self.rfile.read(n) or '{}')
   if 'message' in u:text(u['message'])
   elif 'callback_query' in u:cb(u['callback_query'])
   return self.out()
  except Exception as e:return self.out(500,{'ok':False,'error':str(e)})