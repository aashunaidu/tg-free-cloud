import zipfile, datetime as dt, os
from pathlib import Path
from ..paths import BACKUP_DIR, WORK_DIR, CLOUD_DIR
from ..utils import make_sig
from .models import FileMeta

def create_zip_backup(meta, refresh_table_cb=None):
    now = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    name = f"tgcloud_backup_{now}.zip"
    dest = BACKUP_DIR / name
    with zipfile.ZipFile(dest, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        for root, _, files in os.walk(CLOUD_DIR):
            for fn in files:
                fp = Path(root) / fn
                rel = fp.relative_to(CLOUD_DIR)
                zf.write(fp, arcname=str(rel))
    key = str(dest.relative_to(WORK_DIR))
    fm = FileMeta(size=dest.stat().st_size, mtime=dest.stat().st_mtime, status="pending",
                  sig=make_sig(dest.stat().st_size, dest.stat().st_mtime))
    meta.files[key] = fm
    if refresh_table_cb:
        refresh_table_cb()
    return dest
