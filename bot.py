import os
import requests
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, ContextTypes

BOT_TOKEN = os.getenv("BOT_TOKEN")
CHANNEL_ID = os.getenv("CHANNEL_ID")
FOOTBALL_KEY = os.getenv("FOOTBALL_KEY")
WEBHOOK_URL = os.getenv("WEBHOOK_URL")

# ===== START COMMAND =====
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("⚽ Bot LIVE đang chạy!\nChờ trận để cập nhật...")

# ===== GET LIVE MATCH =====
def get_live_match():
    url = "https://v3.football.api-sports.io/fixtures?live=all"
    headers = {"x-apisports-key": FOOTBALL_KEY}
    res = requests.get(url, headers=headers).json()

    if "response" not in res or len(res["response"]) == 0:
        return None

    match = res["response"][0]

    home = match["teams"]["home"]["name"]
    away = match["teams"]["away"]["name"]
    goals_home = match["goals"]["home"]
    goals_away = match["goals"]["away"]
    minute = match["fixture"]["status"]["elapsed"]

    return f"⚽ {home} {goals_home}-{goals_away} {away}\n⏱ {minute}'"

# ===== SEND MATCH =====
async def send_live(context: ContextTypes.DEFAULT_TYPE):
    text = get_live_match()

    if not text:
        return

    keyboard = [
        [
            InlineKeyboardButton("🔥 Xem ngay", url="https://yourlink.com")
        ]
    ]

    reply_markup = InlineKeyboardMarkup(keyboard)

    await context.bot.send_message(
        chat_id=CHANNEL_ID,
        text=text,
        reply_markup=reply_markup
    )

# ===== MAIN =====
def main():
    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))

    # chạy mỗi 60s
    app.job_queue.run_repeating(send_live, interval=60, first=10)

    # webhook (KHÔNG polling)
    app.run_webhook(
        listen="0.0.0.0",
        port=int(os.environ.get("PORT", 10000)),
        webhook_url=WEBHOOK_URL
    )

if __name__ == "__main__":
    main()
