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
WEBHOOK_URL = os.environ.get("WEBHOOK_URL", "")
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
    confirm_object = State()
    uploading = State()

class AddPhoto(StatesGroup):
    waiting_object = State()
    confirm_object = State()
    uploading = State()

class Download(StatesGroup):
    waiting_object = State()

# ========== КОНСТАНТЫ ==========
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

# ========== КЛАВИАТУРЫ ==========
def main_kb():
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="/start"), KeyboardButton(text="/photo")],
            [KeyboardButton(text="/addphoto"), KeyboardButton(text="/download")]
        ],
        resize_keyboard=True
    )

def step_kb(has_files=False):
    if has_files:
        buttons = [[
            InlineKeyboardButton(text="💾 Сохранить", callback_data="save"),
            InlineKeyboardButton(text="❌ Отмена", callback_data="cancel")
        ]]
    else:
        buttons = [[
            InlineKeyboardButton(text="➡️ Пропустить", callback_data="skip"),
            InlineKeyboardButton(text="❌ Отмена", callback_data="cancel")
        ]]
    return InlineKeyboardMarkup(inline_keyboard=buttons)

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

# ========== КОМАНДЫ ==========
@router.message(Command("start"))
async def cmd_start(m: Message):
    await m.answer("🤖 Бот для фотофиксации по объектам.\nИспользуйте /photo или /addphoto внутри вашей темы.", reply_markup=main_kb())

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
    await m.answer("📝 Введите номер объекта:", reply_markup=main_kb())

# ========== ПРОВЕРКА ОБЪЕКТА ==========
@router.message(Upload.waiting_object)
async def check_upload_object(m: Message, state: FSMContext):
    obj = m.text.strip()
    ok, name = check_object_excel(obj)
    if ok:
        await state.update_data(object=obj, name=name, step=0, steps=[{"name": s, "files": []} for s in UPLOAD_STEPS])
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
        await state.update_data(object=obj, name=name, files=[])
        await state.set_state(AddPhoto.uploading)
        await m.answer(f"📸 Отправьте дополнительные файлы для объекта {obj}.", reply_markup=step_kb(has_files=True))
    else:
        await m.answer(f"❌ Объект {obj} не найден.")
        await state.clear()

# ========== ПРИЁМ ФАЙЛОВ ==========
@router.message(Upload.uploading, F.photo | F.video | F.document)
async def handle_upload(m: Message, state: FSMContext):
    data = await state.get_data()
    step_i = data["step"]
    steps = data["steps"]
    cur = steps[step_i]

    if m.photo:
        cur["files"].append({"type": "photo", "file_id": m.photo[-1].file_id})
    elif m.video:
        cur["files"].append({"type": "video", "file_id": m.video.file_id})
    elif m.document:
        cur["files"].append({"type": "document", "file_id": m.document.file_id})

    if len(cur["files"]) == 1:
        msg = await m.answer(reply_markup=step_kb(has_files=True))
        await state.update_data(last_msg=msg.message_id)

    await state.update_data(steps=steps)

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

# ========== CALLBACKS ==========
@router.callback_query(F.data == "save")
async def step_save(c: CallbackQuery, state: FSMContext):
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
async def step_skip(c: CallbackQuery, state: FSMContext):
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
async def step_cancel(c: CallbackQuery, state: FSMContext):
    await state.clear()
    try:
        await bot.delete_message(c.message.chat.id, c.message.message_id)
    except:
        pass
    await c.message.answer("❌ Загрузка отменена.", reply_markup=main_kb())
    await c.answer("Отменено")

# ========== ВСПОМОГАТЕЛЬНЫЕ ==========
async def send_step(m: Message, state: FSMContext):
    data = await state.get_data()
    step_i = data["step"]
    steps = data["steps"]
    if step_i >= len(steps):
        return
    step = steps[step_i]
    msg = await m.answer(f"📸 Отправьте {step['name']}", reply_markup=step_kb())
    await state.update_data(last_msg=msg.message_id)

async def post_archive(object_id, steps, author):
    try:
        header = f"💾 ОБЪЕКТ #{object_id}\n👤 Исполнитель: {author}\n🕒 {datetime.now().strftime('%d.%m.%Y %H:%M')}"
        await safe_call(bot.send_message(ARCHIVE_CHAT_ID, header))
        for step in steps:
            files = step["files"]
            if not files:
                continue
            await safe_call(bot.send_message(ARCHIVE_CHAT_ID, f"📁 {step['name']}"))
            media_batch = []
            for f in files:
                if f["type"] == "photo":
                    media_batch.append(InputMediaPhoto(media=f["file_id"]))
                elif f["type"] == "video":
                    media_batch.append(InputMediaVideo(media=f["file_id"]))
            if media_batch:
                await safe_call(bot.send_media_group(ARCHIVE_CHAT_ID, media_batch))
            docs = [f for f in files if f["type"] == "document"]
            for d in docs:
                await safe_call(bot.send_document(ARCHIVE_CHAT_ID, d["file_id"]))
    except Exception as e:
        print(f"[archive] {e}")

# ========== DOWNLOAD ==========
@router.message(Download.waiting_object)
async def download_files(m: Message, state: FSMContext):
    obj = m.text.strip()
    data = get_files(obj)
    if not data:
        await m.answer(f"❌ Файлы по объекту {obj} не найдены.")
        await state.clear()
        return
    await m.answer(f"📂 Найдено шагов: {len(data)}. Отправляю...")
    for step, files in data.items():
        await safe_call(bot.send_message(m.chat.id, f"📁 {step}"))
        media_batch = []
        for f in files:
            if f["type"] == "photo":
                media_batch.append(InputMediaPhoto(media=f["file_id"]))
            elif f["type"] == "video":
                media_batch.append(InputMediaVideo(media=f["file_id"]))
        if media_batch:
            await safe_call(bot.send_media_group(m.chat.id, media_batch))
        docs = [f for f in files if f["type"] == "document"]
        for d in docs:
            await safe_call(bot.send_document(m.chat.id, d["file_id"]))
    await m.answer(f"✅ Файлы по объекту {obj} отправлены.")
    await state.clear()

# ========== WEBHOOK ==========
async def on_startup():
    init_db()
    webhook_url = f"{WEBHOOK_URL}/{TOKEN}"
    await bot.delete_webhook(drop_pending_updates=True)
    await bot.set_webhook(webhook_url)
    await bot.set_my_commands([
        BotCommand(command="start", description="Перезапуск"),
        BotCommand(command="photo", description="Загрузить фото по объекту"),
        BotCommand(command="addphoto", description="Добавить фото"),
        BotCommand(command="download", description="Скачать файлы объекта"),
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
