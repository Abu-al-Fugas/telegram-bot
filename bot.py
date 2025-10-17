import os
import asyncio
from datetime import datetime
from aiogram import Bot, Dispatcher, types
from aiogram.types import (
    InlineKeyboardMarkup, InlineKeyboardButton,
    ReplyKeyboardMarkup, KeyboardButton,
    InputMediaPhoto, InputMediaVideo, InputMediaDocument
)
from aiogram.dispatcher.fsm.storage.memory import MemoryStorage
from aiohttp import web
import pandas as pd

# ========== НАСТРОЙКИ ==========
TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
ARCHIVE_CHAT_ID = os.environ.get("ARCHIVE_CHAT_ID", "-1003160855229")
WEBHOOK_PATH = f"/{TOKEN}"
WEBHOOK_URL = f"https://telegram-bot-b6pn.onrender.com{WEBHOOK_PATH}"
PORT = int(os.environ.get("PORT", 10000))

bot = Bot(token=TOKEN)
dp = Dispatcher(storage=MemoryStorage())

# ========== ГЛОБАЛЬНЫЕ ПЕРЕМЕННЫЕ ==========
user_state = {}
objects_data = {}

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

OBJECTS_XLSX_URL = "https://github.com/Abu-al-Fugas/telegram-bot/blob/main/objects.xlsx?raw=true"

# ========== ХЕЛПЕРЫ ==========
def make_key(chat_id, thread_id, user_id):
    return (chat_id, thread_id, user_id)

def upload_keyboard(step_name):
    kb = InlineKeyboardMarkup(row_width=2)
    buttons = []
    if step_name not in MANDATORY_STEPS:
        buttons.append(InlineKeyboardButton("➡️ След.", callback_data="upload_next"))
    buttons.append(InlineKeyboardButton("❌ Отмена", callback_data="upload_cancel"))
    buttons.append(InlineKeyboardButton("✅ OK", callback_data="upload_ok"))
    kb.add(*buttons)
    return kb

def send_reply_keyboard():
    kb = ReplyKeyboardMarkup(resize_keyboard=True)
    kb.add(
        KeyboardButton("/start"),
        KeyboardButton("/photo"),
        KeyboardButton("/addphoto")
    )
    kb.add(
        KeyboardButton("/download"),
        KeyboardButton("/result"),
        KeyboardButton("/info")
    )
    return kb

async def send_message(chat_id, text, reply_markup=None, thread_id=None):
    try:
        return await bot.send_message(chat_id, text, reply_markup=reply_markup)
    except Exception as e:
        print(f"[send_message] Ошибка: {e}")

async def delete_message(chat_id, msg_id):
    try:
        await bot.delete_message(chat_id, msg_id)
    except Exception:
        pass

# ========== КОМАНДЫ ==========
@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    text = (
        "🤖 Бот для управления объектами ИПУГ\n\n"
        "Доступные команды:\n"
        "/photo – загрузка файлов с чек-листом\n"
        "/addphoto – добавить файлы к существующему объекту\n"
        "/download – скачать файлы объекта из архива\n"
        "/result – список обработанных объектов\n"
        "/info – информация об объекте из objects.xlsx"
    )
    await message.answer(text, reply_markup=send_reply_keyboard())

@dp.message(Command("photo"))
async def cmd_photo(message: types.Message):
    key = make_key(message.chat.id, message.message_thread_id or 0, message.from_user.id)
    user_state[key] = {"command": "await_object"}
    await message.answer("Введите номер объекта для загрузки:", reply_markup=send_reply_keyboard())

@dp.message(Command("addphoto"))
async def cmd_addphoto(message: types.Message):
    key = make_key(message.chat.id, message.message_thread_id or 0, message.from_user.id)
    user_state[key] = {"command": "await_addphoto_object"}
    await message.answer("Введите номер объекта, чтобы добавить фото:", reply_markup=send_reply_keyboard())

@dp.message(Command("download"))
async def cmd_download(message: types.Message):
    key = make_key(message.chat.id, message.message_thread_id or 0, message.from_user.id)
    user_state[key] = {"command": "await_download_object"}
    await message.answer("Введите номер объекта для скачивания файлов:", reply_markup=send_reply_keyboard())

@dp.message(Command("result"))
async def cmd_result(message: types.Message):
    if not objects_data:
        await message.answer("📋 Нет завершённых загрузок.", reply_markup=send_reply_keyboard())
        return
    text = "✅ Завершённые загрузки:\n"
    for oid, data in objects_data.items():
        total_files = sum(len(s["files"]) for s in data["steps"])
        text += f"• Объект {oid}: {total_files} файлов\n"
    await message.answer(text, reply_markup=send_reply_keyboard())

@dp.message(Command("info"))
async def cmd_info(message: types.Message):
    key = make_key(message.chat.id, message.message_thread_id or 0, message.from_user.id)
    user_state[key] = {"command": "await_info_object"}
    await message.answer("Введите номер объекта для информации:", reply_markup=send_reply_keyboard())

# ========== ОБРАБОТКА ТЕКСТА ==========
@dp.message()
async def handle_text(message: types.Message):
    key = make_key(message.chat.id, message.message_thread_id or 0, message.from_user.id)
    state = user_state.get(key)
    if not state:
        return

    text = message.text.strip()

    if state["command"] == "await_object":
        object_id = text
        steps = [{"name": s, "files": []} for s in UPLOAD_STEPS]
        user_state[key] = {
            "command": "upload_steps",
            "object_id": object_id,
            "steps": steps,
            "step_index": 0,
            "chat_id": message.chat.id
        }
        await send_upload_step(key)

    elif state["command"] == "await_addphoto_object":
        object_id = text
        user_state[key] = {
            "command": "add_photos",
            "object_id": object_id,
            "files": [],
            "chat_id": message.chat.id
        }
        await message.answer(f"📸 Отправьте фото для объекта {object_id}. Когда закончите, нажмите ✅ Завершить.",
                             reply_markup=InlineKeyboardMarkup().add(
                                 InlineKeyboardButton("✅ Завершить", callback_data="addphoto_done"),
                                 InlineKeyboardButton("❌ Отмена", callback_data="upload_cancel")
                             ))

    elif state["command"] == "await_download_object":
        object_id = text
        if object_id not in objects_data:
            await message.answer(f"❌ Объект {object_id} не найден", reply_markup=send_reply_keyboard())
        else:
            steps = objects_data[object_id]["steps"]
            for s in steps:
                media = []
                for f in s["files"][:10]:
                    if f["type"] == "photo":
                        media.append(InputMediaPhoto(f["file_id"]))
                    elif f["type"] == "video":
                        media.append(InputMediaVideo(f["file_id"]))
                    elif f["type"] == "document":
                        media.append(InputMediaDocument(f["file_id"]))
                if media:
                    await bot.send_media_group(message.chat.id, media)
            await message.answer(f"✅ Файлы объекта {object_id} отправлены", reply_markup=send_reply_keyboard())
        user_state.pop(key, None)

    elif state["command"] == "await_info_object":
        try:
            df = pd.read_excel(OBJECTS_XLSX_URL)
            row = df[df['object_id'] == text]
            if row.empty:
                await message.answer(f"❌ Объект {text} не найден", reply_markup=send_reply_keyboard())
            else:
                info = row.to_string(index=False)
                await message.answer(f"ℹ️ Информация по объекту:\n{info}", reply_markup=send_reply_keyboard())
        except Exception as e:
            await message.answer(f"Ошибка при получении информации: {e}", reply_markup=send_reply_keyboard())
        user_state.pop(key, None)

# ========== ОБРАБОТКА ФАЙЛОВ ==========
@dp.message(lambda message: message.photo or message.document or message.video)
async def handle_files(message: types.Message):
    key = make_key(message.chat.id, message.message_thread_id or 0, message.from_user.id)
    state = user_state.get(key)
    if not state:
        return

    file_info = {}
    if message.photo:
        file_info = {"type": "photo", "file_id": message.photo[-1].file_id}
    elif message.document:
        file_info = {"type": "document", "file_id": message.document.file_id}
    elif message.video:
        file_info = {"type": "video", "file_id": message.video.file_id}

    if state["command"] == "upload_steps":
        step = state["steps"][state["step_index"]]
        step["files"].append(file_info)
        msg = await message.answer("Выберите действие:", reply_markup=upload_keyboard(step["name"]))
        state["last_message_id"] = msg.message_id

    elif state["command"] == "add_photos":
        state["files"].append(file_info)

# ========== CALLBACKS ==========
@dp.callback_query(lambda c: c.data.startswith("upload_") or c.data.startswith("addphoto_"))
async def handle_callback(call: types.CallbackQuery):
    key = make_key(call.message.chat.id, call.message.message_thread_id or 0, call.from_user.id)
    state = user_state.get(key)
    if not state:
        await call.answer("Нет активной загрузки")
        return

    if call.data == "upload_ok":
        await advance_step(key)
        await call.answer("✅ Шаг завершён")
    elif call.data == "upload_next":
        await advance_step(key, skip=True)
        await call.answer("➡️ Пропущено")
    elif call.data == "upload_cancel":
        obj = state.get("object_id", "")
        user_state.pop(key, None)
        await call.message.delete()
        await send_message(call.message.chat.id, f"❌ Загрузка для объекта {obj} отменена", reply_markup=send_reply_keyboard())
        await call.answer("Отменено")
    elif call.data == "addphoto_done":
        object_id = state["object_id"]
        if object_id not in objects_data:
            objects_data[object_id] = {"steps": []}
        await save_to_archive(object_id, [{"name": "Дополнительные файлы", "files": state["files"]}], append=True)
        user_state.pop(key, None)
        await call.message.delete()
        await send_message(call.message.chat.id, f"✅ Дополнительные файлы для объекта {object_id} сохранены.", reply_markup=send_reply_keyboard())
        await call.answer("Готово ✅")

# ========== ПРОДВИЖЕНИЕ ШАГОВ ==========
async def send_upload_step(key):
    state = user_state[key]
    step = state["steps"][state["step_index"]]
    msg = await send_message(state["chat_id"], f"📸 Шаг: {step['name']}\nВыберите действие:",
                             reply_markup=upload_keyboard(step["name"]))
    state["last_message_id"] = msg.message_id

async def advance_step(key, skip=False):
    state = user_state[key]
    state["step_index"] += 1
    if state["step_index"] >= len(state["steps"]):
        object_id = state["object_id"]
        all_steps = state["steps"]
        await save_to_archive(object_id, all_steps)
        objects_data[object_id] = {"steps": all_steps}
        user_state.pop(key, None)
        await send_message(state["chat_id"], f"✅ Загрузка завершена для объекта {object_id}", reply_markup=send_reply_keyboard())
    else:
        await send_upload_step(key)

# ========== СОХРАНЕНИЕ В АРХИВ ==========
async def save_to_archive(object_id, steps, append=False):
    try:
        info_text = f"💾 ОБЪЕКТ #{object_id}\n🕒 {datetime.now().strftime('%d.%m.%Y %H:%M')}"
        await bot.send_message(ARCHIVE_CHAT_ID, info_text)
        for s in steps:
            files = s["files"]
            if not files:
                continue
            media = []
            for f in files[:50]:
                if f["type"] == "photo":
                    media.append(InputMediaPhoto(f["file_id"]))
                elif f["type"] == "video":
                    media.append(InputMediaVideo(f["file_id"]))
                elif f["type"] == "document":
                    media.append(InputMediaDocument(f["file_id"]))
            if media:
                await bot.send_media_group(ARCHIVE_CHAT_ID, media)
    except Exception as e:
        print(f"[save_to_archive] Ошибка: {e}")

# ========== WEBHOOK ==========
async def handle_webhook(request: web.Request):
    update = types.Update(**await request.json())
    await dp.feed_update(bot, update)
    return web.Response(text="OK")

app = web.Application()
app.router.add_post(WEBHOOK_PATH, handle_webhook)
app.router.add_get("/", lambda request: web.Response(text="🤖 Бот работает"))

async def on_startup(app):
    await bot.set_webhook(WEBHOOK_URL)

async def on_shutdown(app):
    await bot.delete_webhook()

if __name__ == "__main__":
    web.run_app(app, host="0.0.0.0", port=PORT)
