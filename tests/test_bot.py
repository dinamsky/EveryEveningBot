"""Offline regression tests. All Telegram and Redis requests are intercepted."""
import copy
from datetime import datetime, timezone, timedelta
import importlib
import io
import json
import os
from pathlib import Path
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from zoneinfo import ZoneInfo

from api import stage3 as bot
from eveningbot import features, library
from eveningbot.messages import chunks, message_parts
from eveningbot.webhook import WebhookHandler


class FakeRedis:
    def __init__(self):
        self.data = {}
        self.calls = []

    def __call__(self, command, *args):
        self.calls.append((command, *args))
        command = command.upper()
        key = args[0] if args else None
        if command == 'GET':
            return self.data.get(key)
        if command == 'SET':
            self.data[key] = args[1]
            return 'OK'
        if command == 'GETDEL':
            return self.data.pop(key, None)
        if command == 'MGET':
            return [self.data.get(k) for k in args]
        if command == 'DEL':
            return sum(self.data.pop(k, None) is not None for k in args)
        if command == 'SADD':
            self.data.setdefault(key, set()).add(str(args[1]))
            return 1
        if command == 'SMEMBERS':
            return list(self.data.get(key, set()))
        if command == 'LPUSH':
            self.data.setdefault(key, []).insert(0, args[1])
            return len(self.data[key])
        if command == 'LTRIM':
            self.data[key] = self.data.get(key, [])[int(args[1]):int(args[2]) + 1]
            return 'OK'
        if command == 'LRANGE':
            return self.data.get(key, [])[int(args[1]):int(args[2]) + 1]
        raise AssertionError('Unexpected Redis command: ' + command)


class BotTest(unittest.TestCase):
    def setUp(self):
        self.db = FakeRedis()
        self.messages = []
        self.telegram_calls = []
        self.uid = 202
        self.owner = 101
        self.fake_now = datetime(2026, 9, 19, 1, 0, tzinfo=ZoneInfo('Europe/Moscow'))
        for p in (patch.object(bot, 'rc', self.db), patch.object(bot, 'tg', self.telegram),
                  patch.object(bot, 'OWNER', self.owner), patch.object(bot, 'TOKEN', 'test-not-a-real-token'),
                  patch.object(bot, 'now', self.clock)):
            p.start()
            self.addCleanup(p.stop)
        bot.ensure({'id': self.uid, 'first_name': 'Тестовый участник'}, self.owner)
        self.messages.clear()

    def clock(self, user=None):
        return self.fake_now.astimezone(ZoneInfo((user or {}).get('timezone', 'Europe/Moscow')))

    def telegram(self, method, payload):
        self.telegram_calls.append((method, copy.deepcopy(payload)))
        if method == 'sendMessage':
            self.assertLessEqual(len(payload['text'].encode('utf-16-le')) // 2, 4096)
            self.messages.append(copy.deepcopy(payload))
        if method == 'getMe':
            return {'ok': True, 'result': {'username': 'offline_test_bot'}}
        return {'ok': True, 'result': {'message_id': len(self.messages)}}

    def text(self, text, uid=None, chat_type='private'):
        uid = self.uid if uid is None else uid
        bot.handle_text({'from': {'id': uid, 'first_name': 'Тестовый участник'},
                         'chat': {'id': uid, 'type': chat_type}, 'text': text, 'message_id': 42})

    def tap(self, data, uid=None, chat_type='private'):
        uid = self.uid if uid is None else uid
        bot.callback({'id': 'offline_callback', 'from': {'id': uid}, 'data': data,
                      'message': {'chat': {'id': uid, 'type': chat_type}}})

    def entry(self, uid=None, label='Ответ', **kwargs):
        p = {'uid': self.uid if uid is None else uid, 'name': 'Тестовый участник',
             'mode': 'evening', 'date': '2026-09-18', 'time': '18.09.2026 22:30',
             'answers': [label] * 12, **kwargs}
        self.db('LPUSH', f'entries:{p["uid"]}', json.dumps(p, ensure_ascii=False))
        return p

    def test_menu_no_recursion(self):
        for i in range(20):
            text = str(bot.menu(self.owner))
            self.assertEqual(text.count('🙏 Молитвы'), 1)
            self.assertIn('🛠 Админка', text)
        self.assertNotIn('🛠 Админка', str(bot.menu(self.uid)))

    def test_all_prayers(self):
        self.text('/prayers')
        for key in library.PRAYERS:
            self.tap('p:' + key)
            self.assertIn('адаптация', self.messages[-1]['text'])

    def test_all_book_sections(self):
        self.text('/bigbook')
        for key in library.BK:
            self.tap('bk:' + key)
            self.assertIn('не полный текст', self.messages[-1]['text'])

    def test_unknown_library_item(self):
        self.tap('p:missing')
        self.assertIn('не найден', self.messages[-1]['text'])

    def test_library_back(self):
        self.tap('library:prayers')
        self.assertIn('inline_keyboard', self.messages[-1]['reply_markup'])

    def test_evening_complete_keeps_storage_schema(self):
        self.text('/evening')
        for i in range(12):
            self.text(f'Ответ {i + 1}')
        p = bot.gj('entry:evening:202:2026-09-19')
        self.assertEqual(len(p['answers']), 12)
        self.assertIsNotNone(bot.gj('done:evening:2026-09-19:202'))
        self.assertIsNone(bot.gj('s:202'))
        self.assertEqual(len(self.db.data['entries:202']), 1)
        self.assertTrue(any(m['chat_id'] == self.owner for m in self.messages))

    def test_other_flows_complete(self):
        for mode, count in (('morning', 5), ('tenth', 6)):
            self.text('/' + mode)
            for _ in range(count):
                self.text('Тестовый ответ')
            self.assertEqual(len(bot.gj(f'entry:{mode}:202:2026-09-19')['answers']), count)

    def test_reading_does_not_consume_answer(self):
        self.text('/evening')
        self.text('Один ответ')
        before = bot.gj('s:202')
        for cmd in ('🙏 Молитвы', '📘 Большая книга', '📚 Мои ответы', '📖 Чтение дня', '/history'):
            self.text(cmd)
        self.assertEqual(bot.gj('s:202'), before)

    def test_start_preserves_unfinished_answers(self):
        self.text('/evening'); self.text('Первый ответ')
        before = bot.gj('s:202')
        self.text('/start')
        self.assertEqual(bot.gj('s:202'), before)
        self.assertIn('Продолжить', str(self.messages[-1]))

    def test_resume(self):
        self.text('/evening'); self.text('Первый ответ'); self.text('/resume')
        self.assertIn('Вопрос 2 из 12', self.messages[-1]['text'])

    def test_task_add_keeps_evening_draft(self):
        self.text('/evening'); self.text('Сохранить')
        before = bot.gj('s:202')
        self.tap('task:add'); self.text('Новое задание')
        self.assertEqual(bot.gj('s:202'), before)
        self.assertEqual(bot.gj('tasks:202')[0]['text'], 'Новое задание')

    def test_sobriety_keeps_evening_draft(self):
        self.text('/evening'); before = bot.gj('s:202')
        self.text('/sobriety'); self.text('01.09.2026')
        self.assertEqual(bot.gj('s:202'), before)

    def test_resume_leaves_setting_form(self):
        self.text('/evening'); self.text('/reminder'); self.text('/resume'); self.text('Ответ')
        self.assertEqual(bot.gj('s:202')['answers'], ['Ответ'])
        self.assertIsNone(bot.gj('s:202:settings'))

    def test_repeated_evening_does_not_erase_answers(self):
        self.text('/evening'); self.text('Первый ответ'); self.text('/evening')
        self.assertEqual(bot.gj('s:202')['answers'], ['Первый ответ'])

    def test_other_flow_cannot_erase_session(self):
        self.text('/evening'); self.text('Первый ответ'); self.text('/morning')
        self.assertEqual(bot.gj('s:202')['mode'], 'evening')

    def test_sos_has_priority_over_form(self):
        self.text('/evening'); before = bot.gj('s:202')
        self.text('🆘 Мне тяжело')
        self.assertEqual(bot.gj('s:202'), before)
        self.assertTrue(any(m['chat_id'] == self.owner and 'просит связаться' in m['text'] for m in self.messages))

    def test_cancel_preserves_completed_reports(self):
        p = self.entry()
        self.text('/evening'); self.text('/reminder'); self.text('/cancel')
        self.assertIsNone(bot.gj('s:202')); self.assertIsNone(bot.gj('s:202:settings'))
        self.assertEqual(features.records(bot, self.uid), [p])

    def test_cancel_not_added_as_task(self):
        self.tap('task:add'); self.text('/cancel')
        self.assertFalse(bot.gj('tasks:202', []))

    def test_unknown_command_not_added_as_task(self):
        self.tap('task:add'); self.text('/unknown')
        self.assertFalse(bot.gj('tasks:202', []))

    def test_empty_answer_does_not_advance(self):
        self.text('/evening'); self.text('')
        self.assertEqual(bot.gj('s:202')['i'], 0)

    def test_group_message_has_no_effect(self):
        before = copy.deepcopy(self.db.data)
        self.text('/evening', chat_type='group')
        self.assertEqual(self.db.data, before)

    def test_group_callback_has_no_effect(self):
        before = copy.deepcopy(self.db.data)
        self.tap('flow:evening', chat_type='group')
        self.assertEqual(self.db.data, before)

    def test_nonowner_cannot_admin(self):
        self.text('/admin')
        self.assertNotIn('Панель спонсора', self.messages[-1]['text'])
        self.tap('a:users')
        self.assertNotIn('Подопечные', self.messages[-1]['text'])

    def test_long_report_sent_in_multiple_parts(self):
        self.text('/evening')
        for _ in range(12):
            self.text('<страх> & 🙏 ' * 200)
        parts = [m for m in self.messages if m['chat_id'] == self.owner]
        self.assertGreater(len(parts), 1)
        self.assertIn('<страх>', ''.join(m['text'] for m in parts))
        self.assertTrue(all('parse_mode' not in m for m in parts))
        self.assertIsNone(bot.gj('s:202'))

    def test_history_is_read_only_and_fingerprint_survives_new_entries(self):
        p = self.entry(label='Искомый ответ')
        self.text('/history')
        key = self.messages[-1]['reply_markup']['inline_keyboard'][0][0]['callback_data']
        self.entry(label='Более новый ответ')
        before = copy.deepcopy(self.db.data)
        self.tap(key)
        self.assertIn('Искомый ответ', self.messages[-1]['text'])
        self.assertEqual(self.db.data, before)
        self.assertEqual(key, f'h:entry:202:{features.fingerprint(p)}')

    def test_foreign_history_denied(self):
        self.entry(uid=303, label='ЧУЖОЙ_ОТВЕТ')
        self.tap('h:page:303:0')
        self.assertIn('Нет доступа', self.messages[-1]['text'])
        self.assertNotIn('ЧУЖОЙ', str(self.messages))

    def test_owner_history_requires_explicit_link(self):
        p = self.entry()
        self.tap('h:page:202:0', self.owner)
        self.assertIn('История', self.messages[-1]['text'])
        self.entry(uid=303)
        self.tap('h:page:303:0', self.owner)
        self.assertIn('Нет доступа', self.messages[-1]['text'])

    def test_card_permission(self):
        self.tap('card:202', self.owner)
        self.assertIn('История ответов', str(self.messages[-1]))
        self.tap('card:202', 303)
        self.assertIn('Нет доступа', self.messages[-1]['text'])

    def test_history_pagination_and_bad_records(self):
        for i in range(13):
            self.entry(label=str(i))
        self.db('LPUSH', 'entries:202', 'broken-json')
        self.db('LPUSH', 'entries:202', '{}')
        self.text('/history')
        self.assertIn('1/3', self.messages[-1]['text'])
        self.tap('h:page:202:2')
        self.assertIn('3/3', self.messages[-1]['text'])

    def test_malformed_history_callback(self):
        self.tap('h:page:bad:0')
        self.assertIn('заново', self.messages[-1]['text'])

    def test_missing_history_item(self):
        self.entry(); self.tap('h:entry:202:doesnotexist')
        self.assertIn('не найдена', self.messages[-1]['text'])

    def test_reminder_setting_does_not_claim_delivery(self):
        self.text('/evening'); self.text('/reminder'); self.text('22:30')
        self.assertEqual(bot.gj('u:202')['reminder_time'], '22:30')
        self.assertEqual(bot.gj('s:202')['i'], 0)
        self.assertIn('только после подключения', self.messages[-1]['text'])

    def test_bad_reminder_time(self):
        self.text('/reminder'); self.text('24:00')
        self.assertNotIn('reminder_time', bot.gj('u:202'))

    def test_stop_reminder(self):
        self.text('/reminder'); self.text('22:30'); self.text('/stop_reminder')
        self.assertFalse(bot.gj('u:202')['reminder_enabled'])

    def test_timezone(self):
        self.text('/timezone'); self.tap('tz:Europe/Helsinki')
        self.assertEqual(bot.gj('u:202')['timezone'], 'Europe/Helsinki')
        self.tap('tz:Invalid/Zone')
        self.assertEqual(bot.gj('u:202')['timezone'], 'Europe/Helsinki')

    def test_future_sobriety_date_rejected(self):
        self.text('/sobriety'); self.text('01.01.2099')
        self.assertNotIn('sobriety_date', bot.gj('u:202'))
        self.text('01.09.2026')
        self.assertIn('18 дней', self.messages[-1]['text'])

    def test_sobriety_reset(self):
        self.text('/sobriety'); self.text('01.09.2026')
        self.text('/sobriety_reset'); self.text('10.09.2026')
        self.assertEqual(bot.gj('u:202')['sobriety_date'], '2026-09-10')

    def test_specific_task_completion_and_duplicate_tap(self):
        bot.sj('tasks:202', [{'text': 'Первое', 'done': False}, {'text': 'Второе', 'done': False}])
        self.tap('task:done')
        self.assertFalse(any(x['done'] for x in bot.gj('tasks:202')))
        self.tap('task:finish:1'); self.tap('task:finish:1')
        self.assertEqual([x['done'] for x in bot.gj('tasks:202')], [False, True])

    def test_expired_ack_does_not_drop_action(self):
        normal = self.telegram
        def expired(method, payload):
            if method == 'answerCallbackQuery':
                raise HTTPError('https://example.invalid', 400, 'expired', {}, None)
            return normal(method, payload)
        with patch.object(bot, 'tg', expired):
            self.tap('p:serenity')
        self.assertIn('душевном покое', self.messages[-1]['text'])

    def test_unknown_flow_button(self):
        self.tap('flow:unknown')
        self.assertIn('устарела', self.messages[-1]['text'])

    def test_streak_survives_morning_and_legacy_days(self):
        for i in range(1, 6):
            day = (self.fake_now.date() - timedelta(days=i)).isoformat()
            prefix = 'done' if i == 4 else 'done:evening'
            bot.sj(f'{prefix}:{day}:202', {'time': 'test'})
        w, m, streak = bot.stats(self.uid)
        self.assertEqual((w, m, streak), (5, 5, 5))
        self.assertEqual(sum(c[0] == 'MGET' for c in self.db.calls), 1)

    def test_today_uses_participant_timezone(self):
        user = bot.gj('u:202'); user['timezone'] = 'America/Los_Angeles'
        bot.sj('u:202', user)
        bot.sj('done:evening:2026-09-18:202', {'time': 'test'})
        self.assertTrue(bot.completed_today(user))

    def test_atomic_invitation_read(self):
        self.db('SET', 'invite:test', '101')
        self.text('/start invite_test')
        self.assertIsNone(self.db('GET', 'invite:test'))
        self.assertEqual(bot.gj('u:202')['sponsor_id'], 101)


class MessageTest(unittest.TestCase):
    def test_utf16_limit_and_lossless_reassembly(self):
        original = 'а🙏<&>\n' * 2000
        result = chunks(original)
        self.assertEqual(''.join(result), original)
        self.assertTrue(all(len(x.encode('utf-16-le')) // 2 <= 3500 for x in result))

    def test_long_html_entities_preserved_as_plain_text(self):
        result = message_parts('<b>Заголовок</b>\n' + '&lt;x&gt;&amp;' * 2000)
        self.assertEqual(''.join(p['text'] for p in result), 'Заголовок\n' + '<x>&' * 2000)

    def test_short_html_format_preserved(self):
        self.assertEqual(message_parts('<b>Тест</b>'), [{'text': '<b>Тест</b>', 'parse_mode': 'HTML'}])


class WebhookTest(unittest.TestCase):
    secret = 's' * 32

    def request(self, method='POST', payload=None, headers=None, query='', env=None):
        h = object.__new__(bot.handler)
        h.path = '/api/index' + query
        raw = json.dumps(payload or {'update_id': 1}).encode()
        h.headers = {'Content-Length': str(len(raw)), **(headers or {})}
        h.rfile = io.BytesIO(raw)
        h.out = lambda code=200, obj=None: (code, obj)
        h.dispatch = lambda update: None
        h.configure = lambda url, secret: {'ok': True, 'url': url}
        with patch.dict(os.environ, {'WEBHOOK_SECRET': self.secret, 'VERCEL_ENV': 'production',
                                    'PUBLIC_BASE_URL': 'https://example.vercel.app', **(env or {})}):
            return getattr(h, 'do_' + method)()

    def test_missing_auth_denied(self):
        self.assertEqual(self.request()[0], 403)

    def test_valid_auth(self):
        self.assertEqual(self.request(headers={'X-Telegram-Bot-Api-Secret-Token': self.secret})[0], 200)

    def test_missing_secret_fails_closed(self):
        self.assertEqual(self.request(env={'WEBHOOK_SECRET': ''})[0], 503)

    def test_preview_no_writes(self):
        self.assertEqual(self.request(env={'VERCEL_ENV': 'preview'},
                                      headers={'X-Telegram-Bot-Api-Secret-Token': self.secret})[0], 403)

    def test_get_cannot_change_webhook(self):
        self.assertEqual(self.request('GET', query='?setup=1')[0], 405)

    def test_get_health(self):
        code, result = self.request('GET')
        self.assertEqual(code, 200); self.assertEqual(result['version'], '2026.09.19-review1')

    def test_setup_needs_auth_and_uses_configured_host(self):
        self.assertEqual(self.request(query='?setup=1')[0], 403)
        code, result = self.request(query='?setup=1', headers={'Authorization': 'Bearer ' + self.secret, 'Host': 'wrong.example'})
        self.assertEqual(code, 200)
        self.assertEqual(result['url'], 'https://example.vercel.app/api/index')

    def test_setup_invalid_url_rejected(self):
        self.assertEqual(self.request(query='?setup=1', headers={'Authorization': 'Bearer ' + self.secret},
                                      env={'PUBLIC_BASE_URL': 'http://wrong.example'})[0], 503)

    def test_oversized_body(self):
        self.assertEqual(self.request(headers={'Content-Length': '9999999',
                             'X-Telegram-Bot-Api-Secret-Token': self.secret})[0], 413)

    def test_bad_content_length(self):
        self.assertEqual(self.request(headers={'Content-Length': 'bad',
                            'X-Telegram-Bot-Api-Secret-Token': self.secret})[0], 400)


if __name__ == '__main__':
    unittest.main()
