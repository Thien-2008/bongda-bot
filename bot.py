import os
import asyncio
import random
import logging
import threading
import time
import requests
from datetime import datetime
from flask import Flask, request as flask_request
from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler, ContextTypes
)
from motor.motor_asyncio import AsyncIOMotorClient

# ===== CONFIG =====
TOKEN = os.environ.get("TOKEN")
CHANNEL_ID = os.environ.get("CHANNEL_ID")
MONGO_URI = os.environ.get("MONGO_URI")
FOOTBALL_KEY = os.environ.get("FOOTBALL_KEY")
PORT = int(os.environ.get("PORT", 8080))
WEBHOOK_URL = os.environ.get("WEBHOOK_URL")

# QC
QC_TEXT = "\n\n👉 Xem bóng đá miễn phí: https://yourlink.com"

# ===== DB =====
mongo = AsyncIOMotorClient(MONGO_URI)
db = mongo["bot"]
votes_col = db["votes"]

# ===== FLASK =====
app = Flask(__name__)
application = None
bot_loop = None

@app.route("/")
def home():
    return "OK"

@app.route(f"/{TOKEN}", methods=["POST"])
def webhook():
    data = flask_request.get_json(force=True)
    update = Update.de_json(data, application.bot)
    asyncio.run_coroutine_threadsafe(
        application.process_update(update), bot_loop
    )
    return "ok"

# ===== API =====
def get_matches():
    try:
        r = requests.get(
            "https://v3.football.api-sports.io/fixtures",
            headers={"x-apisports-key": FOOTBALL_KEY},
            params={"live": "all"},
            timeout=10
        )
        return r.json().get("response", [])
    except:
        return []

# ===== CONTENT =====
def create_prediction(home, away, match_id):
    home_p = random.randint(45, 65)
    draw_p = random.randint(15, 25)
    away_p = 100 - home_p - draw_p

    text = f"""
{home} vs {away}

Mano trận này đang nghiêng về {home if home_p > away_p else away}

📊 XÁC SUẤT
• {home}: {home_p}%
• Hòa: {draw_p}%
• {away}: {away_p}%

📉 PHONG ĐỘ
• {home}: {random.randint(2,5)} thắng / 5
• {away}: {random.randint(1,4)} thắng / 5

👉 Bạn chọn đội nào?
"""

    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton(home, callback_data=f"vote_{match_id}_1"),
        InlineKeyboardButton("Hòa", callback_data=f"vote_{match_id}_draw"),
        InlineKeyboardButton(away, callback_data=f"vote_{match_id}_2"),
    ]])

    return text, kb

# ===== VOTE =====
async def handle_vote(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    parts = query.data.split("_")
    match = parts[1]
    choice = parts[2]
    user_id = query.from_user.id

    # check vote
    if await votes_col.find_one({"match": match, "user": user_id}):
        await query.answer("Bạn vote rồi!", show_alert=True)
        return

    await votes_col.insert_one({
        "match": match,
        "user": user_id,
        "choice": choice
    })

    v1 = await votes_col.count_documents({"match": match, "choice": "1"})
    vd = await votes_col.count_documents({"match": match, "choice": "draw"})
    v2 = await votes_col.count_documents({"match": match, "choice": "2"})

    total = v1 + vd + v2

    await query.answer(f"Đã vote! Tổng: {total}")

# ===== COMMAND =====
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Bot phân tích bóng đá + vote 🔥")

# ===== AUTO POST =====
async def auto_post(context: ContextTypes.DEFAULT_TYPE):
    matches = await asyncio.to_thread(get_matches)

    if not matches:
        return

    for m in matches[:5]:
        home = m["teams"]["home"]["name"]
        away = m["teams"]["away"]["name"]

        match_id = f"{home}_{away}".replace(" ", "")

        text, kb = create_prediction(home, away, match_id)

        await context.bot.send_message(
            chat_id=CHANNEL_ID,
            text=text + QC_TEXT,
            reply_markup=kb
        )

        await asyncio.sleep(5)

# ===== MAIN =====
async def run_bot():
    global application, bot_loop
    bot_loop = asyncio.get_event_loop()

    application = Application.builder().token(TOKEN).build()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CallbackQueryHandler(handle_vote, pattern="^vote_"))

    application.job_queue.run_repeating(auto_post, interval=3600, first=10)

    await application.initialize()
    await application.bot.set_webhook(f"{WEBHOOK_URL}/{TOKEN}")
    await application.start()

    while True:
        await asyncio.sleep(3600)

def start_bot():
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(run_bot())

if __name__ == "__main__":
    threading.Thread(target=start_bot).start()
    time.sleep(2)
    app.run(host="0.0.0.0", port=PORT)
