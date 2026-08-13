"""Google Sheets source for the lifecycle pipeline.

Credentials come from the GOOGLE_CREDS environment variable (the full
service-account JSON as a single-line string), never from a file on disk -
nothing secret is ever committed or bundled into the deployment.

valueRenderOption=UNFORMATTED_VALUE is the important flag: it returns each
cell's underlying value and ignores the display format. Several thousand phone
numbers in the CRM registrations tab are formatted as dates; a formatted read
returns them as blanks and silently loses ~700 customers.
"""
import json
import os
import time

import pandas as pd

WA, RUP, MEM, CRM = 'wa', 'rupam', 'tracker', 'crm'

SPREADSHEET_IDS = {
    WA:  '1Oe7GehssdTJXM83oKjJrpfujAa2DYWMpWLvbAHh-bt8',
    RUP: '14iWWITONlauBdg0Luyazl-9-mIchTI55wvj1B80SOlY',
    MEM: '11v2gsG9pdbjd-g5qrlulppCmEklfchCeRuMCqhhEXzk',
    CRM: '1TKNRIDcHUKrNXaMzzs0E4aZAAIBVYKfVDXMC0JDwouo',
}

# (book, tab, header_row) - the header for the membership tracker is on row 2.
TABS = [
    (WA,  'registrations',         0),
    (WA,  'bookings',              0),
    (WA,  'Nudges Log',            0),
    (CRM, 'registrations',         0),
    (CRM, 'bookings',              0),
    (CRM, 'A-Combined Follow-ups', 0),
    (RUP, 'Users to be Nudged',    0),
    (RUP, 'Sheet1',                0),
    (MEM, 'Sheet1',                1),
]


def _decode_creds(raw):
    """Accept the service-account key as base64 or as raw JSON.

    Base64 is the recommended form: the key's private_key field contains
    newlines, and pasting a multi-line value into an environment-variable
    field mangles it. Raw JSON is still accepted so a local run can point
    GOOGLE_CREDS straight at the file contents.
    """
    raw = raw.strip()
    if raw.startswith('{'):
        return json.loads(raw)
    import base64
    try:
        decoded = base64.b64decode(raw, validate=True).decode('utf-8')
    except Exception as exc:                               # noqa: BLE001
        raise RuntimeError(
            'GOOGLE_CREDS is neither JSON nor valid base64 (%s). Re-copy it '
            'from GOOGLE_CREDS_base64.txt as a single line.' % type(exc).__name__)
    return json.loads(decoded)


def _dedupe(header):
    """Sheets happily ships duplicate headers ('Name', 'Name')."""
    cols, seen = [], {}
    for i, h in enumerate(header):
        c = str(h).strip() if h not in (None, '') else 'col%d' % i
        if c in seen:
            seen[c] += 1
            c = '%s.%d' % (c, seen[c])
        else:
            seen[c] = 0
        cols.append(c)
    return cols


class SheetsSource:
    name = 'google sheets (live)'

    def __init__(self):
        from google.oauth2.service_account import Credentials
        from googleapiclient.discovery import build

        raw = os.environ.get('GOOGLE_CREDS')
        if not raw:
            raise RuntimeError(
                'GOOGLE_CREDS is not set. Put the service-account key in the '
                'Vercel environment variable of that name, base64-encoded.')
        info = _decode_creds(raw)
        creds = Credentials.from_service_account_info(
            info, scopes=['https://www.googleapis.com/auth/spreadsheets.readonly'])
        self.api = build('sheets', 'v4', credentials=creds,
                         cache_discovery=False).spreadsheets()
        self._cache = {}

    # Do not fetch tabs concurrently with a shared client. googleapiclient's
    # httplib2 transport is not thread-safe: sharing it corrupts the SSL stream
    # and the retry storm makes the run ~4x slower than plain serial.

    @staticmethod
    def _retry(fn, tries=4):
        for i in range(tries):
            try:
                return fn()
            except Exception as exc:                       # noqa: BLE001
                transient = any(t in str(exc) for t in
                                ('429', '500', '503', 'Rate Limit', 'timed out'))
                if not transient or i == tries - 1:
                    raise
                time.sleep(2 ** i)

    def frame(self, book, sheet, header_row=0):
        key = (book, sheet, header_row)
        if key in self._cache:
            return self._cache[key]

        resp = self._retry(lambda: self.api.values().get(
            spreadsheetId=SPREADSHEET_IDS[book],
            range="'%s'" % sheet.replace("'", "''"),
            valueRenderOption='UNFORMATTED_VALUE',
            dateTimeRenderOption='SERIAL_NUMBER').execute())

        rows = resp.get('values', [])
        if len(rows) <= header_row:
            self._cache[key] = pd.DataFrame()
            return self._cache[key]

        cols = _dedupe(rows[header_row])
        width = len(cols)
        body = [(r + [None] * (width - len(r)))[:width]
                for r in rows[header_row + 1:]]
        df = pd.DataFrame(body, columns=cols).replace('', None)
        self._cache[key] = df
        return df
