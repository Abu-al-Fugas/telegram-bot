import os
import asyncio
import logging
import yt_dlp
from aiohttp import web
from aiogram import Bot, Dispatcher
from aiogram.types import Message, Update
from aiogram.filters import CommandStart

TOKEN = os.getenv("TOKEN")
RENDER_EXTERNAL_URL = os.getenv("RENDER_EXTERNAL_URL")

if not TOKEN:
    raise ValueError("TOKEN не задан")

if not RENDER_EXTERNAL_URL:
    raise ValueError("RENDER_EXTERNAL_URL не задан (Render автоматически добавляет его для Web Service)")

WEBHOOK_PATH = f"/webhook/{TOKEN}"
WEBHOOK_URL = f"{RENDER_EXTERNAL_URL}{WEBHOOK_PATH}"

logging.basicConfig(level=logging.INFO)

DOWNLOAD_FOLDER = "downloads"
os.makedirs(DOWNLOAD_FOLDER, exist_ok=True)

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
    if not message.text:
        return

    url = message.text.strip()

    if not url.startswith("http"):
        await message.answer("Отправь корректную ссылку.")
        return

    await message.answer("Скачиваю видео...")

    try:
        loop = asyncio.get_running_loop()
        file_path = await loop.run_in_executor(None, download_video, url)

        if os.path.getsize(file_path) > 50 * 1024 * 1024:
            await message.answer("Видео больше 50MB.")
            os.remove(file_path)
            return

        with open(file_path, "rb") as video:
            await message.answer_video(video)

        os.remove(file_path)

    except Exception as e:
        logging.error(e)
        await message.answer("Ошибка при скачивании.")


async def on_startup(app):
    await bot.set_webhook(WEBHOOK_URL)
    logging.info(f"Webhook установлен: {WEBHOOK_URL}")


async def on_shutdown(app):
    await bot.delete_webhook()
    await bot.session.close()


async def handle_webhook(request):
    update = Update.model_validate(await request.json())
    await dp.feed_update(bot, update)
    return web.Response()


def main():
    app = web.Application()
    app.router.add_post(WEBHOOK_PATH, handle_webhook)

    app.on_startup.append(on_startup)
    app.on_shutdown.append(on_shutdown)

    port = int(os.getenv("PORT", 10000))
    web.run_app(app, host="0.0.0.0", port=port)


if __name__ == "__main__":
    main()
