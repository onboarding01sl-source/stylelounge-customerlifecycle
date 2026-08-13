"""Generate the Vercel dashboard page from the canonical template.

dashboard/template.html stays the single source of truth, so the local build
and the hosted build never drift. This script swaps the tail of its script
block - the passphrase gate and the AES decryption - for a plain authenticated
fetch of /api/data.

Basic Auth at the edge is the access control now, so the gate is not merely
unused here: leaving it in would imply a passphrase still protects something.

Run after editing dashboard/template.html:
    python make_template.py
"""
import os

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(os.path.dirname(HERE), 'dashboard', 'template.html')
OUT = os.path.join(HERE, 'api', '_lib', 'template.html')

tpl = open(SRC, encoding='utf-8').read()

# everything from this comment to the end of the script is gate + decrypt + boot
CUT = '/* ---------- boot: inline payload, or fetch + decrypt ---------- */'
if CUT not in tpl:
    raise SystemExit('boot marker not found in %s' % SRC)

head, _ = tpl.split(CUT, 1)
if '</script>' not in tpl:
    raise SystemExit('no </script> in template')

BOOT = """/* ---------- boot: fetch the payload from the authenticated API ---------- */
(async function boot(){
  const load = document.getElementById('loading');
  try {
    const res = await fetch('/api/data', {cache:'no-store'});
    if (!res.ok) throw new Error('HTTP ' + res.status);
    D = await res.json();
    if (D && D.error) throw new Error(D.detail || D.error);
  } catch (err) {
    load.innerHTML =
      '<h2>Could not load the data</h2>' +
      '<p class="sub">' + String(err.message || err).replace(/[<>&]/g,'') + '</p>' +
      '<p class="sub">If this is the first load after a deploy, the four sheets ' +
      'are still being read - that takes about half a minute. Refresh shortly.</p>';
    return;
  }
  load.remove();
  document.querySelector('.wrap').hidden = false;
  renderAll();
})();
</script>
"""

body = head + BOOT

LOADING = '''<div id="loading" class="wrap" style="padding-top:80px">
  <p class="eyebrow">Style Lounge &middot; Customer Intelligence</p>
  <h2>Reading the latest data&hellip;</h2>
  <p class="sub">Pulling registrations, bookings, nudges and memberships
     from the four live sheets.</p>
</div>
'''
WRAP = '<div class="wrap" hidden>'
if WRAP not in body:
    raise SystemExit('content wrapper not found')
body = body.replace(WRAP, LOADING + WRAP, 1)

# a standalone page needs a real head; the artifact wrapper used to supply one
head_html = ('<!doctype html>\n<html lang="en">\n<head>\n<meta charset="utf-8">\n'
             '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
             '<meta name="robots" content="noindex, nofollow">\n'
             '<meta name="referrer" content="no-referrer">\n')
body = head_html + body
body = body.replace('</style>\n', '</style>\n</head>\n<body>\n', 1)
body += '\n</body>\n</html>\n'

os.makedirs(os.path.dirname(OUT), exist_ok=True)
with open(OUT, 'w', encoding='utf-8') as fh:
    fh.write(body)

for bad, msg in [('decryptPayload', 'decryption code survived'),
                 ('showGate', 'passphrase gate survived'),
                 ('data.enc.json', 'encrypted-file reference survived'),
                 ('__SL_INLINE__', 'inline-payload branch survived')]:
    if bad in body:
        raise SystemExit('FAIL: %s (%r still present)' % (msg, bad))
if "fetch('/api/data'" not in body:
    raise SystemExit('FAIL: /api/data fetch missing')

print('wrote %s (%.1f KB)' % (OUT, os.path.getsize(OUT) / 1024))
print('checks passed: no gate, no decryption, fetches /api/data')
