# Loom demo script (5 minutes)

Target: **4:30**, leaving buffer. The goal is to show the agent working and then show the
three things that make it more than a scraper: the two-step download, the parser pairing,
and honest failure handling.

## Before recording

```bash
# 1. live Gemini for the demo
#    .env -> GEMINI_MODE=live

# 2. warm the browser cache so the first run is not slow
.venv/Scripts/python.exe agent/cli.py M12383 "Key Documents"

# 3. clear the decks
rm -rf output
``` 

Have open and ready:
- A terminal, large font
- Gmail as **your own account**, composing to `senpilot.oa.agent@gmail.com`
- The repo on GitHub
- `https://uarb.novascotia.ca/fmi/webd/UARB15` in a browser tab

Pre-send nothing. The live arrival is the demo.

---

## 0:00 - 0:25 · What it is

> "This is an email agent for Nova Scotia utility regulatory filings. You email it a matter
> number and a document type, and it replies with a summary of the matter, the document
> counts for all five categories, and a ZIP of up to ten documents.
>
> Email is the whole interface. There's no UI to build and nothing for the user to learn."

## 0:25 - 1:10 · Send the request, start the agent

Type the email live:

> **To:** senpilot.oa.agent@gmail.com
> **Subject:** Document request
> **Body:** `Hi Agent, Can you give me Other Documents files from M12205? Thanks!`

Send it. Then in the terminal:

```bash
.venv/Scripts/python.exe agent/main.py --once
```

While it runs, narrate over the log:

> "It's polling Gmail, and it parsed that into matter M12205, Other Documents. Now it's
> driving a real browser against the board's site, downloading each document."

## 1:10 - 1:50 · The reply

Switch to Gmail, show the reply arriving **in the same thread**.

Point at, in order:
- The matter summary: title, industry, type, status, both dates
- **All five counts** - "note it reports every category, not just the one asked for"
- Downloaded 10 of 43, and the limit
- The ZIP attachment - open it, show ten real PDFs

> "End to end, that's about forty seconds."

## 1:50 - 2:35 · Why this site was the hard part

Open the NSUARB site. Open DevTools, run in the console:

```js
document.querySelectorAll('input').length     // 0
```

> "It's a FileMaker WebDirect app. There is not a single input, link, or button on the
> page - every field is a styled div. Playwright's `fill()` can't work here; fields have to
> be clicked and typed into. And `page.content()` returns a stale shell that never updates,
> so everything has to go through `page.evaluate()` against the live DOM.
>
> The downloads were the real surprise."

Click a `GO GET IT` on the site to show the modal.

> "Clicking Go Get It downloads nothing. It opens this modal, and the filename inside it is
> the actual download button. Two clicks, not one. Recordings are a third variant again -
> they open a save-as dialog first, so that's three steps."

## 2:35 - 3:20 · Why an LLM is here at all

> "Gemini interprets the request. Everything after that is deterministic code - the reply is
> a fixed template, so the agent can't hallucinate a document count.
>
> There's also a regex fallback, so it works with no API key at all. Which raises a fair
> question: why keep Gemini?"

Run:

```bash
.venv/Scripts/python.exe demo_parser.py
```

> "Here's a request where the regex is confidently wrong. 'The documents that aren't
> exhibits or key documents' - regex sees 'exhibits' first and picks Exhibits. Gemini reads
> the negation and correctly returns Other Documents.
>
> And a matter number typed with spaces - regex misses it entirely, Gemini recovers it.
> That's the whole justification: Gemini handles intent, code handles action."

## 3:20 - 3:45 · It asks instead of guessing

Send a second email live:

> `Can you send me exhibits and transcripts for M12205 and M12383?`

Run the agent again. Show the reply.

> "That request names two matters and two document types. Rather than picking one and
> hoping, it lists what it saw and asks which I meant. Gemini decides whether a request is
> genuinely ambiguous or whether a type is just mentioned in passing - the negation example
> earlier mentions two types too, and it correctly doesn't ask there."

## 3:45 - 4:05 · Failure handling

> "Most of my time went into things going wrong."

Pick two or three:

- **Nonexistent matter** - `agent/cli.py M99999 Exhibits` fails cleanly in ten seconds with
  the board's own message, instead of hanging.
- **Oversize archive** - ten Exhibits from M12205 zip to 117MB. Email can't carry that, so
  it attaches as many as fit and names the rest in the reply.
- **Spam** - "A new Gmail account files first-contact mail as spam. My own first test email
  landed there and the agent couldn't see it. A reviewer's first email would have hit the
  same wall. So it polls spam: valid requests are rescued and answered, junk is dropped
  silently without a reply."
- **Per-document failures** - one bad download doesn't abandon the other nine.

## 4:05 - 4:30 · What I'd do next, and close

Optionally show `pytest tests -q` passing - 77 offline tests, one for every bug found.

> "Known limits: it runs while my machine does, so there's no hosted deployment. Archives
> over 17MB can't be attached. And Transcripts I never got to test, because only one matter
> in the twenty-two I sampled had any recordings at all and none had transcripts.
>
> With more time: host it as a worker, and cache documents between requests.
>
> One thing worth saying - the counts you just saw don't match the assignment's example.
> The board has kept filing since it was written. Nothing here is hardcoded; it all comes
> off the live page."

---

## Fallback if the live run fails

Don't debug on camera. Say *"the board's site is being slow, here's a run from a minute
ago"* and cut to a pre-recorded successful run. **Record one before you start.**

## Cut these first if over time

1. The 1:50 DevTools segment - describe it instead of demonstrating
2. Failure examples - keep the spam one, it is the most interesting
3. The "what I'd do next" list - keep only the honest limitations
