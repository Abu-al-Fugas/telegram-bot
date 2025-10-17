import os
from datetime import datetime
from aiogram import Bot, Dispatcher, types
from aiogram.types import (
    ReplyKeyboardMarkup, KeyboardButton,
    InlineKeyboardMarkup, InlineKeyboardButton,
    InputMediaPhoto, InputMediaVideo, InputMediaDocument
)
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.memory import MemoryStorage
from aiohttp import web
import pandas as pd

# ================= НАСТРОЙКИ =================
TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
ARCHIVE_CHAT_ID = os.environ.get("ARCHIVE_CHAT_ID", "-1003160855229")
BOT_URL = f"https://telegram-bot-b6pn.onrender.com/{TOKEN}"
XLSX_URL = "https://github.com/Abu-al-Fugas/telegram-bot/blob/main/objects.xlsx"

bot = Bot(token=TOKEN)
storage = MemoryStorage()
dp = Dispatcher(storage=storage)

# ================= ГЛОБАЛЬНЫЕ ПЕРЕМЕННЫЕ =================
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

# ================== КНОПКИ ==================
def reply_keyboard():
    kb = ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    kb.add(
        KeyboardButton(text="/start"),
        KeyboardButton(text="/photo"),
        KeyboardButton(text="/addphoto"),
        KeyboardButton(text="/download"),
        KeyboardButton(text="/result"),
        KeyboardButton(text="/info")
    )
    return kb

def upload_keyboard(step_name):
    kb = InlineKeyboardMarkup(row_width=2)
    buttons = [InlineKeyboardButton(text="✅ Завершить", callback_data="upload_ok")]
    if step_name not in MANDATORY_STEPS:
        buttons.append(InlineKeyboardButton(text="➡️ След.", callback_data="upload_next"))
    buttons.append(InlineKeyboardButton(text="❌ Отмена", callback_data="upload_cancel"))
    kb.add(*buttons)
    return kb

# ================== ХЕЛПЕРЫ ==================
def make_key(chat_id, thread_id, user_id):
    return (chat_id, thread_id, user_id)

async def send_message(chat_id, text, reply_markup=None, thread_id=None):
    try:
        return await bot.send_message(chat_id, text, reply_markup=reply_markup, message_thread_id=thread_id)
    except Exception as e:
        print(f"[send_message] Ошибка: {e}")

async def delete_message(chat_id, msg_id):
    try:
        await bot.delete_message(chat_id, msg_id)
    except Exception:
        pass

# ================== КОМАНДЫ ==================
@dp.message(Command(commands=["start"]))
async def cmd_start(message: types.Message):
    text = (
        "🤖 Бот для управления объектами ИПУГ\n\n"
        "Доступные команды:\n"
        "/photo – загрузка файлов с чек-листом\n"
        "/addphoto – добавить файлы к существующему объекту\n"
        "/download – скачать файлы объекта из архива\n"
        "/result – список обработанных объектов\n"
        "/info – информация об объекте из XLSX"
    )
    await message.answer(text, reply_markup=reply_keyboard())

@dp.message(Command(commands=["photo"]))
async def cmd_photo(message: types.Message):
    key = make_key(message.chat.id, message.message_thread_id, message.from_user.id)
    user_state[key] = {"command": "await_object"}
    await send_message(message.chat.id, "Введите номер объекта для загрузки:", thread_id=message.message_thread_id)

@dp.message(Command(commands=["addphoto"]))
async def cmd_addphoto(message: types.Message):
    key = make_key(message.chat.id, message.message_thread_id, message.from_user.id)
    user_state[key] = {"command": "await_addphoto_object"}
    await send_message(message.chat.id, "Введите номер объекта, чтобы добавить фото:", thread_id=message.message_thread_id)

@dp.message(Command(commands=["download"]))
async def cmd_download(message: types.Message):
    key = make_key(message.chat.id, message.message_thread_id, message.from_user.id)
    user_state[key] = {"command": "await_download_object"}
    await send_message(message.chat.id, "Введите номер объекта для скачивания файлов:", thread_id=message.message_thread_id)

@dp.message(Command(commands=["result"]))
async def cmd_result(message: types.Message):
    if not objects_data:
        await send_message(message.chat.id, "📋 Нет завершённых загрузок.", thread_id=message.message_thread_id)
        return
    text = "✅ Завершённые загрузки:\n"
    for oid, data in objects_data.items():
        total_files = sum(len(s["files"]) for s in data["steps"])
        text += f"• Объект {oid}: {total_files} файлов\n"
    await send_message(message.chat.id, text, thread_id=message.message_thread_id)

@dp.message(Command(commands=["info"]))
async def cmd_info(message: types.Message):
    key = make_key(message.chat.id, message.message_thread_id, message.from_user.id)
    user_state[key] = {"command": "await_info_object"}
    await send_message(message.chat.id, f"Введите номер объекта, чтобы получить информацию из XLSX:\n{XLSX_URL}",
                       thread_id=message.message_thread_id)

# ================== ОБРАБОТКА ТЕКСТА ==================
@dp.message()
async def handle_text(message: types.Message):
    key = make_key(message.chat.id, message.message_thread_id, message.from_user.id)
    state = user_state.get(key)
    if not state:
        return

    text = message.text.strip()

    # Начало загрузки нового объекта
    if state.get("command") == "await_object":
        object_id = text
        steps = [{"name": s, "files": []} for s in UPLOAD_STEPS]
        user_state[key] = {
            "command": "upload_steps",
            "object_id": object_id,
            "steps": steps,
            "step_index": 0,
            "chat_id": message.chat.id,
            "thread_id": message.message_thread_id
        }
        await send_upload_step(key)

    # Добавление фото к существующему объекту
    elif state.get("command") == "await_addphoto_object":
        object_id = text
        user_state[key] = {
            "command": "add_photos",
            "object_id": object_id,
            "files": [],
            "chat_id": message.chat.id,
            "thread_id": message.message_thread_id
        }
        kb = InlineKeyboardMarkup(row_width=2).add(
            InlineKeyboardButton(text="✅ Завершить", callback_data="addphoto_done"),
            InlineKeyboardButton(text="❌ Отмена", callback_data="upload_cancel")
        )
        await send_message(message.chat.id, f"📸 Отправьте фото для объекта {object_id}.", reply_markup=kb,
                           thread_id=message.message_thread_id)

    # Скачивание файлов объекта
    elif state.get("command") == "await_download_object":
        object_id = text
        if object_id not in objects_data:
            await send_message(message.chat.id, f"❌ Объект {object_id} не найден.", thread_id=message.message_thread_id)
            user_state.pop(key, None)
            return
        data = objects_data[object_id]["steps"]
        for step in data:
            for f in step["files"]:
                if f["type"] == "photo":
                    await bot.send_photo(message.chat.id, f["file_id"])
                elif f["type"] == "document":
                    await bot.send_document(message.chat.id, f["file_id"])
                elif f["type"] == "video":
                    await bot.send_video(message.chat.id, f["file_id"])
        user_state.pop(key, None)

    # Получение информации из XLSX
    elif state.get("command") == "await_info_object":
        object_id = text
        try:
            df = pd.read_excel("objects.xlsx")
            if object_id in df["object_id"].astype(str).values:
                row = df[df["object_id"].astype(str) == object_id].iloc[0]
                info_text = "\n".join([f"{col}: {row[col]}" for col in df.columns])
                await send_message(message.chat.id, info_text, thread_id=message.message_thread_id)
            else:
                await send_message(message.chat.id, f"❌ Объект {object_id} не найден в XLSX.", thread_id=message.message_thread_id)
        except Exception as e:
            await send_message(message.chat.id, f"Ошибка чтения XLSX: {e}", thread_id=message.message_thread_id)
        user_state.pop(key, None)

# ================== ОБРАБОТКА ФАЙЛОВ ==================
@dp.message(content_types=[types.ContentType.PHOTO, types.ContentType.DOCUMENT, types.ContentType.VIDEO])
async def handle_files(message: types.Message):
    key = make_key(message.chat.id, message.message_thread_id, message.from_user.id)
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

    if state.get("command") == "upload_steps":
        step = state["steps"][state["step_index"]]
        step["files"].append(file_info)
        if "last_message_id" in state:
            await delete_message(state["chat_id"], state["last_message_id"])
        msg = await send_message(state["chat_id"], "Выберите действие:", reply_markup=upload_keyboard(step["name"]),
                                 thread_id=state["thread_id"])
        state["last_message_id"] = msg.message_id

    elif state.get("command") == "add_photos":
        state["files"].append(file_info)

# ================== CALLBACK ==================
@dp.callback_query()
async def handle_callback(call: types.CallbackQuery):
    key = make_key(call.message.chat.id, call.message.message_thread_id, call.from_user.id)
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
        await delete_message(call.message.chat.id, call.message.message_id)
        await send_message(call.message.chat.id, f"❌ Загрузка для объекта {obj} отменена")
        await call.answer("Отменено")
    elif call.data == "addphoto_done":
        object_id = state["object_id"]
        if object_id not in objects_data:
            objects_data[object_id] = {"steps": []}
        await save_to_archive(object_id, [{"name": "Дополнительные файлы", "files": state["files"]}], append=True)
        user_state.pop(key, None)
        await delete_message(call.message.chat.id, call.message.message_id)
        await send_message(call.message.chat.id, f"✅ Дополнительные файлы для объекта {object_id} сохранены.")
        await call.answer("Готово ✅")

# ================== ПРОДВИЖЕНИЕ ШАГОВ ==================
async def send_upload_step(key):
    state = user_state[key]
    step = state["steps"][state["step_index"]]
    if "last_message_id" in state:
        await delete_message(state["chat_id"], state["last_message_id"])
    msg = await send_message(state["chat_id"], f"📸 Отправьте {step['name']}", reply_markup=upload_keyboard(step["name"]),
                             thread_id=state["thread_id"])
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
        await send_message(state["chat_id"], f"✅ Загрузка завершена для объекта {object_id}")
    else:
        await send_upload_step(key)

# ================== СОХРАНЕНИЕ В АРХИВ ==================
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

# ================== WEBHOOK ==================
async def handle_webhook(request: web.Request):
    try:
        update = types.Update(**await request.json())
        await dp.feed_update(bot, update)
        return web.Response(text="OK")
    except Exception as e:
        print(f"[handle_webhook] Ошибка: {e}")
        return web.Response(text="Error", status=500)

app = web.Application()
app.router.add_post(f"/{TOKEN}", handle_webhook)

if __name__ == "__main__":
    import logging
    logging.basicConfig(level=logging.INFO)
    print("🚀 Бот запущен...")
    # Устанавливаем webhook
    import asyncio
    async def on_startup():
        await bot.delete_webhook()
        await bot.set_webhook(BOT_URL)

    loop = asyncio.get_event_loop()
    loop.run_until_complete(on_startup())
    web.run_app(app, host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))
