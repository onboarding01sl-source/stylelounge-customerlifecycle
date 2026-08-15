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

# When one order number carries several statuses, the furthest-along wins.
# An order that was ever marked Completed did complete; a later "Pending" row
# is a stale export, not a reversal. Cancelled is deliberately lowest: a
# cancelled row alongside a completed one means the completion is the later
# truth. This is the rule that decides revenue, so it is stated once here.
STATUS_RANK = {'Completed': 4, 'In Progress': 3, 'Pending': 2, 'Cancelled': 1}

# Counters filled during the run and surfaced in the dashboard's data-quality
# panel, so the cleaning is visible rather than silent.
DQ = {}


def _reset_dq():
    DQ.clear()
    DQ.update(dupe_booking_rows=0, status_conflicts=set(),
              bad_phone_rows=0, undated_events=0)


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
    # The two workbooks overlap heavily - 4,549 completed rows describe only
    # 2,458 real orders - and 14 order numbers carry contradictory statuses
    # (the same order logged Completed *and* Pending). Collapse to one record
    # per order number first, resolving status by precedence, so an order can
    # never be counted twice or counted under two different outcomes.
    seen_orders = {}
    for book in (S.WA, S.CRM):
        df = src.frame(book, 'bookings')
        for d in df.to_dict('records'):
            ph = phone(d.get('Contact Number'))
            status = clean(d.get('Status'))
            if not ph or status == 'Status':      # a repeated header row sits mid-sheet
                continue
            order = clean(d.get('Order No.'))
            when = to_dt(d.get('Booking Date'))
            if pd.isna(when):
                when = to_dt(d.get('Order Date'))
            try:
                amt = float(d.get('Grand Total Amount'))
            except (TypeError, ValueError):
                amt = None
            rec = dict(phone=ph, when=when, status=status, amount=amt,
                       salon=clean(d.get('Salon Name')), order=order,
                       name=clean(d.get('Customer Name')), city=clean(d.get('City')))
            # rows with no order number cannot be de-duplicated; key them by
            # the customer, timestamp and amount instead
            key = order or 'noorder:%s|%s|%s' % (ph, when, amt)
            prev = seen_orders.get(key)
            if prev is None:
                seen_orders[key] = rec
                continue
            DQ['dupe_booking_rows'] += 1
            if STATUS_RANK.get(status, 0) > STATUS_RANK.get(prev['status'], 0):
                if status != prev['status']:
                    DQ['status_conflicts'].add(key)
                seen_orders[key] = rec
            elif status != prev['status']:
                DQ['status_conflicts'].add(key)

    for rec in seen_orders.values():
        ident(rec['phone'], rec['name'], rec['city'])
        emit(rec['phone'], rec['when'], 'booking', 'salon',
             detail=rec['salon'], outcome=rec['status'], source='bookings',
             amount=rec['amount'], order_no=rec['order'])

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

    joined_at = (events[(events.event_type == 'membership_start') & events.date.notna()]
                 .groupby('phone')['date'].min())

    def people_outcomes(nudges):
        """Unique people who booked, and who joined, after being contacted."""
        booked, joined_p = set(), set()
        first = nudges.groupby('phone')['date'].min()
        for ph_, dd in zip(nudges['phone'], nudges['date']):
            arr = bmap.get(ph_)
            if arr is None:
                continue
            delta = (arr - np.datetime64(dd)) / np.timedelta64(1, 'D')
            after = delta[delta > 0]
            if len(after) and after.min() <= 14:
                booked.add(ph_)
        for ph_, f in first.items():
            st = joined_at.get(ph_)
            if st is not None and pd.notna(st) and st >= f:
                joined_p.add(ph_)
        return len(booked), len(joined_p)

    attribution = []
    for et, label in [('nudge_whatsapp', 'WhatsApp'), ('nudge_call', 'CRM call'),
                      ('nudge_membership', 'Membership call')]:
        nd = events[(events.event_type == et) & events.date.notna()]
        n = len(nd)
        uniq = int(nd['phone'].nunique())
        pb, pj = people_outcomes(nd)
        row = dict(channel=label, nudges=n, uniq=uniq,
                   people_booked=pb, people_joined=pj,
                   person_rate=100.0 * pb / uniq if uniq else 0.0)
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


# ---------------------------------------------------------------- team / KRA
# Who owns which outreach. Vishakha and Shivani come from the CRM Owner column,
# which is populated on every follow-up row. Rupam's membership calls carry no
# owner column at all - that sheet is his, so the whole channel attributes to
# him. If someone else starts working that sheet this attribution needs an
# Owner column rather than a guess.
MEMBERSHIP_OWNER = 'Rupam'

# Callers have different jobs, so they are scored on different outcomes.
# Shivani and Vishakha work the CRM follow-up list to get people booking
# through the app; Rupam works the membership list. Showing bookings against
# Rupam, or membership against the other two, measures them on work they were
# never asked to do.
ROLES = {
    'Rupam':    dict(role='membership', team='Membership',
                     goal_key='people_joined', goal_label='bought a membership'),
    'Shivani':  dict(role='bookings', team='CRM follow-ups',
                     goal_key='people_booked', goal_label='booked after the call'),
    'Vishakha': dict(role='bookings', team='CRM follow-ups',
                     goal_key='people_booked', goal_label='booked after the call'),
}
DEFAULT_ROLE = dict(role='bookings', team='Follow-ups',
                    goal_key='people_booked', goal_label='booked after the call')

# outcomes that mean the customer actually picked up
CONNECTED = {'connected', 'call back', 'callback', 'interested', 'not interested',
             'service taken', 'will book later', 'booked', 'follow up',
             'just checking out the app', 'will take service later',
             'no follow-up', 'not required', 'already membership',
             'already taken membership'}
NO_ANSWER = {'no answer', 'not answered', 'not connected', 'not answered ',
             'disconected the call'}


def build_team(events, cust, today):
    """Per-caller activity: volume, recency windows, outcomes and a daily series.

    Every figure is derived from the dated call events, so it is reproducible
    from the sheets and needs no stored state.
    """
    calls = events[events.event_type.isin(['nudge_call', 'nudge_membership'])].copy()
    calls['owner'] = calls['owner'].where(calls['owner'].notna(), None)
    calls.loc[calls.event_type == 'nudge_membership', 'owner'] = MEMBERSHIP_OWNER
    calls = calls[calls['owner'].notna() & calls['date'].notna()]

    yesterday = today - pd.Timedelta(days=1)
    booked_after = _booking_lookup(events)
    # when each customer's membership started, for crediting membership sales
    joined = (events[(events.event_type == 'membership_start') & events.date.notna()]
              .groupby('phone')['date'].min())

    people = []
    for owner, g in calls.groupby('owner'):
        d = g['date'].dt.normalize()
        outcomes = g['outcome'].fillna('(blank)').str.strip()
        low = outcomes.str.lower()
        connected = int(low.isin(CONNECTED).sum())
        noans = int(low.isin(NO_ANSWER).sum())

        # Count PEOPLE, not calls. Someone rung four times who then books once
        # is one win, not four - counting calls would flatter whoever dials most.
        first_touch = g.groupby('phone')['date'].min()
        won_people, won_calls, joined_people = set(), 0, set()
        for ph_, dd in zip(g['phone'], g['date']):
            arr = booked_after.get(ph_)
            if arr is None:
                continue
            delta = (arr - np.datetime64(dd)) / np.timedelta64(1, 'D')
            after = delta[delta > 0]
            if len(after) and after.min() <= 14:
                won_calls += 1
                won_people.add(ph_)
        # A membership is credited only when it starts after the first contact.
        # Contacting someone who already holds a membership is a wasted call,
        # counted separately rather than quietly credited as a conversion.
        already_member = set()
        for ph_, first in first_touch.items():
            start = joined.get(ph_)
            if start is None or pd.isna(start):
                continue
            if start >= first:
                joined_people.add(ph_)
            else:
                already_member.add(ph_)
        wins = won_calls
        nudged_people = int(g['phone'].nunique())
        daily = (d.value_counts().sort_index()
                 .tail(45).rename_axis('d').reset_index(name='n'))
        people.append(dict(
            owner=owner,
            total=int(len(g)),
            yesterday=int((d == yesterday).sum()),
            today=int((d == today).sum()),
            d7=int((d > today - pd.Timedelta(days=7)).sum()),
            d30=int((d > today - pd.Timedelta(days=30)).sum()),
            customers=int(g['phone'].nunique()),
            connected=connected,
            no_answer=noans,
            connect_rate=100.0 * connected / len(g) if len(g) else 0.0,
            # headline pair: people contacted -> people who then acted
            people_nudged=nudged_people,
            people_booked=len(won_people),
            people_joined=len(joined_people),
            nudged_existing_members=len(already_member),
            booked_after=wins,                       # calls followed by a booking
            win_rate=100.0 * wins / len(g) if len(g) else 0.0,
            person_rate=100.0 * len(won_people) / nudged_people if nudged_people else 0.0,
            join_rate=100.0 * len(joined_people) / nudged_people if nudged_people else 0.0,
            active_days=int(d.nunique()),
            avg_per_active_day=round(len(g) / max(d.nunique(), 1), 1),
            last_active=(d.max().strftime('%Y-%m-%d') if len(d) else None),
            top_outcomes=outcomes.value_counts().head(6).to_dict(),
            daily=[dict(d=r.d.strftime('%Y-%m-%d'), n=int(r.n))
                   for r in daily.itertuples()],
        ))
    for p in people:
        meta = ROLES.get(p['owner'], DEFAULT_ROLE)
        p.update(role=meta['role'], team=meta['team'],
                 goal_label=meta['goal_label'],
                 goal=p[meta['goal_key']],
                 goal_rate=(100.0 * p[meta['goal_key']] / p['people_nudged']
                            if p['people_nudged'] else 0.0))
        # a wasted-call signal only means something on the membership list
        if meta['role'] != 'membership':
            p['nudged_existing_members'] = 0
    people.sort(key=lambda p: -p['d30'])

    # one combined daily series so the team's overall rhythm is visible
    allday = (calls['date'].dt.normalize().value_counts().sort_index().tail(45))
    return dict(
        people=people,
        team_daily=[dict(d=k.strftime('%Y-%m-%d'), n=int(v)) for k, v in allday.items()],
        totals=dict(
            calls=int(len(calls)),
            yesterday=int((calls['date'].dt.normalize() == yesterday).sum()),
            d7=int((calls['date'].dt.normalize() > today - pd.Timedelta(days=7)).sum()),
            d30=int((calls['date'].dt.normalize() > today - pd.Timedelta(days=30)).sum()),
        ),
        note=('Vishakha and Shivani are attributed from the CRM Owner column. '
              'Membership calls carry no owner, so the whole channel is '
              'attributed to %s.' % MEMBERSHIP_OWNER),
    )


def _booking_lookup(events):
    bk = events[(events.event_type == 'booking')
                & (events.outcome != 'Cancelled') & events.date.notna()]
    return bk.groupby('phone')['date'].apply(lambda s: np.sort(s.values))


def run():
    """Full pipeline. Returns the dashboard payload dict."""
    _reset_dq()
    src = S.SheetsSource()
    today = pd.Timestamp.now().normalize()
    events, ident, members = build_events(src)
    cust = build_customers(events, ident, members, today)
    payload = build_payload(events, cust, members, today)
    payload['team'] = build_team(events, cust, today)
    payload['quality'] = dict(
        duplicate_booking_rows=DQ['dupe_booking_rows'],
        status_conflicts=len(DQ['status_conflicts']),
        undated_events=int(events['date'].isna().sum()),
        unique_orders=int(events[events.event_type == 'booking']['order_no'].nunique()),
        booking_events=int((events.event_type == 'booking').sum()),
    )
    # Vercel runs in UTC; the team reads this in India, so show both rather
    # than a bare time that is five and a half hours out.
    now_utc = pd.Timestamp.now(tz='UTC')
    ist = now_utc.tz_convert('Asia/Kolkata')
    payload['generated_at'] = ist.strftime('%d %b %Y, %I:%M %p')
    payload['generated_iso'] = now_utc.isoformat()
    return payload
