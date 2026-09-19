"""Authenticated webhook and an inert preview. No Telegram/Redis calls on GET."""
from http.server import BaseHTTPRequestHandler
import hmac
import json
import logging
import os
import re
from urllib.parse import parse_qs, urlparse

VERSION = '2026.09.19-review1'
MAX_BODY = 65536


def secret_matches(received: str, expected: str) -> bool:
    return bool(expected) and len(received) <= 512 and hmac.compare_digest(
        received.encode('utf-8'), expected.encode('utf-8'))


class WebhookHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        # Never log query strings, update bodies, prayer answers or credential URLs.
        return

    def out(self, code=200, obj=None):
        body = json.dumps(obj or {'ok': True}, ensure_ascii=False).encode('utf-8')
        self.send_response(code)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Cache-Control', 'no-store')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        query = parse_qs(urlparse(self.path).query)
        if 'setup' in query:
            return self.out(405, {'ok': False, 'error': 'Use authenticated POST for setup'})
        return self.out(200, {'ok': True, 'service': 'EveryEveningBot', 'version': VERSION,
                              'preview_read_only': os.getenv('VERCEL_ENV') == 'preview'})

    def do_POST(self):
        # Feature previews cannot share the production token/Redis or switch its webhook.
        if os.getenv('VERCEL_ENV') == 'preview':
            return self.out(403, {'ok': False, 'error': 'Preview is read-only'})
        secret = os.getenv('WEBHOOK_SECRET', '').strip()
        if not re.fullmatch(r'[A-Za-z0-9_-]{32,256}', secret):
            return self.out(503, {'ok': False, 'error': 'Webhook secret is not configured'})
        parsed = urlparse(self.path)
        if parsed.path.rstrip('/') != '/api/index':
            return self.out(404, {'ok': False, 'error': 'Not found'})
        if 'setup' in parse_qs(parsed.query):
            if not secret_matches(self.headers.get('Authorization', ''), 'Bearer ' + secret):
                return self.out(403, {'ok': False, 'error': 'Forbidden'})
            root = os.getenv('PUBLIC_BASE_URL', '').rstrip('/')
            parts = urlparse(root)
            if parts.scheme != 'https' or not parts.hostname or parts.path or parts.query or parts.fragment or parts.username or parts.password:
                return self.out(503, {'ok': False, 'error': 'PUBLIC_BASE_URL is not configured'})
            try:
                return self.out(200, self.configure(root + '/api/index', secret))
            except Exception as error:
                logging.error('Webhook setup failed: %s', type(error).__name__)
                return self.out(502, {'ok': False, 'error': 'Webhook setup failed'})
        if not secret_matches(self.headers.get('X-Telegram-Bot-Api-Secret-Token', ''), secret):
            return self.out(403, {'ok': False, 'error': 'Forbidden'})
        try:
            size = int(self.headers.get('Content-Length', '0'))
        except ValueError:
            return self.out(400, {'ok': False, 'error': 'Invalid length'})
        if not 0 < size <= MAX_BODY:
            return self.out(413, {'ok': False, 'error': 'Invalid body size'})
        try:
            update = json.loads(self.rfile.read(size))
            if not isinstance(update, dict):
                raise ValueError('not an object')
        except (ValueError, UnicodeDecodeError):
            return self.out(400, {'ok': False, 'error': 'Invalid JSON'})
        try:
            self.dispatch(update)
            return self.out()
        except Exception as error:
            logging.error('Bot update failed: %s', type(error).__name__)
            return self.out(503, {'ok': False, 'error': 'Temporarily unavailable'})

    def dispatch(self, update):
        raise NotImplementedError

    def configure(self, url, secret):
        raise NotImplementedError
