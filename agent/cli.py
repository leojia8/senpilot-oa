"""Terminal entry point: matter + category -> metadata, downloads, ZIP."""
import sys
import tempfile
from pathlib import Path

from scraper import CATEGORIES, MatterNotFound, fetch
from packager import make_zip, verify_zip


def run(matter, category, max_docs=10, headless=True, outdir="output"):
    tmp = Path(tempfile.mkdtemp(prefix=f"{matter}_"))
    info, files, failed = fetch(matter, category, tmp, max_docs, headless)

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
    print(f"  downloaded   : {len(files)}  failed: {len(failed)}")

    if files:
        zip_path = Path(outdir) / f"{info['matter_no']}_{category.replace(' ', '_')}.zip"
        make_zip(files, zip_path)
        names = verify_zip(zip_path, files)
        print(f"  zip          : {zip_path} ({zip_path.stat().st_size:,} bytes)")
        print(f"  verified     : {len(names)} entries")
    return info, files, failed


if __name__ == "__main__":
    matter = sys.argv[1] if len(sys.argv) > 1 else "M12205"
    category = sys.argv[2] if len(sys.argv) > 2 else "Other Documents"
    try:
        run(matter, category)
    except (ValueError, MatterNotFound) as e:
        print(f"error: {e}")
        sys.exit(1)
