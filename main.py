import os
from threading import Thread
from http.server import HTTPServer, BaseHTTPRequestHandler
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters, ContextTypes
import google.generativeai as genai

# --- 1. ВЕБ-СЕРВЕР ДЛЯ ЗАГЛУШКИ RENDER ---
class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"OK")

    def log_message(self, format, *args):
        return  # Отключаем лишние логи сервера

def run_health_check_server():
    port = int(os.environ.get("PORT", 10000))
    server = HTTPServer(('0.0.0.0', port), HealthCheckHandler)
    server.serve_forever()

# --- 2. НАСТРОЙКА GEMINI API ---
GEMINI_KEY = os.environ.get("GEMINI_API_KEY")
if GEMINI_KEY:
    genai.configure(api_key=GEMINI_KEY)
    model = genai.GenerativeModel('gemini-1.5-flash')
else:
    model = None

# --- 3. ОБРАБОТЧИКИ ТЕЛЕГРАМ ---
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Привет! Я бот на связи. Отправь мне текст, и я отвечу!")

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_text = update.message.text
    
    if not model:
        await update.message.reply_text("Ошибка: GEMINI_API_KEY не настроен в Environment Variables.")
        return

    # Отправляем статус "печатает..."
    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")

    try:
        response = model.generate_content(user_text)
        await update.message.reply_text(response.text)
    except Exception as e:
        await update.message.reply_text(f"Произошла ошибка при обращении к Gemini: {e}")

# --- 4. ТОЧКА ВХОДА ---
def main():
    # Запуск фонового HTTP-сервера
    Thread(target=run_health_check_server, daemon=True).start()

    bot_token = os.environ.get("BOT_TOKEN")
    if not bot_token:
        print("Критическая ошибка: BOT_TOKEN не найден!")
        return

    app = ApplicationBuilder().token(bot_token).build()

    # Регистрация команд и сообщений
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    # Запуск polling с авто-сбросом зависших конфликтов
    app.run_polling(drop_pending_updates=True)

if __name__ == '__main__':
    main()
