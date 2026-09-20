# Senpilot Assessment — NSUARB Document Agent

## What this is
An email-driven agent for the Senpilot SWE intern technical assessment. It receives a
natural-language email requesting documents from a Nova Scotia Utility and Review Board
(NSUARB) regulatory matter, scrapes the public database, downloads up to 10 documents of
the requested type, zips them, and emails the ZIP back with a matter summary.

**Timebox: 3 hours. Due Wednesday 2026-09-23.** Partial completion is explicitly allowed;
working output beats polish.

## Input contract
An email containing:
- a matter number: `M` followed by exactly 5 digits (e.g. `M12205`, `M12383`)
- one of exactly five document types: **Exhibits, Key Documents, Other Documents,
  Transcripts, Recordings**

Example: *"Hi Agent, Can you give me Other Documents files from M12205? Thanks!"*

## Required workflow
1. Receive and interpret the email.
2. Go to https://uarb.novascotia.ca/fmi/webd/UARB15, enter the matter number in the
   **"Go Directly to Matter"** field.
3. Click Search, open the matter details.
4. Extract matter title/description, industry, type, dates, other available metadata.
5. Extract document counts for **all five** categories — not just the requested one.
6. Select the requested document-type tab.
7. Download up to 10 documents via the **"Go Get It"** buttons.
8. Zip the successfully downloaded files.
9. Reply to the original sender with: matter summary, all five counts, number of documents
   actually downloaded, and the ZIP attached.

## Reference example (NOT hardcoded data)
M12205 — Halifax Regional Water Commission, Windsor Street Exchange Redevelopment Project,
$69,270,000. Industry: Water. Type: Capital Expenditure. Initial filing 2025-04-07, final
filing 2025-10-23. Counts: 13 Exhibits / 5 Key Documents / 21 Other Documents /
0 Transcripts / 0 Recordings. An "Other Documents" request downloads 10 of the 21.

Every matter number must work — never hardcode these values.

## Architecture
Gemini interprets natural language; **deterministic Python code performs all actions.**

| Concern | Tool |
|---|---|
| Receive / send email | Gmail API (OAuth, dedicated Gmail account), 60s inbox polling |
| Parse request → structured | Gemini API, with regex/keyword fallback |
| Validate matter no. / doc type | Plain Python |
| Scrape + download | Playwright (sync API, Chromium) |
| Package | stdlib `zipfile` |
| Wire it together | `agent/main.py` orchestrator |

**Deliberately excluded:** frontend (email *is* the interface), database, LangChain/LangGraph,
FastAPI, Pub/Sub webhooks. Reply email uses a deterministic template — Gemini does not write it.

## Critical constraints
- The site is a **FileMaker WebDirect** app, *not* static HTML. Inspect real browser behavior;
  "Go Get It" may involve a popup or a filename click. **Never invent selectors.**
- Start Playwright with `headless=False` so actions are visible during development.
- **Never commit** credentials, OAuth tokens, API keys, or downloaded documents.
- Per-request temp directories; clean up after.
- Don't process the agent's own sent mail; don't double-process a message. Mark processed
  only *after* successful delivery.

## Verified site behaviour (probed live 2026-09-20 — do not re-guess)

The site is Vaadin/GWT (FileMaker WebDirect 8.27.7). **There are zero `<input>`, `<textarea>`,
`<a href>` or real `<button>` elements for fields** — everything is a styled `<div>`.
Consequences:
- `page.content()` returns a stale ~31KB shell; it does **not** reflect the rendered app.
  Use `page.evaluate("() => document.body.innerText")` / `document.querySelectorAll` instead.
- Fields are **clicked then typed into** (`locator.click()` + `keyboard.type`), never `fill()`.
- Everything renders ~3–10s after `domcontentloaded`. Gate on content, not `networkidle`.

**Verified selectors** (stable across 5+ independent browser sessions, and across matters):

| Thing | Selector |
|---|---|
| "Go Directly to Matter" field | `div.fm-textarea.fm_object_254` |
| Search button | `button#b0p0o258i0i0r1` |
| Category tabs | text, e.g. `get_by_text("Other Documents - ")` |
| Download modal | `.v-window.fm-modal-dialog` |

**Matter page** exposes, in `body.innerText`: `"<Category> - <count>"` for all five categories,
matter no, type, status, title/description, both dates, industry, and `"Found Count: N"`
for the selected tab.

**Download is a TWO-STEP click** (this is the popup the assignment hints at):
1. Click `GO GET IT` on the row → a **"Download Files" modal** opens (`.v-window.fm-modal-dialog`).
2. The modal contains a button labelled with the actual filename (e.g. `102674.pdf`).
   Clicking **that** fires the real Playwright `download` event. Then click `Close`.

Clicking GO GET IT alone fires **no** download event and opens **no** popup/new page — an
`expect_download` around step 1 times out. Confirmed working: `102674.pdf`, 170,571 bytes,
valid `%PDF-1.6`. Download URL pattern is
`/fmi/webd/APP/connector/0/<session>/dl/<docno>.pdf` — the session segment is per-session,
so **do not** try to fetch it directly; drive the UI.

**The document table is virtualised** — only ~12 rows render at a time regardless of
`Found Count`. Fine for the ≤10 requirement on a first page, but scrolling is needed if a
tab renders fewer than 10 rows initially. The modal text says "download each file" (plural),
so a multi-select batch download may exist — unexplored, possible speed-up.

**Live data drifts from the assignment PDF.** M12205 now reads 13 / 6 / 43 / 0 / 0 and
$69,275,000 (assignment example said 13 / 5 / 21 / 0 / 0 and $69,270,000). Extract, never hardcode.


### Verified behaviour added during steps 4-6
- Header fields have stable object ids: `fm_object_286` matter no, `289` status,
  `298` industry (site label "Type"), `287` category, `292` date received,
  `294` decision date, `295` outcome. The **title has no object id** — it is found
  by position (the widest `div.v-label` in the header band).
- `Found Count: N` renders **only when the list overflows**. Do not gate tab-ready on it;
  gate on `GO GET IT` rows existing.
- Row identifiers differ per tab: **doc numbers** (`102674`) on most tabs, **exhibit labels**
  (`A-1`, and real data contains `A -5`) on Exhibits. `ROWS_JS` matches both and must not
  match the Security column (`Public`).
- Each download is guarded: the modal filename must correspond to the row clicked, so a
  row/modal desync fails loudly instead of silently saving the wrong file.
- Unknown matter (e.g. M99999) → site modal "No Records Found"; raised as `MatterNotFound`
  in ~10s rather than hanging.
- The virtualised table renders ~12 rows at a 1600x1000 viewport, so ≤10 needs no scrolling.
  **A request for more than ~12 would need scroll handling — not implemented.**
- M12205 Other Documents contains **genuinely duplicated PDFs** (102329==102330,
  102197==102202, byte-identical). Verified as source data, not a scraper bug.


## Gmail behaviour (learned the hard way)

- **A new Gmail account files first-contact mail as spam.** The reviewers' first email will
  very likely land there. `messages.list` excludes spam unless `includeSpamTrash=True`, so
  the agent polls spam too. Policy: a spam message that parses as a valid request is rescued
  (`unmark_spam`) and answered; a spam message that does not parse is dropped **silently** --
  never reply to spam, it risks the account.
- **Never reply to automated senders.** Bounces (`mailer-daemon`), `no-reply@`, and
  `notifications@` are filtered by `mailer.is_automated()`; replying invites a mail loop.
- **Gmail caps messages at 25MB and base64 inflates attachments ~33%**, so the raw ZIP limit
  is 17MB (`MAX_ATTACHMENT_BYTES`), not 25.
- The agent's own replies may land in the *recipient's* spam folder too -- worth telling
  reviewers to check.

## Gemini quota

The free tier allows roughly 20 requests/day, so `GEMINI_MODE` gates the API:
`mock` (default, never calls), `live` (always), `auto` (live only when a key is set).
The regex/keyword fallback in `agent/parser.py` passes every parser test on its own, so
`mock` is fully functional -- flip to `live` only for the demo.

## Environment
`python` is **not** on PATH on this machine, and `py -3.13` is a broken Microsoft Store stub.
Use the venv binaries directly:
```
.venv\Scripts\python.exe      # Python 3.10.5
.venv\Scripts\pip.exe
```
(venv was created from `C:\Users\leo8j\AppData\Local\Programs\Python\Python310\python.exe`)

## Implementation order — follow incrementally, test each step
1. ✅ Environment, deps, `.gitignore`, `.env.example`, skeleton
2. ✅ Investigate the website (M12205 + M12383) — see *Verified site behaviour* above
3. ✅ Minimal Playwright script: open → search → matter page — `agent/scraper.py`
4. ✅ Metadata + all five counts → `extract_matter_info()` in `agent/scraper.py`
5. ✅ Tab selection + download ≤10 → `select_category()` / `download_documents()`
6. ✅ ZIP + verification → `agent/packager.py`; CLI at `agent/cli.py`.
   **Milestone reached: lookup → download → ZIP works from the terminal, no Gmail.**
7. ✅ Gmail API — `agent/mailer.py`. Authorised as senpilot.oa.agent@gmail.com; send with
   a real 10MB ZIP attachment confirmed delivered.
8. ✅ Gemini request parsing — `agent/parser.py`, with a regex/keyword fallback that works
   with no API key at all. Invalid/incomplete requests raise `ParseError`.
9. ✅ Orchestrator — `agent/main.py`; all six routing branches tested with stubs.
10. ✅ Live end-to-end — real inbound email from leo8.jia@gmail.com parsed by live Gemini
    (`gemini-3.5-flash-lite`) → M12383 / Key Documents → 4 docs → ZIP → threaded reply, 24s.
11. *If time remains:* reliability, optional background-worker deployment. Never at the
    expense of core functionality.
12. ✅ README written. Leo records the
    Loom and submits.

## How to work on this
One milestone at a time — explain briefly, give the code/commands, say how to test, then
**wait for results** before continuing. Prioritize browser navigation and downloading; those
are the real unknowns. Simple readable Python, no unnecessary abstractions or docs. Don't
assume something works without testing it.
