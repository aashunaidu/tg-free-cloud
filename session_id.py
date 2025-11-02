from pyrogram import Client

api_id = int(input("API ID: "))
api_hash = input("API Hash: ")

with Client("tgcloud_user", api_id=api_id, api_hash=api_hash) as app:
    print("\n✅ Your session string:\n")
    print(app.export_session_string())
