import io
import json
import os
import re
import asyncio
from PIL import Image, ImageDraw
import edge_tts
from google import genai
from google.genai import types
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, CallbackQueryHandler, filters, ContextTypes
from aiohttp import web

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")

MONO_BANK_URL = "https://send.monobank.ua/"

# Испольуем вашу модель gemini-3.6-flash
MODEL_NAME = "gemini-3.6-flash"

client = genai.Client(api_key=GEMINI_API_KEY)

MEDICAL_DISCLAIMER = (
    "\n\n⚠️ **Важливо:** Цей бот є розважальним і не дає медичних порад! "
    "Якщо ви помітили зміну форми, кольору чи розміру родимок, висип або біль на шкірі — "
    "обов'язково зверніться до лікаря-дерматолога."
)

async def handle_ping(request):
    return web.Response(text="Molestrology UA is running!")

async def start_web_server():
    app = web.Application()
    app.router.add_get('/', handle_ping)
    app.router.add_get('/health', handle_ping)
    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.environ.get("PORT", 10000))
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()

def clean_text_for_tts(text: str) -> str:
    text = text.replace('*', '').replace('«', '').replace('»', '').replace('"', '')
    text = re.sub(r'[^\w\s,.!?-А-Яа-яЄєІіЇїҐґ]', ' ', text)
    return re.sub(r'\s+', ' ', text).strip()

def detect_gender_by_name(name: str) -> str:
    name_lower = name.lower()
    female_endings = ('а', 'я', 'іна', 'ина', 'ела', 'іза')
    if name_lower.endswith(female_endings) and not name_lower.endswith(('ілля', 'микита', 'сава', 'ярема')):
        return "female"
    return "male"

def get_prompt(mode: str, gender: str, user_name: str) -> str:
    if gender == "male":
        gender_instruction = (
            f"Користувач {user_name} — ЧОЛОВІК. "
            "ПОВНІСТЮ ІГНОРУЙ ЖІНОЧИЙ ОДЯГ ТА СУКНІ! "
            "Пиши про пошук панянки/дівчини/леді. Радити одягти чоловічий стиль (сорочка, смокінг, стильний піджак тощо)."
        )
    else:
        gender_instruction = (
            f"Користувач {user_name} — ЖІНКА. "
            "Пиши про пошук кавалера/чоловіка. Радити жіночий стиль (сукня, капелюшок тощо)."
        )

    prompts = {
        "love": f"""
            Ти — грайлива, дуже дотепна та кумедна українська астрологиня-сваха з додатка Molestrology. 
            ВАЖЛИВО: {gender_instruction}
            
            Проаналізуй це фото шкіри:
            1. Знайди всі родимки або цятки [ymin, xmin, ymax, xmax] у діапазоні від 0 до 1000.
            2. Напиши ПЕРСОНАЛЬНИЙ ЛЮБОВНИЙ ГОРОСКОП (3 короткі речення). 
            
            Вимоги: 
            - Згадай геометричні особливості цього візерунка (кути між точками, формацію).
            - Вигадай кумедну назву для сузір'я кохання.
            - Дай 2 кумедні порадоньки для зваблювання (що одягти і куди піти), СУВОРО враховуючи стать ({gender}).
            - Звертайся до людини на "Ви".
            
            Поверни відповідь СУВОРО у JSON: {{"moles": [[ymin, xmin, ymax, xmax]], "prediction": "Текст..."}}
        """,
        "money": """
            Ти — дотепний фінансовий астролог з додатка Molestrology. 
            Проаналізуй це фото шкіри/долоні:
            1. Знайди всі родимки або цятки [ymin, xmin, ymax, xmax] від 0 до 1000.
            2. Напиши ПЕРСОНАЛЬНИЙ ФІНАНСОВИЙ ГОРОСКОП (3 речення).
            
            Поверни відповідь СУВОРО у JSON: {"moles": [[ymin, xmin, ymax, xmax]], "prediction": "Текст..."}
        """,
        "pet": """
            Ти — космічний КІТ-АСТРОЛОГ з додатка Molestrology. 
            Проаналізуй це фото тваринки:
            1. Знайди всі цятки або родимки [ymin, xmin, ymax, xmax] від 0 до 1000.
            2. Напиши ПЕРСОНАЛЬНИЙ ГОРОСКОП ДЛЯ ТВАРИНКИ (3 речення).
            
            Поверни відповідь СУВОРО у JSON: {"moles": [[ymin, xmin, ymax, xmax]], "prediction": "Текст..."}
        """
    }
    return prompts.get(mode, prompts["love"])

def get_mode_keyboard():
    keyboard = [
        [InlineKeyboardButton("💘 Любовний гороскоп (по фото)", callback_data="mode_love")],
        [InlineKeyboardButton("💰 Фінансовий (по фото)", callback_data="mode_money")],
        [InlineKeyboardButton("🐾 Папстрологія (для тварин)", callback_data="mode_pet")],
        [InlineKeyboardButton("🌟 Загальний астропрогноз (без фото)", callback_data="mode_general")]
    ]
    return InlineKeyboardMarkup(keyboard)

def get_gender_keyboard():
    keyboard = [
        [InlineKeyboardButton("👨 Чоловік", callback_data="gender_male")],
        [InlineKeyboardButton("👩 Жінка", callback_data="gender_female")]
    ]
    return InlineKeyboardMarkup(keyboard)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["mode"] = context.user_data.get("mode", "love")
    user_name = update.effective_user.first_name or "Шукач Долі"

    await update.message.reply_text(
        f"✨ **Вітаю, {user_name}! Ласкаво просимо до Molestrology UA!** ✨\n\n"
        "Оберіть режим гороскопу або отримайте загальний прогноз на сьогодні:"
        f"{MEDICAL_DISCLAIMER}",
        reply_markup=get_mode_keyboard(),
        parse_mode="Markdown"
    )

async def set_mode(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.data == "mode_love":
        context.user_data["mode"] = "love"
        text = "💘 Обрано **Любовний режим**!\nВкажіть вашу стать та надішліть фото шкіри:"
        try:
            await query.edit_message_text(text=text, reply_markup=get_gender_keyboard(), parse_mode="Markdown")
        except Exception:
            await query.message.reply_text(text=text, reply_markup=get_gender_keyboard(), parse_mode="Markdown")
        return

    elif query.data == "mode_general":
        context.user_data["mode"] = "general"
        text = "🌟 Обрано **Загальний астропрогноз**!\nБудь ласка, вкажіть вашу стать:"
        try:
            await query.edit_message_text(text=text, reply_markup=get_gender_keyboard(), parse_mode="Markdown")
        except Exception:
            await query.message.reply_text(text=text, reply_markup=get_gender_keyboard(), parse_mode="Markdown")
        return

    elif query.data == "mode_money":
        context.user_data["mode"] = "money"
        text = "💰 Обрано **Фінансовий режим**! Надішліть фото долоні або шкіри."
    elif query.data == "mode_pet":
        context.user_data["mode"] = "pet"
        text = "🐾 Обрано **Папстрологію**! Надішліть фото носа, лапки чи шерсті улюбленця."

    try:
        await query.edit_message_text(text=text, parse_mode="Markdown")
    except Exception:
        await query.message.reply_text(text=text, parse_mode="Markdown")

async def generate_daily_horoscope(update: Update, context: ContextTypes.DEFAULT_TYPE, user_name: str, gender: str):
    msg = await update.callback_query.message.reply_text("🔮 Звіряю розташування планет на сьогодні...")

    target_gender = "чоловіка" if gender == "male" else "жінки"
    prompt = f"""
        Ти — грайлива, дотепна та кумедна українська астрологиня з додатка Molestrology.
        Склади загальний астропрогноз на сьогодні для {user_name} (стать: {target_gender}).
        
        Вимоги:
        - Почни з оригінального привітання.
        - Розкажи про "вплив Ретроградного Меркурія" або іншої планети в іронічному ключі.
        - Дай 3 кумедні поради на день (що варто зробити, чого уникати та який ваш залізобетонний талісман дня).
        - Довжина: 3-4 речення.
    """

    try:
        # Выносим генерацию Gemini в отдельный поток через asyncio.to_thread
        response = await asyncio.to_thread(
            client.models.generate_content,
            model=MODEL_NAME,
            contents=prompt
        )
        prediction_text = response.text

        keyboard = [
            [InlineKeyboardButton("☕ Пригостити астролога (Monobank)", url=MONO_BANK_URL)],
            [InlineKeyboardButton("🔄 Змінити режим гороскопу", callback_data="show_menu")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)

        await msg.edit_text(
            f"🌟 **Загальний астропрогноз на сьогодні для {user_name}:**\n\n{prediction_text}",
            reply_markup=reply_markup,
            parse_mode="Markdown"
        )

        clean_speech = clean_text_for_tts(prediction_text)
        if clean_speech:
            try:
                communicate = edge_tts.Communicate(clean_speech, "uk-UA-PolinaNeural")
                audio_stream = io.BytesIO()
                async for chunk in communicate.stream():
                    if chunk["type"] == "audio":
                        audio_stream.write(chunk["data"])
                audio_stream.seek(0)
                if audio_stream.getbuffer().nbytes > 0:
                    audio_stream.name = "voice.ogg"
                    await update.callback_query.message.reply_voice(voice=audio_stream)
            except Exception as tts_err:
                print(f"Помилка TTS: {tts_err}")

    except Exception as e:
        print(f"Помилка генерації загального прогнозу: {e}")
        await msg.edit_text("❌ Зірки сьогодні трохи затуманилися. Спробуйте ще раз пізніше!")

async def set_gender(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    gender = "male" if query.data == "gender_male" else "female"
    context.user_data["gender"] = gender
    gender_text = "чоловічу" if gender == "male" else "жіночу"

    mode = context.user_data.get("mode", "love")
    user_name = update.effective_user.first_name or "Шукач Долі"

    if mode == "general":
        await query.edit_message_text(f"✅ Обрано **{gender_text} стать**.")
        await generate_daily_horoscope(update, context, user_name, gender)
    else:
        text = f"✅ Обрано **{gender_text} стать**. Тепер надішліть фото шкіри з родимками!"
        try:
            await query.edit_message_text(text=text, parse_mode="Markdown")
        except Exception:
            await query.message.reply_text(text=text, parse_mode="Markdown")

async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    mode = context.user_data.get("mode", "love")
    user_name = update.effective_user.first_name or "Шукач Долі"
    
    gender = context.user_data.get("gender")
    if not gender:
        gender = detect_gender_by_name(user_name)
        context.user_data["gender"] = gender

    msg = await update.message.reply_text("🔮 Зчитую сакральну геометрію точок (10-15 сек)...")
    
    try:
        photo_file = await update.message.photo[-1].get_file()
        photo_bytes = await photo_file.download_as_bytearray()
        
        image = Image.open(io.BytesIO(photo_bytes)).convert("RGB")
        width, height = image.size

        prompt = get_prompt(mode, gender, user_name)

        # Выносим генерацию Gemini в отдельный поток
        response = await asyncio.to_thread(
            client.models.generate_content,
            model=MODEL_NAME,
            contents=[image, prompt],
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                temperature=1.0
            )
        )

        data = json.loads(response.text)
        moles = data.get("moles", [])
        prediction_text = data.get("prediction", "Зірки бачать шалений магнетизм!")

        context.user_data["last_prediction"] = prediction_text
        context.user_data["moles_count"] = len(moles)

        draw = ImageDraw.Draw(image)
        centers = []

        for mole in moles:
            ymin, xmin, ymax, xmax = mole
            cx = int(((xmin + xmax) / 2) / 1000 * width)
            cy = int(((ymin + ymax) / 2) / 1000 * height)
            centers.append((cx, cy))
            draw.ellipse([cx - 6, cy - 6, cx + 6, cy + 6], fill="red", outline="yellow", width=2)

        if len(centers) > 1:
            draw.line(centers, fill="cyan", width=4)
            if len(centers) > 2:
                draw.line([centers[-1], centers[0]], fill="cyan", width=4)

        img_buffer = io.BytesIO()
        image.save(img_buffer, format="JPEG")
        img_buffer.seek(0)

        keyboard = [
            [InlineKeyboardButton("☕ Пригостити астролога (Monobank)", url=MONO_BANK_URL)],
            [InlineKeyboardButton("🔄 Змінити режим гороскопу", callback_data="show_menu")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)

        if gender == "male":
            compatibility_hint = "🤫 *Псс... Якщо сфотографуєш родимки своєї обраниці (дівчини), я згенерую гороскоп вашої сумісності!*"
        else:
            compatibility_hint = "🤫 *Псс... Якщо сфотографуєш родимки свого кавалера (хлопця), я згенерую гороскоп вашої сумісності!*"

        caption_text = (
            f"✨ **Персональний астропрогноз для {user_name}:**\n\n{prediction_text}\n\n"
            f"{compatibility_hint}\n\n"
            f"💬 *Можете поставити запитання астрологу у чаті!*"
            f"{MEDICAL_DISCLAIMER}"
        )

        await update.message.reply_photo(
            photo=img_buffer, 
            caption=caption_text,
            reply_markup=reply_markup,
            parse_mode="Markdown"
        )
        
        clean_speech = clean_text_for_tts(prediction_text)
        if clean_speech:
            try:
                female_voice = "uk-UA-PolinaNeural"
                communicate = edge_tts.Communicate(clean_speech, female_voice)
                
                audio_stream = io.BytesIO()
                async for chunk in communicate.stream():
                    if chunk["type"] == "audio":
                        audio_stream.write(chunk["data"])

                audio_stream.seek(0)
                if audio_stream.getbuffer().nbytes > 0:
                    audio_stream.name = "voice.ogg"
                    await update.message.reply_voice(voice=audio_stream)
            except Exception as tts_err:
                print(f"Помилка TTS: {tts_err}")

        await msg.delete()

    except Exception as e:
        print(f"Помилка обробки: {e}")
        try:
            await msg.edit_text(f"❌ Помилка аналізу: {e}")
        except Exception:
            pass

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_text = update.message.text
    user_name = update.effective_user.first_name or "Шукач Долі"
    gender = context.user_data.get("gender") or detect_gender_by_name(user_name)
    last_prediction = context.user_data.get("last_prediction", "Фото ще не надсилалося.")
    moles_count = context.user_data.get("moles_count", 0)

    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")

    prompt = f"""
        Ти — грайлива, дотепна, але ВІДПОВІДАЛЬНА українська астрологиня з додатка Molestrology.
        
        Контекст користувача:
        - Ім'я: {user_name}
        - Стать: {gender}
        - Кількість родимок на фото: {moles_count}
        - Останній астрологічний аналіз: "{last_prediction}"

        Користувач запитує: "{user_text}"

        ІНСТРУКЦІЇ:
        1. Якщо питання стосується МЕДИЦИНИ, ВИСИПУ, ЗМІНИ РОДИМОК, БОЛЮ, СВЕРБЕЖУ, КРОВОТЕЧІ чи ПІДОЗРІЛИХ ПЛЯМ:
           - Обов'язково наголоси, що ти астролог, а не лікар!
           - Настійно порадь звернутися до дерматолога. Не став діагнози!
        2. Якщо це звичайне запитання:
           - Відповідай з урахуванням статі користувача ({gender}). Для чоловіків — підбирай поради стосовно жінок, для жінок — стосовно чоловіків.
           - Дай дотепну відповідь (2-3 речення).
    """

    try:
        # Выносим генерацию Gemini в отдельный поток
        response = await asyncio.to_thread(
            client.models.generate_content,
            model=MODEL_NAME,
            contents=prompt
        )
        await update.message.reply_text(response.text, parse_mode="Markdown")
    except Exception as e:
        print(f"Помилка текстового обробника: {e}")
        await update.message.reply_text("Зірки трохи збилися з ритму. Спробуйте поставити питання ще раз! ✨")

async def show_menu_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.message.reply_text(
        "Оберіть бажаний режим для наступного гороскопу:",
        reply_markup=get_mode_keyboard()
    )

async def post_init(application):
    """Безопасный запуск веб-сервера aiohttp внутри единого event loop телеграм-бота"""
    await start_web_server()

def main():
    # Регистрируем post_init hook вместо создания ручного asyncio.new_event_loop()
    app = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).post_init(post_init).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(set_mode, pattern="^mode_"))
    app.add_handler(CallbackQueryHandler(set_gender, pattern="^gender_"))
    app.add_handler(CallbackQueryHandler(show_menu_callback, pattern="^show_menu$"))
    
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    print("UA Бот успішно запущено!")
    app.run_polling(drop_pending_updates=True)

if __name__ == '__main__':
    main()
