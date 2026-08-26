import os
from threading import Thread
from http.server import HTTPServer, BaseHTTPRequestHandler
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters, ContextTypes
import google.generativeai as genai

# ==========================================
# 1. HTTP-СЕРВЕР ДЛЯ ЗАГЛУШКИ RENDER (Port 10000)
# ==========================================
class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"OK - Bot is running")

    def log_message(self, format, *args):
        return  # Отключаем лишний спам в логах Render

def run_health_check_server():
    port = int(os.environ.get("PORT", 10000))
    server = HTTPServer(('0.0.0.0', port), HealthCheckHandler)
    server.serve_forever()

# ==========================================
# 2. ИНИЦИАЛИЗАЦИЯ GEMINI API
# ==========================================
GEMINI_KEY = os.environ.get("GEMINI_API_KEY")
if GEMINI_KEY:
    genai.configure(api_key=GEMINI_KEY)
    # Используем актуальную рабочую модель
    model = genai.GenerativeModel(
        model_name='gemini-2.5-flash',
        system_instruction="Ти — дружній та корисний ШІ-помічник. Відповідай українською мовою, чітко, грамотно та зрозуміло."
    )
else:
    model = None

# ==========================================
# 3. ОБРАБОТЧИКИ ТЕЛЕГРАМ
# ==========================================
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    welcome_text = (
        "Вітаю! Я твій цифровий помічник на базі Gemini ШІ.\n\n"
        "Задай мені будь-яке запитання, і я допоможу з відповіддю!"
    )
    await update.message.reply_text(welcome_text)

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_text = update.message.text
    
    if not model:
        await update.message.reply_text("Помилка: Змінна GEMINI_API_KEY не налаштована в Render.")
        return

    # Индикация "печатает..."
    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")

    try:
        response = model.generate_content(user_text)
        await update.message.reply_text(response.text)
    except Exception as e:
        await update.message.reply_text(f"Виникла помилка при зверненні до Gemini: {e}")

# ==========================================
# 4. ГОЛОВНА ТОЧКА ВХОДУ (MAIN)
# ==========================================
def main():
    # 1. Запуск веб-сервера в отдельном потоке для Render
    Thread(target=run_health_check_server, daemon=True).start()

    # 2. Получение токена Telegram
    bot_token = os.environ.get("BOT_TOKEN")
    if not bot_token:
        print("Критична помилка: BOT_TOKEN не знайдено в Environment Variables!")
        return

    # 3. Сборка приложения Telegram
    app = ApplicationBuilder().token(bot_token).build()

    # Регистрация команд
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    # 4. Запуск Long Polling с очисткой накопившихся конфликтов
    print("Бот успішно запущений!")
    app.run_polling(drop_pending_updates=True)

if __name__ == '__main__':
    main()
