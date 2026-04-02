# Bot Bóng Đá 24H v2.1 - Fix scheduler + post_init
import os
import asyncio
import logging
import threading
import time
import requests
import feedparser
from datetime import datetime
from flask import Flask
from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton, Bot
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler,
    filters, ContextTypes
)
from google import genai
from motor.motor_asyncio import AsyncIOMotorClient

logging.basicConfig(level=logging.INFO)

TOKEN      = os.environ.get("TOKEN", "")
ADMIN_ID   = int(os.environ.get("ADMIN_ID", "0"))
CHANNEL_ID = int(os.environ.get("CHANNEL_ID", "-1003721755956"))
GROUP_ID   = int(os.environ.get("GROUP_ID", "-1003772590166"))
GEMINI_KEY = os.environ.get("GEMINI_KEY", "")
MONGO_URI  = os.environ.get("MONGO_URI", "")
PORT       = int(os.environ.get("PORT", 8080))

client_ai    = genai.Client(api_key=GEMINI_KEY)
mongo_client = AsyncIOMotorClient(MONGO_URI)
db           = mongo_client["bongdabot"]
news_col     = db["posted_news"]
votes_col    = db["votes"]

flask_app = Flask(__name__)

@flask_app.route("/")
def index():
    return "Bot Bóng Đá 24H đang chạy!", 200

def run_flask():
    flask_app.run(host="0.0.0.0", port=PORT)

RSS_FEEDS = [
    "https://vnexpress.net/rss/the-thao/bong-da.rss",
    "https://tuoitre.vn/rss/the-thao.rss",
    "https://thanhnien.vn/rss/the-thao.rss",
]

def ai_generate(prompt):
    try:
        response = client_ai.models.generate_content(
            model="gemini-2.0-flash",
            contents=prompt
        )
        return response.text
    except Exception as e:
        logging.error(f"AI error: {e}")
        return None

async def rewrite_with_ai(title, summary):
    prompt = f"""Viết lại tin bóng đá sau theo phong cách hấp dẫn, ngắn gọn bằng tiếng Việt.
Thêm emoji phù hợp. KHÔNG copy nguyên văn. Tối đa 200 từ.
Tiêu đề gốc: {title}
Nội dung gốc: {summary}
Định dạng:
**[TIÊU ĐỀ HAY]**
[NỘI DUNG]
#BongDa24H #BóngĐá"""
    result = await asyncio.to_thread(ai_generate, prompt)
    return result or f"⚽ {title}\n\n{summary}\n\n#BongDa24H"

async def fetch_and_post_news(bot):
    posted = 0
    for rss_url in RSS_FEEDS:
        try:
            feed = feedparser.parse(rss_url)
            for entry in feed.entries[:2]:
                title   = entry.get("title", "")
                summary = entry.get("summary", entry.get("description", ""))[:500]
                link    = entry.get("link", "")
                if not title or not link:
                    continue
                exists = await news_col.find_one({"link": link})
                if exists:
                    continue
                content = await rewrite_with_ai(title, summary)
                keyboard = InlineKeyboardMarkup([[
                    InlineKeyboardButton("🔗 Đọc thêm", url=link),
                ]])
                await bot.send_message(
                    chat_id=CHANNEL_ID,
                    text=content,
                    parse_mode="Markdown",
                    reply_markup=keyboard
                )
                await news_col.insert_one({
                    "link": link, "title": title,
                    "posted_at": datetime.now()
                })
                posted += 1
                await asyncio.sleep(3)
        except Exception as e:
            logging.error(f"RSS error: {e}")
    return posted

async def post_daily_schedule(bot):
    prompt = "Tạo lịch thi đấu bóng đá hôm nay các giải lớn, giờ VN, format đẹp với emoji."
    result = await asyncio.to_thread(ai_generate, prompt)
    if result:
        await bot.send_message(
            chat_id=CHANNEL_ID,
            text=f"📅 *LỊCH THI ĐẤU HÔM NAY*\n\n{result}\n\n#LịchThiĐấu",
            parse_mode="Markdown"
        )

async def handle_vote(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query   = update.callback_query
    parts   = query.data.split("_")
    if len(parts) < 3:
        return
    match   = parts[1]
    choice  = parts[2]
    user_id = query.from_user.id
    existing = await votes_col.find_one({"match": match, "user_id": user_id})
    if existing:
        await query.answer("Bạn đã vote rồi! 😅", show_alert=True)
        return
    await votes_col.insert_one({
        "match": match, "user_id": user_id,
        "choice": choice, "voted_at": datetime.now()
    })
    total = await votes_col.count_documents({"match": match})
    await query.answer(f"✅ Đã vote! Tổng {total} người", show_alert=True)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "⚽ Chào mừng đến với *Bóng Đá 24H Việt Nam*!\n\n"
        "/lichthidau — Lịch thi đấu hôm nay\n"
        "/ketqua — Kết quả mới nhất\n"
        "/bangxephang — Bảng xếp hạng\n"
        "/phanhtich — Phân tích trận đấu",
        parse_mode="Markdown"
    )

async def lich_thi_dau(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("⏳ Đang lấy lịch...")
    result = await asyncio.to_thread(ai_generate, "Lịch thi đấu bóng đá hôm nay các giải lớn, giờ VN, emoji đẹp.")
    await update.message.reply_text(result or "❌ Thử lại sau!")

async def ket_qua(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("⏳ Đang lấy kết quả...")
    result = await asyncio.to_thread(ai_generate, "Kết quả bóng đá mới nhất hôm nay các giải lớn, tỷ số, emoji đẹp.")
    await update.message.reply_text(result or "❌ Thử lại sau!")

async def bang_xep_hang(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("⏳ Đang lấy BXH...")
    result = await asyncio.to_thread(ai_generate, "Top 5 BXH Premier League và La Liga hiện tại, emoji đẹp.")
    await update.message.reply_text(result or "❌ Thử lại sau!")

async def phan_tich(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Dùng: /phanhtich MU Chelsea")
        return
    teams  = " ".join(context.args)
    await update.message.reply_text(f"⏳ Đang phân tích {teams}...")
    result = await asyncio.to_thread(ai_generate, f"Phân tích trận {teams}: lịch sử đối đầu, phong độ, dự đoán. Emoji đẹp.")
    await update.message.reply_text(result or "❌ Thử lại sau!")

async def dang_tin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
    await update.message.reply_text("⏳ Đang đăng tin...")
    posted = await fetch_and_post_news(context.bot)
    await update.message.reply_text(f"✅ Đã đăng {posted} tin!")

async def dang_lich(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
    await post_daily_schedule(context.bot)
    await update.message.reply_text("✅ Đã đăng lịch!")

async def tao_vote(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
    if len(context.args) < 3:
        await update.message.reply_text("Dùng: /vote <id> <doi1> <doi2>")
        return
    match, team1, team2 = context.args[0], context.args[1], context.args[2]
    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton(f"🏆 {team1}", callback_data=f"vote_{match}_1"),
        InlineKeyboardButton("🤝 Hòa", callback_data=f"vote_{match}_draw"),
        InlineKeyboardButton(f"🏆 {team2}", callback_data=f"vote_{match}_2"),
    ]])
    await context.bot.send_message(
        chat_id=CHANNEL_ID,
        text=f"🗳 *DỰ ĐOÁN KẾT QUẢ*\n\n⚽ {team1} vs {team2}\n\nBạn nghĩ ai thắng?",
        parse_mode="Markdown",
        reply_markup=keyboard
    )
    await update.message.reply_text("✅ Đã tạo vote!")

async def ket_qua_vote(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
    if not context.args:
        await update.message.reply_text("Dùng: /ketquavote <id>")
        return
    match = context.args[0]
    v1 = await votes_col.count_documents({"match": match, "choice": "1"})
    vd = await votes_col.count_documents({"match": match, "choice": "draw"})
    v2 = await votes_col.count_documents({"match": match, "choice": "2"})
    await update.message.reply_text(
        f"📊 Kết quả vote {match}:\n\n"
        f"🏆 Đội 1: {v1}\n🤝 Hòa: {vd}\n🏆 Đội 2: {v2}\n"
        f"Tổng: {v1+vd+v2} người"
    )

def run_scheduler():
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    async def _scheduler():
        await asyncio.sleep(15)
        bot = Bot(token=TOKEN)
        while True:
            try:
                now = datetime.now()
                await fetch_and_post_news(bot)
                if now.hour == 8 and now.minute < 5:
                    await post_daily_schedule(bot)
                await asyncio.sleep(3600)
            except Exception as e:
                logging.error(f"Scheduler error: {e}")
                await asyncio.sleep(60)

    loop.run_until_complete(_scheduler())

def force_delete_webhook():
    for _ in range(5):
        try:
            r = requests.get(
                f"https://api.telegram.org/bot{TOKEN}/deleteWebhook?drop_pending_updates=true",
                timeout=10
            )
            if r.json().get("ok"):
                time.sleep(3)
                return
        except:
            pass
        time.sleep(2)

def run_bot():
    while True:
        try:
            force_delete_webhook()
            app = Application.builder().token(TOKEN).build()
            app.add_handler(CommandHandler("start", start))
            app.add_handler(CommandHandler("lichthidau", lich_thi_dau))
            app.add_handler(CommandHandler("ketqua", ket_qua))
            app.add_handler(CommandHandler("bangxephang", bang_xep_hang))
            app.add_handler(CommandHandler("phanhtich", phan_tich))
            app.add_handler(CommandHandler("dangtin", dang_tin))
            app.add_handler(CommandHandler("danglich", dang_lich))
            app.add_handler(CommandHandler("vote", tao_vote))
            app.add_handler(CommandHandler("ketquavote", ket_qua_vote))
            app.add_handler(CallbackQueryHandler(handle_vote, pattern="^vote_"))
            logging.info("✅ Bot Bóng Đá 24H started!")
            app.run_polling(drop_pending_updates=True)
        except Exception as e:
            logging.error(f"Bot crashed: {e} — restarting in 10s...")
            time.sleep(10)

def main():
    threading.Thread(target=run_flask, daemon=True).start()
    threading.Thread(target=run_scheduler, daemon=True).start()
    run_bot()

if __name__ == "__main__":
    main()
