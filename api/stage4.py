from http.server import BaseHTTPRequestHandler
import json, urllib.parse
from api import stage3 as base

PRAYERS = {
    'serenity': '🙏 <b>Молитва о душевном покое</b>\n\nБоже, дай мне душевный покой принять то, что я не могу изменить, мужество изменить то, что могу, и мудрость отличить одно от другого.',
    'third': '3️⃣ <b>Молитва Третьего Шага</b>\n\nБоже, я вверяю Тебе свою волю и свою жизнь. Освободи меня от своеволия, чтобы я мог лучше исполнять Твою волю и быть полезным другим.',
    'seventh': '7️⃣ <b>Молитва Седьмого Шага</b>\n\nБоже, я готов, чтобы Ты устранил во мне то, что мешает быть полезным Тебе и людям. Дай мне силы поступать правильно.',
    'fear': '🕊 <b>Молитва при страхе</b>\n\nБоже, освободи меня от страха и направь моё внимание на то, каким Ты хотел бы видеть меня и мои действия.',
    'resentment': '🤝 <b>Молитва при обиде</b>\n\nБоже, помоги мне увидеть в этом человеке такого же духовно больного человека, как я. Дай мне терпение, сострадание и готовность быть полезным.',
    'morning': '🌅 <b>Утренняя молитва</b>\n\nБоже, направь сегодня мои мысли и поступки. Освободи меня от жалости к себе, нечестности и корыстных мотивов. Покажи, каким должно быть моё следующее правильное действие.',
    'evening': '🌙 <b>Вечерняя молитва</b>\n\nБоже, прости мои ошибки этого дня. Покажи, что я могу исправить, кому должен принести извинения и как завтра быть более полезным.'
}

BK = {
    'doctor': '📘 <b>Мнение доктора</b>\n\nАлкоголизм рассматривается как сочетание телесной реакции и навязчивого мышления. Полезно обсудить со спонсором: где в моей истории проявлялась потеря контроля?',
    'bill': '📘 <b>Рассказ Билла</b>\n\nИстория перехода от безнадёжности к духовному опыту и помощи другим. Вопрос: что в этом рассказе похоже на мой собственный путь?',
    'solution': '📘 <b>Решение есть</b>\n\nСообщество, программа действий и духовный образ жизни предлагаются как практический выход. Вопрос: какие действия программы я выполняю сегодня?',
    'more': '📘 <b>Ещё об алкоголизме</b>\n\nГлава показывает, почему одного знания и силы воли часто недостаточно. Вопрос: где я всё ещё рассчитываю только на себя?',
    'how': '📘 <b>Как это работает</b>\n\nОсновное содержание Шагов и начало практической работы. Вопрос: какой Шаг требует от меня действия сейчас?',
    'action': '📘 <b>За работу</b>\n\nПрактика инвентаризации, признания, готовности, возмещения ущерба и ежедневной дисциплины. Вопрос: какое конкретное исправление я могу сделать?',
    'family': '📘 <b>Обращение к жёнам и семьям</b>\n\nМатериал о влиянии алкоголизма на близких и необходимости терпения, честности и границ. Его полезно обсуждать вместе с профильной литературой семейных сообществ.',
    'employers': '📘 <b>Работодателям</b>\n\nРассматривается отношение к алкоголику на работе, ответственность и возможность восстановления. Вопрос: честен ли я в трудовых отношениях?',
    'vision': '📘 <b>Перспектива для вас</b>\n\nОписание жизни сообщества, служения и передачи опыта. Вопрос: кому я могу быть полезен сегодня?'
}

def menu(uid):
    m=base.menu(uid)
    m['keyboard'].insert(2,[{'text':'🙏 Молитвы'},{'text':'📘 Большая книга'}])
    return m
base.menu=menu

def prayers(uid):
    rows=[[{'text':'Душевный покой','callback_data':'p:serenity'},{'text':'3 Шаг','callback_data':'p:third'}],[{'text':'7 Шаг','callback_data':'p:seventh'},{'text':'При страхе','callback_data':'p:fear'}],[{'text':'При обиде','callback_data':'p:resentment'}],[{'text':'Утро','callback_data':'p:morning'},{'text':'Вечер','callback_data':'p:evening'}]]
    base.send(uid,'<b>🙏 Молитвы</b>\n\nВыбери молитву:',base.ik(rows))

def bigbook(uid):
    rows=[[{'text':'Мнение доктора','callback_data':'bk:doctor'}],[{'text':'Рассказ Билла','callback_data':'bk:bill'},{'text':'Решение есть','callback_data':'bk:solution'}],[{'text':'Ещё об алкоголизме','callback_data':'bk:more'}],[{'text':'Как это работает','callback_data':'bk:how'},{'text':'За работу','callback_data':'bk:action'}],[{'text':'Семьям','callback_data':'bk:family'},{'text':'Работодателям','callback_data':'bk:employers'}],[{'text':'Перспектива для вас','callback_data':'bk:vision'}]]
    base.send(uid,'<b>📘 Большая книга</b>\n\nКраткие ориентиры по главам для чтения и разговора со спонсором:',base.ik(rows))

def handle_text(m):
    t=m.get('text','').strip(); uid=m['from']['id']
    if t in ('🙏 Молитвы','/prayers'): return prayers(uid)
    if t in ('📘 Большая книга','/bigbook','/bk'): return bigbook(uid)
    return base.handle_text(m)

def callback(c):
    d=c.get('data',''); uid=c['from']['id']
    if d.startswith('p:'):
        base.tg('answerCallbackQuery',{'callback_query_id':c['id']}); return base.send(uid,PRAYERS.get(d[2:],'Молитва не найдена.'))
    if d.startswith('bk:'):
        base.tg('answerCallbackQuery',{'callback_query_id':c['id']}); return base.send(uid,BK.get(d[3:],'Раздел не найден.'))
    return base.callback(c)

def setup(host): return base.tg('setWebhook',{'url':'https://'+host+'/api/index'})

class handler(BaseHTTPRequestHandler):
    def out(self,code=200,obj=None):
        self.send_response(code); self.send_header('Content-Type','application/json'); self.end_headers(); self.wfile.write(json.dumps(obj or {'ok':True}).encode())
    def do_GET(self):
        q=urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        try:
            if 'setup' in q: return self.out(200,setup(self.headers.get('x-forwarded-host') or self.headers.get('host')))
            return self.out(200,{'ok':True,'service':'EveryEveningBot Stage 4'})
        except Exception as e: return self.out(500,{'ok':False,'error':str(e)})
    def do_POST(self):
        try:
            n=int(self.headers.get('content-length','0')); u=json.loads(self.rfile.read(n) or '{}')
            if 'message' in u: handle_text(u['message'])
            elif 'callback_query' in u: callback(u['callback_query'])
            return self.out()
        except Exception as e: return self.out(500,{'ok':False,'error':str(e)})
