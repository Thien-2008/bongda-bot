# Bot Bóng Đá 24H v3.1 - Fixed all errors
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
from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton, Bot
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
CHANNEL_ID = int(os.environ.get("CHANNEL_ID", "-1003721755956"))
GROUP_ID   = int(os.environ.get("GROUP_ID", "-1003772590166"))
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
    return "Bot Bóng Đá 24H đang chạy!", 200

def run_flask():
    flask_app.run(host="0.0.0.0", port=PORT)

RSS_FEEDS = [
    "https://vnexpress.net/rss/the-thao/bong-da.rss",
    "https://tuoitre.vn/rss/the-thao.rss",
]

def ai_generate(prompt):
    try:
        response = groq_client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[{"role": "user", "content": prompt}],
            max_tokens=400,
            temperature=0.7
        )
        return response.choices[0].message.content
    except Exception as e:
        logging.error(f"Lỗi AI: {e}")
        return None

async def rewrite_with_ai(title, summary):
    prompt = (
        f"Viết lại tin bóng đá bằng tiếng Việt, hấp dẫn, có emoji, "
        f"KHÔNG copy nguyên văn, tối đa 150 từ.\n"
        f"Tiêu đề: {title}\nNội dung: {summary}\n"
        f"Chỉ trả về nội dung, không giải thích.\n#BongDa24H"
    )
    result = await asyncio.to_thread(ai_generate, prompt)
    return result or f"⚽ {title}\n\n{summary[:300]}\n\n#BongDa24H"

async def fetch_and_post_news(bot):
    posted = 0
    for rss_url in RSS_FEEDS:
        try:
            feed = feedparser.parse(rss_url)
            for entry in feed.entries[:3]:
                try:
                    title   = entry.get("title", "").strip()
                    summary = entry.get("summary", entry.get("description", ""))[:400]
                    link    = entry.get("link", "").strip()
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
                        reply_markup=keyboard
                    )
                    await news_col.insert_one({
                        "link": link, "title": title,
                        "posted_at": datetime.now()
                    })
                    posted += 1
                    await asyncio.sleep(5)
                except Exception as e:
                    logging.error(f"Lỗi entry: {e}")
        except Exception as e:
            logging.error(f"Lỗi RSS: {e}")
    return posted

async def post_daily_schedule(bot):
    result = await asyncio.to_thread(ai_generate,
        "Tạo lịch thi đấu bóng đá hôm nay các giải lớn, giờ VN, emoji đẹp.")
    if result:
        await bot.send_message(
            chat_id=CHANNEL_ID,
            text=f"📅 LỊCH THI ĐẤU HÔM NAY\n\n{result}\n\n#LịchThiĐấu"
        )

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

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "⚽ Chào mừng đến Bóng Đá 24H Việt Nam!\n\n"
        "🔥 Tin tức AI viết lại mỗi giờ\n"
        "📅 Lịch thi đấu tự động lúc 8h\n"
        "🗳 Vote dự đoán tỷ số\n\n"
        "/lichthidau - Lịch thi đấu hôm nay\n"
        "/ketqua - Kết quả mới nhất\n"
        "/bangxephang - Bảng xếp hạng\n"
        "/phanhtich [đội1] [đội2] - Phân tích trận"
    )

async def lich_thi_dau(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("⏳ Đang lấy lịch...")
    result = await asyncio.to_thread(ai_generate,
        "Lịch thi đấu bóng đá hôm nay các giải lớn, giờ VN, emoji đẹp.")
    await update.message.reply_text(result or "❌ Thử lại sau!")

async def ket_qua(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("⏳ Đang lấy kết quả...")
    result = await asyncio.to_thread(ai_generate,
        "Kết quả bóng đá hôm nay các giải lớn, tỷ số đầy đủ, emoji đẹp.")
    await update.message.reply_text(result or "❌ Thử lại sau!")

async def bang_xep_hang(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("⏳ Đang lấy BXH...")
    result = await asyncio.to_thread(ai_generate,
        "Top 5 BXH Premier League và La Liga mùa này, điểm số, emoji đẹp.")
    await update.message.reply_text(result or "❌ Thử lại sau!")

async def phan_tich(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Dùng: /phanhtich MU Chelsea")
        return
    teams = " ".join(context.args)
    await update.message.reply_text(f"⏳ Đang phân tích {teams}...")
    result = await asyncio.to_thread(ai_generate,
        f"Phân tích trận {teams}: lịch sử đối đầu, phong độ, dự đoán tỷ số. Emoji đẹp.")
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
        text=f"🗳 DỰ ĐOÁN KẾT QUẢ\n\n⚽ {team1} vs {team2}\n\nBạn nghĩ ai thắng?",
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
        f"📊 Vote {match}:\n🏆 Đội 1: {v1}\n🤝 Hòa: {vd}\n🏆 Đội 2: {v2}\nTổng: {v1+vd+v2}"
    )

async def scheduler(context: ContextTypes.DEFAULT_TYPE):
    bot = context.bot
    try:
        now = datetime.now()
        await fetch_and_post_news(bot)
        if now.hour == 8 and now.minute < 35:
            await post_daily_schedule(bot)
        logging.info("✅ Scheduler chạy xong!")
    except Exception as e:
        logging.error(f"Scheduler lỗi: {e}")

def force_delete_webhook():
    for _ in range(5):
        try:
            r = requests.get(
                f"https://api.telegram.org/bot{TOKEN}/deleteWebhook?drop_pending_updates=true",
                timeout=10
            )
            if r.json().get("ok"):
                logging.info("✅ Webhook deleted!")
                time.sleep(3)
                return
        except Exception as e:
            logging.error(f"Webhook error: {e}")
        time.sleep(2)

def main():
    threading.Thread(target=run_flask, daemon=True).start()
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

    # Scheduler dùng JobQueue của PTB - an toàn nhất
    app.job_queue.run_repeating(scheduler, interval=3600, first=20)

    logging.info("✅ Bot Bóng Đá 24H v3.1 started!")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
