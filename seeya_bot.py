import logging
import os
import re
import io
import asyncio
import random
from concurrent.futures import ThreadPoolExecutor

from telegram import (
    Update, ChatMember, ChatPermissions, InlineQueryResultArticle, 
    InputTextMessageContent, InlineKeyboardMarkup, InlineKeyboardButton
)
from telegram.ext import (
    Application, CommandHandler, MessageHandler, ChatMemberHandler, 
    InlineQueryHandler, filters, ContextTypes
)
from PIL import Image, ImageDraw, ImageFont
import yt_dlp

# Enable logging
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

executor = ThreadPoolExecutor(max_workers=3)

# ==========================================
# 1. HELPER FUNCTIONS
# ==========================================

def create_welcome_banner(name, user_id):
    """Generates a welcome banner image."""
    width, height = 800, 400
    img = Image.new('RGB', (width, height), color=(30, 30, 50))
    d = ImageDraw.Draw(img)
    
    font = ImageFont.load_default(size=40)
    font_small = ImageFont.load_default(size=30)

    text = f"Welcome {name}!"
    text_bbox = d.textbbox((0, 0), text, font=font)
    text_width = text_bbox[2] - text_bbox[0]
    
    d.text(((width - text_width) / 2, 150), text, fill=(255, 255, 255), font=font)
    d.text((50, 300), f"ID: {user_id}", fill=(200, 200, 200), font=font_small)
    
    bio = io.BytesIO()
    bio.name = 'welcome.png'
    img.save(bio, 'PNG')
    bio.seek(0)
    return bio

async def is_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        member = await context.bot.get_chat_member(update.effective_chat.id, update.effective_user.id)
        return member.status in [ChatMember.ADMINISTRATOR, ChatMember.OWNER]
    except:
        return False

async def get_target_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Helper to get user ID from reply or username"""
    if update.message.reply_to_message:
        return update.message.reply_to_message.from_user
    elif context.args:
        try:
            user = await context.bot.get_chat(context.args[0])
            return user
        except:
            return None
    return None

# ==========================================
# 2. GROUP MANAGEMENT COMMANDS
# ==========================================

async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("👋 Hello! I am Seeya Bot. I can manage your group, play music, and chat!\nType /help for commands.")

async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    help_text = (
        "🛠 **Admin Commands:**\n"
        "`/add @user` - Add user to group\n"
        "`/kick @user` - Kick user (reply or username)\n"
        "`/ban @user` - Ban user\n"
        "`/unban @user` - Unban user\n"
        "`/mute @user` - Mute user\n"
        "`/unmute @user` - Unmute user\n"
        "`/promote @user` - Make user admin\n"
        "`/demote @user` - Remove admin\n"
        "`/pin` - Pin replied message\n"
        "`/unpin` - Unpin message\n"
        "`/id` - Get User/Chat ID\n\n"
        "🎵 **Music:**\n"
        "`/play <song>` or say 'play <song>'\n"
        "Inline: `@YourBotName <song>`\n\n"
        "💬 **Chat:**\n"
        "Reply to me or say 'seeya'"
    )
    await update.message.reply_text(help_text, parse_mode='Markdown')

async def add_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update, context): return await update.message.reply_text("❌ Admin only.")
    if not context.args: return await update.message.reply_text("Usage: `/add @username`", parse_mode='Markdown')
    target = context.args[0]
    try:
        await context.bot.add_chat_member(update.effective_chat.id, target if target.startswith('@') else int(target))
        await update.message.reply_text(f"✅ Added {target} to the group.")
    except Exception as e:
        await update.message.reply_text(f"❌ Failed to add. Error: {e}")

async def kick_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update, context): return await update.message.reply_text("❌ Admin only.")
    user = await get_target_user(update, context)
    if not user: return await update.message.reply_text("Reply to a user or provide @username.")
    try:
        await context.bot.ban_chat_member(update.effective_chat.id, user.id)
        await context.bot.unban_chat_member(update.effective_chat.id, user.id)
        await update.message.reply_text(f"✅ Kicked {user.first_name}.")
    except Exception as e:
        await update.message.reply_text(f"❌ Error: {e}")

async def ban_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update, context): return await update.message.reply_text("❌ Admin only.")
    user = await get_target_user(update, context)
    if not user: return await update.message.reply_text("Reply to a user or provide @username.")
    try:
        await context.bot.ban_chat_member(update.effective_chat.id, user.id)
        await update.message.reply_text(f"🔨 Banned {user.first_name}.")
    except Exception as e:
        await update.message.reply_text(f"❌ Error: {e}")

async def unban_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update, context): return await update.message.reply_text("❌ Admin only.")
    user = await get_target_user(update, context)
    if not user: return await update.message.reply_text("Provide @username.")
    try:
        await context.bot.unban_chat_member(update.effective_chat.id, user.id)
        await update.message.reply_text(f"✅ Unbanned {user.first_name}.")
    except Exception as e:
        await update.message.reply_text(f"❌ Error: {e}")

async def mute_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update, context): return await update.message.reply_text("❌ Admin only.")
    user = await get_target_user(update, context)
    if not user: return await update.message.reply_text("Reply to a user or provide @username.")
    try:
        await context.bot.restrict_chat_member(update.effective_chat.id, user.id, permissions=ChatPermissions(can_send_messages=False))
        await update.message.reply_text(f"🔇 Muted {user.first_name}.")
    except Exception as e:
        await update.message.reply_text(f"❌ Error: {e}")

async def unmute_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update, context): return await update.message.reply_text("❌ Admin only.")
    user = await get_target_user(update, context)
    if not user: return await update.message.reply_text("Reply to a user or provide @username.")
    try:
        await context.bot.restrict_chat_member(update.effective_chat.id, user.id, permissions=ChatPermissions(can_send_messages=True, can_send_media_messages=True, can_send_other_messages=True, can_add_web_page_previews=True))
        await update.message.reply_text(f"🔊 Unmuted {user.first_name}.")
    except Exception as e:
        await update.message.reply_text(f"❌ Error: {e}")

async def promote_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update, context): return await update.message.reply_text("❌ Admin only.")
    user = await get_target_user(update, context)
    if not user: return await update.message.reply_text("Reply to a user or provide @username.")
    try:
        await context.bot.promote_chat_member(update.effective_chat.id, user.id, can_change_info=True, can_delete_messages=True, can_invite_users=True, can_restrict_members=True, can_pin_messages=True, can_promote_members=False)
        await update.message.reply_text(f"⭐ Promoted {user.first_name} to Admin.")
    except Exception as e:
        await update.message.reply_text(f"❌ Error: {e}")

async def demote_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update, context): return await update.message.reply_text("❌ Admin only.")
    user = await get_target_user(update, context)
    if not user: return await update.message.reply_text("Reply to a user or provide @username.")
    try:
        await context.bot.promote_chat_member(update.effective_chat.id, user.id, can_change_info=False, can_delete_messages=False, can_invite_users=False, can_restrict_members=False, can_pin_messages=False, can_promote_members=False)
        await update.message.reply_text(f"⬇️ Demoted {user.first_name}.")
    except Exception as e:
        await update.message.reply_text(f"❌ Error: {e}")

async def pin_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update, context): return await update.message.reply_text("❌ Admin only.")
    if not update.message.reply_to_message: return await update.message.reply_text("Reply to a message to pin it.")
    try:
        await context.bot.pin_chat_message(update.effective_chat.id, update.message.reply_to_message.message_id)
        await update.message.reply_text("📌 Pinned.")
    except Exception as e:
        await update.message.reply_text(f"❌ Error: {e}")

async def unpin_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update, context): return await update.message.reply_text("❌ Admin only.")
    try:
        await context.bot.unpin_chat_message(update.effective_chat.id)
        await update.message.reply_text("📌 Unpinned.")
    except Exception as e:
        await update.message.reply_text(f"❌ Error: {e}")

async def get_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.reply_to_message:
        user = update.message.reply_to_message.from_user
        await update.message.reply_text(f"👤 User ID: `{user.id}`\n💬 Chat ID: `{update.effective_chat.id}`", parse_mode='Markdown')
    else:
        await update.message.reply_text(f"👤 Your ID: `{update.effective_user.id}`\n💬 Chat ID: `{update.effective_chat.id}`", parse_mode='Markdown')

# ==========================================
# 3. WELCOME BANNER HANDLER
# ==========================================

async def welcome_new_member(update: Update, context: ContextTypes.DEFAULT_TYPE):
    result = extract_status_change(update.chat_member)
    if result is None: return
    
    was_member, is_member = result
    if not was_member and is_member:
        user = update.chat_member.new_chat_member.user
        loop = asyncio.get_event_loop()
        banner = await loop.run_in_executor(executor, create_welcome_banner, user.first_name, user.id)
        
        await context.bot.send_photo(
            chat_id=update.effective_chat.id,
            photo=banner,
            caption=f"Welcome to the group, {user.mention_html()}!",
            parse_mode='HTML'
        )

def extract_status_change(chat_member_update):
    status_change = chat_member_update.difference().get("status")
    old_is_member, new_is_member = chat_member_update.difference().get("is_member", (None, None))
    if status_change is None: return None
    old_status, new_status = status_change
    was_member = old_status in [ChatMember.MEMBER, ChatMember.ADMINISTRATOR] or (old_status == ChatMember.RESTRICTED and old_is_member is True)
    is_member = new_status in [ChatMember.MEMBER, ChatMember.ADMINISTRATOR] or (new_status == ChatMember.RESTRICTED and new_is_member is True)
    return was_member, is_member

# ==========================================
# 4. MUSIC / YT-DLP HANDLER (FIXED)
# ==========================================

def download_audio(query):
    """Searches YouTube and downloads audio. (Fixed filename detection)"""
    os.makedirs("downloads", exist_ok=True)
    ydl_opts = {
        'format': 'bestaudio/best',
        'outtmpl': 'downloads/%(id)s.%(ext)s',
        'noplaylist': True,
        'default_search': 'ytsearch1',
        'quiet': True,
        'postprocessors': [{
            'key': 'FFmpegExtractAudio',
            'preferredcodec': 'mp3',
            'preferredquality': '192',
        }],
    }
    
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            # Download directly instead of extracting info first (saves time)
            info = ydl.extract_info(query, download=True)
            video = info['entries'][0] if 'entries' in info else info
            title = video.get('title', 'Unknown')
            video_id = video.get('id')
            
            # Because of the postprocessor, the final file will be an .mp3
            filename = f"downloads/{video_id}.mp3"
            
            # Verify the file exists (in case yt-dlp named it slightly differently)
            if os.path.exists(filename):
                return filename, title
            else:
                # Fallback: Find the file by ID
                for f in os.listdir("downloads"):
                    if f.startswith(str(video_id)) and f.endswith('.mp3'):
                        return os.path.join("downloads", f), title
                return None, None
                
    except Exception as e:
        logger.error(f"Download error: {e}")
        return None, None

async def music_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Triggers when someone says play, search, found song, or uses /play."""
    text = update.message.text.lower()
    
    # Improved query extraction (handles both /play command and normal text)
    if text.startswith('/play'):
        query = text.replace('/play', '', 1).strip()
    else:
        query = re.sub(r'(?i)\b(play|search|found song)\b', '', text).strip()
    
    if not query: 
        return await update.message.reply_text("What song? Example: `play shape of you`")

    msg = await update.message.reply_text(f"🔎 Searching for: **{query}**...")
    
    loop = asyncio.get_event_loop()
    filepath, title = await loop.run_in_executor(executor, download_audio, query)
    
    if filepath and os.path.exists(filepath):
        # Check file size (Telegram limit is 50MB)
        file_size = os.path.getsize(filepath)
        if file_size > 50 * 1024 * 1024:
            os.remove(filepath)
            return await msg.edit_text("❌ The audio file is too large for Telegram (Over 50MB). Try a shorter song.")

        await msg.edit_text(f"🎵 Found: **{title}**\nUploading immediately...")
        try:
            with open(filepath, 'rb') as audio:
                await context.bot.send_audio(
                    chat_id=update.effective_chat.id, 
                    audio=audio, 
                    title=title
                )
            os.remove(filepath) # Cleanup
            await msg.delete()
        except Exception as e:
            await msg.edit_text(f"❌ Error sending audio: {e}")
            if os.path.exists(filepath): os.remove(filepath)
    else:
        await msg.edit_text("❌ Couldn't find or download that song. (Make sure ffmpeg is installed on your host)")

# ==========================================
# 5. INLINE MODE HANDLER
# ==========================================

async def inline_query_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.inline_query.query
    if not query: return

    results = [
        InlineQueryResultArticle(
            id="1",
            title="Play Song in Group",
            description=f"Send a command to play '{query}'",
            input_message_content=InputTextMessageContent(
                message_text=f"/play {query}"
            )
        )
    ]
    await update.inline_query.answer(results, cache_time=5)

# ==========================================
# 6. AUTO-REPLY & CHAT HANDLER
# ==========================================

async def chat_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.lower() if update.message.text else ""
    should_reply = False
    response = ""

    # Check 1: Reply to bot
    if update.message.reply_to_message and update.message.reply_to_message.from_user.id == context.bot.id:
        should_reply = True
        
    # Check 2: Name mention
    if "seeya" in text:
        should_reply = True
        text = text.replace("seeya", "").strip()

    if should_reply or ("seeya" in update.message.text.lower()):
        if "hello" in text or "hi" in text or text == "":
            response = f"Hello {update.effective_user.first_name}! How can I help you today? 🥰"
        elif "how are you" in text:
            response = "I am doing great! Ready to play some music or manage the group! 🎶"
        elif "who are you" in text:
            response = "I am Seeya, your all-in-one Telegram assistant. I can play music, manage admins, and welcome new members!"
        elif "bye" in text:
            response = "Goodbye! See you soon. 👋"
        else:
            responses = [
                "That's interesting! Tell me more.",
                "I'm here to help! Do you want to play a song?",
                "I'm just a bot, but I'm learning fast! 🤖",
                "Use `/help` to see what I can do!"
            ]
            response = random.choice(responses)

        await update.message.reply_text(response)

# ==========================================
# 7. MAIN APPLICATION
# ==========================================

def main():
    # ⚠️ REPLACE WITH YOUR NEW TOKEN
    TOKEN = "YOUR_NEW_BOT_TOKEN_HERE" 

    app = Application.builder().token(TOKEN).build()

    # Basic Commands
    app.add_handler(CommandHandler("start", start_cmd))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("id", get_id))

    # Admin Commands
    app.add_handler(CommandHandler("add", add_user))
    app.add_handler(CommandHandler("kick", kick_user))
    app.add_handler(CommandHandler("ban", ban_user))
    app.add_handler(CommandHandler("unban", unban_user))
    app.add_handler(CommandHandler("mute", mute_user))
    app.add_handler(CommandHandler("unmute", unmute_user))
    app.add_handler(CommandHandler("promote", promote_user))
    app.add_handler(CommandHandler("demote", demote_user))
    app.add_handler(CommandHandler("pin", pin_message))
    app.add_handler(CommandHandler("unpin", unpin_message))

    # Welcome Banner
    app.add_handler(ChatMemberHandler(welcome_new_member, ChatMemberHandler.CHAT_MEMBER))

    # Music Handlers (Both Commands and Regex Trigger)
    app.add_handler(CommandHandler("play", music_handler))
    app.add_handler(MessageHandler(filters.Regex(r'(?i)\b(play|search|found song)\b') & ~filters.COMMAND, music_handler))

    # Inline Mode
    app.add_handler(InlineQueryHandler(inline_query_handler))

    # Chat / Auto-Reply
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, chat_handler))

    logger.info("Bot is running...")
    app.run_polling(drop_pending_updates=True)

if __name__ == '__main__':
    main()
