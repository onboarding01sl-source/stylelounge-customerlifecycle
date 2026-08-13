"""GET /api/data - the dashboard payload, behind Basic Auth.

Serves the cached copy written by the nightly cron. If the cache is empty
(first deploy, or KV not configured) it computes on demand instead, which
takes ~27s.
"""
import json
import os
import sys
import traceback

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from http.server import BaseHTTPRequestHandler        # noqa: E402
from _lib import auth, store                          # noqa: E402


class handler(BaseHTTPRequestHandler):
    def do_GET(self):                                  # noqa: N802
        if not auth.require(self):
            return

        try:
            payload = store.load()
            cached = payload is not None
            if not cached:
                from _lib import pipeline
                payload = pipeline.run()
                if store.available():
                    store.save(payload)

            body = json.dumps(payload, default=str).encode('utf-8')
            self.send_response(200)
            self.send_header('Content-Type', 'application/json; charset=utf-8')
            self.send_header('Content-Length', str(len(body)))
            # never let a shared cache hold customer data
            self.send_header('Cache-Control', 'private, no-store')
            self.send_header('X-Payload-Source', 'cache' if cached else 'computed')
            self.end_headers()
            self.wfile.write(body)
        except Exception as exc:                       # noqa: BLE001
            traceback.print_exc()
            body = json.dumps({'error': type(exc).__name__, 'detail': str(exc)[:400]}).encode()
            self.send_response(500)
            self.send_header('Content-Type', 'application/json; charset=utf-8')
            self.send_header('Content-Length', str(len(body)))
            self.send_header('Cache-Control', 'no-store')
            self.end_headers()
            self.wfile.write(body)
