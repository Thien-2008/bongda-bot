# ================== BÓNG ĐÁ 24H BOT PRO v6 ==================
import os
import asyncio
import logging
import threading
import time
import requests
import feedparser
from datetime import datetime
from flask import Flask, request as flask_request
from groq import Groq
from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler, ContextTypes
)
from motor.motor_asyncio import AsyncIOMotorClient

logging.basicConfig(level=logging.INFO)

# ===== ENV =====
TOKEN        = os.environ.get("TOKEN")
CHANNEL_ID   = os.environ.get("CHANNEL_ID")
FOOTBALL_KEY = os.environ.get("FOOTBALL_KEY")
GROQ_KEY     = os.environ.get("GROQ_KEY")
MONGO_URI    = os.environ.get("MONGO_URI")
PORT         = int(os.environ.get("PORT", 8080))
WEBHOOK_URL  = "https://bongda-bot.onrender.com"

# ===== SETUP =====
groq_client  = Groq(api_key=GROQ_KEY)
mongo_client = AsyncIOMotorClient(MONGO_URI)
db           = mongo_client["bongda"]
news_col     = db["news"]

FOOTBALL_HEADERS = {"x-apisports-key": FOOTBALL_KEY}
BASE_URL = "https://v3.football.api-sports.io"

app = Flask(__name__)
application = None
bot_loop = None

# ===== WEBHOOK =====
@app.route("/")
def home():
    return "BOT RUNNING"

@app.route(f"/{TOKEN}", methods=["POST"])
def webhook():
    if application:
        data = flask_request.get_json(force=True)
        update = Update.de_json(data, application.bot)
        asyncio.run_coroutine_threadsafe(
            application.process_update(update), bot_loop
        )
    return "OK"

# ===== API =====
def get_live():
    r = requests.get(f"{BASE_URL}/fixtures",
        headers=FOOTBALL_HEADERS,
        params={"live":"all"})
    return r.json()["response"]

def get_today():
    today = datetime.now().strftime("%Y-%m-%d")
    r = requests.get(f"{BASE_URL}/fixtures",
        headers=FOOTBALL_HEADERS,
        params={"date":today})
    return r.json()["response"]

def get_events(fid):
    r = requests.get(f"{BASE_URL}/fixtures/events",
        headers=FOOTBALL_HEADERS,
        params={"fixture":fid})
    return r.json()["response"]

# ===== FORMAT PRO =====
def format_match(m):
    home = m["teams"]["home"]["name"]
    away = m["teams"]["away"]["name"]
    gh = m["goals"]["home"] or 0
    ga = m["goals"]["away"] or 0
    status = m["fixture"]["status"]["short"]

    text = f"🔥 {home} {gh} - {ga} {away}\n"

    # goals
    if status in ["1H","2H","HT","FT"]:
        events = get_events(m["fixture"]["id"])
        for e in events:
            if e["type"] == "Goal":
                minute = e["time"]["elapsed"]
                player = e["player"]["name"]
                assist = e["assist"]["name"] if e["assist"] else None

                if assist:
                    text += f"⚽ {minute}' {player} (KT: {assist})\n"
                else:
                    text += f"⚽ {minute}' {player}\n"

    if status == "NS":
        t = m["fixture"]["date"][11:16]
        text += f"⏰ {t}\n"
    elif status in ["1H","2H"]:
        text += "🔴 Đang diễn ra\n"
    elif status == "FT":
        text += "✅ Kết thúc\n"

    return text

# ===== AI =====
def ai(prompt):
    try:
        r = groq_client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[{"role":"user","content":prompt}],
            max_tokens=300
        )
        return r.choices[0].message.content
    except:
        return None

# ===== AUTO LIVE =====
last_scores = {}

async def auto_live(bot):
    matches = await asyncio.to_thread(get_live)

    for m in matches:
        fid = str(m["fixture"]["id"])
        score = f"{m['goals']['home']}-{m['goals']['away']}"

        if fid not in last_scores or last_scores[fid] != score:
            last_scores[fid] = score

            text = format_match(m)

            await bot.send_message(
                chat_id=CHANNEL_ID,
                text=f"🔴 LIVE UPDATE\n\n{text}\n#Live"
            )

# ===== AUTO NEWS =====
RSS = [
    "https://vnexpress.net/rss/the-thao.rss",
]

async def auto_news(bot):
    for url in RSS:
        feed = feedparser.parse(url)

        for e in feed.entries[:2]:
            if await news_col.find_one({"link":e.link}):
                continue

            prompt = f"Viết lại tin bóng đá ngắn, hấp dẫn:\n{e.title}\n{e.summary}"
            content = await asyncio.to_thread(ai, prompt)

            await bot.send_message(
                chat_id=CHANNEL_ID,
                text=content or e.title
            )

            await news_col.insert_one({"link":e.link})

# ===== COMMAND =====
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("⚽ Bot bóng đá PRO đang chạy!")

# ===== SCHEDULER =====
async def scheduler(context: ContextTypes.DEFAULT_TYPE):
    await auto_live(context.bot)
    await auto_news(context.bot)

# ===== RUN =====
async def run_bot():
    global application, bot_loop
    bot_loop = asyncio.get_event_loop()

    application = Application.builder().token(TOKEN).build()

    application.add_handler(CommandHandler("start", start))
    application.job_queue.run_repeating(scheduler, interval=60)

    await application.initialize()
    await application.bot.set_webhook(f"{WEBHOOK_URL}/{TOKEN}")
    await application.start()

    while True:
        await asyncio.sleep(3600)

def start_bot():
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(run_bot())

def main():
    threading.Thread(target=start_bot).start()
    time.sleep(3)
    app.run(host="0.0.0.0", port=PORT)

if __name__ == "__main__":
    main()
