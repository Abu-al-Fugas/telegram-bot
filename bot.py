import os
import asyncio
import sqlite3
from contextlib import closing
from datetime import datetime

from aiogram import Bot, Dispatcher, Router, F
from aiogram.types import (
    Message,
    CallbackQuery,
    ReplyKeyboardMarkup,
    KeyboardButton,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    InputMediaPhoto,
    InputMediaVideo,
    BotCommand
)
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiohttp import web
import openpyxl

# ========== НАСТРОЙКИ ==========
TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
if not TOKEN:
    raise RuntimeError("TELEGRAM_BOT_TOKEN is not set")

ARCHIVE_CHAT_ID = int(os.environ.get("ARCHIVE_CHAT_ID", "-1003160855229"))
WEBHOOK_URL = os.environ.get("WEBHOOK_URL", "https://telegram-bot-b6pn.onrender.com")
PORT = int(os.environ.get("PORT", 10000))
DB_PATH = os.environ.get("DB_PATH", "files.db")

# ========== ИНИЦИАЛИЗАЦИЯ БОТА ==========
bot = Bot(token=TOKEN)
storage = MemoryStorage()
dp = Dispatcher(storage=storage)
router = Router()

# ========== КОНСТАНТЫ ЧЕК-ЛИСТА ==========
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

# ========== СОСТОЯНИЯ FSM ==========
class UploadStates(StatesGroup):
    waiting_object_id = State()
    uploading_steps = State()

class AddPhotoStates(StatesGroup):
    waiting_object_id = State()
    uploading_files = State()

class DownloadStates(StatesGroup):
    waiting_object_id = State()

class InfoStates(StatesGroup):
    waiting_object_id = State()

# ========== ПАМЯТЬ СЕССИИ (для /result) ==========
objects_data = {}

# ========== БД ==========
def init_db():
    with closing(sqlite3.connect(DB_PATH)) as conn:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS objects (
                object_id TEXT PRIMARY KEY,
                created_at TEXT NOT NULL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS files (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                object_id TEXT NOT NULL,
                step TEXT NOT NULL,
                kind TEXT NOT NULL CHECK (kind IN ('photo','video','document')),
                file_id TEXT NOT NULL,
                author_id INTEGER,
                created_at TEXT NOT NULL,
                FOREIGN KEY (object_id) REFERENCES objects(object_id) ON DELETE CASCADE
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS topics (
                user_id INTEGER PRIMARY KEY,
                chat_id INTEGER NOT NULL,
                thread_id INTEGER NOT NULL,
                linked_at TEXT NOT NULL
            )
        """)
        conn.commit()

def ensure_object(conn, object_id: str):
    conn.execute(
        "INSERT OR IGNORE INTO objects(object_id, created_at) VALUES (?, ?)",
        (object_id, datetime.now().isoformat())
    )

def save_files_to_db(object_id: str, step_name: str, files: list[dict], author_id: int | None):
    if not files:
        return
    with closing(sqlite3.connect(DB_PATH)) as conn:
        ensure_object(conn, object_id)
        conn.executemany(
            "INSERT INTO files(object_id, step, kind, file_id, author_id, created_at) VALUES (?,?,?,?,?,?)",
            [
                (object_id, step_name, f["type"], f["file_id"], author_id, datetime.now().isoformat())
                for f in files
            ]
        )
        conn.commit()

def read_files_from_db(object_id: str):
    with closing(sqlite3.connect(DB_PATH)) as conn:
        cur = conn.execute(
            "SELECT step, kind, file_id FROM files WHERE object_id = ? ORDER BY id ASC",
            (object_id,)
        )
        rows = cur.fetchall()
    by_step = {}
    for step, kind, file_id in rows:
        by_step.setdefault(step, []).append({"type": kind, "file_id": file_id})
    return by_step

def has_object_in_db(object_id: str) -> bool:
    with closing(sqlite3.connect(DB_PATH)) as conn:
        cur = conn.execute("SELECT 1 FROM objects WHERE object_id = ? LIMIT 1", (object_id,))
        return cur.fetchone() is not None

def set_topic_for_user(user_id: int, chat_id: int, thread_id: int):
    with closing(sqlite3.connect(DB_PATH)) as conn:
        conn.execute(
            "INSERT INTO topics(user_id, chat_id, thread_id, linked_at) VALUES (?,?,?,?) "
            "ON CONFLICT(user_id) DO UPDATE SET chat_id=excluded.chat_id, thread_id=excluded.thread_id, linked_at=excluded.linked_at",
            (user_id, chat_id, thread_id, datetime.now().isoformat())
        )
        conn.commit()

def get_topic_for_user(user_id: int):
    with closing(sqlite3.connect(DB_PATH)) as conn:
        cur = conn.execute(
            "SELECT chat_id, thread_id FROM topics WHERE user_id = ? LIMIT 1",
            (user_id,)
        )
        row = cur.fetchone()
        return (row[0], row[1]) if row else (None, None)

# ========== КЛАВИАТУРЫ ==========
def get_main_keyboard():
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="/start"), KeyboardButton(text="/photo")],
            [KeyboardButton(text="/addphoto"), KeyboardButton(text="/download")],
            [KeyboardButton(text="/result"), KeyboardButton(text="/info")]
        ],
        resize_keyboard=True,
        is_persistent=True
    )

def get_upload_keyboard(step_name, has_files=False):
    if has_files:
        buttons = [[
            InlineKeyboardButton(text="✅ Завершить", callback_data="upload_ok"),
            InlineKeyboardButton(text="❌ Отмена", callback_data="upload_cancel")
        ]]
    else:
        if step_name in MANDATORY_STEPS:
            buttons = [[InlineKeyboardButton(text="❌ Отмена", callback_data="upload_cancel")]]
        else:
            buttons = [[
                InlineKeyboardButton(text="➡️ След.", callback_data="upload_next"),
                InlineKeyboardButton(text="❌ Отмена", callback_data="upload_cancel")
            ]]
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def get_addphoto_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ Завершить", callback_data="addphoto_done"),
        InlineKeyboardButton(text="❌ Отмена", callback_data="upload_cancel")
    ]])

# ========== КОМАНДЫ ==========
@router.message(Command("start"))
async def cmd_start(message: Message):
    text = (
        "🤖 Бот для обследования объектов котельных\n\n"
        "Команды:\n"
        "/photo – пошаговая загрузка по чек-листу\n"
        "/addphoto – добавить файлы к объекту\n"
        "/download – скачать файлы объекта (из БД)\n"
        "/result – завершённые загрузки (текущая сессия)\n"
        "/info – сведения об объекте из objects.xlsx\n"
        "/settopic – привязать текущую Тему из группы Архив к вашему профилю"
    )
    await message.answer(text, reply_markup=get_main_keyboard())

@router.message(Command("settopic"))
async def cmd_settopic(message: Message):
    """Выполнить ЭТУ команду НУЖНО внутри группы Архив, находясь в нужной теме."""
    if message.chat.id != ARCHIVE_CHAT_ID:
        await message.answer("Эту команду нужно выполнять в группе Архив, внутри нужной темы (topic).")
        return
    if not getattr(message, "is_topic_message", False):
        await message.answer("Команда должна выполняться внутри темы (topic) группы Архив.")
        return
    thread_id = message.message_thread_id
    set_topic_for_user(message.from_user.id, ARCHIVE_CHAT_ID, thread_id)
    await message.answer(f"✅ Тема привязана к пользователю @{message.from_user.username or message.from_user.id} (thread_id={thread_id}).")

@router.message(Command("photo"))
async def cmd_photo(message: Message, state: FSMContext):
    await state.set_state(UploadStates.waiting_object_id)
    await message.answer("📝 Введите номер объекта для загрузки:", reply_markup=get_main_keyboard())

@router.message(Command("addphoto"))
async def cmd_addphoto(message: Message, state: FSMContext):
    await state.set_state(AddPhotoStates.waiting_object_id)
    await message.answer("📝 Введите номер объекта, чтобы добавить фото:", reply_markup=get_main_keyboard())

@router.message(Command("download"))
async def cmd_download(message: Message, state: FSMContext):
    await state.set_state(DownloadStates.waiting_object_id)
    await message.answer("📝 Введите номер объекта для скачивания файлов:", reply_markup=get_main_keyboard())

@router.message(Command("result"))
async def cmd_result(message: Message):
    if not objects_data:
        await message.answer("📋 Нет завершённых загрузок в текущей сессии.", reply_markup=get_main_keyboard())
        return
    text = "✅ Завершённые загрузки (сессия):\n"
    for oid, data in objects_data.items():
        total_files = sum(len(s['files']) for s in data["steps"])
        text += f"• Объект {oid}: {total_files} файлов\n"
    await message.answer(text, reply_markup=get_main_keyboard())

@router.message(Command("info"))
async def cmd_info(message: Message, state: FSMContext):
    await state.set_state(InfoStates.waiting_object_id)
    await message.answer("📝 Введите номер объекта для получения информации:", reply_markup=get_main_keyboard())

# ========== ОБРАБОТКА ИД ОБЪЕКТА ==========
@router.message(UploadStates.waiting_object_id)
async def process_upload_object_id(message: Message, state: FSMContext):
    object_id = message.text.strip()
    steps = [{"name": s, "files": []} for s in UPLOAD_STEPS]
    await state.update_data(object_id=object_id, steps=steps, step_index=0, last_message_id=None)
    await state.set_state(UploadStates.uploading_steps)
    await send_upload_step(message, state)

@router.message(AddPhotoStates.waiting_object_id)
async def process_addphoto_object_id(message: Message, state: FSMContext):
    object_id = message.text.strip()
    await state.update_data(object_id=object_id, files=[], last_message_id=None)
    await state.set_state(AddPhotoStates.uploading_files)
    msg = await message.answer(
        f"📸 Отправьте файлы для объекта {object_id}.\nКогда закончите, нажмите ✅ Завершить.",
        reply_markup=get_addphoto_keyboard()
    )
    await state.update_data(last_message_id=msg.message_id)

@router.message(DownloadStates.waiting_object_id)
async def process_download_object_id(message: Message, state: FSMContext):
    object_id = message.text.strip()
    await message.answer(f"🔍 Ищу файлы объекта {object_id} в БД...")
    try:
        if not has_object_in_db(object_id):
            await message.answer(f"❌ Объект {object_id} не найден в базе.", reply_markup=get_main_keyboard())
            await state.clear()
            return

        by_step = read_files_from_db(object_id)
        total = sum(len(v) for v in by_step.values())
        if total == 0:
            await message.answer(f"❌ Для объекта {object_id} нет файлов в базе.", reply_markup=get_main_keyboard())
            await state.clear()
            return

        await message.answer(f"📂 Найдено файлов: {total}. Отправляю...")

        # По шагам с заголовками
        for step_name, files in by_step.items():
            await message.answer(f"📁 {step_name}")

            # Фото/видео альбомами (Telegram альбом 2..10 элементов)
            pv = [f for f in files if f["type"] in ("photo", "video")]
            i = 0
            while i < len(pv):
                batch = pv[i:i+10]
                if len(batch) == 1:
                    f = batch[0]
                    if f["type"] == "photo":
                        await bot.send_photo(chat_id=message.chat.id, photo=f["file_id"])
                    else:
                        await bot.send_video(chat_id=message.chat.id, video=f["file_id"])
                else:
                    media = []
                    for f in batch:
                        if f["type"] == "photo":
                            media.append(InputMediaPhoto(media=f["file_id"]))
                        else:
                            media.append(InputMediaVideo(media=f["file_id"]))
                    await bot.send_media_group(chat_id=message.chat.id, media=media)
                i += len(batch)

            # Документы — по одному
            docs = [f for f in files if f["type"] == "document"]
            for d in docs:
                await bot.send_document(chat_id=message.chat.id, document=d["file_id"])

        await message.answer(f"✅ Все файлы объекта {object_id} отправлены.", reply_markup=get_main_keyboard())

    except Exception as e:
        print(f"[process_download_object_id] Ошибка: {e}")
        await message.answer(f"❌ Произошла ошибка при выдаче файлов: {e}", reply_markup=get_main_keyboard())

    await state.clear()

@router.message(InfoStates.waiting_object_id)
async def process_info_object_id(message: Message, state: FSMContext):
    object_id = message.text.strip()
    try:
        workbook = openpyxl.load_workbook("objects.xlsx")
        sheet = workbook.active
        found = False
        info_text = f"📋 Информация об объекте {object_id}:\n\n"
        for row in sheet.iter_rows(min_row=2, values_only=True):
            if str(row[0]).strip() == object_id:
                found = True
                info_text += f"🏢 Потребитель: {row[1] if len(row) > 1 else 'Н/Д'}\n"
                info_text += f"📍 Объект: {row[2] if len(row) > 2 else 'Н/Д'}\n"
                info_text += f"🗺 Адрес: {row[3] if len(row) > 3 else 'Н/Д'}\n"
                break
        if found:
            await message.answer(info_text, reply_markup=get_main_keyboard())
        else:
            await message.answer(f"❌ Объект {object_id} не найден в файле objects.xlsx", reply_markup=get_main_keyboard())
    except FileNotFoundError:
        await message.answer("❌ Файл objects.xlsx не найден.", reply_markup=get_main_keyboard())
    except Exception as e:
        await message.answer(f"❌ Ошибка при чтении файла: {e}", reply_markup=get_main_keyboard())
    await state.clear()

# ========== ОБРАБОТКА ФАЙЛОВ ==========
@router.message(UploadStates.uploading_steps, F.photo | F.video | F.document)
async def handle_upload_files(message: Message, state: FSMContext):
    data = await state.get_data()
    step_index = data["step_index"]
    steps = data["steps"]
    current_step = steps[step_index]

    file_info = {}
    if message.photo:
        file_info = {"type": "photo", "file_id": message.photo[-1].file_id}
    elif message.document:
        file_info = {"type": "document", "file_id": message.document.file_id}
    elif message.video:
        file_info = {"type": "video", "file_id": message.video.file_id}

    if file_info:
        current_step["files"].append(file_info)

    # Обновляем кнопки
    if data.get("last_message_id"):
        try:
            await bot.delete_message(message.chat.id, data["last_message_id"])
        except:
            pass
    msg = await message.answer("Выберите действие:", reply_markup=get_upload_keyboard(current_step["name"], has_files=True))
    await state.update_data(steps=steps, last_message_id=msg.message_id)

@router.message(AddPhotoStates.uploading_files, F.photo | F.video | F.document)
async def handle_addphoto_files(message: Message, state: FSMContext):
    data = await state.get_data()
    files = data["files"]

    file_info = {}
    if message.photo:
        file_info = {"type": "photo", "file_id": message.photo[-1].file_id}
    elif message.document:
        file_info = {"type": "document", "file_id": message.document.file_id}
    elif message.video:
        file_info = {"type": "video", "file_id": message.video.file_id}

    if file_info:
        files.append(file_info)
        await state.update_data(files=files)

# ========== CALLBACKS ==========
@router.callback_query(F.data == "upload_ok")
async def callback_upload_ok(callback: CallbackQuery, state: FSMContext):
    await callback.answer("✅ Шаг завершён")
    await advance_step(callback.message, state, author_id=callback.from_user.id)

@router.callback_query(F.data == "upload_next")
async def callback_upload_next(callback: CallbackQuery, state: FSMContext):
    await callback.answer("➡️ Пропущено")
    await advance_step(callback.message, state, skip=True, author_id=callback.from_user.id)

@router.callback_query(F.data == "upload_cancel")
async def callback_upload_cancel(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    object_id = data.get("object_id", "")
    await state.clear()
    try:
        await callback.message.delete()
    except:
        pass
    await callback.message.answer(f"❌ Загрузка для объекта {object_id} отменена.", reply_markup=get_main_keyboard())
    await callback.answer("Отменено")

@router.callback_query(F.data == "addphoto_done")
async def callback_addphoto_done(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    object_id = data["object_id"]
    files = data["files"]

    if not files:
        await callback.answer("❌ Не загружено ни одного файла")
        return

    # Сохраняем в БД
    save_files_to_db(object_id, "Дополнительные файлы", files, author_id=callback.from_user.id)

    # Пишем в архив (по теме сотрудника)
    await post_to_archive(object_id, [{"name": "Дополнительные файлы", "files": files}], author_id=callback.from_user.id)

    await state.clear()
    try:
        await callback.message.delete()
    except:
        pass
    await callback.message.answer(
        f"✅ Дополнительные файлы для объекта {object_id} сохранены ({len(files)} шт.).",
        reply_markup=get_main_keyboard()
    )
    await callback.answer("Готово ✅")

# ========== ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ==========
async def send_upload_step(message: Message, state: FSMContext):
    data = await state.get_data()
    step_index = data["step_index"]
    steps = data["steps"]
    current_step = steps[step_index]

    if data.get("last_message_id"):
        try:
            await bot.delete_message(message.chat.id, data["last_message_id"])
        except:
            pass

    msg = await message.answer(
        f"📸 Отправьте {current_step['name']}",
        reply_markup=get_upload_keyboard(current_step["name"], has_files=False)
    )
    await state.update_data(last_message_id=msg.message_id)

async def advance_step(message: Message, state: FSMContext, skip=False, author_id: int | None = None):
    data = await state.get_data()
    step_index = data["step_index"]
    steps = data["steps"]
    object_id = data["object_id"]

    # Если НЕ пропускаем — сохраняем файлы текущего шага
    current = steps[step_index]
    if not skip and current["files"]:
        save_files_to_db(object_id, current["name"], current["files"], author_id=author_id)

    # Переходим к следующему
    step_index += 1
    if step_index >= len(steps):
        # Финал: постим весь комплект в архив (по теме сотрудника) и очищаем
        await post_to_archive(object_id, steps, author_id=author_id)
        objects_data[object_id] = {"steps": steps}  # для /result (сессия)
        total_files = sum(len(s["files"]) for s in steps)

        try:
            await message.delete()
        except:
            pass
        await message.answer(
            f"✅ Загрузка завершена для объекта {object_id}\nВсего файлов: {total_files}",
            reply_markup=get_main_keyboard()
        )
        await state.clear()
    else:
        await state.update_data(step_index=step_index)
        await send_upload_step(message, state)

async def post_to_archive(object_id: str, steps: list, author_id: int | None):
    """Отправляет инфо и файлы в группу Архив в ТЕМУ, привязанную к автору."""
    try:
        chat_id, thread_id = get_topic_for_user(author_id) if author_id else (None, None)
        # Если тема не привязана, шлём в общий чат (без thread_id)
        kwargs = {}
        if chat_id == ARCHIVE_CHAT_ID and thread_id:
            kwargs["message_thread_id"] = thread_id

        header = (
            f"💾 ОБЪЕКТ #{object_id}\n"
            f"👤 Исполнитель: {author_id}\n"
            f"🕒 {datetime.now().strftime('%d.%m.%Y %H:%M')}"
        )
        await bot.send_message(ARCHIVE_CHAT_ID, header, **kwargs)

        for step in steps:
            files = step["files"]
            if not files:
                continue
            await bot.send_message(ARCHIVE_CHAT_ID, f"📁 {step['name']}", **kwargs)

            # Фото+видео альбомами
            pv = [f for f in files if f["type"] in ("photo", "video")]
            i = 0
            while i < len(pv):
                batch = pv[i:i+10]
                if len(batch) == 1:
                    f = batch[0]
                    if f["type"] == "photo":
                        await bot.send_photo(ARCHIVE_CHAT_ID, f["file_id"], **kwargs)
                    else:
                        await bot.send_video(ARCHIVE_CHAT_ID, f["file_id"], **kwargs)
                else:
                    media = []
                    for f in batch:
                        if f["type"] == "photo":
                            media.append(InputMediaPhoto(media=f["file_id"]))
                        else:
                            media.append(InputMediaVideo(media=f["file_id"]))
                    await bot.send_media_group(ARCHIVE_CHAT_ID, media, **kwargs)
                i += len(batch)

            # Документы — по одному
            docs = [f for f in files if f["type"] == "document"]
            for d in docs:
                await bot.send_document(ARCHIVE_CHAT_ID, d["file_id"], **kwargs)

    except Exception as e:
        print(f"[post_to_archive] Ошибка: {e}")

# ========== WEBHOOK ==========
async def on_startup():
    init_db()
    webhook_url = f"{WEBHOOK_URL}/{TOKEN}"
    await bot.delete_webhook(drop_pending_updates=True)
    await bot.set_webhook(webhook_url)

    commands = [
        BotCommand(command="start", description="Перезапуск/справка"),
        BotCommand(command="photo", description="Загрузить файлы по чек-листу"),
        BotCommand(command="addphoto", description="Добавить файлы к объекту"),
        BotCommand(command="download", description="Скачать файлы объекта"),
        BotCommand(command="result", description="Результаты загрузок"),
        BotCommand(command="info", description="Информация об объекте"),
        BotCommand(command="settopic", description="Привязать текущую тему Архива к пользователю"),
    ]
    await bot.set_my_commands(commands)
    print("🚀 Бот запущен с webhook:", webhook_url)

async def on_shutdown():
    await bot.session.close()

async def handle_webhook(request):
    """Очень важно: обрабатываем апдейт В ФОНЕ, чтобы мгновенно вернуть 200 OK и Telegram не делал повторов."""
    update = await request.json()
    from aiogram.types import Update
    telegram_update = Update(**update)
    asyncio.create_task(dp.feed_update(bot, telegram_update))  # фоновая обработка
    return web.Response(text="OK")

async def health_check(request):
    return web.Response(text="🤖 OK")

# ========== ЗАПУСК ==========
def main():
    dp.include_router(router)
    app = web.Application()
    app.router.add_post(f"/{TOKEN}", handle_webhook)
    app.router.add_get("/", health_check)
    app.on_startup.append(lambda app: asyncio.create_task(on_startup()))
    app.on_shutdown.append(lambda app: asyncio.create_task(on_shutdown()))
    web.run_app(app, host="0.0.0.0", port=PORT)

if __name__ == "__main__":
    main()
