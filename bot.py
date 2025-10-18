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
    InputMediaPhoto, InputMediaVideo
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
WEBHOOK_URL = os.environ.get("WEBHOOK_URL", "https://example.com")
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

# ========== УЧЁТ СЕССИИ ==========
# завершение только по /photo (а не /addphoto)
PHOTO_COMPLETED = set()                # {object_id}
PHOTO_FILES_COUNT = {}                 # {object_id: int}

# ========== КЛАВИАТУРЫ ==========
def main_kb():
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="/photo"), KeyboardButton(text="/addphoto"), KeyboardButton(text="/info")]],
        resize_keyboard=True
    )

def step_kb(step_name, has_files=False, allow_skip=True):
    if has_files:
        buttons = [[
            InlineKeyboardButton(text="💾 Сохранить", callback_data="save"),
            InlineKeyboardButton(text="❌ Отмена", callback_data="cancel")
        ]]
    else:
        if step_name in MANDATORY_STEPS:
            buttons = [[InlineKeyboardButton(text="❌ Отмена", callback_data="cancel")]]
        else:
            if allow_skip:
                buttons = [[
                    InlineKeyboardButton(text="➡️ Пропустить", callback_data="skip"),
                    InlineKeyboardButton(text="❌ Отмена", callback_data="cancel")
                ]]
            else:
                buttons = [[InlineKeyboardButton(text="❌ Отмена", callback_data="cancel")]]
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def add_kb(has_files: bool):
    if has_files:
        return InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="💾 Сохранить", callback_data="add_save"),
            InlineKeyboardButton(text="❌ Отмена", callback_data="add_cancel")
        ]])
    else:
        return InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="❌ Отмена", callback_data="add_cancel")
        ]])

def confirm_kb(prefix: str):
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ Да", callback_data=f"{prefix}_confirm_yes"),
        InlineKeyboardButton(text="❌ Отмена", callback_data=f"{prefix}_confirm_no"),
    ]])

# ========== ХЕЛПЕРЫ ==========
def is_from_work_topic(msg: Message) -> bool:
    # Разрешаем запуск только из указанного рабочего чата (темы внутри него)
    return (msg.chat and msg.chat.id == WORK_CHAT_ID)

async def safe_call(coro, pause=0.2):
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

def combine_all_files(steps: list) -> list:
    allf = []
    for s in steps:
        allf.extend(s.get("files", []))
    return allf

# Унифицированные отправки в тему
async def send_in_topic_message(chat_id: int, thread_id: int | None, text: str, **kwargs):
    return await bot.send_message(chat_id=chat_id, text=text, message_thread_id=thread_id, **kwargs)

async def send_in_topic_media_group(chat_id: int, thread_id: int | None, media: list, **kwargs):
    return await bot.send_media_group(chat_id=chat_id, media=media, message_thread_id=thread_id, **kwargs)

# ========== КОМАНДЫ ==========
@router.message(Command("start"))
async def cmd_start(m: Message):
    await m.answer(
        "👋 Привет! Это бот для загрузки фото по объектам.\n\n"
        "📸 /photo — пошаговая загрузка\n"
        "➕ /addphoto — добавить фото к объекту\n"
        "ℹ️ /info — информация об объекте(ах)\n",
        reply_markup=main_kb()
    )

@router.message(Command("photo"))
async def cmd_photo(m: Message, state: FSMContext):
    if not is_from_work_topic(m):
        await m.answer("📍 Эта команда работает только в рабочей группе.", reply_markup=main_kb())
        return
    await state.set_state(Upload.waiting_object)
    # фиксируем чат и тему сразу
    await state.update_data(chat_id=m.chat.id, thread_id=m.message_thread_id)
    await m.answer("📝 Введите номер объекта:", reply_markup=main_kb())

@router.message(Command("addphoto"))
async def cmd_addphoto(m: Message, state: FSMContext):
    if not is_from_work_topic(m):
        await m.answer("📍 Эта команда работает только в рабочей группе.", reply_markup=main_kb())
        return
    await state.set_state(AddPhoto.waiting_object)
    await state.update_data(chat_id=m.chat.id, thread_id=m.message_thread_id)
    await m.answer("📝 Введите номер объекта:", reply_markup=main_kb())

@router.message(Command("download"))
async def cmd_download(m: Message, state: FSMContext):
    if not is_from_work_topic(m):
        await m.answer("📍 Эта команда работает только в рабочей группе.", reply_markup=main_kb())
        return
    await state.set_state(Download.waiting_object)
    await state.update_data(chat_id=m.chat.id, thread_id=m.message_thread_id)
    await m.answer("📝 Введите номер объекта для выгрузки из БД:", reply_markup=main_kb())

@router.message(Command("info"))
async def cmd_info(m: Message, state: FSMContext):
    if not is_from_work_topic(m):
        await m.answer("📍 Эта команда работает только в рабочей группе.", reply_markup=main_kb())
        return
    await state.set_state(Info.waiting_object)
    await state.update_data(chat_id=m.chat.id, thread_id=m.message_thread_id)
    await m.answer("📝 Введите номер(а) объекта: `7` или `1, 4, 6, 199, 19`", parse_mode="Markdown")

@router.message(Command("result"))
async def cmd_result(m: Message):
    # Показываем только те, что завершены ПОШАГОВОЙ /photo
    if not PHOTO_COMPLETED:
        await m.answer("📋 Нет завершённых объектов по шагам /photo в текущей сессии.", reply_markup=main_kb())
        return
    lines = ["✅ Завершённые объекты (/photo):"]
    for oid in sorted(PHOTO_COMPLETED, key=lambda x: (len(x), x)):
        cnt = PHOTO_FILES_COUNT.get(oid, 0)
        lines.append(f"• Объект {oid}: {cnt} файл(ов)")
    await m.answer("\n".join(lines), reply_markup=main_kb())

# ========== ПРОВЕРКА ОБЪЕКТА + ПОДТВЕРЖДЕНИЕ ==========
@router.message(Upload.waiting_object)
async def check_upload_object(m: Message, state: FSMContext):
    obj = m.text.strip()
    ok, name = check_object_excel(obj)
    if ok:
        await state.update_data(
            object=obj,
            object_name=name,
            step=0,
            steps=[{"name": s, "files": []} for s in UPLOAD_STEPS],
            media_buffers={},   # {group_id: {"files": [], "task": asyncio.Task}}
            last_msg=None
        )
        await state.set_state(Upload.confirming)
        await m.answer(f"Подтвердите объект:\n\n🆔 {obj}\n🏷️ {name}", reply_markup=confirm_kb("photo"))
    else:
        await m.answer(f"❌ Объект {obj} не найден.")
        await state.clear()

@router.message(AddPhoto.waiting_object)
async def check_add_object(m: Message, state: FSMContext):
    obj = m.text.strip()
    ok, name = check_object_excel(obj)
    if ok:
        await state.update_data(
            object=obj,
            object_name=name,
            files=[],
            media_buffers={},
            last_msg=None
        )
        await state.set_state(AddPhoto.confirming)
        await m.answer(f"Подтвердите объект (добавление файлов):\n\n🆔 {obj}\n🏷️ {name}", reply_markup=confirm_kb("add"))
    else:
        await m.answer(f"❌ Объект {obj} не найден.")
        await state.clear()

# ===== Подтверждение: /photo =====
@router.callback_query(F.data == "photo_confirm_yes")
async def photo_confirm_yes(c: CallbackQuery, state: FSMContext):
    await state.set_state(Upload.uploading)
    data = await state.get_data()
    chat_id = data.get("chat_id")
    thread_id = data.get("thread_id")
    step0 = data["steps"][0]["name"]

    # в ту же тему
    msg = await send_in_topic_message(chat_id, thread_id, f"📸 Отправьте {step0}", reply_markup=step_kb(step0, has_files=False))
    await state.update_data(last_msg=msg.message_id)
    await c.answer("Подтверждено")
    # удаляем исходное сообщение с инлайн-кнопками, чтобы не путать
    try:
        await c.message.delete()
    except:
        pass

@router.callback_query(F.data == "photo_confirm_no")
async def photo_confirm_no(c: CallbackQuery, state: FSMContext):
    await state.clear()
    try:
        await c.message.edit_text("❌ Операция отменена.")
    except:
        await c.message.answer("❌ Операция отменена.")
    await c.answer("Отмена")

# ===== Подтверждение: /addphoto =====
@router.callback_query(F.data == "add_confirm_yes")
async def add_confirm_yes(c: CallbackQuery, state: FSMContext):
    await state.set_state(AddPhoto.uploading)
    data = await state.get_data()
    chat_id = data.get("chat_id")
    thread_id = data.get("thread_id")
    text = "📸 Отправьте дополнительные файлы для объекта.\nКогда закончите — появится кнопка «Сохранить»."
    msg = await send_in_topic_message(chat_id, thread_id, text, reply_markup=add_kb(has_files=False))
    await state.update_data(last_msg=msg.message_id)
    await c.answer("Подтверждено")
    try:
        await c.message.delete()
    except:
        pass

@router.callback_query(F.data == "add_confirm_no")
async def add_confirm_no(c: CallbackQuery, state: FSMContext):
    await state.clear()
    try:
        await c.message.edit_text("❌ Операция отменена.")
    except:
        await c.message.answer("❌ Операция отменена.")
    await c.answer("Отмена")

# ===== Общая утилита буферизации альбомов =====
async def schedule_album_finalize(state: FSMContext, group_id: str, mode: str):
    await asyncio.sleep(1.4)  # ждём склейку медиагруппы
    data = await state.get_data()
    buffers = data.get("media_buffers", {})
    buf = buffers.get(group_id)
    if not buf:
        return

    files = buf.get("files", [])
    try:
        if buf.get("task"):
            buf["task"].cancel()
    except:
        pass
    buffers.pop(group_id, None)
    await state.update_data(media_buffers=buffers)

    chat_id = data.get("chat_id")
    thread_id = data.get("thread_id")

    # удалить старую панель
    last_msg = data.get("last_msg")
    if last_msg:
        try:
            await bot.delete_message(chat_id=chat_id, message_id=last_msg)
        except:
            pass

    if mode == "photo":
        step_i = data["step"]
        steps = data["steps"]
        steps[step_i]["files"].extend(files)
        await state.update_data(steps=steps)
        msg = await send_in_topic_message(chat_id, thread_id, "Выберите", reply_markup=step_kb(steps[step_i]["name"], has_files=True))
        await state.update_data(last_msg=msg.message_id)
    else:
        add_files = data.get("files", [])
        add_files.extend(files)
        await state.update_data(files=add_files)
        msg = await send_in_topic_message(chat_id, thread_id, "Готово к сохранению", reply_markup=add_kb(has_files=True))
        await state.update_data(last_msg=msg.message_id)

def capture_file_from_message(m: Message):
    if m.photo:
        return {"type": "photo", "file_id": m.photo[-1].file_id}
    if m.video:
        return {"type": "video", "file_id": m.video.file_id}
    if m.document:
        return {"type": "document", "file_id": m.document.file_id}
    return None

# ========== ПРИЁМ ФАЙЛОВ ==========
@router.message(Upload.uploading, F.photo | F.video | F.document)
async def handle_upload(m: Message, state: FSMContext):
    data = await state.get_data()
    # фиксируем на всякий случай
    if not data.get("chat_id"):
        await state.update_data(chat_id=m.chat.id)
    if not data.get("thread_id"):
        await state.update_data(thread_id=m.message_thread_id)

    file_info = capture_file_from_message(m)
    if not file_info:
        return

    if m.media_group_id:
        buffers = data.get("media_buffers", {})
        buf = buffers.get(m.media_group_id, {"files": [], "task": None})
        buf["files"].append(file_info)
        if buf["task"] is None:
            buf["task"] = asyncio.create_task(schedule_album_finalize(state, m.media_group_id, mode="photo"))
        buffers[m.media_group_id] = buf
        await state.update_data(media_buffers=buffers)
    else:
        step_i = data["step"]
        steps = data["steps"]
        steps[step_i]["files"].append(file_info)

        # убрать старую панель
        if data.get("last_msg"):
            try:
                await bot.delete_message(data["chat_id"], data["last_msg"])
            except:
                pass

        msg = await send_in_topic_message(data["chat_id"], data["thread_id"], "Выберите", reply_markup=step_kb(steps[step_i]["name"], has_files=True))
        await state.update_data(steps=steps, last_msg=msg.message_id)

@router.message(AddPhoto.uploading, F.photo | F.video | F.document)
async def handle_add(m: Message, state: FSMContext):
    data = await state.get_data()
    if not data.get("chat_id"):
        await state.update_data(chat_id=m.chat.id)
    if not data.get("thread_id"):
        await state.update_data(thread_id=m.message_thread_id)

    file_info = capture_file_from_message(m)
    if not file_info:
        return

    if m.media_group_id:
        buffers = data.get("media_buffers", {})
        buf = buffers.get(m.media_group_id, {"files": [], "task": None})
        buf["files"].append(file_info)
        if buf["task"] is None:
            buf["task"] = asyncio.create_task(schedule_album_finalize(state, m.media_group_id, mode="add"))
        buffers[m.media_group_id] = buf
        await state.update_data(media_buffers=buffers)
    else:
        files = data.get("files", [])
        files.append(file_info)
        # убрать предыдущую панель
        if data.get("last_msg"):
            try:
                await bot.delete_message(data["chat_id"], data["last_msg"])
            except:
                pass
        msg = await send_in_topic_message(data["chat_id"], data["thread_id"], "Готово к сохранению", reply_markup=add_kb(has_files=True))
        await state.update_data(files=files, last_msg=msg.message_id)

# ========== CALLBACKS ==========
@router.callback_query(F.data == "save")
async def step_save(c: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    chat_id = data.get("chat_id")
    thread_id = data.get("thread_id")
    step_i = data["step"] + 1
    steps = data["steps"]
    await state.update_data(step=step_i)

    if step_i < len(steps):
        next_name = steps[step_i]["name"]
        # заменяем панель в той же теме
        try:
            await bot.edit_message_text(
                chat_id=chat_id,
                message_id=data.get("last_msg"),
                text=f"📸 Отправьте {next_name}",
                reply_markup=step_kb(next_name, has_files=False)
            )
        except:
            msg = await send_in_topic_message(chat_id, thread_id, f"📸 Отправьте {next_name}", reply_markup=step_kb(next_name, has_files=False))
            await state.update_data(last_msg=msg.message_id)
    else:
        # завершение /photo — одна шапка + батчи по 10, документы отдельно
        obj = data["object"]
        obj_name = data.get("object_name") or ""
        author = c.from_user.full_name or c.from_user.username or str(c.from_user.id)
        all_files = combine_all_files(steps)

        # сохраняем по шагам в БД
        for s in steps:
            if s["files"]:
                save_files(obj, s["name"], s["files"], author)

        # Отправка в архив
        if all_files:
            await post_archive_single_group(obj, obj_name, all_files, author)

        # считаем объект выполненным ТОЛЬКО тут (по /photo)
        PHOTO_COMPLETED.add(obj)
        PHOTO_FILES_COUNT[obj] = PHOTO_FILES_COUNT.get(obj, 0) + len(all_files)

        # закрывающий ответ — в ту же тему
        try:
            await bot.edit_message_text(chat_id=chat_id, message_id=data.get("last_msg"),
                                        text=f"✅ Загрузка завершена для объекта {obj}.", reply_markup=None)
        except:
            await send_in_topic_message(chat_id, thread_id, f"✅ Загрузка завершена для объекта {obj}.")
        await state.clear()

    await c.answer("Сохранено ✅")

@router.callback_query(F.data == "skip")
async def step_skip(c: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    chat_id = data.get("chat_id")
    thread_id = data.get("thread_id")
    step_i = data["step"] + 1
    steps = data["steps"]
    await state.update_data(step=step_i)

    if step_i < len(steps):
        next_name = steps[step_i]["name"]
        try:
            await bot.edit_message_text(
                chat_id=chat_id,
                message_id=data.get("last_msg"),
                text=f"📸 Отправьте {next_name}",
                reply_markup=step_kb(next_name, has_files=False)
            )
        except:
            msg = await send_in_topic_message(chat_id, thread_id, f"📸 Отправьте {next_name}", reply_markup=step_kb(next_name, has_files=False))
            await state.update_data(last_msg=msg.message_id)
        await c.answer("Пропущено")
    else:
        try:
            await bot.edit_message_text(chat_id=chat_id, message_id=data.get("last_msg"),
                                        text="✅ Загрузка завершена.", reply_markup=None)
        except:
            await send_in_topic_message(chat_id, thread_id, "✅ Загрузка завершена.")
        await state.clear()
        await c.answer("Готово")

@router.callback_query(F.data == "cancel")
async def step_cancel(c: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    chat_id = data.get("chat_id")
    thread_id = data.get("thread_id")
    await state.clear()
    try:
        await bot.edit_message_text(chat_id=chat_id, message_id=data.get("last_msg"),
                                    text="❌ Загрузка отменена.", reply_markup=None)
    except:
        await send_in_topic_message(chat_id, thread_id, "❌ Загрузка отменена.")
    await c.answer("Отменено")

# ==== addphoto callbacks ====
@router.callback_query(F.data == "add_save")
async def add_save(c: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    chat_id = data.get("chat_id")
    thread_id = data.get("thread_id")
    obj = data["object"]
    obj_name = data.get("object_name") or ""
    files = data.get("files", [])
    author = c.from_user.full_name or c.from_user.username or str(c.from_user.id)

    if files:
        save_files(obj, "Дополнительные фотографии (addphoto)", files, author)
        # отправка в архив по тем же правилам
        await post_archive_single_group(obj, obj_name, files, author)

    # addphoto НЕ помечает объект завершённым
    try:
        await bot.edit_message_text(chat_id=chat_id, message_id=data.get("last_msg"),
                                    text=f"✅ Дополнительные файлы отправлены в архив для объекта {obj}.", reply_markup=None)
    except:
        await send_in_topic_message(chat_id, thread_id, f"✅ Дополнительные файлы отправлены в архив для объекта {obj}.")
    await state.clear()
    await c.answer("Сохранено ✅")

@router.callback_query(F.data == "add_cancel")
async def add_cancel(c: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    chat_id = data.get("chat_id")
    thread_id = data.get("thread_id")
    await state.clear()
    try:
        await bot.edit_message_text(chat_id=chat_id, message_id=data.get("last_msg"),
                                    text="❌ Добавление отменено.", reply_markup=None)
    except:
        await send_in_topic_message(chat_id, thread_id, "❌ Добавление отменено.")
    await c.answer("Отменено")

# ========== DOWNLOAD ==========
@router.message(Download.waiting_object)
async def download_files(m: Message, state: FSMContext):
    obj = m.text.strip()
    data_state = await state.get_data()
    chat_id = data_state.get("chat_id", m.chat.id)
    thread_id = data_state.get("thread_id", m.message_thread_id)

    data = get_files(obj)
    if not data:
        await send_in_topic_message(chat_id, thread_id, f"❌ Файлы по объекту {obj} не найдены.")
        await state.clear()
        return

    await send_in_topic_message(chat_id, thread_id, f"📂 Найдено шагов: {len(data)}. Отправляю...")
    for step, files in data.items():
        await safe_call(send_in_topic_message(chat_id, thread_id, f"📁 {step}"))
        media_batch = []
        docs = []
        for f in files:
            if f["type"] == "photo":
                media_batch.append(InputMediaPhoto(media=f["file_id"]))
            elif f["type"] == "video":
                media_batch.append(InputMediaVideo(media=f["file_id"]))
            elif f["type"] == "document":
                docs.append(f["file_id"])

        # фото/видео батчами по 10 — в ТЕМУ
        for i in range(0, len(media_batch), 10):
            await safe_call(send_in_topic_media_group(chat_id, thread_id, media_batch[i:i+10]))

        # документы — отдельно, тоже в ТЕМУ
        for d in docs:
            await safe_call(bot.send_document(chat_id=chat_id, document=d, message_thread_id=thread_id))

    await send_in_topic_message(chat_id, thread_id, f"✅ Файлы по объекту {obj} отправлены.")
    await state.clear()

# ========== INFO ==========
@router.message(Info.waiting_object)
async def info_object(m: Message, state: FSMContext):
    raw = m.text.strip()
    data_state = await state.get_data()
    chat_id = data_state.get("chat_id", m.chat.id)
    thread_id = data_state.get("thread_id", m.message_thread_id)

    parts = [p.strip() for p in raw.replace(";", ",").split(",") if p.strip()]
    if not parts:
        await send_in_topic_message(chat_id, thread_id, "❌ Не понял номера объекта. Пример: `7` или `1, 4, 6, 199, 19`", parse_mode="Markdown")
        await state.clear()
        return

    replies = []
    for obj in parts:
        info = get_object_info(obj)
        if not info:
            replies.append(f"❌ Объект {obj} не найден в objects.xlsx")
        else:
            replies.append(
                "📋 Информация об объекте {id}:\n\n"
                "🏢 Потребитель: {consumer}\n"
                "📍 Объект: {object}\n"
                "🗺 Адрес: {address}".format(**info)
            )
    await send_in_topic_message(chat_id, thread_id, "\n\n".join(replies))
    await state.clear()

# ========== ОТПРАВКА В АРХИВ ==========
async def post_archive_single_group(object_id: str, object_name: str, files: list, author: str):
    """Одна шапка на объект + медиагруппы по 10 (фото/видео). Документы отдельно. В архив отправляем без темы (или настройте отдельную при необходимости)."""
    try:
        header = (
            f"💾 ОБЪЕКТ #{object_id}\n"
            f"🏷️ {object_name or ''}\n"
            f"👤 Исполнитель: {author}\n"
            f"🕒 {datetime.now().strftime('%d.%m.%Y %H:%M')}\n"
            f"------------------------------------"
        )
        await safe_call(bot.send_message(ARCHIVE_CHAT_ID, header))

        media = []
        docs = []
        for f in files:
            if f["type"] == "photo":
                media.append(InputMediaPhoto(media=f["file_id"]))
            elif f["type"] == "video":
                media.append(InputMediaVideo(media=f["file_id"]))
            elif f["type"] == "document":
                docs.append(f["file_id"])

        for i in range(0, len(media), 10):
            await safe_call(bot.send_media_group(ARCHIVE_CHAT_ID, media[i:i+10]))

        for file_id in docs:
            await safe_call(bot.send_document(ARCHIVE_CHAT_ID, file_id))
    except Exception as e:
        print(f"[archive_single_group] {e}")

# ========== WEBHOOK ==========
async def on_startup():
    init_db()
    webhook_url = f"{WEBHOOK_URL}/{TOKEN}"
    await bot.delete_webhook(drop_pending_updates=True)
    await bot.set_webhook(webhook_url)
    print("✅ Webhook:", webhook_url)

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
