import os
from pathlib import Path

APP_NAME = "TGCloud"
WORK_DIR = Path(os.getcwd())
CLOUD_DIR = WORK_DIR / "MyCloudData"
DOWNLOAD_DIR = WORK_DIR / "tgdownloaded"
LOG_DIR = WORK_DIR / "logs"
BACKUP_DIR = WORK_DIR / "backups"
CREDENTIALS_FILE = WORK_DIR / "credentials.json"
METADATA_FILE = WORK_DIR / "metadata.json"
