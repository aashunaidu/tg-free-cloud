import os
from pathlib import Path
from ..paths import CLOUD_DIR, METADATA_FILE
from ..utils import sha256_of, make_sig, IGNORED_SUFFIXES
from .models import FileMeta

def initial_scan_and_enqueue(cfg: dict, meta, pool):
    for root, _, files in os.walk(CLOUD_DIR):
        for fn in files:
            fp = Path(root) / fn
            try:
                rel = str(fp.relative_to(CLOUD_DIR))
            except Exception:
                continue
            if fp.suffix.lower() in IGNORED_SUFFIXES or fp.name.startswith("~$"):
                continue
            size = fp.stat().st_size
            mtime = fp.stat().st_mtime
            sha = sha256_of(fp) if cfg.get("use_sha256") else None
            sig_now = make_sig(size, mtime, sha)
            fm = meta.files.get(rel)
            if fm is None:
                meta.files[rel] = FileMeta(size=size, mtime=mtime, sha256=sha, status="pending", sig=sig_now)
                pool.enqueue(fp)
            else:
                if fm.sig != sig_now or fm.status != "uploaded":
                    fm.size = size; fm.mtime = mtime; fm.sha256 = sha; fm.sig = sig_now; fm.status = "pending"
                    pool.enqueue(fp)
    meta.save(METADATA_FILE)
