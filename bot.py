import os
import requests
from flask import Flask, request
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    ContextTypes,
)

# ====== ENV ======
BOT_TOKEN = os.getenv("BOT_TOKEN")
CHANNEL_ID = os.getenv("CHANNEL_ID")
FOOTBALL_KEY = os.getenv("FOOTBALL_KEY")
WEBHOOK_URL = os.getenv("WEBHOOK_URL")

# ====== APP ======
flask_app = Flask(__name__)
app = ApplicationBuilder().token(BOT_TOKEN).build()

# ====== COMMAND ======
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("⚽ Bot bóng đá LIVE đang chạy!")

# ====== GET MATCH ======
def get_live_match():
    url = "https://v3.football.api-sports.io/fixtures?live=all"
    headers = {"x-apisports-key": FOOTBALL_KEY}
    res = requests.get(url, headers=headers).json()

    try:
        match = res["response"][0]
        home = match["teams"]["home"]["name"]
        away = match["teams"]["away"]["name"]
        score_home = match["goals"]["home"]
        score_away = match["goals"]["away"]
        minute = match["fixture"]["status"]["elapsed"]

        return home, away, score_home, score_away, minute
    except:
        return None

# ====== SEND LIVE ======
async def send_live(context: ContextTypes.DEFAULT_TYPE):
    match = get_live_match()
    if not match:
        return

    home, away, sh, sa, minute = match

    text = f"""🔥 LIVE MATCH

{home} {sh} - {sa} {away}
⏱ {minute}'

👉 Ai thắng?"""

    keyboard = [
        [
            InlineKeyboardButton(home, callback_data="home"),
            InlineKeyboardButton("Hòa", callback_data="draw"),
            InlineKeyboardButton(away, callback_data="away"),
        ]
    ]

    await context.bot.send_message(
        chat_id=CHANNEL_ID,
        text=text,
        reply_markup=InlineKeyboardMarkup(keyboard),
    )

# ====== SET JOB ======
async def on_start(app):
    app.job_queue.run_repeating(send_live, interval=60, first=10)

app.post_init = on_start

# ====== HANDLER ======
app.add_handler(CommandHandler("start", start))

# ====== WEBHOOK ======
@flask_app.route("/", methods=["GET"])
def home():
    return "Bot đang chạy!"

@flask_app.route("/webhook", methods=["POST"])
async def webhook():
    data = request.get_json(force=True)
    update = Update.de_json(data, app.bot)
    await app.process_update(update)
    return "ok"

# ====== MAIN ======
if __name__ == "__main__":
    import asyncio

    async def main():
        await app.initialize()
        await app.bot.set_webhook(WEBHOOK_URL + "/webhook")

    asyncio.run(main())

    flask_app.run(host="0.0.0.0", port=10000)
