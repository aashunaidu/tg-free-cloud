import threading
from pathlib import Path
import telebot

class BotThread(threading.Thread):
    def __init__(self, token: str, meta, enqueue_cb, create_backup_cb):
        super().__init__(daemon=True)
        self.meta = meta; self.enqueue_cb = enqueue_cb; self.create_backup_cb = create_backup_cb
        self.bot = telebot.TeleBot(token, parse_mode=None)

        @self.bot.message_handler(commands=["start","help"])
        def _help(m):
            txt=("TGCloud Bot commands:\n"
                 "/list - list files\n"
                 "/download <filename> - resend a file (bot mode only)\n"
                 "/backup - create & upload a zip backup\n"
                 "/status - summary\n")
            self.bot.reply_to(m, txt)

        @self.bot.message_handler(commands=["list"])
        def _list(m):
            lines = [f"- {rel} ({fm.status})" for rel,fm in sorted(self.meta.files.items())]
            if not lines: self.bot.reply_to(m, "No files tracked yet."); return
            chunk,total=[],0
            for line in lines:
                if total+len(line)+1>3900: self.bot.send_message(m.chat.id,"\n".join(chunk)); chunk,total=[],0
                chunk.append(line); total+=len(line)+1
            if chunk: self.bot.send_message(m.chat.id,"\n".join(chunk))

        @self.bot.message_handler(commands=["download"])
        def _download(m):
            args=m.text.split(maxsplit=1)
            if len(args)<2: self.bot.reply_to(m,"Usage: /download <filename>"); return
            name=args[1].strip().lower()
            for rel,fm in self.meta.files.items():
                if Path(rel).name.lower()==name:
                    if fm.file_id:
                        try: self.bot.send_document(m.chat.id, fm.file_id, caption=Path(rel).name)
                        except Exception as e: self.bot.reply_to(m, f"Send failed: {e}")
                    else:
                        self.bot.reply_to(m, "File not available via bot (likely >50MB). Use desktop app to download.")
                    return
            self.bot.reply_to(m,"Not found.")

        @self.bot.message_handler(commands=["backup"])
        def _backup(m):
            try:
                zip_path=self.create_backup_cb(); self.enqueue_cb(zip_path)
                self.bot.reply_to(m, f"Backup enqueued: {Path(zip_path).name}")
            except Exception as e:
                self.bot.reply_to(m, f"Backup error: {e}")

        @self.bot.message_handler(commands=["status"])
        def _status(m):
            total=len(self.meta.files); uploaded=sum(1 for f in self.meta.files.values() if f.status=="uploaded")
            pending=sum(1 for f in self.meta.files.values() if f.status=="pending"); failed=sum(1 for f in self.meta.files.values() if f.status=="failed")
            last=self.meta.last_backup_iso or "—"
            self.bot.reply_to(m, f"Files: {total}\nUploaded: {uploaded}\nPending: {pending}\nFailed: {failed}\nLast Backup: {last}")

    def run(self):
        try: self.bot.infinity_polling(skip_pending=True, timeout=60, long_polling_timeout=50)
        except Exception as e: pass
