import json
from .paths import CREDENTIALS_FILE, CLOUD_DIR, DOWNLOAD_DIR, LOG_DIR, BACKUP_DIR

DEFAULT_CONFIG = {
    "bot_token": "",
    "chat_id": "",
    "api_id": "",
    "api_hash": "",
    "user_session_string": "",
    "enable_2gb_mode": False,
    "encryption_enabled": False,
    "encryption_passphrase": "",
    "rate_limit_seconds": 0.5,
    "autosync": True,
    "max_file_mb": 48,
    "num_workers": 3,
    "download_workers": 3,
    "daily_backup_time": "02:00",
    "use_sha256": False,
    "force_user_api": True,
    "preferred_python": "py -3.11"
}

def ensure_dirs():
    for p in [CLOUD_DIR, DOWNLOAD_DIR, LOG_DIR, BACKUP_DIR]:
        p.mkdir(parents=True, exist_ok=True)

def save_config(cfg: dict):
    with open(CREDENTIALS_FILE, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2)

def load_or_create_config(prompt_cb=None) -> dict:
    ensure_dirs()
    if not CREDENTIALS_FILE.exists():
        cfg = DEFAULT_CONFIG.copy()
        if prompt_cb:
            prompt_cb(cfg)
        return cfg
    with open(CREDENTIALS_FILE, "r", encoding="utf-8") as f:
        cfg = json.load(f)
    for k, v in DEFAULT_CONFIG.items():
        if k not in cfg: cfg[k] = v
    return cfg
