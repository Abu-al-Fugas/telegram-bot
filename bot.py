import os
import logging
from aiohttp import web
from aiogram import Bot, Dispatcher, types
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton, Message
from aiogram.filters import Command
from aiogram.client.session.aiohttp import AiohttpSession

# Включаем логирование
logging.basicConfig(level=logging.INFO)

# Используем переменную окружения TELEGRAM_BOT_TOKEN
TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
if not TOKEN:
    raise ValueError("Не задан токен бота в переменной TELEGRAM_BOT_TOKEN!")

# Инициализация бота и диспетчера
bot = Bot(token=TOKEN)
dp = Dispatcher()

# Простая клавиатура
def reply_keyboard() -> ReplyKeyboardMarkup:
    kb = ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="/start"), KeyboardButton(text="Помощь")]
        ],
        resize_keyboard=True
    )
    return kb

# Обработчик команды /start
@dp.message(Command(commands=["start"]))
async def cmd_start(message: Message):
    text = "Привет! Я твой бот. Используй кнопки ниже."
    await message.answer(text, reply_markup=reply_keyboard())

# Обработчик текста "Помощь"
@dp.message(lambda message: message.text.lower() == "помощь")
async def help_handler(message: Message):
    await message.answer("Вот что я умею:\n- /start — перезапуск бота\n- Помощь — эта подсказка")

# Вебхук через aiohttp
async def handle_webhook(request: web.Request):
    try:
        update = types.Update(**await request.json())
        await dp.feed_update(bot, update)
    except Exception as e:
        logging.exception(f"Ошибка при обработке вебхука: {e}")
    return web.Response(text="OK")

# Настройка веб-сервера
app = web.Application()
app.router.add_post(f"/{TOKEN}", handle_webhook)

# Запуск aiohttp сервера
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    logging.info(f"Запуск сервера на порту {port}")
    web.run_app(app, host="0.0.0.0", port=port)
