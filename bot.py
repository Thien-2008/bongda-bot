# Bot Bóng Đá 24H v3.2 - No while True, no Conflict
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

TOKEN      = os.environ.get("TOKEN", "")
ADMIN_ID   = int(os.environ.get("ADMIN_ID", "0"))
CHANNEL_ID = os.environ.get("CHANNEL_ID", "-1003721755956")
GROQ_KEY   = os.environ.get("GROQ_KEY", "")
MONGO_URI  = os.environ.get("MONGO_URI", "")
PORT       = int(os.environ.get("PORT", 8080))

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

def ai_generate(prompt):
    try:
        r = groq_client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[{"role": "user", "content": prompt}],
            max_tokens=400, temperature=0.7
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

async def post_schedule(bot):
    result = await asyncio.to_thread(ai_generate,
        "Lịch thi đấu bóng đá hôm nay các giải lớn, giờ VN, emoji đẹp.")
    if result:
        await bot.send_message(chat_id=CHANNEL_ID,
            text=f"📅 LỊCH THI ĐẤU HÔM NAY\n\n{result}\n\n#LịchThiĐấu")

async def scheduler(context: ContextTypes.DEFAULT_TYPE):
    try:
        now = datetime.now()
        await fetch_and_post_news(context.bot)
        if now.hour == 8 and now.minute < 35:
            await post_schedule(context.bot)
    except Exception as e:
        logging.error(f"Scheduler lỗi: {e}")

async def handle_vote(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    parts = query.data.split("_")
    if len(parts) < 3:
        return
    match, choice = parts[1], parts[2]
    user_id = query.from_user.id
    if await votes_col.find_one({"match": match, "user_id": user_id}):
        await query.answer("Bạn đã vote rồi! 😅", show_alert=True)
        return
    await votes_col.insert_one({"match": match, "user_id": user_id, "choice": choice, "voted_at": datetime.now()})
    total = await votes_col.count_documents({"match": match})
    await query.answer(f"✅ Đã vote! Tổng {total} người", show_alert=True)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "⚽ Chào mừng đến Bóng Đá 24H!\n\n"
        "/lichthidau - Lịch thi đấu\n"
        "/ketqua - Kết quả\n"
        "/bangxephang - BXH\n"
        "/phanhtich MU Chelsea - Phân tích"
    )

async def lich_thi_dau(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("⏳ Đang lấy...")
    r = await asyncio.to_thread(ai_generate, "Lịch thi đấu bóng đá hôm nay, giờ VN, emoji.")
    await update.message.reply_text(r or "❌ Thử lại sau!")

async def ket_qua(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("⏳ Đang lấy...")
    r = await asyncio.to_thread(ai_generate, "Kết quả bóng đá hôm nay, tỷ số, emoji.")
    await update.message.reply_text(r or "❌ Thử lại sau!")

async def bang_xep_hang(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("⏳ Đang lấy...")
    r = await asyncio.to_thread(ai_generate, "Top 5 BXH Premier League và La Liga, emoji.")
    await update.message.reply_text(r or "❌ Thử lại sau!")

async def phan_tich(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Dùng: /phanhtich MU Chelsea")
        return
    teams = " ".join(context.args)
    await update.message.reply_text(f"⏳ Phân tích {teams}...")
    r = await asyncio.to_thread(ai_generate, f"Phân tích {teams}: lịch sử, phong độ, dự đoán, emoji.")
    await update.message.reply_text(r or "❌ Thử lại sau!")

async def dang_tin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID: return
    await update.message.reply_text("⏳ Đang đăng...")
    n = await fetch_and_post_news(context.bot)
    await update.message.reply_text(f"✅ Đã đăng {n} tin!")

async def dang_lich(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID: return
    await post_schedule(context.bot)
    await update.message.reply_text("✅ Xong!")

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
    await context.bot.send_message(chat_id=CHANNEL_ID,
        text=f"🗳 DỰ ĐOÁN\n\n⚽ {t1} vs {t2}\nBạn nghĩ ai thắng?", reply_markup=kb)
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
        f"📊 {match}:\n🏆 Đội 1: {v1}\n🤝 Hòa: {vd}\n🏆 Đội 2: {v2}\nTổng: {v1+vd+v2}")

def main():
    # Xóa webhook qua HTTP trước
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
    app.add_handler(CommandHandler("bangxephang", bang_xep_hang))
    app.add_handler(CommandHandler("phanhtich", phan_tich))
    app.add_handler(CommandHandler("dangtin", dang_tin))
    app.add_handler(CommandHandler("danglich", dang_lich))
    app.add_handler(CommandHandler("vote", tao_vote))
    app.add_handler(CommandHandler("ketquavote", ket_qua_vote))
    app.add_handler(CallbackQueryHandler(handle_vote, pattern="^vote_"))
    app.job_queue.run_repeating(scheduler, interval=3600, first=20)

    logging.info("✅ Bot v3.2 started!")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
