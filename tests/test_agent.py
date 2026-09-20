"""Offline test suite. No network, no Gmail, no browser.

Every bug found during development has a regression test here, marked REGRESSION.

    .venv/Scripts/python.exe -m pytest tests -q
"""
import os
import sys
import zipfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "agent"))
os.environ["GEMINI_MODE"] = "mock"  # never call the API from tests

import mailer  # noqa: E402
import packager  # noqa: E402
import parser as reqparser  # noqa: E402
import reply as replymod  # noqa: E402
import scraper  # noqa: E402

INFO = {
    "matter_no": "M12205", "status": "Open", "industry": "Water",
    "category": "Capital Expenditure Approvals",
    "date_received": "04/07/2025", "decision_date": "10/23/2025", "outcome": None,
    "title": "Halifax Regional Water Commission - Windsor Street Exchange "
             "Redevelopment Project - $69,275,000",
    "counts": {"Exhibits": 13, "Key Documents": 6, "Other Documents": 43,
               "Transcripts": 0, "Recordings": 0},
}


def docs(tmp_path, n=3, size=100, prefix="1026"):
    out = []
    for i in range(n):
        p = tmp_path / f"{prefix}{i:02d}.pdf"
        p.write_bytes(b"%PDF-1.6" + os.urandom(size))
        out.append({"path": p, "doc_no": f"{prefix}{i:02d}", "title": f"Document {i}"})
    return out


# --------------------------------------------------------------- matter numbers
@pytest.mark.parametrize("raw,expected", [
    ("M12205", "M12205"), ("m12205", "M12205"), ("  M12205  ", "M12205"),
])
def test_validate_matter_accepts(raw, expected):
    assert scraper.validate_matter(raw) == expected


@pytest.mark.parametrize("bad", ["12205", "M1220", "M123456", "MABCDE", "", None, "M-1220"])
def test_validate_matter_rejects(bad):
    with pytest.raises(ValueError):
        scraper.validate_matter(bad)


# ---------------------------------------------------------------------- parsing
@pytest.mark.parametrize("text,matter,category", [
    ("Hi Agent, Can you give me Other Documents files from M12205? Thanks!",
     "M12205", "Other Documents"),
    ("please send exhibits for m12383", "M12383", "Exhibits"),
    ("I need the key documents from Matter M 12205 please", "M12205", "Key Documents"),
    ("Could I get recordings for M-12383?", "M12383", "Recordings"),
    ("transcripts M12205", "M12205", "Transcripts"),
    ("Send me the OTHER DOCS for m12205", "M12205", "Other Documents"),
    ("hey can you grab audio from M12383", "M12383", "Recordings"),
    ("SEND ME THE KEY DOCUMENTS FOR M12383 NOW", "M12383", "Key Documents"),
    ("hi can I get the exhibits for M12383 please - thanks", "M12383", "Exhibits"),
    ("Matter: M12205\nType: Other Documents", "M12205", "Other Documents"),
])
def test_parse_ordinary_requests(text, matter, category):
    assert reqparser.parse_request(text) == (matter, category)


@pytest.mark.parametrize("text", ["", "   \n\t ", "just saying hi", "give me everything"])
def test_parse_rejects_unusable(text):
    with pytest.raises(reqparser.ParseError):
        reqparser.parse_request(text)


def test_parse_rejects_missing_category():
    with pytest.raises(reqparser.ParseError):
        reqparser.parse_request("please send files for M12205")


def test_parse_handles_very_long_body():
    assert reqparser.parse_request("blah " * 2000 + " exhibits for M12383") == (
        "M12383", "Exhibits")


def test_parse_nbsp_category():
    """REGRESSION: 'Other&nbsp;Documents' from an HTML email matched nothing."""
    assert reqparser.parse_request("Other\xa0Documents for M12205") == (
        "M12205", "Other Documents")
    assert reqparser.parse_request("Other&nbsp;Documents for M12205") == (
        "M12205", "Other Documents")


# ------------------------------------------------------------------- ambiguity
def test_ambiguous_two_matters():
    with pytest.raises(reqparser.AmbiguousRequest) as e:
        reqparser.parse_request("Send exhibits for M12205 and M12383")
    assert e.value.matters == ["M12205", "M12383"]


def test_ambiguous_two_categories():
    with pytest.raises(reqparser.AmbiguousRequest) as e:
        reqparser.parse_request("I need exhibits and transcripts from M12205")
    assert set(e.value.categories) == {"Exhibits", "Transcripts"}


def test_quoted_reply_chain_not_ambiguous():
    """REGRESSION: a quoted thread naming another matter must not confuse us."""
    text = ("Exhibits for M12383 please\n\n"
            "> On Mon, someone wrote:\n> here are Other Documents from M12205")
    assert reqparser.parse_request(text) == ("M12383", "Exhibits")


def test_signature_stripped():
    """REGRESSION: a signature naming other matter numbers must be ignored."""
    text = "Key Documents M12383\n\n-- \nJane Doe | Matter M99999 | ext 12205"
    assert reqparser.parse_request(text) == ("M12383", "Key Documents")


def test_strip_quoted_removes_reply_markers():
    assert "M12205" not in reqparser.strip_quoted(
        "Exhibits M12383\nFrom: someone\nOther Documents M12205")


def test_find_candidates_dedupes():
    matters, cats = reqparser.find_candidates("M12205 M12205 exhibits exhibits")
    assert matters == ["M12205"] and cats == ["Exhibits"]


# ------------------------------------------------------------- gemini mode gate
@pytest.mark.parametrize("mode,key,expected", [
    ("mock", "k", False), ("mock", "", False),
    ("live", "k", True), ("auto", "k", True), ("auto", "", False),
])
def test_gemini_mode_gating(mode, key, expected, monkeypatch):
    """REGRESSION: mock mode must not call the API even when a key is present."""
    monkeypatch.setenv("GEMINI_MODE", mode)
    monkeypatch.setenv("GEMINI_API_KEY", key)
    assert reqparser.gemini_enabled() is expected


def test_mock_mode_never_calls_api(monkeypatch):
    monkeypatch.setenv("GEMINI_MODE", "mock")
    monkeypatch.setenv("GEMINI_API_KEY", "would-fail-if-used")

    def boom(*a, **k):
        raise AssertionError("Gemini was called in mock mode")

    monkeypatch.setattr(reqparser, "gemini_parse", boom)
    assert reqparser.parse_request("exhibits for M12205") == ("M12205", "Exhibits")


# ------------------------------------------------------------------ reply text
def test_dates_rendered_as_prose():
    assert replymod._date("04/07/2025") == "April 7, 2025"
    assert replymod._date("10/23/2025") == "October 23, 2025"
    assert replymod._date("not a date") == "not a date"
    assert replymod._date("13/01/2025") == "13/01/2025"  # invalid month, left alone


def test_counts_sentence_matches_requested_phrasing():
    s = replymod.counts_sentence(INFO["counts"])
    assert s == ("13 Exhibits, 6 Key Documents, 43 Other Documents, "
                 "and no Transcripts or Recordings")


def test_counts_sentence_singular():
    assert "1 Recording" in replymod.counts_sentence(
        {"Exhibits": 0, "Key Documents": 0, "Other Documents": 0,
         "Transcripts": 0, "Recordings": 1})


def test_counts_sentence_all_zero():
    assert replymod.counts_sentence({c: 0 for c in scraper.CATEGORIES}) == (
        "no documents in any category")


def test_summary_paragraph_reads_as_prose():
    import re as _re
    # The paragraph is hard-wrapped, so compare against unwrapped text.
    p = _re.sub(r"\s+", " ", replymod.summary_paragraph(INFO))
    assert p.startswith("M12205 is about the Halifax Regional Water Commission")
    assert "within the Water category" in p
    assert "initial filing on April 7, 2025" in p
    assert "final filing on October 23, 2025" in p
    assert "It is currently open." in p


def test_reply_contains_all_five_counts_and_manifest(tmp_path):
    d = docs(tmp_path, 3)
    subject, body = replymod.format_reply(INFO, "Other Documents", d, [], "a.zip")
    assert subject == "M12205 - Other Documents (3 documents)"
    for cat in scraper.CATEGORIES:
        assert cat in body
    assert "<- requested" in body
    for doc in d:
        assert doc["doc_no"] in body and doc["title"] in body
    assert "I downloaded 3 Other Documents out of the 43" in body


def test_reply_no_documents_available():
    info = dict(INFO, counts=dict(INFO["counts"], Recordings=0))
    _, body = replymod.format_reply(info, "Recordings", [], [])
    assert "no Recordings, so there is nothing to attach" in body


def test_reply_mentions_failures(tmp_path):
    _, body = replymod.format_reply(INFO, "Other Documents", docs(tmp_path, 1), ["102197"])
    assert "could not be retrieved" in body and "102197" in body


def test_reply_mentions_omitted(tmp_path):
    d = docs(tmp_path, 3)
    _, body = replymod.format_reply(INFO, "Other Documents", d, [], "a.zip",
                                    omitted=d[2:])
    assert "too large to send by email" in body
    assert "(too large to attach)" in body


def test_omitted_documents_listed_once(tmp_path):
    """REGRESSION: omitted files appeared both as attached and as omitted."""
    d = docs(tmp_path, 4)
    _, body = replymod.format_reply(INFO, "Other Documents", d, [], "a.zip",
                                    omitted=d[2:])
    for left_out in d[2:]:
        assert body.count(left_out["doc_no"]) == 1
    for kept in d[:2]:
        assert kept["title"] in body


def test_reply_lines_are_readable():
    """The body must not be one long unreadable block."""
    _, body = replymod.format_reply(INFO, "Other Documents", [], [])
    assert all(len(line) <= 80 for line in body.splitlines())
    assert body.count("\n\n") >= 3  # paragraph breaks


def test_ambiguous_reply_lists_options():
    _, body = replymod.format_ambiguous(["M12205", "M12383"], ["Exhibits"], "docs")
    assert "M12205" in body and "M12383" in body
    assert "more than one matter" in body


def test_error_reply_lists_categories():
    _, body = replymod.format_error("could not find a matter number", "hi")
    for cat in scraper.CATEGORIES:
        assert cat in body


# ------------------------------------------------------------------- packaging
def test_zip_roundtrip_and_verify(tmp_path):
    d = docs(tmp_path, 4)
    z = packager.make_zip([x["path"] for x in d], tmp_path / "o.zip")
    assert sorted(packager.verify_zip(z, d)) == sorted(x["path"].name for x in d)


def test_verify_zip_detects_mismatch(tmp_path):
    d = docs(tmp_path, 3)
    z = packager.make_zip([x["path"] for x in d[:2]], tmp_path / "o.zip")
    with pytest.raises(RuntimeError):
        packager.verify_zip(z, d)


def test_zip_within_budget_splits(tmp_path):
    """REGRESSION: an oversize archive used to fail the send and retry forever."""
    d = docs(tmp_path, 5, size=1000)
    limit = 2600  # room for roughly two members
    z, included, omitted = packager.make_zip_within(d, tmp_path / "o.zip", limit)
    assert z is not None
    assert z.stat().st_size <= limit
    assert len(included) + len(omitted) == 5
    assert included and omitted
    packager.verify_zip(z, included)


def test_zip_within_budget_keeps_all_when_small(tmp_path):
    d = docs(tmp_path, 3, size=50)
    z, included, omitted = packager.make_zip_within(d, tmp_path / "o.zip", 10_000_000)
    assert omitted == [] and len(included) == 3


def test_zip_within_budget_when_nothing_fits(tmp_path):
    d = docs(tmp_path, 2, size=5000)
    z, included, omitted = packager.make_zip_within(d, tmp_path / "o.zip", 100)
    assert z is None and included == [] and len(omitted) == 2


def test_zip_preserves_content(tmp_path):
    d = docs(tmp_path, 2)
    original = {x["path"].name: x["path"].read_bytes() for x in d}
    z = packager.make_zip([x["path"] for x in d], tmp_path / "o.zip")
    with zipfile.ZipFile(z) as zf:
        for name, data in original.items():
            assert zf.read(name) == data


# ----------------------------------------------------------------------- mail
@pytest.mark.parametrize("addr,expected", [
    ("mailer-daemon@googlemail.com", True), ("no-reply@google.com", True),
    ("noreply@x.com", True), ("notifications@github.com", True),
    ("do-not-reply@x.com", True),
    ("leo8.jia@gmail.com", False), ("reviewer@senpilot.com", False),
])
def test_automated_sender_detection(addr, expected):
    """REGRESSION: replying to a bounce invites a mail loop."""
    assert mailer.is_automated(addr) is expected


def test_spam_detection():
    assert mailer.is_spam({"labels": ["SPAM", "UNREAD"]}) is True
    assert mailer.is_spam({"labels": ["INBOX"]}) is False
    assert mailer.is_spam({}) is False


def test_extract_body_prefers_plain_text():
    import base64

    def enc(s):
        return base64.urlsafe_b64encode(s.encode()).decode()

    payload = {"mimeType": "multipart/alternative", "body": {}, "parts": [
        {"mimeType": "text/plain", "body": {"data": enc("Other Documents M12205")},
         "parts": []},
        {"mimeType": "text/html", "body": {"data": enc("<p>ignored</p>")}, "parts": []}]}
    assert mailer._extract_body(payload) == "Other Documents M12205"


def test_extract_body_html_fallback_strips_nbsp():
    """REGRESSION: HTML bodies leaked \\xa0 into the parser."""
    import base64
    html = "<div>Exhibits for <b>M12383</b>&nbsp;please</div>"
    payload = {"mimeType": "multipart/mixed", "body": {}, "parts": [
        {"mimeType": "text/html",
         "body": {"data": base64.urlsafe_b64encode(html.encode()).decode()},
         "parts": []}]}
    body = mailer._extract_body(payload)
    assert "\xa0" not in body
    assert reqparser.parse_request(body) == ("M12383", "Exhibits")


def test_attachment_size_guard(tmp_path):
    """REGRESSION: base64 inflates ~33%, so the raw cap is below Gmail's 25MB."""
    big = tmp_path / "big.zip"
    big.write_bytes(b"x" * (mailer.MAX_ATTACHMENT_BYTES + 1))
    with pytest.raises(ValueError):
        mailer.build_message("a@b.c", "s", "b", attachment=big)


def test_build_message_threads_and_attaches(tmp_path):
    z = tmp_path / "a.zip"
    z.write_bytes(b"PK\x03\x04tiny")
    m = mailer.build_message("a@b.c", "subj", "body", attachment=z,
                             in_reply_to="<id@mail>")
    assert m["In-Reply-To"] == "<id@mail>" and m["References"] == "<id@mail>"
    names = [p.get_filename() for p in m.walk() if p.get_filename()]
    assert names == ["a.zip"]


# --------------------------------------------------------------- scraper logic
def test_unique_avoids_collisions(tmp_path):
    (tmp_path / "a.pdf").write_bytes(b"x")
    assert scraper._unique(tmp_path, "a.pdf").name == "a_2.pdf"
    (tmp_path / "a_2.pdf").write_bytes(b"x")
    assert scraper._unique(tmp_path, "a.pdf").name == "a_3.pdf"


def test_norm_tolerates_spacing_and_case():
    """REGRESSION: 'A -5' in the table becomes 'A -5.pdf'; 'H-5(c)' may re-case."""
    assert scraper._norm("A -5.pdf").startswith(scraper._norm("A -5"))
    assert scraper._norm("H-5(C)-ii.pdf").startswith(scraper._norm("H-5(c)-ii"))


def test_date_like_identifiers_recognised():
    """REGRESSION: the Recordings tab's left column is a date, not an id."""
    assert scraper.DATE_LIKE.match("10/27/2025")
    assert not scraper.DATE_LIKE.match("102674")
    assert not scraper.DATE_LIKE.match("H-4(C)")


def test_select_category_rejects_unknown():
    with pytest.raises(ValueError):
        scraper.select_category(None, "Nonsense")


def test_counts_parsed_from_page_text():
    class FakePage:
        def evaluate(self, _js):
            return ("Exhibits - 13\nKey Documents - 6\nOther Documents - 43\n"
                    "Transcripts - 0\nRecordings - 0")

    assert scraper.get_counts(FakePage()) == INFO["counts"]


def test_singular_grammar(tmp_path):
    """One document must read 'attached it', not 'attached them'."""
    _, body = replymod.format_reply(INFO, "Exhibits", docs(tmp_path, 1), [], "a.zip")
    assert "1 Exhibit out of the 13 and attached it as a ZIP" in " ".join(body.split())


def test_plural_grammar(tmp_path):
    _, body = replymod.format_reply(INFO, "Exhibits", docs(tmp_path, 3), [], "a.zip")
    assert "attached them as a ZIP" in " ".join(body.split())
