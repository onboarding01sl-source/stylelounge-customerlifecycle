"""Serve the generated Vercel page locally with a stub /api/data, to confirm
the rewritten boot path renders. Localhost only; nothing leaves the machine."""
import http.server
import os
import socketserver

BASE = r"D:\Style Lounge\Customer Life Cycle Style lounge"
PAGE = os.path.join(BASE, 'vercel-app', 'api', '_lib', 'template.html')
DATA = os.path.join(BASE, 'dashboard', 'dash.json')

HTML = open(PAGE, 'rb').read()
JSON = open(DATA, 'rb').read()


class H(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        if 'route=refresh' in self.path:
            import json as _j, time as _t
            _t.sleep(2)          # stand in for the real ~20s sheet read
            d = _j.loads(JSON)
            body = _j.dumps({'ok': True, 'seconds': 2.0,
                             'generated': d.get('generated'),
                             'customers': d['totals']['customers'],
                             'events': d['totals']['events'],
                             'timelines': len(d.get('timelines', [])),
                             'kra_days_recorded': 1}).encode()
            ctype = 'application/json; charset=utf-8'
        elif 'route=data' in self.path or self.path.startswith('/api/data'):
            body, ctype = JSON, 'application/json; charset=utf-8'
        else:
            body, ctype = HTML, 'text/html; charset=utf-8'
        self.send_response(200)
        self.send_header('Content-Type', ctype)
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)


with socketserver.TCPServer(('127.0.0.1', 8791), H) as srv:
    print('serving on http://127.0.0.1:8791')
    srv.serve_forever()
