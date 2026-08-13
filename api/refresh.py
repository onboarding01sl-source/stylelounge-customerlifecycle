"""GET /api/refresh - recompute from the live Sheets and cache the result.

Called by the Vercel cron each morning. Vercel signs scheduled requests with
CRON_SECRET; a logged-in human may also trigger it with Basic Auth, which is
handy after fixing a sheet.
"""
import json
import os
import sys
import time
import traceback

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from http.server import BaseHTTPRequestHandler        # noqa: E402
from _lib import auth, store                          # noqa: E402


class handler(BaseHTTPRequestHandler):
    def do_GET(self):                                  # noqa: N802
        # either the scheduler or an authenticated human
        if not (auth.is_cron(self) or auth.check(self.headers.get('Authorization'))):
            body = b'Not authorised.'
            self.send_response(401)
            self.send_header('WWW-Authenticate', 'Basic realm="Style Lounge Dashboard"')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        t0 = time.time()
        try:
            from _lib import pipeline
            payload = pipeline.run()
            stored = store.save(payload) if store.available() else 0
            out = {
                'ok': True,
                'seconds': round(time.time() - t0, 1),
                'generated': payload.get('generated'),
                'customers': payload.get('totals', {}).get('customers'),
                'events': payload.get('totals', {}).get('events'),
                'timelines': len(payload.get('timelines', [])),
                'cached_bytes': stored,
                'cache': 'kv' if stored else 'none (KV not configured)',
            }
            code = 200
        except Exception as exc:                       # noqa: BLE001
            traceback.print_exc()
            out = {'ok': False, 'seconds': round(time.time() - t0, 1),
                   'error': type(exc).__name__, 'detail': str(exc)[:400]}
            code = 500

        body = json.dumps(out).encode('utf-8')
        self.send_response(code)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        self.wfile.write(body)
