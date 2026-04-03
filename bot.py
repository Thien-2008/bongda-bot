import os
import requests
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, ContextTypes

BOT_TOKEN = os.getenv("BOT_TOKEN")
CHANNEL_ID = os.getenv("CHANNEL_ID")
FOOTBALL_KEY = os.getenv("FOOTBALL_KEY")
WEBHOOK_URL = os.getenv("WEBHOOK_URL")

# ===== START =====
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("⚽ Bot LIVE đang chạy!")

# ===== GET MATCH =====
def get_live():
    url = "https://v3.football.api-sports.io/fixtures?live=all"
    headers = {"x-apisports-key": FOOTBALL_KEY}
    res = requests.get(url, headers=headers).json()

    if not res.get("response"):
        return None

    m = res["response"][0]

    home = m["teams"]["home"]["name"]
    away = m["teams"]["away"]["name"]
    gh = m["goals"]["home"]
    ga = m["goals"]["away"]
    minute = m["fixture"]["status"]["elapsed"]

    return f"⚽ {home} {gh}-{ga} {away}\n⏱ {minute}'"

# ===== SEND =====
async def send_live(context: ContextTypes.DEFAULT_TYPE):
    text = get_live()
    if not text:
        return

    keyboard = [[InlineKeyboardButton("🔥 Xem ngay", url="https://yourlink.com")]]

    await context.bot.send_message(
        chat_id=CHANNEL_ID,
        text=text,
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

# ===== MAIN =====
def main():
    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))

    # JOB chạy mỗi 60s
    app.job_queue.run_repeating(send_live, interval=60, first=10)

    app.run_webhook(
        listen="0.0.0.0",
        port=int(os.environ.get("PORT", 10000)),
        webhook_url=WEBHOOK_URL
    )

if __name__ == "__main__":
    main()
