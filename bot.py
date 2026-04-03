# ⚽ BOT BÓNG ĐÁ 24H PRO v6 (AUTO + XỊN)
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
from telegram.ext import Application, CommandHandler, ContextTypes
from motor.motor_asyncio import AsyncIOMotorClient

logging.basicConfig(level=logging.INFO)

TOKEN        = os.environ.get("BOT_TOKEN")
CHANNEL_ID   = os.environ.get("CHANNEL_ID")
FOOTBALL_KEY = os.environ.get("FOOTBALL_KEY")
GROQ_KEY     = os.environ.get("GROQ_KEY")
MONGO_URI    = os.environ.get("MONGO_URI")
PORT         = int(os.environ.get("PORT", 8080))

WEBHOOK_URL  = "https://bongda-bot.onrender.com"

groq_client  = Groq(api_key=GROQ_KEY)
mongo_client = AsyncIOMotorClient(MONGO_URI)
db           = mongo_client["bongda"]
posted_col   = db["posted"]

app = Flask(__name__)
application = None
loop = None

# ================= API =================
HEADERS = {"x-apisports-key": FOOTBALL_KEY}
BASE    = "https://v3.football.api-sports.io"

def get_live():
    r = requests.get(f"{BASE}/fixtures",
        headers=HEADERS,
        params={"live": "all"}, timeout=10)
    return r.json().get("response", [])

def get_events(fixture_id):
    r = requests.get(f"{BASE}/fixtures/events",
        headers=HEADERS,
        params={"fixture": fixture_id}, timeout=10)
    return r.json().get("response", [])

# ================= FORMAT =================
def format_live(match):
    home = match["teams"]["home"]["name"]
    away = match["teams"]["away"]["name"]
    gh   = match["goals"]["home"] or 0
    ga   = match["goals"]["away"] or 0
    time = match["fixture"]["status"]["elapsed"]

    text = f"""🔴 LIVE UPDATE

🔥 {home} {gh} - {ga} {away}
⏱ {time}'

"""

    # lấy sự kiện ghi bàn
    events = get_events(match["fixture"]["id"])
    goals = []

    for e in events:
        if e["type"] == "Goal":
            minute = e["time"]["elapsed"]
            player = e["player"]["name"]
            assist = e["assist"]["name"] if e["assist"] else None
            detail = e["detail"]

            g = f"⚽ {minute}' {player}"
            if assist:
                g += f" (🅰 {assist})"
            if "Penalty" in detail:
                g += " (P)"
            goals.append(g)

    if goals:
        text += "\n".join(goals)

    text += "\n\n#Live"

    return text

# ================= AI =================
def ai_rewrite(title, summary):
    try:
        prompt = f"""
Viết lại tin bóng đá cực cuốn, ngắn gọn, tiếng Việt, có emoji.
Ưu tiên người Việt thích đọc.

Tiêu đề: {title}
Nội dung: {summary}

Không dài quá 120 từ.
"""
        r = groq_client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[{"role":"user","content":prompt}]
        )
        return r.choices[0].message.content
    except:
        return f"⚽ {title}\n\n{summary[:200]}"

# ================= AUTO =================
async def auto_live(bot):
    matches = await asyncio.to_thread(get_live)

    for m in matches[:5]:  # limit free API
        fid = m["fixture"]["id"]

        if await posted_col.find_one({"id": fid}):
            continue

        text = await asyncio.to_thread(format_live, m)

        await bot.send_message(chat_id=CHANNEL_ID, text=text)

        await posted_col.insert_one({"id": fid})

async def auto_news(bot):
    feeds = [
        "https://vnexpress.net/rss/the-thao/bong-da.rss",
    ]

    for url in feeds:
        feed = feedparser.parse(url)

        for e in feed.entries[:2]:
            link = e.link

            if await posted_col.find_one({"link": link}):
                continue

            content = await asyncio.to_thread(
                ai_rewrite, e.title, e.summary
            )

            kb = InlineKeyboardMarkup([[
                InlineKeyboardButton("📖 Xem chi tiết", url=link)
            ]])

            await bot.send_message(
                chat_id=CHANNEL_ID,
                text=content,
                reply_markup=kb
            )

            await posted_col.insert_one({"link": link})

# ================= SCHEDULER =================
async def scheduler(context: ContextTypes.DEFAULT_TYPE):
    await auto_live(context.bot)
    await auto_news(context.bot)
    print("✅ AUTO chạy")

# ================= WEBHOOK =================
@app.route("/")
def home():
    return "Bot đang chạy!"

@app.route(f"/{TOKEN}", methods=["POST"])
def hook():
    data = flask_request.get_json(force=True)
    update = Update.de_json(data, application.bot)
    asyncio.run_coroutine_threadsafe(
        application.process_update(update), loop
    )
    return "ok"

# ================= RUN =================
async def run():
    global application, loop
    loop = asyncio.get_event_loop()

    application = Application.builder().token(TOKEN).build()

    application.job_queue.run_repeating(
        scheduler, interval=60, first=10
    )

    await application.initialize()
    await application.bot.set_webhook(
        f"{WEBHOOK_URL}/{TOKEN}"
    )
    await application.start()

    while True:
        await asyncio.sleep(999)

def start():
    asyncio.run(run())

if __name__ == "__main__":
    threading.Thread(target=start).start()
    time.sleep(3)
    app.run(host="0.0.0.0", port=PORT)
