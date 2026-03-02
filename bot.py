import os
import logging
import yt_dlp
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters, ContextTypes

# Токен берём из переменных окружения Render
TOKEN = os.getenv("TOKEN")

if not TOKEN:
    raise ValueError("Не задан TOKEN в переменных окружения")

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)

DOWNLOAD_FOLDER = "downloads"

if not os.path.exists(DOWNLOAD_FOLDER):
    os.makedirs(DOWNLOAD_FOLDER)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Привет! Отправь ссылку на видео с YouTube или Instagram."
    )


def download_video(url):
    ydl_opts = {
        "outtmpl": os.path.join(DOWNLOAD_FOLDER, "%(title).80s.%(ext)s"),
        "format": "mp4",
        "noplaylist": True,
        "quiet": True,
        "merge_output_format": "mp4",
    }

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=True)
        filename = ydl.prepare_filename(info)
        return filename


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    url = update.message.text.strip()

    if not url.startswith("http"):
        await update.message.reply_text("Пожалуйста, отправь корректную ссылку.")
        return

    await update.message.reply_text("Скачиваю видео...")

    try:
        file_path = download_video(url)

        # Telegram ограничивает размер файла 50MB для обычных ботов
        if os.path.getsize(file_path) > 50 * 1024 * 1024:
            await update.message.reply_text("Файл слишком большой (больше 50MB).")
            os.remove(file_path)
            return

        with open(file_path, "rb") as video:
            await update.message.reply_video(video=video)

        os.remove(file_path)

    except Exception as e:
        logging.error(str(e))
        await update.message.reply_text("Ошибка при скачивании видео.")


def main():
    app = ApplicationBuilder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    print("Бот запущен...")
    app.run_polling()


if __name__ == "__main__":
    main()
