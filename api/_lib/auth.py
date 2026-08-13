"""HTTP Basic Auth for every route.

Nothing on this deployment is publicly readable: the dashboard and the data
endpoint both call require_auth() before writing a byte of response. That is
the whole point of the Vercel move - on GitHub Pages and the Webflow CDN the
payload sat behind an unauthenticated URL.
"""
import base64
import hmac
import os


def _eq(a, b):
    """Constant-time compare, so a wrong password cannot be found by timing."""
    return hmac.compare_digest(a.encode('utf-8'), b.encode('utf-8'))


def check(header):
    user = os.environ.get('DASH_USER')
    pw = os.environ.get('DASH_PASS')
    if not user or not pw:
        return False
    if not header or not header.lower().startswith('basic '):
        return False
    try:
        raw = base64.b64decode(header.split(None, 1)[1]).decode('utf-8')
        got_user, got_pw = raw.split(':', 1)
    except Exception:                                      # noqa: BLE001
        return False
    # evaluate both, then AND - avoids leaking which half was wrong
    ok_user = _eq(got_user, user)
    ok_pw = _eq(got_pw, pw)
    return ok_user and ok_pw


def require(handler):
    """Return True when authorised; otherwise send a 401 and return False."""
    if check(handler.headers.get('Authorization')):
        return True
    body = b'Authentication required.'
    handler.send_response(401)
    handler.send_header('WWW-Authenticate', 'Basic realm="Style Lounge Dashboard"')
    handler.send_header('Content-Type', 'text/plain; charset=utf-8')
    handler.send_header('Content-Length', str(len(body)))
    handler.send_header('Cache-Control', 'no-store')
    handler.end_headers()
    handler.wfile.write(body)
    return False


def is_cron(handler):
    """Vercel signs scheduled invocations with CRON_SECRET."""
    secret = os.environ.get('CRON_SECRET')
    if not secret:
        return False
    got = handler.headers.get('Authorization') or ''
    return got.startswith('Bearer ') and _eq(got[7:], secret)
