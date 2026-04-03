import os
import logging
from datetime import datetime, timedelta
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
from pymongo import MongoClient
from groq import Groq
import requests
from dotenv import load_dotenv

load_dotenv()

TOKEN = os.getenv("TOKEN")
GROQ_KEY = os.getenv("GROQ_KEY")
FOOTBALL_KEY = os.getenv("FOOTBALL_KEY")
MONGO_URI = os.getenv("MONGO_URI")
WEBHOOK_URL = os.getenv("WEBHOOK_URL")

client = MongoClient(MONGO_URI)
db = client["football_bot"]
predictions = db["predictions"]

BASE_URL = "https://v3.football.api-sports.io"
headers = {"x-apisports-key": FOOTBALL_KEY}

groq_client = Groq(api_key=GROQ_KEY)

def search_team(query):
    r = requests.get(f"{BASE_URL}/teams", headers=headers, params={"search": query})
    data = r.json()
    return data["response"][0] if data.get("response") else None

def get_upcoming_fixture(home_id, away_id):
    today = datetime.now().strftime("%Y-%m-%d")
    next_week = (datetime.now() + timedelta(days=10)).strftime("%Y-%m-%d")
    params = {"h2h": f"{home_id}-{away_id}", "from": today, "to": next_week, "status": "NS"}
    r = requests.get(f"{BASE_URL}/fixtures/headtohead", headers=headers, params=params)
    data = r.json()
    return data["response"][0] if data.get("response") else None

def get_full_prediction_data(fixture_id):
    data = {}
    r = requests.get(f"{BASE_URL}/predictions?fixture={fixture_id}", headers=headers)
    if r.status_code == 200 and r.json().get("response"):
        data["prediction"] = r.json()["response"][0]
    r = requests.get(f"{BASE_URL}/fixtures?id={fixture_id}", headers=headers)
    if r.status_code == 200 and r.json().get("response"):
        data["fixture"] = r.json()["response"][0]
    if "fixture" in data:
        h2h = f"{data['fixture']['teams']['home']['id']}-{data['fixture']['teams']['away']['id']}"
        r = requests.get(f"{BASE_URL}/fixtures/headtohead", headers=headers, params={"h2h": h2h, "last": 5})
        data["h2h"] = r.json()["response"] if r.status_code == 200 else []
    return data

def get_prediction(full_data):
    prompt = f"""
Phân tích trận {full_data['fixture']['teams']['home']['name']} vs {full_data['fixture']['teams']['away']['name']}.
Ngày: {full_data['fixture']['fixture']['date']}.
Dự đoán API: {full_data['prediction'].get('advice', 'N/A')}.
Xác suất Home {full_data['prediction'].get('percent', {}).get('home',0)}% Draw {full_data['prediction'].get('percent', {}).get('draw',0)}% Away {full_data['prediction'].get('percent', {}).get('away',0)}%.
Đưa dự đoán thắng hòa thua, tỷ số, xác suất, lý do ngắn gọn bằng tiếng Việt.
"""
    resp = groq_client.chat.completions.create(
        messages=[{"role": "user", "content": prompt}],
        model="llama-3.3-70b-versatile",
        temperature=0.5,
        max_tokens=400
    )
    return resp.choices[0].message.content

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Gửi Dự đoán Đội1 vs Đội2.")

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.lower().strip()
    if not any(x in text for x in ["dự đoán", "predict"]):
        await update.message.reply_text("Gửi Dự đoán Đội1 vs Đội2.")
        return
    await update.message.reply_text("Đang phân tích...")
    try:
        parts = text.replace("dự đoán", "").replace("predict", "").strip().split(" vs ")
        home_query = parts[0].strip()
        away_query = parts[1].strip()
    except:
        await update.message.reply_text("Gửi đúng dạng Dự đoán Đội1 vs Đội2.")
        return
    home = search_team(home_query)
    away = search_team(away_query)
    if not home or not away:
        await update.message.reply_text("Không tìm thấy đội.")
        return
    fixture = get_upcoming_fixture(home["team"]["id"], away["team"]["id"])
    if not fixture:
        await update.message.reply_text("Không có trận sắp tới.")
        return
    fixture_id = fixture["fixture"]["id"]
    full_data = get_full_prediction_data(fixture_id)
    result = get_prediction(full_data)
    reply = f"{full_data['fixture']['teams']['home']['name']} vs {full_data['fixture']['teams']['away']['name']}\n{full_data['fixture']['fixture']['date'][:16]}\n{result}"
    await update.message.reply_text(reply)
    predictions.insert_one({
        "user_id": update.effective_user.id,
        "fixture_id": fixture_id,
        "match": f"{home['team']['name']} vs {away['team']['name']}",
        "prediction": result,
        "time": datetime.utcnow()
    })

def main():
    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & \~filters.COMMAND, handle_message))
    if WEBHOOK_URL:
        app.run_webhook(
            listen="0.0.0.0",
            port=int(os.getenv("PORT", 8443)),
            url_path=TOKEN,
            webhook_url=f"{WEBHOOK_URL}/{TOKEN}"
        )
    else:
        app.run_polling()

if __name__ == "__main__":
    main()
