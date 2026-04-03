# Bot Bóng Đá 24H v4.0 - Real API Football Data
import os
import asyncio
import logging
import threading
import time
import requests
import feedparser
from datetime import datetime
from flask import Flask
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
CHANNEL_ID   = os.environ.get("CHANNEL_ID", "-1003721755956")
GROQ_KEY     = os.environ.get("GROQ_KEY", "")
FOOTBALL_KEY = os.environ.get("FOOTBALL_KEY", "")
MONGO_URI    = os.environ.get("MONGO_URI", "")
PORT         = int(os.environ.get("PORT", 8080))

groq_client  = Groq(api_key=GROQ_KEY)
mongo_client = AsyncIOMotorClient(MONGO_URI)
db           = mongo_client["bongdabot"]
news_col     = db["posted_news"]
votes_col    = db["votes"]

flask_app = Flask(__name__)

@flask_app.route("/")
def index():
    return "OK", 200

def run_flask():
    flask_app.run(host="0.0.0.0", port=PORT)

RSS_FEEDS = [
    "https://vnexpress.net/rss/the-thao/bong-da.rss",
    "https://tuoitre.vn/rss/the-thao.rss",
]

# ── API Football ──────────────────────────────────────
FOOTBALL_HEADERS = {
    "x-apisports-key": FOOTBALL_KEY
}
FOOTBALL_URL = "https://v3.football.api-sports.io"

def get_today_fixtures():
    """Lấy lịch thi đấu hôm nay"""
    try:
        today = datetime.now().strftime("%Y-%m-%d")
        r = requests.get(
            f"{FOOTBALL_URL}/fixtures",
            headers=FOOTBALL_HEADERS,
            params={"date": today, "timezone": "Asia/Ho_Chi_Minh"},
            timeout=10
        )
        data = r.json()
        return data.get("response", [])
    except Exception as e:
        logging.error(f"API Football lỗi: {e}")
        return []

def get_live_scores():
    """Lấy kết quả đang diễn ra"""
    try:
        r = requests.get(
            f"{FOOTBALL_URL}/fixtures",
            headers=FOOTBALL_HEADERS,
            params={"live": "all"},
            timeout=10
        )
        data = r.json()
        return data.get("response", [])
    except Exception as e:
        logging.error(f"API Football live lỗi: {e}")
        return []

def get_standings(league_id, season=2024):
    """Lấy BXH"""
    try:
        r = requests.get(
            f"{FOOTBALL_URL}/standings",
            headers=FOOTBALL_HEADERS,
            params={"league": league_id, "season": season},
            timeout=10
        )
        data = r.json()
        standings = data.get("response", [])
        if standings:
            return standings[0]["league"]["standings"][0]
        return []
    except Exception as e:
        logging.error(f"API Football BXH lỗi: {e}")
        return []

def format_fixtures(fixtures):
    """Format lịch thi đấu đẹp"""
    if not fixtures:
        return "📅 Hôm nay không có trận đấu lớn!"
    
    text = ""
    leagues = {}
    for f in fixtures[:20]:
        league = f["league"]["name"]
        if league not in leagues:
            leagues[league] = []
        leagues[league].append(f)
    
    for league, matches in leagues.items():
        text += f"\n🏆 **{league}**\n"
        for m in matches:
            home = m["teams"]["home"]["name"]
            away = m["teams"]["away"]["name"]
            time_str = m["fixture"]["date"][11:16]
            status = m["fixture"]["status"]["short"]
            
            if status == "NS":
                text += f"⏰ {time_str} | {home} vs {away}\n"
            elif status in ["1H", "2H", "HT"]:
                g_home = m["goals"]["home"] or 0
                g_away = m["goals"]["away"] or 0
                text += f"🔴 LIVE | {home} {g_home}-{g_away} {away}\n"
            elif status == "FT":
                g_home = m["goals"]["home"] or 0
                g_away = m["goals"]["away"] or 0
                text += f"✅ KT | {home} {g_home}-{g_away} {away}\n"
    
    return text or "📅 Không có trận đấu!"

def format_standings(standings, title):
    """Format BXH đẹp"""
    if not standings:
        return f"❌ Không lấy được BXH {title}"
    
    text = f"🏆 **{title}**\n\n"
    medals = ["🥇", "🥈", "🥉"]
    for i, team in enumerate(standings[:10]):
        rank = team["rank"]
        name = team["team"]["name"]
        pts  = team["points"]
        played = team["all"]["played"]
        gd = team["goalsDiff"]
        medal = medals[i] if i < 3 else f"{rank}."
        text += f"{medal} {name} | {pts}đ | {played}trận | GD:{gd:+}\n"
    return text

# ── AI ────────────────────────────────────────────────
def ai_generate(prompt):
    try:
        r = groq_client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[{"role": "user", "content": prompt}],
            max_tokens=400,
            temperature=0.7
        )
        return r.choices[0].message.content
    except Exception as e:
        logging.error(f"AI lỗi: {e}")
        return None

async def rewrite_with_ai(title, summary):
    prompt = (
        f"Viết lại tin bóng đá tiếng Việt, hấp dẫn, emoji, KHÔNG copy, tối đa 150 từ.\n"
        f"Tiêu đề: {title}\nNội dung: {summary}\nChỉ trả nội dung.\n#BongDa24H"
    )
    result = await asyncio.to_thread(ai_generate, prompt)
    return result or f"⚽ {title}\n\n{summary[:300]}\n\n#BongDa24H"

# ── News ──────────────────────────────────────────────
async def fetch_and_post_news(bot):
    posted = 0
    for url in RSS_FEEDS:
        try:
            feed = feedparser.parse(url)
            for entry in feed.entries[:3]:
                try:
                    title   = entry.get("title", "").strip()
                    summary = entry.get("summary", entry.get("description", ""))[:400]
                    link    = entry.get("link", "").strip()
                    if not title or not link:
                        continue
                    if await news_col.find_one({"link": link}):
                        continue
                    content = await rewrite_with_ai(title, summary)
                    kb = InlineKeyboardMarkup([[
                        InlineKeyboardButton("🔗 Đọc thêm", url=link)
                    ]])
                    await bot.send_message(chat_id=CHANNEL_ID, text=content, reply_markup=kb)
                    await news_col.insert_one({"link": link, "title": title, "posted_at": datetime.now()})
                    posted += 1
                    await asyncio.sleep(5)
                except Exception as e:
                    logging.error(f"Entry lỗi: {e}")
        except Exception as e:
            logging.error(f"RSS lỗi: {e}")
    return posted

# ── Handlers ──────────────────────────────────────────
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "⚽ Chào mừng đến Bóng Đá 24H Việt Nam!\n\n"
        "🔥 Tin tức AI viết lại mỗi giờ\n"
        "📅 Lịch thi đấu THẬT từ API\n"
        "📊 BXH cập nhật real-time\n"
        "🗳 Vote dự đoán tỷ số\n\n"
        "/lichthidau - Lịch thi đấu hôm nay\n"
        "/ketqua - Kết quả đang diễn ra\n"
        "/bxh_anh - BXH Premier League\n"
        "/bxh_tbn - BXH La Liga\n"
        "/phanhtich [đội1] [đội2] - Phân tích AI\n"
        "/vote - Dự đoán tỷ số"
    )

async def lich_thi_dau(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("⏳ Đang lấy lịch thật...")
    fixtures = await asyncio.to_thread(get_today_fixtures)
    text = format_fixtures(fixtures)
    await update.message.reply_text(
        f"📅 LỊCH THI ĐẤU HÔM NAY\n{text}\n\n#LịchThiĐấu #BongDa24H",
        parse_mode="Markdown"
    )

async def ket_qua(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("⏳ Đang lấy kết quả live...")
    fixtures = await asyncio.to_thread(get_live_scores)
    if fixtures:
        text = format_fixtures(fixtures)
        await update.message.reply_text(
            f"🔴 ĐANG DIỄN RA\n{text}\n\n#LiveScore #BongDa24H",
            parse_mode="Markdown"
        )
    else:
        # Nếu không có live thì lấy kết quả hôm nay
        all_fixtures = await asyncio.to_thread(get_today_fixtures)
        finished = [f for f in all_fixtures if f["fixture"]["status"]["short"] == "FT"]
        if finished:
            text = format_fixtures(finished)
            await update.message.reply_text(
                f"✅ KẾT QUẢ HÔM NAY\n{text}\n\n#KếtQuả #BongDa24H",
                parse_mode="Markdown"
            )
        else:
            await update.message.reply_text("⚽ Chưa có trận nào kết thúc hôm nay!")

async def bxh_anh(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("⏳ Đang lấy BXH Premier League...")
    standings = await asyncio.to_thread(get_standings, 39, 2024)
    text = format_standings(standings, "Premier League 2024/25")
    await update.message.reply_text(text, parse_mode="Markdown")

async def bxh_tbn(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("⏳ Đang lấy BXH La Liga...")
    standings = await asyncio.to_thread(get_standings, 140, 2024)
    text = format_standings(standings, "La Liga 2024/25")
    await update.message.reply_text(text, parse_mode="Markdown")

async def phan_tich(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Dùng: /phanhtich MU Chelsea")
        return
    teams = " ".join(context.args)
    await update.message.reply_text(f"⏳ AI đang phân tích {teams}...")
    result = await asyncio.to_thread(ai_generate,
        f"Phân tích chi tiết trận {teams}: phong độ gần đây, "
        f"lịch sử đối đầu, điểm mạnh yếu, dự đoán tỷ số. "
        f"Viết tiếng Việt, emoji đẹp, chuyên nghiệp.")
    await update.message.reply_text(result or "❌ Thử lại sau!")

async def handle_vote(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query   = update.callback_query
    parts   = query.data.split("_")
    if len(parts) < 3:
        return
    match, choice = parts[1], parts[2]
    user_id = query.from_user.id
    if await votes_col.find_one({"match": match, "user_id": user_id}):
        await query.answer("Bạn đã vote rồi! 😅", show_alert=True)
        return
    await votes_col.insert_one({
        "match": match, "user_id": user_id,
        "choice": choice, "voted_at": datetime.now()
    })
    total = await votes_col.count_documents({"match": match})
    await query.answer(f"✅ Đã vote! Tổng {total} người", show_alert=True)

async def dang_tin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID: return
    await update.message.reply_text("⏳ Đang đăng...")
    n = await fetch_and_post_news(context.bot)
    await update.message.reply_text(f"✅ Đã đăng {n} tin!")

async def tao_vote(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID: return
    if len(context.args) < 3:
        await update.message.reply_text("Dùng: /vote <id> <doi1> <doi2>")
        return
    match, t1, t2 = context.args[0], context.args[1], context.args[2]
    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton(f"🏆 {t1}", callback_data=f"vote_{match}_1"),
        InlineKeyboardButton("🤝 Hòa", callback_data=f"vote_{match}_draw"),
        InlineKeyboardButton(f"🏆 {t2}", callback_data=f"vote_{match}_2"),
    ]])
    await context.bot.send_message(
        chat_id=CHANNEL_ID,
        text=f"🗳 DỰ ĐOÁN KẾT QUẢ\n\n⚽ {t1} vs {t2}\nBạn nghĩ ai thắng?",
        reply_markup=kb
    )
    await update.message.reply_text("✅ Đã tạo vote!")

async def ket_qua_vote(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID: return
    if not context.args:
        await update.message.reply_text("Dùng: /ketquavote <id>")
        return
    match = context.args[0]
    v1 = await votes_col.count_documents({"match": match, "choice": "1"})
    vd = await votes_col.count_documents({"match": match, "choice": "draw"})
    v2 = await votes_col.count_documents({"match": match, "choice": "2"})
    await update.message.reply_text(
        f"📊 {match}:\n🏆 Đội 1: {v1}\n🤝 Hòa: {vd}\n🏆 Đội 2: {v2}\nTổng: {v1+vd+v2}"
    )

async def scheduler(context: ContextTypes.DEFAULT_TYPE):
    try:
        now = datetime.now()
        # Đăng tin mỗi giờ
        await fetch_and_post_news(context.bot)
        # Đăng lịch lúc 8h sáng
        if now.hour == 8 and now.minute < 35:
            fixtures = await asyncio.to_thread(get_today_fixtures)
            text = format_fixtures(fixtures)
            await context.bot.send_message(
                chat_id=CHANNEL_ID,
                text=f"📅 LỊCH THI ĐẤU HÔM NAY\n{text}\n\n#LịchThiĐấu",
                parse_mode="Markdown"
            )
        logging.info("✅ Scheduler xong!")
    except Exception as e:
        logging.error(f"Scheduler lỗi: {e}")

def main():
    try:
        requests.get(
            f"https://api.telegram.org/bot{TOKEN}/deleteWebhook?drop_pending_updates=true",
            timeout=10
        )
        time.sleep(3)
    except:
        pass

    threading.Thread(target=run_flask, daemon=True).start()

    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("lichthidau", lich_thi_dau))
    app.add_handler(CommandHandler("ketqua", ket_qua))
    app.add_handler(CommandHandler("bxh_anh", bxh_anh))
    app.add_handler(CommandHandler("bxh_tbn", bxh_tbn))
    app.add_handler(CommandHandler("phanhtich", phan_tich))
    app.add_handler(CommandHandler("dangtin", dang_tin))
    app.add_handler(CommandHandler("vote", tao_vote))
    app.add_handler(CommandHandler("ketquavote", ket_qua_vote))
    app.add_handler(CallbackQueryHandler(handle_vote, pattern="^vote_"))
    app.job_queue.run_repeating(scheduler, interval=3600, first=20)

    logging.info("✅ Bot v4.0 started!")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
