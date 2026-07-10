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

# Load environment variables
load_dotenv()

# Logging setup
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Get environment variables
BOT_TOKEN = os.getenv("BOT_TOKEN")
OWNER_ID = int(os.getenv("OWNER_ID", "7302427268"))
API_URL = os.getenv("API_URL", "https://project-fawn-eight-95.vercel.app/tg2phone/api")
API_KEY = os.getenv("API_KEY", "Smoke")
MONGO_URI = os.getenv("MONGO_URI")
DB_NAME = os.getenv("DB_NAME", "telegram_bot")
DAILY_LIMIT = int(os.getenv("DAILY_LIMIT", "10"))

# Debug information
print("=" * 60)
print("🤖 TELEGRAM BOT STARTING...")
print("=" * 60)
print(f"✅ BOT_TOKEN: {'SET' if BOT_TOKEN else '❌ MISSING'}")
print(f"✅ MONGO_URI: {'SET' if MONGO_URI else '❌ MISSING'}")
print(f"✅ OWNER_ID: {OWNER_ID}")
print(f"✅ DAILY_LIMIT: {DAILY_LIMIT}")
print(f"✅ API_URL: {API_URL}")
print("=" * 60)

# Validate required variables
if not BOT_TOKEN:
    print("❌ ERROR: BOT_TOKEN is not set in .env file!")
    print("Please add BOT_TOKEN=your_telegram_bot_token to .env file")
    sys.exit(1)

if not MONGO_URI:
    print("❌ ERROR: MONGO_URI is not set in .env file!")
    print("Please add MONGO_URI=mongodb://localhost:27017 to .env file")
    sys.exit(1)

# MongoDB Connection
try:
    print("🔄 Connecting to MongoDB...")
    mongo = MongoClient(MONGO_URI, serverSelectionTimeoutMS=5000)
    db = mongo[DB_NAME]
    users = db.users
    # Test connection
    mongo.admin.command('ping')
    print("✅ MongoDB connected successfully!")
except ConnectionFailure as e:
    print(f"❌ ERROR: MongoDB connection failed: {e}")
    print("Please check if MongoDB is running and MONGO_URI is correct")
    print("For local MongoDB: MONGO_URI=mongodb://localhost:27017")
    print("For MongoDB Atlas: MONGO_URI=mongodb+srv://username:password@cluster.mongodb.net/")
    sys.exit(1)
except Exception as e:
    print(f"❌ ERROR: MongoDB error: {e}")
    sys.exit(1)

# Constants for conversation states
WAITING_INPUT = 1
WAITING_BROADCAST = 2

def today():
    """Get today's date in UTC"""
    return datetime.utcnow().strftime("%Y-%m-%d")

def register_user(user_id: int, username: str | None = None):
    """Register or update user in database"""
    try:
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
        return True
    except Exception as e:
        logger.error(f"Error registering user {user_id}: {e}")
        return False

def can_use(user_id: int) -> bool:
    """Check if user can use the bot today"""
    if user_id == OWNER_ID:
        return True

    try:
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
    except Exception as e:
        logger.error(f"Error checking usage for {user_id}: {e}")
        return False

def remaining_uses(user_id: int) -> int:
    """Get remaining uses for today"""
    if user_id == OWNER_ID:
        return 9999
    try:
        data = users.find_one({"_id": user_id})
        if not data or data.get("date") != today():
            return DAILY_LIMIT
        return max(0, DAILY_LIMIT - data.get("count", 0))
    except Exception as e:
        logger.error(f"Error getting remaining uses for {user_id}: {e}")
        return 0

def lookup(query: str):
    """Query the API"""
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
    """Format API result for display"""
    if isinstance(result, dict):
        if result.get("ok") is False and "error" in result:
            return f"❌ Error: {result['error']}"
        if "result" in result:
            return str(result["result"])
        return "\n".join([f"• {k}: {v}" for k, v in result.items()])
    return str(result)

def home_menu(is_owner: bool = False):
    """Create home menu keyboard"""
    rows = [
        [InlineKeyboardButton("🔍 Search", callback_data="search"), 
         InlineKeyboardButton("📊 Limit", callback_data="limit")],
        [InlineKeyboardButton("❓ Help", callback_data="help"), 
         InlineKeyboardButton("ℹ️ About", callback_data="about")],
        [InlineKeyboardButton("📈 Stats", callback_data="stats")],
    ]
    if is_owner:
        rows.insert(2, [InlineKeyboardButton("📢 Broadcast", callback_data="broadcast")])
    return InlineKeyboardMarkup(rows)

def back_menu():
    """Create back/cancel menu"""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔙 Back", callback_data="back"), 
         InlineKeyboardButton("❌ Cancel", callback_data="cancel")]
    ])

def result_menu():
    """Create result menu"""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔍 Search Again", callback_data="search"), 
         InlineKeyboardButton("🏠 Home", callback_data="back")],
        [InlineKeyboardButton("❌ Cancel", callback_data="cancel")]
    ])

def broadcast_menu():
    """Create broadcast menu"""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📤 Send", callback_data="broadcast_send"), 
         InlineKeyboardButton("❌ Cancel", callback_data="cancel")]
    ])

# Command Handlers
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /start command"""
    u = update.effective_user
    if not u:
        return
    
    register_user(u.id, u.username)
    remaining = remaining_uses(u.id) if u.id != OWNER_ID else '♾️ Unlimited'
    
    text = (
        f"👋 Hello, {u.first_name}!\n\n"
        f"📋 Daily limit: {DAILY_LIMIT} uses\n"
        f"✅ Remaining today: {remaining}\n\n"
        f"Use the buttons below to get started."
    )
    await update.message.reply_text(text, reply_markup=home_menu(u.id == OWNER_ID))

async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /help command"""
    text = (
        "❓ Help\n\n"
        "• 🔍 Search - Look up information\n"
        "• 📊 Limit - Check remaining uses\n"
        "• 📈 Stats - View bot statistics\n"
        "• ℹ️ About - About this bot\n"
        "• 📢 Broadcast - Send message to all users (Owner only)\n"
        "• 🔙 Back - Go back to home\n"
        "• ❌ Cancel - Cancel current operation"
    )
    if update.message:
        await update.message.reply_text(text, reply_markup=back_menu())
    else:
        await update.callback_query.message.reply_text(text, reply_markup=back_menu())

async def about_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /about command"""
    text = (
        "ℹ️ About\n\n"
        "🤖 Telegram Bot with:\n"
        "• MongoDB database\n"
        "• Daily usage limits\n"
        "• Broadcast feature\n"
        "• User statistics\n"
        "• Interactive buttons\n\n"
        "Made with ❤️ using python-telegram-bot"
    )
    if update.message:
        await update.message.reply_text(text, reply_markup=back_menu())
    else:
        await update.callback_query.message.reply_text(text, reply_markup=back_menu())

async def limit_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /limit command"""
    uid = update.effective_user.id
    if uid == OWNER_ID:
        text = "👑 Owner has unlimited usage."
    else:
        text = f"📊 Remaining uses today: {remaining_uses(uid)}"
    
    if update.message:
        await update.message.reply_text(text, reply_markup=back_menu())
    else:
        await update.callback_query.message.reply_text(text, reply_markup=back_menu())

async def stats_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /stats command"""
    uid = update.effective_user.id
    try:
        total_users = users.count_documents({})
        active_today = users.count_documents({"date": today()})
        limit_hit = users.count_documents({"date": today(), "count": {"$gte": DAILY_LIMIT}})
        
        text = (
            "📈 Statistics\n\n"
            f"👥 Total users: {total_users}\n"
            f"✅ Active today: {active_today}\n"
            f"⚠️ Users at limit: {limit_hit}\n"
            f"📊 Your remaining: {remaining_uses(uid) if uid != OWNER_ID else '♾️ Unlimited'}"
        )
        
        if update.message:
            await update.message.reply_text(text, reply_markup=back_menu())
        else:
            await update.callback_query.message.reply_text(text, reply_markup=back_menu())
    except Exception as e:
        logger.error(f"Error in stats: {e}")
        error_text = "❌ Error fetching statistics. Please try again."
        if update.message:
            await update.message.reply_text(error_text, reply_markup=back_menu())
        else:
            await update.callback_query.message.reply_text(error_text, reply_markup=back_menu())

# Conversation Handlers for Search
async def search_entry(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Start search conversation"""
    q = update.callback_query
    await q.answer()
    await q.message.reply_text(
        "🔍 Send your query now.\n\n"
        "Example: phone number, email, or name",
        reply_markup=back_menu()
    )
    return WAITING_INPUT

async def handle_query(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle search query"""
    u = update.effective_user
    register_user(u.id, u.username)

    query_text = update.message.text.strip()
    if not query_text:
        await update.message.reply_text(
            "❌ Please send a valid query.",
            reply_markup=back_menu()
        )
        return WAITING_INPUT

    if not can_use(u.id):
        await update.message.reply_text(
            "❌ Daily limit reached. Try again tomorrow.",
            reply_markup=home_menu(u.id == OWNER_ID)
        )
        return ConversationHandler.END

    try:
        msg = await update.message.reply_text("⏳ Searching...")
        result = await asyncio.to_thread(lookup, query_text)
        text = format_result(result)

        # Limit message length to 4000 characters (Telegram limit)
        if len(text) > 3900:
            text = text[:3900] + "\n\n... (truncated)"
        
        await msg.edit_text(text, reply_markup=result_menu())
    except Exception as e:
        logger.error(f"Error in handle_query: {e}")
        await update.message.reply_text(
            "❌ An error occurred while searching. Please try again.",
            reply_markup=result_menu()
        )

    return ConversationHandler.END

# Broadcast Handlers (Owner only)
async def broadcast_entry(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Start broadcast conversation"""
    q = update.callback_query
    await q.answer()
    if q.from_user.id != OWNER_ID:
        await q.message.reply_text(
            "❌ Owner only feature.",
            reply_markup=home_menu(False)
        )
        return ConversationHandler.END
    await q.message.reply_text(
        "📢 Send broadcast text now.\n\n"
        "This will be sent to all users.",
        reply_markup=broadcast_menu()
    )
    return WAITING_BROADCAST

async def handle_broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle broadcast message"""
    if update.effective_user.id != OWNER_ID:
        await update.message.reply_text("❌ Owner only feature.")
        return ConversationHandler.END

    text = update.message.text.strip()
    if not text:
        await update.message.reply_text(
            "❌ Broadcast text cannot be empty.",
            reply_markup=broadcast_menu()
        )
        return WAITING_BROADCAST

    try:
        all_users = list(users.find({}, {"_id": 1}))
        sent, failed = 0, 0

        status = await update.message.reply_text(
            f"📤 Broadcasting to {len(all_users)} users..."
        )
        
        for doc in all_users:
            try:
                await context.bot.send_message(chat_id=doc["_id"], text=text)
                sent += 1
                await asyncio.sleep(0.1)  # Rate limiting
            except Exception as e:
                logger.error(f"Failed to send to {doc['_id']}: {e}")
                failed += 1

        await status.edit_text(
            f"✅ Broadcast complete!\n\n"
            f"📤 Sent: {sent}\n"
            f"❌ Failed: {failed}",
            reply_markup=home_menu(True)
        )
    except Exception as e:
        logger.error(f"Error in broadcast: {e}")
        await update.message.reply_text(
            "❌ Error during broadcast. Please try again.",
            reply_markup=home_menu(True)
        )
    
    return ConversationHandler.END

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Cancel current operation"""
    user_id = update.effective_user.id
    
    if update.message:
        await update.message.reply_text(
            "❌ Cancelled.",
            reply_markup=home_menu(user_id == OWNER_ID)
        )
    elif update.callback_query:
        await update.callback_query.message.reply_text(
            "❌ Cancelled.",
            reply_markup=home_menu(user_id == OWNER_ID)
        )
    return ConversationHandler.END

# Button Router
async def button_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle all callback queries"""
    q = update.callback_query
    await q.answer()
    uid = q.from_user.id

    if q.data == "help":
        await q.message.reply_text(
            "❓ Help\n\n"
            "• 🔍 Search - Look up information\n"
            "• 📊 Limit - Check remaining uses\n"
            "• 📈 Stats - View bot statistics\n"
            "• ℹ️ About - About this bot\n"
            "• 📢 Broadcast - Send message to all users (Owner only)\n"
            "• 🔙 Back - Go back to home\n"
            "• ❌ Cancel - Cancel current operation",
            reply_markup=back_menu()
        )
    elif q.data == "limit":
        if uid == OWNER_ID:
            text = "👑 Owner has unlimited usage."
        else:
            text = f"📊 Remaining uses today: {remaining_uses(uid)}"
        await q.message.reply_text(text, reply_markup=back_menu())
    elif q.data == "stats":
        try:
            total_users = users.count_documents({})
            active_today = users.count_documents({"date": today()})
            limit_hit = users.count_documents({"date": today(), "count": {"$gte": DAILY_LIMIT}})
            text = (
                "📈 Statistics\n\n"
                f"👥 Total users: {total_users}\n"
                f"✅ Active today: {active_today}\n"
                f"⚠️ Users at limit: {limit_hit}\n"
                f"📊 Your remaining: {remaining_uses(uid) if uid != OWNER_ID else '♾️ Unlimited'}"
            )
            await q.message.reply_text(text, reply_markup=back_menu())
        except Exception as e:
            logger.error(f"Error in stats callback: {e}")
            await q.message.reply_text(
                "❌ Error fetching statistics.",
                reply_markup=back_menu()
            )
    elif q.data == "about":
        await q.message.reply_text(
            "ℹ️ About\n\n"
            "🤖 Telegram Bot with:\n"
            "• MongoDB database\n"
            "• Daily usage limits\n"
            "• Broadcast feature\n"
            "• User statistics\n"
            "• Interactive buttons\n\n"
            "Made with ❤️ using python-telegram-bot",
            reply_markup=back_menu()
        )
    elif q.data == "back":
        remaining = remaining_uses(uid) if uid != OWNER_ID else '♾️ Unlimited'
        await q.message.reply_text(
            f"🏠 Home Menu\n\n"
            f"✅ Remaining today: {remaining}",
            reply_markup=home_menu(uid == OWNER_ID)
        )
    elif q.data == "cancel":
        await q.message.reply_text(
            "❌ Cancelled.",
            reply_markup=home_menu(uid == OWNER_ID)
        )

# Error Handler
async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    """Handle errors"""
    logger.exception(f"Unhandled error: {context.error}")
    
    # Send error message to user if possible
    if update and hasattr(update, 'effective_message'):
        try:
            await update.effective_message.reply_text(
                "❌ An error occurred. Please try again later."
            )
        except:
            pass

# Main Function
def main():
    """Start the bot"""
    try:
        print("🔄 Initializing bot application...")
        app = Application.builder().token(BOT_TOKEN).build()
        print("✅ Bot application created successfully!")
        
        # Command handlers
        app.add_handler(CommandHandler("start", start))
        app.add_handler(CommandHandler("help", help_cmd))
        app.add_handler(CommandHandler("limit", limit_cmd))
        app.add_handler(CommandHandler("stats", stats_cmd))
        app.add_handler(CommandHandler("about", about_cmd))

        # Search conversation
        app.add_handler(ConversationHandler(
            entry_points=[CallbackQueryHandler(search_entry, pattern="^search$")],
            states={
                WAITING_INPUT: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_query)]
            },
            fallbacks=[CommandHandler("cancel", cancel), CallbackQueryHandler(cancel, pattern="^cancel$")],
        ))

        # Broadcast conversation (Owner only)
        app.add_handler(ConversationHandler(
            entry_points=[CallbackQueryHandler(broadcast_entry, pattern="^broadcast$")],
            states={
                WAITING_BROADCAST: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_broadcast)]
            },
            fallbacks=[CommandHandler("cancel", cancel), CallbackQueryHandler(cancel, pattern="^cancel$")],
        ))

        # Callback query handler
        app.add_handler(CallbackQueryHandler(button_router))
        
        # Error handler
        app.add_error_handler(error_handler)
        
        print("=" * 60)
        print("🚀 Bot is running! Press Ctrl+C to stop.")
        print("=" * 60)
        
        # Start polling
        app.run_polling()
        
    except InvalidToken as e:
        print(f"❌ ERROR: Invalid Telegram Bot Token: {e}")
        print("Please check your BOT_TOKEN in .env file")
        print("Get a new token from @BotFather on Telegram")
        sys.exit(1)
    except Exception as e:
        print(f"❌ ERROR: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)

if __name__ == "__main__":
    main()
