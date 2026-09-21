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
Gmail inbox (polled)
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
| `agent/packager.py` | ZIP creation, size-aware splitting, verification |
| `agent/cli.py` | Terminal entry point; runs the scrape without Gmail |

## Setup

Requires Python 3.10+.

```bash
python -m venv .venv
.venv/Scripts/python.exe -m pip install -r requirements.txt
.venv/Scripts/python.exe -m playwright install chromium

cp .env.example .env
```

Paths below use the Windows interpreter, `.venv/Scripts/python.exe`. On macOS or Linux
substitute `.venv/bin/python` throughout.

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
.venv/Scripts/python.exe agent/cli.py M12205 "Other Documents" --dry-run  # metadata only
```

Run the tests (offline -- no network, Gmail or browser):

```bash
.venv/Scripts/python.exe -m pytest tests -q
```

Compare the two parsers side by side (used in the demo):

```bash
.venv/Scripts/python.exe demo_parser.py
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

**Layout differs per tab.** Most tabs identify rows by document number (`102674`),
Exhibits uses exhibit labels (`H-4(C)-iii`, and the live data contains `A -5` with a
stray space), and Recordings uses a date. These vary too much to pattern-match, so rows
are identified by layout instead. The `Found Count` label only renders when the list
overflows, so tab-readiness waits on rows rather than on that label.

**Recordings download differently again.** Container fields open an *Export Field to File*
dialog first -- a filename box and an OK button -- and only then the usual download modal.
The dialog pre-fills the correct name (`M 12400.MP3`), so the agent accepts that default
rather than inventing one. This makes Recordings a three-click sequence where every other
tab takes two.

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
- **Ambiguous requests are questioned, not guessed.** An email naming two matters or two
  document types gets a reply listing the options and asking which was meant. Gemini
  distinguishes real ambiguity from a type that is merely mentioned in passing, such as a
  negation ("everything that isn't exhibits") or a quoted reply chain.
- **The reply reads as prose, with a manifest.** A short summary of the matter, the counts
  in a sentence, then the documents listed by number and title so the archive does not have
  to be opened to see what arrived.
- **Oversize archives are split, not refused.** As many documents as fit under the limit are
  attached and the rest named in the reply.
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
- **Requests are retried, but not forever.** A crashed browser or a site timeout leaves the
  message unread so the next poll retries it; after three failures the sender is told the
  lookup failed rather than being left waiting indefinitely.

## Deployment

The agent runs as a scheduled GitHub Actions workflow (`.github/workflows/agent.yml`), so it
answers email without a machine of its own. Each run boots a fresh container, installs
Chromium, polls Gmail once, handles anything it finds, and exits. That works because the job
is effectively stateless -- only the OAuth refresh token has to persist, and it lives in
repository secrets.

Configure once, under Settings -> Secrets and variables -> Actions:

| Name | Kind | Value |
|---|---|---|
| `GMAIL_CREDENTIALS` | secret | contents of `credentials.json` |
| `GMAIL_TOKEN` | secret | contents of `token.json` |
| `GEMINI_API_KEY` | secret | Gemini key (optional) |
| `GEMINI_MODE` | variable | `live` or `mock` (defaults to `mock`) |

Two settings exist for hosts that differ from a developer laptop:

- `TIMING_SCALE` multiplies every browser wait in the scraper, so a slower or more distant
  host can be accommodated without retuning each timeout individually. CI uses `2.0`, and it
  is also a `workflow_dispatch` input for re-running a flaky job more patiently.
- `NON_INTERACTIVE` makes the OAuth flow fail fast with a clear message instead of blocking
  on a browser that a server does not have.

Runs can also be triggered by hand from the Actions tab, which is the quickest way to check
the deployment is healthy.

### What triggers a run

GitHub's own `schedule` trigger is best-effort: runs are queued, deprioritised under load,
and may be dropped entirely. On this repository it did not fire once in the first half hour
after the workflow was added, which is not good enough for an agent that is supposed to be
reachable.

So the primary trigger is `repository_dispatch`, poked by an external pinger
([cron-job.org](https://cron-job.org), free) every few minutes:

```
POST https://api.github.com/repos/<owner>/<repo>/dispatches
Authorization: Bearer <fine-grained PAT with Actions: read and write>
Accept: application/vnd.github+json
Content-Type: application/json

{"event_type": "poll"}
```

That starts a run the moment the request lands. The `schedule` trigger is kept as a backstop
for whenever it does decide to fire. Neither is a guarantee, so treat the response time as
"within a few minutes, usually" rather than as a promise.

**Token expiry.** The OAuth consent screen uses restricted Gmail scopes and stays in
*Testing*, for which Google expires refresh tokens weekly. When runs start failing
authentication, re-run `agent/mailer.py auth` locally and update the `GMAIL_TOKEN` secret.

## Limitations

- **Transcripts are untested.** No matter among the 22 sampled had any. They are container
  fields like Recordings and so take the same export path, which is tested, but that is
  inference rather than evidence.
- **Maximum ~12 documents per request.** The document table is virtualised and renders
  about 12 rows at a time; the 10-document requirement never hits this, but a larger
  request would need scroll handling.
- **Attachments are capped at 17MB.** Gmail rejects messages over 25MB and base64 inflates
  attachments by roughly a third. Some matters hold very large filings -- ten Exhibits from
  M12205 come to 117MB. The agent attaches as many as fit and names the rest in the reply,
  so a request is never silently dropped, but very large documents cannot be emailed.
- **OAuth tokens expire after 7 days.** The consent screen uses restricted Gmail scopes and
  stays in *Testing* status, for which Google expires refresh tokens weekly. Re-run
  `agent/mailer.py auth` to restore access and update the `GMAIL_TOKEN` secret; publishing to
  production would require Google verification.
- **Replies are not instant.** Deployed, the agent polls on a schedule, so a request is
  answered within roughly 5-20 minutes rather than immediately. Run locally it polls every
  60 seconds.
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
| M12205 / Exhibits (13 available) | capped at 10, incl. `H-4(C)-iii` style labels |
| M12400 / Recordings | 22MB MP3 via the export dialog, valid audio |
| Oversize archive (117MB) | replies with summary, explains the omission |
| Bounce / no-reply sender | ignored, no reply |
| Ambiguous request | asks which matter or type was meant |
| Oversize archive (117MB) | 6 attached, 4 named as too large |

77 offline tests cover the parser, reply formatting, packaging, mail handling and the
scraper's pure logic, including a regression test for every bug found during development.
