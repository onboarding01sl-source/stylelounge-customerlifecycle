"""Run the pipeline against the live sheets and check it for internal consistency.

    python test_local.py

Deliberately checks *invariants*, not frozen totals. The sheets change every
day, so asserting "customers == 14523" only guarantees the test rots. These
checks stay true no matter how the data grows, and each one corresponds to a
way the pipeline could silently go wrong.
"""
import io
import os
import sys
import time
import warnings

warnings.filterwarnings('ignore')
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, 'api'))

KEY = os.path.join(os.path.dirname(HERE), 'dashboard', 'service_account1.json')
if 'GOOGLE_CREDS' not in os.environ and os.path.exists(KEY):
    with open(KEY, encoding='utf-8') as fh:
        os.environ['GOOGLE_CREDS'] = fh.read()

from _lib import pipeline                                   # noqa: E402
import pandas as pd                                         # noqa: E402

t0 = time.time()
src = pipeline.S.SheetsSource()
today = pd.Timestamp.now().normalize()
pipeline._reset_dq()
events, ident, members = pipeline.build_events(src)
cust = pipeline.build_customers(events, ident, members, today)
payload = pipeline.build_payload(events, cust, members, today)
payload['team'] = pipeline.build_team(events, cust, today)
elapsed = time.time() - t0

t = payload['totals']
print('ran in %.1fs\n' % elapsed)
for k in ('customers', 'events', 'registered', 'bookers', 'members', 'at_risk'):
    print('  %-11s %s' % (k, format(t[k], ',')))
print('  %-11s %s' % ('revenue', format(round(t['revenue']), ',')))
print()

fails = []


def check(name, ok, detail=''):
    print('  %-52s %s' % (name, 'ok' if ok else 'FAIL  ' + detail))
    if not ok:
        fails.append(name)


bookings = events[events.event_type == 'booking']
orders = bookings[bookings.order_no.notna()]

# 1. the de-duplication actually happened
dupes = orders['order_no'].duplicated().sum()
check('every order number appears exactly once', dupes == 0, 'found %d repeats' % dupes)

# 2. one status per order - the thing that moved revenue by 6,181
per_order_status = orders.groupby('order_no')['outcome'].nunique()
check('no order carries two different statuses',
      (per_order_status > 1).sum() == 0,
      '%d orders with conflicting status' % (per_order_status > 1).sum())

# 3. revenue is exactly the completed orders, nothing double counted
completed = orders[orders.outcome == 'Completed']
rev = float(completed['amount'].fillna(0).sum())
check('revenue == sum of completed unique orders',
      abs(rev - t['revenue']) < 1, 'pipeline %.0f vs recomputed %.0f' % (t['revenue'], rev))

# 4. funnel must be monotonically narrowing
vals = [f['value'] for f in payload['funnel']]
check('funnel never widens as it deepens',
      all(a >= b for a, b in zip(vals, vals[1:])), str(vals))

# 5. stage buckets partition the customer base exactly once
check('lifecycle stages sum to the customer count',
      sum(payload['stages'].values()) == t['customers'],
      '%d vs %d' % (sum(payload['stages'].values()), t['customers']))

# 6. every phone key is a well-formed Indian mobile
bad = [p for p in cust['phone'] if not (len(p) == 10 and p[0] in '6789' and p.isdigit())]
check('all customer keys are valid 10-digit mobiles', not bad, str(bad[:3]))

# 7. members are a subset of the customer base
check('every member exists in the customer table',
      set(members) <= set(cust['phone']))

# 8. bookers count matches the events
booked = set(bookings[bookings.outcome != 'Cancelled']['phone'])
check('bookers == distinct phones with a live booking',
      len(booked) == t['bookers'], '%d vs %d' % (len(booked), t['bookers']))

# 9. team attribution covers every dated call, and none twice
calls = events[events.event_type.isin(['nudge_call', 'nudge_membership'])]
dated_calls = calls[calls.date.notna()]
team_total = sum(p['total'] for p in payload['team']['people'])
check('team totals account for every dated call',
      team_total == len(dated_calls), '%d attributed vs %d dated' % (team_total, len(dated_calls)))

# 10. windows must nest
for p in payload['team']['people']:
    ok = p['yesterday'] <= p['d7'] <= p['d30'] <= p['total']
    check('%s: yday <= 7d <= 30d <= total' % p['owner'], ok,
          str([p['yesterday'], p['d7'], p['d30'], p['total']]))

# 11. no timeline may reference a customer that does not exist
tl_phones = {x['phone'] for x in payload['timelines']}
check('every timeline maps to a real customer', tl_phones <= set(cust['phone']))

# 12. attribution rates are percentages
rates = [r['rate'] for r in payload['wa_by_category']]
check('all attribution rates are within 0-100',
      all(0 <= r <= 100 for r in rates), str(rates))

print()
q = payload.get('quality') or pipeline.DQ
print('  cleaned: %s duplicate rows merged, %s status conflicts resolved'
      % (format(pipeline.DQ['dupe_booking_rows'], ','), len(pipeline.DQ['status_conflicts'])))
print()
if fails:
    print('FAILED %d check(s): %s' % (len(fails), '; '.join(fails)))
    sys.exit(1)
print('all invariants hold')
