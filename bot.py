import os
import asyncio
import logging
from aiohttp import web
import yt_dlp
from aiogram import Bot, Dispatcher
from aiogram.types import Message, Update, FSInputFile
from aiogram.filters import CommandStart

# ==== Настройки ====
TOKEN = os.getenv("TOKEN")
RENDER_EXTERNAL_URL = os.getenv("RENDER_EXTERNAL_URL")  # Render задаёт автоматически

if not TOKEN:
    raise ValueError("TOKEN не задан")
if not RENDER_EXTERNAL_URL:
    raise ValueError("RENDER_EXTERNAL_URL не задан")

WEBHOOK_PATH = f"/webhook/{TOKEN}"
WEBHOOK_URL = f"{RENDER_EXTERNAL_URL}{WEBHOOK_PATH}"

logging.basicConfig(level=logging.INFO)

DOWNLOAD_FOLDER = "downloads"
os.makedirs(DOWNLOAD_FOLDER, exist_ok=True)

bot = Bot(token=TOKEN)
dp = Dispatcher()


# ==== Команда /start ====
@dp.message(CommandStart())
async def start_handler(message: Message):
    await message.answer(
        "Привет! Пришли ссылку на YouTube или Instagram видео, и я скачаю его."
    )


# ==== Функция скачивания видео ====
def download_video(url: str) -> str:
    ydl_opts = {
        "outtmpl": os.path.join(DOWNLOAD_FOLDER, "%(title).80s.%(ext)s"),
        "format": "bestvideo+bestaudio/best",
        "merge_output_format": "mp4",
        "noplaylist": True,
        "quiet": True,
        # Для YouTube с авторизацией можно добавить cookiefile
        # "cookiefile": "cookies.txt",
    }
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=True)
        return ydl.prepare_filename(info)


# ==== Обработка всех сообщений ====
@dp.message()
async def handle_message(message: Message):
    if not message.text:
        return

    url = message.text.strip()

    if not url.startswith("http"):
        await message.answer("Отправь корректную ссылку на видео.")
        return

    await message.answer("Скачиваю видео, подожди...")

    try:
        loop = asyncio.get_running_loop()
        file_path = await loop.run_in_executor(None, download_video, url)

        if os.path.getsize(file_path) > 50 * 1024 * 1024:
            await message.answer("Видео слишком большое (>50MB). Telegram не позволит отправить.")
            os.remove(file_path)
            return

        video_file = FSInputFile(file_path)
        await message.answer_video(video_file)
        os.remove(file_path)

    except Exception as e:
        logging.error(e)
        await message.answer("Произошла ошибка при скачивании видео.")


# ==== Webhook startup/shutdown ====
async def on_startup(app):
    await bot.set_webhook(WEBHOOK_URL)
    logging.info(f"Webhook установлен: {WEBHOOK_URL}")


async def on_shutdown(app):
    await bot.delete_webhook()
    await bot.session.close()


# ==== Обработка POST-запросов от Telegram ====
async def handle_webhook(request):
    data = await request.json()
    update = Update.model_validate(data)
    await dp.feed_update(bot, update)
    return web.Response()


# ==== Запуск сервера ====
def main():
    app = web.Application()
    app.router.add_post(WEBHOOK_PATH, handle_webhook)
    app.on_startup.append(on_startup)
    app.on_shutdown.append(on_shutdown)

    port = int(os.getenv("PORT", 10000))
    web.run_app(app, host="0.0.0.0", port=port)


if __name__ == "__main__":
    main()
