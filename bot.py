# Bot Phân Tích Bóng Đá 24H v1.0
# Tự động dự đoán tỷ số dựa trên data thật
import os
import asyncio
import logging
import threading
import time
import requests
import feedparser
from datetime import datetime, timedelta
from flask import Flask, request as flask_request
from groq import Groq
from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler, ContextTypes
)
from motor.motor_asyncio import AsyncIOMotorClient

logging.basicConfig(
    format="%(asctime)s - %(levelname)s - %(message)s",
    level=logging.INFO
)

TOKEN        = os.environ.get("TOKEN", "")
ADMIN_ID     = int(os.environ.get("ADMIN_ID", "0"))
CHANNEL_ID   = os.environ.get("CHANNEL_ID", "")
GROQ_KEY     = os.environ.get("GROQ_KEY", "")
FOOTBALL_KEY = os.environ.get("FOOTBALL_KEY", "")
MONGO_URI    = os.environ.get("MONGO_URI", "")
PORT         = int(os.environ.get("PORT", 8080))
WEBHOOK_URL  = "https://bongda-bot.onrender.com"

groq_client  = Groq(api_key=GROQ_KEY)
mongo_client = AsyncIOMotorClient(MONGO_URI)
db              = mongo_client["phantichbongda"]
predictions_col = db["predictions"]
results_col     = db["results"]
news_col        = db["news"]

flask_app   = Flask(__name__)
application = None
bot_loop    = None

@flask_app.route("/")
def index():
    return "Bot Phân Tích Bóng Đá 24H đang chạy!", 200

@flask_app.route(f"/{TOKEN}", methods=["POST"])
def webhook():
    if application and bot_loop:
        data   = flask_request.get_json(force=True)
        update = Update.de_json(data, application.bot)
        asyncio.run_coroutine_threadsafe(
            application.process_update(update), bot_loop
        )
    return "OK", 200

FOOTBALL_HEADERS = {"x-apisports-key": FOOTBALL_KEY}
FOOTBALL_BASE    = "https://v3.football.api-sports.io"

LEAGUES = {
    "Premier League": 39,
    "La Liga": 140,
    "Champions League": 2,
    "Serie A": 135,
    "Bundesliga": 78,
    "V-League": 340,
}

def api_get(endpoint, params={}):
    try:
        r = requests.get(
            f"{FOOTBALL_BASE}/{endpoint}",
            headers=FOOTBALL_HEADERS,
            params=params, timeout=15
        )
        return r.json().get("response", [])
    except Exception as e:
        logging.error(f"API lỗi {endpoint}: {e}")
        return []

def get_upcoming_fixtures(days=1):
    today    = datetime.now().strftime("%Y-%m-%d")
    tomorrow = (datetime.now() + timedelta(days=days)).strftime("%Y-%m-%d")
    results  = []
    for name, league_id in LEAGUES.items():
        fixtures = api_get("fixtures", {
            "league": league_id, "from": today, "to": tomorrow,
            "timezone": "Asia/Ho_Chi_Minh", "status": "NS"
        })
        results.extend(fixtures)
    return results

def get_team_recent_form(team_id, last=10):
    fixtures = api_get("fixtures", {
        "team": team_id, "last": last,
        "timezone": "Asia/Ho_Chi_Minh"
    })
    if not fixtures:
        return None
    wins = draws = losses = goals_for = goals_against = 0
    form_str = ""
    for f in fixtures:
        home_id    = f["teams"]["home"]["id"]
        home_goals = f["goals"]["home"] or 0
        away_goals = f["goals"]["away"] or 0
        is_home    = home_id == team_id
        gf, ga     = (home_goals, away_goals) if is_home else (away_goals, home_goals)
        goals_for += gf
        goals_against += ga
        if gf > ga:
            wins += 1; form_str += "W"
        elif gf == ga:
            draws += 1; form_str += "D"
        else:
            losses += 1; form_str += "L"
    total = len(fixtures)
    return {
        "played": total, "wins": wins, "draws": draws, "losses": losses,
        "goals_for": goals_for, "goals_against": goals_against,
        "avg_goals_for": round(goals_for/total, 1) if total > 0 else 0,
        "avg_goals_against": round(goals_against/total, 1) if total > 0 else 0,
        "form": form_str[::-1],
        "win_rate": round(wins/total*100) if total > 0 else 0
    }

def get_head_to_head(team1_id, team2_id, last=10):
    fixtures = api_get("fixtures/headtohead", {
        "h2h": f"{team1_id}-{team2_id}", "last": last
    })
    if not fixtures:
        return None
    team1_wins = team2_wins = draws = 0
    for f in fixtures:
        home_id    = f["teams"]["home"]["id"]
        home_goals = f["goals"]["home"] or 0
        away_goals = f["goals"]["away"] or 0
        if home_goals > away_goals:
            winner = home_id
        elif away_goals > home_goals:
            winner = f["teams"]["away"]["id"]
        else:
            winner = None
        if winner == team1_id: team1_wins += 1
        elif winner == team2_id: team2_wins += 1
        else: draws += 1
    return {"total": len(fixtures), "team1_wins": team1_wins,
            "team2_wins": team2_wins, "draws": draws}

def get_team_standings(team_id, league_id, season=2024):
    standings = api_get("standings", {"league": league_id, "season": season})
    if not standings:
        return None
    for group in standings:
        for team in group["league"]["standings"][0]:
            if team["team"]["id"] == team_id:
                return {"rank": team["rank"], "points": team["points"],
                        "played": team["all"]["played"], "goals_diff": team["goalsDiff"]}
    return None

def ai_predict(prompt):
    try:
        r = groq_client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[{"role": "user", "content": prompt}],
            max_tokens=600, temperature=0.3
        )
        return r.choices[0].message.content
    except Exception as e:
        logging.error(f"AI lỗi: {e}")
        return None

def build_prediction_prompt(fixture, home_form, away_form, h2h, home_standing, away_standing):
    home   = fixture["teams"]["home"]["name"]
    away   = fixture["teams"]["away"]["name"]
    league = fixture["league"]["name"]
    t      = fixture["fixture"]["date"][11:16]
    return f"""Bạn là chuyên gia phân tích bóng đá. Phân tích trận:
🏆 {league} | ⚽ {home} vs {away} | ⏰ {t} GMT+7

{home}: Form {home_form['form'] if home_form else 'N/A'} | Thắng {home_form['win_rate'] if home_form else 0}% | TB {home_form['avg_goals_for'] if home_form else 0} bàn/trận | Thủng {home_form['avg_goals_against'] if home_form else 0}/trận {f"| Hạng {home_standing['rank']} ({home_standing['points']}đ)" if home_standing else ''}

{away}: Form {away_form['form'] if away_form else 'N/A'} | Thắng {away_form['win_rate'] if away_form else 0}% | TB {away_form['avg_goals_for'] if away_form else 0} bàn/trận | Thủng {away_form['avg_goals_against'] if away_form else 0}/trận {f"| Hạng {away_standing['rank']} ({away_standing['points']}đ)" if away_standing else ''}

H2H {h2h['total'] if h2h else 0} trận: {home} thắng {h2h['team1_wins'] if h2h else 0} | Hòa {h2h['draws'] if h2h else 0} | {away} thắng {h2h['team2_wins'] if h2h else 0}

Đưa ra:
1. Nhận định (3-4 câu)
2. Dự đoán tỷ số
3. Xác suất {home} thắng / Hòa / {away} thắng (%)
4. Kèo khuyên

Tiếng Việt, emoji, chuyên nghiệp, ngắn gọn."""

async def create_prediction_post(fixture):
    home_id    = fixture["teams"]["home"]["id"]
    away_id    = fixture["teams"]["away"]["id"]
    league_id  = fixture["league"]["id"]
    fixture_id = fixture["fixture"]["id"]
    home_form     = await asyncio.to_thread(get_team_recent_form, home_id)
    away_form     = await asyncio.to_thread(get_team_recent_form, away_id)
    h2h           = await asyncio.to_thread(get_head_to_head, home_id, away_id)
    home_standing = await asyncio.to_thread(get_team_standings, home_id, league_id)
    away_standing = await asyncio.to_thread(get_team_standings, away_id, league_id)
    prompt     = build_prediction_prompt(fixture, home_form, away_form, h2h, home_standing, away_standing)
    ai_result  = await asyncio.to_thread(ai_predict, prompt)
    if not ai_result:
        return None
    home   = fixture["teams"]["home"]["name"]
    away   = fixture["teams"]["away"]["name"]
    league = fixture["league"]["name"]
    t      = fixture["fixture"]["date"][11:16]
    post = (
        f"🔮 PHÂN TÍCH & DỰ ĐOÁN\n{'─'*30}\n"
        f"🏆 {league}\n⚽ {home} vs {away}\n⏰ {t} hôm nay\n{'─'*30}\n\n"
        f"{ai_result}\n\n{'─'*30}\n"
        f"#PhanTich #{home.replace(' ','')} #{away.replace(' ','')}"
    )
    return post, fixture_id

async def auto_predict_scheduler(context: ContextTypes.DEFAULT_TYPE):
    try:
        now      = datetime.now()
        fixtures = await asyncio.to_thread(get_upcoming_fixtures, 1)
        if not fixtures:
            return
        posted = 0
        for fixture in fixtures:
            fixture_id = fixture["fixture"]["id"]
            exists = await predictions_col.find_one({"fixture_id": fixture_id})
            if exists:
                continue
            match_time       = datetime.fromisoformat(fixture["fixture"]["date"].replace("Z","+00:00"))
            match_time_local = match_time + timedelta(hours=7)
            hours_until      = (match_time_local - now).total_seconds() / 3600
            if 0 < hours_until <= 3:
                result = await create_prediction_post(fixture)
                if result:
                    post, fid = result
                    home = fixture["teams"]["home"]["name"]
                    away = fixture["teams"]["away"]["name"]
                    kb   = InlineKeyboardMarkup([[
                        InlineKeyboardButton(f"🏆 {home}", callback_data=f"pred_{fid}_home"),
                        InlineKeyboardButton("🤝 Hòa", callback_data=f"pred_{fid}_draw"),
                        InlineKeyboardButton(f"🏆 {away}", callback_data=f"pred_{fid}_away"),
                    ]])
                    await context.bot.send_message(
                        chat_id=CHANNEL_ID, text=post, reply_markup=kb
                    )
                    await predictions_col.insert_one({
                        "fixture_id": fid, "home": home, "away": away,
                        "league": fixture["league"]["name"],
                        "match_time": fixture["fixture"]["date"],
                        "posted_at": datetime.now()
                    })
                    posted += 1
                    await asyncio.sleep(5)
        logging.info(f"✅ Đã đăng {posted} dự đoán!")
    except Exception as e:
        logging.error(f"Scheduler lỗi: {e}")

async def handle_prediction_vote(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query      = update.callback_query
    parts      = query.data.split("_")
    if len(parts) < 3:
        return
    fixture_id = parts[1]
    choice     = parts[2]
    user_id    = query.from_user.id
    exists = await predictions_col.find_one({
        "fixture_id": int(fixture_id),
        f"votes.{user_id}": {"$exists": True}
    })
    if exists:
        await query.answer("Bạn đã chọn rồi! 😅", show_alert=True)
        return
    await predictions_col.update_one(
        {"fixture_id": int(fixture_id)},
        {"$set": {f"votes.{user_id}": choice}}
    )
    doc   = await predictions_col.find_one({"fixture_id": int(fixture_id)})
    count = len(doc.get("votes", {})) if doc else 1
    await query.answer(f"✅ Đã chọn! Tổng {count} người vote", show_alert=True)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📊 Chào mừng đến Phân Tích Bóng Đá 24H!\n\n"
        "🔮 Tự động dự đoán trước mỗi trận 3 tiếng\n"
        "📡 Data thật từ API Football\n"
        "🤖 AI phân tích phong độ + lịch sử đối đầu\n\n"
        "/lichthidau - Lịch hôm nay\n"
        "/ketqua - Kết quả live\n"
        "/bxh_anh - BXH Premier League\n"
        "/bxh_tbn - BXH La Liga\n"
        "/dudoan Arsenal Chelsea - Dự đoán ngay"
    )

async def lich_thi_dau(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("⏳ Đang lấy lịch...")
    fixtures = await asyncio.to_thread(get_upcoming_fixtures, 1)
    if not fixtures:
        await update.message.reply_text("📅 Không có trận hôm nay!")
        return
    text = "📅 LỊCH HÔM NAY\n\n"
    for f in fixtures[:15]:
        text += f"⏰ {f['fixture']['date'][11:16]} | {f['teams']['home']['name']} vs {f['teams']['away']['name']} ({f['league']['name']})\n"
    await update.message.reply_text(text)

async def ket_qua(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("⏳ Đang lấy live...")
    live = api_get("fixtures", {"live": "all"})
    if not live:
        await update.message.reply_text("⚽ Không có trận nào đang diễn ra!")
        return
    text = "🔴 ĐANG DIỄN RA\n\n"
    for f in live[:10]:
        gh = f["goals"]["home"] or 0
        ga = f["goals"]["away"] or 0
        m  = f["fixture"]["status"]["elapsed"] or 0
        text += f"⚡ {m}' | {f['teams']['home']['name']} {gh}-{ga} {f['teams']['away']['name']}\n"
    await update.message.reply_text(text)

async def bxh_anh(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("⏳ Đang lấy BXH...")
    data = api_get("standings", {"league": 39, "season": 2024})
    if not data:
        await update.message.reply_text("❌ Lỗi!")
        return
    s    = data[0]["league"]["standings"][0]
    text = "🏆 PREMIER LEAGUE 2024/25\n\n"
    for i, t in enumerate(s[:10]):
        medal = ["🥇","🥈","🥉"][i] if i < 3 else f"{t['rank']}."
        text += f"{medal} {t['team']['name']} | {t['points']}đ | GD:{t['goalsDiff']:+}\n"
    await update.message.reply_text(text)

async def bxh_tbn(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("⏳ Đang lấy BXH...")
    data = api_get("standings", {"league": 140, "season": 2024})
    if not data:
        await update.message.reply_text("❌ Lỗi!")
        return
    s    = data[0]["league"]["standings"][0]
    text = "🏆 LA LIGA 2024/25\n\n"
    for i, t in enumerate(s[:10]):
        medal = ["🥇","🥈","🥉"][i] if i < 3 else f"{t['rank']}."
        text += f"{medal} {t['team']['name']} | {t['points']}đ | GD:{t['goalsDiff']:+}\n"
    await update.message.reply_text(text)

async def du_doan(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args or len(context.args) < 2:
        await update.message.reply_text("Dùng: /dudoan Arsenal Chelsea")
        return
    team1 = context.args[0]
    team2 = " ".join(context.args[1:])
    await update.message.reply_text(f"🔮 Đang phân tích {team1} vs {team2}...")
    t1_data = api_get("teams", {"search": team1})
    t2_data = api_get("teams", {"search": team2})
    if not t1_data or not t2_data:
        await update.message.reply_text("❌ Không tìm thấy đội!")
        return
    t1_id     = t1_data[0]["team"]["id"]
    t2_id     = t2_data[0]["team"]["id"]
    home_form = await asyncio.to_thread(get_team_recent_form, t1_id)
    away_form = await asyncio.to_thread(get_team_recent_form, t2_id)
    h2h       = await asyncio.to_thread(get_head_to_head, t1_id, t2_id)
    prompt = f"""Phân tích {team1} vs {team2}:
{team1}: Form {home_form['form'] if home_form else 'N/A'} | Thắng {home_form['win_rate'] if home_form else 0}% | TB {home_form['avg_goals_for'] if home_form else 0} bàn
{team2}: Form {away_form['form'] if away_form else 'N/A'} | Thắng {away_form['win_rate'] if away_form else 0}% | TB {away_form['avg_goals_for'] if away_form else 0} bàn
H2H: {team1} thắng {h2h['team1_wins'] if h2h else 0} | Hòa {h2h['draws'] if h2h else 0} | {team2} thắng {h2h['team2_wins'] if h2h else 0}
Đưa ra: 1.Nhận định 2.Dự đoán tỷ số 3.Xác suất % 4.Kèo khuyên. Tiếng Việt, emoji."""
    result = await asyncio.to_thread(ai_predict, prompt)
    await update.message.reply_text(
        f"🔮 {team1} vs {team2}\n\n{result or '❌ Thử lại sau!'}"
    )

async def thong_ke(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
    total   = await predictions_col.count_documents({})
    correct = await predictions_col.count_documents({"correct": True})
    rate    = round(correct/total*100) if total > 0 else 0
    await update.message.reply_text(
        f"📊 Thống kê:\nTổng: {total}\nĐúng: {correct}\nChính xác: {rate}%"
    )

async def run_bot():
    global application, bot_loop
    bot_loop    = asyncio.get_event_loop()
    application = Application.builder().token(TOKEN).updater(None).build()
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("lichthidau", lich_thi_dau))
    application.add_handler(CommandHandler("ketqua", ket_qua))
    application.add_handler(CommandHandler("bxh_anh", bxh_anh))
    application.add_handler(CommandHandler("bxh_tbn", bxh_tbn))
    application.add_handler(CommandHandler("dudoan", du_doan))
    application.add_handler(CommandHandler("thongke", thong_ke))
    application.add_handler(CallbackQueryHandler(handle_prediction_vote, pattern="^pred_"))
    application.job_queue.run_repeating(auto_predict_scheduler, interval=1800, first=30)
    await application.initialize()
    await application.bot.set_webhook(
        url=f"{WEBHOOK_URL}/{TOKEN}", drop_pending_updates=True
    )
    await application.start()
    logging.info("✅ Bot Phân Tích Bóng Đá 24H started!")
    while True:
        await asyncio.sleep(3600)

def start_bot():
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(run_bot())

def main():
    threading.Thread(target=start_bot, daemon=True).start()
    time.sleep(3)
    flask_app.run(host="0.0.0.0", port=PORT)

if __name__ == "__main__":
    main()
