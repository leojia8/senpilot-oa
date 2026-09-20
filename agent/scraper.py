"""Playwright driver for the NSUARB FileMaker WebDirect public documents database.

WebDirect renders everything as <div>s driven by Vaadin/GWT -- there are no <input>,
<a href> or real <button> elements for fields, so fields are clicked and typed into
rather than filled, and page.content() returns a stale shell that must not be used.
Selectors below were verified against the live site; see CLAUDE.md.
"""
import re
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

URL = "https://uarb.novascotia.ca/fmi/webd/UARB15"

# FileMaker layout-object classes/ids. Stable across independent sessions (verified).
MATTER_FIELD = "div.fm-textarea.fm_object_254"
SEARCH_BUTTON = "button#b0p0o258i0i0r1"
MODAL = ".v-window.fm-modal-dialog"

CATEGORIES = ["Exhibits", "Key Documents", "Other Documents", "Transcripts", "Recordings"]
MATTER_RE = re.compile(r"^M\d{5}$")

# The matter page is ready once the category tab bar renders.
TAB_BAR_RE = re.compile(r"Exhibits - \d+")


class MatterNotFound(Exception):
    """Matter number is well-formed but the board has no such matter."""


def validate_matter(matter):
    m = (matter or "").strip().upper()
    if not MATTER_RE.match(m):
        raise ValueError(f"invalid matter number: {matter!r} (expected M followed by 5 digits)")
    return m


def new_page(pw, headless=False):
    browser = pw.chromium.launch(headless=headless)
    ctx = browser.new_context(viewport={"width": 1600, "height": 1000}, accept_downloads=True)
    return browser, ctx, ctx.new_page()


def open_matter(page, matter, timeout=45):
    """Search for a matter number and land on its detail page."""
    matter = validate_matter(matter)
    page.goto(URL, timeout=60000, wait_until="domcontentloaded")
    page.wait_for_selector(MATTER_FIELD, timeout=60000)

    page.locator(MATTER_FIELD).click()
    page.wait_for_timeout(800)
    page.keyboard.type(matter, delay=100)
    page.wait_for_timeout(500)
    page.click(SEARCH_BUTTON)

    # An unknown matter leaves us on the search screen with an error dialog, so
    # race the tab bar against a modal that sticks around.
    deadline, modal_seen = time.monotonic() + timeout, 0
    while time.monotonic() < deadline:
        if TAB_BAR_RE.search(page.evaluate("() => document.body.innerText")):
            page.wait_for_timeout(1500)
            return matter
        if page.locator(MODAL).count():
            modal_seen += 1
            if modal_seen >= 3:
                note = page.locator(MODAL).first.inner_text().strip().replace("\n", " ")
                raise MatterNotFound(f"{matter}: {note[:150]}")
        else:
            modal_seen = 0
        page.wait_for_timeout(1000)
    raise MatterNotFound(f"{matter}: matter page did not load within {timeout}s")


# --- metadata ---------------------------------------------------------------
# Header field object ids (verified on M12205/M12383). NB: the site labels
# fm_object_298 "Type" and fm_object_287 "Category"; the assignment calls those
# "industry" and "type" respectively.
FIELDS = {
    "matter_no": "fm_object_286",
    "status": "fm_object_289",
    "industry": "fm_object_298",
    "category": "fm_object_287",
    "date_received": "fm_object_292",
    "decision_date": "fm_object_294",
    "outcome": "fm_object_295",
}

# The title is a plain label with no fm_object id; find it by position in the
# header band, to the right of the matter number.
TITLE_JS = """() => {
  const els = [...document.querySelectorAll('div.v-label')].map(e => {
    const r = e.getBoundingClientRect();
    return {txt: (e.textContent || '').trim(), x: r.x, y: r.y};
  }).filter(o => o.txt && o.x > 140 && o.x < 860 && o.y > 160 && o.y < 240);
  els.sort((a, b) => b.txt.length - a.txt.length);
  return els.length ? els[0].txt : null;
}"""


def get_counts(page):
    text = page.evaluate("() => document.body.innerText")
    counts = {}
    for cat in CATEGORIES:
        m = re.search(rf"{re.escape(cat)} - (\d+)", text)
        counts[cat] = int(m.group(1)) if m else None
    return counts


def extract_matter_info(page):
    """Structured metadata plus all five document counts for the open matter."""
    info = {}
    for key, obj in FIELDS.items():
        val = page.evaluate(
            "(cls) => { const e = document.querySelector('div.' + cls);"
            " return e ? e.innerText.trim() : null; }", obj)
        info[key] = val or None
    info["title"] = page.evaluate(TITLE_JS)
    info["counts"] = get_counts(page)
    return info


# --- document table ---------------------------------------------------------
HAS_ROWS_JS = ("() => [...document.querySelectorAll('*')].some(e =>"
               " e.children.length === 0 && /GO GET IT/i.test(e.textContent))")


def select_category(page, category):
    """Click a category tab. Returns the tab's document count."""
    if category not in CATEGORIES:
        raise ValueError(f"unknown category: {category!r}")
    count = get_counts(page).get(category)
    if not count:
        return 0
    page.get_by_text(f"{category} - ", exact=False).first.click()
    # "Found Count" only renders when the list overflows, so wait on the rows.
    page.wait_for_function(HAS_ROWS_JS, timeout=60000)
    page.wait_for_timeout(2500)
    return count


# Pair each GO GET IT control with the document number in its own table row,
# matching on vertical position (the table is a positioned grid, not a <table>).
ROWS_JS = """() => {
  const ggi = [...document.querySelectorAll('*')]
    .filter(e => e.children.length === 0 && /GO GET IT/i.test(e.textContent));
  // Identifiers vary far too much to pattern-match: doc numbers ("102674") on
  // most tabs, but exhibit labels on Exhibits -- "H-1", "H-4(C)", "H-4(C)-iii",
  // and real data contains "A -5" with a stray space. So match on layout
  // instead: the identifier and the Security value share the leftmost column,
  // with the identifier sitting just above it. Titles are further right, so the
  // x bound must stay tight enough to exclude them.
  const left = [...document.querySelectorAll('div.text')].map(d => {
    const r = d.getBoundingClientRect();
    return {t: (d.textContent || '').trim(), x: r.x, y: r.y + r.height / 2};
  }).filter(o => o.t && o.x < 100);
  return ggi.map((e, i) => {
    const r = e.getBoundingClientRect(), y = r.y + r.height / 2;
    const c = left.filter(o => Math.abs(o.y - y) < 40).sort((a, b) => a.y - b.y);
    return {index: i, doc_no: c.length ? c[0].t : null, y: Math.round(y)};
  });
}"""

# The modal's download control is a bare div labelled with the real filename.
MODAL_FILE_JS = """() => {
  const w = document.querySelector('.v-window.fm-modal-dialog');
  if (!w) return null;
  // Filenames contain brackets and spaces ("H-4(C).pdf"), so match on having a
  // plausible extension rather than on an allow-list of characters.
  const t = [...w.querySelectorAll('*')]
    .filter(e => e.children.length === 0)
    .map(e => (e.textContent || '').trim())
    .filter(s => s && s !== 'Close' && s.length <= 150 && /\\.[A-Za-z0-9]{2,5}$/.test(s));
  return t.length ? t[0] : null;
}"""


def _norm(s):
    return re.sub(r"\s+", "", (s or "")).lower()


def _unique(dest, name):
    path = dest / name
    if not path.exists():
        return path
    stem, suffix, n = path.stem, path.suffix, 2
    while (dest / f"{stem}_{n}{suffix}").exists():
        n += 1
    return dest / f"{stem}_{n}{suffix}"


def _close_modal(page):
    try:
        if page.locator(MODAL).count():
            page.locator(MODAL).get_by_text("Close", exact=True).first.click()
            page.wait_for_timeout(800)
    except Exception:
        pass


def download_documents(page, dest, max_docs=10, log=print):
    """Download up to max_docs from the currently selected tab.

    Each download takes two clicks: GO GET IT opens a modal, and the filename
    button inside that modal fires the actual download. Failures are per-document.
    """
    dest = Path(dest)
    dest.mkdir(parents=True, exist_ok=True)
    rows = page.evaluate(ROWS_JS)
    saved, failed = [], []

    for row in rows[:max_docs]:
        label = row["doc_no"] or f"row{row['index']}"
        try:
            page.get_by_text("GO GET IT", exact=False).nth(row["index"]).click()
            page.wait_for_selector(MODAL, state="visible", timeout=30000)
            name = page.evaluate(MODAL_FILE_JS)
            if not name:
                raise RuntimeError("no filename button in modal")
            # The modal must offer the file from the row we actually clicked.
            # Compare loosely: case and stray spaces differ between the two
            # ("A -5" in the table becomes "A -5.pdf", "H-5(c)-ii" may re-case).
            if row["doc_no"] and not _norm(name).startswith(_norm(row["doc_no"])):
                raise RuntimeError(f"row/modal mismatch: row {row['doc_no']} -> {name}")
            with page.expect_download(timeout=60000) as dl:
                page.locator(MODAL).get_by_text(name, exact=True).first.click()
            path = _unique(dest, name)
            dl.value.save_as(str(path))
            saved.append(path)
            log(f"    ok   {name} ({path.stat().st_size:,} bytes)")
        except Exception as e:
            failed.append(label)
            log(f"    FAIL {label}: {type(e).__name__}: {str(e)[:90]}")
        finally:
            _close_modal(page)

    return saved, failed


def fetch(matter, category, dest, max_docs=10, headless=True, log=print):
    """Full workflow: open matter -> metadata -> download. Returns (info, files, failed)."""
    with sync_playwright() as pw:
        browser, _ctx, page = new_page(pw, headless=headless)
        try:
            open_matter(page, matter)
            info = extract_matter_info(page)
            available = select_category(page, category)
            if not available:
                return info, [], []
            files, failed = download_documents(page, dest, max_docs, log)
            return info, files, failed
        finally:
            browser.close()
