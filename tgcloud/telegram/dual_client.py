import time
import asyncio
import threading
import logging
import requests
from pathlib import Path
from typing import Optional, Tuple
from ..paths import WORK_DIR

PYRO_AVAILABLE = False
try:
    from pyrogram import Client
    from pyrogram.types import Message
    from pyrogram.errors import SessionPasswordNeeded, PhoneCodeInvalid, PhoneCodeExpired, RPCError
    PYRO_AVAILABLE = True
except Exception:
    PYRO_AVAILABLE = False


class DualTelegramClient:
    BOT_LIMIT = 49 * 1024 * 1024  # ~49MB to be conservative

    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.rate_limit = float(cfg.get("rate_limit_seconds", 0.5))
        self._last_call = 0.0
        self._lock = threading.Lock()

        # Bot API basics
        self.bot_token = cfg.get("bot_token", "")
        self.chat_id = cfg.get("chat_id", "")
        self.bot_base = f"https://api.telegram.org/bot{self.bot_token}"
        self.bot_file_base = f"https://api.telegram.org/file/bot{self.bot_token}"

        # User API (Pyrogram)
        self.user_client: Optional["Client"] = None
        self.user_loop: Optional[asyncio.AbstractEventLoop] = None
        self.user_ready = threading.Event()

        self._init_user_client()

    # ------------- rate limit for bot calls -------------
    def _guard(self):
        with self._lock:
            elapsed = time.time() - self._last_call
            if elapsed < self.rate_limit:
                time.sleep(self.rate_limit - elapsed)
            self._last_call = time.time()

    # ------------- user client bootstrap -------------
    def _init_user_client(self):
        if not PYRO_AVAILABLE:
            logging.info("Pyrogram not available; user API disabled.")
            return
        if not bool(self.cfg.get("enable_2gb_mode", False)):
            logging.info("2GB mode is OFF; user API not started.")
            return

        api_id = self.cfg.get("api_id")
        api_hash = self.cfg.get("api_hash")
        session_str = self.cfg.get("user_session_string")
        if not (api_id and api_hash and session_str):
            logging.info("2GB mode ON but session/API credentials missing; staying in Bot mode.")
            return

        try:
            self.user_loop = asyncio.new_event_loop()

            def _runner():
                asyncio.set_event_loop(self.user_loop)
                try:
                    self.user_client = Client(
                        name="tgcloud_user",
                        api_id=int(api_id),
                        api_hash=api_hash,
                        session_string=session_str,
                        workdir=str(WORK_DIR / ".pyrogram"),
                        no_updates=True
                    )

                    async def _start():
                        await self.user_client.start()
                        return True

                    self.user_loop.run_until_complete(_start())
                    self.user_ready.set()
                    logging.info("User API client started (2GB mode ON).")
                    self.user_loop.run_forever()
                except Exception as e:
                    logging.exception("User API loop crashed: %s", e)
                finally:
                    try:
                        if self.user_client:
                            self.user_loop.run_until_complete(self.user_client.stop())
                    except Exception:
                        pass

            t = threading.Thread(target=_runner, name="pyrogram-loop", daemon=True)
            t.start()
        except Exception as e:
            logging.exception("User API init failed: %s", e)
            self.user_client = None
            self.user_loop = None

    # ------------- Bot helpers -------------
    def bot_get_me(self) -> dict:
        self._guard()
        r = requests.get(f"{self.bot_base}/getMe", timeout=30)
        r.raise_for_status()
        return r.json()

    def bot_send_document(self, file_path: Path, caption: Optional[str], progress_cb=None) -> Tuple[Optional[int], Optional[str]]:
        from requests_toolbelt.multipart.encoder import MultipartEncoder, MultipartEncoderMonitor

        self._guard()
        url = f"{self.bot_base}/sendDocument"
        total = file_path.stat().st_size
        start = time.time()

        def _progress(monitor):
            if progress_cb:
                sent = monitor.bytes_read
                elapsed = max(time.time() - start, 1e-6)
                speed = sent / elapsed
                remaining = max(total - sent, 0)
                eta = remaining / speed if speed > 0 else 0
                progress_cb(sent, total, speed, eta)

        # robust open (windows sometimes locks files briefly)
        for _ in range(10):
            try:
                fobj = open(file_path, "rb")
                break
            except PermissionError:
                time.sleep(0.5)
        else:
            return None, None

        files = {"document": (file_path.name, fobj, "application/octet-stream")}
        data = {"chat_id": self.cfg.get("chat_id", "")}
        if caption:
            data["caption"] = caption
        enc = MultipartEncoder(fields={**data, "document": files["document"]})
        mon = MultipartEncoderMonitor(enc, _progress)
        headers = {"Content-Type": mon.content_type}
        r = requests.post(url, data=mon, headers=headers, timeout=1800)
        try:
            if r.status_code == 429:
                retry_after = r.json().get("parameters", {}).get("retry_after", 5)
                time.sleep(int(retry_after) + 1)
                return self.bot_send_document(file_path, caption, progress_cb)
            r.raise_for_status()
            resp = r.json()
            if not resp.get("ok"):
                return None, None
            res = resp["result"]
            return res.get("message_id"), res.get("document", {}).get("file_id")
        finally:
            try:
                fobj.close()
            except Exception:
                pass

    def bot_get_file_path(self, file_id: str):
        self._guard()
        r = requests.get(f"{self.bot_base}/getFile", params={"file_id": file_id}, timeout=60)
        if r.status_code != 200:
            return None
        data = r.json()
        if data.get("ok") and data.get("result", {}).get("file_path"):
            return data["result"]["file_path"]
        return None

    def bot_download_file(self, file_id: str, dest: Path, progress_cb=None) -> bool:
        """
        Stream a file via the Bot API with progress updates.
        """
        fp = self.bot_get_file_path(file_id)
        if not fp:
            return False
        url = f"{self.bot_file_base}/{fp}"
        self._guard()

        start = time.time()
        bytes_read = 0
        last_ts = start
        last_bytes = 0

        with requests.get(url, stream=True, timeout=1800) as r:
            if r.status_code != 200:
                return False
            total = int(r.headers.get("Content-Length", "0") or 0)

            # ensure folder exists
            dest.parent.mkdir(parents=True, exist_ok=True)

            with open(dest, "wb") as out:
                for chunk in r.iter_content(chunk_size=1024 * 128):
                    if not chunk:
                        continue
                    out.write(chunk)
                    bytes_read += len(chunk)

                    # progress update ~1/sec
                    if progress_cb:
                        now = time.time()
                        if now - last_ts >= 1.0 or bytes_read == total:
                            elapsed = max(now - last_ts, 1e-6)
                            speed = (bytes_read - last_bytes) / elapsed
                            remain = max(total - bytes_read, 0)
                            eta = (remain / speed) if speed > 0 else 0
                            last_ts = now
                            last_bytes = bytes_read
                            progress_cb(bytes_read, total, speed, eta)
        return True

    # ------------- User helpers -------------
    def _await_on_user_loop(self, coro, timeout=None):
        if not (self.user_loop and self.user_ready.is_set() and self.user_client):
            raise RuntimeError("User client not ready")
        fut = asyncio.run_coroutine_threadsafe(coro, self.user_loop)
        return fut.result(timeout=timeout)

    def user_send_document(self, file_path: Path, caption: Optional[str], progress_cb=None):
        if not (self.user_loop and self.user_ready.is_set() and self.user_client):
            logging.warning("User client not initialized; cannot send via 2GB mode.")
            return None, None

        start = time.time()

        def _pyro_progress(current, total_):
            if progress_cb:
                elapsed = max(time.time() - start, 1e-6)
                speed = current / elapsed
                remaining = max(total_ - current, 0)
                eta = remaining / speed if speed > 0 else 0
                progress_cb(current, total_, speed, eta)

        async def _do_send():
            m = await self.user_client.send_document(
                chat_id=int(self.cfg.get("chat_id")),
                document=str(file_path),
                caption=caption or "",
                progress=_pyro_progress,
            )
            return m.id

        try:
            mid = self._await_on_user_loop(_do_send(), timeout=None)
            logging.info("User API upload success: %s", file_path.name)
            return mid, None
        except Exception as e:
            logging.exception("User send failed: %s", e)
            return None, None

    def user_download_by_message_id(self, message_id: int, dest: Path, progress_cb=None) -> bool:
        """
        Download via user API (Pyrogram) with progress updates.
        """
        if not (self.user_loop and self.user_ready.is_set() and self.user_client):
            return False

        start = time.time()
        last = start
        last_bytes = 0

        # Pyrogram progress callback gets (current, total)
        def _pyro_progress(current, total):
            nonlocal last, last_bytes
            if not progress_cb:
                return
            now = time.time()
            if now - last >= 1.0 or current == total:
                elapsed = max(now - last, 1e-6)
                speed = (current - last_bytes) / elapsed
                remaining = max(total - current, 0)
                eta = remaining / speed if speed > 0 else 0
                last = now
                last_bytes = current
                progress_cb(current, total, speed, eta)

        async def _dl():
            m: "Message" = await self.user_client.get_messages(int(self.cfg.get("chat_id")), message_id)
            await self.user_client.download_media(m, file_name=str(dest), progress=_pyro_progress)
            return True

        try:
            return bool(self._await_on_user_loop(_dl(), timeout=None))
        except RPCError as e:
            logging.error(f"User download RPC error: {e}")
            return False
        except Exception as e:
            logging.exception("User download failed: %s", e)
            return False

    def shutdown_user_client(self):
        if self.user_loop and self.user_client:
            try:
                self._await_on_user_loop(self.user_client.stop(), timeout=10)
            except Exception:
                pass
            try:
                self.user_loop.call_soon_threadsafe(self.user_loop.stop)
            except Exception:
                pass

    # ------------- High-level wrappers -------------
    def send_document(self, path: Path, caption: str, prefer_user: bool = False, progress_cb=None):
        enable_2gb = bool(self.cfg.get("enable_2gb_mode", False))
        if enable_2gb and (self.user_client is not None) and self.user_ready.is_set():
            force_user = bool(self.cfg.get("force_user_api", True))
            use_user = force_user or (path.stat().st_size > self.BOT_LIMIT) or prefer_user
            if use_user:
                mid, _ = self.user_send_document(path, caption, progress_cb)
                return ("user", mid, None)

        # Bot path
        if path.stat().st_size > self.BOT_LIMIT:
            logging.warning("File %s is >50MB but 2GB mode is OFF or user client not ready. Skipping.", path.name)
            return ("bot", None, None)
        mid, fid = self.bot_send_document(path, caption, progress_cb)
        return ("bot", mid, fid)

    def download(self, fm, dest: Path, progress_cb=None) -> bool:
        """
        High-level downloader that chooses bot or user path and emits progress.
        """
        # Prefer bot if we have a bot file_id (unless it fails)
        if fm.via != "user" and getattr(fm, "file_id", None):
            ok = self.bot_download_file(fm.file_id, dest, progress_cb=progress_cb)
            if ok:
                return True

        # Try user download by message id
        if getattr(fm, "user_message_id", None):
            return self.user_download_by_message_id(fm.user_message_id, dest, progress_cb=progress_cb)

        # Fallback: some records may have message_id only
        if getattr(fm, "message_id", None) and self.user_client:
            return self.user_download_by_message_id(fm.message_id, dest, progress_cb=progress_cb)

        logging.warning("No valid download source (bot file_id or user message id) for item.")
        return False
