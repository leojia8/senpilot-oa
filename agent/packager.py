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


def verify_zip(zip_path, expected_files):
    """Check the archive lists exactly the expected filenames and is readable."""
    with zipfile.ZipFile(zip_path) as zf:
        if zf.testzip() is not None:
            raise RuntimeError("corrupt entry in archive")
        names = sorted(zf.namelist())
    expected = sorted(Path(f).name for f in expected_files)
    if names != expected:
        raise RuntimeError(f"zip contents mismatch: {names} != {expected}")
    return names
