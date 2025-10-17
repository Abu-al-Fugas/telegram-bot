import os
from datetime import datetime
from aiogram import Bot, Dispatcher, F, types
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.fsm.state import StatesGroup, State
from aiogram.types import (
    ReplyKeyboardMarkup, KeyboardButton,
    InlineKeyboardMarkup, InlineKeyboardButton, InputMediaPhoto,
    InputMediaVideo, InputMediaDocument
)
from aiohttp import web
import pandas as pd

# ========== НАСТРОЙКИ ==========
TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
ARCHIVE_CHAT_ID = os.environ.get("ARCHIVE_CHAT_ID", "-1003160855229")
BOT_URL = os.environ.get("BOT_URL", f"https://telegram-bot-b6pn.onrender.com/{TOKEN}")

bot = Bot(token=TOKEN)
storage = MemoryStorage()
dp = Dispatcher(storage=storage)

# ========== ГЛОБАЛЬНЫЕ ==========
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

EXCEL_FILE = "https://github.com/Abu-al-Fugas/telegram-bot/blob/main/objects.xlsx"

# ========== FSM ==========
class UploadStates(StatesGroup):
    waiting_for_object = State()
    upload_steps = State()
    add_photos = State()
    download_object = State()
    info_object = State()

# ========== КЛАВИАТУРЫ ==========
def reply_keyboard():
    kb = ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton("/start"), KeyboardButton("/photo")],
            [KeyboardButton("/addphoto"), KeyboardButton("/download")],
            [KeyboardButton("/result"), KeyboardButton("/info")]
        ],
        resize_keyboard=True
    )
    return kb

def upload_keyboard(step_name):
    kb = InlineKeyboardMarkup(row_width=2)
    buttons = [InlineKeyboardButton("✅ Завершить", callback_data="upload_ok")]
    if step_name not in MANDATORY_STEPS:
        buttons.append(InlineKeyboardButton("➡️ След.", callback_data="upload_next"))
    buttons.append(InlineKeyboardButton("❌ Отмена", callback_data="upload_cancel"))
    kb.add(*buttons)
    return kb

def add_photo_keyboard():
    kb = InlineKeyboardMarkup(row_width=2)
    kb.add(
        InlineKeyboardButton("✅ Завершить", callback_data="addphoto_done"),
        InlineKeyboardButton("❌ Отмена", callback_data="upload_cancel")
    )
    return kb

# ========== ХЕЛПЕРЫ ==========
def make_key(chat_id, user_id):
    return (chat_id, user_id)

async def send_message(chat_id, text, reply_markup=None):
    try:
        return await bot.send_message(chat_id, text, reply_markup=reply_markup)
    except Exception as e:
        print(f"[send_message] Ошибка: {e}")

async def delete_message(chat_id, msg_id):
    try:
        await bot.delete_message(chat_id, msg_id)
    except Exception:
        pass

def save_to_archive(object_id, steps, append=False):
    try:
        info_text = f"💾 ОБЪЕКТ #{object_id}\n🕒 {datetime.now().strftime('%d.%m.%Y %H:%M')}"
        bot.send_message(ARCHIVE_CHAT_ID, info_text)
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
                bot.send_media_group(ARCHIVE_CHAT_ID, media)
    except Exception as e:
        print(f"[save_to_archive] Ошибка: {e}")

# ========== КОМАНДЫ ==========
@dp.message(Command("start"))
async def cmd_start(message: types.Message, state: FSMContext):
    text = (
        "🤖 Бот для управления объектами ИПУГ\n\n"
        "Доступные команды:\n"
        "/photo – загрузка файлов с чек-листом\n"
        "/addphoto – добавить файлы к существующему объекту\n"
        "/download – скачать файлы объекта\n"
        "/result – список обработанных объектов\n"
        "/info – информация об объекте"
    )
    await state.clear()
    await message.answer(text, reply_markup=reply_keyboard())

@dp.message(Command("photo"))
async def cmd_photo(message: types.Message, state: FSMContext):
    await state.set_state(UploadStates.waiting_for_object)
    await message.answer("Введите номер объекта для загрузки:", reply_markup=reply_keyboard())

@dp.message(Command("addphoto"))
async def cmd_addphoto(message: types.Message, state: FSMContext):
    await state.set_state(UploadStates.add_photos)
    await message.answer("Введите номер объекта, чтобы добавить фото:", reply_markup=reply_keyboard())

@dp.message(Command("download"))
async def cmd_download(message: types.Message, state: FSMContext):
    await state.set_state(UploadStates.download_object)
    if not objects_data:
        await message.answer("📂 Нет сохранённых объектов.", reply_markup=reply_keyboard())
        return
    await message.answer("Введите номер объекта для скачивания:", reply_markup=reply_keyboard())

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
async def cmd_info(message: types.Message, state: FSMContext):
    await state.set_state(UploadStates.info_object)
    await message.answer("Введите номер объекта для информации:", reply_markup=reply_keyboard())

# ========== ОБРАБОТКА ТЕКСТА ==========
@dp.message(F.text)
async def handle_text(message: types.Message, state: FSMContext):
    current = await state.get_state()
    key = make_key(message.chat.id, message.from_user.id)
    
    # Загрузка нового объекта
    if current == UploadStates.waiting_for_object.state:
        object_id = message.text.strip()
        steps = [{"name": s, "files": []} for s in UPLOAD_STEPS]
        user_state[key] = {"object_id": object_id, "steps": steps, "step_index": 0}
        await state.set_state(UploadStates.upload_steps)
        await send_upload_step(message.chat.id, key)
    
    # Добавление фото
    elif current == UploadStates.add_photos.state:
        object_id = message.text.strip()
        user_state[key] = {"object_id": object_id, "files": []}
        await message.answer(f"📸 Отправьте фото для объекта {object_id}", reply_markup=add_photo_keyboard())

    # Download
    elif current == UploadStates.download_object.state:
        object_id = message.text.strip()
        if object_id not in objects_data:
            await message.answer(f"❌ Объект {object_id} не найден.", reply_markup=reply_keyboard())
            return
        all_steps = objects_data[object_id]["steps"]
        for s in all_steps:
            media = []
            for f in s["files"][:50]:
                if f["type"] == "photo":
                    media.append(InputMediaPhoto(f["file_id"]))
                elif f["type"] == "video":
                    media.append(InputMediaVideo(f["file_id"]))
                elif f["type"] == "document":
                    media.append(InputMediaDocument(f["file_id"]))
            if media:
                await bot.send_media_group(message.chat.id, media)
        await state.clear()

    # Info
    elif current == UploadStates.info_object.state:
        object_id = message.text.strip()
        try:
            df = pd.read_excel(EXCEL_FILE)
            info = df[df['object_id'] == int(object_id)].to_string(index=False)
            await message.answer(f"ℹ️ Информация по объекту {object_id}:\n{info}", reply_markup=reply_keyboard())
        except Exception as e:
            await message.answer(f"Ошибка при получении информации: {e}", reply_markup=reply_keyboard())
        await state.clear()

# ========== ОБРАБОТКА ФАЙЛОВ ==========
@dp.message(F.content_type.in_({"photo", "document", "video"}))
async def handle_files(message: types.Message, state: FSMContext):
    key = make_key(message.chat.id, message.from_user.id)
    if key not in user_state:
        return
    state_data = user_state[key]

    current_state = await state.get_state()
    if current_state == UploadStates.upload_steps.state:
        step = state_data["steps"][state_data["step_index"]]
        if message.photo:
            step["files"].append({"type": "photo", "file_id": message.photo[-1].file_id})
        elif message.document:
            step["files"].append({"type": "document", "file_id": message.document.file_id})
        elif message.video:
            step["files"].append({"type": "video", "file_id": message.video.file_id})
        await send_upload_step(message.chat.id, key)

    elif current_state == UploadStates.add_photos.state:
        if message.photo:
            state_data["files"].append({"type": "photo", "file_id": message.photo[-1].file_id})
        elif message.document:
            state_data["files"].append({"type": "document", "file_id": message.document.file_id})
        elif message.video:
            state_data["files"].append({"type": "video", "file_id": message.video.file_id})

# ========== CALLBACK ==========
@dp.callback_query()
async def handle_callback(query: types.CallbackQuery, state: FSMContext):
    key = make_key(query.message.chat.id, query.from_user.id)
    if key not in user_state:
        await query.answer("Нет активной загрузки")
        return
    data = user_state[key]

    if query.data == "upload_ok":
        await advance_step(query.message.chat.id, key)
        await query.answer("✅ Шаг завершён")
    elif query.data == "upload_next":
        await advance_step(query.message.chat.id, key, skip=True)
        await query.answer("➡️ Пропущено")
    elif query.data == "upload_cancel":
        obj = data.get("object_id", "")
        user_state.pop(key, None)
        await query.message.delete()
        await query.message.answer(f"❌ Загрузка для объекта {obj} отменена", reply_markup=reply_keyboard())
        await query.answer("Отменено")
    elif query.data == "addphoto_done":
        object_id = data["object_id"]
        if object_id not in objects_data:
            objects_data[object_id] = {"steps": []}
        save_to_archive(object_id, [{"name": "Дополнительные файлы", "files": data["files"]}], append=True)
        user_state.pop(key, None)
        await query.message.delete()
        await query.message.answer(f"✅ Дополнительные файлы для объекта {object_id} сохранены.", reply_markup=reply_keyboard())
        await query.answer("Готово ✅")

# ========== ПРОДВИЖЕНИЕ ШАГОВ ==========
async def send_upload_step(chat_id, key):
    state_data = user_state[key]
    step = state_data["steps"][state_data["step_index"]]
    text = f"📸 Отправьте {step['name']}"
    kb = upload_keyboard(step["name"])
    await bot.send_message(chat_id, text, reply_markup=kb)

async def advance_step(chat_id, key, skip=False):
    state_data = user_state[key]
    state_data["step_index"] += 1
    if state_data["step_index"] >= len(state_data["steps"]):
        object_id = state_data["object_id"]
        all_steps = state_data["steps"]
        save_to_archive(object_id, all_steps)
        objects_data[object_id] = {"steps": all_steps}
        user_state.pop(key, None)
        await bot.send_message(chat_id, f"✅ Загрузка завершена для объекта {object_id}", reply_markup=reply_keyboard())
    else:
        await send_upload_step(chat_id, key)

# ========== WEBHOOK ==========
async def handle_webhook(request):
    update = types.Update(**await request.json())
    await dp.feed_update(bot, update)
    return web.Response(text="OK")

app = web.Application()
app.router.add_post(f"/{TOKEN}", handle_webhook)
app.router.add_get("/", lambda r: web.Response(text="🤖 Бот работает"))

if __name__ == "__main__":
    import asyncio
    from aiohttp import web
    # устанавливаем webhook
    async def on_startup(app):
        await bot.delete_webhook(drop_pending_updates=True)
        await bot.set_webhook(BOT_URL)

    app.on_startup.append(on_startup)
    web.run_app(app, host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))
