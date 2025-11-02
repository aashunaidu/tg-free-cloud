import logging
import threading
import time
import datetime as dt
import tkinter as tk
from watchdog.observers import Observer

from .logging_setup import setup_logging
from .config import load_or_create_config, save_config
from .paths import METADATA_FILE, CLOUD_DIR
from .core.models import MetadataDB
from .telegram.dual_client import DualTelegramClient, PYRO_AVAILABLE
from .workers.upload_pool import UploadPool
from .ui.gui import TGCloudGUI
from .core.watcher import FolderEventHandler
from .core.scan import initial_scan_and_enqueue
from .telegram.bot_thread import BotThread


def prompt_credentials_gui(cfg: dict):
    """Small GUI to collect bot_token and chat_id on first run."""
    from tkinter import ttk, messagebox
    root = tk.Tk()
    root.title("TGCloud - Setup")
    root.geometry("520x340")
    root.resizable(False, False)
    token_var = tk.StringVar(value=cfg.get("bot_token", ""))
    chat_var = tk.StringVar(value=cfg.get("chat_id", ""))

    def on_save():
        tok = token_var.get().strip()
        chat = chat_var.get().strip()
        if not tok or not chat:
            messagebox.showerror("Missing", "Enter both Bot Token and Chat ID.")
            return
        cfg["bot_token"] = tok
        cfg["chat_id"] = chat
        save_config(cfg)
        root.destroy()

    frm = ttk.Frame(root, padding=12)
    frm.pack(fill="both", expand=True)

    ttk.Label(frm, text="Welcome to TGCloud", font=("Segoe UI", 14, "bold")).grid(row=0, column=0, columnspan=2, pady=(0, 8))
    ttk.Label(frm, text="Telegram Bot Token:").grid(row=1, column=0, sticky="e", padx=(0, 8))
    ttk.Entry(frm, textvariable=token_var, width=48).grid(row=1, column=1, sticky="we")
    ttk.Label(frm, text="Telegram Chat ID:").grid(row=2, column=0, sticky="e", padx=(0, 8))
    ttk.Entry(frm, textvariable=chat_var, width=48).grid(row=2, column=1, sticky="we")

    tip = "For 2GB uploads, turn ON 2GB Mode in Settings and generate a Session String (Python 3.11)."
    ttk.Label(frm, text=tip, foreground="#555").grid(row=3, column=0, columnspan=2, pady=(6, 0))
    ttk.Button(frm, text="Save & Continue", command=on_save).grid(row=4, column=0, columnspan=2, pady=(14, 0))
    root.mainloop()


class App:
    def __init__(self):
        setup_logging()
        logging.info("Booting TGCloud App…")

        # Load config (prompts only if credentials.json missing)
        self.cfg = load_or_create_config(prompt_cb=prompt_credentials_gui)
        save_config(self.cfg)

        # Metadata (uploaded files, backup timestamp, etc.)
        self.meta = MetadataDB.load(METADATA_FILE)

        # Telegram engines (Bot + optional Pyrogram user client on own loop)
        self.tg = DualTelegramClient(self.cfg)

        # GUI
        self.root = tk.Tk()
        self.gui = TGCloudGUI(self.root, self.cfg, self.meta, self.tg, PYRO_AVAILABLE)

        # Show connection state
        try:
            me = self.tg.bot_get_me()
            if me.get("ok"):
                uname = me["result"].get("username") or "bot"
                two = " | 2GB: ON" if self.cfg.get("enable_2gb_mode") else " | 2GB: OFF"
                self.gui.connection_var.set(f"Connected as @{uname}{two}")
                logging.info("Connected as @%s%s", uname, " (2GB ON)" if self.cfg.get("enable_2gb_mode") else " (2GB OFF)")
            else:
                self.gui.connection_var.set("Bot not connected")
                logging.warning("Bot getMe returned not ok: %s", me)
        except Exception as e:
            self.gui.connection_var.set(f"Bot connection error: {e}")
            logging.exception("Bot connection error: %s", e)

        # Uploader pool (must exist BEFORE watcher is scheduled)
        self.pool = UploadPool(self.tg, self.cfg, self.meta)
        self.pool.set_gui(self.gui)
        self.gui.attach_pool(self.pool)

        # Filesystem watcher on the sync folder
        self.observer = Observer()
        self.observer.schedule(FolderEventHandler(self.pool), str(CLOUD_DIR), recursive=True)

        # Daily backup thread
        self.backup_thread = threading.Thread(target=self._daily_backup_loop, name="daily-backup", daemon=True)

        # Bot command thread
        # IMPORTANT: create_backup_cb must return a Path WITHOUT enqueuing; the bot thread will only call it.
        # We wire a wrapper that creates the zip and returns its path; the UI/worker will handle enqueue separately.
        if self.cfg.get("bot_token"):
            self.bot_thread = BotThread(
                token=self.cfg.get("bot_token", ""),
                meta=self.meta,
                enqueue_cb=self.pool.enqueue,
                create_backup_cb=self._make_backup_and_return_path
            )
        else:
            self.bot_thread = None
            logging.info("No bot_token configured; bot thread not started.")

    # --- helpers -------------------------------------------------------------
    def _make_backup_and_return_path(self):
        """Create a backup ZIP and return its path (without enqueue); used by /backup command."""
        from .core.backup import create_zip_backup
        try:
            zip_path = create_zip_backup(self.meta, refresh_table_cb=self.gui.refresh_table)
            logging.info("Backup created via bot request: %s", zip_path.name)
            return zip_path
        except Exception as e:
            logging.exception("Backup creation failed: %s", e)
            return None

    def _daily_backup_loop(self):
        """Runs once a day at configured HH:MM; creates ZIP and enqueues it."""
        while True:
            try:
                hh, mm = map(int, self.cfg.get("daily_backup_time", "02:00").split(":"))
            except Exception:
                hh, mm = 2, 0

            now = dt.datetime.now()
            target = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
            if target <= now:
                target += dt.timedelta(days=1)

            sleep_s = max(1, int((target - now).total_seconds()))
            logging.info("Daily backup sleeping %ss until %s", sleep_s, target.isoformat())
            time.sleep(sleep_s)

            try:
                from .core.backup import create_zip_backup
                zip_path = create_zip_backup(self.meta, refresh_table_cb=self.gui.refresh_table)
                if zip_path:
                    self.pool.enqueue(zip_path)
                    self.meta.last_backup_iso = dt.datetime.now().isoformat(timespec="seconds")
                    self.meta.save(METADATA_FILE)
                    logging.info("Daily backup enqueued: %s", zip_path.name)
            except Exception as e:
                logging.exception("Daily backup error: %s", e)

    # --- lifecycle -----------------------------------------------------------
    def start(self):
        # Start uploader workers FIRST so queued files will actually process
        logging.info("Starting upload workers…")
        self.pool.start()

        # Start filesystem observer and backup thread
        logging.info("Starting folder observer on: %s", CLOUD_DIR)
        self.observer.start()

        logging.info("Starting daily backup thread…")
        self.backup_thread.start()

        # Start bot command thread (optional)
        if self.bot_thread:
            logging.info("Starting bot thread…")
            self.bot_thread.start()

        # Initial scan to (re)enqueue any pending/changed files
        logging.info("Scheduling initial scan…")
        threading.Thread(
            target=initial_scan_and_enqueue,
            args=(self.cfg, self.meta, self.pool),
            name="initial-scan",
            daemon=True
        ).start()

        self.root.protocol("WM_DELETE_WINDOW", self.on_close)
        logging.info("App started. Opening GUI mainloop.")
        self.root.mainloop()

    def on_close(self):
        logging.info("Shutting down…")
        # Stop new work and watcher
        try:
            self.observer.stop()
            self.pool.stop_event.set()
            self.observer.join(timeout=2)
        except Exception:
            pass

        # Stop Pyrogram loop cleanly (if running)
        try:
            self.tg.shutdown_user_client()
        except Exception:
            pass

        try:
            self.root.destroy()
        except Exception:
            pass

        logging.info("Goodbye.")
