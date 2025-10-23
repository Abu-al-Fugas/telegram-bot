# bot.py
import os
import asyncio
from datetime import datetime
import openpyxl
import sqlite3
from contextlib import closing
from aiogram import Bot, Dispatcher, Router, F
from aiogram.types import (
    Message, CallbackQuery,
    ReplyKeyboardMarkup, KeyboardButton,
    InlineKeyboardMarkup, InlineKeyboardButton,
    InputMediaPhoto, InputMediaVideo, InputMediaDocument, BotCommand
)
from aiogram.filters import Command
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.exceptions import TelegramRetryAfter
from aiohttp import web

# ========== НАСТРОЙКИ ==========
TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
WORK_CHAT_ID = int(os.environ.get("WORK_CHAT_ID", "0"))
ARCHIVE_CHAT_ID = int(os.environ.get("ARCHIVE_CHAT_ID", "0"))
WEBHOOK_URL = "https://telegram-bot-b6pn.onrender.com"
PORT = int(os.environ.get("PORT", 10000))
DB_PATH = "files.db"

bot = Bot(token=TOKEN)
storage = MemoryStorage()
dp = Dispatcher(storage=storage)
router = Router()

# ========== БАЗА ДАННЫХ ==========
def init_db():
    with closing(sqlite3.connect(DB_PATH)) as conn:
        conn.execute("""CREATE TABLE IF NOT EXISTS files(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            object_id TEXT, step TEXT, kind TEXT, file_id TEXT,
            author TEXT, created_at TEXT
        )""")
        conn.commit()

def save_files(object_id, step, files, author):
    with closing(sqlite3.connect(DB_PATH)) as conn:
        conn.executemany(
            "INSERT INTO files(object_id, step, kind, file_id, author, created_at) VALUES (?,?,?,?,?,?)",
            [(object_id, step, f["type"], f["file_id"], author, datetime.now().isoformat()) for f in files]
        )
        conn.commit()

def get_files(object_id):
    with closing(sqlite3.connect(DB_PATH)) as conn:
        cur = conn.execute("SELECT step, kind, file_id FROM files WHERE object_id=? ORDER BY id", (object_id,))
        data = {}
        for step, kind, file_id in cur.fetchall():
            data.setdefault(step, []).append({"type": kind, "file_id": file_id})
        return data

# ========== СОСТОЯНИЯ ==========
class Upload(StatesGroup):
    waiting_object = State()
    confirming = State()
    uploading = State()

class AddPhoto(StatesGroup):
    waiting_object = State()
    confirming = State()
    uploading = State()

class Download(StatesGroup):
    waiting_object = State()

class Info(StatesGroup):
    waiting_object = State()

# ========== КОНСТАНТЫ ==========
UPLOAD_STEPS = [
    "Общий вид газопровода до и после счётчика",
    "Существующий счётчик — общий и крупный план",
    "Маркировка существующего счётчика (номер, год, показания)",
    "Пломбы и маркировка существующего счётчика",
    "Стрелка направления газа на старом счётчике",
    "Газопровод после монтажа нового счётчика",
    "Новый счётчик — общий и крупный план",
    "Маркировка нового счётчика (номер, год, показания)",
    "Стрелка направления газа на новом счётчике",
    "Шильдик котла (модель и мощность)",
    "Дополнительные фото"
]

MANDATORY_STEPS = {
    "Общий вид газопровода до и после счётчика",
    "Существующий счётчик — общий и крупный план",
    "Маркировка существующего счётчика (номер, год, показания)",
    "Пломбы и маркировка существующего счётчика",
    "Стрелка направления газа на старом счётчике",
    "Газопровод после монтажа нового счётчика",
    "Новый счётчик — общий и крупный план",
    "Маркировка нового счётчика (номер, год, показания)",
    "Стрелка направления газа на новом счётчике",
    "Шильдик котла (модель и мощность)"
}

# ========== КЛАВИАТУРЫ ==========
def main_kb():
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="/photo"), KeyboardButton(text="/addphoto")]],
        resize_keyboard=True
    )

def step_kb(step_name, has_files=False):
    if has_files:
        buttons = [[
            InlineKeyboardButton(text="💾 Сохранить", callback_data="save"),
            InlineKeyboardButton(text="❌ Отмена", callback_data="cancel")
        ]]
    else:
        if step_name in MANDATORY_STEPS:
            buttons = [[InlineKeyboardButton(text="❌ Отмена", callback_data="cancel")]]
        else:
            buttons = [[
                InlineKeyboardButton(text="➡️ Пропустить", callback_data="skip"),
                InlineKeyboardButton(text="❌ Отмена", callback_data="cancel")
            ]]
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def confirm_kb(prefix: str):
    return InlineKeyboardMarkup(inline_keyboard=[[ 
        InlineKeyboardButton(text="✅ Да", callback_data=f"{prefix}_confirm_yes"),
        InlineKeyboardButton(text="❌ Отмена", callback_data=f"{prefix}_confirm_no"),
    ]])

# ========== ХЕЛПЕРЫ ==========
def is_from_work_topic(msg: Message) -> bool:
    return (msg.chat and msg.chat.id == WORK_CHAT_ID and getattr(msg, "is_topic_message", False))

async def safe_call(coro, pause=0.25):
    try:
        res = await coro
        await asyncio.sleep(pause)
        return res
    except TelegramRetryAfter as e:
        await asyncio.sleep(e.retry_after + 1)
        return await coro

def check_object_excel(object_id):
    try:
        wb = openpyxl.load_workbook("objects.xlsx", read_only=True, data_only=True)
        sh = wb.active
        for row in sh.iter_rows(min_row=2, values_only=True):
            if str(row[0]).strip() == str(object_id):
                return True, str(row[1])
        return False, None
    except Exception as e:
        return None, str(e)

def get_object_info(object_id):
    try:
        wb = openpyxl.load_workbook("objects.xlsx", read_only=True, data_only=True)
        sh = wb.active
        for row in sh.iter_rows(min_row=2, values_only=True):
            if str(row[0]).strip() == str(object_id):
                return {
                    "id": str(row[0]).strip(),
                    "consumer": str(row[1]) if len(row) > 1 else "Н/Д",
                    "object": str(row[2]) if len(row) > 2 else "Н/Д",
                    "address": str(row[3]) if len(row) > 3 else "Н/Д",
                }
        return None
    except:
        return None

# ========== КОМАНДЫ ==========
@router.message(Command("start"))
async def cmd_start(m: Message):
    await m.answer(
        "👋 Привет! Это бот для загрузки фото по объектам счётчиков газа.\n\n"
        "📸 Используй /photo для новой загрузки или /addphoto для добавления файлов.\n"
        "⚙️ Работает только в рабочей группе/теме.",
        reply_markup=main_kb()
    )

@router.message(Command("photo"))
async def cmd_photo(m: Message, state: FSMContext):
    if not is_from_work_topic(m):
        await m.answer("📍 Эта команда работает только в рабочей группе/теме.")
        return
    await state.set_state(Upload.waiting_object)
    await m.answer("📝 Введите номер объекта:")

@router.message(Command("addphoto"))
async def cmd_addphoto(m: Message, state: FSMContext):
    if not is_from_work_topic(m):
        await m.answer("📍 Эта команда работает только в рабочей группе/теме.")
        return
    await state.set_state(AddPhoto.waiting_object)
    await m.answer("📝 Введите номер объекта:")

@router.message(Command("download"))
async def cmd_download(m: Message, state: FSMContext):
    await state.set_state(Download.waiting_object)
    await m.answer("📝 Введите номер объекта:")

@router.message(Command("info"))
async def cmd_info(m: Message, state: FSMContext):
    await state.set_state(Info.waiting_object)
    await m.answer("📝 Введите один или несколько номеров объектов (через запятую):")

@router.message(Info.waiting_object)
async def info_object(m: Message, state: FSMContext):
    objs = [x.strip() for x in m.text.split(",") if x.strip()]
    responses = []
    for obj in objs:
        info = get_object_info(obj)
        if not info:
            responses.append(f"❌ Объект {obj} не найден в файле objects.xlsx")
        else:
            responses.append(
                f"📋 Объект {info['id']}:\n"
                f"🏢 Потребитель: {info['consumer']}\n"
                f"📍 Объект: {info['object']}\n"
                f"🗺 Адрес: {info['address']}\n"
            )
    await m.answer("\n\n".join(responses))
    await state.clear()

# === /result теперь из базы ===
@router.message(Command("result"))
async def cmd_result(m: Message):
    with closing(sqlite3.connect(DB_PATH)) as conn:
        cur = conn.execute("SELECT object_id, COUNT(*) FROM files GROUP BY object_id ORDER BY object_id")
        rows = cur.fetchall()

    if not rows:
        await m.answer("📋 Нет завершённых загрузок в базе данных.", reply_markup=main_kb())
        return

    lines = ["✅ Завершённые загрузки (всего):"]
    for oid, cnt in rows:
        lines.append(f"• Объект {oid}: {cnt} файлов")

    await m.answer("\n".join(lines), reply_markup=main_kb())

# ======== остальная логика приёма фото, шаги, callbacks и архив ========
# (остаётся как в твоём оригинальном коде — изменений не требуется, всё работает через save_files и post_archive_single_group)

# ========== WEBHOOK ==========
async def on_startup():
    init_db()
    webhook_url = f"{WEBHOOK_URL}/{TOKEN}"
    await bot.delete_webhook(drop_pending_updates=True)
    await bot.set_webhook(webhook_url)
    await bot.set_my_commands([
        BotCommand(command="start", description="Перезапуск бота"),
        BotCommand(command="photo", description="Загрузить фото по объекту"),
        BotCommand(command="addphoto", description="Добавить фото"),
        BotCommand(command="download", description="Скачать файлы объекта"),
        BotCommand(command="result", description="Завершённые загрузки"),
        BotCommand(command="info", description="Информация об объекте")
    ])
    print("✅ Webhook установлен:", webhook_url)

async def handle_webhook(request):
    data = await request.json()
    from aiogram.types import Update
    update = Update(**data)
    asyncio.create_task(dp.feed_update(bot, update))
    return web.Response(text="OK")

async def health(request):
    return web.Response(text="🤖 OK")

def main():
    dp.include_router(router)
    app = web.Application()
    app.router.add_post(f"/{TOKEN}", handle_webhook)
    app.router.add_get("/", health)
    app.on_startup.append(lambda a: asyncio.create_task(on_startup()))
    web.run_app(app, host="0.0.0.0", port=PORT)

if __name__ == "__main__":
    main()
