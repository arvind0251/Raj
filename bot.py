import os
import sys
import asyncio
import logging
from datetime import datetime

import requests
from dotenv import load_dotenv
from pymongo import MongoClient
from pymongo.errors import ConnectionFailure
from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    ConversationHandler,
    MessageHandler,
    ContextTypes,
    filters,
)
from telegram.error import InvalidToken

# ─────────────────────────────────────────────
# ENV & LOGGING
# ─────────────────────────────────────────────
load_dotenv()

logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(message)s",
    level=logging.INFO,
)
log = logging.getLogger(__name__)

BOT_TOKEN    = os.getenv("BOT_TOKEN")
OWNER_ID     = int(os.getenv("OWNER_ID", "7302427268"))
API_URL      = os.getenv("API_URL", "https://project-fawn-eight-95.vercel.app/tg2phone/api")
API_KEY      = os.getenv("API_KEY", "Smoke")
MONGO_URI    = os.getenv("MONGO_URI")
DB_NAME      = os.getenv("DB_NAME", "telegram_bot")
DAILY_LIMIT  = int(os.getenv("DAILY_LIMIT", "10"))
SHARE_REWARD = int(os.getenv("SHARE_REWARD", "5"))

for name, val in [("BOT_TOKEN", BOT_TOKEN), ("MONGO_URI", MONGO_URI)]:
    if not val:
        print(f"❌ {name} is missing in .env!")
        sys.exit(1)

# ─────────────────────────────────────────────
# MONGODB
# ─────────────────────────────────────────────
try:
    mongo = MongoClient(MONGO_URI, serverSelectionTimeoutMS=5000)
    mongo.admin.command("ping")
    users = mongo[DB_NAME].users
    log.info("✅ MongoDB connected")
except (ConnectionFailure, Exception) as e:
    print(f"❌ MongoDB error: {e}")
    sys.exit(1)

# ─────────────────────────────────────────────
# CONVERSATION STATES
# ─────────────────────────────────────────────
WAITING_SEARCH, WAITING_BROADCAST = range(2)

# ─────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────
def today() -> str:
    return datetime.utcnow().strftime("%Y-%m-%d")

def is_owner(uid: int) -> bool:
    return uid == OWNER_ID

def register_user(uid: int, username: str | None = None):
    users.update_one(
        {"_id": uid},
        {
            "$setOnInsert": {
                "_id": uid,
                "first_seen": today(),
                "count": 0,
                "date": today(),
                "shared_count": 0,
                "bonus_limits": 0,
            },
            "$set": {"username": username, "last_seen": today(), "user_id": uid},
        },
        upsert=True,
    )

def get_user(uid: int) -> dict:
    return users.find_one({"_id": uid}) or {}

def total_limit(uid: int) -> int:
    if is_owner(uid):
        return 9999
    return DAILY_LIMIT + get_user(uid).get("bonus_limits", 0)

def remaining(uid: int) -> int:
    if is_owner(uid):
        return 9999
    data = get_user(uid)
    if not data or data.get("date") != today():
        return total_limit(uid)
    return max(0, total_limit(uid) - data.get("count", 0))

def can_use(uid: int) -> bool:
    if is_owner(uid):
        return True
    data = get_user(uid)
    if not data:
        register_user(uid)
        return True
    if data.get("date") != today():
        users.update_one({"_id": uid}, {"$set": {"date": today(), "count": 1}})
        return True
    if data.get("count", 0) >= total_limit(uid):
        return False
    users.update_one({"_id": uid}, {"$inc": {"count": 1}})
    return True

def add_bonus(uid: int, amount: int) -> bool:
    result = users.update_one(
        {"_id": uid},
        {"$inc": {"bonus_limits": amount, "shared_count": 1}},
    )
    return result.modified_count > 0

# ─────────────────────────────────────────────
# API
# ─────────────────────────────────────────────
def search_api(query: str) -> dict:
    try:
        r = requests.get(
            API_URL,
            params={"key": API_KEY, "q": query},
            timeout=30,
        )
        r.raise_for_status()
        return r.json()
    except requests.Timeout:
        return {"ok": False, "error": "Request timed out. Try again."}
    except requests.ConnectionError:
        return {"ok": False, "error": "Cannot reach API server."}
    except Exception as e:
        return {"ok": False, "error": str(e)}

def format_result(data: dict | list | str) -> str:
    if isinstance(data, dict):
        if data.get("ok") is False:
            return f"❌ {data.get('error', 'Unknown error')}"
        if "result" in data:
            return format_result(data["result"])
        if data:
            return "📊 Result:\n\n" + "\n".join(f"• {k}: {v}" for k, v in data.items())
        return "❌ No data found."
    if isinstance(data, list):
        parts = []
        for item in data:
            if isinstance(item, dict):
                parts.append("\n".join(f"• {k}: {v}" for k, v in item.items()))
            else:
                parts.append(f"• {item}")
        return "✅ Results:\n\n" + "\n---\n".join(parts) if parts else "❌ No results."
    return str(data)

# ─────────────────────────────────────────────
# KEYBOARDS
# ─────────────────────────────────────────────
def kb_home(owner: bool = False) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton("🔍 Search", callback_data="search")],
        [
            InlineKeyboardButton("📊 Limit",        callback_data="limit"),
            InlineKeyboardButton("📢 Share & Earn",  callback_data="share"),
        ],
    ]
    if owner:
        rows.append([
            InlineKeyboardButton("📈 Stats",     callback_data="stats"),
            InlineKeyboardButton("📣 Broadcast", callback_data="broadcast"),
        ])
    rows.append([
        InlineKeyboardButton("❓ Help",  callback_data="help"),
        InlineKeyboardButton("ℹ️ About", callback_data="about"),
    ])
    return InlineKeyboardMarkup(rows)

def kb_back() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("🔙 Back",   callback_data="back"),
        InlineKeyboardButton("❌ Cancel", callback_data="cancel"),
    ]])

def kb_result() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("🔍 Search Again", callback_data="search"),
        InlineKeyboardButton("🏠 Home",         callback_data="back"),
    ]])

# ─────────────────────────────────────────────
# COMMAND HANDLERS
# ─────────────────────────────────────────────
async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    u = update.effective_user
    register_user(u.id, u.username)

    # Referral
    if ctx.args and ctx.args[0].startswith("ref_"):
        try:
            ref_id = int(ctx.args[0][4:])
            if ref_id != u.id and add_bonus(ref_id, SHARE_REWARD):
                await ctx.bot.send_message(
                    ref_id,
                    f"🎉 Koi aapke link se join kiya!\n💰 +{SHARE_REWARD} bonus uses add ho gaye.",
                )
        except ValueError:
            pass

    rem = remaining(u.id)
    tot = total_limit(u.id)
    await update.message.reply_text(
        f"👋 Hey {u.first_name}!\n\n"
        f"📋 Daily limit : {DAILY_LIMIT}\n"
        f"🎁 Bonus limits: {tot - DAILY_LIMIT if not is_owner(u.id) else 0}\n"
        f"✅ Remaining   : {rem if not is_owner(u.id) else '♾️'}\n\n"
        f"🔍 Search button dabao aur User ID ya Username do — phone number milega!\n"
        f"📢 Bot share karo aur {SHARE_REWARD} extra uses pao!",
        reply_markup=kb_home(is_owner(u.id)),
    )

async def cmd_help(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    text = (
        "❓ Help\n\n"
        "🔍 Search       — User ID ya Username se phone dhundho\n"
        "📊 Limit        — Apna daily usage dekho\n"
        "📢 Share & Earn — Bot share karo, extra uses pao\n"
        "ℹ️  About        — Bot ke baare mein\n"
    )
    if is_owner(uid):
        text += "📈 Stats      — Usage statistics\n📣 Broadcast  — Sabko message bhejo\n"
    await update.message.reply_text(text, reply_markup=kb_back())

async def cmd_limit(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if is_owner(uid):
        text = "👑 Owner — unlimited usage."
    else:
        tot = total_limit(uid)
        rem = remaining(uid)
        text = (
            f"📊 Usage\n\n"
            f"📋 Daily limit : {DAILY_LIMIT}\n"
            f"🎁 Bonus       : {tot - DAILY_LIMIT}\n"
            f"📊 Total       : {tot}\n"
            f"✅ Remaining   : {rem}"
        )
    await update.message.reply_text(text, reply_markup=kb_back())

async def cmd_stats(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not is_owner(uid):
        await update.message.reply_text("❌ Owner only.", reply_markup=kb_home())
        return
    await _send_stats(update.message.reply_text)

async def cmd_about(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "ℹ️ About\n\n"
        "🤖 Telegram Search Bot\n"
        "User ID ya Username do — phone number milega!\n\n"
        f"💰 Share reward: {SHARE_REWARD} uses per friend\n"
        "Built with python-telegram-bot ❤️",
        reply_markup=kb_back(),
    )

# ─────────────────────────────────────────────
# SEARCH CONVERSATION
# ─────────────────────────────────────────────
async def search_entry(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    uid = q.from_user.id
    rem = remaining(uid) if not is_owner(uid) else "♾️"
    await q.message.reply_text(
        f"🔍 Search\n\n"
        f"User ID ya Username bhejo:\n\n"
        f"• User ID  → <code>123456789</code>\n"
        f"• Username → <code>@username</code>\n\n"
        f"✅ Remaining uses: {rem}\n\n"
        f"/cancel — wapas jaane ke liye",
        parse_mode="HTML",
        reply_markup=kb_back(),
    )
    return WAITING_SEARCH

async def handle_search(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    u = update.effective_user
    register_user(u.id, u.username)
    query = update.message.text.strip()

    if not can_use(u.id):
        await update.message.reply_text(
            f"❌ Daily limit khatam ho gaya.\n"
            f"💡 Bot share karo aur {SHARE_REWARD} extra uses pao!",
            reply_markup=kb_home(is_owner(u.id)),
        )
        return ConversationHandler.END

    msg = await update.message.reply_text("⏳ Searching…")
    result = await asyncio.to_thread(search_api, query)
    text = format_result(result)
    if len(text) > 3900:
        text = text[:3900] + "\n\n… (truncated)"

    await msg.edit_text(text, reply_markup=kb_result())

    if not is_owner(u.id):
        rem = remaining(u.id)
        await update.message.reply_text(f"✅ Remaining today: {rem}")

    return ConversationHandler.END

# ─────────────────────────────────────────────
# BROADCAST CONVERSATION
# ─────────────────────────────────────────────
async def broadcast_entry(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    if not is_owner(q.from_user.id):
        await q.message.reply_text("❌ Owner only.", reply_markup=kb_home())
        return ConversationHandler.END
    await q.message.reply_text(
        "📣 Woh message bhejo jo sabko jaayega.",
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("❌ Cancel", callback_data="cancel")
        ]]),
    )
    return WAITING_BROADCAST

async def handle_broadcast(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update.effective_user.id):
        return ConversationHandler.END

    text = update.message.text.strip()
    if not text:
        await update.message.reply_text("❌ Message khali nahi ho sakta.")
        return WAITING_BROADCAST

    all_users = list(users.find({}, {"_id": 1}))
    sent = failed = 0
    status = await update.message.reply_text(f"📤 {len(all_users)} users ko bhej raha hoon…")

    for doc in all_users:
        try:
            await ctx.bot.send_message(doc["_id"], text)
            sent += 1
        except Exception:
            failed += 1
        await asyncio.sleep(0.05)

    await status.edit_text(
        f"✅ Broadcast done!\n📤 Sent: {sent}  ❌ Failed: {failed}",
        reply_markup=kb_home(True),
    )
    return ConversationHandler.END

# ─────────────────────────────────────────────
# CANCEL
# ─────────────────────────────────────────────
async def cancel(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    target = update.message or (update.callback_query and update.callback_query.message)
    if target:
        await target.reply_text("❌ Cancelled.", reply_markup=kb_home(is_owner(uid)))
    return ConversationHandler.END

# ─────────────────────────────────────────────
# BUTTON ROUTER
# ─────────────────────────────────────────────
async def _send_stats(send_fn):
    total  = users.count_documents({})
    active = users.count_documents({"date": today()})
    at_lim = users.count_documents({"date": today(), "count": {"$gte": DAILY_LIMIT}})
    shares = users.count_documents({"shared_count": {"$gt": 0}})
    agg    = list(users.aggregate([{"$group": {"_id": None, "t": {"$sum": "$bonus_limits"}}}]))
    bonus  = agg[0]["t"] if agg else 0
    await send_fn(
        f"📈 Stats\n\n"
        f"👥 Total users  : {total}\n"
        f"✅ Active today : {active}\n"
        f"⚠️ At limit     : {at_lim}\n"
        f"🔗 Total shares : {shares}\n"
        f"🎁 Bonus given  : {bonus}",
        reply_markup=kb_home(True),
    )

async def button_router(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q    = update.callback_query
    await q.answer()
    uid  = q.from_user.id
    data = q.data

    if data == "back":
        rem = remaining(uid) if not is_owner(uid) else "♾️"
        tot = total_limit(uid) if not is_owner(uid) else "♾️"
        await q.message.reply_text(
            f"🏠 Home\n\n📊 Total: {tot}  ✅ Remaining: {rem}",
            reply_markup=kb_home(is_owner(uid)),
        )

    elif data == "cancel":
        await q.message.reply_text("❌ Cancelled.", reply_markup=kb_home(is_owner(uid)))

    elif data == "limit":
        if is_owner(uid):
            await q.message.reply_text("👑 Owner — unlimited.", reply_markup=kb_back())
        else:
            tot = total_limit(uid)
            rem = remaining(uid)
            await q.message.reply_text(
                f"📊 Usage\n\n"
                f"📋 Daily : {DAILY_LIMIT}\n"
                f"🎁 Bonus : {tot - DAILY_LIMIT}\n"
                f"📊 Total : {tot}\n"
                f"✅ Left  : {rem}",
                reply_markup=kb_back(),
            )

    elif data == "share":
        bot_info = await ctx.bot.get_me()
        link = f"https://t.me/{bot_info.username}?start=ref_{uid}"
        bonus = total_limit(uid) - DAILY_LIMIT
        await q.message.reply_text(
            f"📢 Share & Earn\n\n"
            f"Apna link share karo aur {SHARE_REWARD} uses pao!\n\n"
            f"🔗 Aapka link:\n`{link}`\n\n"
            f"🎁 Current bonus: {bonus} uses",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🔙 Back", callback_data="back")
            ]]),
        )

    elif data == "stats":
        if not is_owner(uid):
            await q.message.reply_text("❌ Owner only.", reply_markup=kb_home())
            return
        await _send_stats(q.message.reply_text)

    elif data == "help":
        text = (
            "❓ Help\n\n"
            "🔍 Search       — User ID ya Username se phone dhundho\n"
            "📊 Limit        — Daily usage dekho\n"
            "📢 Share & Earn — Extra uses kamao\n"
            "ℹ️  About        — Bot ke baare mein\n"
        )
        if is_owner(uid):
            text += "📈 Stats      — Usage stats\n📣 Broadcast  — Sabko message karo\n"
        await q.message.reply_text(text, reply_markup=kb_back())

    elif data == "about":
        await q.message.reply_text(
            "ℹ️ About\n\n"
            "🤖 Telegram Search Bot\n"
            "User ID ya Username do — phone number milega!\n\n"
            f"💰 Share reward: {SHARE_REWARD} uses per friend\n"
            "Built with python-telegram-bot ❤️",
            reply_markup=kb_back(),
        )

# ─────────────────────────────────────────────
# ERROR HANDLER
# ─────────────────────────────────────────────
async def error_handler(update: object, ctx: ContextTypes.DEFAULT_TYPE):
    log.exception(f"Update error: {ctx.error}")

# ─────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────
def main():
    try:
        app = Application.builder().token(BOT_TOKEN).build()

        # Commands
        app.add_handler(CommandHandler("start", cmd_start))
        app.add_handler(CommandHandler("help",  cmd_help))
        app.add_handler(CommandHandler("limit", cmd_limit))
        app.add_handler(CommandHandler("stats", cmd_stats))
        app.add_handler(CommandHandler("about", cmd_about))

        # Search conversation (button → User ID/Username → result)
        app.add_handler(ConversationHandler(
            entry_points=[CallbackQueryHandler(search_entry, pattern="^search$")],
            states={WAITING_SEARCH: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_search)]},
            fallbacks=[
                CommandHandler("cancel", cancel),
                CallbackQueryHandler(cancel, pattern="^cancel$"),
            ],
        ))

        # Broadcast conversation
        app.add_handler(ConversationHandler(
            entry_points=[CallbackQueryHandler(broadcast_entry, pattern="^broadcast$")],
            states={WAITING_BROADCAST: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_broadcast)]},
            fallbacks=[
                CommandHandler("cancel", cancel),
                CallbackQueryHandler(cancel, pattern="^cancel$"),
            ],
        ))

        # Button router (must be last)
        app.add_handler(CallbackQueryHandler(button_router))
        app.add_error_handler(error_handler)

        log.info("🚀 Bot started!")
        app.run_polling()

    except InvalidToken:
        print("❌ Invalid BOT_TOKEN!")
        sys.exit(1)
    except Exception as e:
        log.exception(f"Startup error: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()
