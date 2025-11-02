from watchdog.events import FileSystemEventHandler
from pathlib import Path

class FolderEventHandler(FileSystemEventHandler):
    def __init__(self, pool): self.pool = pool
    def on_created(self, event):
        if not event.is_directory: self.pool.enqueue(Path(event.src_path))
    def on_modified(self, event):
        if not event.is_directory: self.pool.enqueue(Path(event.src_path))
