import os
import asyncio
import requests
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes

# ===== CONFIG =====
TOKEN = os.getenv("BOT_TOKEN")
CHANNEL_ID = os.getenv("CHANNEL_ID")
FOOTBALL_KEY = os.getenv("FOOTBALL_KEY")

live_matches = {}

# ===== LẤY TRẬN LIVE =====
def get_live_matches():
    url = "https://v3.football.api-sports.io/fixtures"
    headers = {"x-apisports-key": FOOTBALL_KEY}
    params = {"live": "all"}

    r = requests.get(url, headers=headers, params=params)
    return r.json().get("response", [])

# ===== GỬI TRẬN =====
async def send_real_match(context: ContextTypes.DEFAULT_TYPE):
    matches = await asyncio.to_thread(get_live_matches)

    if not matches:
        await context.bot.send_message(chat_id=CHANNEL_ID, text="Không có trận live")
        return

    m = matches[0]

    fixture_id = m["fixture"]["id"]
    home = m["teams"]["home"]["name"]
    away = m["teams"]["away"]["name"]

    score_home = m["goals"]["home"] or 0
    score_away = m["goals"]["away"] or 0
    minute = m["fixture"]["status"]["elapsed"] or 0

    text = f"""
LIVE • {minute}'

{home}  {score_home} — {score_away}  {away}

Đang cập nhật...
"""

    msg = await context.bot.send_message(chat_id=CHANNEL_ID, text=text.strip())

    live_matches[fixture_id] = {
        "message_id": msg.message_id,
        "last_goals": (score_home, score_away)
    }

    asyncio.create_task(track_match(context, fixture_id))

# ===== THEO DÕI TRẬN =====
async def track_match(context, fixture_id):
    while True:
        await asyncio.sleep(20)

        url = "https://v3.football.api-sports.io/fixtures"
        headers = {"x-apisports-key": FOOTBALL_KEY}
        params = {"id": fixture_id}

        r = requests.get(url, headers=headers, params=params)
        data = r.json()["response"][0]

        home = data["teams"]["home"]["name"]
        away = data["teams"]["away"]["name"]

        score_home = data["goals"]["home"] or 0
        score_away = data["goals"]["away"] or 0

        old_home, old_away = live_matches[fixture_id]["last_goals"]

        if score_home != old_home or score_away != old_away:
            await update_real_match(context, fixture_id, data)
            live_matches[fixture_id]["last_goals"] = (score_home, score_away)

# ===== UPDATE =====
async def update_real_match(context, fixture_id, data):
    home = data["teams"]["home"]["name"]
    away = data["teams"]["away"]["name"]

    score_home = data["goals"]["home"] or 0
    score_away = data["goals"]["away"] or 0
    minute = data["fixture"]["status"]["elapsed"] or 0

    events = data["events"]

    goals = []
    for e in events:
        if e["type"] == "Goal":
            player = e["player"]["name"]
            minute_goal = e["time"]["elapsed"]
            goals.append(f"{minute_goal}' {player}")

    goals_text = "\n".join(goals) if goals else "Chưa có bàn"

    text = f"""
LIVE • {minute}'

{home}  {score_home} — {score_away}  {away}

BÀN THẮNG
{goals_text}
"""

    await context.bot.edit_message_text(
        chat_id=CHANNEL_ID,
        message_id=live_matches[fixture_id]["message_id"],
        text=text.strip()
    )

# ===== COMMAND =====
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Bot đang chạy!")

# ===== MAIN =====
async def main():
    app = ApplicationBuilder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))

    # auto chạy sau 10s
    app.job_queue.run_once(send_real_match, 10)

    print("Bot đang chạy...")
    await app.run_polling()

if __name__ == "__main__":
    asyncio.run(main())
