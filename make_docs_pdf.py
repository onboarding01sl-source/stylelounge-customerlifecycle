"""Build the 'how every number is calculated' reference PDF.

Numbers are pulled from the live payload rather than typed in, so the worked
examples cannot drift out of step with the dashboard.
"""
import io
import json
import os
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (BaseDocTemplate, Frame, KeepTogether, PageBreak,
                                PageTemplate, Paragraph, Spacer, Table, TableStyle)

BASE = r"D:\Style Lounge\Customer Life Cycle Style lounge"
D = json.load(open(os.path.join(BASE, 'dashboard', 'dash.json'), encoding='utf-8'))
OUT = os.path.join(BASE, 'dashboard', 'How the dashboard is calculated.pdf')

T, Q = D['totals'], D['quality']
COV, MEM = D['coverage'], D['membership']
ATTR = {r['channel']: r for r in D['attribution']}
WA = {r['cat']: r for r in D['wa_by_category']}
TEAM = {p['owner']: p for p in D['team']['people']}
FUNNEL = {f['label']: f['value'] for f in D['funnel']}


def n(x):
    """Indian digit grouping, so the PDF matches the dashboard exactly
    (12,34,567 rather than 1,234,567)."""
    x = int(round(x))
    sign, x = ('-' if x < 0 else ''), abs(x)
    s = str(x)
    if len(s) <= 3:
        return sign + s
    head, tail = s[:-3], s[-3:]
    parts = []
    while len(head) > 2:
        parts.insert(0, head[-2:])
        head = head[:-2]
    if head:
        parts.insert(0, head)
    return sign + ','.join(parts) + ',' + tail


# ---------------------------------------------------------------- palette
INK = colors.HexColor('#181818')
INK2 = colors.HexColor('#5c564c')
MUTED = colors.HexColor('#8a8378')
WINE = colors.HexColor('#49252f')
BRASS = colors.HexColor('#857860')
RULE = colors.HexColor('#e0dbd1')
BAND = colors.HexColor('#f6f4ef')
CREAM = colors.HexColor('#faf9f7')

ss = getSampleStyleSheet()
S = {}
S['title'] = ParagraphStyle('t', parent=ss['Title'], fontName='Times-Roman',
                            fontSize=30, leading=34, textColor=INK,
                            alignment=TA_LEFT, spaceAfter=6)
S['sub'] = ParagraphStyle('s', fontName='Helvetica', fontSize=10.5, leading=15,
                          textColor=INK2, spaceAfter=3)
S['eyebrow'] = ParagraphStyle('e', fontName='Helvetica-Bold', fontSize=7.8,
                              textColor=BRASS, spaceAfter=8, leading=10)
S['h1'] = ParagraphStyle('h1', fontName='Times-Roman', fontSize=18, leading=22,
                         textColor=INK, spaceBefore=2, spaceAfter=7)
S['h2'] = ParagraphStyle('h2', fontName='Helvetica-Bold', fontSize=11.2,
                         leading=14, textColor=INK, spaceBefore=11, spaceAfter=4)
S['body'] = ParagraphStyle('b', fontName='Helvetica', fontSize=9.6, leading=14,
                           textColor=INK2, spaceAfter=4)
S['bullet'] = ParagraphStyle('bu', parent=S['body'], leftIndent=11,
                             bulletIndent=2, spaceAfter=3)
S['eg'] = ParagraphStyle('eg', fontName='Helvetica-Oblique', fontSize=9.3,
                         leading=13.5, textColor=WINE, leftIndent=9, spaceAfter=2)
S['note'] = ParagraphStyle('nt', fontName='Helvetica', fontSize=8.8, leading=12.5,
                           textColor=MUTED, spaceAfter=3)
S['cell'] = ParagraphStyle('c', fontName='Helvetica', fontSize=8.8, leading=12,
                           textColor=INK2)
S['cellb'] = ParagraphStyle('cb', fontName='Helvetica-Bold', fontSize=8.8,
                            leading=12, textColor=INK)

story = []


def h1(txt, kicker=None):
    story.append(Spacer(1, 4))
    if kicker:
        story.append(Paragraph(kicker.upper(), S['eyebrow']))
    story.append(Paragraph(txt, S['h1']))
    story.append(Table([['']], colWidths=[170 * mm], rowHeights=[0.7],
                       style=TableStyle([('BACKGROUND', (0, 0), (-1, -1), RULE)])))
    story.append(Spacer(1, 8))


def h2(txt):
    story.append(Paragraph(txt, S['h2']))


def p(txt):
    story.append(Paragraph(txt, S['body']))


def bullets(items):
    for it in items:
        story.append(Paragraph(it, S['bullet'], bulletText='\u2022'))


def steps(items):
    for i, it in enumerate(items, 1):
        story.append(Paragraph(it, S['bullet'], bulletText='%d.' % i))


def example(txt):
    story.append(Spacer(1, 2))
    story.append(Paragraph('Right now: ' + txt, S['eg']))


def box(rows, widths, head=True):
    data = []
    for r_i, row in enumerate(rows):
        style = S['cellb'] if (head and r_i == 0) else S['cell']
        data.append([Paragraph(str(c), style) for c in row])
    t = Table(data, colWidths=widths, hAlign='LEFT')
    cmds = [('VALIGN', (0, 0), (-1, -1), 'TOP'),
            ('TOPPADDING', (0, 0), (-1, -1), 5),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 5),
            ('LEFTPADDING', (0, 0), (-1, -1), 8),
            ('RIGHTPADDING', (0, 0), (-1, -1), 8),
            ('LINEBELOW', (0, 0), (-1, -2), 0.5, RULE)]
    if head:
        cmds += [('BACKGROUND', (0, 0), (-1, 0), BAND),
                 ('LINEBELOW', (0, 0), (-1, 0), 0.9, RULE)]
    t.setStyle(TableStyle(cmds))
    story.append(Spacer(1, 4))
    story.append(t)
    story.append(Spacer(1, 6))


# ================================================================ COVER
story.append(Spacer(1, 14))
story.append(Paragraph('STYLE LOUNGE \u00b7 CUSTOMER INTELLIGENCE', S['eyebrow']))
story.append(Paragraph('How every number on the dashboard is calculated', S['title']))
story.append(Spacer(1, 4))
story.append(Paragraph(
    'A plain-language reference. Each section names one figure you can see on the '
    'dashboard, explains in simple steps where it comes from, and shows the current '
    'value so you can check it yourself.', S['sub']))
story.append(Spacer(1, 10))
box([['Data as of', D.get('generated_at', D.get('generated', ''))],
     ['Customers covered', n(T['customers'])],
     ['Lifecycle events', n(T['events'])],
     ['Sources', 'WA Marketing \u00b7 CRM Style Lounge \u00b7 Rupam Membership \u00b7 Membership Tracker']],
    [38 * mm, 132 * mm], head=False)

h1('Start here: the two rules everything else depends on', 'foundation')

h2('Rule 1 \u2014 the phone number is the customer')
p('The four sheets have no shared customer ID. The CRM follow-up sheet has a '
  '<b>Customer ID</b> column but it is empty on all 13,809 rows, so it cannot join '
  'anything. Phone number is the only field present everywhere.')
bullets([
    'Every phone is trimmed to <b>10 digits</b>. <b>+91</b>, spaces, brackets and '
    'leading zeros are removed.',
    'The same person appears in the sheets as <b>9971372011</b>, <b>9971372011.0</b> '
    'and <b>\u201a9310311589</b>. All three become one customer.',
    'A number is only accepted if it is 10 digits and starts with 6, 7, 8 or 9. '
    'Anything else is ignored rather than guessed at.',
])
p('<b>Why this matters:</b> if two sheets spell the same number differently, that '
  'customer would otherwise be counted twice.')

h2('Rule 2 \u2014 one booking is counted once')
p('The WA Marketing and CRM sheets both contain the same bookings. Adding them '
  'together would roughly double every booking figure.')
steps([
    'Every booking row is grouped by its <b>Order No.</b>',
    'Rows sharing an order number are merged into one.',
    'If the merged rows disagree on status, the furthest-along one wins: '
    '<b>Completed</b> beats <b>In Progress</b>, which beats <b>Pending</b>, '
    'which beats <b>Cancelled</b>.',
])
p('An order once marked Completed did complete \u2014 a later "Pending" row is a '
  'stale export, not a cancellation.')
example('%s duplicate rows merged down to %s real orders, and %s orders had '
        'contradictory statuses that had to be resolved this way.'
        % (n(Q['duplicate_booking_rows']), n(Q['unique_orders']), n(Q['status_conflicts'])))

story.append(PageBreak())

# ================================================================ TOP TILES
h1('The six tiles at the top', 'where the business stands')

box([['Tile', 'How it is counted', 'Now'],
     ['Registered',
      'Distinct phone numbers found in either registrations tab.', n(T['registered'])],
     ['Have booked',
      'Distinct phones with at least one booking that is not Cancelled.', n(T['bookers'])],
     ['Members',
      'Rows in the Membership Tracker with a valid phone.', n(T['members'])],
     ['Revenue',
      'Sum of <b>Grand Total Amount</b> across unique orders marked Completed. '
      'Pending and cancelled orders are excluded.', '\u20b9' + n(T['revenue'])],
     ['At risk',
      'Has booked before, last booking more than <b>25 days</b> ago, and is not a member.',
      n(T['at_risk'])],
     ['Never contacted',
      'Never appears in any nudge log \u2014 no WhatsApp, no call, no membership call.',
      n(COV['never_touched'])]],
    [26 * mm, 116 * mm, 24 * mm])

p('<b>Revenue is completed bookings only.</b> It is money from services actually '
  'delivered, not the value of everything ever booked.')

h1('The funnel', 'where customers drop out')

p('Each step is a smaller group inside the one above it. The same person can only '
  'be in a step if they are also in every step above.')

box([['Step', 'Meaning', 'Now'],
     ['Registered', 'Signed up in the app', n(FUNNEL['Registered'])],
     ['Booked once', 'At least 1 live booking', n(FUNNEL['Booked once'])],
     ['Booked twice', 'At least 2 live bookings', n(FUNNEL['Booked twice'])],
     ['Booked 3+ times', 'At least 3 live bookings', n(FUNNEL['Booked 3+ times'])],
     ['Became a member', 'Appears in the Membership Tracker', n(FUNNEL['Became a member'])]],
    [34 * mm, 108 * mm, 24 * mm])

p('The percentage beside each bar is <b>share of the step above</b>, not share of the '
  'total. That is what shows you where people are actually lost.')
example('%s of %s registered customers ever book \u2014 %.1f%%. But of those who book '
        'once, %.0f%% come back for a second. The leak is the first booking, not retention.'
        % (n(FUNNEL['Booked once']), n(FUNNEL['Registered']),
           100 * FUNNEL['Booked once'] / FUNNEL['Registered'],
           100 * FUNNEL['Booked twice'] / FUNNEL['Booked once']))

story.append(PageBreak())
h1('Lifecycle stages', 'every customer sits in exactly one')

p('Checked in order, top to bottom. The first rule that matches wins, so nobody is '
  'counted twice and the six numbers always add up to the total.')

box([['Stage', 'Rule', 'Now'],
     ['Member', 'Is in the Membership Tracker', n(D['stages']['6. Member'])],
     ['Loyal', 'Has 3 or more bookings', n(D['stages']['5. Loyal (3+)'])],
     ['Repeat', 'Has exactly 2 bookings', n(D['stages']['4. Repeat (2)'])],
     ['First booking', 'Has exactly 1 booking', n(D['stages']['3. First booking'])],
     ['Nudged, no booking', 'Contacted at least once, never booked',
      n(D['stages']['2. Nudged, no booking'])],
     ['Registered only', 'Everyone else', n(D['stages']['1. Registered only'])]],
    [34 * mm, 108 * mm, 24 * mm])

# ================================================================ NUDGES
h1('Did the nudge work?', 'outreach effectiveness')

h2('The rule for crediting a nudge')
steps([
    'Take the date the nudge was sent.',
    'Look for that customer\u2019s next booking <b>after</b> that date.',
    'If it falls within <b>14 days</b>, the nudge is credited.',
])
p('A booking <i>before</i> the nudge is never credited. This is deliberately generous '
  'to the channel \u2014 the customer may have booked for their own reasons \u2014 so '
  'treat these as best-case numbers.')

h2('People, not calls')
p('The dashboard counts <b>people</b>. Someone called four times who then books once '
  'is <b>one</b> success, not four. Counting calls would flatter whoever dials most.')

box([['Channel', 'People contacted', 'Then booked', 'Then joined'],
     ['WhatsApp', n(ATTR['WhatsApp']['uniq']), n(ATTR['WhatsApp']['people_booked']),
      n(ATTR['WhatsApp']['people_joined'])],
     ['CRM call', n(ATTR['CRM call']['uniq']), n(ATTR['CRM call']['people_booked']),
      n(ATTR['CRM call']['people_joined'])],
     ['Membership call', n(ATTR['Membership call']['uniq']),
      n(ATTR['Membership call']['people_booked']),
      n(ATTR['Membership call']['people_joined'])]],
    [40 * mm, 40 * mm, 40 * mm, 40 * mm])

h2('WhatsApp broken down by list')
p('Each WhatsApp nudge belongs to a campaign list. Splitting by list is what makes '
  'the result meaningful, because the lists are not comparable.')

box([['List', 'Sent', 'Booked in 14 days', 'Rate'],
     ['3rd Time Users', n(WA['3rd Time Users']['n']), n(WA['3rd Time Users']['booked']),
      '%.1f%%' % WA['3rd Time Users']['rate']],
     ['1st Time Users', n(WA['1st Time Users']['n']), n(WA['1st Time Users']['booked']),
      '%.1f%%' % WA['1st Time Users']['rate']],
     ['2nd Time Users', n(WA['2nd Time Users']['n']), n(WA['2nd Time Users']['booked']),
      '%.1f%%' % WA['2nd Time Users']['rate']],
     ['Never Booked', n(WA['Never Booked']['n']), n(WA['Never Booked']['booked']),
      '%.1f%%' % WA['Never Booked']['rate']]],
    [40 * mm, 30 * mm, 50 * mm, 26 * mm])

story.append(KeepTogether([
    Paragraph('Read this one carefully', S['h2']),
    Paragraph(
        'Three of the four lists are built <b>from people who had already booked</b>. '
        'Only the <b>Never Booked</b> list is a clean test of whether a nudge can create '
        'a first booking \u2014 and it produced <b>%s bookings from %s messages</b>. '
        'Any headline comparing "nudged" against "never nudged" is mostly measuring who '
        'was chosen for the list, not what the message did.'
        % (n(WA['Never Booked']['booked']), n(WA['Never Booked']['n'])), S['body']),
]))

story.append(PageBreak())

# ================================================================ TEAM
h1('The caller scorecard', 'who is doing what')

h2('Where the names come from')
bullets([
    '<b>Shivani</b> and <b>Vishakha</b> come from the <b>Owner</b> column in the CRM '
    'follow-up sheet, which is filled on every row.',
    '<b>Rupam</b> is credited with every membership call. That sheet has no Owner '
    'column, so the whole channel is attributed to him.',
])
p('<b>Worth knowing:</b> if anyone else starts working the membership sheet, their '
  'calls will show under Rupam until an Owner column is added.')

h2('Each person is scored on their own job')
p('Shivani and Vishakha work the follow-up list to get people booking through the app. '
  'Rupam works the membership list. Showing bookings against Rupam, or memberships '
  'against the other two, would measure them on work they were never asked to do.')

box([['Person', 'Their job', 'Headline number', 'Now'],
     ['Shivani', 'CRM follow-ups', 'People who booked after her call',
      '%s of %s' % (n(TEAM['Shivani']['goal']), n(TEAM['Shivani']['people_nudged']))],
     ['Vishakha', 'CRM follow-ups', 'People who booked after her call',
      '%s of %s' % (n(TEAM['Vishakha']['goal']), n(TEAM['Vishakha']['people_nudged']))],
     ['Rupam', 'Membership', 'People who bought a membership',
      '%s of %s' % (n(TEAM['Rupam']['goal']), n(TEAM['Rupam']['people_nudged']))]],
    [24 * mm, 30 * mm, 72 * mm, 34 * mm])

h2('How each figure on a card is worked out')
box([['Figure', 'How it is calculated'],
     ['Yday / 7 days / 30 days / All time',
      'A count of dated call records. The CRM sheet adds a new dated column each day; '
      'each filled cell is one call on that date.'],
     ['People contacted',
      'Distinct phone numbers that person has called at least once.'],
     ['Booked after the call',
      'Of those people, how many booked within 14 days of being called.'],
     ['Bought a membership',
      'Of those people, how many have a membership <b>start date on or after</b> the '
      'first call. A membership that started earlier is never credited.'],
     ['Reached the customer',
      'Share of calls whose remark means the customer actually picked up '
      '(Connected, Call back, Service taken, and similar). "No answer" and '
      '"Not connected" do not count.'],
     ['Calls per working day',
      'Total calls divided by the number of days that person made any call. Days off '
      'are not counted against them.'],
     ['Already held a membership',
      'People on the membership list who were <b>already members</b> when called. '
      'Shown for Rupam only, because it is a wasted call on that list.']],
    [46 * mm, 124 * mm])

example('Rupam has contacted %s people and %s bought a membership. %s of the people '
        'he called already held one \u2014 those calls cannot convert.'
        % (n(TEAM['Rupam']['people_nudged']), n(TEAM['Rupam']['goal']),
           n(TEAM['Rupam']['nudged_existing_members'])))

story.append(PageBreak())

# ================================================================ MEMBERSHIP + QUALITY
h1('Membership', 'the programme in numbers')
box([['Figure', 'Meaning', 'Now'],
     ['Nudged for membership', 'People on the membership call list', n(MEM['nudged'])],
     ['Of those, now members', 'They appear in the tracker, whenever they joined',
      n(MEM['converted'])],
     ['Members never nudged', 'Joined without ever being on the list', n(MEM['never_nudged'])],
     ['Members not registered', 'Hold a membership but never signed up in the app',
      n(MEM['unregistered'])]],
    [46 * mm, 100 * mm, 22 * mm])
p('<b>Note the difference:</b> "now members" counts anyone on the list who holds a '
  'membership, even if they joined long before the call. The caller scorecard is '
  'stricter \u2014 it only credits memberships that <b>started after</b> the call.')
example('%s of the %s people nudged are members, but only %s joined after being '
        'nudged. And %s members have no app registration at all \u2014 they signed up '
        'in the salon, so app-driven lists never reach them.'
        % (n(MEM['converted']), n(MEM['nudged']), n(TEAM['Rupam']['goal']),
           n(MEM['unregistered'])))

h1('Data quality panel', 'what had to be cleaned')
box([['Figure', 'What it means', 'Now'],
     ['Duplicate rows merged', 'Booking rows that described an order already seen',
      n(Q['duplicate_booking_rows'])],
     ['Status conflicts', 'Orders logged with two different statuses',
      n(Q['status_conflicts'])],
     ['Unique orders', 'Real orders left after merging', n(Q['unique_orders'])],
     ['Events without a date', 'Records with no usable date; excluded from all '
      'time-based figures, still counted in totals', n(Q['undated_events'])]],
    [46 * mm, 100 * mm, 22 * mm])
p('These are issues in the source sheets, shown rather than hidden. The dashboard '
  'works around them; fixing them in the sheets would be better.')

h1('Coverage and timelines', 'the rest of the page')
box([['Figure', 'How it is calculated'],
     ['Channel coverage',
      'How many distinct people each channel has reached at least once. '
      '"More than one" means reached by two or more channels.'],
     ['Time to first booking',
      'Days between registering and the first booking. The median is used, not the '
      'average, because a few very long gaps would distort the average.'],
     ['Customer timelines',
      'Every event for one person, sorted by date. Includes anyone with a booking, '
      'a nudge or a membership. Search by name or phone.'],
     ['Last updated',
      'When the sheets were last read, shown in India time. Press <b>Refresh now</b> '
      'to re-read them immediately; otherwise it runs by itself each morning.']],
    [40 * mm, 130 * mm])

h1('Three things to keep in mind', 'honest limits')
bullets([
    '<b>A nudge is credited generously.</b> Any booking within 14 days after a nudge '
    'counts, even if the customer would have booked anyway. Real impact is likely lower.',
    '<b>Rupam\u2019s attribution is an assumption.</b> The membership sheet has no Owner '
    'column, so every membership call is credited to him.',
    '<b>The dashboard is only as good as the sheets.</b> A renamed tab or moved column '
    'will break a figure. A check runs before every refresh and refuses to publish if '
    'the sheets no longer look right.',
])

# ---------------------------------------------------------------- render
def furniture(canvas, doc):
    canvas.saveState()
    canvas.setFillColor(CREAM)
    canvas.rect(0, 0, A4[0], A4[1], stroke=0, fill=1)
    canvas.setFillColor(MUTED)
    canvas.setFont('Helvetica', 7.6)
    canvas.drawString(20 * mm, 12 * mm, 'Style Lounge \u00b7 Customer Lifecycle Dashboard')
    canvas.drawRightString(A4[0] - 20 * mm, 12 * mm, 'Page %d' % doc.page)
    canvas.setStrokeColor(RULE)
    canvas.setLineWidth(0.5)
    canvas.line(20 * mm, 16 * mm, A4[0] - 20 * mm, 16 * mm)
    canvas.restoreState()


doc = BaseDocTemplate(OUT, pagesize=A4,
                      leftMargin=20 * mm, rightMargin=20 * mm,
                      topMargin=18 * mm, bottomMargin=22 * mm,
                      title='How the Style Lounge dashboard is calculated',
                      author='Style Lounge')
frame = Frame(doc.leftMargin, doc.bottomMargin,
              doc.width, doc.height, id='f', showBoundary=0)
doc.addPageTemplates([PageTemplate(id='main', frames=[frame], onPage=furniture)])
doc.build(story)
print('wrote %s (%.0f KB)' % (OUT, os.path.getsize(OUT) / 1024))
