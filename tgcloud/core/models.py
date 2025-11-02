from dataclasses import dataclass, field, asdict
from typing import Dict, Optional
from pathlib import Path
import json
import logging


@dataclass
class FileMeta:
    size: int
    mtime: float
    sha256: Optional[str] = None
    message_id: Optional[int] = None
    file_id: Optional[str] = None
    user_message_id: Optional[int] = None
    via: Optional[str] = None
    status: str = "pending"
    sig: Optional[str] = None
    uploaded_at: Optional[str] = None


@dataclass
class MetadataDB:
    files: Dict[str, FileMeta] = field(default_factory=dict)
    last_backup_iso: Optional[str] = None

    # Safe loader: automatically handles missing, empty, or corrupted metadata.json
    @staticmethod
    def load(path: Path) -> "MetadataDB":
        if not path.exists():
            logging.info("Metadata file not found, creating a new one.")
            return MetadataDB()

        try:
            text = path.read_text(encoding="utf-8").strip()
            if not text:
                logging.warning("Metadata file is empty — recreating.")
                return MetadataDB()

            raw = json.loads(text)
            files = {k: FileMeta(**v) for k, v in raw.get("files", {}).items()}
            db = MetadataDB(files=files, last_backup_iso=raw.get("last_backup_iso"))
            logging.info(f"Loaded metadata with {len(files)} tracked file(s).")
            return db

        except json.JSONDecodeError as e:
            logging.error(f"Metadata JSON corrupted ({e}); recreating clean file.")
            backup = path.with_suffix(".corrupt.json")
            try:
                path.replace(backup)
                logging.info(f"Corrupted metadata backed up to {backup.name}")
            except Exception:
                pass
            return MetadataDB()

        except Exception as e:
            logging.exception(f"Unexpected error loading metadata: {e}")
            return MetadataDB()

    def save(self, path: Path):
        try:
            data = {
                "files": {k: asdict(v) for k, v in self.files.items()},
                "last_backup_iso": self.last_backup_iso,
            }
            tmp = path.with_suffix(".tmp")
            tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
            tmp.replace(path)
            logging.debug(f"Metadata saved ({len(self.files)} files).")
        except Exception as e:
            logging.exception(f"Failed to save metadata: {e}")
