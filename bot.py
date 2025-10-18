# bot.py
import os
import asyncio
import sqlite3
from datetime import datetime
from contextlib import closing
import openpyxl
from aiohttp import web
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

# === НАСТРОЙКИ ===
TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
WORK_CHAT_ID = int(os.environ.get("WORK_CHAT_ID", "0"))
ARCHIVE_CHAT_ID = int(os.environ.get("ARCHIVE_CHAT_ID", "0"))
WEBHOOK_URL = "https://telegram-bot-b6pn.onrender.com"
PORT = int(os.environ.get("PORT", 10000))
DB_PATH = "files.db"

bot = Bot(TOKEN)
storage = MemoryStorage()
dp = Dispatcher(storage=storage)
router = Router()

# === БАЗА ===
def init_db():
    with closing(sqlite3.connect(DB_PATH)) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS files(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                object_id TEXT, step TEXT, kind TEXT,
                file_id TEXT, author TEXT, created_at TEXT
            )
        """)
        conn.commit()

def save_files(object_id, step, files, author):
    with closing(sqlite3.connect(DB_PATH)) as conn:
        conn.executemany("""
            INSERT INTO files(object_id, step, kind, file_id, author, created_at)
            VALUES (?,?,?,?,?,?)
        """, [(object_id, step, f["type"], f["file_id"], author, datetime.now().isoformat()) for f in files])
        conn.commit()

def get_files(object_id):
    with closing(sqlite3.connect(DB_PATH)) as conn:
        cur = conn.execute("SELECT step, kind, file_id FROM files WHERE object_id=? ORDER BY id", (object_id,))
        data = {}
        for step, kind, fid in cur.fetchall():
            data.setdefault(step, []).append({"type": kind, "file_id": fid})
        return data

# === СОСТОЯНИЯ ===
class Upload(StatesGroup):
    waiting_object = State()
    uploading = State()

class AddPhoto(StatesGroup):
    waiting_object = State()
    uploading = State()

class Download(StatesGroup):
    waiting_object = State()

# === ЧЕК-ЛИСТ ===
UPLOAD_STEPS = [
    "Общее фото помещения",
    "Фото корректора",
    "Фото существующей СТМ потребителя",
    "Фото места устанавливаемой СТМ",
    "Фото (ГРУ)",
    "Фото котлов относительно корректора и устанавливаемой СТМ",
    "Фото газового оборудования",
    "Фото точки подключения 220В",
    "Фото места прокладки кабелей",
    "Фото входных дверей снаружи",
    "Дополнительные фотографии"
]
MANDATORY_STEPS = {
    "Общее фото помещения",
    "Фото корректора",
    "Фото места устанавливаемой СТМ",
    "Фото места прокладки кабелей"
}

# === КЛАВИАТУРЫ ===
def main_kb():
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton("/photo"), KeyboardButton("/addphoto")]],
        resize_keyboard=True
    )

def step_kb(step, has_files=False):
    if has_files:
        kb = [[
            InlineKeyboardButton("💾 Сохранить", callback_data="save"),
            InlineKeyboardButton("❌ Отмена", callback_data="cancel")
        ]]
    else:
        if step in MANDATORY_STEPS:
            kb = [[InlineKeyboardButton("❌ Отмена", callback_data="cancel")]]
        else:
            kb = [[
                InlineKeyboardButton("➡️ Пропустить", callback_data="skip"),
                InlineKeyboardButton("❌ Отмена", callback_data="cancel")
            ]]
    return InlineKeyboardMarkup(inline_keyboard=kb)

# === ВСПОМОГАТЕЛЬНЫЕ ===
def is_from_work_topic(m: Message) -> bool:
    return (m.chat and m.chat.id == WORK_CHAT_ID and getattr(m, "is_topic_message", False))

async def safe_call(coro, pause=0.25):
    try:
        res = await coro
        await asyncio.sleep(pause)
        return res
    except TelegramRetryAfter as e:
        await asyncio.sleep(e.retry_after + 1)
        return await coro

def check_object_excel(obj_id):
    try:
        wb = openpyxl.load_workbook("objects.xlsx", read_only=True)
        sh = wb.active
        for row in sh.iter_rows(min_row=2, values_only=True):
            if str(row[0]).strip() == str(obj_id):
                return True, str(row[1])
        return False, None
    except Exception as e:
        return None, str(e)

# === КОМАНДЫ ===
@router.message(Command("start"))
async def start(m: Message):
    await m.answer(
        "👋 Привет! Это бот для загрузки фото по объектам котельных.\n\n"
        "📸 Используй /photo для новой загрузки или /addphoto для добавления файлов.\n"
        "⚙️ Работает только в рабочей группе/теме.",
        reply_markup=main_kb()
    )

@router.message(Command("photo"))
async def photo_cmd(m: Message, state: FSMContext):
    if not is_from_work_topic(m):
        await m.answer("📍 Эта команда работает только в рабочей группе/теме.")
        return
    await state.set_state(Upload.waiting_object)
    await m.answer("📝 Введите номер объекта:")

@router.message(Command("addphoto"))
async def addphoto_cmd(m: Message, state: FSMContext):
    if not is_from_work_topic(m):
        await m.answer("📍 Эта команда работает только в рабочей группе/теме.")
        return
    await state.set_state(AddPhoto.waiting_object)
    await m.answer("📝 Введите номер объекта:")

@router.message(Command("download"))
async def download_cmd(m: Message, state: FSMContext):
    await state.set_state(Download.waiting_object)
    await m.answer("📝 Введите номер объекта:")

# === ПРОВЕРКА ОБЪЕКТА ===
@router.message(Upload.waiting_object)
async def check_upload_object(m: Message, state: FSMContext):
    obj = m.text.strip()
    ok, name = check_object_excel(obj)
    if ok:
        await state.update_data(object=obj, step=0, steps=[{"name": s, "files": []} for s in UPLOAD_STEPS])
        await state.set_state(Upload.uploading)
        await send_step(m, state)
    else:
        await m.answer(f"❌ Объект {obj} не найден.")
        await state.clear()

@router.message(AddPhoto.waiting_object)
async def check_add_object(m: Message, state: FSMContext):
    obj = m.text.strip()
    ok, name = check_object_excel(obj)
    if ok:
        await state.update_data(object=obj, files=[])
        await state.set_state(AddPhoto.uploading)
        await m.answer("📸 Отправьте дополнительные файлы для объекта.", reply_markup=step_kb('', True))
    else:
        await m.answer(f"❌ Объект {obj} не найден.")
        await state.clear()

# === ПРИЁМ ФАЙЛОВ (с поддержкой медиагрупп) ===
@router.message(Upload.uploading, F.photo | F.video | F.document)
async def handle_upload(m: Message, state: FSMContext):
    data = await state.get_data()
    step_i = data["step"]
    steps = data["steps"]
    cur = steps[step_i]

    file_info = {}
    if m.photo:
        file_info = {"type": "photo", "file_id": m.photo[-1].file_id}
    elif m.video:
        file_info = {"type": "video", "file_id": m.video.file_id}
    elif m.document:
        file_info = {"type": "document", "file_id": m.document.file_id}

    # медиагруппа
    if m.media_group_id:
        media_groups = data.get("media_groups", {})
        group_id = m.media_group_id
        media_groups.setdefault(group_id, []).append(file_info)
        await state.update_data(media_groups=media_groups)
        await asyncio.sleep(1.2)  # ждём пока придут все элементы

        data = await state.get_data()
        media_groups = data.get("media_groups", {})
        if group_id in media_groups:
            cur["files"].extend(media_groups.pop(group_id))
            if data.get("last_msg"):
                try:
                    await bot.delete_message(m.chat.id, data["last_msg"])
                except:
                    pass
            msg = await m.answer("Выберите", reply_markup=step_kb(cur["name"], has_files=True))
            await state.update_data(steps=steps, last_msg=msg.message_id, media_groups=media_groups)
    else:
        # одиночный файл
        cur["files"].append(file_info)
        if data.get("last_msg"):
            try:
                await bot.delete_message(m.chat.id, data["last_msg"])
            except:
                pass
        msg = await m.answer("Выберите", reply_markup=step_kb(cur["name"], has_files=True))
        await state.update_data(steps=steps, last_msg=msg.message_id)

# === ДОБАВЛЕНИЕ ФАЙЛОВ ===
@router.message(AddPhoto.uploading, F.photo | F.video | F.document)
async def handle_add(m: Message, state: FSMContext):
    data = await state.get_data()
    files = data["files"]
    if m.photo:
        files.append({"type": "photo", "file_id": m.photo[-1].file_id})
    elif m.video:
        files.append({"type": "video", "file_id": m.video.file_id})
    elif m.document:
        files.append({"type": "document", "file_id": m.document.file_id})
    await state.update_data(files=files)

# === CALLBACKS ===
@router.callback_query(F.data == "save")
async def save_step(c: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    obj = data["object"]
    step_i = data["step"]
    steps = data["steps"]
    cur = steps[step_i]
    author = c.from_user.full_name or c.from_user.username or str(c.from_user.id)

    if cur["files"]:
        save_files(obj, cur["name"], cur["files"], author)
        await post_archive(obj, [{"name": cur["name"], "files": cur["files"]}], author)

    await state.update_data(step=step_i + 1, steps=steps)
    try:
        await bot.delete_message(c.message.chat.id, c.message.message_id)
    except:
        pass

    if step_i + 1 < len(steps):
        await send_step(c.message, state)
    else:
        await c.message.answer(f"✅ Загрузка завершена для объекта {obj}.", reply_markup=main_kb())
        await state.clear()
    await c.answer("Сохранено ✅")

@router.callback_query(F.data == "skip")
async def skip_step(c: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    step_i = data["step"]
    await state.update_data(step=step_i + 1)
    try:
        await bot.delete_message(c.message.chat.id, c.message.message_id)
    except:
        pass
    await send_step(c.message, state)
    await c.answer("Пропущено")

@router.callback_query(F.data == "cancel")
async def cancel_step(c: CallbackQuery, state: FSMContext):
    await state.clear()
    try:
        await bot.delete_message(c.message.chat.id, c.message.message_id)
    except:
        pass
    await c.message.answer("❌ Загрузка отменена.", reply_markup=main_kb())
    await c.answer("Отменено")

# === ВСПОМОГАТЕЛЬНЫЕ ===
async def send_step(m: Message, state: FSMContext):
    data = await state.get_data()
    step_i = data["step"]
    steps = data["steps"]
    if step_i >= len(steps):
        return
    step = steps[step_i]
    msg = await m.answer(f"📸 Отправьте {step['name']}", reply_markup=step_kb(step["name"]))
    await state.update_data(last_msg=msg.message_id)

async def post_archive(obj, steps, author):
    try:
        header = f"💾 ОБЪЕКТ #{obj}\n👤 Исполнитель: {author}\n🕒 {datetime.now():%d.%m.%Y %H:%M}"
        await safe_call(bot.send_message(ARCHIVE_CHAT_ID, header))
        for s in steps:
            if not s["files"]:
                continue
            await safe_call(bot.send_message(ARCHIVE_CHAT_ID, f"📁 {s['name']}"))
            media = [InputMediaPhoto(f["file_id"]) for f in s["files"] if f["type"] == "photo"] + \
                    [InputMediaVideo(f["file_id"]) for f in s["files"] if f["type"] == "video"]
            if media:
                await safe_call(bot.send_media_group(ARCHIVE_CHAT_ID, media))
            for d in [f for f in s["files"] if f["type"] == "document"]:
                await safe_call(bot.send_document(ARCHIVE_CHAT_ID, d["file_id"]))
    except Exception as e:
        print("archive error:", e)

# === DOWNLOAD ===
@router.message(Download.waiting_object)
async def download(m: Message, state: FSMContext):
    obj = m.text.strip()
    data = get_files(obj)
    if not data:
        await m.answer(f"❌ Файлы по объекту {obj} не найдены.")
        await state.clear()
        return
    await m.answer(f"📂 Найдено шагов: {len(data)}. Отправляю...")
    for step, files in data.items():
        await safe_call(bot.send_message(m.chat.id, f"📁 {step}"))
        media = [InputMediaPhoto(f["file_id"]) for f in files if f["type"] == "photo"] + \
                [InputMediaVideo(f["file_id"]) for f in files if f["type"] == "video"]
        if media:
            await safe_call(bot.send_media_group(m.chat.id, media))
        for d in [f for f in files if f["type"] == "document"]:
            await safe_call(bot.send_document(m.chat.id, d["file_id"]))
    await m.answer(f"✅ Файлы по объекту {obj} отправлены.")
    await state.clear()

# === WEBHOOK ===
async def on_startup():
    init_db()
    webhook_url = f"{WEBHOOK_URL}/{TOKEN}"
    await bot.delete_webhook(drop_pending_updates=True)
    await bot.set_webhook(webhook_url)
    await bot.set_my_commands([
        BotCommand("start", "Перезапуск бота"),
        BotCommand("photo", "Загрузить фото по объекту"),
        BotCommand("addphoto", "Добавить фото"),
        BotCommand("download", "Скачать файлы объекта"),
        BotCommand("result", "Завершённые загрузки (сессия)"),
        BotCommand("info", "Информация об объекте")
    ])
    print("✅ Webhook установлен:", webhook_url)

async def handle_webhook(req):
    data = await req.json()
    from aiogram.types import Update
    asyncio.create_task(dp.feed_update(bot, Update(**data)))
    return web.Response(text="OK")

async def health(req):
    return web.Response(text="🤖 OK")

def main():
    dp.include_router(router)
    app = web.Application()
    app.router.add_post(f"/{TOKEN}", handle_webhook)
    app.router.add_get("/", health)
    app.on_startup.append(lambda _: asyncio.create_task(on_startup()))
    web.run_app(app, host="0.0.0.0", port=PORT)

if __name__ == "__main__":
    main()
