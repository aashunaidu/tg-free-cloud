import time
import threading
import queue
import datetime as dt
import logging
import hashlib
from pathlib import Path

from ..utils import sha256_of, make_sig, human_size, wait_for_file_readable, IGNORED_SUFFIXES
from ..crypto import FERNET_AVAILABLE, derive_fernet_key
from ..core.models import FileMeta
from ..paths import METADATA_FILE, CLOUD_DIR

# Don't hard-import Fernet when cryptography may not be installed.
try:
    from cryptography.fernet import Fernet  # noqa: F401
except Exception:
    Fernet = None


class UploadPool:
    def __init__(self, tg, cfg, meta):
        self.tg = tg
        self.cfg = cfg
        self.meta = meta
        self.gui = None

        self.q: "queue.Queue[Path]" = queue.Queue()
        self.stop_event = threading.Event()
        self.paused = threading.Event()
        self._last_event = {}
        self.threads = []

    # -------- logging helper (console/app.log + GUI log pane) --------
    def _log(self, msg: str):
        logging.info(msg)
        if self.gui:
            try:
                self.gui.log(msg)
            except Exception:
                pass

    def set_gui(self, gui):
        self.gui = gui

    # -------- lifecycle --------
    def start(self):
        # Ensure at least 1 worker, even if config is bad
        try:
            n = int(self.cfg.get("num_workers", 3))
        except Exception:
            n = 3
        n = max(1, n)

        self._log(f"Starting {n} uploader worker thread(s)…")
        for i in range(n):
            t = threading.Thread(target=self._worker_loop, name=f"uploader-{i+1}", daemon=True)
            self.threads.append(t)
            t.start()
            self._log(f"Started upload worker thread: {t.name}")

    def pause(self):
        self.paused.set()
        if self.gui:
            self.gui.set_paused_label(True)
        self._log("Upload paused.")

    def resume(self):
        self.paused.clear()
        if self.gui:
            self.gui.set_paused_label(False)
        self._log("Upload resumed.")

    # -------- enqueue from watcher/scan --------
    def enqueue(self, path: Path):
        if not path.exists():
            return
        if path.suffix.lower() in IGNORED_SUFFIXES or path.name.startswith("~$"):
            return

        # Safety: only handle files under CLOUD_DIR
        try:
            rel = str(path.relative_to(CLOUD_DIR))
        except Exception:
            return

        now = time.time()
        # De-bounce rapid repeat events for the same file
        if now - self._last_event.get(rel, 0) < 1.0:
            return
        self._last_event[rel] = now

        # Skip if unchanged and already uploaded
        if self._unchanged(path):
            self._log(f"Skip unchanged {rel}")
            return

        # Avoid duplicate queue entries
        try:
            if path in list(self.q.queue):
                return
        except Exception:
            pass

        self.q.put(path)
        self._log(f"Enqueued file: {rel}")

    # -------- worker loop --------
    def _worker_loop(self):
        tname = threading.current_thread().name
        self._log(f"Worker thread {tname} entering loop")
        while not self.stop_event.is_set():
            try:
                path = self.q.get(timeout=0.5)
            except queue.Empty:
                continue

            # Respect pause
            while self.paused.is_set() and not self.stop_event.is_set():
                time.sleep(0.2)

            try:
                try:
                    rel = str(path.relative_to(CLOUD_DIR))
                except Exception:
                    rel = str(path)
                self._log(f"{tname} picked: {rel}")
                self._process(path)
            except Exception as e:
                logging.exception("Error in worker while processing %s: %s", path, e)
                # Don't crash the worker; mark task done and continue
            finally:
                try:
                    self.q.task_done()
                except Exception:
                    pass

        self._log(f"Worker thread {tname} exiting")

    # -------- unchanged check --------
    def _unchanged(self, path: Path) -> bool:
        try:
            rel = str(path.relative_to(CLOUD_DIR))
        except Exception:
            return True  # outside scope, treat as unchanged/skip

        try:
            size = path.stat().st_size
            mtime = path.stat().st_mtime
        except Exception:
            return False  # if we cannot stat, let the worker try later

        sha = sha256_of(path) if self.cfg.get("use_sha256") else None
        sig_now = make_sig(size, mtime, sha)
        fm = self.meta.files.get(rel)
        return bool(fm and fm.status == "uploaded" and fm.sig == sig_now)

    # -------- core upload --------
    def _process(self, path: Path):
        try:
            rel = str(path.relative_to(CLOUD_DIR))
        except Exception:
            # Not in our sync folder
            return

        # Wait until file is stable & readable (not being written)
        if not wait_for_file_readable(path):
            self._log(f"Locked/changing, retry later: {rel}")
            time.sleep(1.5)
            self.enqueue(path)
            return

        size = path.stat().st_size
        mtime = path.stat().st_mtime
        sha = sha256_of(path) if self.cfg.get("use_sha256") else None
        sig_now = make_sig(size, mtime, sha)

        fm_prev = self.meta.files.get(rel)
        if fm_prev and fm_prev.status == "uploaded" and fm_prev.sig == sig_now:
            self._log(f"Already synced (unchanged): {rel}")
            return

        upload_path = path
        caption = rel
        temp_file = None

        # Optional encryption (only if cryptography installed AND passphrase set)
        if self.cfg.get("encryption_enabled") and FERNET_AVAILABLE and Fernet is not None:
            pp = self.cfg.get("encryption_passphrase") or ""
            if pp:
                self._log(f"Encrypting before upload: {rel}")
                salt = hashlib.sha256(rel.encode("utf-8")).digest()[:16]
                key = derive_fernet_key(pp, salt)
                f = Fernet(key)
                with open(path, "rb") as s:
                    enc = f.encrypt(s.read())
                temp_file = Path(str(path) + ".enc")
                with open(temp_file, "wb") as o:
                    o.write(enc)
                upload_path = temp_file
                caption = rel + " (enc)"
                size = len(enc)  # update size to encrypted blob
            else:
                self._log("Encryption enabled but passphrase empty. Uploading raw.")

        # Progress callback → GUI bar
        def progress_cb(sent, total, speed, eta):
            pct = int(sent * 100 / total) if total else 0
            if self.gui:
                try:
                    self.gui.update_progress(rel, pct, speed, eta)
                except Exception:
                    pass

        if self.gui:
            try:
                self.gui.set_current_upload(rel, size)
            except Exception:
                pass

        self._log(f"Uploading: {rel} ({human_size(size)})")
        via, mid, fid = self.tg.send_document(
            upload_path,
            caption,
            prefer_user=False,
            progress_cb=progress_cb
        )

        # Clean temp encrypted file
        if temp_file and temp_file.exists():
            try:
                temp_file.unlink()
            except Exception:
                pass

        if mid:
            # Success
            fm = self.meta.files.get(rel) or FileMeta(size=0, mtime=0)
            fm.size = path.stat().st_size
            fm.mtime = path.stat().st_mtime
            fm.sha256 = sha
            fm.status = "uploaded"
            fm.uploaded_at = dt.datetime.now().isoformat(timespec="seconds")
            fm.sig = make_sig(fm.size, fm.mtime, sha)
            fm.via = via
            if via == "bot":
                fm.message_id = mid
                fm.file_id = fid
            else:
                fm.user_message_id = mid

            self.meta.files[rel] = fm
            self.meta.save(METADATA_FILE)

            msg = f"Uploaded {rel} via {via} ✓ ({human_size(fm.size)})"
            self._log(msg)
            if self.gui:
                try:
                    self.gui.refresh_table()
                    self.gui.notify(f"Uploaded {rel}")
                except Exception:
                    pass
        else:
            # Failure / Skipped
            if size > self.tg.BOT_LIMIT and not bool(self.cfg.get("enable_2gb_mode", False)):
                msg = f"Skipped {rel}: {human_size(size)} > 50MB while 2GB mode is OFF."
                self._log(msg)
            else:
                msg = f"Failed to upload {rel} ✗"
                self._log(msg)

            fm = self.meta.files.get(rel) or FileMeta(size=size, mtime=mtime)
            fm.status = "failed"
            fm.sig = sig_now
            self.meta.files[rel] = fm
            self.meta.save(METADATA_FILE)
