import os
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes

TOKEN = os.getenv("TELEGRAM_TOKEN")

# Команда /start
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Привет! Бот работает!")

if __name__ == "__main__":
    # Создаём приложение
    app = ApplicationBuilder().token(TOKEN).build()

    # Добавляем команду /start
    app.add_handler(CommandHandler("start", start))

    # Запускаем бот
    app.run_polling()
