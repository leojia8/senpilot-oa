"""Deterministic reply templates. Gemini interprets requests; it does not write replies.

The opening reads as prose, the way a colleague would summarise a matter, with the
detail broken out below so the whole thing is scannable rather than a wall of text.
"""
import re
import textwrap

from scraper import CATEGORIES

MONTHS = ["January", "February", "March", "April", "May", "June", "July",
          "August", "September", "October", "November", "December"]

WIDTH = 74


def _date(value):
    """04/07/2025 -> April 7, 2025. Left alone if it is not a date."""
    m = re.match(r"^(\d{1,2})/(\d{1,2})/(\d{4})$", (value or "").strip())
    if not m:
        return value
    mm, dd, yyyy = (int(x) for x in m.groups())
    if not 1 <= mm <= 12:
        return value
    return f"{MONTHS[mm - 1]} {dd}, {yyyy}"


def _singular(category):
    return category[:-1] if category.endswith("s") else category


def _quantity(category, n):
    return f"{n} {category if n != 1 else _singular(category)}"


def _join(parts):
    if not parts:
        return ""
    if len(parts) == 1:
        return parts[0]
    return ", ".join(parts[:-1]) + (" and " if len(parts) == 2 else ", and ") + parts[-1]


def counts_sentence(counts):
    """"13 Exhibits, 6 Key Documents, 43 Other Documents, and no Transcripts or
    Recordings." """
    present = [_quantity(c, counts[c]) for c in CATEGORIES if counts.get(c)]
    absent = [c for c in CATEGORIES if not counts.get(c)]
    if not present:
        return "no documents in any category"
    if absent:
        none_of = absent[0] if len(absent) == 1 else (
            " or ".join(absent) if len(absent) == 2
            else ", ".join(absent[:-1]) + " or " + absent[-1])
        return _join(present + [f"no {none_of}"])
    return _join(present)


def _wrap(text):
    return textwrap.fill(text, width=WIDTH)


def summary_paragraph(info):
    """Prose description of the matter."""
    matter = info.get("matter_no") or "This matter"
    title = (info.get("title") or "").strip()
    # "is about the Halifax Regional Water Commission ..." reads better than
    # dropping straight into the title, unless it already opens with an article.
    article = "" if re.match(r"^(the|a|an)\b", title, re.IGNORECASE) else "the "
    bits = [f"{matter} is about {article}{title}." if title
            else f"{matter} is a matter before the board."]

    cat, industry = info.get("category"), info.get("industry")
    if cat and industry:
        bits.append(f"It relates to {cat} within the {industry} category.")
    elif cat or industry:
        bits.append(f"It relates to {cat or industry}.")

    received, decision = _date(info.get("date_received")), _date(info.get("decision_date"))
    if received and decision:
        bits.append(f"The matter had an initial filing on {received} "
                    f"and a final filing on {decision}.")
    elif received:
        bits.append(f"The matter had an initial filing on {received}.")

    status = info.get("status")
    if status:
        bits.append(f"It is currently {status.lower()}.")
    if info.get("outcome"):
        bits.append(f"The outcome was {info['outcome']}.")
    return _wrap(" ".join(bits))


def _manifest(documents, zip_name, omitted=()):
    lines = [f"DOCUMENTS ATTACHED  ({zip_name})" if zip_name else "DOCUMENTS RETRIEVED"]
    width = max((len(str(d.get("doc_no") or "")) for d in documents), default=0)
    left_out = {str(d["path"]) for d in omitted}
    for d in documents:
        if str(d["path"]) in left_out:
            continue
        ident = str(d.get("doc_no") or "")
        title = (d.get("title") or d["path"].name).strip()
        if len(title) > WIDTH - width - 6:
            title = title[:WIDTH - width - 9] + "..."
        lines.append(f"   {ident:<{width}}  {title}")
    for d in omitted:
        ident = str(d.get("doc_no") or "")
        lines.append(f"   {ident:<{width}}  (too large to attach)")
    return lines


def _counts_block(counts, requested):
    lines = ["DOCUMENT COUNTS"]
    for cat in CATEGORIES:
        n = counts.get(cat)
        mark = "   <- requested" if cat == requested else ""
        lines.append(f"   {cat:<18}{'?' if n is None else n:>4}{mark}")
    return lines


def format_reply(info, category, documents, failed=(), zip_name=None, max_docs=10,
                 omitted=()):
    """Build (subject, body) for a successful lookup.

    documents is the list of {path, doc_no, title} that were downloaded, and
    omitted those left out of the archive because it would have been too large.
    """
    counts = info.get("counts", {})
    available = counts.get(category) or 0
    matter = info.get("matter_no") or "?"
    n = len(documents)

    subject = f"{matter} - {category} ({n} document{'s' if n != 1 else ''})"

    body = ["Hi,", "", summary_paragraph(info), ""]
    body.append(_wrap(f"I found {counts_sentence(counts)}."))
    body.append("")

    if available == 0:
        body.append(_wrap(f"This matter has no {category}, so there is nothing to attach."))
    else:
        attached = n - len(omitted)
        sent = f"I downloaded {_quantity(category, n)}"
        if n < available:
            sent += f" out of the {available}"
        if omitted:
            sent += (f" and attached {attached} of them as a ZIP. The remaining "
                     f"{len(omitted)} {'was' if len(omitted) == 1 else 'were'} "
                     "too large to send by email.")
        elif zip_name:
            sent += f" and attached {'it' if n == 1 else 'them'} as a ZIP."
        else:
            sent += "."
        body.append(_wrap(sent))
        if failed:
            body.append("")
            body.append(_wrap(
                f"{len(failed)} document(s) could not be retrieved: "
                f"{', '.join(map(str, failed))}."))

    if documents:
        body += [""] + _manifest(documents, zip_name, omitted)
    body += [""] + _counts_block(counts, category)
    body += ["", "-- ", "NSUARB document agent"]
    return subject, "\n".join(body)


def format_ambiguous(matters, categories, original_subject=""):
    """Ask which of several matters or document types was meant."""
    subject = "Which did you mean?"
    if original_subject:
        subject += f" - re: {original_subject[:60]}"

    body = ["Hi,", ""]
    if len(matters) > 1 and len(categories) > 1:
        body.append(_wrap(
            "Your request mentions more than one matter and more than one document "
            "type, so I did not want to guess."))
    elif len(matters) > 1:
        body.append(_wrap("Your request mentions more than one matter number, "
                          "so I did not want to guess."))
    else:
        body.append(_wrap("Your request mentions more than one document type, "
                          "so I did not want to guess."))
    body.append("")

    if len(matters) > 1:
        body += ["Matters you mentioned:"] + [f"   {m}" for m in matters] + [""]
    if len(categories) > 1:
        body += ["Document types you mentioned:"] + [f"   {c}" for c in categories] + [""]

    example_matter = matters[0] if matters else "M12205"
    example_category = categories[0] if categories else "Other Documents"
    body.append(_wrap("Reply with a single matter and a single document type and "
                      "I will fetch them, for example:"))
    body += ["", f'   "Can you send me {example_category} from {example_matter}?"',
             "", "-- ", "NSUARB document agent"]
    return subject, "\n".join(body)


def format_error(reason, original_subject=""):
    """Build (subject, body) for a request the agent could not fulfil."""
    subject = "Could not process your request"
    if original_subject:
        subject += f" - re: {original_subject[:60]}"
    body = "\n".join([
        "Hi,",
        "",
        _wrap(f"Sorry - I could not process that request, because {reason}."),
        "",
        _wrap("Please include a matter number (the letter M followed by 5 digits, "
              "e.g. M12205) and one of these document types:"),
        "",
        *[f"   {c}" for c in CATEGORIES],
        "",
        'For example: "Can you give me Other Documents files from M12205?"',
        "",
        "-- ",
        "NSUARB document agent",
    ])
    return subject, body
