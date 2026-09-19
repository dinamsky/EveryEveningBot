"""Run only after an approved production deployment; never print credentials."""
import json
import os
import sys
import urllib.error
import urllib.request
from urllib.parse import urlparse


def main():
    root = os.getenv('PUBLIC_BASE_URL', '').rstrip('/')
    secret = os.getenv('WEBHOOK_SECRET', '').strip()
    parsed = urlparse(root)
    if not secret or parsed.scheme != 'https' or not parsed.hostname or parsed.path or parsed.query or parsed.fragment or parsed.username:
        raise SystemExit('Set WEBHOOK_SECRET and a valid PUBLIC_BASE_URL locally.')
    request = urllib.request.Request(root + '/api/index?setup=1', data=b'{}',
        headers={'Authorization': 'Bearer ' + secret, 'Content-Type': 'application/json'}, method='POST')
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            result = json.load(response)
    except urllib.error.HTTPError as error:
        raise SystemExit(f'Setup failed: HTTP {error.code}. Check deployment and environment variables.') from None
    except (OSError, ValueError):
        raise SystemExit('Setup failed: network or invalid response. No credentials printed.') from None
    if not result.get('ok'):
        raise SystemExit('Telegram did not confirm webhook configuration.')
    print('Webhook configured; pending updates were not dropped.')


if __name__ == '__main__':
    main()
