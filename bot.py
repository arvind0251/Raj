import os
import asyncio
import logging
from datetime import datetime

import requests
from dotenv import load_dotenv
from pymongo import MongoClient
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

load_dotenv()

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

BOT_TOKEN = os.getenv("BOT_TOKEN")
OWNER_ID = int(os.getenv("OWNER_ID", "7302427268"))
API_URL = os.getenv("API_URL", "https://project-fawn-eight-95.vercel.app/tg2phone/api")
API_KEY = os.getenv("API_KEY", "Smoke")
MONGO_URI = os.getenv("MONGO_URI")
DB_NAME = os.getenv("DB_NAME", "telegram_bot")
DAILY_LIMIT = int(os.getenv("DAILY_LIMIT", "10"))

if not BOT_TOKEN or not MONGO_URI:
    raise RuntimeError("BOT_TOKEN and MONGO_URI must be set in .env")

mongo = MongoClient(MONGO_URI)
db = mongo[DB_NAME]
users = db.users

WAITING_INPUT = 1
WAITING_BROADCAST = 2

def today():
    return datetime.utcnow().strftime("%Y-%m-%d")

def register_user(user_id: int, username: str | None = None):
    users.update_one(
        {"_id": user_id},
        {
            "$setOnInsert": {
                "_id": user_id,
                "first_seen": today(),
                "count": 0,
                "date": today(),
            },
            "$set": {
                "username": username,
                "last_seen": today(),
            },
        },
        upsert=True,
    )

def can_use(user_id: int) -> bool:
    if user_id == OWNER_ID:
        return True

    data = users.find_one({"_id": user_id})
    if not data:
        users.insert_one(
            {
                "_id": user_id,
                "date": today(),
                "count": 1,
                "first_seen": today(),
                "last_seen": today(),
            }
        )
        return True

    if data.get("date") != today():
        users.update_one(
            {"_id": user_id},
            {"$set": {"date": today(), "count": 1, "last_seen": today()}}
        )
        return True

    if data.get("count", 0) >= DAILY_LIMIT:
        return False

    users.update_one(
        {"_id": user_id},
        {"$inc": {"count": 1}, "$set": {"last_seen": today()}}
    )
    return True

def remaining_uses(user_id: int) -> int:
    if user_id == OWNER_ID:
        return 9999
    data = users.find_one({"_id": user_id})
    if not data or data.get("date") != today():
        return DAILY_LIMIT
    return max(0, DAILY_LIMIT - data.get("count", 0))

def lookup(query: str):
    try:
        r = requests.get(API_URL, params={"key": API_KEY, "q": query}, timeout=20)
        r.raise_for_status()
        try:
            return r.json()
        except ValueError:
            return r.text
    except requests.exceptions.RequestException as e:
        return {"ok": False, "error": str(e)}

def format_result(result):
    if isinstance(result, dict):
        if result.get("ok") is False and "error" in result:
            return f"Error: {result['error']}"
        if "result" in result:
            return str(result["result"])
        return "\n".join([f"{k}: {v}" for k, v in result.items()])
    return str(result)

def home_menu(is_owner: bool = False):
    rows = [
        [InlineKeyboardButton("Search", callback_data="search"), InlineKeyboardButton("Limit", callback_data="limit")],
        [InlineKeyboardButton("Help", callback_data="help"), InlineKeyboardButton("About", callback_data="about")],
        [InlineKeyboardButton("Stats", callback_data="stats")],
    ]
    if is_owner:
        rows.insert(2, [InlineKeyboardButton("Broadcast", callback_data="broadcast")])
    return InlineKeyboardMarkup(rows)

def back_menu():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("Back", callback_data="back"), InlineKeyboardButton("Cancel", callback_data="cancel")]
    ])

def result_menu():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("Search Again", callback_data="search"), InlineKeyboardButton("Home", callback_data="back")],
        [InlineKeyboardButton("Cancel", callback_data="cancel")]
    ])

def broadcast_menu():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("Send", callback_data="broadcast_send"), InlineKeyboardButton("Cancel", callback_data="cancel")]
    ])

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = update.effective_user
    register_user(u.id, u.username)
    text = (
        f"Hello, {u.first_name}.\n\n"
        f"Daily limit: {DAILY_LIMIT} uses.\n"
        f"Remaining today: {remaining_uses(u.id) if u.id != OWNER_ID else 'Unlimited'}"
    )
    await update.message.reply_text(text, reply_markup=home_menu(u.id == OWNER_ID))

async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = "Help:\n\nSearch, Limit, Stats, About, Broadcast, Back, Cancel."
    if update.message:
        await update.message.reply_text(text, reply_markup=back_menu())
    else:
        await update.callback_query.message.reply_text(text, reply_markup=back_menu())

async def about_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = "About:\n\nPolished UI bot with MongoDB, daily limit, broadcast, and stats."
    if update.message:
        await update.message.reply_text(text, reply_markup=back_menu())
    else:
        await update.callback_query.message.reply_text(text, reply_markup=back_menu())

async def limit_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    text = "Owner has unlimited usage." if uid == OWNER_ID else f"Remaining uses today: {remaining_uses(uid)}"
    if update.message:
        await update.message.reply_text(text, reply_markup=back_menu())
    else:
        await update.callback_query.message.reply_text(text, reply_markup=back_menu())

async def stats_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    total_users = users.count_documents({})
    active_today = users.count_documents({"date": today()})
    limit_hit = users.count_documents({"date": today(), "count": {"$gte": DAILY_LIMIT}})
    text = (
        "Stats:\n\n"
        f"Total users: {total_users}\n"
        f"Active today: {active_today}\n"
        f"Users at limit: {limit_hit}\n"
        f"Your remaining: {remaining_uses(uid) if uid != OWNER_ID else 'Unlimited'}"
    )
    if update.message:
        await update.message.reply_text(text, reply_markup=back_menu())
    else:
        await update.callback_query.message.reply_text(text, reply_markup=back_menu())

async def search_entry(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    await q.message.reply_text("Send your query now.", reply_markup=back_menu())
    return WAITING_INPUT

async def handle_query(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = update.effective_user
    register_user(u.id, u.username)

    query_text = update.message.text.strip()
    if not query_text:
        await update.message.reply_text("Please send a valid query.", reply_markup=back_menu())
        return WAITING_INPUT

    if not can_use(u.id):
        await update.message.reply_text(
            "Daily limit reached. Try again tomorrow.",
            reply_markup=home_menu(u.id == OWNER_ID)
        )
        return ConversationHandler.END

    msg = await update.message.reply_text("Searching...")
    result = await asyncio.to_thread(lookup, query_text)
    text = format_result(result)

    try:
        await msg.edit_text(text[:3900], reply_markup=result_menu())
    except Exception:
        await update.message.reply_text(text[:3900], reply_markup=result_menu())

    return ConversationHandler.END

async def broadcast_entry(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    if q.from_user.id != OWNER_ID:
        await q.message.reply_text("Owner only feature.", reply_markup=home_menu(False))
        return ConversationHandler.END
    await q.message.reply_text("Send broadcast text now.", reply_markup=broadcast_menu())
    return WAITING_BROADCAST

async def handle_broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID:
        await update.message.reply_text("Owner only feature.")
        return ConversationHandler.END

    text = update.message.text.strip()
    if not text:
        await update.message.reply_text("Broadcast text cannot be empty.", reply_markup=broadcast_menu())
        return WAITING_BROADCAST

    all_users = list(users.find({}, {"_id": 1}))
    sent, failed = 0, 0

    status = await update.message.reply_text(f"Broadcasting to {len(all_users)} users...")
    for doc in all_users:
        try:
            await context.bot.send_message(chat_id=doc["_id"], text=text)
            sent += 1
            # Add small delay to avoid rate limiting
            await asyncio.sleep(0.1)
        except Exception as e:
            logger.error(f"Failed to send to {doc['_id']}: {e}")
            failed += 1

    await status.edit_text(
        f"Broadcast complete.\nSent: {sent}\nFailed: {failed}",
        reply_markup=home_menu(True)
    )
    return ConversationHandler.END

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message:
        await update.message.reply_text(
            "Cancelled.",
            reply_markup=home_menu(update.effective_user.id == OWNER_ID)
        )
    elif update.callback_query:
        await update.callback_query.message.reply_text(
            "Cancelled.",
            reply_markup=home_menu(update.callback_query.from_user.id == OWNER_ID)
        )
    return ConversationHandler.END

async def button_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    uid = q.from_user.id

    if q.data == "help":
        await q.message.reply_text(
            "Help:\n\nSearch, Limit, Stats, About, Broadcast, Back, Cancel.",
            reply_markup=back_menu()
        )
    elif q.data == "limit":
        text = "Owner has unlimited usage." if uid == OWNER_ID else f"Remaining uses today: {remaining_uses(uid)}"
        await q.message.reply_text(text, reply_markup=back_menu())
    elif q.data == "stats":
        total_users = users.count_documents({})
        active_today = users.count_documents({"date": today()})
        limit_hit = users.count_documents({"date": today(), "count": {"$gte": DAILY_LIMIT}})
        text = (
            "Stats:\n\n"
            f"Total users: {total_users}\n"
            f"Active today: {active_today}\n"
            f"Users at limit: {limit_hit}\n"
            f"Your remaining: {remaining_uses(uid) if uid != OWNER_ID else 'Unlimited'}"
        )
        await q.message.reply_text(text, reply_markup=back_menu())
    elif q.data == "about":
        await q.message.reply_text(
            "About:\n\nPolished UI bot with MongoDB, daily limit, broadcast, and stats.",
            reply_markup=back_menu()
        )
    elif q.data == "back":
        await q.message.reply_text(
            f"Home menu.\nRemaining today: {remaining_uses(uid) if uid != OWNER_ID else 'Unlimited'}",
            reply_markup=home_menu(uid == OWNER_ID)
        )
    elif q.data == "cancel":
        await q.message.reply_text("Cancelled.", reply_markup=home_menu(uid == OWNER_ID))

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    logger.exception("Unhandled error: %s", context.error)

def main():
    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("limit", limit_cmd))
    app.add_handler(CommandHandler("stats", stats_cmd))
    app.add_handler(CommandHandler("about", about_cmd))

    app.add_handler(ConversationHandler(
        entry_points=[CallbackQueryHandler(search_entry, pattern="^search$")],
        states={WAITING_INPUT: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_query)]},
        fallbacks=[CommandHandler("cancel", cancel), CallbackQueryHandler(cancel, pattern="^cancel$")],
    ))

    app.add_handler(ConversationHandler(
        entry_points=[CallbackQueryHandler(broadcast_entry, pattern="^broadcast$")],
        states={WAITING_BROADCAST: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_broadcast)]},
        fallbacks=[CommandHandler("cancel", cancel), CallbackQueryHandler(cancel, pattern="^cancel$")],
    ))

    app.add_handler(CallbackQueryHandler(button_router))
    app.add_error_handler(error_handler)
    app.run_polling()

if __name__ == "__main__":
    main()
