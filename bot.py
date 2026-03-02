import os
import asyncio
import logging
import yt_dlp
from aiogram import Bot, Dispatcher, types
from aiogram.filters import CommandStart
from aiogram.types import Message

TOKEN = os.getenv("TOKEN")

if not TOKEN:
    raise ValueError("TOKEN не задан в переменных окружения")

logging.basicConfig(level=logging.INFO)

DOWNLOAD_FOLDER = "downloads"

if not os.path.exists(DOWNLOAD_FOLDER):
    os.makedirs(DOWNLOAD_FOLDER)

bot = Bot(token=TOKEN)
dp = Dispatcher()


@dp.message(CommandStart())
async def start_handler(message: Message):
    await message.answer("Привет! Пришли ссылку на YouTube или Instagram видео.")


def download_video(url: str) -> str:
    ydl_opts = {
        "outtmpl": os.path.join(DOWNLOAD_FOLDER, "%(title).80s.%(ext)s"),
        "format": "bestvideo+bestaudio/best",
        "merge_output_format": "mp4",
        "noplaylist": True,
        "quiet": True,
    }

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=True)
        return ydl.prepare_filename(info)


@dp.message()
async def handle_message(message: Message):
    url = message.text.strip()

    if not url.startswith("http"):
        await message.answer("Отправь корректную ссылку.")
        return

    await message.answer("Скачиваю видео...")

    try:
        loop = asyncio.get_running_loop()
        file_path = await loop.run_in_executor(None, download_video, url)

        if os.path.getsize(file_path) > 50 * 1024 * 1024:
            await message.answer("Видео больше 50MB. Telegram не позволяет отправить.")
            os.remove(file_path)
            return

        with open(file_path, "rb") as video:
            await message.answer_video(video)

        os.remove(file_path)

    except Exception as e:
        logging.error(str(e))
        await message.answer("Ошибка при скачивании видео.")


async def main():
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
