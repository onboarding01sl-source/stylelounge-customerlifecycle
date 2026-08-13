"""Cache the computed payload in Vercel KV (Upstash Redis over REST).

Why a store at all: a full run takes ~27s (16s of that is just pulling 58k rows
out of the Sheets API, which cannot be parallelised - see sources.py). That is
fine for a nightly job and far too slow for a page load, so the cron writes and
the dashboard reads.

The payload is gzipped before storing: ~1.0 MB of JSON becomes ~74 KB, which
sits comfortably inside the 1 MB per-value limit on the free tier.

If KV is not configured the functions still work - they just compute on demand
and the first load is slow.
"""
import base64
import gzip
import json
import os
import urllib.error
import urllib.request

KEY = 'sl:lifecycle:payload'
TIMEOUT = 15


def _cfg():
    """Find a Redis-compatible REST endpoint under any of its names.

    Vercel retired its own KV product; the same store now arrives through the
    Marketplace as Upstash, which injects UPSTASH_* names. Older projects still
    have KV_*, and the Marketplace sometimes prefixes with the integration name.
    Accept whichever pair is present rather than pinning to one vendor.
    """
    pairs = [
        ('KV_REST_API_URL', 'KV_REST_API_TOKEN'),
        ('UPSTASH_REDIS_REST_URL', 'UPSTASH_REDIS_REST_TOKEN'),
        ('REDIS_REST_URL', 'REDIS_REST_TOKEN'),
        ('STORAGE_REST_API_URL', 'STORAGE_REST_API_TOKEN'),
    ]
    for u, t in pairs:
        url, tok = os.environ.get(u), os.environ.get(t)
        if url and tok:
            return url.rstrip('/'), tok
    return None, None


def available():
    return _cfg()[0] is not None


def _call(path, data=None):
    url, tok = _cfg()
    if not url:
        return None
    req = urllib.request.Request(
        '%s/%s' % (url, path),
        data=data,
        headers={'Authorization': 'Bearer %s' % tok,
                 'Content-Type': 'application/octet-stream'})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        return json.loads(r.read().decode('utf-8'))


def save(payload):
    """Gzip + base64 the payload and SET it. Returns stored size in bytes."""
    blob = gzip.compress(json.dumps(payload, default=str).encode('utf-8'), 9)
    b64 = base64.b64encode(blob)
    _call('set/%s' % KEY, data=b64)
    return len(b64)


def load():
    """Return the cached payload dict, or None if absent/unreadable."""
    try:
        res = _call('get/%s' % KEY)
    except (urllib.error.URLError, OSError, ValueError):
        return None
    if not res:
        return None
    val = res.get('result')
    if not val:
        return None
    try:
        return json.loads(gzip.decompress(base64.b64decode(val)).decode('utf-8'))
    except Exception:                                      # noqa: BLE001
        return None
