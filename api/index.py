"""GET / - the dashboard page, behind Basic Auth.

The HTML is served by a function rather than as a static file so there is no
unauthenticated path to anything. On Vercel, static files bypass function
auth entirely, which is exactly the hole this deployment exists to close.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from http.server import BaseHTTPRequestHandler        # noqa: E402
from _lib import auth                                 # noqa: E402

PAGE = os.path.join(os.path.dirname(os.path.abspath(__file__)), '_lib', 'template.html')

with open(PAGE, encoding='utf-8') as _fh:             # read once per cold start
    _HTML = _fh.read().encode('utf-8')


class handler(BaseHTTPRequestHandler):
    def do_GET(self):                                  # noqa: N802
        if not auth.require(self):
            return
        self.send_response(200)
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.send_header('Content-Length', str(len(_HTML)))
        self.send_header('Cache-Control', 'private, no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Referrer-Policy', 'no-referrer')
        self.end_headers()
        self.wfile.write(_HTML)
