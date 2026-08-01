import asyncio
import json
import time
import logging
from os import getenv

from pyrogram import Client, filters
from pyrogram.enums import ChatMemberStatus
from pyrogram.errors import FloodWait, UserAlreadyParticipant, UserNotParticipant
from pyrogram.types import Message, ChatPrivileges
from pyrogram.handlers import MessageHandler

# ================= CONFIG ================= #

API_ID = int(getenv("API_ID"))
API_HASH = getenv("API_HASH")
BOT_TOKEN = getenv("BOT_TOKEN")
USERBOT_STRING = getenv("USERBOT_STRING")

MSG_ID = {25864, 96066, 236364}

WHITELIST_USERS = {
    6804133304,
    6446224566
}

PROGRESS_FILE = "progress.json"
CANCEL_TASKS = set()

# ================= LOGGING ================= #

logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)

# ================= CLIENT placeholders ================= #

bot: Client = None
userbot: Client = None

# ================= HELPERS ================= #

def progress_bar(percent):
    total = 20
    filled = int((percent / 100) * total)
    return "▓" * filled + "░" * (total - filled)

def format_eta(seconds):
    m, s = divmod(int(seconds), 60)
    h, m = divmod(m, 60)
    return f"{h}h {m}m {s}s"

def load_progress():
    try:
        with open(PROGRESS_FILE, "r") as f:
            return json.load(f)
    except:
        return {}

def save_progress(data):
    try:
        with open(PROGRESS_FILE, "w") as f:
            json.dump(data, f)
    except Exception as e:
        log.error(f"Failed to save progress: {e}")

async def get_last_message_id(client, chat_id):
    async for m in client.get_chat_history(chat_id, limit=1):
        return m.id
    return 0

# ================= HANDLERS ================= #

async def cancel_handler(_, msg: Message):
    CANCEL_TASKS.add(msg.chat.id)
    await msg.reply("⛔ Deletion cancelled")


async def delete_all_handler(client: Client, msg: Message):
    chat_id = msg.chat.id

    # -------- USER ADMIN CHECK -------- #

    member = await client.get_chat_member(chat_id, msg.from_user.id)
    if member.status not in (ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.OWNER):
        return await msg.reply("❌ Admin only")

    if not member.privileges or not member.privileges.can_delete_messages:
        return await msg.reply("❌ No delete permission")

    # -------- BOT ADMIN CHECK -------- #

    bot_member = await client.get_chat_member(chat_id, (await client.get_me()).id)
    priv = bot_member.privileges

    if bot_member.status != ChatMemberStatus.ADMINISTRATOR:
        return await msg.reply("❌ Bot is not admin")

    if not priv or not (
        priv.can_delete_messages and
        priv.can_invite_users and
        priv.can_promote_members
    ):
        return await msg.reply("❌ Bot lacks required permissions")

    status = await msg.reply("🧹 Preparing deletion...")

    # -------- USERBOT SETUP -------- #

    userbot_id = (await userbot.get_me()).id
    need_leave = False
    need_demote = False

    try:
        ub = await client.get_chat_member(chat_id, userbot_id)
        if ub.status != ChatMemberStatus.ADMINISTRATOR:
            await client.promote_chat_member(
                chat_id,
                userbot_id,
                ChatPrivileges(can_delete_messages=True)
            )
            need_demote = True
    except UserNotParticipant:
        invite = await client.create_chat_invite_link(chat_id)
        await userbot.join_chat(invite.invite_link)
        await client.promote_chat_member(
            chat_id,
            userbot_id,
            ChatPrivileges(can_delete_messages=True)
        )
        need_leave = True

    # -------- LOAD PROGRESS -------- #

    progress = load_progress().get(str(chat_id), {})
    last_id = progress.get("last_id", 0)
    user_stats = progress.get("users", {})

    # -------- TOTAL COUNT (ONCE) -------- #

    last_msg_id = await get_last_message_id(userbot, chat_id)
    total = max(0, last_msg_id - last_id)

    if total == 0:
        return await status.edit("ℹ️ Nothing to delete")

    start_time = time.time()
    deleted = 0
    skipped = 0
    last_percent = -1

    # -------- DELETE LOOP (SINGLE DELETE) -------- #

    async for m in userbot.get_chat_history(chat_id):

        if chat_id in CANCEL_TASKS:
            await status.edit("⛔ Cancelled")
            CANCEL_TASKS.remove(chat_id)
            break

        if m.id <= last_id:
            break
            
        if m.id == status.id or m.id in MSG_ID:
            continue

        if not m.from_user:
            continue

        if m.from_user.id in WHITELIST_USERS:
            skipped += 1
            continue

        try:
            await userbot.delete_messages(chat_id, m.id)
            deleted += 1
        except FloodWait as e:
            await asyncio.sleep(e.value)
        except Exception as e:
            log.error(f"Failed to delete message {m.id}: {e}")
            continue

        uid = str(m.from_user.id)
        user_stats[uid] = user_stats.get(uid, 0) + 1

        if deleted % 1000 == 0:
            percent = min(100, int((deleted / total) * 100))
            if percent >= last_percent + 5:
                last_percent = percent
                elapsed = time.time() - start_time
                speed = deleted / elapsed if elapsed else 0
                eta = (total - deleted) / speed if speed else 0

                try:
                    await status.edit(
                        f"🧹 Deleting...\n\n"
                        f"{progress_bar(percent)} {percent}%\n"
                        f"🗑 Deleted: {deleted}\n"
                        f"⏭ Skipped: {skipped}\n"
                        f"⏳ ETA: {format_eta(eta)}"
                    )
                except FloodWait as e:
                    await asyncio.sleep(e.value)

        save_progress({
            str(chat_id): {
                "last_id": m.id,
                "users": user_stats
            }
        })

    # -------- FINAL -------- #

    await status.edit(
        f"✅ Done\n\n"
        f"🗑 Deleted: {deleted}\n"
        f"⏭ Skipped: {skipped}"
    )

    # -------- CLEANUP -------- #

    if need_demote:
        try:
            await client.promote_chat_member(chat_id, userbot_id, ChatPrivileges())
        except Exception as e:
            log.error(f"Failed to demote userbot during cleanup: {e}")

    if need_leave:
        try:
            await userbot.leave_chat(chat_id)
        except Exception as e:
            log.error(f"Failed to make userbot leave during cleanup: {e}")

# ================= RUN ================= #

async def main():
    global bot, userbot
    
    # Capture the explicitly active loop generated by asyncio.run()
    loop = asyncio.get_running_loop()
    
    # Initialize clients bound directly to this loop
    bot = Client(
        "delete_bot",
        api_id=API_ID,
        api_hash=API_HASH,
        bot_token=BOT_TOKEN,
        loop=loop
    )

    userbot = Client(
        "delete_userbot",
        api_id=API_ID,
        api_hash=API_HASH,
        session_string=USERBOT_STRING,
        loop=loop
    )
    
    # Safely assign functional routes 
    bot.add_handler(MessageHandler(cancel_handler, filters.command("cancel") & filters.group))
    bot.add_handler(MessageHandler(delete_all_handler, filters.command("delall") & filters.group))

    await userbot.start()
    await bot.start()
    
    log.info("Kurigram context started successfully.")
    await asyncio.Event().wait()

if __name__ == "__main__":
    asyncio.run(main())
