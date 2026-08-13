# Style Lounge — Customer Lifecycle Dashboard

Reads the four live Google Sheets, joins them on customer phone number, and
serves one lifecycle timeline per customer behind a password.

**No customer data is stored in this repository.** Everything is read from the
sheets at run time and cached only in Vercel KV. There is no public URL: both
routes require HTTP Basic Auth before returning a byte.

---

## How it fits together

```
Google Sheets (4 workbooks, live)
        │  service account, read-only
        ▼
  api/refresh.py   ← Vercel cron, 07:00 IST daily
        │  gzip → 74 KB
        ▼
   Vercel KV  ──────►  api/data.py   ─┐
                                       ├─ Basic Auth on both
                       api/index.py   ─┘
                            │
                            ▼
                    Webflow → link
```

| Route | Purpose |
|---|---|
| `/` | The dashboard page. Basic Auth. |
| `/api/data` | The payload as JSON. Basic Auth. Served from KV. |
| `/api/refresh` | Recompute and cache. Cron, or an authenticated human. |

### Why the cache exists

A full run takes ~24s, and 16s of that is just pulling 58k rows out of the
Sheets API. That cannot be parallelised — `googleapiclient`'s httplib2
transport is not thread-safe, and forcing concurrency made it 4× slower before
corrupting the SSL stream. So the cron writes and the dashboard reads.

If KV is not configured everything still works; `/api/data` just computes on
demand and the first load takes ~24s.

---

## Setup

### 1. Environment variables

In Vercel → Settings → Environment Variables, for **all** environments:

| Name | Value |
|---|---|
| `GOOGLE_CREDS` | the entire service-account JSON, pasted as one value |
| `DASH_USER` | the username your team types |
| `DASH_PASS` | the password your team types |
| `CRON_SECRET` | any long random string; Vercel signs cron calls with it |

`KV_REST_API_URL` and `KV_REST_API_TOKEN` are added automatically when you
attach a KV store (Storage → Create → KV → connect to this project).

### 2. Share the sheets

Give **Viewer** access on all four spreadsheets to the service-account address
in `GOOGLE_CREDS` (`client_email`).

### 3. Deploy

Push to `main`. Vercel builds automatically. Then visit `/api/refresh` once,
signed in, to populate the cache before anyone opens the dashboard.

---

## Daily operation

The cron in `vercel.json` runs `/api/refresh` at 01:30 UTC (07:00 IST). It
re-reads every sheet and replaces the cached payload. Nothing else is needed —
there is no local machine in the loop.

To force a refresh after fixing a sheet, open `/api/refresh` in a browser and
sign in. It returns a summary:

```json
{"ok": true, "seconds": 23.4, "customers": 14523, "events": 20786}
```

---

## Editing the dashboard

`../dashboard/template.html` is the single source of truth for the UI. After
editing it:

```bash
python make_template.py
```

That regenerates `api/_lib/template.html`, stripping the passphrase gate and
pointing the page at `/api/data`. It fails loudly if any decryption code
survives, so the hosted build can never quietly reintroduce a second,
half-managed access path.

## Checking a change

```bash
python test_local.py
```

Runs the real pipeline against the live sheets and asserts the totals against
known-good figures (14,523 customers / 20,786 events / ₹11,63,776). Catches a
renamed tab or moved column before it reaches the team.

---

## Known data issues in the sheets

- **CRM `registrations` phone column is formatted as a date.** The values are
  intact but a formatted read returns blanks, losing ~700 customers. The code
  works around it with `UNFORMATTED_VALUE`; fixing the column format in Sheets
  would be better.
- **`A-Combined Follow-ups` grows two columns per day** (`Remarks DD-MM-YYYY`).
  It is 44 wide already. The pipeline unpivots them, but a tall `Call Log`
  sheet would be healthier.
- **47 of 106 members have no registration record** — signed up in the salon,
  never in the app, so they are invisible to app-driven nudge lists.
- The `N Time Users` tabs are stale snapshots; booking counts are recomputed
  from raw `bookings` instead.
