"""Single entrypoint for the dashboard - handles every route.

Why one function rather than three files in /api: with the Python framework
preset, Vercel routes *all* traffic to a single entrypoint and ignores the
per-file split, so /api/data quietly returned the HTML page. Dispatching on
the path here works under both the "Python" and "Other" presets, which makes
the deployment independent of a dashboard setting that is easy to get wrong.

Routes (all require Basic Auth):
    /              the dashboard page
    /api/data      the payload as JSON, from cache when available
    /api/refresh   recompute from the sheets and cache the result

Nothing is served as a static file, so there is no unauthenticated path to
any of it - which is the whole reason this deployment exists.
"""
import json
import os
import sys
import time
import traceback
from http.server import BaseHTTPRequestHandler
from urllib.parse import urlparse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from _lib import auth, store                          # noqa: E402

PAGE = os.path.join(os.path.dirname(os.path.abspath(__file__)), '_lib', 'template.html')

with open(PAGE, encoding='utf-8') as _fh:             # read once per cold start
    _HTML = _fh.read().encode('utf-8')


class handler(BaseHTTPRequestHandler):

    # ------------------------------------------------------------------ helpers
    def _send(self, code, body, ctype, extra=None):
        self.send_response(code)
        self.send_header('Content-Type', ctype)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'private, no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Referrer-Policy', 'no-referrer')
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if body:
            self.wfile.write(body)

    def _json(self, code, obj, extra=None):
        self._send(code, json.dumps(obj, default=str).encode('utf-8'),
                   'application/json; charset=utf-8', extra)

    # ------------------------------------------------------------------ routes
    def _page(self):
        self._send(200, _HTML, 'text/html; charset=utf-8')

    def _data(self):
        try:
            payload = store.load()
            cached = payload is not None
            if not cached:
                from _lib import pipeline
                payload = pipeline.run()
                if store.available():
                    try:
                        store.save(payload)
                    except Exception:                  # noqa: BLE001
                        traceback.print_exc()          # serving still succeeds
            self._json(200, payload,
                       {'X-Payload-Source': 'cache' if cached else 'computed'})
        except Exception as exc:                       # noqa: BLE001
            traceback.print_exc()
            self._json(500, {'error': type(exc).__name__, 'detail': str(exc)[:400]})

    def _refresh(self):
        t0 = time.time()
        try:
            from _lib import pipeline
            payload = pipeline.run()
            stored = store.save(payload) if store.available() else 0
            self._json(200, {
                'ok': True,
                'seconds': round(time.time() - t0, 1),
                'generated': payload.get('generated'),
                'customers': payload.get('totals', {}).get('customers'),
                'events': payload.get('totals', {}).get('events'),
                'timelines': len(payload.get('timelines', [])),
                'cached_bytes': stored,
                'cache': 'stored' if stored else 'none (no store configured)',
            })
        except Exception as exc:                       # noqa: BLE001
            traceback.print_exc()
            self._json(500, {'ok': False, 'seconds': round(time.time() - t0, 1),
                             'error': type(exc).__name__, 'detail': str(exc)[:400]})

    # ------------------------------------------------------------------ entry
    def do_GET(self):                                  # noqa: N802
        path = urlparse(self.path).path.rstrip('/') or '/'

        # the scheduler calls /api/refresh with CRON_SECRET rather than a password
        if path == '/api/refresh' and auth.is_cron(self):
            return self._refresh()

        if not auth.require(self):
            return

        if path == '/api/data':
            return self._data()
        if path == '/api/refresh':
            return self._refresh()
        return self._page()

    def do_HEAD(self):                                 # noqa: N802
        """Without this, HEAD returns 501 and uptime monitors report an outage."""
        if not auth.require(self):
            return
        self._send(200, b'', 'text/html; charset=utf-8')

    def log_message(self, fmt, *args):                 # noqa: A003
        # default logging writes the full request line, which would put the
        # Basic Auth header path into the log; keep it to method and status
        sys.stderr.write('%s %s\n' % (self.command, self.path.split('?')[0]))
