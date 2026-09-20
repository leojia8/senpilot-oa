"""Turn a natural-language email into (matter number, document category).

Gemini does the interpreting; a regex/keyword fallback covers the case where no
API key is configured or the call fails, so the agent never hard-depends on it.
"""
import json
import os
import re

from dotenv import load_dotenv

from scraper import CATEGORIES, MATTER_RE

load_dotenv()

# GEMINI_MODE: "mock" never calls the API (deterministic fallback only -- keeps
# the free tier's daily quota intact), "live" always calls it, "auto" calls it
# only when a key is configured. Read lazily so .env and tests can override.
DEFAULT_MODE = "mock"

# Keyword -> canonical category. Longer/more specific phrases must win, so the
# fallback scores by earliest match of the most specific alias.
ALIASES = {
    "Exhibits": ["exhibit"],
    "Key Documents": ["key document", "key doc", "keydoc"],
    "Other Documents": ["other document", "other doc", "otherdoc"],
    "Transcripts": ["transcript"],
    "Recordings": ["recording", "audio"],
}

MATTER_IN_TEXT = re.compile(r"\bM\s?-?\s?(\d{5})\b", re.IGNORECASE)

PROMPT = """Extract the regulatory matter number and the requested document type \
from this email.

The matter number is the letter M followed by exactly 5 digits.
The document type must be exactly one of: Exhibits, Key Documents, Other Documents, \
Transcripts, Recordings.

If either value is missing, return null for it. Do not guess.

Set "ambiguous" to true only when the email genuinely asks for more than one matter
or more than one document type and there is no single clear intent. A request that
mentions a type only to exclude it (for example "everything that isn't exhibits")
is NOT ambiguous -- resolve it to the type actually wanted.

Email:
---
{text}
---"""

SCHEMA = {
    "type": "object",
    "properties": {
        "matter_number": {"type": "string", "nullable": True},
        "document_type": {
            "type": "string",
            "enum": CATEGORIES,
            "nullable": True,
        },
        "ambiguous": {"type": "boolean"},
    },
    "required": ["matter_number", "document_type", "ambiguous"],
}


class ParseError(Exception):
    """The email did not contain a usable request."""


class AmbiguousRequest(Exception):
    """The email named more than one matter or document type."""

    def __init__(self, matters, categories):
        self.matters = matters
        self.categories = categories
        super().__init__("ambiguous request")


# Everything below a reply marker or signature belongs to an earlier message and
# must not be mistaken for part of the request.
QUOTE_MARKERS = re.compile(
    r"^\s*(>|On .{0,80}\bwrote:|-{2,}\s*Original Message|From:\s|Sent from )",
    re.IGNORECASE | re.MULTILINE)
SIGNATURE = re.compile(r"^--\s*$", re.MULTILINE)


def strip_quoted(text):
    """Drop quoted reply chains and signatures."""
    t = _clean(text)
    m = SIGNATURE.search(t)
    if m:
        t = t[:m.start()]
    lines, kept = t.splitlines(), []
    for line in lines:
        if QUOTE_MARKERS.match(line):
            break
        kept.append(line)
    return "\n".join(kept) if kept else t


def find_candidates(text):
    """Every distinct matter number and category the text mentions."""
    t = _clean(text)
    low = t.lower()
    matters = list(dict.fromkeys(f"M{m.group(1)}" for m in MATTER_IN_TEXT.finditer(t)))
    cats = [canon for canon, keys in ALIASES.items() if any(k in low for k in keys)]
    return matters, cats


def _normalise_matter(value):
    if not value:
        return None
    m = MATTER_IN_TEXT.search(str(value))
    if m:
        return f"M{m.group(1)}"
    v = str(value).strip().upper().replace(" ", "").replace("-", "")
    return v if MATTER_RE.match(v) else None


def _normalise_category(value):
    if not value:
        return None
    v = str(value).strip().lower()
    for canon, keys in ALIASES.items():
        if v == canon.lower() or any(k in v for k in keys):
            return canon
    return None


def _clean(text):
    """Normalise whitespace so HTML-sourced bodies parse like plain ones.

    A category name split by a non-breaking space ("Other&nbsp;Documents")
    would otherwise fail to match any alias.
    """
    t = (text or "").replace("&nbsp;", " ").replace("\xa0", " ")
    return re.sub(r"[ \t]+", " ", t)


def fallback_parse(text):
    """Regex/keyword extraction. Used when Gemini is unavailable."""
    text = _clean(text)
    low = text.lower()

    matter = None
    m = MATTER_IN_TEXT.search(text or "")
    if m:
        matter = f"M{m.group(1)}"

    # Pick the alias that appears earliest; within a tie prefer the longest,
    # so "other documents" beats a bare "documents"-style partial.
    best = None
    for canon, keys in ALIASES.items():
        for k in keys:
            i = low.find(k)
            if i >= 0 and (best is None or (i, -len(k)) < (best[0], -len(best[1]))):
                best = (i, k, canon)
    return matter, (best[2] if best else None)


def gemini_enabled(api_key=None):
    """Whether this run should actually call the Gemini API."""
    mode = os.environ.get("GEMINI_MODE", DEFAULT_MODE).strip().lower()
    key = api_key or os.environ.get("GEMINI_API_KEY")
    if mode == "mock":
        return False
    if mode == "live":
        return True
    return bool(key)  # auto


def gemini_parse(text, api_key=None):
    """Structured extraction via Gemini. Returns (matter, category) or raises."""
    key = api_key or os.environ.get("GEMINI_API_KEY")
    if not key:
        raise RuntimeError("no GEMINI_API_KEY configured")

    from google import genai
    from google.genai import types

    client = genai.Client(api_key=key)
    resp = client.models.generate_content(
        model=os.environ.get("GEMINI_MODEL", "gemini-2.0-flash"),
        contents=PROMPT.format(text=(text or "")[:4000]),
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=SCHEMA,
            temperature=0,
        ),
    )
    data = json.loads(resp.text)
    return (data.get("matter_number"), data.get("document_type"),
            bool(data.get("ambiguous")))


def parse_request(text, api_key=None):
    """Best-effort parse of an email body. Returns (matter, category).

    Tries Gemini, falls back to regex, and raises ParseError with a
    human-readable reason if either field cannot be resolved.
    """
    text = strip_quoted(text)
    matter = category = None
    resolved_by_gemini = False

    if gemini_enabled(api_key):
        try:
            matter, category, ambiguous = gemini_parse(text, api_key)
            if ambiguous:
                raise AmbiguousRequest(*find_candidates(text))
            resolved_by_gemini = True
        except AmbiguousRequest:
            raise
        except Exception:
            matter = category = None  # fall through to the deterministic path

    matter = _normalise_matter(matter)
    category = _normalise_category(category)

    if not matter or not category:
        # Gemini can reason past a type that is merely mentioned (a negation, a
        # quoted thread). The deterministic parser cannot, so when it is doing
        # the work, refuse to guess between genuinely competing options.
        if not resolved_by_gemini:
            matters, cats = find_candidates(text)
            if len(matters) > 1 or len(cats) > 1:
                raise AmbiguousRequest(matters, cats)
        fb_matter, fb_category = fallback_parse(text)
        matter = matter or fb_matter
        category = category or fb_category

    missing = []
    if not matter:
        missing.append("a matter number (the letter M followed by 5 digits, e.g. M12205)")
    if not category:
        missing.append("a document type (one of: " + ", ".join(CATEGORIES) + ")")
    if missing:
        raise ParseError("could not find " + " and ".join(missing))

    return matter, category
