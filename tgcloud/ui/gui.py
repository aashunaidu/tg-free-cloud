import os
import sys
import math
import time
import shutil
import platform
import logging
import threading
import datetime as dt
from pathlib import Path
from typing import List, Optional

import tkinter as tk
from tkinter import ttk, messagebox, simpledialog, filedialog
from tkinter import filedialog
from ..core.folder_packer import auto_zip_folder
from ..paths import BACKUP_DIR

# Optional DnD support (auto-detect). If not installed, everything still works.
try:
    from tkinterdnd2 import DND_FILES, TkinterDnD  # pip install tkinterdnd2
    TKDND_AVAILABLE = True
except Exception:
    DND_FILES = None
    TkinterDnD = tk.Tk
    TKDND_AVAILABLE = False

# Optional notifications
try:
    from plyer import notification
except Exception:
    notification = None

# Watchdog to mirror extra folders
try:
    from watchdog.observers import Observer
    from watchdog.events import FileSystemEventHandler
    WATCHDOG_AVAILABLE = True
except Exception:
    WATCHDOG_AVAILABLE = False

from ..paths import WORK_DIR, CLOUD_DIR, DOWNLOAD_DIR, BACKUP_DIR
from ..utils import human_size
from ..core.backup import create_zip_backup


# ---------- Helpers ----------
def open_os_folder(path: Path):
    try:
        if os.name == "nt":
            os.startfile(str(path))  # noqa: P204
        elif sys.platform == "darwin":
            os.system(f"open '{path}'")
        else:
            os.system(f"xdg-open '{path}'")
    except Exception as e:
        logging.exception("Open folder failed: %s", e)


def sanitize_name(p: Path) -> str:
    s = p.name.strip().replace(":", "_").replace("/", "_").replace("\\", "_")
    return s or "linked"


class MirrorEventHandler(FileSystemEventHandler):
    """
    Mirrors created/modified files from an extra folder into CLOUD_DIR/linked/<folder-name>/
    so your existing UploadPool (which watches CLOUD_DIR) will pick them up.
    """
    def __init__(self, source_root: Path, dest_root: Path, gui_logger):
        super().__init__()
        self.source_root = source_root
        self.dest_root = dest_root
        self.gui_logger = gui_logger

    def _mirror_file(self, src: Path):
        try:
            if not src.is_file():
                return
            rel = src.relative_to(self.source_root)
            dst = self.dest_root / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
            if self.gui_logger:
                self.gui_logger(f"Mirrored: {rel} → {dst.relative_to(self.dest_root.parent)}")
        except Exception as e:
            logging.exception("Mirror copy failed: %s", e)
            if self.gui_logger:
                self.gui_logger(f"Mirror copy failed: {src.name}: {e}")

    def on_created(self, event):
        if getattr(event, "is_directory", False):
            return
        self._mirror_file(Path(event.src_path))

    def on_modified(self, event):
        if getattr(event, "is_directory", False):
            return
        self._mirror_file(Path(event.src_path))


# ---------- Settings Dialog ----------
class SettingsDialog(tk.Toplevel):
    def __init__(self, master, cfg: dict, on_save_cb, restart_cb, pyrogram_available: bool):
        super().__init__(master)
        self.title("Settings")
        self.geometry("860x680")
        self.resizable(True, True)

        self.cfg = cfg
        self.on_save_cb = on_save_cb
        self.restart_cb = restart_cb
        self.vars = {k: tk.StringVar(value=str(v)) for k, v in cfg.items()}
        self.pyrogram_available = pyrogram_available

        nb = ttk.Notebook(self)
        nb.pack(fill="both", expand=True, padx=10, pady=10)

        # Telegram
        f1 = ttk.Frame(nb, padding=10)
        nb.add(f1, text="Telegram")
        self._entry(f1, "Bot Token", "bot_token", 0)
        self._entry(f1, "Chat ID (int)", "chat_id", 1)
        ttk.Separator(f1).grid(row=2, column=0, columnspan=3, sticky="we", pady=6)
        ttk.Label(f1, text="User API (2GB uploads via Pyrogram)", font=("Segoe UI", 10, "bold")).grid(row=3, column=0, columnspan=3, sticky="w", pady=(4,6))
        self._entry(f1, "API ID", "api_id", 4)
        self._entry(f1, "API Hash", "api_hash", 5)
        self._entry(f1, "Session String", "user_session_string", 6, width=56)
        ttk.Button(f1, text="Generate Session String…", command=self.generate_session_string).grid(row=7, column=1, sticky="w", pady=(6,0))

        # Behavior
        f2 = ttk.Frame(nb, padding=10)
        nb.add(f2, text="Behavior")
        self._entry(f2, "Rate Limit (s)", "rate_limit_seconds", 0)
        self._entry(f2, "Max file MB (UI only)", "max_file_mb", 1)
        self._entry(f2, "Num upload workers", "num_workers", 2)
        self._entry(f2, "Num download workers", "download_workers", 3)
        self._entry(f2, "Daily backup time HH:MM", "daily_backup_time", 4)
        self._entry(f2, "Use SHA256 (true/false)", "use_sha256", 5)
        self._entry(f2, "Force user API (true/false)", "force_user_api", 6)
        self._entry(f2, "Enable 2GB mode (true/false)", "enable_2gb_mode", 7)

        # System
        f3 = ttk.Frame(nb, padding=10)
        nb.add(f3, text="System")
        self._entry(f3, "Preferred Python launcher", "preferred_python", 0, width=40)
        ttk.Button(f3, text="Restart now (Py 3.11)", command=self._restart).grid(row=1, column=1, sticky="w", pady=(6,0))

        # Linked Folders (extra)
        f4 = ttk.Frame(nb, padding=10)
        nb.add(f4, text="Linked Folders")
        ttk.Label(f4, text="Mirror these folders into your TGCloud sync (CLOUD_DIR):").grid(row=0, column=0, columnspan=3, sticky="w", pady=(0,8))
        self.linked_list = tk.Listbox(f4, height=8)
        self.linked_list.grid(row=1, column=0, columnspan=3, sticky="nsew")
        f4.grid_rowconfigure(1, weight=1)
        f4.grid_columnconfigure(1, weight=1)
        ttk.Button(f4, text="Add folder…", command=self._add_linked_folder).grid(row=2, column=0, sticky="w", pady=6)
        ttk.Button(f4, text="Remove selected", command=self._remove_linked_folder).grid(row=2, column=1, sticky="w", pady=6)
        ttk.Button(f4, text="Open mirror root", command=lambda: open_os_folder(CLOUD_DIR / "linked")).grid(row=2, column=2, sticky="e", pady=6)

        # Populate list
        for p in self._get_linked_folders():
            self.linked_list.insert("end", p)
        

        # Bottom buttons
        btns = ttk.Frame(self)
        btns.pack(fill="x", padx=10, pady=(0,10))
        ttk.Button(btns, text="Save", command=self._save).pack(side="right")
        ttk.Button(btns, text="Cancel", command=self.destroy).pack(side="right", padx=(0,8))

        tip = "Some changes (Telegram auth, workers, 2GB toggle) require app restart."
        ttk.Label(self, text=tip, foreground="#666").pack(pady=(0,6))

    def _entry(self, parent, label, key, row, width=44):
        ttk.Label(parent, text=label + ":").grid(row=row, column=0, sticky="e", padx=(0,8), pady=4)
        ttk.Entry(parent, textvariable=self.vars[key], width=width).grid(row=row, column=1, sticky="we", pady=4)
        parent.grid_columnconfigure(1, weight=1)

    def _get_linked_folders(self) -> List[str]:
        v = self.cfg.get("extra_sync_folders") or []
        if isinstance(v, list):
            return v
        return []

    def _add_linked_folder(self):
        d = filedialog.askdirectory(title="Choose a folder to mirror into TGCloud")
        if not d:
            return
        d = os.path.abspath(d)
        arr = self._get_linked_folders()
        if d not in arr:
            arr.append(d)
            self.cfg["extra_sync_folders"] = arr
            from ..config import save_config
            save_config(self.cfg)
            self.linked_list.insert("end", d)
            messagebox.showinfo("Added", "Folder added. Files created/updated there will mirror into TGCloud/linked/ .")
        else:
            messagebox.showinfo("Already linked", "This folder is already in the linked list.")

    def _remove_linked_folder(self):
        sel = list(self.linked_list.curselection())
        if not sel:
            return
        arr = self._get_linked_folders()
        for idx in reversed(sel):
            val = self.linked_list.get(idx)
            self.linked_list.delete(idx)
            if val in arr:
                arr.remove(val)
        self.cfg["extra_sync_folders"] = arr
        from ..config import save_config
        save_config(self.cfg)
        messagebox.showinfo("Removed", "Selected folder(s) removed from linked list.")

    def _save(self):
        for k, var in self.vars.items():
            val = var.get()
            if k in ("rate_limit_seconds",):
                try:
                    self.cfg[k] = float(val)
                except Exception:
                    pass
            elif k in ("max_file_mb", "num_workers", "download_workers"):
                try:
                    self.cfg[k] = int(val)
                except Exception:
                    pass
            elif k in ("use_sha256", "encryption_enabled", "force_user_api", "enable_2gb_mode"):
                self.cfg[k] = str(val).lower() in ("1","true","yes","on")
            else:
                self.cfg[k] = val
        from ..config import save_config
        save_config(self.cfg)
        self.on_save_cb(self.cfg)
        self.destroy()

    def _restart(self):
        self._save()
        self.restart_cb()
    
    def generate_session_string(self):
        if not self.pyrogram_available:
            messagebox.showerror("Pyrogram not installed", "Install first:\n\npip install pyrogram tgcrypto")
            return
        api_id = self.vars["api_id"].get().strip()
        api_hash = self.vars["api_hash"].get().strip()
        if not api_id or not api_hash:
            messagebox.showinfo("Missing", "Enter API ID and API Hash first (from my.telegram.org).")
            return
        try:
            api_id_int = int(api_id)
        except Exception:
            messagebox.showerror("Invalid", "API ID must be a number.")
            return

        phone = simpledialog.askstring("Phone", "Enter your Telegram phone (with country code):", parent=self)
        if not phone:
            return

        try:
            from pyrogram import Client
            from pyrogram.errors import SessionPasswordNeeded, PhoneCodeInvalid, PhoneCodeExpired
            app = Client(":memory:", api_id=api_id_int, api_hash=api_hash, in_memory=True, no_updates=True)
            app.connect()
            sent = app.send_code(phone)
            while True:
                code = simpledialog.askstring("Login Code", "Enter the login code you received:", parent=self)
                if code is None:
                    app.disconnect(); return
                code = code.strip().replace(" ", "")
                if code:
                    break
                messagebox.showerror("Empty code", "Code cannot be empty. Please enter the code from Telegram.")
            try:
                app.sign_in(phone_number=phone, phone_code=code, phone_code_hash=sent.phone_code_hash)
            except PhoneCodeInvalid:
                messagebox.showerror("Invalid code", "The code you entered is invalid. Try again.")
                app.disconnect(); return
            except PhoneCodeExpired:
                messagebox.showerror("Expired code", "The code expired. Please try again.")
                app.disconnect(); return
            except SessionPasswordNeeded:
                pwd = simpledialog.askstring("Two-Step Password", "Enter your Telegram password:", parent=self, show="*")
                if not pwd:
                    app.disconnect(); return
                app.check_password(password=pwd)
            s = app.export_session_string()
            app.disconnect()
        except Exception as e:
            messagebox.showerror("Login failed", f"{e}")
            return

        self.vars["user_session_string"].set(s)
        self.cfg["api_id"] = api_id_int
        self.cfg["api_hash"] = api_hash
        self.cfg["user_session_string"] = s
        from ..config import save_config
        save_config(self.cfg)
        messagebox.showinfo("Done", "Session string saved. Turn ON 2GB mode and Restart to activate 2GB uploads.")


# ---------- Main GUI ----------
class TGCloudGUI:
    def __init__(self, root: tk.Tk, cfg: dict, meta, tg, pyrogram_available: bool):
        # If TkDND is available, upgrade the root to support DnD
        if TKDND_AVAILABLE and not isinstance(root, TkinterDnD):
            # Replace root with DnD-enabled root (best effort)
            root.destroy()
            root = TkinterDnD()

        self.root = root
        self.cfg = cfg
        self.meta = meta
        self.tg = tg
        self.pool = None
        self.pyrogram_available = pyrogram_available

        self.root.title("TGCloud")
        self.root.geometry("1120x760")
        self.root.minsize(900, 620)

        # Top status vars
        self.current_file_var = tk.StringVar(value="Idle")
        self.connection_var = tk.StringVar(value="Connecting…")
        self.pause_state_var = tk.StringVar(value="")
        self.progress_pct_var = tk.StringVar(value="0%")
        self.progress_speed_var = tk.StringVar(value="0 KB/s")
        self.progress_eta_var = tk.StringVar(value="—")
        self.status_var = tk.StringVar(value="Ready")

        # Extra folder observers
        self._linked_observers: List[Observer] = []

        self._build_ui()
        self._load_linked_folder_watchers()

    def attach_pool(self, pool):
        self.pool = pool

        # ---------- UI build ----------
    def _build_ui(self):
        # ===== HEADER =====
        header = ttk.Frame(self.root, padding=(10, 8))
        header.pack(fill="x")
        ttk.Label(header, text="TGCloud Dashboard", font=("Segoe UI", 16, "bold")).pack(side="left")
        ttk.Button(header, text="Settings", command=self.open_settings).pack(side="left", padx=(10,0))
        ttk.Button(header, text="Restart (Py 3.11)", command=self.restart_app).pack(side="left", padx=(10,0))
        ttk.Label(header, textvariable=self.connection_var, foreground="#008000", font=("Segoe UI", 10, "bold")).pack(side="right")

        # ===== ACTIONS ROW =====
        actions = ttk.Frame(self.root, padding=(10, 6))
        actions.pack(fill="x", pady=(2, 6))

        # Folder / Upload
        ttk.Button(actions, text="📁 Open TGCloud", command=lambda: open_os_folder(CLOUD_DIR)).pack(side="left")
        ttk.Button(actions, text="📥 Open Downloads", command=lambda: open_os_folder(DOWNLOAD_DIR)).pack(side="left", padx=(8,0))
        ttk.Button(actions, text="⬆️ Upload Files…", command=self._pick_and_copy_files).pack(side="left", padx=(16,0))
        ttk.Button(actions, text="➕ Add Sync Folder…", command=self._add_linked_folder_gui).pack(side="left", padx=(8,0))

        # Backup / ZIP tools
        ttk.Separator(actions, orient="vertical").pack(side="left", fill="y", padx=8, pady=2)
        ttk.Button(actions, text="🧾 Backup Now (ZIP)", command=self.on_backup_now).pack(side="left", padx=(8,0))
        ttk.Button(actions, text="📦 Auto ZIP Folder (≤2GB)", command=self.on_auto_zip_folder).pack(side="left", padx=(8,0))

        # Download tools
        ttk.Separator(actions, orient="vertical").pack(side="left", fill="y", padx=8, pady=2)
        ttk.Button(actions, text="⬇️ Download ALL", command=self.on_download_all).pack(side="left", padx=(8,0))

        # Controls
        ttk.Separator(actions, orient="vertical").pack(side="left", fill="y", padx=8, pady=2)
        self.pause_btn = ttk.Button(actions, text="⏸ Pause", command=self.on_pause_resume)
        self.pause_btn.pack(side="left", padx=(8, 0))
        ttk.Button(actions, text="🔄 Refresh", command=self.refresh_table).pack(side="left", padx=(8,0))

        # ===== PROGRESS SECTION =====
        prog = ttk.Labelframe(self.root, text="Sync / Upload Progress", padding=10)
        prog.pack(fill="x", padx=10, pady=(4, 10))

        ttk.Label(prog, text="Current:").grid(row=0, column=0, sticky="w")
        ttk.Label(prog, textvariable=self.current_file_var).grid(row=0, column=1, sticky="w")
        ttk.Label(prog, textvariable=self.pause_state_var, foreground="#888").grid(row=0, column=2, sticky="e")

        self.pb = ttk.Progressbar(prog, length=540, mode="determinate")
        self.pb.grid(row=1, column=0, columnspan=3, sticky="we", pady=(6,0))
        ttk.Label(prog, textvariable=self.progress_pct_var, width=8).grid(row=1, column=3, sticky="e")

        ttk.Label(prog, text="Speed:").grid(row=2, column=0, sticky="w", pady=(6,0))
        ttk.Label(prog, textvariable=self.progress_speed_var).grid(row=2, column=1, sticky="w", pady=(6,0))
        ttk.Label(prog, text="ETA:").grid(row=2, column=2, sticky="e", pady=(6,0))
        ttk.Label(prog, textvariable=self.progress_eta_var).grid(row=2, column=3, sticky="w", pady=(6,0))

        # ===== TABLE + LOG AREA =====
        mid = ttk.Panedwindow(self.root, orient="horizontal")
        mid.pack(fill="both", expand=True, padx=10, pady=(0,10))

        table_frame = ttk.Frame(mid, padding=(0,0,8,0))
        log_frame = ttk.Frame(mid, padding=(4,0,0,0))
        mid.add(table_frame, weight=3)
        mid.add(log_frame, weight=2)

        # Table setup
        cols = ("filename", "size", "status", "via")
        self.table = ttk.Treeview(table_frame, columns=cols, show="headings", selectmode="browse")
        for c, w in zip(cols, (620, 120, 100, 80)):
            self.table.heading(c, text=c.capitalize(), command=lambda c=c: self._sort_by(c))
            self.table.column(c, width=w, anchor="w")
        self.table.grid(row=0, column=0, sticky="nsew")
        table_frame.grid_rowconfigure(0, weight=1)
        table_frame.grid_columnconfigure(0, weight=1)

        sb = ttk.Scrollbar(table_frame, orient="vertical", command=self.table.yview)
        self.table.configure(yscroll=sb.set)
        sb.grid(row=0, column=1, sticky="ns")

        # ===== RIGHT-CLICK MENU =====
        self.menu = tk.Menu(self.root, tearoff=False)
        self.menu.add_command(label="Download", command=self.on_download_selected)
        self.menu.add_command(label="Open in Folder", command=self._open_selected_in_folder)
        self.menu.add_separator()
        self.menu.add_command(label="Remove from List (Local Only)", command=self._remove_from_list)

        self.table.bind("<Button-3>", self._popup_menu)
        self.table.bind("<Double-1>", lambda e: self.on_download_selected())

        # ===== DRAG & DROP SUPPORT =====
        if TKDND_AVAILABLE:
            self.table.drop_target_register(DND_FILES)
            self.table.dnd_bind("<<Drop>>", self._on_drop_files)

        # ===== LOG AREA =====
        ttk.Label(log_frame, text="Activity Log", font=("Segoe UI", 10, "bold")).pack(anchor="w", pady=(4,2))
        self.log_text = tk.Text(log_frame, height=12)
        self.log_text.pack(fill="both", expand=True)
        ttk.Button(log_frame, text="🧹 Clear Log", command=lambda: self.log_text.delete("1.0", "end")).pack(anchor="e", pady=(6,0))

        # ===== STATUS BAR =====
        status = ttk.Frame(self.root, padding=(6,4))
        status.pack(fill="x")
        ttk.Label(status, textvariable=self.status_var, foreground="#444").pack(side="left", padx=10)

        # ===== FINAL REFRESH =====
        self.refresh_table()


    def on_auto_zip_folder(self):
        """Automatically zip large folders into ≤2GB chunks and show real-time popup safely."""
        from tkinter import filedialog
        from queue import Queue
        from pathlib import Path
        from ..core.folder_packer import auto_zip_folder
        from ..paths import BACKUP_DIR

        folder = filedialog.askdirectory(title="Select Folder to Auto-ZIP (≤2GB each)")
        if not folder:
            return

        folder = Path(folder)
        self.log(f"🗜 Starting Auto ZIP for folder: {folder}")
        self.set_current_upload("Zipping...", 0)

        # Queue for progress updates
        progress_q = Queue()

        # 🪟 Create the popup window safely in main thread
        popup = self._show_zip_progress_window(folder.name, BACKUP_DIR / "zip_files", progress_q)

        # 🔧 Run zipping in background
        def _run():
            try:
                zips = auto_zip_folder(folder, BACKUP_DIR, progress_queue=progress_q)
                self.log(f"✅ Created {len(zips)} ZIP files in {BACKUP_DIR / 'zip_files'}")
                if self.pool:
                    for z in zips:
                        self.pool.enqueue(z)
                progress_q.put("DONE")
            except Exception as e:
                self.log(f"❌ Auto ZIP failed: {e}")
                progress_q.put("DONE")

        threading.Thread(target=_run, daemon=True).start()



    # ---------- Popup Progress Window ----------
    def _show_zip_progress_window(self, folder_name: str, out_dir: Path, progress_q):
    
        win = tk.Toplevel(self.root)
        win.title(f"Zipping: {folder_name}")
        win.geometry("500x260")
        win.resizable(False, False)
        win.attributes('-topmost', True)

        ttk.Label(win, text=f"Zipping Folder: {folder_name}", font=("Segoe UI", 11, "bold")).pack(pady=(10,6))
        ttk.Label(win, text="Output Folder:").pack()
        link = ttk.Label(win, text=str(out_dir), foreground="#0078D7", cursor="hand2")
        link.pack()
        link.bind("<Button-1>", lambda e: os.startfile(str(out_dir)))

        pb = ttk.Progressbar(win, length=440, mode="determinate")
        pb.pack(pady=(14,4))
        lbl_status = ttk.Label(win, text="Starting...", font=("Segoe UI", 10))
        lbl_status.pack(pady=(2,2))

        lbl_time = ttk.Label(win, text="Elapsed: 0s | ETA: —", font=("Segoe UI", 9), foreground="#666")
        lbl_time.pack(pady=(0,10))
        close_btn = ttk.Button(win, text="Close", command=win.destroy, state="disabled")
        close_btn.pack(side="bottom", pady=(8,6))

        def update_loop():
            try:
                msg = progress_q.get_nowait()
                if msg == "DONE":
                    lbl_status.config(text="✅ ZIP Completed!")
                    pb["value"] = 100
                    close_btn.config(state="normal")
                    return
                if isinstance(msg, dict) and msg.get("type") == "progress":
                    pb["value"] = msg["pct"]
                    lbl_status.config(
                        text=f"Creating {msg['current_zip']} ({msg['completed_zips']}/{msg['total_zips']}) - {msg['pct']:.1f}%"
                    )
                    lbl_time.config(
                        text=f"Elapsed: {int(msg['elapsed'])}s | ETA: {int(msg['eta'])}s"
                    )
            except:
                pass
            win.after(300, update_loop)

        update_loop()
        return win


    # ---------- linked folders watchers ----------
    def _load_linked_folder_watchers(self):
        if not WATCHDOG_AVAILABLE:
            return
        # Stop old
        for obs in self._linked_observers:
            try:
                obs.stop()
                obs.join(timeout=1)
            except Exception:
                pass
        self._linked_observers.clear()

        folders = self.cfg.get("extra_sync_folders") or []
        if not folders:
            return

        mirror_root = (CLOUD_DIR / "linked")
        mirror_root.mkdir(parents=True, exist_ok=True)

        for folder in folders:
            src = Path(folder)
            if not src.exists() or not src.is_dir():
                continue
            dest = mirror_root / sanitize_name(src)
            dest.mkdir(parents=True, exist_ok=True)
            handler = MirrorEventHandler(src, dest, self.log)
            obs = Observer()
            obs.schedule(handler, str(src), recursive=True)
            obs.daemon = True
            obs.start()
            self._linked_observers.append(obs)
            self.log(f"Linked watcher: {src} → {dest}")

    # ---------- actions ----------
    def open_settings(self):
        def on_save(new_cfg):
            self.cfg.update(new_cfg)
            self.tg.rate_limit = float(new_cfg.get("rate_limit_seconds", 0.5))
            self.log("Settings saved. Some changes require restart.")
            # reload watchers in case linked folders changed
            self._load_linked_folder_watchers()

        SettingsDialog(self.root, self.cfg, on_save, self.restart_app, self.pyrogram_available)

    def restart_app(self):
        launcher = self.cfg.get("preferred_python") or "py -3.11"
        cmd = f'{launcher} "{(WORK_DIR / "run.py")}"'
        self.log(f"Restarting with: {cmd}")
        try:
            subprocess = __import__("subprocess")
            subprocess.Popen(cmd, shell=True, cwd=str(WORK_DIR))
        except Exception as e:
            messagebox.showerror("Restart failed", str(e))
            return
        self.root.after(500, self.root.destroy)

    def _pick_and_copy_files(self):
        paths = filedialog.askopenfilenames(title="Select files to upload (copied into TGCloud)")
        if not paths:
            return
        for p in paths:
            self._copy_into_cloud(Path(p))

    def _add_linked_folder_gui(self):
        # Shortcut to open Settings → Linked Folders tab
        self.open_settings()

    def _copy_into_cloud(self, src: Path):
        try:
            CLOUD_DIR.mkdir(parents=True, exist_ok=True)
            dst = CLOUD_DIR / src.name
            # If name collision, add timestamp
            if dst.exists():
                ts = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
                dst = CLOUD_DIR / f"{src.stem}_{ts}{src.suffix}"
            shutil.copy2(src, dst)
            self.log(f"Queued upload: {dst.name}")
            if self.pool:
                # Not strictly needed (watcher will catch), but enqueuing is snappier
                try:
                    self.pool.enqueue(dst)
                except Exception:
                    pass
        except Exception as e:
            logging.exception("Copy into cloud failed: %s", e)
            messagebox.showerror("Copy failed", str(e))

    # ---------- table helpers ----------
    def _popup_menu(self, event):
        try:
            row = self.table.identify_row(event.y)
            if row:
                self.table.selection_set(row)
                self.menu.tk_popup(event.x_root, event.y_root)
        finally:
            self.menu.grab_release()

    def _get_selected_rel(self) -> Optional[str]:
        sel = self.table.selection()
        if not sel:
            return None
        return self.table.item(sel[0], "values")[0]

    def _open_selected_in_folder(self):
        rel = self._get_selected_rel()
        if not rel:
            return
        p = CLOUD_DIR / rel
        if not p.exists():
            # fallback: open the cloud dir
            open_os_folder(CLOUD_DIR)
            return
        open_os_folder(p.parent)

    def _remove_from_list(self):
        rel = self._get_selected_rel()
        if not rel:
            return
        if messagebox.askyesno("Remove", f"Remove {rel} from the list (local metadata only)?"):
            if rel in self.meta.files:
                del self.meta.files[rel]
                # saving metadata
                from ..paths import METADATA_FILE
                self.meta.save(METADATA_FILE)
                self.refresh_table()
                self.log(f"Removed {rel} from metadata.")

    def _sort_by(self, col):
        items = [(self.table.set(k, col), k) for k in self.table.get_children("")]
        # try numeric for size, else lexicographic
        try:
            items.sort(key=lambda t: float(t[0].split()[0]) if col == "size" else t[0].lower())
        except Exception:
            items.sort(key=lambda t: t[0].lower())
        for index, (_, k) in enumerate(items):
            self.table.move(k, "", index)

    # ---------- logging & progress ----------
    def log(self, msg: str):
        logging.info(msg)
        try:
            self.log_text.insert("end", f"{dt.datetime.now().strftime('%H:%M:%S')}  {msg}\n")
            self.log_text.see("end")
        except Exception:
            pass
        self.status_var.set(msg)

    def notify(self, text: str):
        if notification:
            try:
                notification.notify(title="TGCloud", message=text, app_name="TGCloud", timeout=3)
            except Exception:
                pass

    def set_paused_label(self, paused: bool):
        if paused:
            self.pause_state_var.set("Paused (waiting to start next upload)")
            self.pause_btn.config(text="Resume")
        else:
            self.pause_state_var.set("")
            self.pause_btn.config(text="Pause")

    def set_current_upload(self, rel: str, size: int):
        self.current_file_var.set(f"{rel} ({human_size(size)})")
        self.pb["value"] = 0
        self.progress_pct_var.set("0%")
        self.progress_speed_var.set("0 KB/s")
        self.progress_eta_var.set("—")

    def done_current_upload(self):
        # Call this after each file finishes (success or fail)
        self.current_file_var.set("Idle")
        self.pb["value"] = 0
        self.progress_pct_var.set("0%")
        self.progress_speed_var.set("0 KB/s")
        self.progress_eta_var.set("—")

    def update_progress(self, rel: str, pct: int, speed_bps: float, eta_secs: float):
        self.root.after(0, self._update_progress_ui, pct, speed_bps, eta_secs)

    def _update_progress_ui(self, pct: int, speed_bps: float, eta_secs: float):
        self.pb["value"] = max(0, min(100, pct))
        self.progress_pct_var.set(f"{self.pb['value']:.0f}%")
        self.progress_speed_var.set(f"{human_size(int(speed_bps))}/s")
        self.progress_eta_var.set("—" if (eta_secs <= 0 or math.isinf(eta_secs) or math.isnan(eta_secs)) else f"{int(eta_secs)//60:02d}:{int(eta_secs)%60:02d}")

    def refresh_table(self):
        for i in self.table.get_children():
            self.table.delete(i)
        items = sorted(self.meta.files.items(), key=lambda kv: kv[0].lower())
        for rel, fm in items:
            self.table.insert("", "end", values=(rel, human_size(fm.size), fm.status, fm.via or "-"))

        # ---------- downloads ----------
    def on_download_selected(self):
        """Download a single selected file asynchronously with progress updates."""
        rel = self._get_selected_rel()
        if not rel:
            messagebox.showinfo("No selection", "Select a file in the list first.")
            return
        fm = self.meta.files.get(rel)
        if not fm:
            messagebox.showerror("Not tracked", "This item is not in metadata.")
            return
        if fm.status != "uploaded":
            messagebox.showerror("Not uploaded", "This item isn't uploaded yet.")
            return

        DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
        dest = DOWNLOAD_DIR / Path(rel).name
        self.log(f"Downloading {rel} → {dest.name} …")

        # progress callback for per-second updates
        def _progress(current, total, speed, eta):
            pct = int(current * 100 / total) if total else 0
            self.update_progress(rel, pct, speed, eta)

        # background thread for the download (avoid freezing UI)
        def _worker():
            ok = self.tg.download(fm, dest, progress_cb=_progress)
            self.root.after(0, self._after_download, rel, dest, ok)

        threading.Thread(target=_worker, daemon=True).start()

    def _after_download(self, rel, dest, ok):
        """Called when single download finishes."""
        if not ok:
            self.log(f"Download failed for {rel}.")
            messagebox.showerror("Download failed", "Could not download via bot or user API. Check Settings & chat_id.")
            self.done_current_upload()
            return

        self.done_current_upload()
        self.log(f"Saved to {dest}")
        self.notify(f"Downloaded {dest.name}")
    def on_download_all(self):
        """Sequentially download all uploaded files (1 by 1) with live progress."""
        uploaded_items = [(rel, fm) for rel, fm in self.meta.files.items() if fm.status == "uploaded"]
        if not uploaded_items:
            messagebox.showinfo("Nothing to download", "No uploaded files found in metadata.")
            return

        ts = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
        dest_dir = BACKUP_DIR / f"restore_{ts}"
        dest_dir.mkdir(parents=True, exist_ok=True)

        total_files = len(uploaded_items)
        total_bytes = sum(fm.size for _, fm in uploaded_items)
        self.log(f"Sequential download: {total_files} files ({human_size(total_bytes)}) → {dest_dir.name}")

        done = {"ok": 0, "fail": 0, "bytes": 0}
        start = time.time()

        def _worker():
            for idx, (rel, fm) in enumerate(uploaded_items, start=1):
                dest = dest_dir / Path(rel).name
                self.log(f"[{idx}/{total_files}] Downloading {rel} → {dest.name}")

                def _progress(current, total, speed, eta):
                    pct = int(current * 100 / total) if total else 0
                    self.update_progress(rel, pct, speed, eta)

                ok = self.tg.download(fm, dest, progress_cb=_progress)
                if ok:
                    done["ok"] += 1
                    done["bytes"] += fm.size
                    self.log(f"✓ {rel} ({human_size(fm.size)})")
                else:
                    done["fail"] += 1
                    self.log(f"✗ {rel} (download failed)")
                self.done_current_upload()

            elapsed = time.time() - start
            mbps = (done["bytes"] / 1024 / 1024) / elapsed if elapsed > 0 else 0
            msg = (f"Downloaded {done['ok']}/{total_files} files "
                   f"({human_size(done['bytes'])}/{human_size(total_bytes)}) "
                   f"in {int(elapsed)//60}m {int(elapsed)%60}s  ~{mbps:.2f} MB/s")
            self.log(msg)
            self.notify(msg)
            messagebox.showinfo("Download complete", msg)
            self.done_current_upload()

        threading.Thread(target=_worker, daemon=True).start()
    # ---------- pause ----------
    def on_pause_resume(self):
        if not self.pool:
            return
        if self.pool.paused.is_set():
            self.pool.resume()
        else:
            self.pool.pause()
    
        # ---------- backup ----------
    def on_backup_now(self):
        """Create a ZIP backup of metadata and enqueue it for upload."""
        try:
            from ..core.backup import create_zip_backup
            zip_path = create_zip_backup(self.meta, refresh_table_cb=self.refresh_table)
            if not zip_path:
                self.log("Backup creation skipped or failed.")
                return
            self.log(f"Backup created: {zip_path.name}")
            if self.pool:
                self.pool.enqueue(zip_path)
                self.log(f"Backup {zip_path.name} enqueued for upload.")
        except Exception as e:
            self.log(f"Backup error: {e}")
            import traceback
            traceback.print_exc()
            from tkinter import messagebox
            messagebox.showerror("Backup error", str(e))

    # ---------- end of class ----------
