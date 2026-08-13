"""Style Lounge customer-lifecycle pipeline.

Reads the four Google Sheets and returns the dashboard payload as a dict.
Everything happens in memory - a serverless function has no writable
filesystem worth using, and no customer data should be persisted anyway.

The shape of the work:
  1. every source row becomes one row in a long `events` table
  2. `customers` is derived from that, one row per phone number
  3. `payload` is the aggregate the dashboard actually renders

Phone number is the primary key. `Customer ID` is empty for all 13,809 rows of
the CRM follow-up sheet, so it cannot join anything.
"""
import datetime as dt
import re

import numpy as np
import pandas as pd

from . import sources as S

_EPOCH = dt.datetime(1899, 12, 30)


# ---------------------------------------------------------------- normalisers
def phone(v):
    """Any representation -> canonical 10-digit Indian mobile, else None.

    The same customer appears as 9971372011, 9971372011.0, '9310311589 and
    919971372011 across the four sheets.
    """
    if v is None or (isinstance(v, float) and np.isnan(v)):
        return None
    s = str(v).strip().strip("'")
    if s.endswith('.0'):
        s = s[:-2]
    if 'e' in s.lower():
        try:
            s = '%.0f' % float(s)
        except ValueError:
            pass
    d = re.sub(r'\D', '', s)
    if len(d) > 10 and d.startswith('91'):
        d = d[2:]
    d = d.lstrip('0')
    if len(d) > 10:
        d = d[-10:]
    return d if len(d) == 10 and d[0] in '6789' else None


def to_dt(v):
    """Raw cell -> Timestamp. Handles Excel serials and text dates."""
    if v is None or (isinstance(v, float) and np.isnan(v)):
        return pd.NaT
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        f = float(v)
        if not (1 <= f < 80000):          # implausible as a date serial
            return pd.NaT
        return pd.Timestamp(_EPOCH + dt.timedelta(days=f))
    s = str(v).strip()
    if not s or s == '-':
        return pd.NaT
    for dayfirst in (True, False):
        try:
            return pd.to_datetime(s, dayfirst=dayfirst)
        except Exception:                                  # noqa: BLE001
            continue
    return pd.NaT


def clean(v):
    if v is None or (isinstance(v, float) and np.isnan(v)):
        return None
    s = str(v).strip().strip("'")
    return s if s and s != '-' else None


# ---------------------------------------------------------------- events
def build_events(src):
    """Collapse all four workbooks into one long event log."""
    EV = []
    IDENT = {}
    members = {}

    def emit(ph, when, etype, channel, detail=None, outcome=None, source=None, **extra):
        if ph:
            row = dict(phone=ph, date=when, event_type=etype, channel=channel,
                       detail=detail, outcome=outcome, source=source)
            row.update(extra)
            EV.append(row)

    def ident(ph, name=None, city=None):
        if not ph:
            return
        r = IDENT.setdefault(ph, {'name': None, 'city': None})
        if not r['name'] and name:
            r['name'] = name
        if not r['city'] and city:
            r['city'] = city

    # 1. registrations -------------------------------------------------
    for book in (S.WA, S.CRM):
        df = src.frame(book, 'registrations')
        for d in df.to_dict('records'):
            ph = phone(d.get('Contact Number'))
            if not ph:
                continue
            ident(ph, clean(d.get('Name')), clean(d.get('City')))
            emit(ph, to_dt(d.get('Onboarding Date')), 'registration', 'app',
                 source='%s:registrations' % book)

    # 2. bookings ------------------------------------------------------
    for book in (S.WA, S.CRM):
        df = src.frame(book, 'bookings')
        for d in df.to_dict('records'):
            ph = phone(d.get('Contact Number'))
            status = clean(d.get('Status'))
            if not ph or status == 'Status':      # a repeated header row sits mid-sheet
                continue
            ident(ph, clean(d.get('Customer Name')), clean(d.get('City')))
            when = to_dt(d.get('Booking Date'))
            if pd.isna(when):
                when = to_dt(d.get('Order Date'))
            try:
                amt = float(d.get('Grand Total Amount'))
            except (TypeError, ValueError):
                amt = None
            emit(ph, when, 'booking', 'salon',
                 detail=clean(d.get('Salon Name')), outcome=status,
                 source='%s:bookings' % book,
                 amount=amt, order_no=clean(d.get('Order No.')))

    # 3. whatsapp nudges ----------------------------------------------
    for d in src.frame(S.WA, 'Nudges Log').to_dict('records'):
        ph = phone(d.get('Phone Number'))
        ident(ph, clean(d.get('Name')))
        emit(ph, to_dt(d.get('Date')), 'nudge_whatsapp', 'whatsapp',
             detail=clean(d.get('Category')), source='wa:Nudges Log')

    # 4. CRM calls - the sheet grows a dated column pair every day -----
    df = src.frame(S.CRM, 'A-Combined Follow-ups')
    dated = [c for c in df.columns if re.match(r'^Remarks \d{2}-\d{2}-\d{4}$', str(c))]
    for d in df.to_dict('records'):
        ph = phone(d.get('Contact Number'))
        if not ph:
            continue
        ident(ph, clean(d.get('Name')), clean(d.get('City')))
        owner, lq = clean(d.get('Owner')), clean(d.get('Lead Quality'))
        fired = False
        for c in dated:
            rem = clean(d.get(c))
            det = clean(d.get('Detailed ' + c))
            if not rem and not det:
                continue
            when = pd.to_datetime(c.split(' ', 1)[1], format='%d-%m-%Y', errors='coerce')
            emit(ph, when, 'nudge_call', 'call', detail=det or rem, outcome=rem,
                 source='crm:A-Combined Follow-ups', owner=owner, lead_quality=lq)
            fired = True
        base = clean(d.get('Remarks'))
        if base and not fired:
            emit(ph, to_dt(d.get('Last Contacted')), 'nudge_call', 'call',
                 detail=clean(d.get('Detailed Remarks')), outcome=base,
                 source='crm:A-Combined Follow-ups', owner=owner, lead_quality=lq)

    # 5. membership nudges --------------------------------------------
    for d in src.frame(S.RUP, 'Users to be Nudged').to_dict('records'):
        ph = phone(d.get('Mobile'))
        if not ph:
            continue
        ident(ph, clean(d.get('Customer Name')))
        emit(ph, to_dt(d.get('Last Contacted')), 'nudge_membership', 'membership_call',
             detail=clean(d.get('Remarks')),
             outcome=clean(d.get(' Status')) or clean(d.get('Status')),
             source='rupam:Users to be Nudged',
             priority=clean(d.get('Priority')), value_tier=clean(d.get('Value Tier')))

    for d in src.frame(S.RUP, 'Sheet1').to_dict('records'):
        ph = phone(d.get('Contact Number'))
        if not ph:
            continue
        ident(ph, clean(d.get('Customer Name')))
        if clean(d.get('Call Status')) or clean(d.get('Remarks')):
            emit(ph, to_dt(d.get('Date')), 'nudge_membership', 'membership_call',
                 detail=clean(d.get('Remarks')), outcome=clean(d.get('Call Status')),
                 source='rupam:Sheet1')

    # 6. memberships ---------------------------------------------------
    for d in src.frame(S.MEM, 'Sheet1', header_row=1).to_dict('records'):
        ph = phone(d.get('Phone'))
        if not ph:
            continue
        ident(ph, clean(d.get('Name')))
        start = to_dt(d.get('Membership Starting Date'))
        try:
            spend = float(d.get('Total Spend (₹)') or 0)
        except (TypeError, ValueError):
            spend = 0.0
        members[ph] = dict(start=start, end=to_dt(d.get('Membership Ending Date')),
                           mid=clean(d.get('membership_id')), spend=spend)
        emit(ph, start, 'membership_start', 'membership',
             detail=clean(d.get('membership_id')), source='tracker:Sheet1')

    events = pd.DataFrame(EV)
    events['date'] = pd.to_datetime(events['date'], errors='coerce')
    for c in ('amount', 'order_no', 'owner', 'lead_quality', 'priority', 'value_tier'):
        if c not in events.columns:
            events[c] = None

    # the WA and CRM workbooks overlap; collapse the duplicates
    events['_src'] = events['source'].str.split(':').str[-1]
    events = events.drop_duplicates(
        subset=['phone', 'date', 'event_type', 'order_no', '_src', 'outcome'])
    events = events.drop(columns=['_src'])
    return events, IDENT, members


# ---------------------------------------------------------------- customers
def build_customers(events, IDENT, members, today):
    cust = pd.DataFrame({'phone': sorted(set(events['phone']))})
    cust['name'] = cust['phone'].map(lambda p: IDENT.get(p, {}).get('name'))
    cust['city'] = cust['phone'].map(lambda p: IDENT.get(p, {}).get('city'))

    is_book = events['event_type'] == 'booking'
    valid = is_book & (~events['outcome'].isin(['Cancelled']))
    done = is_book & (events['outcome'] == 'Completed')

    def m(mask, col, fn):
        return cust['phone'].map(events[mask].groupby('phone')[col].agg(fn))

    cust['reg_date'] = m(events['event_type'] == 'registration', 'date', 'min')
    cust['first_booking'] = m(valid, 'date', 'min')
    cust['last_booking'] = m(valid, 'date', 'max')
    cust['bookings'] = cust['phone'].map(
        events[valid].groupby('phone')['order_no'].nunique()).fillna(0).astype(int)
    cust['revenue'] = cust['phone'].map(
        events[done].drop_duplicates('order_no').groupby('phone')['amount'].sum()
    ).fillna(0.0)

    for et, nm in [('nudge_whatsapp', 'wa_nudges'), ('nudge_call', 'call_nudges'),
                   ('nudge_membership', 'mem_nudges')]:
        cust[nm] = cust['phone'].map(
            events[events['event_type'] == et].groupby('phone').size()
        ).fillna(0).astype(int)

    cust['total_nudges'] = cust[['wa_nudges', 'call_nudges', 'mem_nudges']].sum(axis=1)
    cust['is_member'] = cust['phone'].isin(members)
    cust['is_registered'] = cust['phone'].isin(
        set(events[events['event_type'] == 'registration']['phone']))
    cust['days_since_booking'] = (today - cust['last_booking']).dt.days
    cust['days_to_first_booking'] = (cust['first_booking'] - cust['reg_date']).dt.days

    def stage(r):
        if r['is_member']:
            return '6. Member'
        if r['bookings'] >= 3:
            return '5. Loyal (3+)'
        if r['bookings'] == 2:
            return '4. Repeat (2)'
        if r['bookings'] == 1:
            return '3. First booking'
        if r['total_nudges'] > 0:
            return '2. Nudged, no booking'
        return '1. Registered only'

    cust['stage'] = cust.apply(stage, axis=1)
    cust['at_risk'] = ((cust['bookings'] > 0) & (cust['days_since_booking'] > 25)
                       & (~cust['is_member']))
    return cust


# ---------------------------------------------------------------- payload
def build_payload(events, cust, members, today):
    bk = events[(events.event_type == 'booking')
                & (events.outcome != 'Cancelled') & events.date.notna()]
    bmap = bk.groupby('phone')['date'].apply(lambda s: np.sort(s.values))

    def booked_within(nudges, days):
        hit = 0
        for ph, d in zip(nudges['phone'], nudges['date']):
            arr = bmap.get(ph)
            if arr is None:
                continue
            delta = (arr - np.datetime64(d)) / np.timedelta64(1, 'D')
            after = delta[delta > 0]
            if len(after) and after.min() <= days:
                hit += 1
        return hit

    attribution = []
    for et, label in [('nudge_whatsapp', 'WhatsApp'), ('nudge_call', 'CRM call'),
                      ('nudge_membership', 'Membership call')]:
        nd = events[(events.event_type == et) & events.date.notna()]
        n = len(nd)
        row = dict(channel=label, nudges=n, uniq=int(nd['phone'].nunique()))
        for w in (7, 14, 30):
            h = booked_within(nd, w)
            row['w%d' % w] = h
            row['r%d' % w] = 100.0 * h / n if n else 0.0
        attribution.append(row)

    # WhatsApp by campaign list - the only unconfounded cut, because the
    # "Never Booked" list is the one group with no prior booking history.
    had_booked = set(cust[cust.bookings > 0].phone)
    wa = events[(events.event_type == 'nudge_whatsapp') & events.date.notna()]
    wa_cat = []
    for catname, g in wa.groupby('detail'):
        h = booked_within(g, 14)
        wa_cat.append(dict(cat=catname, n=len(g), booked=h,
                           rate=100.0 * h / len(g),
                           warm=100.0 * float(g.phone.isin(had_booked).mean())))
    wa_cat.sort(key=lambda r: -r['rate'])

    memn = set(events[events.event_type == 'nudge_membership']['phone'])
    mem = set(cust[cust.is_member].phone)
    reg = int(cust.is_registered.sum())
    funnel = [('Registered', reg),
              ('Booked once', int((cust.bookings >= 1).sum())),
              ('Booked twice', int((cust.bookings >= 2).sum())),
              ('Booked 3+ times', int((cust.bookings >= 3).sum())),
              ('Became a member', int(cust.is_member.sum()))]

    t = cust[cust.days_to_first_booking.notna()
             & (cust.days_to_first_booking >= 0)]['days_to_first_booking']

    payload = dict(
        generated=today.strftime('%Y-%m-%d'),
        totals=dict(customers=int(len(cust)), events=int(len(events)),
                    registered=reg,
                    bookers=int((cust.bookings >= 1).sum()),
                    members=int(cust.is_member.sum()),
                    revenue=float(cust.revenue.sum()),
                    at_risk=int(cust.at_risk.sum())),
        funnel=[dict(label=k, value=v) for k, v in funnel],
        stages=cust['stage'].value_counts().sort_index().to_dict(),
        attribution=attribution,
        wa_by_category=wa_cat,
        coverage=dict(
            total=int(len(cust)),
            never_touched=int(((cust.wa_nudges == 0) & (cust.call_nudges == 0)
                               & (cust.mem_nudges == 0)).sum()),
            wa=int((cust.wa_nudges > 0).sum()),
            call=int((cust.call_nudges > 0).sum()),
            mem=int((cust.mem_nudges > 0).sum()),
            multi=int((((cust.wa_nudges > 0).astype(int)
                        + (cust.call_nudges > 0).astype(int)
                        + (cust.mem_nudges > 0).astype(int)) > 1).sum())),
        membership=dict(nudged=len(memn), converted=len(memn & mem),
                        never_nudged=len(mem - memn),
                        unregistered=int((cust.is_member & ~cust.is_registered).sum())),
        call_outcomes=events[events.event_type == 'nudge_call']['outcome']
                      .value_counts().head(8).to_dict(),
        mem_outcomes=events[events.event_type == 'nudge_membership']['outcome']
                     .value_counts().head(8).to_dict(),
        ttfb=dict(n=int(len(t)), median=float(t.median()) if len(t) else 0.0,
                  sameday=float(100 * (t == 0).mean()) if len(t) else 0.0,
                  w7=float(100 * (t <= 7).mean()) if len(t) else 0.0,
                  w30=float(100 * (t <= 30).mean()) if len(t) else 0.0),
    )

    # per-customer timelines, for everyone with any real activity
    active = (cust[cust.is_member | (cust.bookings > 0) | (cust.total_nudges > 0)]
              .sort_values(['is_member', 'revenue', 'bookings'], ascending=False))
    by_phone = {p: g.sort_values('date')
                for p, g in events[events.phone.isin(set(active.phone))].groupby('phone')}
    timelines = []
    for r in active.itertuples(index=False):
        evs = by_phone.get(r.phone)
        if evs is None:
            continue
        timelines.append(dict(
            phone=r.phone, name=r.name if pd.notna(r.name) else 'Unknown',
            city=r.city if pd.notna(r.city) else '',
            stage=r.stage, bookings=int(r.bookings), revenue=float(r.revenue),
            member=bool(r.is_member), nudges=int(r.total_nudges),
            events=[dict(d=(e.date.strftime('%Y-%m-%d') if pd.notna(e.date) else None),
                         t=e.event_type, c=e.channel,
                         o=(e.outcome if pd.notna(e.outcome) else None),
                         det=(str(e.detail)[:70] if pd.notna(e.detail) else None))
                    for e in evs.itertuples()]))
    payload['timelines'] = timelines
    return payload


def run():
    """Full pipeline. Returns the dashboard payload dict."""
    src = S.SheetsSource()
    today = pd.Timestamp.now().normalize()
    events, ident, members = build_events(src)
    cust = build_customers(events, ident, members, today)
    return build_payload(events, cust, members, today)
