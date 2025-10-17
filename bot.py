import os
import pandas as pd
from datetime import datetime
from aiohttp import web
from aiogram import Bot, Dispatcher, types, F
from aiogram.enums import ParseMode
from aiogram.filters import Command
from aiogram.utils.keyboard import ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.client.default import DefaultBotProperties

# ====================== НАСТРОЙКИ ======================
TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
ARCHIVE_CHAT_ID = os.getenv("ARCHIVE_CHAT_ID", "-1003160855229")

# Ссылка на файл objects.xlsx
OBJECTS_URL = "https://github.com/Abu-al-Fugas/telegram-bot/blob/main/objects.xlsx"

# Создаём бота и диспетчер
bot = Bot(token=TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher()

# ====================== ГЛОБАЛЬНЫЕ ПЕРЕМЕННЫЕ ======================
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

# ====================== ХЕЛПЕРЫ ======================
def make_key(chat_id, user_id):
    return (chat_id, user_id)

def reply_keyboard():
    return ReplyKeyboardMarkup(
        keyboard=[
            [
                KeyboardButton("/start"),
                KeyboardButton("/photo"),
                KeyboardButton("/addphoto"),
            ],
            [
                KeyboardButton("/download"),
                KeyboardButton("/result"),
                KeyboardButton("/info"),
            ]
        ],
        resize_keyboard=True
    )

def upload_keyboard(step_name):
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Завершить", callback_data="upload_ok")]
    ])
    if step_name not in MANDATORY_STEPS:
        kb.inline_keyboard.append([InlineKeyboardButton(text="➡️ След.", callback_data="upload_next")])
    kb.inline_keyboard.append([InlineKeyboardButton(text="❌ Отмена", callback_data="upload_cancel")])
    return kb

# ====================== КОМАНДЫ ======================
@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    text = (
        "🤖 Бот для управления объектами ИПУГ\n\n"
        "Доступные команды:\n"
        "/photo – загрузка файлов с чек-листом\n"
        "/addphoto – добавить файлы к существующему объекту\n"
        "/download – скачать файлы объекта\n"
        "/result – список обработанных объектов\n"
        "/info – получить информацию об объекте из файла objects.xlsx"
    )
    await message.answer(text, reply_markup=reply_keyboard())

@dp.message(Command("photo"))
async def cmd_photo(message: types.Message):
    key = make_key(message.chat.id, message.from_user.id)
    user_state[key] = {"command": "await_object"}
    await message.answer("Введите номер объекта для загрузки:", reply_markup=reply_keyboard())

@dp.message(Command("addphoto"))
async def cmd_addphoto(message: types.Message):
    key = make_key(message.chat.id, message.from_user.id)
    user_state[key] = {"command": "await_addphoto_object"}
    await message.answer("Введите номер объекта, чтобы добавить фото:", reply_markup=reply_keyboard())

@dp.message(Command("download"))
async def cmd_download(message: types.Message):
    key = make_key(message.chat.id, message.from_user.id)
    user_state[key] = {"command": "await_download_object"}
    await message.answer("Введите номер объекта, чтобы скачать файлы:", reply_markup=reply_keyboard())

@dp.message(Command("result"))
async def cmd_result(message: types.Message):
    if not objects_data:
        await message.answer("📋 Нет завершённых загрузок.", reply_markup=reply_keyboard())
        return
    text = "✅ Завершённые загрузки:\n"
    for oid, data in objects_data.items():
        total_files = sum(len(s["files"]) for s in data["steps"])
        text += f"• Объект {oid}: {total_files} файлов\n"
    await message.answer(text, reply_markup=reply_keyboard())

@dp.message(Command("info"))
async def cmd_info(message: types.Message):
    await message.answer(f"📘 Информация по объектам доступна здесь:\n{OBJECTS_URL}", reply_markup=reply_keyboard())

# ====================== ОБРАБОТКА ТЕКСТА ======================
@dp.message(F.text)
async def handle_text(message: types.Message):
    key = make_key(message.chat.id, message.from_user.id)
    state = user_state.get(key)
    if not state:
        return

    if state["command"] == "await_object":
        object_id = message.text.strip()
        steps = [{"name": s, "files": []} for s in UPLOAD_STEPS]
        user_state[key] = {
            "command": "upload_steps",
            "object_id": object_id,
            "steps": steps,
            "step_index": 0,
            "chat_id": message.chat.id,
        }
        await send_upload_step(key)

    elif state["command"] == "await_addphoto_object":
        object_id = message.text.strip()
        user_state[key] = {
            "command": "add_photos",
            "object_id": object_id,
            "files": [],
            "chat_id": message.chat.id,
        }
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton("✅ Завершить", callback_data="addphoto_done")],
            [InlineKeyboardButton("❌ Отмена", callback_data="upload_cancel")]
        ])
        await message.answer(f"📸 Отправьте фото для объекта {object_id}.", reply_markup=kb)

    elif state["command"] == "await_download_object":
        object_id = message.text.strip()
        if object_id not in objects_data:
            await message.answer(f"❌ Объект {object_id} не найден.", reply_markup=reply_keyboard())
        else:
            await send_object_files(message.chat.id, object_id)
        user_state.pop(key, None)

# ====================== ОБРАБОТКА ФАЙЛОВ ======================
@dp.message(F.photo | F.document | F.video)
async def handle_files(message: types.Message):
    key = make_key(message.chat.id, message.from_user.id)
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

# ====================== CALLBACK ======================
@dp.callback_query(F.data.startswith("upload_") | F.data.startswith("addphoto_"))
async def handle_callback(call: types.CallbackQuery):
    key = make_key(call.message.chat.id, call.from_user.id)
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
        await call.message.answer(f"❌ Загрузка для объекта {obj} отменена", reply_markup=reply_keyboard())
        await call.answer("Отменено")
    elif call.data == "addphoto_done":
        object_id = state["object_id"]
        if object_id not in objects_data:
            objects_data[object_id] = {"steps": []}
        save_to_archive(object_id, [{"name": "Дополнительные файлы", "files": state["files"]}], append=True)
        user_state.pop(key, None)
        await call.message.answer(f"✅ Дополнительные файлы для объекта {object_id} сохранены.", reply_markup=reply_keyboard())
        await call.answer("Готово ✅")

# ====================== ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ======================
async def send_upload_step(key):
    state = user_state[key]
    step = state["steps"][state["step_index"]]
    msg = await bot.send_message(state["chat_id"], f"📸 Отправьте {step['name']}", reply_markup=upload_keyboard(step["name"]))
    state["last_message_id"] = msg.message_id

async def advance_step(key, skip=False):
    state = user_state[key]
    state["step_index"] += 1
    if state["step_index"] >= len(state["steps"]):
        object_id = state["object_id"]
        all_steps = state["steps"]
        save_to_archive(object_id, all_steps)
        objects_data[object_id] = {"steps": all_steps}
        user_state.pop(key, None)
        await bot.send_message(state["chat_id"], f"✅ Загрузка завершена для объекта {object_id}", reply_markup=reply_keyboard())
    else:
        await send_upload_step(key)

async def send_object_files(chat_id, object_id):
    data = objects_data.get(object_id)
    if not data:
        await bot.send_message(chat_id, f"❌ Нет файлов для объекта {object_id}.", reply_markup=reply_keyboard())
        return
    await bot.send_message(chat_id, f"💾 Файлы для объекта {object_id}:")
    for step in data["steps"]:
        media = []
        for f in step["files"][:50]:
            if f["type"] == "photo":
                media.append(types.InputMediaPhoto(media=f["file_id"]))
            elif f["type"] == "video":
                media.append(types.InputMediaVideo(media=f["file_id"]))
            elif f["type"] == "document":
                media.append(types.InputMediaDocument(media=f["file_id"]))
        if media:
            await bot.send_media_group(chat_id, media)

def save_to_archive(object_id, steps, append=False):
    try:
        info_text = f"💾 ОБЪЕКТ #{object_id}\n🕒 {datetime.now().strftime('%d.%m.%Y %H:%M')}"
        import asyncio
        asyncio.create_task(bot.send_message(ARCHIVE_CHAT_ID, info_text))
        for s in steps:
            files = s["files"]
            if not files:
                continue
            media = []
            for f in files[:50]:
                if f["type"] == "photo":
                    media.append(types.InputMediaPhoto(media=f["file_id"]))
                elif f["type"] == "video":
                    media.append(types.InputMediaVideo(media=f["file_id"]))
                elif f["type"] == "document":
                    media.append(types.InputMediaDocument(media=f["file_id"]))
            if media:
                asyncio.create_task(bot.send_media_group(ARCHIVE_CHAT_ID, media))
    except Exception as e:
        print(f"[save_to_archive] Ошибка: {e}")

# ====================== WEBHOOK ======================
async def on_startup(app):
    WEBHOOK_URL = f"https://telegram-bot-b6pn.onrender.com/{TOKEN}"
    await bot.set_webhook(WEBHOOK_URL)
    print(f"🚀 Webhook установлен: {WEBHOOK_URL}")

async def on_shutdown(app):
    await bot.delete_webhook()
    print("🛑 Webhook удалён")

async def handle_webhook(request):
    data = await request.json()
    update = types.Update(**data)
    await dp.feed_update(bot, update)
    return web.Response()

app = web.Application()
app.router.add_post(f'/{TOKEN}', handle_webhook)
app.on_startup.append(on_startup)
app.on_shutdown.append(on_shutdown)

if __name__ == "__main__":
    print("🚀 Бот запущен через aiohttp.web на Render")
    web.run_app(app, host="0.0.0.0", port=int(os.getenv("PORT", 10000)))
