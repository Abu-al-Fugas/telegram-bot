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
        conn.execute("""CREATE TABLE IF NOT EXISTS completed(
            object_id TEXT PRIMARY KEY,
            author TEXT,
            completed_at TEXT
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
def main_kb():
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="/addphoto"), KeyboardButton(text="/info"), KeyboardButton(text="/photo")]],
        resize_keyboard=True
    )

def step_kb(step_name, has_files=False):
    # Особый случай: первый экран /addphoto — только Отмена
    if step_name == "" and not has_files:
        buttons = [[InlineKeyboardButton(text="❌ Отмена", callback_data="cancel")]]
        return InlineKeyboardMarkup(inline_keyboard=buttons)

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

# ========== KEEPALIVE ==========
async def keepalive():
    while True:
        try:
            async with aiohttp.ClientSession() as s:
                await s.get(WEBHOOK_URL)
        except:
            pass
        await asyncio.sleep(240)  # 4 минуты

# ========== КОМАНДЫ ==========
@router.message(Command("start"))
async def cmd_start(m: Message):
    await m.answer(
        "👋 Привет! Это бот для загрузки фото по объектам счётчиков газа.\n\n"
        "📸 /photo — новая загрузка\n"
        "📎 /addphoto — добавить фото\n"
        "ℹ️ /info — информация по объектам\n"
        "⚙️ Работает только в рабочей теме.",
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
    await m.answer("📝 Введите номер объекта (для добавления файлов):")

@router.message(Command("info"))
async def cmd_info(m: Message, state: FSMContext):
    await state.set_state(Info.waiting_object)
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
    ok, name = check_object_excel(obj)
    if ok:
        await state.update_data(object=obj, object_name=name, step=0, steps=[{"name": s, "files": []} for s in UPLOAD_STEPS])
        await state.set_state(Upload.confirming)
        await m.answer(f"Подтвердите объект:\n\n🆔 {obj}\n🏷️ {name}", reply_markup=confirm_kb("photo"))
    elif ok is False:
        await m.answer(f"❌ Объект {obj} не найден.")
        await state.clear()
    else:
        await m.answer(f"❌ Ошибка чтения objects.xlsx: {name or 'неизвестно'}")
        await state.clear()

@router.message(AddPhoto.waiting_object)
async def check_add_object(m: Message, state: FSMContext):
    obj = m.text.strip()
    ok, name = check_object_excel(obj)
    if ok:
        await state.update_data(object=obj, object_name=name, files=[])
        await state.set_state(AddPhoto.confirming)
        await m.answer(f"Подтвердите объект (добавление файлов):\n\n🆔 {obj}\n🏷️ {name}", reply_markup=confirm_kb("add"))
    elif ok is False:
        await m.answer(f"❌ Объект {obj} не найден.")
        await state.clear()
    else:
        await m.answer(f"❌ Ошибка чтения objects.xlsx: {name or 'неизвестно'}")
        await state.clear()

# ===== Подтверждение: /photo =====
@router.callback_query(F.data == "photo_confirm_yes")
async def photo_confirm_yes(c: CallbackQuery, state: FSMContext):
    await state.set_state(Upload.uploading)
    data = await state.get_data()
    step0 = data["steps"][0]["name"]
    await c.message.edit_text(step0, reply_markup=step_kb(step0))
    await state.update_data(last_msg=c.message.message_id)
    try:
        await c.answer("Подтверждено")
    except:
        pass

# ===== Подтверждение: /addphoto =====
@router.callback_query(F.data == "add_confirm_yes")
async def add_confirm_yes(c: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    obj = data["object"]
    await state.set_state(AddPhoto.uploading)
    await c.message.edit_text(f"📸 Отправьте дополнительные файлы для объекта №{obj}.", reply_markup=step_kb('', False))
    await state.update_data(last_msg=c.message.message_id)
    try:
        await c.answer("Подтверждено")
    except:
        pass

# ====== ОТМЕНА ======
@router.callback_query(F.data == "cancel")
async def cancel_anywhere(c: CallbackQuery, state: FSMContext):
    await state.clear()
    try:
        await c.message.edit_text("❌ Действие отменено.", reply_markup=None)
    except:
        pass
    try:
        await c.answer("Отменено")
    except:
        pass

@router.callback_query(F.data.in_({"photo_confirm_no", "add_confirm_no"}))
async def cancel_confirm(c: CallbackQuery, state: FSMContext):
    await state.clear()
    try:
        await c.message.edit_text("❌ Действие отменено.", reply_markup=None)
    except:
        pass
    try:
        await c.answer("Отменено")
    except:
        pass

# ========== ПРИЁМ ФАЙЛОВ ==========
# --- помощник: финализация media_group для /photo с анти-дубликатом ---
async def _finalize_media_group_for_photo(m: Message, state: FSMContext, group_id: str):
    await asyncio.sleep(1.2)  # дождаться всех сообщений альбома
    data = await state.get_data()
    step_i = data["step"]
    steps = data["steps"]
    cur = steps[step_i]

    media_groups = data.get("media_groups", {})
    finalizing = set(data.get("finalizing_groups", []))

    # двойная проверка: если сейчас эта группа ещё помечена как финализируемая — мы её финализируем
    if group_id not in finalizing:
        return

    group = media_groups.pop(group_id, [])
    # снимаем флаг финализации
    finalizing.discard(group_id)

    if group:
        cur["files"].extend(group)

    # заменить/создать одно сообщение с кнопками
    last_msg_id = data.get("last_msg")
    if last_msg_id:
        try:
            await m.bot.delete_message(chat_id=m.chat.id, message_id=last_msg_id)
        except:
            pass
    msg = await m.answer("Выберите действие", reply_markup=step_kb(cur["name"], has_files=True))
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

        # если финализация ещё не запущена, помечаем и запускаем
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
        msg = await m.answer("Выберите действие", reply_markup=step_kb(cur["name"], has_files=True))
        await state.update_data(steps=steps, last_msg=msg.message_id)

# --- помощник: финализация media_group для /addphoto с анти-дубликатом ---
async def _finalize_media_group_for_add(m: Message, state: FSMContext, group_id: str):
    await asyncio.sleep(1.2)
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
    msg = await m.answer("Выберите действие", reply_markup=step_kb('', has_files=True))
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
        msg = await m.answer("Выберите действие", reply_markup=step_kb('', has_files=True))
        await state.update_data(files=files, last_msg=msg.message_id)

# ========== CALLBACKS ==========
@router.callback_query(F.data == "save")
async def step_save(c: CallbackQuery, state: FSMContext):
    current_state = await state.get_state()
    data = await state.get_data()
    author = c.from_user.full_name or c.from_user.username or str(c.from_user.id)

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
                await post_archive_single_group(obj, obj_name, all_files_flat, author)
                delete_files_by_object(obj)
        await state.clear()
        try:
            await c.message.edit_text(f"✅ Файлы по объекту {obj} отправлены в архив.")
        except:
            pass
        try:
            await c.answer("Сохранено ✅")
        except:
            pass
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
            await c.message.edit_text(next_name, reply_markup=step_kb(next_name))
        except:
            pass
        await state.update_data(last_msg=c.message.message_id)
        try:
            await c.answer("Сохранено ✅")
        except:
            pass
    else:
        all_steps = get_files(obj)
        all_files_flat = [f for ff in all_steps.values() for f in ff]
        if all_files_flat:
            await post_archive_single_group(obj, obj_name, all_files_flat, author)
            delete_files_by_object(obj)
        mark_completed(obj, author)
        try:
            await c.message.edit_text(f"✅ Загрузка завершена для объекта {obj}. Файлы отправлены в архив.")
        except:
            pass
        await state.clear()
        try:
            await c.answer("Готово ✅")
        except:
            pass

@router.callback_query(F.data == "skip")
async def step_skip(c: CallbackQuery, state: FSMContext):
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
            await post_archive_single_group(obj, obj_name, all_files_flat, author)
            delete_files_by_object(obj)
        mark_completed(obj, author)
        try:
            await c.message.edit_text(f"✅ Загрузка завершена для объекта {obj}. Файлы отправлены в архив.")
        except:
            pass
        await state.clear()
        try:
            await c.answer("Готово ✅")
        except:
            pass
        return

    next_name = steps[step_i]["name"]
    try:
        await c.message.edit_text(next_name, reply_markup=step_kb(next_name))
    except:
        pass
    await state.update_data(last_msg=c.message.message_id)
    try:
        await c.answer("Пропущено")
    except:
        pass

# ========== INFO ==========
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

# ========== ОТПРАВКА В АРХИВ ==========
async def post_archive_single_group(object_id: str, object_name: str, files: list, author: str):
    try:
        title = object_name or ""
        header = (
            f"💾 ОБЪЕКТ #{object_id}\n"
            f"🏷️ {title}\n"
            f"👤 Исполнитель: {author}\n"
            f"🕒 {datetime.now().strftime('%d.%m.%Y %H:%M')}"
        )
        await safe_call(bot.send_message(ARCHIVE_CHAT_ID, header))
        batch = []
        for f in files:
            if f["type"] == "photo":
                batch.append(InputMediaPhoto(media=f["file_id"]))
            elif f["type"] == "video":
                batch.append(InputMediaVideo(media=f["file_id"]))
            elif f["type"] == "document":
                pass
            if len(batch) == 10:
                await safe_call(bot.send_media_group(ARCHIVE_CHAT_ID, batch))
                batch = []
        if batch:
            await safe_call(bot.send_media_group(ARCHIVE_CHAT_ID, batch))
        for d in [x for x in files if x["type"] == "document"]:
            await safe_call(bot.send_document(ARCHIVE_CHAT_ID, d["file_id"]))
    except Exception as e:
        print(f"[archive_single_group] Ошибка при отправке в архив: {e}")

# ========== WEBHOOK / APP ==========
async def on_startup():
    init_db()
    webhook_url = f"{WEBHOOK_URL}/{TOKEN}"
    await bot.delete_webhook(drop_pending_updates=True)
    await bot.set_webhook(webhook_url)
    await bot.set_my_commands([
        BotCommand(command="addphoto", description="Добавить фото"),
        BotCommand(command="info", description="Информация об объекте"),
        BotCommand(command="photo", description="Загрузить фото по объекту"),
        BotCommand(command="result", description="Завершённые загрузки"),
        BotCommand(command="start", description="Перезапуск бота"),
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
