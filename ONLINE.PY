import os
import json
import time
import requests
import threading
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes

# --- DATA FILES ---
DATA_FILE = "user_codespaces.json"
APPROVED_USERS_FILE = "approved_users.json"

def load_json_file(path, default):
    if not os.path.exists(path):
        return default
    try:
        with open(path, "r") as f:
            content = f.read().strip()
            if not content:
                return default
            return json.loads(content)
    except (json.JSONDecodeError, IOError):
        return default

# Load stored codespace data
USER_DATA = load_json_file(DATA_FILE, {})

# Load approved users
APPROVED_USERS = load_json_file(APPROVED_USERS_FILE, [])
if not os.path.exists(APPROVED_USERS_FILE):
    with open(APPROVED_USERS_FILE, "w") as f:
        json.dump(APPROVED_USERS, f)

# --- HELPERS ---

def save_data():
    with open(DATA_FILE, "w") as f:
        json.dump(USER_DATA, f)

def save_approved_users():
    with open(APPROVED_USERS_FILE, "w") as f:
        json.dump(APPROVED_USERS, f)

def github_headers(token):
    return {
        "Authorization": f"token {token}",
        "Accept": "application/vnd.github+json"
    }

def add_codespace(user_id, token, codespace_name, chat_id):
    user_id = str(user_id)
    if user_id not in USER_DATA:
        USER_DATA[user_id] = []
    for item in USER_DATA[user_id]:
        if item["codespace"] == codespace_name:
            return
    USER_DATA[user_id].append({
        "token": token,
        "codespace": codespace_name,
        "chat_id": chat_id
    })
    save_data()

def is_authorized(user_id):
    return str(user_id) in APPROVED_USERS

# --- TELEGRAM COMMANDS ---

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    if user_id in APPROVED_USERS:
        await update.message.reply_text(
            "👋 Welcome back!\n\n"
            " Bot made by VEXA to keep Codespaces 24/7 active.\n\n"
            "📌 Available Commands:\n"
            "`/token <token>` – Add all Codespaces from token\n"
            "`/list` – List Codespaces\n"
            "`/approve <user_id>` – (Owner only)\n",
            parse_mode="Markdown"
        )
    else:
        await update.message.reply_text(
            "👋 Welcome!\n\n"
            "🚫 You're not authorized to use this bot.\n"
            "Please contact the bot owner to request access."
        )

async def add_token(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not is_authorized(user_id):
        await update.message.reply_text("🚫 You're not authorized to use this bot.")
        return

    if len(context.args) != 1:
        await update.message.reply_text("❌ Usage: /token <github_token>")
        return

    token = context.args[0]
    chat_id = update.effective_chat.id

    # Fetch all codespaces from GitHub API
    url = "https://api.github.com/user/codespaces"
    resp = requests.get(url, headers=github_headers(token))

    if resp.status_code != 200:
        await update.message.reply_text(f"❌ Failed to fetch codespaces: {resp.text}")
        return

    data = resp.json()
    if "codespaces" not in data or not data["codespaces"]:
        await update.message.reply_text("📭 No codespaces found for this token.")
        return

    added = []
    for cs in data["codespaces"]:
        name = cs["name"]
        add_codespace(user_id, token, name, chat_id)
        added.append(name)

    msg = "✅ Registered the following codespaces:\n"
    for name in added:
        msg += f"- `{name}`\n"

    await update.message.reply_text(msg, parse_mode="Markdown")

async def list_codespaces(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    if not is_authorized(user_id):
        await update.message.reply_text("🚫 You're not authorized.")
        return

    codespaces = USER_DATA.get(user_id, [])
    if not codespaces:
        await update.message.reply_text("📭 No codespaces registered.")
        return

    msg = "🖥️ Your tracked Codespaces:\n"
    for item in codespaces:
        msg += f"- `{item['codespace']}`\n"
    await update.message.reply_text(msg, parse_mode="Markdown")

async def approve_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    owner_id = OWNER_ID  # using hardcoded owner ID

    if user_id != owner_id:
        await update.message.reply_text("🚫 Only the bot owner can approve users.")
        return

    if len(context.args) != 1:
        await update.message.reply_text("❌ Usage: /approve <telegram_user_id>")
        return

    new_user_id = context.args[0]
    if new_user_id not in APPROVED_USERS:
        APPROVED_USERS.append(new_user_id)
        save_approved_users()
        await update.message.reply_text(f"✅ Approved user {new_user_id}.")
    else:
        await update.message.reply_text("ℹ️ User is already approved.")

# --- BACKGROUND MONITOR ---

def monitor_codespaces(bot_app):
    stopped_states = {"Stopped", "Offline", "Unavailable"}
    skip_states = {"Available", "Starting", "Provisioning", "Stopping"}

    while True:
        print("🔁 Checking Codespaces...")
        for user_id, items in USER_DATA.items():
            for entry in items:
                token = entry["token"]
                name = entry["codespace"]
                chat_id = entry["chat_id"]

                # Check status
                url = f"https://api.github.com/user/codespaces/{name}"
                try:
                    resp = requests.get(url, headers=github_headers(token))
                    if resp.status_code == 200:
                        state = resp.json().get("state")
                        print(f"[{name}] → {state}")
                        if state in stopped_states:
                            print(f"⚡ Starting stopped codespace {name}...")
                            start_url = f"https://api.github.com/user/codespaces/{name}/start"
                            start_resp = requests.post(start_url, headers=github_headers(token))
                            if start_resp.status_code in (200, 202):
                                print(f"✅ Started {name}")
                                try:
                                    bot_app.bot.send_message(chat_id=chat_id,
                                        text=f"✅ Codespace `{name}` was stopped and is now starting.",
                                        parse_mode="Markdown")
                                except Exception as e:
                                    print(f"❌ Telegram error: {e}")
                            else:
                                print(f"❌ Start failed: {start_resp.status_code} {start_resp.text}")
                        elif state in skip_states:
                            print(f"ℹ️ No action needed for {name} ({state}).")
                        else:
                            print(f"⚠️ Unknown state for {name}: {state}. Skipping start.")
                    elif resp.status_code == 404:
                        print(f"❌ Codespace not found: {name}")
                    else:
                        print(f"❌ Failed to fetch {name}: {resp.status_code} {resp.text}")
                except Exception as e:
                    print(f"❌ Error checking {name}: {e}")
        time.sleep(60)

# --- START TELEGRAM BOT ---

# ✅ Hardcoded values (works without environment variables)
BOT_TOKEN = "8606883467:AAGsOdLmMTRlD9oCdO_g_e_277pTGQNxrxg"
OWNER_ID = "5722584981"

app = ApplicationBuilder().token(BOT_TOKEN).build()
app.add_handler(CommandHandler("start", start_command))
app.add_handler(CommandHandler("token", add_token))
app.add_handler(CommandHandler("list", list_codespaces))
app.add_handler(CommandHandler("approve", approve_user))

# Start background monitor
threading.Thread(target=lambda: monitor_codespaces(app), daemon=True).start()

print("🤖 Bot is live with Codespace auto-monitoring!")
app.run_polling()
