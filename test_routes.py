"""Exercise the real handler's routing and auth in-process, no network.

Feeds fake requests through index.handler and checks each path returns the
right thing. Catches routing regressions before a deploy.
"""
import base64
import io
import json
import os
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

APP = r"D:\Style Lounge\Customer Life Cycle Style lounge\vercel-app\api"
sys.path.insert(0, APP)

os.environ['DASH_USER'] = 'team'
os.environ['DASH_PASS'] = 'pw'
os.environ['CRON_SECRET'] = 'cronsecret'
os.environ.pop('GOOGLE_CREDS', None)          # force the pipeline to fail fast
os.environ.pop('KV_REST_API_URL', None)
os.environ.pop('UPSTASH_REDIS_REST_URL', None)

import index                                             # noqa: E402


class FakeSocket(io.BytesIO):
    def makefile(self, *a, **k):
        return self


def request(path, authz=None, method='GET'):
    lines = ['%s %s HTTP/1.1' % (method, path), 'Host: x']
    if authz:
        lines.append('Authorization: %s' % authz)
    raw = ('\r\n'.join(lines) + '\r\n\r\n').encode()

    class H(index.handler):
        def __init__(self, data):
            self.rfile = io.BytesIO(data)
            self.wfile = io.BytesIO()
            self.client_address = ('127.0.0.1', 0)
            self.server = None
            self.connection = None
            self.handle_one_request()

        def log_message(self, *a):
            pass

        def send_response(self, code, message=None):
            self._code = code
            super().send_response(code, message)

    h = H(raw)
    out = h.wfile.getvalue().decode('utf-8', 'replace')
    status = int(out.split()[1]) if out.startswith('HTTP') else 0
    ctype = ''
    for line in out.split('\r\n'):
        if line.lower().startswith('content-type:'):
            ctype = line.split(':', 1)[1].strip()
    body = out.split('\r\n\r\n', 1)[1] if '\r\n\r\n' in out else ''
    return status, ctype, body


def basic(u, p):
    return 'Basic ' + base64.b64encode(('%s:%s' % (u, p)).encode()).decode()


GOOD = basic('team', 'pw')
CASES = [
    ('/',             None,             401, None,   'no auth -> challenge'),
    ('/api/data',     None,             401, None,   'no auth -> challenge'),
    ('/api/refresh',  None,             401, None,   'no auth -> challenge'),
    ('/',             basic('x', 'y'),  401, None,   'bad creds -> challenge'),
    ('/',             GOOD,             200, 'html', 'dashboard page'),
    ('/dashboard',    GOOD,             200, 'html', 'alias -> page'),
    ('/whatever',     GOOD,             200, 'html', 'unknown -> page'),
    ('/api/data',     GOOD,             500, 'json', 'data (no creds configured)'),
    ('/api/refresh',  GOOD,             500, 'json', 'refresh (no creds configured)'),
]

print('%-14s %-22s %-6s %-6s %s' % ('PATH', 'CASE', 'CODE', 'TYPE', 'RESULT'))
print('-' * 78)
fails = 0
for path, authz, want_code, want_kind, label in CASES:
    code, ctype, body = request(path, authz)
    kind = 'json' if 'json' in ctype else ('html' if 'html' in ctype else 'other')
    ok = code == want_code and (want_kind is None or kind == want_kind)
    fails += not ok
    print('%-14s %-22s %-6s %-6s %s' % (path, label, code, kind, 'ok' if ok else 'FAIL'))

# the JSON routes must return JSON, never the HTML page - the deployed bug
code, ctype, body = request('/api/data', GOOD)
is_html = body.lstrip().startswith('<!doctype')
print()
print('/api/data returns HTML instead of JSON :', is_html, '(must be False)')
fails += is_html

code, ctype, _ = request('/', GOOD, method='HEAD')
print('HEAD / status                          :', code, '(must be 200, was 501)')
fails += (code != 200)

code, _, _ = request('/api/refresh', 'Bearer cronsecret')
print('cron bearer reaches refresh            :', code, '(must not be 401)')
fails += (code == 401)

code, _, _ = request('/api/refresh', 'Bearer wrong')
print('wrong cron bearer rejected             :', code, '(must be 401)')
fails += (code != 401)

print()
print('FAILURES:', fails)
sys.exit(1 if fails else 0)
