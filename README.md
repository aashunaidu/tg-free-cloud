# 🧠 TGCloud – Telegram‑Based Cloud Backup (Student Project)

> **Built by a Python student — functional, useful, and still evolving.**  
> TGCloud is a personal sync/backup tool that uses **Telegram** (via Bot API or User API) as your **storage backend**.  
> It can auto‑sync folders, split and zip large directories, and upload/download files from a clean Tkinter GUI.

---

## ✨ Highlights

- 📂 **Auto‑sync** local folders (defaults to `MyCloudData/`)
- 🧾 **ZIP & Restore** backups (single ZIP or split parts)
- 📦 **Auto‑split** large folders into ≤ **1.9 GB** archive parts
- 📤 Upload via **Bot API (≤ ~50 MB)** or **User API (≤ 2 GB)** per file
- 🧠 Real‑time GUI progress (speed • ETA • % complete)
- 🪟 Cross‑platform (Windows/Linux) — **Python 3.11** recommended
- 🧑‍💻 Learning‑in‑public: **works today** but has **known bugs** — PRs welcome!

---

## 🗂 Default Sync Folder

By default, TGCloud watches and syncs the folder:
```
MyCloudData/
```
Anything you drop here will be detected and uploaded (subject to your settings).  
You can add more folders from **GUI → Add Sync Folder…**

---

## 🏗 Project Structure

```
tgcloud_modular/
│
├── MyCloudData/                  # <— Default sync root (watched on startup)
│
├── tgcloud/
│   ├── ui/
│   │   └── gui.py                # Tkinter dashboard, tables, progress bars
│   ├── core/
│   │   ├── dual_client.py        # Bot + User API logic (upload/download)
│   │   ├── folder_packer.py      # Auto‑ZIP into ≤1.9 GB parts (multithread)
│   │   ├── backup.py             # Scheduled & on‑demand backups
│   │   └── models.py             # Metadata (sizes, timestamps, status)
│   ├── paths.py                  # Centralized paths and directories
│   └── config.py                 # Settings load/save (JSON)
│
├── session_id.py                 # Generate Pyrogram session string (User API)
├── run.py                        # Entry point
└── requirements.txt
```

---

## ⚙️ Installation

### 1) Prerequisites
- **Python 3.11**
- A **Telegram** account
- Command‑line basics

### 2) Install dependencies
```bash
pip install -r requirements.txt
```
If `requirements.txt` is missing, install manually:
```bash
pip install pyrogram tgcrypto requests pillow plyer tqdm
```

### 3) Launch
Windows (recommended command):
```bash
py -3.11 run.py
```
Linux/macOS:
```bash
python3.11 run.py
```

---

## 🔐 Telegram Setup (Two Modes)

TGCloud supports **two** upload modes. You can enable either or both.

### 1) Bot Mode (simple; ~50 MB/file)
Best for small files and quick automation.

**Steps:**
1. Open **@BotFather** in Telegram → `/newbot` → follow prompts.  
2. Copy your **Bot Token** (looks like `123456:AA...`).
3. Create a **private channel** or chat to store files.
4. Add your bot to that channel **as Admin**.
5. Get the **Chat ID**:
   - Add **@RawDataBot** or **@userinfobot** in the channel and forward a message, or
   - Use a small script to print `chat.id` after sending a test message.
6. In TGCloud GUI → **Settings → Telegram**, paste **Bot Token** and **Chat ID**.
7. Make sure **“Prefer Bot API”** is enabled when uploading ≤ ~50 MB files.

> ⚠️ Telegram Bot API limits direct uploads to roughly **50 MB** per file. For large files use **2 GB mode** below.

### 2) 2 GB Mode (User API via Pyrogram)
Best for big files (up to **2 GB** per file). Uses your personal account.

**Steps:**
1. Go to **https://my.telegram.org** → **API development tools**.
2. Create a new app, then copy your **API ID** and **API HASH**.
3. In TGCloud GUI → **Settings → Telegram**:
   - **App api_id:** `26320325`  
   - **App api_hash:** `<PASTE_YOUR_API_HASH_HERE>`
4. Click **Generate Session String** (you’ll get a login code in Telegram).  
   If the built‑in generator fails, run the helper:
   ```bash
   py -3.11 session_id.py
   ```
   Paste the long session string into **User Session String**.
5. Enable:
   - ✅ **Enable 2GB Mode**
   - (Optional) ✅ **Force User API**
6. **Restart** TGCloud. You should see `User API client started (2GB mode ON)` in logs.

> 🔒 Keep your **api_hash**, **bot token**, and **session string** private. Do not commit them to Git.

---

## 🧭 Using the App

### GUI Overview
| Section          | Purpose                                                |
|------------------|--------------------------------------------------------|
| Header           | App name, connection state, quick settings             |
| Actions Row      | Open folders, Upload, Add Sync Folder, Backup, ZIP     |
| Progress         | Active job progress (% • speed • ETA)                  |
| File Table       | Tracked files, size, status, upload type               |
| Log Panel        | Events, warnings, and errors                           |
| Status Bar       | Live status, last action                               |

### Common Actions
- **Add Sync Folder…** → choose extra folders in addition to `MyCloudData/`.
- **Upload / Sync** → pushes pending items automatically.
- **📦 Auto ZIP Folder (≤2GB)** → splits huge directories into parts like:
  `DriveBackup_001.zip`, `DriveBackup_002.zip`, … (each ≤ **1.9 GB**).
- **Backup Now** → creates a single ZIP of your sync directory into `/backup/` and queues it for upload.
- **Download** (right‑click a row) or **Download ALL** → restores into `/tgdownloaded/`.

---

## 🧠 Function Reference (Public/Key)

### `auto_zip_folder(src_folder, dest_dir, base_name="DriveBackup")`
- Compresses a folder into multiple ZIP parts (each ≤ **1.9 GB**).
- Multithreaded; progress/ETA piped to GUI.

### `DualTelegramClient.send_document(path, caption, prefer_user, progress_cb)`
- Uploads a file, **auto‑choosing** Bot vs User API based on size & settings.

### `DualTelegramClient.download(fm, dest)`
- Downloads a Telegram file (by file id or message id) to `dest`.

### `MetadataDB.save(path)` / `MetadataDB.load(path)`
- Persists list of tracked files (pending / uploaded / failed).

### `create_zip_backup(meta)`
- Builds a full ZIP of current synced files (usually `MyCloudData/`) and queues upload.

### `TGCloudGUI.refresh_table()`
- Reloads file list and refreshes statuses in the UI table.

### `on_auto_zip_folder()`
- Folder picker → runs `auto_zip_folder` → shows live progress dialog.

### `on_download_all()`
- Sequentially downloads all uploaded items with progress.

---

## 🐞 Known Issues (Student Project)

- Occasional freeze when downloading very large files (> 1 GB).
- Tkinter UI may lag under heavy CPU (zipping + uploading at once).
- Progress window can desync during **very** fast ZIP jobs.
- Some settings require a manual restart to apply.
- Error handling still basic (invalid Telegram credentials can crash).

PRs to improve stability are **very** welcome!

---

## 🧰 Commands Cheat‑Sheet

| Action                         | Command                                      |
|--------------------------------|----------------------------------------------|
| Start app                      | `py -3.11 run.py`                            |
| Generate session (fallback)    | `py -3.11 session_id.py`                     |
| Reinstall deps                 | `pip install -r requirements.txt --force-reinstall` |
| Clean logs & metadata          | Delete `logs/` and `metadata.json`           |

---

## 🔧 Configuration Quick‑Ref

In **Settings → Telegram**:

- **Bot Mode**
  - **Bot Token:** from **@BotFather**
  - **Chat ID:** from your storage channel/chat (see steps above)

- **User API (2 GB Mode)**
  - **App api_id:** `26320325`
  - **App api_hash:** `<YOUR_API_HASH>` (get from **my.telegram.org**)
  - **User Session String:** generated via GUI or `session_id.py`
  - **Enable 2GB Mode:** ✅
  - **Force User API:** (optional) ✅

Other settings live in `tgcloud/config.json` (auto‑created).

---

## 🤝 Contributing

This is a **learning‑in‑public** repository. The code works but has rough edges.  
Please open issues, suggest improvements, or submit PRs — even small ones help!

---

## 📄 License

**MIT** — use, modify, and learn freely. Attribution appreciated. ❤️

---

## 🙌 Final Note

TGCloud started as a simple uploader for class assignments and grew into a real backup tool.  
If you use it, please share your feedback and ideas — that’s how it gets better. 🚀
