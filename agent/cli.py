"""Terminal entry point: matter + category -> metadata, downloads, ZIP.

    python agent/cli.py M12205 "Other Documents"
    python agent/cli.py M12205 "Other Documents" --dry-run   # metadata only
"""
import sys
import tempfile
from pathlib import Path

from packager import make_zip, verify_zip
from reply import format_reply
from scraper import CATEGORIES, MatterNotFound, fetch

MAX_DOCS = 10


def run(matter, category, max_docs=MAX_DOCS, headless=True, outdir="output",
        dry_run=False):
    tmp = Path(tempfile.mkdtemp(prefix=f"{matter}_"))
    info, files, failed = fetch(matter, category, tmp, 0 if dry_run else max_docs,
                                headless)

    print(f"\n=== {info['matter_no']} ===")
    print(f"  title        : {info['title']}")
    print(f"  industry     : {info['industry']}")
    print(f"  category     : {info['category']}")
    print(f"  status       : {info['status']}")
    print(f"  received     : {info['date_received']}")
    print(f"  decision     : {info['decision_date']}")
    print(f"  outcome      : {info['outcome']}")
    print("  counts       :")
    for c in CATEGORIES:
        print(f"      {c:<16} {info['counts'][c]}")

    print(f"\n  requested    : {category}")
    if dry_run:
        print("  dry run      : no documents downloaded")
        return info, [], []
    print(f"  downloaded   : {len(files)}  failed: {len(failed)}")

    zip_path = None
    if files:
        zip_path = Path(outdir) / f"{info['matter_no']}_{category.replace(' ', '_')}.zip"
        make_zip([f["path"] for f in files], zip_path)
        names = verify_zip(zip_path, files)
        print(f"  zip          : {zip_path} ({zip_path.stat().st_size:,} bytes)")
        print(f"  verified     : {len(names)} entries")

    _, body = format_reply(info, category, files, failed,
                           zip_path.name if zip_path else None, max_docs)
    print("\n" + "-" * 74)
    print(body)
    return info, files, failed


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    matter = args[0] if args else "M12205"
    category = args[1] if len(args) > 1 else "Other Documents"
    try:
        run(matter, category, dry_run="--dry-run" in sys.argv)
    except (ValueError, MatterNotFound) as e:
        print(f"error: {e}")
        sys.exit(1)
