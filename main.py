import os
import uuid
import time
import shutil
import psutil
import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton, Message, CallbackQuery

from config import (
    BOT_TOKEN,
    OWNER_ID,
    ADMIN_IDS,
    MAX_FILES_PER_USER,
    MAX_CONCURRENT_PROCESSES_PER_USER,
    MAX_FILE_SIZE_MB
)
from db_manager import db
from security_scanner import SecurityScanner
from process_manager import pm

bot = telebot.TeleBot(BOT_TOKEN, parse_mode="Markdown")


# ==========================================
# CRASH & REAL-TIME ERROR CALLBACK
# ==========================================
def notify_user_on_crash(user_id: int, file_id: str, file_name: str, exit_code: int, error_log: str):
    """Triggered automatically by ProcessManager watcher thread if a hosted script crashes."""
    user = db.get_user(user_id)
    if user and not user.get("settings", {}).get("notify_on_crash", True):
        return

    auto_restart = user.get("settings", {}).get("auto_restart_on_crash", False) if user else False
    restart_note = "\n🔄 *Auto-Restart is enabled. Rebooting script in 3s...*" if auto_restart else ""

    text = (
        f"⚠️ *Real-Time Crash Alert*\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"📄 *File:* `{file_name}`\n"
        f"🆔 *File ID:* `{file_id}`\n"
        f"🛑 *Exit Code:* `{exit_code}`\n\n"
        f"📜 *Traceback / Terminal Error Output:*\n"
        f"```text\n{error_log[-2500:]}\n```"
        f"{restart_note}"
    )
    markup = InlineKeyboardMarkup()
    markup.row(
        InlineKeyboardButton("🔄 Restart Now", callback_data=f"file_start:{file_id}"),
        InlineKeyboardButton("📜 Full Logs", callback_data=f"file_logs:{file_id}")
    )
    try:
        bot.send_message(user_id, text, reply_markup=markup)
    except Exception:
        pass


# ==========================================
# KEYBOARD BUILDERS
# ==========================================
def main_menu_keyboard(user_id: int) -> InlineKeyboardMarkup:
    markup = InlineKeyboardMarkup(row_width=2)
    markup.add(
        InlineKeyboardButton("📤 Upload (.py / .zip)", callback_data="menu_upload"),
        InlineKeyboardButton("⚡ Auto-Host (GitHub)", callback_data="menu_github"),
        InlineKeyboardButton("📁 File Records", callback_data="menu_files"),
        InlineKeyboardButton("🟢 Active Sessions", callback_data="menu_sessions"),
        InlineKeyboardButton("💻 Terminal (Pip)", callback_data="menu_terminal"),
        InlineKeyboardButton("⚙️ Settings & Env", callback_data="menu_settings")
    )
    if user_id in ADMIN_IDS:
        markup.add(InlineKeyboardButton("👑 Owner Control Panel", callback_data="owner_panel"))
    return markup


def file_control_keyboard(file_id: str) -> InlineKeyboardMarkup:
    running = pm.is_running(file_id)
    f_rec = db.get_file(file_id) or {}
    entrypoints = f_rec.get("available_entrypoints", [])

    markup = InlineKeyboardMarkup(row_width=2)
    if running:
        markup.add(
            InlineKeyboardButton("🛑 Stop Process", callback_data=f"file_stop:{file_id}"),
            InlineKeyboardButton("🔄 Restart", callback_data=f"file_restart:{file_id}")
        )
    else:
        markup.add(
            InlineKeyboardButton("▶️ Start Hosting", callback_data=f"file_start:{file_id}"),
            InlineKeyboardButton("📦 Check / Install Reqs", callback_data=f"file_reqs:{file_id}")
        )

    if len(entrypoints) > 1:
        markup.add(InlineKeyboardButton("🔀 Switch Entry File (.py)", callback_data=f"file_entry_menu:{file_id}"))

    markup.add(
        InlineKeyboardButton("📜 Live Terminal Logs", callback_data=f"file_logs:{file_id}"),
        InlineKeyboardButton("🛡️ Rescan Security", callback_data=f"file_scan:{file_id}"),
        InlineKeyboardButton("🗑️ Delete Project", callback_data=f"file_delete:{file_id}"),
        InlineKeyboardButton("🔙 Back to Files", callback_data="menu_files")
    )
    return markup


def settings_keyboard(user_id: int) -> InlineKeyboardMarkup:
    user = db.get_user(user_id) or {}
    settings = user.get("settings", {})
    auto_restart = "✅ ON" if settings.get("auto_restart_on_crash") else "❌ OFF"
    crash_notify = "✅ ON" if settings.get("notify_on_crash", True) else "❌ OFF"
    auto_scan = "✅ ON" if settings.get("auto_scan_imports", True) else "❌ OFF"

    markup = InlineKeyboardMarkup(row_width=1)
    markup.add(
        InlineKeyboardButton(f"🔄 Auto-Restart on Crash: {auto_restart}", callback_data="toggle_setting:auto_restart_on_crash"),
        InlineKeyboardButton(f"🔔 Crash Traceback Alerts: {crash_notify}", callback_data="toggle_setting:notify_on_crash"),
        InlineKeyboardButton(f"🔍 Auto-Check Imports on Upload: {auto_scan}", callback_data="toggle_setting:auto_scan_imports"),
        InlineKeyboardButton("🔐 Add Environment Variable (KEY=VALUE)", callback_data="env_add"),
        InlineKeyboardButton("🧹 Clear All Environment Variables", callback_data="env_clear"),
        InlineKeyboardButton("🔙 Main Menu", callback_data="menu_home")
    )
    return markup


def owner_panel_keyboard() -> InlineKeyboardMarkup:
    markup = InlineKeyboardMarkup(row_width=2)
    markup.add(
        InlineKeyboardButton("📊 Server Telemetry", callback_data="owner_stats"),
        InlineKeyboardButton("🔥 All Active Processes", callback_data="owner_processes"),
        InlineKeyboardButton("👥 User Directory", callback_data="owner_users"),
        InlineKeyboardButton("🛑 Kill All Processes", callback_data="owner_killall"),
        InlineKeyboardButton("🚫 Ban / Unban User", callback_data="owner_ban_prompt"),
        InlineKeyboardButton("📢 Broadcast Message", callback_data="owner_broadcast"),
        InlineKeyboardButton("💾 Export Database JSON", callback_data="owner_export_db"),
        InlineKeyboardButton("🔙 Main Menu", callback_data="menu_home")
    )
    return markup


# ==========================================
# SLASH COMMAND HANDLERS
# ==========================================
@bot.message_handler(commands=["start", "help"])
def cmd_start(message: Message):
    user_id = message.from_user.id
    if db.is_banned(user_id):
        bot.reply_to(message, "🚫 *Access Denied:* Your account has been suspended by the administrator.")
        return

    db.register_user(user_id, message.from_user.username, message.from_user.first_name)
    db.set_user_state(user_id, "IDLE")

    user_files = db.get_user_files(user_id)
    active_sessions = db.get_user_sessions(user_id)

    welcome_text = (
        f"⚡ *Cloud Python Hoster & Terminal Bot*\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"👤 *User:* `{message.from_user.first_name}` (`{user_id}`)\n"
        f"📁 *Hosted Projects:* `{len(user_files)}/{MAX_FILES_PER_USER}`\n"
        f"🟢 *Running Sessions:* `{len(active_sessions)}/{MAX_CONCURRENT_PROCESSES_PER_USER}`\n\n"
        f"🔹 *Core Capabilities:*\n"
        f"• Upload single `.py` scripts or multi-file `.zip` archives\n"
        f"• AST & Regex malicious code scanner + ZipSlip protection\n"
        f"• Interactive `pip install xyz` terminal emulation\n"
        f"• Automatic `.zip` & GitHub deployment with live error reporting\n\n"
        f"👇 *Select an operation from the control dashboard below:*"
    )
    bot.send_message(message.chat.id, welcome_text, reply_markup=main_menu_keyboard(user_id))


@bot.message_handler(commands=["terminal", "pip"])
def cmd_terminal(message: Message):
    user_id = message.from_user.id
    if db.is_banned(user_id):
        return
    db.set_user_state(user_id, "TERMINAL_PIP")
    markup = InlineKeyboardMarkup()
    markup.add(InlineKeyboardButton("🔙 Exit Terminal", callback_data="menu_home"))
    bot.reply_to(
        message,
        "💻 *Interactive Pip Terminal Activated*\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "Send any command in the format:\n"
        "`pip install package_name`\n"
        "or multiple packages:\n"
        "`pip install requests aiohttp beautifulsoup4`\n\n"
        "The bot will download and install each requirement sequentially in real time.",
        reply_markup=markup
    )


@bot.message_handler(commands=["autohost"])
def cmd_autohost(message: Message):
    user_id = message.from_user.id
    if db.is_banned(user_id):
        return
    db.set_user_state(user_id, "AWAITING_GITHUB_URL")
    markup = InlineKeyboardMarkup()
    markup.add(InlineKeyboardButton("🔙 Cancel", callback_data="menu_home"))
    bot.reply_to(
        message,
        "⚡ *GitHub Auto-Hoster*\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "Send your public GitHub repository link (e.g. `https://github.com/username/repo`).\n"
        "Or upload a `.zip` multi-file project directly into the chat!",
        reply_markup=markup
    )


@bot.message_handler(commands=["owner"])
def cmd_owner(message: Message):
    if message.from_user.id not in ADMIN_IDS:
        bot.reply_to(message, "⛔ *Unauthorized:* Owner privileges required.")
        return
    bot.send_message(
        message.chat.id,
        "👑 *Owner Administration Panel*\n━━━━━━━━━━━━━━━━━━━━\nManage all hosted files, active processes, database records, and system security:",
        reply_markup=owner_panel_keyboard()
    )


# ==========================================
# DOCUMENT UPLOAD HANDLER (.py, .zip, requirements.txt)
# ==========================================
@bot.message_handler(content_types=["document"])
def handle_document_upload(message: Message):
    user_id = message.from_user.id
    if db.is_banned(user_id):
        bot.reply_to(message, "🚫 You are banned from using this hoster.")
        return

    db.register_user(user_id, message.from_user.username, message.from_user.first_name)
    doc = message.document
    file_name = doc.file_name or "script.py"

    if doc.file_size and doc.file_size > MAX_FILE_SIZE_MB * 1024 * 1024:
        bot.reply_to(message, f"❌ File exceeds maximum upload size limit of `{MAX_FILE_SIZE_MB} MB`.")
        return

    # 1. Handle direct requirements.txt upload
    if file_name.lower() == "requirements.txt":
        status_msg = bot.reply_to(message, "📥 Downloading `requirements.txt` and parsing packages...")
        file_info = bot.get_file(doc.file_id)
        downloaded = bot.download_file(file_info.file_path)
        lines = [
            line.split("#")[0].strip()
            for line in downloaded.decode("utf-8", errors="ignore").splitlines()
            if line.split("#")[0].strip() and not line.strip().startswith("-")
        ]
        if not lines:
            bot.edit_message_text("⚠️ No valid packages found in `requirements.txt`.", message.chat.id, status_msg.message_id)
            return

        _install_packages_sequentially(message.chat.id, status_msg.message_id, user_id, lines)
        return

    user_files = db.get_user_files(user_id)
    if len(user_files) >= MAX_FILES_PER_USER and user_id not in ADMIN_IDS:
        bot.reply_to(message, f"⚠️ Quota reached (`{MAX_FILES_PER_USER}` projects max). Delete an old file first.")
        return

    # 2. Handle Multi-File .ZIP Project Archive Upload (Auto-Extract, Scan, Install & Run)
    if file_name.lower().endswith(".zip"):
        status_msg = bot.reply_to(message, f"📥 Downloading project archive `{file_name}`...")
        user_workspace = pm.get_user_workspace(user_id)
        temp_zip_path = os.path.join(user_workspace, f"tmp_{uuid.uuid4().hex[:6]}_{os.path.basename(file_name)}")

        try:
            file_info = bot.get_file(doc.file_id)
            downloaded_bytes = bot.download_file(file_info.file_path)
            with open(temp_zip_path, "wb") as f:
                f.write(downloaded_bytes)
        except Exception as e:
            bot.edit_message_text(f"❌ Download failed: `{str(e)}`", message.chat.id, status_msg.message_id)
            return

        def update_zip_progress(step_text: str):
            try:
                bot.edit_message_text(
                    f"📦 *ZIP Multi-File Auto-Deployer*\n━━━━━━━━━━━━━━━━━━━━\n{step_text}",
                    message.chat.id,
                    status_msg.message_id
                )
            except Exception:
                pass

        res = pm.extract_and_prepare_zip(user_id, temp_zip_path, file_name, update_zip_progress)
        if not res["success"]:
            markup = InlineKeyboardMarkup()
            markup.add(InlineKeyboardButton("🔙 Main Menu", callback_data="menu_home"))
            bot.edit_message_text(
                f"❌ *ZIP Project Deployment Failed*\n━━━━━━━━━━━━━━━━━━━━\n{res['error']}",
                message.chat.id,
                status_msg.message_id,
                reply_markup=markup
            )
            return

        file_id = uuid.uuid4().hex[:8]
        display_name = f"{res['project_name']}/{res['entry_name']}"
        db.add_file_record(
            file_id=file_id,
            user_id=user_id,
            file_name=display_name,
            file_path=res["entry_point"],
            source_type="zip",
            requirements=res["requirements"],
            scan_status="CLEAN",
            project_dir=res["project_dir"],
            available_entrypoints=res["available_entrypoints"]
        )
        db.set_user_state(user_id, "IDLE")

        # Automatically start the extracted project if user has available process slots
        active_sessions = db.get_user_sessions(user_id)
        if len(active_sessions) < MAX_CONCURRENT_PROCESSES_PER_USER or user_id in ADMIN_IDS:
            update_zip_progress(f"🚀 Launching `{display_name}` in real-time...")
            pm.start_file(file_id, on_crash_callback=notify_user_on_crash)
            time.sleep(2.5)

        still_running = pm.is_running(file_id)
        logs_preview = pm.read_logs(file_id, lines=15)
        status_header = "✅ *ZIP Project Extracted & Running!*" if still_running else "⚠️ *ZIP Project Extracted (Exited / Stopped)*"

        bot.edit_message_text(
            f"{status_header}\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"📦 *Project:* `{res['project_name']}`\n"
            f"🎯 *Active Entry File:* `{res['entry_name']}` (`{file_id}`)\n"
            f"📄 *Python Files Found:* `{len(res['available_entrypoints'])}`\n"
            f"🟢 *Status:* `{'RUNNING' if still_running else 'STOPPED'}`\n\n"
            f"📜 *Initial Console Output:*\n```text\n{logs_preview}\n```",
            message.chat.id,
            status_msg.message_id,
            reply_markup=file_control_keyboard(file_id)
        )
        return

    # 3. Handle Single .PY File Upload
    if not file_name.lower().endswith(".py"):
        bot.reply_to(message, "⚠️ Supported uploads: `.py` scripts, `.zip` multi-file projects, or `requirements.txt`.")
        return

    status_msg = bot.reply_to(message, f"📥 Downloading `{file_name}` into isolated workspace...")
    user_workspace = pm.get_user_workspace(user_id)
    file_id = uuid.uuid4().hex[:8]
    safe_name = os.path.basename(file_name)
    save_path = os.path.join(user_workspace, f"{file_id}_{safe_name}")

    try:
        file_info = bot.get_file(doc.file_id)
        downloaded_bytes = bot.download_file(file_info.file_path)
        with open(save_path, "wb") as f:
            f.write(downloaded_bytes)
    except Exception as e:
        bot.edit_message_text(f"❌ Download failed: `{str(e)}`", message.chat.id, status_msg.message_id)
        return

    bot.edit_message_text(f"🛡️ Scanning `{safe_name}` for malicious code & analyzing AST imports...", message.chat.id, status_msg.message_id)
    scan_res = SecurityScanner.scan_file(save_path)

    if not scan_res["is_safe"]:
        os.remove(save_path)
        db.increment_stat("blocked_malicious_files")
        reasons_formatted = "\n".join([f"• {r}" for r in scan_res["reasons"]])
        bot.edit_message_text(
            f"🚨 *Security Alert: File Rejected*\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"📄 *File:* `{safe_name}`\n"
            f"⚠️ *Risk Level:* `{scan_res['risk_level']}`\n\n"
            f"*Detected Issues:*\n{reasons_formatted}\n\n"
            f"_The file has been purged from the server._",
            message.chat.id,
            status_msg.message_id
        )
        return

    detected_reqs = scan_res["imports"]
    missing_reqs = pm.check_missing_requirements(detected_reqs)

    db.add_file_record(
        file_id=file_id,
        user_id=user_id,
        file_name=safe_name,
        file_path=save_path,
        source_type="upload",
        requirements=detected_reqs,
        scan_status="CLEAN"
    )
    db.set_user_state(user_id, "IDLE")

    reqs_str = ", ".join([f"`{r}`" for r in detected_reqs]) if detected_reqs else "`None (Standard Library only)`"
    missing_str = ", ".join([f"`{r}`" for r in missing_reqs]) if missing_reqs else "`All Installed ✅`"

    markup = file_control_keyboard(file_id)
    if missing_reqs:
        markup.add(InlineKeyboardButton("⚡ Auto-Install Missing Requirements Now", callback_data=f"file_autoinstall:{file_id}"))

    bot.edit_message_text(
        f"✅ *File Uploaded & Verified Clean*\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"📄 *File Name:* `{safe_name}`\n"
        f"🆔 *File ID:* `{file_id}`\n"
        f"🛡️ *Security Scan:* `{scan_res['risk_level']}`\n"
        f"📦 *Detected Imports:* {reqs_str}\n"
        f"❗ *Missing Packages:* {missing_str}\n\n"
        f"💡 _Tip: Click Auto-Install below, or send `pip install <pkg>` in chat to install packages one by one._",
        message.chat.id,
        status_msg.message_id,
        reply_markup=markup
    )


# ==========================================
# HELPER: SEQUENTIAL ONE-BY-ONE PIP INSTALLER
# ==========================================
def _install_packages_sequentially(chat_id: int, message_id: int, user_id: int, packages: list) -> bool:
    results_log = []
    all_ok = True

    for idx, pkg in enumerate(packages, start=1):
        progress_header = (
            f"💻 *Terminal Pip Installer ({idx}/{len(packages)})*\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"⏳ Executing: `pip install {pkg}`...\n\n"
        )
        prev_summary = "\n".join(results_log[-6:])
        try:
            bot.edit_message_text(progress_header + prev_summary, chat_id, message_id)
        except Exception:
            pass

        ok, output = pm.install_single_package(user_id, pkg)
        if ok:
            results_log.append(f"✅ `{pkg}` — Installed successfully")
        else:
            all_ok = False
            results_log.append(f"❌ `{pkg}` — Failed:\n```text\n{output[-400:]}\n```")

    final_status = "✅ *All Requirements Installed!*" if all_ok else "⚠️ *Requirement Installation Finished with Errors*"
    final_text = f"{final_status}\n━━━━━━━━━━━━━━━━━━━━\n" + "\n".join(results_log)
    markup = InlineKeyboardMarkup()
    markup.add(
        InlineKeyboardButton("📁 Go to My Files", callback_data="menu_files"),
        InlineKeyboardButton("🏠 Main Menu", callback_data="menu_home")
    )
    try:
        bot.edit_message_text(final_text, chat_id, message_id, reply_markup=markup)
    except Exception:
        bot.send_message(chat_id, final_text, reply_markup=markup)
    return all_ok


# ==========================================
# STATEFUL TEXT MESSAGE HANDLER
# ==========================================
@bot.message_handler(func=lambda m: True, content_types=["text"])
def handle_text_states(message: Message):
    user_id = message.from_user.id
    if db.is_banned(user_id):
        return

    user = db.register_user(user_id, message.from_user.username, message.from_user.first_name)
    state = user.get("state", "IDLE")
    text = message.text.strip()

    # Direct `pip install xyz` from ANY state
    if text.lower().startswith("pip install "):
        raw_pkgs = text[len("pip install "):].strip().split()
        pkgs = [p for p in raw_pkgs if not p.startswith("-")]
        if not pkgs:
            bot.reply_to(message, "⚠️ Specify at least one package name. Example: `pip install requests`")
            return
        msg = bot.reply_to(message, f"💻 Initializing `pip` sequence for `{len(pkgs)}` package(s)...")
        _install_packages_sequentially(message.chat.id, msg.message_id, user_id, pkgs)
        return

    # 1. GITHUB AUTO-HOST FLOW
    if state == "AWAITING_GITHUB_URL":
        db.set_user_state(user_id, "IDLE")
        status_msg = bot.reply_to(message, "⏳ Initializing GitHub Auto-Host pipeline...")

        def update_progress(step_text: str):
            try:
                bot.edit_message_text(
                    f"⚡ *GitHub Auto-Hoster Pipeline*\n━━━━━━━━━━━━━━━━━━━━\n{step_text}",
                    message.chat.id,
                    status_msg.message_id
                )
            except Exception:
                pass

        res = pm.clone_and_prepare_github(user_id, text, update_progress)
        if not res["success"]:
            markup = InlineKeyboardMarkup()
            markup.add(InlineKeyboardButton("🔙 Main Menu", callback_data="menu_home"))
            bot.edit_message_text(
                f"❌ *GitHub Auto-Host Failed*\n━━━━━━━━━━━━━━━━━━━━\n{res['error']}",
                message.chat.id,
                status_msg.message_id,
                reply_markup=markup
            )
            return

        file_id = uuid.uuid4().hex[:8]
        display_name = f"{res['project_name']}/{res['entry_name']}"
        db.add_file_record(
            file_id=file_id,
            user_id=user_id,
            file_name=display_name,
            file_path=res["entry_point"],
            source_type="github",
            requirements=res["requirements"],
            scan_status="CLEAN",
            project_dir=res["project_dir"],
            available_entrypoints=res["available_entrypoints"]
        )

        update_progress(f"🚀 Starting `{display_name}` in real-time...")
        pm.start_file(file_id, on_crash_callback=notify_user_on_crash)

        time.sleep(2.5)
        still_running = pm.is_running(file_id)
        logs_preview = pm.read_logs(file_id, lines=15)
        header = "✅ *GitHub Repo Hosted & Running!*" if still_running else "⚠️ *GitHub Repo Deployed, But Exited Immediately*"

        bot.edit_message_text(
            f"{header}\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"📦 *Repository:* `{res['project_name']}`\n"
            f"📄 *Entry File:* `{res['entry_name']}` (`{file_id}`)\n\n"
            f"📜 *Console Output:*\n```text\n{logs_preview}\n```",
            message.chat.id,
            status_msg.message_id,
            reply_markup=file_control_keyboard(file_id)
        )
        return

    # 2. ENVIRONMENT VARIABLE INPUT FLOW
    if state == "AWAITING_ENV_VAR":
        if "=" not in text:
            bot.reply_to(message, "⚠️ Invalid format. Send as `KEY=VALUE` (e.g. `API_KEY=12345`).")
            return
        k, v = text.split("=", 1)
        k, v = k.strip(), v.strip()
        db.set_user_env_var(user_id, k, v)
        db.set_user_state(user_id, "IDLE")
        bot.reply_to(
            message,
            f"✅ Environment variable `{k}` saved! It will be injected into all your hosted scripts.",
            reply_markup=settings_keyboard(user_id)
        )
        return

    # 3. OWNER BAN / UNBAN FLOW
    if state == "OWNER_AWAITING_BAN" and user_id in ADMIN_IDS:
        db.set_user_state(user_id, "IDLE")
        try:
            target_id = int(text)
        except ValueError:
            bot.reply_to(message, "⚠️ Invalid User ID. Send a numeric Telegram User ID.")
            return

        if db.is_banned(target_id):
            db.unban_user(target_id)
            bot.reply_to(message, f"✅ User `{target_id}` has been *UNBANNED*.", reply_markup=owner_panel_keyboard())
        else:
            db.ban_user(target_id)
            for sess in db.get_user_sessions(target_id):
                pm.stop_file(sess["file_id"])
            bot.reply_to(message, f"🚫 User `{target_id}` has been *BANNED* and their processes were terminated.", reply_markup=owner_panel_keyboard())
        return

    # 4. OWNER BROADCAST FLOW
    if state == "OWNER_AWAITING_BROADCAST" and user_id in ADMIN_IDS:
        db.set_user_state(user_id, "IDLE")
        users = db.get_all_users()
        sent = 0
        for uid in users.keys():
            try:
                bot.send_message(int(uid), f"📢 *System Announcement*\n━━━━━━━━━━━━━━━━━━━━\n{text}")
                sent += 1
            except Exception:
                pass
        bot.reply_to(message, f"✅ Broadcast delivered to `{sent}/{len(users)}` users.", reply_markup=owner_panel_keyboard())
        return

    bot.reply_to(
        message,
        "💡 Upload a `.py` script or `.zip` project to host it, type `pip install <package>`, or use the dashboard below:",
        reply_markup=main_menu_keyboard(user_id)
    )


# ==========================================
# CALLBACK QUERY ROUTER
# ==========================================
@bot.callback_query_handler(func=lambda call: True)
def handle_callbacks(call: CallbackQuery):
    user_id = call.from_user.id
    if db.is_banned(user_id):
        bot.answer_callback_query(call.id, "🚫 Access denied.", show_alert=True)
        return

    data = call.data

    if data == "menu_home":
        db.set_user_state(user_id, "IDLE")
        user_files = db.get_user_files(user_id)
        active_sessions = db.get_user_sessions(user_id)
        bot.edit_message_text(
            f"⚡ *Cloud Python Hoster Dashboard*\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"👤 *User ID:* `{user_id}`\n"
            f"📁 *Saved Projects:* `{len(user_files)}/{MAX_FILES_PER_USER}`\n"
            f"🟢 *Active Processes:* `{len(active_sessions)}/{MAX_CONCURRENT_PROCESSES_PER_USER}`\n\n"
            f"Select an action below:",
            call.message.chat.id,
            call.message.message_id,
            reply_markup=main_menu_keyboard(user_id)
        )

    elif data == "menu_upload":
        db.set_user_state(user_id, "AWAITING_UPLOAD")
        markup = InlineKeyboardMarkup()
        markup.add(InlineKeyboardButton("🔙 Back", callback_data="menu_home"))
        bot.edit_message_text(
            "📤 *Upload Script or Multi-File ZIP Project*\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "Send any of the following as a Telegram document attachment:\n\n"
            "1️⃣ **`.zip` Project Archive:** Automatically extracts, scans all files for security risks, installs `requirements.txt` packages one by one, and runs your main script.\n"
            "2️⃣ **`.py` Single Script:** Scans AST imports and prepares interactive hosting.\n"
            "3️⃣ **`requirements.txt`:** Batch-installs listed dependencies one by one.",
            call.message.chat.id,
            call.message.message_id,
            reply_markup=markup
        )

    elif data == "menu_github":
        db.set_user_state(user_id, "AWAITING_GITHUB_URL")
        markup = InlineKeyboardMarkup()
        markup.add(InlineKeyboardButton("🔙 Back", callback_data="menu_home"))
        bot.edit_message_text(
            "⚡ *Auto-Host GitHub Repository*\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "Send a public GitHub URL (e.g. `https://github.com/user/repo`).\n\n"
            "The bot will clone the repository, scan all files, install `requirements.txt` packages one by one, and immediately start your project.",
            call.message.chat.id,
            call.message.message_id,
            reply_markup=markup
        )

    elif data == "menu_terminal":
        db.set_user_state(user_id, "TERMINAL_PIP")
        user = db.get_user(user_id) or {}
        installed = user.get("installed_packages", [])
        recent_pkgs = ", ".join([f"`{p}`" for p in installed[-10:]]) if installed else "`None recorded yet`"
        markup = InlineKeyboardMarkup()
        markup.add(InlineKeyboardButton("🔙 Back", callback_data="menu_home"))
        bot.edit_message_text(
            f"💻 *Interactive Requirement Terminal*\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"Type and send any `pip install` command directly in chat:\n"
            f"• `pip install pyTelegramBotAPI`\n"
            f"• `pip install requests flask aiohttp`\n\n"
            f"📦 *Recently Installed by You:*\n{recent_pkgs}",
            call.message.chat.id,
            call.message.message_id,
            reply_markup=markup
        )

    elif data == "menu_files":
        files = db.get_user_files(user_id)
        markup = InlineKeyboardMarkup(row_width=1)
        if not files:
            markup.add(
                InlineKeyboardButton("📤 Upload First File / ZIP", callback_data="menu_upload"),
                InlineKeyboardButton("🔙 Main Menu", callback_data="menu_home")
            )
            bot.edit_message_text(
                "📁 *Your File & Project Records*\n━━━━━━━━━━━━━━━━━━━━\nYou haven't uploaded any files or `.zip` projects yet.",
                call.message.chat.id,
                call.message.message_id,
                reply_markup=markup
            )
            return

        for f in files:
            fid = f["file_id"]
            running = pm.is_running(fid)
            badge = "🟢 RUNNING" if running else f"🔴 {f.get('status', 'STOPPED')}"
            src_icon = "📦" if f.get("source_type") in ("zip", "github") else "📄"
            markup.add(
                InlineKeyboardButton(f"{badge} | {src_icon} {f['file_name']} ({fid})", callback_data=f"file_view:{fid}")
            )
        markup.add(InlineKeyboardButton("🔙 Main Menu", callback_data="menu_home"))
        bot.edit_message_text(
            f"📁 *Your Hosted Projects ({len(files)}/{MAX_FILES_PER_USER})*\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"Select any file or `.zip` project below to start/stop hosting, switch entry points, or view live logs:",
            call.message.chat.id,
            call.message.message_id,
            reply_markup=markup
        )

    elif data == "menu_sessions":
        sessions = db.get_user_sessions(user_id)
        markup = InlineKeyboardMarkup(row_width=1)
        if not sessions:
            markup.add(InlineKeyboardButton("📁 Start a Project", callback_data="menu_files"))
            markup.add(InlineKeyboardButton("🔙 Main Menu", callback_data="menu_home"))
            bot.edit_message_text(
                "🟢 *Active Running Sessions*\n━━━━━━━━━━━━━━━━━━━━\nNo scripts are currently running.",
                call.message.chat.id,
                call.message.message_id,
                reply_markup=markup
            )
            return

        lines = ["🟢 *Active Running Sessions*\n━━━━━━━━━━━━━━━━━━━━"]
        for s in sessions:
            fid = s["file_id"]
            f_rec = db.get_file(fid) or {"file_name": "Unknown"}
            metrics = pm.get_process_metrics(fid)
            lines.append(
                f"📄 `{f_rec['file_name']}` (`{fid}`)\n"
                f"   ├ PID: `{metrics['pid']}` | Uptime: `{metrics['uptime']}`\n"
                f"   └ CPU: `{metrics['cpu']}%` | RAM: `{metrics['ram_mb']} MB`"
            )
            markup.add(InlineKeyboardButton(f"⚙️ Manage {f_rec['file_name']} ({fid})", callback_data=f"file_view:{fid}"))
        markup.add(
            InlineKeyboardButton("🔄 Refresh Metrics", callback_data="menu_sessions"),
            InlineKeyboardButton("🔙 Main Menu", callback_data="menu_home")
        )
        bot.edit_message_text("\n\n".join(lines), call.message.chat.id, call.message.message_id, reply_markup=markup)

    elif data == "menu_settings":
        user = db.get_user(user_id) or {}
        env_vars = user.get("settings", {}).get("env_vars", {})
        env_display = "\n".join([f"• `{k}` = `{v[:4]}***`" for k, v in env_vars.items()]) if env_vars else "`No custom environment variables set`"
        bot.edit_message_text(
            f"⚙️ *User Hosting Settings & Environment*\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"🔐 *Configured Environment Variables:*\n{env_display}\n\n"
            f"Toggle your execution preferences below:",
            call.message.chat.id,
            call.message.message_id,
            reply_markup=settings_keyboard(user_id)
        )

    elif data.startswith("toggle_setting:"):
        key = data.split(":", 1)[1]
        user = db.get_user(user_id) or {}
        current = user.get("settings", {}).get(key, False)
        db.update_user_setting(user_id, key, not current)
        bot.answer_callback_query(call.id, "Setting updated!")
        call.data = "menu_settings"
        handle_callbacks(call)

    elif data == "env_add":
        db.set_user_state(user_id, "AWAITING_ENV_VAR")
        markup = InlineKeyboardMarkup()
        markup.add(InlineKeyboardButton("🔙 Cancel", callback_data="menu_settings"))
        bot.edit_message_text(
            "🔐 *Add Environment Variable*\n━━━━━━━━━━━━━━━━━━━━\nSend your variable in `KEY=VALUE` format (e.g. `API_ID=987654`).",
            call.message.chat.id,
            call.message.message_id,
            reply_markup=markup
        )

    elif data == "env_clear":
        db.clear_user_env_vars(user_id)
        bot.answer_callback_query(call.id, "All custom environment variables cleared!", show_alert=True)
        call.data = "menu_settings"
        handle_callbacks(call)

    # --- INDIVIDUAL FILE & MULTI-FILE PROJECT OPERATIONS ---
    elif data.startswith("file_view:"):
        fid = data.split(":", 1)[1]
        _render_file_details(call, fid)

    elif data.startswith("file_entry_menu:"):
        fid = data.split(":", 1)[1]
        f_rec = db.get_file(fid)
        if not f_rec:
            return
        entrypoints = f_rec.get("available_entrypoints", [])
        markup = InlineKeyboardMarkup(row_width=1)
        for idx, ep in enumerate(entrypoints):
            markup.add(InlineKeyboardButton(f"🎯 Set Entry: {ep}", callback_data=f"file_set_entry:{fid}:{idx}"))
        markup.add(InlineKeyboardButton("🔙 Back to Project", callback_data=f"file_view:{fid}"))
        bot.edit_message_text(
            f"🔀 *Select Entry Point File*\n━━━━━━━━━━━━━━━━━━━━\nChoose which `.py` script should be executed when starting `{f_rec['file_name']}`:",
            call.message.chat.id,
            call.message.message_id,
            reply_markup=markup
        )

    elif data.startswith("file_set_entry:"):
        _, fid, idx_str = data.split(":")
        f_rec = db.get_file(fid)
        if not f_rec:
            return
        entrypoints = f_rec.get("available_entrypoints", [])
        idx = int(idx_str)
        if 0 <= idx < len(entrypoints):
            new_entry = entrypoints[idx]
            was_running = pm.is_running(fid)
            if was_running:
                pm.stop_file(fid)
            db.update_file_entrypoint(fid, new_entry)
            if was_running:
                pm.start_file(fid, on_crash_callback=notify_user_on_crash)
            bot.answer_callback_query(call.id, f"Entry point updated to {new_entry}!")
        _render_file_details(call, fid)

    elif data.startswith("file_start:"):
        fid = data.split(":", 1)[1]
        active = db.get_user_sessions(user_id)
        if len(active) >= MAX_CONCURRENT_PROCESSES_PER_USER and user_id not in ADMIN_IDS:
            bot.answer_callback_query(
                call.id,
                f"⚠️ Concurrent process limit ({MAX_CONCURRENT_PROCESSES_PER_USER}) reached. Stop another process first.",
                show_alert=True
            )
            return
        ok, msg = pm.start_file(fid, on_crash_callback=notify_user_on_crash)
        bot.answer_callback_query(call.id, msg[:190], show_alert=not ok)
        _render_file_details(call, fid)

    elif data.startswith("file_stop:"):
        fid = data.split(":", 1)[1]
        ok, msg = pm.stop_file(fid)
        bot.answer_callback_query(call.id, msg[:190], show_alert=False)
        _render_file_details(call, fid)

    elif data.startswith("file_restart:"):
        fid = data.split(":", 1)[1]
        pm.stop_file(fid)
        time.sleep(0.5)
        ok, msg = pm.start_file(fid, on_crash_callback=notify_user_on_crash)
        bot.answer_callback_query(call.id, f"Restarted: {msg[:160]}", show_alert=not ok)
        _render_file_details(call, fid)

    elif data.startswith("file_logs:"):
        fid = data.split(":", 1)[1]
        f_rec = db.get_file(fid)
        if not f_rec:
            bot.answer_callback_query(call.id, "File not found.")
            return
        logs = pm.read_logs(fid, lines=35)
        markup = InlineKeyboardMarkup(row_width=2)
        markup.add(
            InlineKeyboardButton("🔄 Refresh Logs", callback_data=f"file_logs:{fid}"),
            InlineKeyboardButton("🔙 Back to File", callback_data=f"file_view:{fid}")
        )
        bot.edit_message_text(
            f"📜 *Live Console Output:* `{f_rec['file_name']}`\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"```text\n{logs}\n```",
            call.message.chat.id,
            call.message.message_id,
            reply_markup=markup
        )

    elif data.startswith("file_reqs:"):
        fid = data.split(":", 1)[1]
        f_rec = db.get_file(fid)
        if not f_rec:
            return
        reqs = f_rec.get("requirements", [])
        missing = pm.check_missing_requirements(reqs)
        markup = InlineKeyboardMarkup(row_width=1)
        if missing:
            markup.add(InlineKeyboardButton("⚡ Install Missing Packages One-by-One", callback_data=f"file_autoinstall:{fid}"))
        if reqs:
            markup.add(InlineKeyboardButton("🔄 Force Reinstall All Detected Packages", callback_data=f"file_forceinstall:{fid}"))
        markup.add(InlineKeyboardButton("🔙 Back to Project", callback_data=f"file_view:{fid}"))

        bot.edit_message_text(
            f"📦 *Dependency Inspector:* `{f_rec['file_name']}`\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"• *Detected Packages:* {', '.join([f'`{r}`' for r in reqs]) if reqs else '`None`'}\n"
            f"• *Missing Packages:* {', '.join([f'`{r}`' for r in missing]) if missing else '`None (All satisfied ✅)`'}",
            call.message.chat.id,
            call.message.message_id,
            reply_markup=markup
        )

    elif data.startswith("file_autoinstall:") or data.startswith("file_forceinstall:"):
        fid = data.split(":", 1)[1]
        force = data.startswith("file_forceinstall:")
        f_rec = db.get_file(fid)
        if not f_rec:
            return
        reqs = f_rec.get("requirements", [])
        target_pkgs = reqs if force else pm.check_missing_requirements(reqs)
        if not target_pkgs:
            bot.answer_callback_query(call.id, "✅ All requirements are already installed!", show_alert=True)
            return
        _install_packages_sequentially(call.message.chat.id, call.message.message_id, user_id, target_pkgs)

    elif data.startswith("file_scan:"):
        fid = data.split(":", 1)[1]
        f_rec = db.get_file(fid)
        if not f_rec:
            return
        proj_dir = f_rec.get("project_dir")
        res = SecurityScanner.scan_directory(proj_dir) if (proj_dir and os.path.exists(proj_dir)) else SecurityScanner.scan_file(f_rec["file_path"])
        status_alert = "✅ Clean! No malicious patterns found." if res["is_safe"] else "🚨 Malicious code detected!"
        bot.answer_callback_query(call.id, status_alert, show_alert=True)
        _render_file_details(call, fid)

    elif data.startswith("file_delete:"):
        fid = data.split(":", 1)[1]
        pm.stop_file(fid)
        f_rec = db.get_file(fid)
        if f_rec:
            proj_dir = f_rec.get("project_dir")
            try:
                if proj_dir and os.path.exists(proj_dir):
                    shutil.rmtree(proj_dir, ignore_errors=True)
                elif os.path.exists(f_rec["file_path"]):
                    os.remove(f_rec["file_path"])
            except Exception:
                pass
        db.delete_file_record(fid)
        bot.answer_callback_query(call.id, "🗑️ Project and logs deleted.")
        call.data = "menu_files"
        handle_callbacks(call)

    elif data.startswith("owner_"):
        if user_id not in ADMIN_IDS:
            bot.answer_callback_query(call.id, "⛔ Owner only!", show_alert=True)
            return
        _handle_owner_callbacks(call)


def _render_file_details(call: CallbackQuery, file_id: str):
    f_rec = db.get_file(file_id)
    if not f_rec:
        bot.answer_callback_query(call.id, "Project no longer exists.")
        return

    metrics = pm.get_process_metrics(file_id)
    status_badge = "🟢 RUNNING" if metrics["running"] else f"🔴 {f_rec.get('status', 'STOPPED')}"
    reqs = f_rec.get("requirements", [])
    missing = pm.check_missing_requirements(reqs)

    text = (
        f"📄 *Project Control Dashboard*\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"• *Active Entry:* `{f_rec['file_name']}`\n"
        f"• *Project ID:* `{file_id}`\n"
        f"• *Source Type:* `{f_rec.get('source_type', 'upload').upper()}`\n"
        f"• *Security Status:* `{f_rec.get('scan_status', 'CLEAN')}`\n"
        f"• *Current State:* `{status_badge}`\n"
        f"• *PID:* `{metrics['pid'] or 'N/A'}` | *Uptime:* `{metrics['uptime']}`\n"
        f"• *CPU / RAM:* `{metrics['cpu']}%` / `{metrics['ram_mb']} MB`\n"
        f"• *Missing Reqs:* `{len(missing)}` package(s)\n"
        f"• *Last Exit Code:* `{f_rec.get('last_exit_code', 'N/A')}`"
    )
    try:
        bot.edit_message_text(
            text,
            call.message.chat.id,
            call.message.message_id,
            reply_markup=file_control_keyboard(file_id)
        )
    except Exception:
        pass


def _handle_owner_callbacks(call: CallbackQuery):
    data = call.data

    if data == "owner_panel":
        bot.edit_message_text(
            "👑 *Owner Administration Panel*\n━━━━━━━━━━━━━━━━━━━━\nFull control over server processes, user workspaces, security logs, and database records:",
            call.message.chat.id,
            call.message.message_id,
            reply_markup=owner_panel_keyboard()
        )

    elif data == "owner_stats":
        stats = db.get_stats()
        users = db.get_all_users()
        files = db.get_all_files()
        sessions = db.get_all_sessions()
        cpu_total = psutil.cpu_percent(interval=0.2)
        ram = psutil.virtual_memory()
        disk = psutil.disk_usage("/")

        text = (
            f"📊 *Host Server & Database Telemetry*\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"🖥️ *Hardware Resources:*\n"
            f"• CPU Load: `{cpu_total}%`\n"
            f"• RAM Usage: `{ram.percent}%` (`{round(ram.used / (1024**3), 2)} / {round(ram.total / (1024**3), 2)} GB`)\n"
            f"• Disk Usage: `{disk.percent}%`\n\n"
            f"🗄️ *Database Records:*\n"
            f"• Registered Users: `{len(users)}`\n"
            f"• Hosted Projects: `{len(files)}`\n"
            f"• Active Running Sessions: `{len(sessions)}`\n"
            f"• Single `.py` Uploads: `{stats.get('total_uploads', 0)}`\n"
            f"• Multi-File `.zip` Deploys: `{stats.get('total_zip_deploys', 0)}`\n"
            f"• GitHub Auto-Deploys: `{stats.get('total_github_deploys', 0)}`\n"
            f"• Blocked Malicious Uploads: `{stats.get('blocked_malicious_files', 0)}`\n"
            f"• Total Pip Installs: `{stats.get('total_pip_installs', 0)}`"
        )
        bot.edit_message_text(text, call.message.chat.id, call.message.message_id, reply_markup=owner_panel_keyboard())

    elif data == "owner_processes":
        sessions = db.get_all_sessions()
        markup = InlineKeyboardMarkup(row_width=1)
        if not sessions:
            bot.answer_callback_query(call.id, "No active processes running on the server.", show_alert=True)
            return

        lines = ["🔥 *All Running Processes Across Server*\n━━━━━━━━━━━━━━━━━━━━"]
        for fid, sess in sessions.items():
            f_rec = db.get_file(fid) or {"file_name": "Unknown"}
            m = pm.get_process_metrics(fid)
            lines.append(
                f"• `{f_rec['file_name']}` (`{fid}`) — User `{sess['user_id']}`\n"
                f"  PID: `{m['pid']}` | CPU: `{m['cpu']}%` | RAM: `{m['ram_mb']} MB`"
            )
            markup.add(InlineKeyboardButton(f"🛑 Kill {f_rec['file_name']} (User {sess['user_id']})", callback_data=f"owner_kill:{fid}"))
        markup.add(InlineKeyboardButton("🔙 Owner Panel", callback_data="owner_panel"))
        bot.edit_message_text("\n".join(lines), call.message.chat.id, call.message.message_id, reply_markup=markup)

    elif data.startswith("owner_kill:"):
        fid = data.split(":", 1)[1]
        ok, msg = pm.stop_file(fid)
        bot.answer_callback_query(call.id, msg[:180], show_alert=True)
        call.data = "owner_processes"
        _handle_owner_callbacks(call)

    elif data == "owner_killall":
        killed = pm.stop_all_processes()
        bot.answer_callback_query(call.id, f"🛑 Terminated {killed} running process(es).", show_alert=True)
        call.data = "owner_panel"
        _handle_owner_callbacks(call)

    elif data == "owner_users":
        users = db.get_all_users()
        lines = ["👥 *Registered Users Directory*\n━━━━━━━━━━━━━━━━━━━━"]
        for uid, u in list(users.items())[:25]:
            banned_flag = "🚫 BANNED" if db.is_banned(int(uid)) else "✅ ACTIVE"
            u_files = len(db.get_user_files(int(uid)))
            lines.append(f"• `{uid}` (@{u.get('username')}) — `{u_files}` projects [{banned_flag}]")
        markup = InlineKeyboardMarkup()
        markup.add(InlineKeyboardButton("🔙 Owner Panel", callback_data="owner_panel"))
        bot.edit_message_text("\n".join(lines), call.message.chat.id, call.message.message_id, reply_markup=markup)

    elif data == "owner_ban_prompt":
        db.set_user_state(call.from_user.id, "OWNER_AWAITING_BAN")
        markup = InlineKeyboardMarkup()
        markup.add(InlineKeyboardButton("🔙 Cancel", callback_data="owner_panel"))
        bot.edit_message_text(
            "🚫 *Ban / Unban User*\n━━━━━━━━━━━━━━━━━━━━\nSend the numeric Telegram User ID to toggle their ban status and kill their active processes:",
            call.message.chat.id,
            call.message.message_id,
            reply_markup=markup
        )

    elif data == "owner_broadcast":
        db.set_user_state(call.from_user.id, "OWNER_AWAITING_BROADCAST")
        markup = InlineKeyboardMarkup()
        markup.add(InlineKeyboardButton("🔙 Cancel", callback_data="owner_panel"))
        bot.edit_message_text(
            "📢 *Global Broadcast*\n━━━━━━━━━━━━━━━━━━━━\nSend the message text you want to broadcast to all registered users:",
            call.message.chat.id,
            call.message.message_id,
            reply_markup=markup
        )

    elif data == "owner_export_db":
        from config import DB_FILE_PATH
        if os.path.exists(DB_FILE_PATH):
            with open(DB_FILE_PATH, "rb") as f:
                bot.send_document(call.message.chat.id, f, caption="💾 *Live Snapshot of `database.json`*")
        bot.answer_callback_query(call.id, "Database exported!")


if __name__ == "__main__":
    print("====================================================")
    print("🚀 Telegram Cloud Hoster Bot (Docker + ZIP Ready)!")
    print(f"👑 Owner ID configured as: {OWNER_ID}")
    print("====================================================")
    bot.infinity_polling(timeout=30, long_polling_timeout=20)
