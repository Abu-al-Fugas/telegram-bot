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
    InputMediaPhoto, InputMediaVideo, BotCommand
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
        cur = conn.execute("SELECT kind, file_id FROM files WHERE object_id=? ORDER BY id", (object_id,))
        return [{"type": k, "file_id": fid} for k, fid in cur.fetchall()]

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

SESSION_COUNTER = {}

# ========== КЛАВИАТУРЫ ==========
def main_kb():
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="/photo"), KeyboardButton(text="/addphoto"), KeyboardButton(text="/info")]],
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
        "👋 Привет! Это бот для загрузки фото по объектам котельных.\n\n"
        "📸 /photo — пошаговая загрузка\n"
        "➕ /addphoto — добавление файлов\n"
        "ℹ️ /info — информация об объекте",
        reply_markup=main_kb()
    )

@router.message(Command("photo"))
async def cmd_photo(m: Message, state: FSMContext):
    if not is_from_work_topic(m):
        await m.answer("📍 Эта команда работает только в рабочей теме.")
        return
    await state.set_state(Upload.waiting_object)
    await m.answer("📝 Введите номер объекта:")

@router.message(Command("addphoto"))
async def cmd_addphoto(m: Message, state: FSMContext):
    if not is_from_work_topic(m):
        await m.answer("📍 Эта команда работает только в рабочей теме.")
        return
    await state.set_state(AddPhoto.waiting_object)
    await m.answer("📝 Введите номер объекта:")

@router.message(Command("info"))
async def cmd_info(m: Message, state: FSMContext):
    await state.set_state(Info.waiting_object)
    await m.answer("📝 Введите один или несколько номеров объектов (через запятую):")

# ========== ПОДТВЕРЖДЕНИЕ ОБЪЕКТА ==========
@router.message(Upload.waiting_object)
async def check_upload_object(m: Message, state: FSMContext):
    obj = m.text.strip()
    ok, name = check_object_excel(obj)
    if ok:
        await state.update_data(object=obj, object_name=name, step=0, steps=[{"name": s, "files": []} for s in UPLOAD_STEPS])
        await state.set_state(Upload.confirming)
        await m.answer(f"Подтвердите объект:\n\n🆔 {obj}\n🏷️ {name}", reply_markup=confirm_kb("photo"))
    else:
        await m.answer(f"❌ Объект {obj} не найден.")
        await state.clear()

@router.callback_query(F.data == "photo_confirm_yes")
async def photo_confirm_yes(c: CallbackQuery, state: FSMContext):
    await state.set_state(Upload.uploading)
    data = await state.get_data()
    step0 = data["steps"][0]["name"]
    try:
        await c.message.edit_text(f"📸 Отправьте {step0}", reply_markup=step_kb(step0))
        await state.update_data(last_msg=c.message.message_id)
    except:
        msg = await c.message.answer(f"📸 Отправьте {step0}", reply_markup=step_kb(step0))
        await state.update_data(last_msg=msg.message_id)
    await c.answer("Подтверждено")

@router.callback_query(F.data == "photo_confirm_no")
async def photo_confirm_no(c: CallbackQuery, state: FSMContext):
    await state.clear()
    await c.message.edit_text("❌ Операция отменена.")
    await c.answer("Отмена")

# ====== ADDPHOTO ======
@router.message(AddPhoto.waiting_object)
async def check_add_object(m: Message, state: FSMContext):
    obj = m.text.strip()
    ok, name = check_object_excel(obj)
    if ok:
        await state.update_data(object=obj, object_name=name, files=[])
        await state.set_state(AddPhoto.uploading)
        await m.answer("📸 Отправьте файлы для добавления.", reply_markup=step_kb('', False))
    else:
        await m.answer(f"❌ Объект {obj} не найден.")
        await state.clear()

# ========== ОБРАБОТКА ФАЙЛОВ ==========
@router.message(Upload.uploading, F.photo | F.video | F.document)
async def handle_upload(m: Message, state: FSMContext):
    data = await state.get_data()
    step_i = data["step"]
    steps = data["steps"]
    cur = steps[step_i]

    # определить тип файла
    file_info = {}
    if m.photo:
        file_info = {"type": "photo", "file_id": m.photo[-1].file_id}
    elif m.video:
        file_info = {"type": "video", "file_id": m.video.file_id}
    elif m.document:
        file_info = {"type": "document", "file_id": m.document.file_id}

    cur["files"].append(file_info)

    msg = await m.answer("Выберите", reply_markup=step_kb(cur["name"], has_files=True))
    await state.update_data(steps=steps, last_msg=msg.message_id)

@router.message(AddPhoto.uploading, F.photo | F.video | F.document)
async def handle_addphoto(m: Message, state: FSMContext):
    data = await state.get_data()
    files = data.get("files", [])
    if m.photo:
        files.append({"type": "photo", "file_id": m.photo[-1].file_id})
    elif m.video:
        files.append({"type": "video", "file_id": m.video.file_id})
    elif m.document:
        files.append({"type": "document", "file_id": m.document.file_id})

    msg = await m.answer("Выберите", reply_markup=step_kb('', has_files=True))
    await state.update_data(files=files, last_msg=msg.message_id)

# ========== СОХРАНЕНИЕ ==========
@router.callback_query(F.data == "save")
async def step_save(c: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    obj = data["object"]
    obj_name = data.get("object_name") or ""
    author = c.from_user.full_name or c.from_user.username or str(c.from_user.id)

    # --- если это AddPhoto ---
    if data.get("files") is not None and "steps" not in data:
        files = data["files"]
        if files:
            for f in files:
                save_files(obj, "Дополнительно", [f], author)
            await post_archive_full_object(obj, obj_name, files, author)
        await state.clear()
        await c.message.edit_text(f"✅ Файлы добавлены для объекта {obj}.", reply_markup=None)
        await c.answer("Сохранено ✅")
        return

    # --- обычный Upload (photo) ---
    step_i = data["step"]
    steps = data["steps"]
    cur = steps[step_i]

    if cur["files"]:
        save_files(obj, cur["name"], cur["files"], author)
        SESSION_COUNTER[obj] = SESSION_COUNTER.get(obj, 0) + len(cur["files"])
        archive_files = data.get("archive_files", [])
        archive_files.extend(cur["files"])
        await state.update_data(archive_files=archive_files)

    step_i += 1
    await state.update_data(step=step_i, steps=steps)
    if step_i < len(steps):
        next_name = steps[step_i]["name"]
        await c.message.edit_text(f"📸 Отправьте {next_name}", reply_markup=step_kb(next_name))
        await state.update_data(last_msg=c.message.message_id)
    else:
        archive_files = data.get("archive_files", [])
        if archive_files:
            await post_archive_full_object(obj, obj_name, archive_files, author)
        await c.message.edit_text(f"✅ Загрузка завершена для объекта {obj}.", reply_markup=None)
        await state.clear()
    await c.answer("Сохранено ✅")

@router.callback_query(F.data == "cancel")
async def cancel_action(c: CallbackQuery, state: FSMContext):
    await state.clear()
    await c.message.edit_text("❌ Операция отменена.", reply_markup=None)
    await c.answer("Отменено")

# ========== АРХИВ ==========
async def post_archive_full_object(object_id: str, object_name: str, all_files: list, author: str):
    """Одна шапка + медиагруппы по 10 файлов"""
    try:
        header = (
            f"💾 ОБЪЕКТ #{object_id}\n"
            f"🏷️ {object_name}\n"
            f"🕒 {datetime.now().strftime('%d.%m.%Y %H:%M')}\n"
            f"------------------------------------"
        )
        await safe_call(bot.send_message(ARCHIVE_CHAT_ID, header))

        # формируем группы по 10
        batch = []
        for f in all_files:
            if f["type"] == "photo":
                batch.append(InputMediaPhoto(media=f["file_id"]))
            elif f["type"] == "video":
                batch.append(InputMediaVideo(media=f["file_id"]))

            if len(batch) == 10:
                await safe_call(bot.send_media_group(ARCHIVE_CHAT_ID, batch))
                batch = []

        if batch:
            await safe_call(bot.send_media_group(ARCHIVE_CHAT_ID, batch))

    except Exception as e:
        print(f"[archive_full_object] {e}")

# ========== INFO ==========
@router.message(Info.waiting_object)
async def info_object(m: Message, state: FSMContext):
    text = m.text.strip()
    ids = [x.strip() for x in text.split(",") if x.strip()]
    if not ids:
        await m.answer("❌ Введите хотя бы один номер объекта.")
        await state.clear()
        return
    for obj_id in ids:
        info = get_object_info(obj_id)
        if info:
            msg = (
                f"📋 Информация об объекте {info['id']}:\n"
                f"🏢 Потребитель: {info['consumer']}\n"
                f"📍 Объект: {info['object']}\n"
                f"🗺 Адрес: {info['address']}\n"
                f"------------------------------------"
            )
        else:
            msg = f"❌ Объект {obj_id} не найден."
        await m.answer(msg)
    await state.clear()

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
        BotCommand(command="info", description="Информация об объекте"),
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
