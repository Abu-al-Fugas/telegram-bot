# bot.py
import os
import asyncio
from datetime import datetime
import openpyxl
import sqlite3
from contextlib import closing
import aiohttp
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
import json

# ========== НАСТРОЙКИ ==========
TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
WEBHOOK_URL = os.environ.get("WEBHOOK_URL", "https://telegram-bot-b6pn.onrender.com")
PORT = int(os.environ.get("PORT", 10000))
DB_PATH = "files.db"

bot = Bot(token=TOKEN)
storage = MemoryStorage()
dp = Dispatcher(storage=storage)
router = Router()

# 👑 Главный админ(ы)
ADMIN_IDS = {7277619113}  # Mr. X

# ========== МАРШРУТИЗАЦИЯ ТЕМ (WORK → ARCHIVE) ==========
# Формат:
# TOPIC_MAP[work_chat_id][work_thread_id] = {"chat_id": archive_chat_id, "thread_id": archive_thread_id}
# ВНИМАНИЕ: при сохранении в JSON ключи становятся строками — ниже в коде есть нормализация доступа.
TOPIC_MAP = {
    # ==== ТЕКУЩИЕ ЖИВЫЕ МАРШРУТЫ ====
    -1003281117256: {  # Рабочая группа A (dagestan.xlsx)
        3: {"chat_id": -1003250982118, "thread_id": 3},
        # ----- 8 заглушек тем на будущее -----
        # 101: {"chat_id": -1003250982118, "thread_id": 401},
        # 102: {"chat_id": -1003250982118, "thread_id": 402},
        # 103: {"chat_id": -1003250982118, "thread_id": 403},
        # 104: {"chat_id": -1003250982118, "thread_id": 404},
        # 105: {"chat_id": -1003250982118, "thread_id": 405},
        # 106: {"chat_id": -1003250982118, "thread_id": 406},
        # 107: {"chat_id": -1003250982118, "thread_id": 407},
        # 108: {"chat_id": -1003250982118, "thread_id": 408},
    },
    -1003237477689: {  # Рабочая группа B (nazran.xlsx)
        15: {"chat_id": -1003252316518, "thread_id": 6},
        # ----- 8 заглушек тем на будущее -----
        # 201: {"chat_id": -1003252316518, "thread_id": 501},
        # 202: {"chat_id": -1003252316518, "thread_id": 501},
        # 203: {"chat_id": -1003252316518, "thread_id": 502},
        # 204: {"chat_id": -1003252316518, "thread_id": 502},
        # 205: {"chat_id": -1003252316518, "thread_id": 503},
        # 206: {"chat_id": -1003252316518, "thread_id": 503},
        # 207: {"chat_id": -1003252316518, "thread_id": 504},
        # 208: {"chat_id": -1003252316518, "thread_id": 504},
    },

    # ====== ЗАГЛУШКИ: 3 будущие рабочие группы (по 10 тем каждая) ======
    # Пример: замени CHAT_ID и THREAD_ID на реальные, когда создашь.
    -1004000000001: {
        1: {"chat_id": -1005000000001, "thread_id": 1},
        2: {"chat_id": -1005000000001, "thread_id": 2},
        3: {"chat_id": -1005000000001, "thread_id": 3},
        4: {"chat_id": -1005000000001, "thread_id": 4},
        5: {"chat_id": -1005000000001, "thread_id": 5},
        6: {"chat_id": -1005000000001, "thread_id": 6},
        7: {"chat_id": -1005000000001, "thread_id": 7},
        8: {"chat_id": -1005000000001, "thread_id": 8},
        9: {"chat_id": -1005000000001, "thread_id": 9},
        10: {"chat_id": -1005000000001, "thread_id": 10},
    },
    -1004000000002: {
        1: {"chat_id": -1005000000002, "thread_id": 1},
        2: {"chat_id": -1005000000002, "thread_id": 2},
        3: {"chat_id": -1005000000002, "thread_id": 3},
        4: {"chat_id": -1005000000002, "thread_id": 4},
        5: {"chat_id": -1005000000002, "thread_id": 5},
        6: {"chat_id": -1005000000002, "thread_id": 6},
        7: {"chat_id": -1005000000002, "thread_id": 7},
        8: {"chat_id": -1005000000002, "thread_id": 8},
        9: {"chat_id": -1005000000002, "thread_id": 9},
        10: {"chat_id": -1005000000002, "thread_id": 10},
    },
    -1004000000003: {
        1: {"chat_id": -1005000000003, "thread_id": 1},
        2: {"chat_id": -1005000000003, "thread_id": 2},
        3: {"chat_id": -1005000000003, "thread_id": 3},
        4: {"chat_id": -1005000000003, "thread_id": 4},
        5: {"chat_id": -1005000000003, "thread_id": 5},
        6: {"chat_id": -1005000000003, "thread_id": 6},
        7: {"chat_id": -1005000000003, "thread_id": 7},
        8: {"chat_id": -1005000000003, "thread_id": 8},
        9: {"chat_id": -1005000000003, "thread_id": 9},
        10: {"chat_id": -1005000000003, "thread_id": 10},
    },
}

# ========== ПРИВЯЗКА EXCEL К РАБОЧИМ ГРУППАМ ==========
# Если группа не в словаре — бот предупредит, что к группе не привязан Excel-документ.
EXCEL_MAP = {
    -1003281117256: "dagestan.xlsx",
    -1003237477689: "nazran.xlsx",
    # Будущие рабочие группы — сразу с файлами-заглушками:
    -1004000000001: "bryunsk.xlsx",
    -1004000000002: "orel.xlsx",
    -1004000000003: "objects.xlsx",
}

# ========== БАЗА ДАННЫХ ==========
def init_db():
    with closing(sqlite3.connect(DB_PATH)) as conn:
        conn.execute("""CREATE TABLE IF NOT EXISTS files(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            object_id TEXT, step TEXT, kind TEXT, file_id TEXT,
            author TEXT, created_at TEXT
        )""")
        conn.execute("""CREATE TABLE IF NOT EXISTS completed(
            object_id TEXT PRIMARY KEY,
            author TEXT,
            completed_at TEXT
        )""")
        conn.execute("""CREATE TABLE IF NOT EXISTS settings(
            key TEXT PRIMARY KEY,
            value TEXT
        )""")
        conn.commit()

def db_set(key, value):
    with closing(sqlite3.connect(DB_PATH)) as conn:
        conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (key, value))
        conn.commit()

def db_get(key):
    with closing(sqlite3.connect(DB_PATH)) as conn:
        cur = conn.execute("SELECT value FROM settings WHERE key=?", (key,))
        r = cur.fetchone()
        return r[0] if r else None

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

def delete_files_by_object(object_id):
    with closing(sqlite3.connect(DB_PATH)) as conn:
        conn.execute("DELETE FROM files WHERE object_id=?", (object_id,))
        conn.commit()

def mark_completed(object_id: str, author: str):
    with closing(sqlite3.connect(DB_PATH)) as conn:
        conn.execute(
            "INSERT OR REPLACE INTO completed(object_id, author, completed_at) VALUES (?,?,?)",
            (object_id, author, datetime.now().isoformat())
        )
        conn.commit()

def list_completed():
    with closing(sqlite3.connect(DB_PATH)) as conn:
        cur = conn.execute("SELECT object_id, author, completed_at FROM completed ORDER BY object_id")
        return cur.fetchall()

# ========== ЗАГРУЗКА/СОХРАНЕНИЕ НАСТРОЕК ==========
def load_settings():
    global TOPIC_MAP, EXCEL_MAP
    try:
        val1 = db_get("TOPIC_MAP")
        val2 = db_get("EXCEL_MAP")
        if val1:
            TOPIC_MAP = json.loads(val1)
        if val2:
            EXCEL_MAP = json.loads(val2)
        print("✅ Настройки (TOPIC_MAP/EXCEL_MAP) загружены из БД.")
    except Exception as e:
        print(f"⚠️ Ошибка загрузки настроек: {e}")

def save_settings():
    # Всегда сериализуем с ключами-строками (для единообразия)
    def stringify_keys(d):
        if isinstance(d, dict):
            return {str(k): stringify_keys(v) for k, v in d.items()}
        elif isinstance(d, list):
            return [stringify_keys(x) for x in d]
        else:
            return d

    db_set("TOPIC_MAP", json.dumps(stringify_keys(TOPIC_MAP), ensure_ascii=False))
    db_set("EXCEL_MAP", json.dumps(stringify_keys(EXCEL_MAP), ensure_ascii=False))

# ========== СОСТОЯНИЯ ==========
class Upload(StatesGroup):
    waiting_object = State()
    confirming = State()
    uploading = State()

class AddPhoto(StatesGroup):
    waiting_object = State()
    confirming = State()
    uploading = State()

class Info(StatesGroup):
    waiting_object = State()

class AdminState(StatesGroup):
    waiting_command = State()
    waiting_route = State()
    waiting_route_del = State()
    waiting_excel = State()
    waiting_excel_del = State()

# ========== КОНСТАНТЫ ==========
UPLOAD_STEPS = [
    "📸 Общий вид газопровода до и после счётчика",
    "🧾 Старый счётчик — общий и крупный план (заводской номер, год, показания)",
    "🔒 Фото пломбы на фоне маркировки счётчика",
    "➡️ Стрелка направления газа на старом счётчике",
    "🧱 Газопровод после монтажа нового счётчика",
    "🆕 Новый счётчик — общий и крупный план (заводской номер, год, показания)",
    "➡️ Стрелка направления нового счётчика",
    "🎥 Видео герметичности соединений",
    "🔥 Шильдик котла (модель и мощность)",
    "📎 Дополнительные фото"
]
MANDATORY_STEPS = set(UPLOAD_STEPS[:-1])  # все кроме "Дополнительные фото"

# ========== КЛАВИАТУРЫ ==========
def admin_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📋 Посмотреть маршруты", callback_data="admin_routes")],
        [InlineKeyboardButton(text="🧭 Добавить маршрут", callback_data="admin_add_route")],
        [InlineKeyboardButton(text="❌ Удалить маршрут", callback_data="admin_del_route")],
        [InlineKeyboardButton(text="📘 Посмотреть Excel", callback_data="admin_excel")],
        [InlineKeyboardButton(text="➕ Добавить Excel", callback_data="admin_add_excel")],
        [InlineKeyboardButton(text="🗑 Удалить Excel", callback_data="admin_del_excel")],
    ])

def main_kb():
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="/photo"), KeyboardButton(text="/addphoto"), KeyboardButton(text="/info")]],
        resize_keyboard=True
    )

def step_kb(step_name, has_files=False, user_id: int | None = None):
    """Клавиатура шагов с привязкой к user_id"""
    cancel_cb = f"cancel_{user_id}" if user_id else "cancel"
    if has_files:
        buttons = [[
            InlineKeyboardButton(text="💾 Сохранить", callback_data=f"save_{user_id}"),
            InlineKeyboardButton(text="❌ Отмена", callback_data=cancel_cb)
        ]]
    else:
        if step_name in MANDATORY_STEPS:
            buttons = [[InlineKeyboardButton(text="❌ Отмена", callback_data=cancel_cb)]]
        else:
            buttons = [[
                InlineKeyboardButton(text="➡️ Пропустить", callback_data=f"skip_{user_id}"),
                InlineKeyboardButton(text="❌ Отмена", callback_data=cancel_cb)
            ]]
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def confirm_kb(prefix: str, user_id: int):
    """Клавиатура подтверждения с привязкой к user_id"""
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ Да", callback_data=f"{prefix}_confirm_yes_{user_id}"),
        InlineKeyboardButton(text="❌ Отмена", callback_data=f"{prefix}_confirm_no_{user_id}")
    ]])

# ========== ХЕЛПЕРЫ ==========
def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS

def get_excel_filename_for_chat(chat_id: int) -> str | None:
    # Учитываем, что ключи могли быть сохранены как строки в JSON
    return (EXCEL_MAP.get(chat_id)
            or EXCEL_MAP.get(str(chat_id)))

def mapping_lookup(work_chat_id: int | str, work_thread_id: int | str):
    """
    Возвращает словарь {"chat_id": ..., "thread_id": ...} для маршрута,
    учитывая, что ключи могли быть сериализованы в строки.
    """
    # прямой доступ
    sub = TOPIC_MAP.get(work_chat_id) or TOPIC_MAP.get(str(work_chat_id))
    if not sub:
        return None
    m = sub.get(work_thread_id) or sub.get(str(work_thread_id))
    return m

def check_object_excel(chat_id: int, object_id: str):
    """Проверка объекта в Excel, привязанном к группе chat_id"""
    filename = get_excel_filename_for_chat(chat_id)
    if not filename:
        return None, "⚠️ К этой группе не привязан Excel-документ. Обратитесь к администратору."
    try:
        wb = openpyxl.load_workbook(filename, read_only=True, data_only=True)
        sh = wb.active
        for row in sh.iter_rows(min_row=2, values_only=True):
            if str(row[0]).strip() == str(object_id):
                return True, str(row[1])
        return False, None
    except Exception as e:
        return None, f"{filename}: {e}"

def get_object_info(chat_id: int, object_id: str):
    """Получение информации об объекте из Excel, привязанного к группе chat_id"""
    filename = get_excel_filename_for_chat(chat_id)
    if not filename:
        return {"error": "⚠️ К этой группе не привязан Excel-документ. Обратитесь к администратору."}
    try:
        wb = openpyxl.load_workbook(filename, read_only=True, data_only=True)
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
    except Exception as e:
        return {"error": f"{filename}: {e}"}

async def safe_call(coro, pause=0.25):
    try:
        res = await coro
        await asyncio.sleep(pause)
        return res
    except TelegramRetryAfter as e:
        await asyncio.sleep(e.retry_after + 1)
        return await coro

# ========== KEEPALIVE ==========
async def keepalive():
    while True:
        try:
            async with aiohttp.ClientSession() as s:
                await s.get(WEBHOOK_URL)
        except:
            pass
        await asyncio.sleep(240)  # 4 минуты

# ========== АДМИН-ПАНЕЛЬ ==========
@router.message(Command("admin"))
async def admin_panel(m: Message, state: FSMContext):
    if not is_admin(m.from_user.id):
        await m.answer("🚫 У вас нет доступа к админ-панели.")
        return
    await m.answer("👑 Панель администратора:", reply_markup=admin_kb())
    await state.set_state(AdminState.waiting_command)

@router.callback_query(F.data == "admin_routes")
async def show_routes(c: CallbackQuery):
    if not is_admin(c.from_user.id):
        await c.answer("🚫 Нет доступа", show_alert=True)
        return
    text = "📋 <b>Маршруты WORK → ARCHIVE</b>\n\n"
    for work_chat, threads in (TOPIC_MAP.items() if isinstance(TOPIC_MAP, dict) else []):
        text += f"<b>{work_chat}</b>:\n"
        if isinstance(threads, dict) and threads:
            for t_id, dest in threads.items():
                try:
                    dst = f"{dest['chat_id']}_{dest['thread_id']}"
                except:
                    dst = str(dest)
                text += f"  🧩 {t_id} → {dst}\n"
        else:
            text += "  (пока нет настроенных тем)\n"
        text += "\n"
    await c.message.edit_text(text or "—", parse_mode="HTML", reply_markup=admin_kb())
    await c.answer()

@router.callback_query(F.data == "admin_add_route")
async def add_route_start(c: CallbackQuery, state: FSMContext):
    if not is_admin(c.from_user.id):
        await c.answer("🚫 Нет доступа", show_alert=True)
        return
    await c.message.edit_text(
        "🧭 Введите данные маршрута одной строкой:\n\n"
        "<code>work_chat_id work_thread_id archive_chat_id archive_thread_id</code>\n\n"
        "Пример:\n<code>-1003281117256 101 -1003250982118 401</code>",
        parse_mode="HTML"
    )
    await state.set_state(AdminState.waiting_route)
    await c.answer()

@router.message(AdminState.waiting_route)
async def add_route_process(m: Message, state: FSMContext):
    if not is_admin(m.from_user.id):
        await m.answer("🚫 Нет доступа.")
        return
    try:
        wc, wt, ac, at = m.text.strip().split()
        # храним ключи как строки — так безопаснее для JSON
        wc_s, wt_s = str(int(wc)), str(int(wt))
        ac_i, at_i = int(ac), int(at)
        TOPIC_MAP.setdefault(wc_s, {})[wt_s] = {"chat_id": ac_i, "thread_id": at_i}
        save_settings()
        await m.answer(f"✅ Добавлен маршрут:\n{wc_s}_{wt_s} → {ac_i}_{at_i}", reply_markup=admin_kb())
    except Exception as e:
        await m.answer(f"⚠️ Ошибка: {e}", reply_markup=admin_kb())
    await state.clear()

@router.callback_query(F.data == "admin_del_route")
async def del_route_start(c: CallbackQuery, state: FSMContext):
    if not is_admin(c.from_user.id):
        await c.answer("🚫 Нет доступа", show_alert=True)
        return
    await c.message.edit_text(
        "🗑 Удаление маршрута.\nОтправьте одной строкой:\n\n"
        "<code>work_chat_id work_thread_id</code>\n\n"
        "Пример:\n<code>-1003281117256 101</code>",
        parse_mode="HTML"
    )
    await state.set_state(AdminState.waiting_route_del)
    await c.answer()

@router.message(AdminState.waiting_route_del)
async def del_route_process(m: Message, state: FSMContext):
    if not is_admin(m.from_user.id):
        await m.answer("🚫 Нет доступа.")
        return
    try:
        wc, wt = m.text.strip().split()
        wc_s, wt_s = str(int(wc)), str(int(wt))
        threads = TOPIC_MAP.get(wc_s)
        if not threads or wt_s not in threads:
            await m.answer("❌ Такой маршрут не найден.", reply_markup=admin_kb())
        else:
            threads.pop(wt_s, None)
            if not threads:
                TOPIC_MAP.pop(wc_s, None)
            save_settings()
            await m.answer(f"✅ Удалён маршрут: {wc_s}_{wt_s}", reply_markup=admin_kb())
    except Exception as e:
        await m.answer(f"⚠️ Ошибка: {e}", reply_markup=admin_kb())
    await state.clear()

@router.callback_query(F.data == "admin_excel")
async def show_excel(c: CallbackQuery):
    if not is_admin(c.from_user.id):
        await c.answer("🚫 Нет доступа", show_alert=True)
        return
    text = "📘 <b>Привязки Excel:</b>\n\n"
    for k, v in (EXCEL_MAP.items() if isinstance(EXCEL_MAP, dict) else []):
        text += f"{k} → <code>{v}</code>\n"
    await c.message.edit_text(text or "—", parse_mode="HTML", reply_markup=admin_kb())
    await c.answer()

@router.callback_query(F.data == "admin_add_excel")
async def add_excel_start(c: CallbackQuery, state: FSMContext):
    if not is_admin(c.from_user.id):
        await c.answer("🚫 Нет доступа", show_alert=True)
        return
    await c.message.edit_text(
        "📎 Добавление/замена Excel:\nОтправьте одной строкой:\n\n"
        "<code>chat_id filename.xlsx</code>\n\n"
        "Пример:\n<code>-1003281117256 dagestan.xlsx</code>",
        parse_mode="HTML"
    )
    await state.set_state(AdminState.waiting_excel)
    await c.answer()

@router.message(AdminState.waiting_excel)
async def add_excel_process(m: Message, state: FSMContext):
    if not is_admin(m.from_user.id):
        await m.answer("🚫 Нет доступа.")
        return
    try:
        chat_id_s, file = m.text.strip().split()
        chat_id_s = str(int(chat_id_s))
        EXCEL_MAP[chat_id_s] = file
        save_settings()
        await m.answer(f"✅ Привязан Excel: {chat_id_s} → {file}", reply_markup=admin_kb())
    except Exception as e:
        await m.answer(f"⚠️ Ошибка: {e}", reply_markup=admin_kb())
    await state.clear()

@router.callback_query(F.data == "admin_del_excel")
async def del_excel_start(c: CallbackQuery, state: FSMContext):
    if not is_admin(c.from_user.id):
        await c.answer("🚫 Нет доступа", show_alert=True)
        return
    await c.message.edit_text(
        "🗑 Удаление Excel-привязки:\nОтправьте одной строкой:\n\n"
        "<code>chat_id</code>\n\n"
        "Пример:\n<code>-1003281117256</code>",
        parse_mode="HTML"
    )
    await state.set_state(AdminState.waiting_excel_del)
    await c.answer()

@router.message(AdminState.waiting_excel_del)
async def del_excel_process(m: Message, state: FSMContext):
    if not is_admin(m.from_user.id):
        await m.answer("🚫 Нет доступа.")
        return
    try:
        chat_id_s = str(int(m.text.strip()))
        if chat_id_s in EXCEL_MAP:
            EXCEL_MAP.pop(chat_id_s, None)
        elif int(chat_id_s) in EXCEL_MAP:
            EXCEL_MAP.pop(int(chat_id_s), None)
        else:
            await m.answer("❌ Привязка не найдена.", reply_markup=admin_kb())
            await state.clear()
            return
        save_settings()
        await m.answer(f"✅ Удалена привязка Excel для {chat_id_s}", reply_markup=admin_kb())
    except Exception as e:
        await m.answer(f"⚠️ Ошибка: {e}", reply_markup=admin_kb())
    await state.clear()

# ========== КОМАНДЫ ПОЛЬЗОВАТЕЛЯ ==========
@router.message(Command("start"))
async def cmd_start(m: Message):
    await m.answer(
        "👋 Привет! Это бот для загрузки фото по объектам счётчиков газа.\n\n"
        "📸 /photo — новая загрузка\n"
        "📎 /addphoto — добавить фото\n"
        "ℹ️ /info — информация по объектам\n"
        "⚙️ Работает в темах форумов (супергруппы).",
        reply_markup=main_kb()
    )

@router.message(Command("photo"))
async def cmd_photo(m: Message, state: FSMContext):
    if not get_excel_filename_for_chat(m.chat.id):
        await m.answer("⚠️ К этой группе не привязан Excel-документ. Обратитесь к администратору.")
        return
    await state.set_state(Upload.waiting_object)
    await state.update_data(
        owner_id=m.from_user.id,
        work_chat_id=m.chat.id,
        work_thread_id=getattr(m, "message_thread_id", None),
    )
    await m.answer("📝 Введите номер объекта:")

@router.message(Command("addphoto"))
async def cmd_addphoto(m: Message, state: FSMContext):
    if not get_excel_filename_for_chat(m.chat.id):
        await m.answer("⚠️ К этой группе не привязан Excel-документ. Обратитесь к администратору.")
        return
    await state.set_state(AddPhoto.waiting_object)
    await state.update_data(
        owner_id=m.from_user.id,
        work_chat_id=m.chat.id,
        work_thread_id=getattr(m, "message_thread_id", None),
    )
    await m.answer("📝 Введите номер объекта (для добавления файлов):")

@router.message(Command("info"))
async def cmd_info(m: Message, state: FSMContext):
    if not get_excel_filename_for_chat(m.chat.id):
        await m.answer("⚠️ К этой группе не привязан Excel-документ. Обратитесь к администратору.")
        return
    await state.set_state(Info.waiting_object)
    await state.update_data(
        owner_id=m.from_user.id,
        work_chat_id=m.chat.id,
        work_thread_id=getattr(m, "message_thread_id", None),
    )
    await m.answer("📝 Введите один или несколько номеров объектов (через запятую):")

@router.message(Command("result"))
async def cmd_result(m: Message):
    rows = list_completed()
    if not rows:
        await m.answer("📋 Пока нет завершённых объектов (через /photo).", reply_markup=main_kb())
        return
    lines = ["✅ Обработанные объекты (сценарий /photo):"]
    for oid, author, ts in rows:
        try:
            ts_h = datetime.fromisoformat(ts).strftime("%d.%m.%Y %H:%M")
        except:
            ts_h = ts
        lines.append(f"• #{oid} — {author} ({ts_h})")
    await m.answer("\n".join(lines), reply_markup=main_kb())

# ========== ПРОВЕРКА ОБЪЕКТА + ПОДТВЕРЖДЕНИЕ ==========
@router.message(Upload.waiting_object)
async def check_upload_object(m: Message, state: FSMContext):
    obj = m.text.strip()
    ok, name = check_object_excel(m.chat.id, obj)
    if ok:
        await state.update_data(
            object=obj,
            object_name=name,
            step=0,
            steps=[{"name": s, "files": []} for s in UPLOAD_STEPS],
            owner_id=m.from_user.id,
            work_chat_id=m.chat.id,
            work_thread_id=getattr(m, "message_thread_id", None),
        )
        await state.set_state(Upload.confirming)
        await m.answer(
            f"Подтвердите объект:\n\n🆔 {obj}\n🏷️ {name}",
            reply_markup=confirm_kb("photo", m.from_user.id)
        )
    elif ok is False:
        await m.answer(f"❌ Объект {obj} не найден.")
        await state.clear()
    else:
        await m.answer(f"❌ Ошибка чтения: {name or 'неизвестно'}")
        await state.clear()

@router.message(AddPhoto.waiting_object)
async def check_add_object(m: Message, state: FSMContext):
    obj = m.text.strip()
    ok, name = check_object_excel(m.chat.id, obj)
    if ok:
        await state.update_data(
            object=obj,
            object_name=name,
            files=[],
            owner_id=m.from_user.id,
            work_chat_id=m.chat.id,
            work_thread_id=getattr(m, "message_thread_id", None),
        )
        await state.set_state(AddPhoto.confirming)
        await m.answer(
            f"Подтвердите объект (добавление файлов):\n\n🆔 {obj}\n🏷️ {name}",
            reply_markup=confirm_kb("add", m.from_user.id)
        )
    elif ok is False:
        await m.answer(f"❌ Объект {obj} не найден.")
        await state.clear()
    else:
        await m.answer(f"❌ Ошибка чтения: {name or 'неизвестно'}")
        await state.clear()

# ===== Подтверждение: /photo =====
@router.callback_query(F.data.startswith("photo_confirm_yes_"))
async def photo_confirm_yes(c: CallbackQuery, state: FSMContext):
    user_id = int(c.data.split("_")[-1])
    if c.from_user.id != user_id:
        await c.answer("Эта кнопка не для вас 😅", show_alert=True)
        return

    await state.set_state(Upload.uploading)
    data = await state.get_data()
    step0 = data["steps"][0]["name"]
    owner_id = data.get("owner_id")
    await c.message.edit_text(step0, reply_markup=step_kb(step0, user_id=owner_id))
    await state.update_data(last_msg=c.message.message_id)
    await c.answer("Подтверждено ✅")

# ===== Подтверждение: /addphoto =====
@router.callback_query(F.data.startswith("add_confirm_yes_"))
async def add_confirm_yes(c: CallbackQuery, state: FSMContext):
    user_id = int(c.data.split("_")[-1])
    if c.from_user.id != user_id:
        await c.answer("Эта кнопка не для вас 😅", show_alert=True)
        return

    data = await state.get_data()
    obj = data["object"]
    owner_id = data.get("owner_id")
    await state.set_state(AddPhoto.uploading)
    await c.message.edit_text(
        f"📸 Отправьте дополнительные файлы для объекта №{obj}.",
        reply_markup=step_kb('', False, user_id=owner_id)
    )
    await state.update_data(last_msg=c.message.message_id)
    await c.answer("Подтверждено ✅")

# ====== ОТМЕНА (универсальная) ======
@router.callback_query(F.data.startswith("cancel_"))
async def cancel_anywhere(c: CallbackQuery, state: FSMContext):
    try:
        user_id = int(c.data.split("_")[1])
    except:
        await c.answer("Эта кнопка не для вас 😅", show_alert=True)
        return

    if c.from_user.id != user_id:
        await c.answer("Эта кнопка не для вас 😅", show_alert=True)
        return

    await state.clear()
    try:
        await c.message.edit_text("❌ Действие отменено.", reply_markup=None)
    except:
        pass
    await c.answer("Отменено ✅")

# ====== ОТМЕНА подтверждения /photo ======
@router.callback_query(F.data.startswith("photo_confirm_no_"))
async def cancel_confirm_photo(c: CallbackQuery, state: FSMContext):
    try:
        user_id = int(c.data.split("_")[-1])
    except:
        await c.answer("Эта кнопка не для вас 😅", show_alert=True)
        return

    if c.from_user.id != user_id:
        await c.answer("Эта кнопка не для вас 😅", show_alert=True)
        return

    await state.clear()
    try:
        await c.message.edit_text("❌ Действие отменено.", reply_markup=None)
    except:
        pass
    await c.answer("Отменено ✅")

# ====== ОТМЕНА подтверждения /addphoto ======
@router.callback_query(F.data.startswith("add_confirm_no_"))
async def cancel_confirm_add(c: CallbackQuery, state: FSMContext):
    try:
        user_id = int(c.data.split("_")[-1])
    except:
        await c.answer("Эта кнопка не для вас 😅", show_alert=True)
        return

    if c.from_user.id != user_id:
        await c.answer("Эта кнопка не для вас 😅", show_alert=True)
        return

    await state.clear()
    try:
        await c.message.edit_text("❌ Действие отменено.", reply_markup=None)
    except:
        pass
    await c.answer("Отменено ✅")

# ========== ПРИЁМ ФАЙЛОВ ==========
async def _finalize_media_group_for_photo(m: Message, state: FSMContext, group_id: str):
    await asyncio.sleep(3.2)  # дождаться всех сообщений альбома
    data = await state.get_data()
    step_i = data["step"]
    steps = data["steps"]
    cur = steps[step_i]

    media_groups = data.get("media_groups", {})
    finalizing = set(data.get("finalizing_groups", []))

    if group_id not in finalizing:
        return

    group = media_groups.pop(group_id, [])
    finalizing.discard(group_id)

    if group:
        cur["files"].extend(group)

    last_msg_id = data.get("last_msg")
    if last_msg_id:
        try:
            await m.bot.delete_message(chat_id=m.chat.id, message_id=last_msg_id)
        except:
            pass
    owner_id = data.get("owner_id")
    msg = await m.answer("Выберите действие", reply_markup=step_kb(cur["name"], has_files=True, user_id=owner_id))
    await state.update_data(
        steps=steps,
        last_msg=msg.message_id,
        media_groups=media_groups,
        finalizing_groups=list(finalizing)
    )

@router.message(Upload.uploading, F.photo | F.video | F.document)
async def handle_upload(m: Message, state: FSMContext):
    data = await state.get_data()
    step_i = data["step"]
    steps = data["steps"]
    cur = steps[step_i]

    if m.photo:
        file_info = {"type": "photo", "file_id": m.photo[-1].file_id}
    elif m.video:
        file_info = {"type": "video", "file_id": m.video.file_id}
    elif m.document:
        file_info = {"type": "document", "file_id": m.document.file_id}
    else:
        return

    if m.media_group_id:
        media_groups = data.get("media_groups", {})
        finalizing = set(data.get("finalizing_groups", []))
        gid = m.media_group_id

        media_groups.setdefault(gid, []).append(file_info)

        start_finalize = False
        if gid not in finalizing:
            finalizing.add(gid)
            start_finalize = True

        await state.update_data(media_groups=media_groups, finalizing_groups=list(finalizing))

        if start_finalize:
            asyncio.create_task(_finalize_media_group_for_photo(m, state, gid))
        return
    else:
        cur["files"].append(file_info)
        last_msg_id = data.get("last_msg")
        if last_msg_id:
            try:
                await m.bot.delete_message(chat_id=m.chat.id, message_id=last_msg_id)
            except:
                pass
        owner_id = data.get("owner_id")
        msg = await m.answer("Выберите действие", reply_markup=step_kb(cur["name"], has_files=True, user_id=owner_id))
        await state.update_data(steps=steps, last_msg=msg.message_id)

async def _finalize_media_group_for_add(m: Message, state: FSMContext, group_id: str):
    await asyncio.sleep(3.2)
    data = await state.get_data()
    files = data.get("files", [])
    media_groups = data.get("media_groups", {})
    finalizing = set(data.get("finalizing_groups", []))

    if group_id not in finalizing:
        return

    group = media_groups.pop(group_id, [])
    finalizing.discard(group_id)

    if group:
        files.extend(group)

    last_msg_id = data.get("last_msg")
    if last_msg_id:
        try:
            await m.bot.delete_message(chat_id=m.chat.id, message_id=last_msg_id)
        except:
            pass
    owner_id = data.get("owner_id")
    msg = await m.answer("Выберите действие", reply_markup=step_kb('', has_files=True, user_id=owner_id))
    await state.update_data(files=files, last_msg=msg.message_id, media_groups=media_groups, finalizing_groups=list(finalizing))

@router.message(AddPhoto.uploading, F.photo | F.video | F.document)
async def handle_addphoto_upload(m: Message, state: FSMContext):
    data = await state.get_data()
    files = data.get("files", [])

    if m.photo:
        file_info = {"type": "photo", "file_id": m.photo[-1].file_id}
    elif m.video:
        file_info = {"type": "video", "file_id": m.video.file_id}
    elif m.document:
        file_info = {"type": "document", "file_id": m.document.file_id}
    else:
        return

    if m.media_group_id:
        media_groups = data.get("media_groups", {})
        finalizing = set(data.get("finalizing_groups", []))
        gid = m.media_group_id

        media_groups.setdefault(gid, []).append(file_info)

        start_finalize = False
        if gid not in finalizing:
            finalizing.add(gid)
            start_finalize = True

        await state.update_data(media_groups=media_groups, finalizing_groups=list(finalizing))

        if start_finalize:
            asyncio.create_task(_finalize_media_group_for_add(m, state, gid))
        return
    else:
        files.append(file_info)
        last_msg_id = data.get("last_msg")
        if last_msg_id:
            try:
                await m.bot.delete_message(chat_id=m.chat.id, message_id=last_msg_id)
            except:
                pass
        owner_id = data.get("owner_id")
        msg = await m.answer("Выберите действие", reply_markup=step_kb('', has_files=True, user_id=owner_id))
        await state.update_data(files=files, last_msg=msg.message_id)

# ========== CALLBACKS ==========
@router.callback_query(F.data.startswith("save_"))
async def step_save(c: CallbackQuery, state: FSMContext):
    user_id = int(c.data.split("_")[-1])
    if c.from_user.id != user_id:
        await c.answer("Эта кнопка не для вас 😅", show_alert=True)
        return

    current_state = await state.get_state()
    data = await state.get_data()
    author = c.from_user.full_name or c.from_user.username or str(c.from_user.id)
    owner_id = data.get("owner_id")

    # === /addphoto ===
    if current_state == AddPhoto.uploading.state:
        obj = data["object"]
        obj_name = data.get("object_name") or ""
        files = data.get("files", [])
        if files:
            save_files(obj, "📎 Дополнительные фото", files, author)
            all_steps = get_files(obj)
            all_files_flat = [f for ff in all_steps.values() for f in ff]
            if all_files_flat:
                await post_archive_single_group(obj, obj_name, all_files_flat, author, data)
                delete_files_by_object(obj)
        await state.clear()
        try:
            await c.message.edit_text(f"✅ Файлы по объекту {obj} отправлены в архив.")
        except:
            pass
        await c.answer("Сохранено ✅")
        return

    # === /photo ===
    obj = data["object"]
    obj_name = data.get("object_name") or ""
    step_i = data["step"]
    steps = data["steps"]
    cur = steps[step_i]
    if cur["files"]:
        save_files(obj, cur["name"], cur["files"], author)
    step_i += 1
    await state.update_data(step=step_i, steps=steps)

    if step_i < len(steps):
        next_name = steps[step_i]["name"]
        try:
            await c.message.edit_text(next_name, reply_markup=step_kb(next_name, user_id=owner_id))
        except:
            pass
        await state.update_data(last_msg=c.message.message_id)
        await c.answer("Сохранено ✅")
    else:
        all_steps = get_files(obj)
        all_files_flat = [f for ff in all_steps.values() for f in ff]
        if all_files_flat:
            await post_archive_single_group(obj, obj_name, all_files_flat, author, data)
            delete_files_by_object(obj)
        mark_completed(obj, author)
        try:
            await c.message.edit_text(f"✅ Загрузка завершена для объекта {obj}. Файлы отправлены в архив.")
        except:
            pass
        await state.clear()
        await c.answer("Готово ✅")

@router.callback_query(F.data.startswith("skip_"))
async def step_skip(c: CallbackQuery, state: FSMContext):
    user_id = int(c.data.split("_")[-1])
    if c.from_user.id != user_id:
        await c.answer("Эта кнопка не для вас 😅", show_alert=True)
        return

    data = await state.get_data()
    obj = data["object"]
    obj_name = data.get("object_name") or ""
    author = c.from_user.full_name or c.from_user.username or str(c.from_user.id)
    step_i = data["step"] + 1
    steps = data["steps"]
    await state.update_data(step=step_i)

    if step_i >= len(steps):
        all_steps = get_files(obj)
        all_files_flat = [f for ff in all_steps.values() for f in ff]
        if all_files_flat:
            await post_archive_single_group(obj, obj_name, all_files_flat, author, data)
            delete_files_by_object(obj)
        mark_completed(obj, author)
        try:
            await c.message.edit_text(f"✅ Загрузка завершена для объекта {obj}. Файлы отправлены в архив.")
        except:
            pass
        await state.clear()
        await c.answer("Готово ✅")
        return

    next_name = steps[step_i]["name"]
    owner_id = data.get("owner_id")
    try:
        await c.message.edit_text(next_name, reply_markup=step_kb(next_name, user_id=owner_id))
    except:
        pass
    await state.update_data(last_msg=c.message.message_id)
    await c.answer("Пропущено ⏭️")

# ========== INFO ==========
@router.message(Info.waiting_object)
async def info_object(m: Message, state: FSMContext):
    objs = [x.strip() for x in m.text.split(",") if x.strip()]
    responses = []
    for obj in objs:
        info = get_object_info(m.chat.id, obj)
        if not info:
            responses.append(f"❌ Объект {obj} не найден в Excel, привязанном к этой группе.")
        elif "error" in info:
            responses.append(info["error"])
        else:
            responses.append(
                f"📋 Объект {info['id']}:\n"
                f"🏢 Потребитель: {info['consumer']}\n"
                f"📍 Объект: {info['object']}\n"
                f"🗺 Адрес: {info['address']}\n"
            )
    await m.answer("\n\n".join(responses))
    await state.clear()

# ========== ОТПРАВКА В АРХИВ С УЧЁТОМ МАРШРУТИЗАЦИИ ==========
async def post_archive_single_group(object_id: str, object_name: str, files: list, author: str, state_data: dict):
    """
    Отправка заголовка, медиа и документов в соответствующую архивную группу и тему.
    Соответствие определяется по TOPIC_MAP[work_chat_id][work_thread_id] (нормализация ключей есть).
    """
    try:
        work_chat_id = state_data.get("work_chat_id")
        work_thread_id = state_data.get("work_thread_id")

        mapping = mapping_lookup(work_chat_id, work_thread_id)
        if not mapping or not mapping.get("chat_id") or not mapping.get("thread_id"):
            # Сообщим прямо в рабочую тему, что маршрут не настроен
            try:
                await safe_call(bot.send_message(
                    chat_id=work_chat_id,
                    text=(
                        "⚠️ Не найдена архивная тема для отправки.\n\n"
                        f"Источник: chat_id={work_chat_id}, thread_id={work_thread_id}\n"
                        f"Объект #{object_id} «{object_name or ''}».\n"
                        f"Добавьте соответствие в TOPIC_MAP и повторите."
                    ),
                    message_thread_id=work_thread_id
                ))
            except Exception as ee:
                print(f"[archive warn] cannot notify source chat: {ee}")
            return

        archive_chat_id = mapping["chat_id"]
        archive_thread_id = mapping["thread_id"]

        title = object_name or ""
        header = (
            f"💾 ОБЪЕКТ #{object_id}\n"
            f"🏷️ {title}\n"
            f"👤 Исполнитель: {author}\n"
            f"🕒 {datetime.now().strftime('%d.%m.%Y %H:%M')}"
        )
        # Заголовок
        await safe_call(bot.send_message(
            archive_chat_id,
            header,
            message_thread_id=archive_thread_id
        ))

        # Медиа альбомами по 10
        batch = []
        for f in files:
            if f["type"] == "photo":
                batch.append(InputMediaPhoto(media=f["file_id"]))
            elif f["type"] == "video":
                batch.append(InputMediaVideo(media=f["file_id"]))
            elif f["type"] == "document":
                pass
            if len(batch) == 10:
                await safe_call(bot.send_media_group(
                    archive_chat_id, batch, message_thread_id=archive_thread_id
                ))
                batch = []
        if batch:
            await safe_call(bot.send_media_group(
                archive_chat_id, batch, message_thread_id=archive_thread_id
            ))

        # Документы по одному
        for d in [x for x in files if x["type"] == "document"]:
            await safe_call(bot.send_document(
                archive_chat_id, d["file_id"], message_thread_id=archive_thread_id
            ))
    except Exception as e:
        print(f"[archive_single_group] Ошибка при отправке в архив: {e}")

# ========== WEBHOOK / APP ==========
async def on_startup():
    init_db()
    load_settings()
    webhook_url = f"{WEBHOOK_URL}/{TOKEN}"
    await bot.delete_webhook(drop_pending_updates=True)
    await bot.set_webhook(webhook_url)
    await bot.set_my_commands([
        BotCommand(command="start", description="Запуск бота"),
        BotCommand(command="photo", description="Загрузка фото"),
        BotCommand(command="addphoto", description="Добавить фото"),
        BotCommand(command="info", description="Информация по объекту"),
        BotCommand(command="result", description="Завершённые загрузки"),
        BotCommand(command="admin", description="Админ-панель"),
    ])
    asyncio.create_task(keepalive())
    print("✅ Webhook установлен:", webhook_url)
    print("💡 KEEPALIVE активен каждые 4 минуты. Оставьте внешний пинг 5 минут.")

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
