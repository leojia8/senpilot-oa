# NSUARB Document Agent

An email-driven agent that retrieves regulatory documents from the Nova Scotia Utility
and Review Board public database.

Email it a matter number and a document type; it replies with a matter summary, the
document counts for all five categories, and a ZIP of up to 10 documents.

**Agent address:** `senpilot.oa.agent@gmail.com`

```
"Hi Agent, Can you give me Other Documents files from M12205? Thanks!"

        -> M12205 - Other Documents (10 documents)
           + M12205_Other_Documents.zip
```

## How it works

```
Gmail inbox (polled every 60s)
    |
    v
parse request ................ Gemini structured output, regex/keyword fallback
    |                          -> matter number (M#####) + one of five categories
    v
scrape NSUARB ................ Playwright / Chromium
    |                          -> search matter, extract metadata + all five counts,
    |                             select tab, download up to 10 documents
    v
package ...................... zipfile + integrity verification
    |
    v
reply ........................ deterministic template + ZIP attachment,
                               threaded into the original message
```

The division of labour is deliberate: **Gemini only interprets the request.** Every
action -- navigation, extraction, downloading, packaging, replying -- is deterministic
Python. The reply is a fixed template, not generated text, so the agent cannot
hallucinate a document count.

## Modules

| File | Role |
|---|---|
| `agent/main.py` | Orchestrator: poll, route, reply, mark processed |
| `agent/scraper.py` | Playwright driver for the NSUARB site |
| `agent/mailer.py` | Gmail OAuth, reading, MIME assembly, sending |
| `agent/parser.py` | Request interpretation (Gemini + fallback) |
| `agent/reply.py` | Deterministic reply and error templates |
| `agent/packager.py` | ZIP creation and verification |
| `agent/cli.py` | Terminal entry point; runs the scrape without Gmail |

## Setup

Requires Python 3.10+.

```bash
python -m venv .venv
.venv/Scripts/python.exe -m pip install -r requirements.txt
.venv/Scripts/python.exe -m playwright install chromium

cp .env.example .env
```

### Gmail credentials

1. Create a Google Cloud project on the agent's own Google account.
2. Enable the **Gmail API**.
3. Configure the OAuth consent screen: **External**, still in **Testing**, and add the
   agent's own address as a test user.
4. Create an OAuth client ID of type **Desktop app**, download the JSON, and save it as
   `credentials.json` in the project root.
5. Authorise:

```bash
.venv/Scripts/python.exe agent/mailer.py auth
```

This opens a browser once and writes `token.json`, which refreshes itself afterwards.
`credentials.json`, `token.json` and `.env` are all gitignored and must never be committed.

### Gemini (optional)

Get a key from [aistudio.google.com/apikey](https://aistudio.google.com/apikey) and set it
in `.env`. `GEMINI_MODE` controls whether the API is called at all:

| Mode | Behaviour |
|---|---|
| `mock` | Never calls Gemini; deterministic parser only. Costs nothing. |
| `live` | Calls Gemini, falling back to regex if the call fails. |
| `auto` | Live when a key is set, otherwise mock. |

The free tier is limited (~20 requests/day), so `mock` is the default. The fallback
parser handles ordinary requests on its own, so the agent is fully functional without a
Gemini key -- see *Why both parsers* below.

## Usage

Run the agent:

```bash
.venv/Scripts/python.exe agent/main.py          # poll forever
.venv/Scripts/python.exe agent/main.py --once   # single pass, useful for testing
```

Run just the scrape, no Gmail involved:

```bash
.venv/Scripts/python.exe agent/cli.py M12205 "Other Documents"
```

Inspect the mailbox:

```bash
.venv/Scripts/python.exe agent/mailer.py auth   # show authorised address
.venv/Scripts/python.exe agent/mailer.py list   # show unread requests
```

## Notes on the NSUARB site

The database is a **FileMaker WebDirect** application (Vaadin/GWT), which behaves very
differently from a static site. Three things shaped the implementation:

**There are no form elements.** No `<input>`, `<a href>` or real `<button>` anywhere --
every field is a styled `<div>`. Fields must be clicked and typed into rather than
filled, and `page.content()` returns a stale shell that never reflects the rendered app,
so all extraction goes through `page.evaluate()` against the live DOM.

**Downloading takes two clicks.** Clicking `GO GET IT` fires no download; it opens a
modal containing a button labelled with the real filename, and clicking *that* starts the
transfer. Each download is guarded so the modal's filename must correspond to the table
row that was clicked -- a desync fails loudly rather than silently saving the wrong file.

**Layout differs per tab.** Most tabs identify rows by document number (`102674`), but
Exhibits uses exhibit labels (`A-1`, and the live data contains `A -5` with a stray
space). The `Found Count` label only renders when the list overflows, so tab-readiness is
detected by waiting for rows rather than for that label.

## Why both parsers

The regex fallback handles ordinary phrasing correctly and costs nothing. Gemini earns
its place on requests that need actual comprehension:

| Request | Regex | Gemini |
|---|---|---|
| "documents that aren't exhibits or key documents" | Exhibits ✗ | Other Documents ✓ |
| "matter M 1 2 3 8 3" | no match ✗ | M12383 ✓ |

Gemini runs first; the fallback fills in anything it misses or if the call fails. A
request that cannot be resolved either way gets a reply explaining the expected format
rather than a guess.

## Behaviour

- **All five category counts** are reported on every reply, not just the requested one.
- **Nothing is hardcoded.** Counts, metadata and titles are extracted per request. Live
  counts already differ from the assignment's examples because the board keeps filing.
- **Spam is polled deliberately.** A new Gmail account files first-contact mail as spam,
  which would make the agent appear dead to anyone emailing it for the first time. A spam
  message that parses as a valid request is rescued to the inbox and answered; one that
  does not parse is dropped silently.
- **Automated senders are never answered.** Bounces, `no-reply@` and `notifications@` are
  filtered, since replying invites a mail loop.
- **A message is marked read only after its reply is sent**, so a crash mid-request leaves
  it to be retried rather than silently dropped.
- **Failures are per-document.** One bad download does not abandon the other nine; the
  reply reports how many succeeded.

## Limitations

- **Maximum ~12 documents per request.** The document table is virtualised and renders
  about 12 rows at a time; the 10-document requirement never hits this, but a larger
  request would need scroll handling.
- **Attachments are capped at 17MB.** Gmail rejects messages over 25MB and base64 inflates
  attachments by roughly a third. Ten large PDFs can approach this.
- **The agent runs only while its host does.** It polls from wherever it is started; there
  is no hosted deployment.
- **Documents are fetched fresh every request.** There is no caching, so repeat requests
  re-download. Typical end-to-end time is 25-45 seconds.
- **Some matters contain genuinely duplicate PDFs.** M12205 serves byte-identical files
  under different document numbers; this is board data, not a scraper fault.

## Tested

Verified against the live site and live Gmail:

| Case | Result |
|---|---|
| M12205 / Other Documents | 10 of 43 downloaded, ZIP verified |
| M12383 / Key Documents | 4 of 4, delivered by real inbound email |
| M12383 / Exhibits | 6 of 6, including the `A -5` filename quirk |
| M12205 / Recordings (0 available) | metadata returned, no ZIP, no crash |
| M99999 (nonexistent) | clean error in ~10s |
| Malformed matter numbers | rejected before any browser launch |
| Unparseable request | reply explaining the expected format |
| Bounce / no-reply sender | ignored, no reply |
