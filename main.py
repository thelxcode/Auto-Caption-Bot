import asyncio
import json
import time
import logging
from os import getenv

from pyrogram import Client, filters
from pyrogram.enums import ChatMemberStatus
from pyrogram.errors import FloodWait, UserNotParticipant
from pyrogram.types import Message, ChatPrivileges

# ================= CONFIG ================= #

API_ID = int(getenv("API_ID", 0))
API_HASH = getenv("API_HASH", "")
BOT_TOKEN = getenv("BOT_TOKEN", "")
USERBOT_STRING = getenv("USERBOT_STRING", "")

MSG_ID = {25864, 96066, 231095}

WHITELIST_USERS = {
    6804133304,
    6446224566
}

PROGRESS_FILE = "progress.json"
CANCEL_TASKS = set()

# ================= LOGGING ================= #

logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)

# ================= CLIENTS ================= #

bot = Client(
    "delete_bot",
    api_id=API_ID,
    api_hash=API_HASH,
    bot_token=BOT_TOKEN
)

userbot = Client(
    "delete_userbot",
    api_id=API_ID,
    api_hash=API_HASH,
    session_string=USERBOT_STRING
)

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

def save_progress(chat_id, last_id, user_stats):
    # Reads and updates safely to avoid overwriting other group data
    all_data = load_progress()
    all_data[str(chat_id)] = {
        "last_id": last_id,
        "users": user_stats
    }
    try:
        with open(PROGRESS_FILE, "w") as f:
            json.dump(all_data, f, indent=4)
    except Exception as e:
        log.error(f"Failed to save progress: {e}")

async def get_last_message_id(client, chat_id):
    async for m in client.get_chat_history(chat_id, limit=1):
        return m.id
    return 0

# ================= CANCEL ================= #

@bot.on_message(filters.command("cancel") & filters.group)
async def cancel_handler(_, msg: Message):
    CANCEL_TASKS.add(msg.chat.id)
    await msg.reply("⛔ Deletion cancelled")

# ================= DELETE ================= #

@bot.on_message(filters.command("delall") & filters.group)
async def delete_all_handler(client: Client, msg: Message):
    chat_id = msg.chat.id

    # Remove previous cancel tokens if any
    CANCEL_TASKS.discard(chat_id)

    # -------- USER ADMIN CHECK -------- #
    member = await client.get_chat_member(chat_id, msg.from_user.id)
    if member.status not in (ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.OWNER):
        return await msg.reply("❌ Admin only")

    if member.status == ChatMemberStatus.ADMINISTRATOR and (not member.privileges or not member.privileges.can_delete_messages):
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
        try:
            invite = await client.create_chat_invite_link(chat_id)
            await userbot.join_chat(invite.invite_link)
            await client.promote_chat_member(
                chat_id,
                userbot_id,
                ChatPrivileges(can_delete_messages=True)
            )
            need_leave = True
        except Exception as e:
            return await status.edit(f"❌ Userbot setup failed: {str(e)}")

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
    processed = 0
    last_percent = -1
    
    delete_queue = []
    current_last_id = last_id

    # -------- DELETE LOOP (BULK DELETION) -------- #
    async for m in userbot.get_chat_history(chat_id):
        if chat_id in CANCEL_TASKS:
            await status.edit("⛔ Cancelled")
            break

        if m.id <= last_id:
            break
            
        processed += 1
        current_last_id = m.id

        if m.id in MSG_ID:
            continue

        if not m.from_user:
            continue

        if m.from_user.id in WHITELIST_USERS:
            skipped += 1
            continue

        delete_queue.append(m.id)
        uid = str(m.from_user.id)
        user_stats[uid] = user_stats.get(uid, 0) + 1

        # Execute chunked deletion when queue hits 100
        if len(delete_queue) >= 100:
            try:
                await userbot.delete_messages(chat_id, delete_queue)
                deleted += len(delete_queue)
                delete_queue.clear()
            except FloodWait as e:
                await asyncio.sleep(e.value)
                # Retry once after sleep
                await userbot.delete_messages(chat_id, delete_queue)
                deleted += len(delete_queue)
                delete_queue.clear()

        # Update stats visually periodically based on processed elements
        if processed % 500 == 0:
            percent = min(100, int((processed / total) * 100))
            if percent >= last_percent + 5:
                last_percent = percent
                elapsed = time.time() - start_time
                speed = processed / elapsed if elapsed else 0
                eta = (total - processed) / speed if speed else 0

                try:
                    await status.edit(
                        f"🧹 Deleting...\n\n"
                        f"{progress_bar(percent)} {percent}%\n"
                        f"🗑 Deleted: {deleted + len(delete_queue)}\n"
                        f"⏭ Skipped: {skipped}\n"
                        f"⏳ ETA: {format_eta(eta)}"
                    )
                except Exception:
                    pass # Ignore occasional telegram edit rate limit errors

            save_progress(chat_id, current_last_id, user_stats)

    # Clear remaining queue elements
    if delete_queue and chat_id not in CANCEL_TASKS:
        try:
            await userbot.delete_messages(chat_id, delete_queue)
            deleted += len(delete_queue)
        except Exception:
            pass

    save_progress(chat_id, current_last_id, user_stats)

    # -------- FINAL -------- #
    await status.edit(
        f"✅ Done\n\n"
        f"🗑 Total Deleted: {deleted}\n"
        f"⏭ Skipped: {skipped}"
    )

    # -------- CLEANUP -------- #
    if need_demote:
        try:
            await client.promote_chat_member(chat_id, userbot_id, ChatPrivileges())
        except Exception:
            pass

    if need_leave:
        try:
            await userbot.leave_chat(chat_id)
        except Exception:
            pass

# ================= RUN ================= #

async def main():
    await userbot.start()
    await bot.start()
    log.info("Clients initialized successfully. Sleeping on Event...")
    await asyncio.Event().wait()

if __name__ == "__main__":
    asyncio.run(main())
