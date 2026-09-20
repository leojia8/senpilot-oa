"""Deterministic reply templates. Gemini interprets requests; it does not write replies."""
from scraper import CATEGORIES


def _line(label, value):
    return f"  {label:<16}{value if value not in (None, '') else '-'}"


def format_reply(info, category, downloaded, failed=(), zip_name=None, max_docs=10,
                 oversize=None):
    """Build (subject, body) for a successful lookup."""
    counts = info.get("counts", {})
    available = counts.get(category) or 0
    matter = info.get("matter_no") or "?"

    subject = f"{matter} - {category} ({downloaded} document{'s' if downloaded != 1 else ''})"

    lines = [
        f"Here are the {category} you requested from matter {matter}.",
        "",
        "MATTER SUMMARY",
        _line("Matter", matter),
        _line("Title", info.get("title")),
        _line("Industry", info.get("industry")),
        _line("Type", info.get("category")),
        _line("Status", info.get("status")),
        _line("Date received", info.get("date_received")),
        _line("Decision date", info.get("decision_date")),
        _line("Outcome", info.get("outcome")),
        "",
        "DOCUMENT COUNTS",
    ]
    for cat in CATEGORIES:
        n = counts.get(cat)
        mark = "  <- requested" if cat == category else ""
        lines.append(f"  {cat:<18}{'?' if n is None else n}{mark}")

    lines += ["", "THIS REQUEST"]
    if available == 0:
        lines.append(f"  This matter has no {category}, so there is nothing to attach.")
    else:
        lines.append(_line("Available", available))
        lines.append(_line("Downloaded", f"{downloaded} (limit {max_docs} per request)"))
        if failed:
            lines.append(_line("Failed", f"{len(failed)} ({', '.join(map(str, failed))})"))
        if zip_name:
            lines.append(_line("Attached", zip_name))
        if oversize:
            mb = oversize / (1024 * 1024)
            lines += [
                "",
                f"  The archive for these documents is {mb:.1f} MB, which exceeds what",
                "  email can carry, so it is not attached. Some filings on this matter",
                "  are very large. Requesting a different document type, or retrieving",
                "  these few documents directly from the board's site, will work better.",
            ]

    lines += ["", "-- ", "NSUARB document agent"]
    return subject, "\n".join(lines)


def format_error(reason, original_subject=""):
    """Build (subject, body) for a request the agent could not fulfil."""
    subject = "Could not process your request"
    if original_subject:
        subject += f" - re: {original_subject[:60]}"
    body = "\n".join([
        "Sorry - I could not process that request.",
        "",
        f"Reason: {reason}",
        "",
        "Please include a matter number (the letter M followed by 5 digits, "
        "e.g. M12205) and one of these document types:",
        *[f"  - {c}" for c in CATEGORIES],
        "",
        'Example: "Can you give me Other Documents files from M12205?"',
        "",
        "-- ",
        "NSUARB document agent",
    ])
    return subject, body
