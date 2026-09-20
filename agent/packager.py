"""ZIP packaging for downloaded documents."""
import zipfile
from pathlib import Path


def make_zip(files, zip_path):
    """Zip the given files (flat, no directory structure). Returns the path."""
    zip_path = Path(zip_path)
    zip_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for f in files:
            zf.write(f, Path(f).name)
    return zip_path


def make_zip_within(files, zip_path, max_bytes):
    """Zip as many files as fit under max_bytes, preserving order.

    Returns (zip_path, included, omitted). PDFs are already compressed, so raw
    size is a good first estimate; the archive is rebuilt without its largest
    member if the estimate turns out optimistic.

    files may be paths or {path: ...} records; omitted mirrors the input type.
    """
    def path_of(f):
        return Path(f["path"] if isinstance(f, dict) else f)

    included, omitted, total = [], [], 0
    budget = max_bytes * 0.95  # leave room for zip overhead
    for f in files:
        size = path_of(f).stat().st_size
        if total + size <= budget:
            included.append(f)
            total += size
        else:
            omitted.append(f)

    while included:
        make_zip([path_of(f) for f in included], zip_path)
        if Path(zip_path).stat().st_size <= max_bytes:
            break
        largest = max(included, key=lambda f: path_of(f).stat().st_size)
        included.remove(largest)
        omitted.append(largest)

    if not included:
        Path(zip_path).unlink(missing_ok=True)
        return None, [], omitted
    return Path(zip_path), included, omitted


def verify_zip(zip_path, expected_files):
    """Check the archive lists exactly the expected filenames and is readable."""
    def path_of(f):
        return Path(f["path"] if isinstance(f, dict) else f)

    with zipfile.ZipFile(zip_path) as zf:
        if zf.testzip() is not None:
            raise RuntimeError("corrupt entry in archive")
        names = sorted(zf.namelist())
    expected = sorted(path_of(f).name for f in expected_files)
    if names != expected:
        raise RuntimeError(f"zip contents mismatch: {names} != {expected}")
    return names
