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

If either value is missing or ambiguous, return null for it. Do not guess.

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
    },
    "required": ["matter_number", "document_type"],
}


class ParseError(Exception):
    """The email did not contain a usable request."""


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


def fallback_parse(text):
    """Regex/keyword extraction. Used when Gemini is unavailable."""
    low = (text or "").lower()

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
    return data.get("matter_number"), data.get("document_type")


def parse_request(text, api_key=None):
    """Best-effort parse of an email body. Returns (matter, category).

    Tries Gemini, falls back to regex, and raises ParseError with a
    human-readable reason if either field cannot be resolved.
    """
    matter = category = None
    if gemini_enabled(api_key):
        try:
            matter, category = gemini_parse(text, api_key)
        except Exception:
            pass  # fall through to the deterministic path

    matter = _normalise_matter(matter)
    category = _normalise_category(category)

    if not matter or not category:
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
