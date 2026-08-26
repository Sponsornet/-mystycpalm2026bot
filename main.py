import asyncio
import io
import os
import sqlite3
from typing import Dict, Any

from aiohttp import web
import aiohttp
from google import genai
from google.genai import types
from PIL import Image, ImageDraw, ImageFont
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    ApplicationBuilder,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

# =====================================================================
# 1. КОНФИГУРАЦИЯ И КЛЮЧИ
# =====================================================================
BOT_TOKEN = os.getenv("BOT_TOKEN") or os.getenv("TELEGRAM_BOT_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
MONO_TOKEN = os.getenv("MONO_TOKEN", "YOUR_MONOBANK_API_KEY")

PRICE_FULL_ANALYSIS_UAH = 50

ai_client = genai.Client(api_key=GEMINI_API_KEY)

# =====================================================================
# 2. СИСТЕМНЫЙ ПРОМПТ GEMINI
# =====================================================================
SYSTEM_PROMPT_UA = """
Ти — професійний, захоплюючий та доброзичливий бот-астролог і фізіогноміст "SkinStarlight".
Твоє завдання — проаналізувати родимки/точки на наданому фото та розкрити зоряну карту на шкірі користувача.

ВАЖЛИВО ЩОДО ТОНУ ТА ЗМІСТУ:
- Пиши натхненно, естетично, з повагою та без лякаючих чи надто негативних передбачень.
- Інформація має захоплювати, давати відчуття унікальності та позитивні емоції.

СТРУКТУРА ВІДПОВІДІ:
1. 🌌 **Зоряна проекція**:
   - Порівняй розташування точок із відомими сузір'ями (Оріон, Касіопея, Плеяди, Велика Ведмедиця тощо).
   - Оголоси найпомітнішу родимку "Альфа-Зіркою" (Сиріус, Вега, Альдебаран, Полярна).
2. 🧠 **Психологічний портрет (Молеософія)**:
   - Опиши риси характеру та приховані таланти залежно від розташування точок (наприклад: на щоці — артистизм та шарм; на лобі — мудрість та інтуїція; біля губ — харизма).
3. 🌟 **Зірковий двійник**:
   - Наведи приклад відомої особистості з схожим розташуванням родимок (Мерілін Монро, Скарлетт Йоганссон, Анжеліна Джолі, Бред Пітт тощо).
4. 📜 **Порада дня**: коротке натхненне напутнє слово.

Наприкінці відповіді обов'язково окремим рядком напиши:
ТИТУЛ: [Короткий та яскравий титул до 4 слів]
"""

# =====================================================================
# 3. БАЗА ДАННЫХ (SQLite)
# =====================================================================
def init_db():
    conn = sqlite3.connect("skinstarlight.db")
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS invoices (
            invoice_id TEXT PRIMARY KEY,
            user_id INTEGER,
            status TEXT DEFAULT 'created'
        )
    """)
    conn.commit()
    conn.close()

def register_user(user_id: int):
    conn = sqlite3.connect("skinstarlight.db")
    cursor = conn.cursor()
    cursor.execute("INSERT OR IGNORE INTO users (user_id) VALUES (?)", (user_id,))
    conn.commit()
    conn.close()

# =====================================================================
# 4. ИНТЕГРАЦИЯ С MONOBANK API
# =====================================================================
async def create_mono_invoice(user_id: int, amount_uah: int) -> Dict[str, Any]:
    url = "https://api.monobank.ua/api/merchant/invoice/create"
    headers = {"X-Token": MONO_TOKEN}
    payload = {
        "amount": amount_uah * 100,
        "ccy": 980,
        "merchantPaymInfo": {
            "destination": "Розширений Астрологічний Звіт SkinStarlight",
            "comment": f"Оплата для користувача {user_id}"
        },
        "redirectUrl": "https://t.me",
    }
    async with aiohttp.ClientSession() as session:
        async with session.post(url, json=payload, headers=headers) as resp:
            data = await resp.json()
            if resp.status == 200:
                conn = sqlite3.connect("skinstarlight.db")
                cursor = conn.cursor()
                cursor.execute("INSERT INTO invoices (invoice_id, user_id) VALUES (?, ?)", (data["invoiceId"], user_id))
                conn.commit()
                conn.close()
            return data

async def check_mono_invoice(invoice_id: str) -> bool:
    url = f"https://api.monobank.ua/api/merchant/invoice/status?invoiceId={invoice_id}"
    headers = {"X-Token": MONO_TOKEN}
    async with aiohttp.ClientSession() as session:
        async with session.get(url, headers=headers) as resp:
            if resp.status == 200:
                data = await resp.json()
                return data.get("status") == "success"
    return False

# =====================================================================
# 5. ГЕНЕРАЦИЯ ФОТО-КАРТОЧКИ (Pillow)
# =====================================================================
def create_starlight_card(image_bytes: bytes, title_text: str) -> bytes:
    img = Image.open(io.BytesIO(image_bytes)).convert("RGB")
    width, height = img.size

    banner_height = int(height * 0.16)
    overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
    overlay_draw = ImageDraw.Draw(overlay)
    
    overlay_draw.rectangle(
        [(0, height - banner_height), (width, height)],
        fill=(15, 12, 41, 210)
    )

    img = Image.alpha_composite(img.convert("RGBA"), overlay).convert("RGB")
    draw = ImageDraw.Draw(img)

    try:
        font_size = max(18, int(banner_height * 0.28))
        font = ImageFont.truetype("arial.ttf", font_size)
    except OSError:
        font = ImageFont.load_default()

    header = "✨ SKINSTARLIGHT AI ✨"
    full_text = f"{header}\n{title_text}"
    
    draw.text((20, height - banner_height + 15), full_text, fill=(255, 215, 0), font=font)

    output = io.BytesIO()
    img.save(output, format="JPEG", quality=92)
    return output.getvalue()

# =====================================================================
# 6. ХЭНДЛЕРЫ TELEGRAM
# =====================================================================
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    register_user(user_id)
    welcome_text = (
        "🔮 **Вітаю у SkinStarlight AI!**\n\n"
        "Кожна точка та родимка на вашій шкірі — це унікальна зоряна карта. "
        "Я проаналізую ваші сузір'я, розкрию приховані риси характеру та підберу вашого зіркового двійника!\n\n"
        "📸 **Надішліть чітке фото обличчя або ділянки тіла, щоб розпочати.**"
    )
    await update.message.reply_text(welcome_text, parse_mode="Markdown")

async def photo_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    register_user(user_id)
    
    wait_msg = await update.message.reply_text("🔮 *Сканую космічні точки та будую карту сузір'їв...*", parse_mode="Markdown")

    try:
        photo_file = await update.message.photo[-1].get_file()
        image_bytes = await photo_file.download_as_bytearray()

        image_part = types.Part.from_bytes(data=bytes(image_bytes), mime_type="image/jpeg")
        
        # Вызов Gemini в отдельном потоке
        response = await asyncio.to_thread(
            ai_client.models.generate_content,
            model="gemini-3.6-flash",
            contents=[image_part, "Проаналізуй фото відповідно до системної інструкції."],
            config=types.GenerateContentConfig(
                system_instruction=SYSTEM_PROMPT_UA,
                temperature=0.7
            )
        )

        full_text = response.text
        
        title_text = "Зоряна Проекція"
        if "ТИТУЛ:" in full_text:
            parts = full_text.split("ТИТУЛ:")
            title_text = parts[1].strip().split("\n")[0]
            caption_text = parts[0].strip()
        else:
            caption_text = full_text

        processed_bytes = create_starlight_card(image_bytes, title_text)
        photo_stream = io.BytesIO(processed_bytes)
        photo_stream.name = "starlight_result.jpg"

        kb = InlineKeyboardMarkup([[
            InlineKeyboardButton(
                text=f"💳 Отримати персональний VIP-розбір ({PRICE_FULL_ANALYSIS_UAH} грн)", 
                callback_data="buy_vip_analysis"
            )
        ]])

        await wait_msg.delete()
        await update.message.reply_photo(
            photo=photo_stream,
            caption=f"🌌 **Результат SkinStarlight**\n\n{caption_text}",
            parse_mode="Markdown",
            reply_markup=kb
        )
    except Exception as e:
        print(f"Помилка аналізу: {e}")
        await wait_msg.edit_text(f"❌ Помилка під час аналізу фото: {e}")

async def pay_invoice_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id

    invoice_data = await create_mono_invoice(user_id, PRICE_FULL_ANALYSIS_UAH)
    page_url = invoice_data.get("pageUrl")
    invoice_id = invoice_data.get("invoiceId")

    if page_url:
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton(text="Сплатити через Monobank 💳", url=page_url)],
            [InlineKeyboardButton(text="Перевірити оплату 🔄", callback_data=f"check_pay_{invoice_id}")]
        ])
        await query.message.reply_text(
            "Натисніть кнопку нижче для безпечної оплати через Monobank:", 
            reply_markup=kb
        )
    else:
        await query.message.reply_text("Помилка створення рахунку Monobank. Перевірте MONO_TOKEN.")

async def check_payment_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    invoice_id = query.data.split("_")[2]
    is_paid = await check_mono_invoice(invoice_id)
    
    if is_paid:
        await query.answer("Оплата успішна!", show_alert=True)
        await query.message.reply_text(
            "✅ **Оплата успішна!**\n\n"
            "Надішліть нове фото з приміткою або дод. питанням, щоб отримати максимальний поглиблений аналіз."
        )
    else:
        await query.answer("Оплата ще не пройшла. Спробуйте через декілька секунд.", show_alert=True)

# =====================================================================
# 7. ВЕБ-СЕРВЕР ДЛЯ RENDER (ФИКС PORT SCAN TIMEOUT)
# =====================================================================
async def handle_ping(request):
    return web.Response(text="SkinStarlight AI is running alive!")

async def start_web_server():
    app = web.Application()
    app.router.add_get('/', handle_ping)
    app.router.add_get('/health', handle_ping)
    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.environ.get("PORT", 10000))
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    print(f"🌐 Веб-сервер піднято на порту {port}")

async def post_init(application):
    await start_web_server()

def main():
    if not BOT_TOKEN or not GEMINI_API_KEY:
        raise ValueError("Критична помилка: BOT_TOKEN або GEMINI_API_KEY не задані в Environment Variables!")

    init_db()
    
    app = ApplicationBuilder().token(BOT_TOKEN).post_init(post_init).build()

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(MessageHandler(filters.PHOTO, photo_handler))
    app.add_handler(CallbackQueryHandler(pay_invoice_handler, pattern="^buy_vip_analysis$"))
    app.add_handler(CallbackQueryHandler(check_payment_callback, pattern="^check_pay_"))

    print("🚀 SkinStarlight Бот успішно запущено!")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
