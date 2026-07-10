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
SHARE_REWARD = int(os.getenv("SHARE_REWARD", "5"))

if not BOT_TOKEN or not MONGO_URI:
    raise RuntimeError("BOT_TOKEN and MONGO_URI must be set in .env")

try:
    mongo = MongoClient(MONGO_URI, serverSelectionTimeoutMS=5000)
    db = mongo[DB_NAME]
    users = db.users
    mongo.admin.command('ping')
    print("✅ MongoDB connected!")
except Exception as e:
    print(f"❌ MongoDB error: {e}")
    sys.exit(1)

WAITING_INPUT = 1
WAITING_BROADCAST = 2

def today():
    return datetime.utcnow().strftime("%Y-%m-%d")

def register_user(user_id: int, username: str | None = None):
    try:
        users.update_one(
            {"_id": user_id},
            {
                "$setOnInsert": {
                    "_id": user_id,
                    "first_seen": today(),
                    "count": 0,
                    "date": today(),
                    "shared_count": 0,
                    "bonus_limits": 0,
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
                    "shared_count": 0,
                    "bonus_limits": 0,
                }
            )
            return True

        # ✅ FIX: Agar naya din hai toh count reset karo
        if data.get("date") != today():
            users.update_one(
                {"_id": user_id},
                {"$set": {"date": today(), "count": 1, "last_seen": today()}}
            )
            return True

        bonus_limits = data.get("bonus_limits", 0)
        daily_count = data.get("count", 0)
        total_limit = DAILY_LIMIT + bonus_limits
        
        # ✅ FIX: Agar limit cross ho gayi toh false
        if daily_count >= total_limit:
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
    """✅ FIXED: Get remaining uses for today"""
    if user_id == OWNER_ID:
        return 9999
    try:
        data = users.find_one({"_id": user_id})
        if not data:
            return DAILY_LIMIT
        
        bonus_limits = data.get("bonus_limits", 0)
        daily_count = data.get("count", 0)
        current_date = data.get("date")
        
        # Agar naya din hai toh reset
        if current_date != today():
            return DAILY_LIMIT + bonus_limits
        
        # ✅ FIX: Remaining = Total - Used
        total_limit = DAILY_LIMIT + bonus_limits
        remaining = total_limit - daily_count
        
        # ✅ FIX: Kabhi negative nahi dikhana
        return max(0, remaining)
        
    except Exception as e:
        logger.error(f"Error getting remaining uses for {user_id}: {e}")
        return 0

def get_total_limits(user_id: int) -> int:
    if user_id == OWNER_ID:
        return 9999
    try:
        data = users.find_one({"_id": user_id})
        if not data:
            return DAILY_LIMIT
        bonus_limits = data.get("bonus_limits", 0)
        return DAILY_LIMIT + bonus_limits
    except Exception as e:
        logger.error(f"Error getting total limits for {user_id}: {e}")
        return DAILY_LIMIT

def add_bonus_limits(user_id: int, amount: int) -> bool:
    try:
        result = users.update_one(
            {"_id": user_id},
            {
                "$inc": {
                    "bonus_limits": amount,
                    "shared_count": 1
                },
                "$set": {"last_seen": today()}
            }
        )
        return result.modified_count > 0
    except Exception as e:
        logger.error(f"Error adding bonus limits to {user_id}: {e}")
        return False

def search_api(query: str):
    try:
        response = requests.get(
            API_URL,
            params={"key": API_KEY, "q": query},
            timeout=30
        )
        response.raise_for_status()
        try:
            return response.json()
        except ValueError:
            return {"ok": False, "error": "Invalid JSON response"}
    except requests.exceptions.Timeout:
        return {"ok": False, "error": "API timeout"}
    except requests.exceptions.RequestException as e:
        return {"ok": False, "error": str(e)}

def format_api_result(data):
    if isinstance(data, dict):
        if data.get("ok") is False:
            error_msg = data.get("error", "Unknown error")
            return f"❌ Error: {error_msg}"
        
        if "result" in data:
            result = data["result"]
            if isinstance(result, dict):
                formatted = "✅ Result Found:\n\n"
                for key, value in result.items():
                    formatted += f"• {key}: {value}\n"
                return formatted
            elif isinstance(result, list):
                formatted = "✅ Results Found:\n\n"
                for item in result:
                    if isinstance(item, dict):
                        for key, value in item.items():
                            formatted += f"• {key}: {value}\n"
                        formatted += "-" * 30 + "\n"
                    else:
                        formatted += f"• {item}\n"
                return formatted
            else:
                return f"✅ Result: {result}"
        
        if data:
            formatted = "📊 API Response:\n\n"
            for key, value in data.items():
                formatted += f"• {key}: {value}\n"
            return formatted
        
        return "❌ No data found"
    
    return str(data)

def home_menu(is_owner: bool = False):
    if is_owner:
        rows = [
            [InlineKeyboardButton("🔍 Search", callback_data="search")],
            [InlineKeyboardButton("📊 Limit", callback_data="limit"), 
             InlineKeyboardButton("📢 Share & Earn", callback_data="share")],
            [InlineKeyboardButton("📈 Stats", callback_data="stats"), 
             InlineKeyboardButton("📢 Broadcast", callback_data="broadcast")],
            [InlineKeyboardButton("❓ Help", callback_data="help"), 
             InlineKeyboardButton("ℹ️ About", callback_data="about")],
        ]
    else:
        rows = [
            [InlineKeyboardButton("🔍 Search", callback_data="search")],
            [InlineKeyboardButton("📊 Limit", callback_data="limit"), 
             InlineKeyboardButton("📢 Share & Earn", callback_data="share")],
            [InlineKeyboardButton("❓ Help", callback_data="help"), 
             InlineKeyboardButton("ℹ️ About", callback_data="about")],
        ]
    return InlineKeyboardMarkup(rows)

def back_menu():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔙 Back", callback_data="back"), 
         InlineKeyboardButton("❌ Cancel", callback_data="cancel")]
    ])

def result_menu():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔍 Search Again", callback_data="search"), 
         InlineKeyboardButton("🏠 Home", callback_data="back")],
        [InlineKeyboardButton("❌ Cancel", callback_data="cancel")]
    ])

def broadcast_menu():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📤 Send", callback_data="broadcast_send"), 
         InlineKeyboardButton("❌ Cancel", callback_data="cancel")]
    ])

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = update.effective_user
    if not u:
        return
    
    register_user(u.id, u.username)
    
    # ✅ FIX: Correct remaining display
    if u.id == OWNER_ID:
        remaining = "♾️ Unlimited"
        total = "♾️ Unlimited"
        bonus = 0
    else:
        remaining = remaining_uses(u.id)
        total = get_total_limits(u.id)
        bonus = total - DAILY_LIMIT
    
    text = (
        f"👋 Hello, {u.first_name}!\n\n"
        f"📋 Daily limit: {DAILY_LIMIT} uses\n"
        f"🎁 Bonus limits: {bonus if u.id != OWNER_ID else 0}\n"
        f"📊 Total available: {total}\n"
        f"✅ Remaining today: {remaining}\n\n"
        f"🔍 Use 'Search' to find phone/email details!\n"
        f"💡 Share to earn {SHARE_REWARD} extra limits per share!\n\n"
        f"Use the buttons below."
    )
    await update.message.reply_text(text, reply_markup=home_menu(u.id == OWNER_ID))

async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    is_owner = uid == OWNER_ID
    
    text = (
        "❓ Help\n\n"
        "• 🔍 Search - Search phone/email/name\n"
        "• 📊 Limit - Check remaining uses\n"
        "• 📢 Share & Earn - Share and earn extra limits\n"
        "• ℹ️ About - About this bot\n"
    )
    
    if is_owner:
        text += "• 📈 Stats - View statistics (Owner only)\n"
        text += "• 📢 Broadcast - Send to all users (Owner only)\n"
    
    text += "• 🔙 Back - Go back to home\n"
    text += "• ❌ Cancel - Cancel current operation\n\n"
    text += f"💰 Earn {SHARE_REWARD} extra limits per share!"
    
    if update.message:
        await update.message.reply_text(text, reply_markup=back_menu())
    else:
        await update.callback_query.message.reply_text(text, reply_markup=back_menu())

async def about_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "ℹ️ About\n\n"
        "🤖 Telegram Search Bot\n\n"
        "Features:\n"
        "• 🔍 Search phone/email/name\n"
        "• 📊 Daily usage limits\n"
        "• 📢 Share & Earn rewards\n"
        "• 📈 Statistics (Owner)\n"
        "• 📢 Broadcast (Owner)\n\n"
        f"💰 Share Reward: {SHARE_REWARD}\n\n"
        "Made with ❤️"
    )
    if update.message:
        await update.message.reply_text(text, reply_markup=back_menu())
    else:
        await update.callback_query.message.reply_text(text, reply_markup=back_menu())

async def limit_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if uid == OWNER_ID:
        text = "👑 Owner has unlimited usage."
    else:
        remaining = remaining_uses(uid)
        total = get_total_limits(uid)
        bonus = total - DAILY_LIMIT
        text = (
            f"📊 Usage Details\n\n"
            f"📋 Daily limit: {DAILY_LIMIT}\n"
            f"🎁 Bonus limits: {bonus}\n"
            f"📊 Total available: {total}\n"
            f"✅ Remaining today: {remaining}"
        )
    
    if update.message:
        await update.message.reply_text(text, reply_markup=back_menu())
    else:
        await update.callback_query.message.reply_text(text, reply_markup=back_menu())

async def stats_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    
    if uid != OWNER_ID:
        if update.message:
            await update.message.reply_text(
                "❌ Owner only feature.",
                reply_markup=home_menu(False)
            )
        else:
            await update.callback_query.message.reply_text(
                "❌ Owner only feature.",
                reply_markup=home_menu(False)
            )
        return
    
    try:
        total_users = users.count_documents({})
        active_today = users.count_documents({"date": today()})
        limit_hit = users.count_documents({"date": today(), "count": {"$gte": DAILY_LIMIT}})
        total_shares = users.count_documents({"shared_count": {"$gt": 0}})
        
        text = (
            "📈 Statistics (Owner Only)\n\n"
            f"👥 Total users: {total_users}\n"
            f"✅ Active today: {active_today}\n"
            f"⚠️ Users at limit: {limit_hit}\n"
            f"🔄 Total shares: {total_shares}\n"
            f"📊 Daily limit: {DAILY_LIMIT}\n"
            f"💰 Share reward: {SHARE_REWARD}"
        )
        
        if update.message:
            await update.message.reply_text(text, reply_markup=home_menu(True))
        else:
            await update.callback_query.message.reply_text(text, reply_markup=home_menu(True))
    except Exception as e:
        logger.error(f"Error in stats: {e}")
        await update.message.reply_text("❌ Error fetching stats.", reply_markup=home_menu(True))

async def share_entry(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    uid = q.from_user.id
    
    bot_info = await context.bot.get_me()
    bot_username = bot_info.username
    share_link = f"https://t.me/{bot_username}?start=ref_{uid}"
    
    text = (
        "📢 Share & Earn\n\n"
        f"Share and earn {SHARE_REWARD} extra limits per friend!\n\n"
        f"🔗 Your share link:\n"
        f"`{share_link}`\n\n"
        "📋 How it works:\n"
        "1. Share the link with friends\n"
        "2. When they join, you get extra limits\n"
        "3. Bonus limits added automatically\n\n"
        f"💰 Current bonus: {get_total_limits(uid) - DAILY_LIMIT}\n"
        f"📊 Total available: {get_total_limits(uid)}\n\n"
        "👆 Tap to copy!"
    )
    
    await q.message.reply_text(
        text,
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("📋 Copy Link", callback_data="copy_link")],
            [InlineKeyboardButton("🔙 Back", callback_data="back")]
        ]),
        parse_mode='Markdown'
    )

async def handle_referral(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = update.effective_user
    if not u:
        return
    
    if context.args and len(context.args) > 0:
        ref_arg = context.args[0]
        if ref_arg.startswith("ref_"):
            try:
                referrer_id = int(ref_arg.replace("ref_", ""))
                
                if referrer_id == u.id:
                    await update.message.reply_text(
                        "❌ Cannot refer yourself!",
                        reply_markup=home_menu(u.id == OWNER_ID)
                    )
                    return
                
                register_user(u.id, u.username)
                
                if add_bonus_limits(referrer_id, SHARE_REWARD):
                    try:
                        await context.bot.send_message(
                            chat_id=referrer_id,
                            text=f"🎉 Someone joined using your referral!\n\n"
                                 f"💰 You earned {SHARE_REWARD} extra limits!\n"
                                 f"📊 Total limits: {get_total_limits(referrer_id)}\n\n"
                                 f"Keep sharing! 🚀"
                        )
                    except Exception as e:
                        logger.error(f"Could not notify referrer {referrer_id}: {e}")
                    
                    remaining = remaining_uses(u.id)
                    text = (
                        f"👋 Welcome {u.first_name}!\n\n"
                        f"✅ Registered!\n"
                        f"🎁 Referrer earned {SHARE_REWARD} extra limits.\n\n"
                        f"📋 Daily limit: {DAILY_LIMIT}\n"
                        f"✅ Remaining today: {remaining}\n\n"
                        f"💡 You can also share and earn!\n\n"
                        f"Use the buttons below."
                    )
                    await update.message.reply_text(text, reply_markup=home_menu(u.id == OWNER_ID))
                    return
            except ValueError:
                logger.error(f"Invalid referrer ID: {ref_arg}")
    
    await start(update, context)

async def search_entry(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    uid = q.from_user.id
    
    if uid != OWNER_ID:
        remaining = remaining_uses(uid)
        if remaining <= 0:
            await q.message.reply_text(
                f"❌ Daily limit reached!\n\n"
                f"📊 Remaining: 0\n"
                f"💡 Share to earn extra limits!",
                reply_markup=home_menu(uid == OWNER_ID)
            )
            return ConversationHandler.END
    
    await q.message.reply_text(
        "🔍 Send your query now.\n\n"
        "Search by:\n"
        "• Phone number\n"
        "• Email address\n"
        "• Name\n\n"
        "Example: 9876543210 or john@email.com\n\n"
        f"📊 Remaining: {remaining_uses(uid) if uid != OWNER_ID else '♾️ Unlimited'}\n\n"
        "Type /cancel to cancel.",
        reply_markup=back_menu()
    )
    return WAITING_INPUT

async def handle_query(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = update.effective_user
    register_user(u.id, u.username)

    query_text = update.message.text.strip()
    if not query_text:
        await update.message.reply_text(
            "❌ Please send a valid query.",
            reply_markup=back_menu()
        )
        return WAITING_INPUT

    # ✅ FIX: Check limit
    if not can_use(u.id):
        remaining = remaining_uses(u.id)
        await update.message.reply_text(
            f"❌ Daily limit reached!\n\n"
            f"📊 Remaining today: {remaining}\n"
            f"💡 Share this bot to earn extra limits!",
            reply_markup=home_menu(u.id == OWNER_ID)
        )
        return ConversationHandler.END

    try:
        msg = await update.message.reply_text("⏳ Searching...")
        result = await asyncio.to_thread(search_api, query_text)
        formatted_text = format_api_result(result)
        
        if len(formatted_text) > 3900:
            formatted_text = formatted_text[:3900] + "\n\n... (truncated)"
        
        await msg.edit_text(formatted_text, reply_markup=result_menu())
        
        remaining = remaining_uses(u.id)
        if remaining > 0 and u.id != OWNER_ID:
            await update.message.reply_text(
                f"✅ Remaining today: {remaining}",
                reply_markup=result_menu()
            )
            
    except Exception as e:
        logger.error(f"Error in handle_query: {e}")
        await update.message.reply_text(
            f"❌ Error: {str(e)}",
            reply_markup=result_menu()
        )

    return ConversationHandler.END

async def broadcast_entry(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    if q.from_user.id != OWNER_ID:
        await q.message.reply_text("❌ Owner only.", reply_markup=home_menu(False))
        return ConversationHandler.END
    await q.message.reply_text(
        "📢 Send broadcast text.\n\nThis will be sent to all users.",
        reply_markup=broadcast_menu()
    )
    return WAITING_BROADCAST

async def handle_broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID:
        await update.message.reply_text("❌ Owner only.")
        return ConversationHandler.END

    text = update.message.text.strip()
    if not text:
        await update.message.reply_text(
            "❌ Text cannot be empty.",
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
                await asyncio.sleep(0.1)
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
        await update.message.reply_text("❌ Error in broadcast.", reply_markup=home_menu(True))
    
    return ConversationHandler.END

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
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

async def copy_link_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer("📋 Copied! Share with friends.", show_alert=True)

async def button_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    uid = q.from_user.id
    is_owner = uid == OWNER_ID

    if q.data == "help":
        text = (
            "❓ Help\n\n"
            "• 🔍 Search - Search phone/email/name\n"
            "• 📊 Limit - Check remaining uses\n"
            "• 📢 Share & Earn - Share and earn extra limits\n"
            "• ℹ️ About - About this bot\n"
        )
        if is_owner:
            text += "• 📈 Stats - Statistics (Owner only)\n"
            text += "• 📢 Broadcast - Send to all (Owner only)\n"
        text += "• 🔙 Back - Go back to home\n"
        text += "• ❌ Cancel - Cancel current operation\n\n"
        text += f"💰 Earn {SHARE_REWARD} extra limits per share!"
        
        await q.message.reply_text(text, reply_markup=back_menu())
    
    elif q.data == "limit":
        if uid == OWNER_ID:
            text = "👑 Unlimited usage."
        else:
            remaining = remaining_uses(uid)
            total = get_total_limits(uid)
            bonus = total - DAILY_LIMIT
            text = (
                f"📊 Usage Details\n\n"
                f"📋 Daily limit: {DAILY_LIMIT}\n"
                f"🎁 Bonus limits: {bonus}\n"
                f"📊 Total available: {total}\n"
                f"✅ Remaining today: {remaining}"
            )
        await q.message.reply_text(text, reply_markup=back_menu())
    
    elif q.data == "stats":
        if not is_owner:
            await q.message.reply_text("❌ Owner only.", reply_markup=home_menu(False))
            return
        
        try:
            total_users = users.count_documents({})
            active_today = users.count_documents({"date": today()})
            limit_hit = users.count_documents({"date": today(), "count": {"$gte": DAILY_LIMIT}})
            total_shares = users.count_documents({"shared_count": {"$gt": 0}})
            
            text = (
                "📈 Statistics (Owner Only)\n\n"
                f"👥 Total users: {total_users}\n"
                f"✅ Active today: {active_today}\n"
                f"⚠️ Users at limit: {limit_hit}\n"
                f"🔄 Total shares: {total_shares}\n"
                f"📊 Daily limit: {DAILY_LIMIT}\n"
                f"💰 Share reward: {SHARE_REWARD}"
            )
            await q.message.reply_text(text, reply_markup=home_menu(True))
        except Exception as e:
            logger.error(f"Error in stats: {e}")
            await q.message.reply_text("❌ Error fetching stats.", reply_markup=home_menu(True))
    
    elif q.data == "about":
        await q.message.reply_text(
            "ℹ️ About\n\n"
            "🤖 Telegram Search Bot\n\n"
            "Features:\n"
            "• 🔍 Search phone/email/name\n"
            "• 📊 Daily usage limits\n"
            "• 📢 Share & Earn rewards\n"
            "• 📈 Statistics (Owner)\n"
            "• 📢 Broadcast (Owner)\n\n"
            f"💰 Share Reward: {SHARE_REWARD}\n\n"
            "Made with ❤️",
            reply_markup=back_menu()
        )
    
    elif q.data == "share":
        await share_entry(update, context)
    
    elif q.data == "copy_link":
        await copy_link_callback(update, context)
    
    elif q.data == "back":
        if uid == OWNER_ID:
            remaining = "♾️ Unlimited"
            total = "♾️ Unlimited"
        else:
            remaining = remaining_uses(uid)
            total = get_total_limits(uid)
        
        await q.message.reply_text(
            f"🏠 Home Menu\n\n"
            f"📊 Total limits: {total}\n"
            f"✅ Remaining today: {remaining}",
            reply_markup=home_menu(uid == OWNER_ID)
        )
    
    elif q.data == "cancel":
        await q.message.reply_text(
            "❌ Cancelled.",
            reply_markup=home_menu(uid == OWNER_ID)
        )

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    logger.exception(f"Unhandled error: {context.error}")
    if update and hasattr(update, 'effective_message'):
        try:
            await update.effective_message.reply_text("❌ An error occurred. Please try again.")
        except:
            pass

def main():
    try:
        print("🔄 Starting bot...")
        app = Application.builder().token(BOT_TOKEN).build()
        print("✅ Bot initialized!")
        
        app.add_handler(CommandHandler("start", handle_referral))
        app.add_handler(CommandHandler("help", help_cmd))
        app.add_handler(CommandHandler("limit", limit_cmd))
        app.add_handler(CommandHandler("stats", stats_cmd))
        app.add_handler(CommandHandler("about", about_cmd))

        app.add_handler(ConversationHandler(
            entry_points=[CallbackQueryHandler(search_entry, pattern="^search$")],
            states={
                WAITING_INPUT: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_query)]
            },
            fallbacks=[CommandHandler("cancel", cancel), CallbackQueryHandler(cancel, pattern="^cancel$")],
        ))

        app.add_handler(ConversationHandler(
            entry_points=[CallbackQueryHandler(broadcast_entry, pattern="^broadcast$")],
            states={
                WAITING_BROADCAST: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_broadcast)]
            },
            fallbacks=[CommandHandler("cancel", cancel), CallbackQueryHandler(cancel, pattern="^cancel$")],
        ))

        app.add_handler(CallbackQueryHandler(button_router))
        app.add_error_handler(error_handler)
        
        print("=" * 60)
        print("🚀 Bot is running!")
        print("=" * 60)
        
        app.run_polling()
        
    except InvalidToken as e:
        print(f"❌ Invalid Bot Token: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"❌ Error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)

if __name__ == "__main__":
    main()
