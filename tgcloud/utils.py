import time, hashlib
from pathlib import Path

IGNORED_SUFFIXES = {".tmp", ".crdownload", ".part", ".partial"}

def human_size(num_bytes: int) -> str:
    units = ["B", "KB", "MB", "GB", "TB"]
    size = float(num_bytes)
    for unit in units:
        if size < 1024.0:
            return f"{size:.2f} {unit}"
        size /= 1024.0
    return f"{size:.2f} PB"

def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()

def make_sig(size: int, mtime: float, sha: str | None = None) -> str:
    return f"{size}:{int(mtime)}:{sha or ''}"

def wait_for_file_readable(path: Path, timeout: float = 120.0, check_interval: float = 0.5, stable_checks: int = 3) -> bool:
    deadline = time.time() + timeout
    last_size = None
    stable = 0
    while time.time() < deadline:
        try:
            size = path.stat().st_size
            with open(path, "rb"): pass
            if last_size is not None and size == last_size:
                stable += 1
            else:
                stable = 0
            last_size = size
            if stable >= stable_checks:
                return True
        except (PermissionError, FileNotFoundError, OSError):
            pass
        time.sleep(check_interval)
    return False
