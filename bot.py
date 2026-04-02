# Bot Bóng Đá 24H - Full Auto
import os
import asyncio
import logging
import threading
import time
import requests
import feedparser
from datetime import datetime
from flask import Flask
from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler,
    MessageHandler, filters, ContextTypes
)
import google.generativeai as genai
from motor.motor_asyncio import AsyncIOMotorClient

logging.basicConfig(level=logging.INFO)

# ── Config ────────────────────────────────────────────
TOKEN       = os.environ.get("TOKEN", "")
ADMIN_ID    = int(os.environ.get("ADMIN_ID", "0"))
CHANNEL_ID  = int(os.environ.get("CHANNEL_ID", "-1003721755956"))
GROUP_ID    = int(os.environ.get("GROUP_ID", "-1003772590166"))
GEMINI_KEY  = os.environ.get("GEMINI_KEY", "")
MONGO_URI   = os.environ.get("MONGO_URI", "")
PORT        = int(os.environ.get("PORT", 8080))

# ── Gemini AI ─────────────────────────────────────────
genai.configure(api_key=GEMINI_KEY)
model = genai.GenerativeModel("gemini-1.5-flash")

# ── MongoDB ───────────────────────────────────────────
client    = AsyncIOMotorClient(MONGO_URI)
db        = client["bongdabot"]
news_col  = db["posted_news"]
votes_col = db["votes"]
users_col = db["users"]

# ── Flask ─────────────────────────────────────────────
flask_app = Flask(__name__)

@flask_app.route("/")
def index():
    return "Bot Bóng Đá 24H đang chạy!", 200

def run_flask():
    flask_app.run(host="0.0.0.0", port=PORT)

# ── RSS Sources bóng đá ───────────────────────────────
RSS_FEEDS = [
    "https://www.bongda.com.vn/rss/toan-cau.rss",
    "https://bongdaplus.vn/rss/tin-tuc.rss",
    "https://vnexpress.net/rss/the-thao/bong-da.rss",
]

# ── AI viết lại tin ───────────────────────────────────
async def rewrite_with_ai(title, summary):
    try:
        prompt = f"""Viết lại tin bóng đá sau theo phong cách hấp dẫn, ngắn gọn bằng tiếng Việt.
Thêm emoji phù hợp. KHÔNG copy nguyên văn. Tối đa 200 từ.

Tiêu đề gốc: {title}
Nội dung gốc: {summary}

Trả về định dạng:
**[TIÊU ĐỀ HAY]**

[NỘI DUNG VIẾT LẠI]

#BongDa24H #BóngĐá"""
        response = model.generate_content(prompt)
        return response.text
    except Exception as e:
        logging.error(f"AI error: {e}")
        return f"⚽ {title}\n\n{summary}\n\n#BongDa24H"

# ── Lấy & đăng tin tức ───────────────────────────────
async def fetch_and_post_news(bot):
    for rss_url in RSS_FEEDS:
        try:
            feed = feedparser.parse(rss_url)
            for entry in feed.entries[:2]:
                title = entry.get("title", "")
                summary = entry.get("summary", entry.get("description", ""))[:500]
                link = entry.get("link", "")

                # Kiểm tra đã đăng chưa
                exists = await news_col.find_one({"link": link})
                if exists:
                    continue

                # AI viết lại
                content = await rewrite_with_ai(title, summary)

                # Đăng lên kênh
                keyboard = InlineKeyboardMarkup([[
                    InlineKeyboardButton("🔗 Đọc thêm", url=link),
                    InlineKeyboardButton("💬 Thảo luận", url=f"https://t.me/c/{str(GROUP_ID)[4:]}/1")
                ]])
                await bot.send_message(
                    chat_id=CHANNEL_ID,
                    text=content,
                    parse_mode="Markdown",
                    reply_markup=keyboard
                )

                # Lưu vào DB tránh đăng lại
                await news_col.insert_one({
                    "link": link,
                    "title": title,
                    "posted_at": datetime.now()
                })
                await asyncio.sleep(3)

        except Exception as e:
            logging.error(f"RSS error: {e}")

# ── Lịch thi đấu hàng ngày ───────────────────────────
async def post_daily_schedule(bot):
    try:
        prompt = """Tạo lịch thi đấu bóng đá hôm nay (các giải lớn: Premier League, La Liga, Champions League, V-League).
Format đẹp với emoji, giờ Việt Nam. Nếu không có trận thì ghi "Hôm nay không có trận đấu lớn".
Thêm hashtag #LịchThiĐấu #BongDa24H"""
        response = model.generate_content(prompt)
        await bot.send_message(
            chat_id=CHANNEL_ID,
            text=f"📅 **LỊCH THI ĐẤU HÔM NAY**\n\n{response.text}",
            parse_mode="Markdown"
        )
    except Exception as e:
        logging.error(f"Schedule error: {e}")

# ── Vote dự đoán tỷ số ────────────────────────────────
async def post_vote(bot, match, team1, team2):
    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton(f"🏆 {team1} thắng", callback_data=f"vote_{match}_1"),
            InlineKeyboardButton("🤝 Hòa", callback_data=f"vote_{match}_draw"),
            InlineKeyboardButton(f"🏆 {team2} thắng", callback_data=f"vote_{match}_2"),
        ]
    ])
    msg = await bot.send_message(
        chat_id=CHANNEL_ID,
        text=f"🗳 **DỰ ĐOÁN KẾT QUẢ**\n\n⚽ {team1} vs {team2}\n\nBạn nghĩ ai sẽ thắng?",
        parse_mode="Markdown",
        reply_markup=keyboard
    )

# ── Xử lý vote ────────────────────────────────────────
async def handle_vote(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data  # vote_match_choice
    parts = data.split("_")
    match = parts[1]
    choice = parts[2]
    user_id = query.from_user.id

    # Kiểm tra đã vote chưa
    existing = await votes_col.find_one({"match": match, "user_id": user_id})
    if existing:
        await query.answer("Bạn đã vote rồi!", show_alert=True)
        return

    await votes_col.insert_one({
        "match": match,
        "user_id": user_id,
        "choice": choice,
        "voted_at": datetime.now()
    })

    # Đếm kết quả
    total = await votes_col.count_documents({"match": match})
    v1 = await votes_col.count_documents({"match": match, "choice": "1"})
    vd = await votes_col.count_documents({"match": match, "choice": "draw"})
    v2 = await votes_col.count_documents({"match": match, "choice": "2"})

    await query.answer(f"✅ Đã vote! Tổng {total} người vote", show_alert=True)

# ── Lệnh /start ───────────────────────────────────────
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "⚽ Chào mừng đến với **Bóng Đá 24H**!\n\n"
        "📢 Kênh tin tức: @bongda24hvn\n"
        "💬 Thảo luận: Nhóm đính kèm\n\n"
        "Các lệnh:\n"
        "/lichthidau — Lịch thi đấu hôm nay\n"
        "/ketqua — Kết quả mới nhất\n"
        "/bangxephang — Bảng xếp hạng",
        parse_mode="Markdown"
    )

# ── Lệnh /lichthidau ──────────────────────────────────
async def lich_thi_dau(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("⏳ Đang lấy lịch thi đấu...")
    try:
        prompt = "Liệt kê lịch thi đấu bóng đá hôm nay các giải lớn, giờ Việt Nam, format đẹp với emoji."
        response = model.generate_content(prompt)
        await update.message.reply_text(response.text)
    except:
        await update.message.reply_text("❌ Không lấy được lịch, thử lại sau!")

# ── Lệnh /ketqua ──────────────────────────────────────
async def ket_qua(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("⏳ Đang lấy kết quả...")
    try:
        prompt = "Liệt kê kết quả bóng đá mới nhất hôm nay các giải lớn, format đẹp với emoji và tỷ số."
        response = model.generate_content(prompt)
        await update.message.reply_text(response.text)
    except:
        await update.message.reply_text("❌ Không lấy được kết quả, thử lại sau!")

# ── Lệnh /bangxephang ─────────────────────────────────
async def bang_xep_hang(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("⏳ Đang lấy bảng xếp hạng...")
    try:
        prompt = "Liệt kê top 5 bảng xếp hạng Premier League và La Liga hiện tại, format đẹp với emoji."
        response = model.generate_content(prompt)
        await update.message.reply_text(response.text)
    except:
        await update.message.reply_text("❌ Không lấy được BXH, thử lại sau!")

# ── Lệnh admin /dangtin ───────────────────────────────
async def dang_tin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
    await fetch_and_post_news(context.bot)
    await update.message.reply_text("✅ Đã đăng tin mới!")

# ── Lệnh admin /vote ──────────────────────────────────
async def tao_vote(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
    if len(context.args) < 3:
        await update.message.reply_text("Dùng: /vote <match_id> <team1> <team2>\nVD: /vote mu_chelsea MU Chelsea")
        return
    match = context.args[0]
    team1 = context.args[1]
    team2 = context.args[2]
    await post_vote(context.bot, match, team1, team2)
    await update.message.reply_text("✅ Đã tạo vote!")

# ── Lệnh admin /lichhdangtin ──────────────────────────
async def lich_dang_tin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
    await update.message.reply_text(
        "📋 Bot tự động:\n"
        "• Đăng tin mỗi 1 giờ\n"
        "• Lịch thi đấu lúc 8:00 sáng\n"
        "• Chạy 24/7 tự động"
    )

# ── Auto scheduler ────────────────────────────────────
async def scheduler(bot):
    while True:
        try:
            now = datetime.now()
            # Đăng tin mỗi 1 giờ
            await fetch_and_post_news(bot)

            # Đăng lịch thi đấu lúc 8 giờ sáng
            if now.hour == 8 and now.minute < 5:
                await post_daily_schedule(bot)

            await asyncio.sleep(3600)  # 1 giờ
        except Exception as e:
            logging.error(f"Scheduler error: {e}")
            await asyncio.sleep(60)

async def post_init(app):
    asyncio.create_task(scheduler(app.bot))
    logging.info("✅ Bot Bóng Đá 24H started!")

def force_delete_webhook():
    for attempt in range(5):
        try:
            url = f"https://api.telegram.org/bot{TOKEN}/deleteWebhook?drop_pending_updates=true"
            r = requests.get(url, timeout=10)
            if r.json().get("ok"):
                time.sleep(3)
                return True
        except:
            pass
        time.sleep(2)
    return False

def run_bot():
    while True:
        try:
            force_delete_webhook()
            app = Application.builder().token(TOKEN).post_init(post_init).build()
            app.add_handler(CommandHandler("start", start))
            app.add_handler(CommandHandler("lichthidau", lich_thi_dau))
            app.add_handler(CommandHandler("ketqua", ket_qua))
            app.add_handler(CommandHandler("bangxephang", bang_xep_hang))
            app.add_handler(CommandHandler("dangtin", dang_tin))
            app.add_handler(CommandHandler("vote", tao_vote))
            app.add_handler(CommandHandler("lichhdangtin", lich_dang_tin))
            app.add_handler(CallbackQueryHandler(handle_vote, pattern="^vote_"))
            logging.info("✅ Bot started!")
            app.run_polling(drop_pending_updates=True)
        except Exception as e:
            logging.error(f"Bot crashed: {e} — restarting in 10s...")
            time.sleep(10)

def main():
    t = threading.Thread(target=run_flask)
    t.daemon = True
    t.start()
    run_bot()

if __name__ == "__main__":
    main()
