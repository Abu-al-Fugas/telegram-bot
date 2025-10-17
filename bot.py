import os
import asyncio
from datetime import datetime
from aiohttp import web
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, KeyboardButton, InputMediaPhoto, InputMediaVideo, InputMediaDocument

# ========== НАСТРОЙКИ ==========
TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
ARCHIVE_CHAT_ID = int(os.environ.get("ARCHIVE_CHAT_ID", "-1003160855229"))
BOT_URL = f"https://telegram-bot-b6pn.onrender.com/{TOKEN}"  # твой webhook

bot = Bot(token=TOKEN)
dp = Dispatcher()
app = web.Application()

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

# ========== ХЕЛПЕРЫ ==========
def make_key(chat_id, thread_id, user_id):
    return (chat_id, thread_id, user_id)

def reply_keyboard():
    kb = ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    kb.add(
        KeyboardButton("/start"),
        KeyboardButton("/photo"),
        KeyboardButton("/addphoto"),
        KeyboardButton("/download"),
        KeyboardButton("/result"),
        KeyboardButton("/info")
    )
    return kb

def upload_keyboard(step_name):
    kb = InlineKeyboardMarkup(row_width=2)
    buttons = []
    if step_name in MANDATORY_STEPS:
        buttons.append(InlineKeyboardButton("✅ Завершить", callback_data="upload_ok"))
    else:
        buttons.append(InlineKeyboardButton("➡️ След.", callback_data="upload_next"))
    buttons.append(InlineKeyboardButton("❌ Отмена", callback_data="upload_cancel"))
    kb.add(*buttons)
    return kb

async def send_message(chat_id, text, reply_markup=None):
    try:
        return await bot.send_message(chat_id, text, reply_markup=reply_markup)
    except Exception as e:
        print(f"[send_message] Ошибка: {e}")

# ========== КОМАНДЫ ==========
@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    text = (
        "🤖 Бот для управления объектами ИПУГ\n\n"
        "Основные команды доступны внизу клавиатуры.\n"
        "Для загрузки файлов используйте /photo или /addphoto."
    )
    await message.answer(text, reply_markup=reply_keyboard())

@dp.message(Command("photo"))
async def cmd_photo(message: types.Message):
    key = make_key(message.chat.id, 0, message.from_user.id)
    user_state[key] = {"command": "await_object"}
    await send_message(message.chat.id, "Введите номер объекта для загрузки:")

@dp.message(Command("addphoto"))
async def cmd_addphoto(message: types.Message):
    key = make_key(message.chat.id, 0, message.from_user.id)
    user_state[key] = {"command": "await_addphoto_object"}
    await send_message(message.chat.id, "Введите номер объекта, чтобы добавить фото:")

@dp.message(Command("download"))
async def cmd_download(message: types.Message):
    key = make_key(message.chat.id, 0, message.from_user.id)
    user_state[key] = {"command": "await_download_object"}
    await send_message(message.chat.id, "Введите номер объекта для скачивания файлов:")

@dp.message(Command("result"))
async def cmd_result(message: types.Message):
    if not objects_data:
        await send_message(message.chat.id, "📋 Нет завершённых загрузок.")
        return
    text = "✅ Завершённые загрузки:\n"
    for oid, data in objects_data.items():
        total_files = sum(len(s["files"]) for s in data["steps"])
        text += f"• Объект {oid}: {total_files} файлов\n"
    await send_message(message.chat.id, text)

@dp.message(Command("info"))
async def cmd_info(message: types.Message):
    await send_message(message.chat.id, "ℹ️ Для информации по объектам см. файл objects.xlsx: https://github.com/Abu-al-Fugas/telegram-bot/blob/main/objects.xlsx")

# ========== ОБРАБОТКА ТЕКСТА ==========
@dp.message()
async def handle_text(message: types.Message):
    key = make_key(message.chat.id, 0, message.from_user.id)
    state = user_state.get(key)
    if not state:
        return

    # начало загрузки
    if state["command"] == "await_object":
        object_id = message.text.strip()
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
        object_id = message.text.strip()
        user_state[key] = {
            "command": "add_photos",
            "object_id": object_id,
            "files": [],
            "chat_id": message.chat.id
        }
        await send_message(message.chat.id, f"📸 Отправьте фото для объекта {object_id}.", reply_markup=upload_keyboard("Дополнительные фотографии"))

    elif state["command"] == "await_download_object":
        object_id = message.text.strip()
        if object_id not in objects_data:
            await send_message(message.chat.id, f"❌ Объект {object_id} не найден")
            user_state.pop(key, None)
            return
        for step in objects_data[object_id]["steps"]:
            media = []
            for f in step["files"][:10]:  # ограничим до 10 файлов в одной группе
                if f["type"] == "photo":
                    media.append(InputMediaPhoto(f["file_id"]))
                elif f["type"] == "video":
                    media.append(InputMediaVideo(f["file_id"]))
                elif f["type"] == "document":
                    media.append(InputMediaDocument(f["file_id"]))
            if media:
                await bot.send_media_group(message.chat.id, media)
        user_state.pop(key, None)

# ========== ОБРАБОТКА ФАЙЛОВ ==========
@dp.message()
async def handle_files(message: types.Message):
    key = make_key(message.chat.id, 0, message.from_user.id)
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
        await send_message(message.chat.id, "Выберите действие:", reply_markup=upload_keyboard(step["name"]))
    elif state["command"] == "add_photos":
        state["files"].append(file_info)
        await send_message(message.chat.id, "Файл добавлен. Нажмите ✅ Завершить или ❌ Отмена", reply_markup=upload_keyboard("Дополнительные фотографии"))

# ========== CALLBACKS ==========
@dp.callback_query()
async def handle_callback(callback: types.CallbackQuery):
    key = make_key(callback.message.chat.id, 0, callback.from_user.id)
    state = user_state.get(key)
    if not state:
        await callback.answer("Нет активной загрузки")
        return

    if callback.data == "upload_ok" or callback.data == "upload_next":
        await advance_step(key)
        await callback.answer("✅ Шаг завершён")
    elif callback.data == "upload_cancel":
        obj = state.get("object_id", "")
        user_state.pop(key, None)
        await callback.message.delete()
        await send_message(callback.message.chat.id, f"❌ Загрузка для объекта {obj} отменена")
        await callback.answer("Отменено")

# ========== ПРОДВИЖЕНИЕ ШАГОВ ==========
async def send_upload_step(key):
    state = user_state[key]
    step = state["steps"][state["step_index"]]
    await send_message(state["chat_id"], f"📸 Шаг: {step['name']}\nВыберите действие:", reply_markup=upload_keyboard(step["name"]))

async def advance_step(key):
    state = user_state[key]
    state["step_index"] += 1
    if state["step_index"] >= len(state["steps"]):
        object_id = state["object_id"]
        all_steps = state["steps"]
        save_to_archive(object_id, all_steps)
        objects_data[object_id] = {"steps": all_steps}
        user_state.pop(key, None)
        await send_message(state["chat_id"], f"✅ Загрузка завершена для объекта {object_id}")
    else:
        await send_upload_step(key)

# ========== СОХРАНЕНИЕ В АРХИВ ==========
def save_to_archive(object_id, steps):
    try:
        info_text = f"💾 ОБЪЕКТ #{object_id}\n🕒 {datetime.now().strftime('%d.%m.%Y %H:%M')}"
        asyncio.create_task(bot.send_message(ARCHIVE_CHAT_ID, info_text))
        for s in steps:
            media = []
            for f in s["files"][:50]:
                if f["type"] == "photo":
                    media.append(InputMediaPhoto(f["file_id"]))
                elif f["type"] == "video":
                    media.append(InputMediaVideo(f["file_id"]))
                elif f["type"] == "document":
                    media.append(InputMediaDocument(f["file_id"]))
            if media:
                asyncio.create_task(bot.send_media_group(ARCHIVE_CHAT_ID, media))
    except Exception as e:
        print(f"[save_to_archive] Ошибка: {e}")

# ========== WEBHOOK ==========
async def handle_webhook(request: web.Request):
    data = await request.json()
    update = types.Update(**data)
    await dp.feed_update(bot, update)
    return web.Response(text="OK")

app.router.add_post(f"/{TOKEN}", handle_webhook)
app.router.add_get("/", lambda r: web.Response(text="🤖 Бот работает"))

async def on_startup(app):
    await bot.delete_webhook(drop_pending_updates=True)
    await bot.set_webhook(BOT_URL)

async def on_shutdown(app):
    await bot.session.close()

app.on_startup.append(on_startup)
app.on_shutdown.append(on_shutdown)

# ========== RUN ==========
if __name__ == "__main__":
    web.run_app(app, host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))
