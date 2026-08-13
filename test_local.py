"""Run the Vercel pipeline locally and check it against the known-good figures.

    python test_local.py

Reads the service-account key from ../dashboard/service_account1.json and puts
it in GOOGLE_CREDS, which is how the deployed functions receive it.
"""
import io
import json
import os
import sys
import time
import warnings

warnings.filterwarnings('ignore')
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, 'api'))

KEY = os.path.join(os.path.dirname(HERE), 'dashboard', 'service_account1.json')
if 'GOOGLE_CREDS' not in os.environ:
    with open(KEY, encoding='utf-8') as fh:
        os.environ['GOOGLE_CREDS'] = fh.read()

from _lib import pipeline                                   # noqa: E402

t0 = time.time()
payload = pipeline.run()
elapsed = time.time() - t0

t = payload['totals']
print('ran in %.1fs\n' % elapsed)
print('customers  %d' % t['customers'])
print('events     %d' % t['events'])
print('registered %d' % t['registered'])
print('bookers    %d' % t['bookers'])
print('members    %d' % t['members'])
print('revenue    %.0f' % t['revenue'])
print('at risk    %d' % t['at_risk'])
print('timelines  %d' % len(payload['timelines']))

# figures established from the verified local pipeline
EXPECT = dict(customers=14523, events=20786, registered=14293,
              bookers=1287, members=106, at_risk=908)
bad = [(k, v, t[k]) for k, v in EXPECT.items() if t[k] != v]
rev_ok = abs(t['revenue'] - 1163776) < 2

print()
if bad or not rev_ok:
    for k, want, got in bad:
        print('MISMATCH %-11s expected %-8s got %s' % (k, want, got))
    if not rev_ok:
        print('MISMATCH revenue     expected ~1163776 got %.0f' % t['revenue'])
    sys.exit(1)

wa = {r['cat']: r for r in payload['wa_by_category']}
print('checks passed - matches the verified pipeline')
print('  Never Booked  : %d nudges -> %d bookings (%.1f%%)'
      % (wa['Never Booked']['n'], wa['Never Booked']['booked'], wa['Never Booked']['rate']))
print('  3rd Time Users: %d nudges -> %d bookings (%.1f%%)'
      % (wa['3rd Time Users']['n'], wa['3rd Time Users']['booked'],
         wa['3rd Time Users']['rate']))

raw = json.dumps(payload, default=str).encode()
import gzip
print('\npayload %.0f KB raw, %.0f KB gzipped'
      % (len(raw) / 1024, len(gzip.compress(raw, 9)) / 1024))
