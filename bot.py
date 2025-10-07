import os
import json
import shutil
import asyncio
from datetime import datetime
from pathlib import Path
from telegram import Update, ReplyKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder,
    ContextTypes,
    CommandHandler,
    MessageHandler,
    ConversationHandler,
    filters,
)

# --- Настройки ---
DATA_DIR = Path("data")
USERS_FILE = DATA_DIR / "users.json"
CHECKLIST = [
    "Фото заменяемого счётчика",
    "Фото пломб старого счётчика",
    "Фото нового счётчика",
    "Фото пломб нового счётчика",
    "Фото паспорта нового счётчика",
    "Фото после монтажа",
    "Видео проверки герметичности"
]

DATA_DIR.mkdir(exist_ok=True)
if not USERS_FILE.exists():
    USERS_FILE.write_text("{}")
with open(USERS_FILE, "r", encoding="utf-8") as f:
    USERS = json.load(f)

REGISTER, OBJECT, FILE_UPLOAD = range(3)
SESSIONS = {}

def save_users():
    with open(USERS_FILE, "w", encoding="utf-8") as f:
        json.dump(USERS, f, ensure_ascii=False, indent=2)

def normalize_name(name: str) -> str:
    return " ".join(part.upper() for part in name.strip().split())

def extract_phone(text: str) -> str:
    digits = "".join(c for c in text if c.isdigit())
    if len(digits) == 10:
        digits = "7" + digits
    return "+" + digits if digits else None

def create_object_dir(object_number: str) -> Path:
    obj_dir = DATA_DIR / f"объект_{object_number}"
    obj_dir.mkdir(parents=True, exist_ok=True)
    return obj_dir

async def delete_message_later(context: ContextTypes.DEFAULT_TYPE, chat_id, message_id, delay=1):
    await asyncio.sleep(delay)
    try:
        await context.bot.delete_message(chat_id=chat_id, message_id=message_id)
    except:
        pass

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    if user_id not in USERS:
        await update.message.reply_text(
            "Привет! Для работы с ботом нужно зарегистрироваться.\n"
            "Пожалуйста, отправьте свои данные в формате: ФИО, телефон"
        )
        return REGISTER
    else:
        await update.message.reply_text(
            "Привет! Вы уже зарегистрированы. Используйте /object для начала загрузки файлов."
        )
        return ConversationHandler.END

async def register(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    text = update.message.text

    phone = extract_phone(text)
    name_part = text.replace(phone[-10:], "").replace(",", "").strip() if phone else text
    full_name = normalize_name(name_part)

    if not phone:
        await update.message.reply_text("Не удалось распознать номер телефона. Попробуйте снова.")
        return REGISTER

    USERS[user_id] = {
        "full_name": full_name,
        "phone": phone,
        "username": update.effective_user.username,
        "first_name": update.effective_user.first_name,
        "last_name": update.effective_user.last_name,
        "registered_at": datetime.now().isoformat()
    }
    save_users()
    await update.message.reply_text(f"Регистрация успешна ✅\nПривет, {full_name}!")
    return ConversationHandler.END

async def object_choice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    if user_id not in USERS:
        await update.message.reply_text("Сначала зарегистрируйтесь командой /start.")
        return ConversationHandler.END

    await update.message.reply_text("Введите номер объекта:")
    return OBJECT

async def receive_object_number(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    object_number = update.message.text.strip()
    SESSIONS[user_id] = {
        "object_number": object_number,
        "checklist_index": 0,
        "files": {step: [] for step in CHECKLIST},
        "msg_ids_to_delete": []
    }
    await send_next_checklist_step(update, context, user_id)
    return FILE_UPLOAD

async def send_next_checklist_step(update, context, user_id):
    session = SESSIONS[user_id]
    idx = session["checklist_index"]
    if idx >= len(CHECKLIST):
        msg = await update.message.reply_text(
            "✅ Все пункты чек-листа пройдены. Нажмите '✅ Завершить загрузку', чтобы отправить файлы.",
            reply_markup=ReplyKeyboardMarkup([["✅ Завершить загрузку"], ["❌ Отмена"]], resize_keyboard=True)
        )
        session["msg_ids_to_delete"].append(msg.message_id)
        return

    step_name = CHECKLIST[idx]
    msg = await update.message.reply_text(
        f"Отправьте файл(ы) для: {step_name}\n"
        "Можно отправлять несколько сообщений (фото/видео/документы).\n"
        "Когда всё отправите — нажмите '✅ Завершить загрузку'."
    )
    session["msg_ids_to_delete"].append(msg.message_id)

async def handle_files(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    if user_id not in SESSIONS:
        await update.message.reply_text("Сначала выберите объект командой /object.")
        return

    session = SESSIONS[user_id]
    idx = session["checklist_index"]
    if idx >= len(CHECKLIST):
        return

    step_name = CHECKLIST[idx]
    files_dir = create_object_dir(session["object_number"]) / step_name
    files_dir.mkdir(parents=True, exist_ok=True)

    if update.message.photo:
        file_obj = update.message.photo[-1].get_file()
        file_path = files_dir / f"{file_obj.file_id}.jpg"
        await file_obj.download_to_drive(custom_path=file_path)
        session["files"][step_name].append(file_path)
    elif update.message.document:
        file_obj = update.message.document.get_file()
        file_path = files_dir / update.message.document.file_name
        await file_obj.download_to_drive(custom_path=file_path)
        session["files"][step_name].append(file_path)
    elif update.message.video:
        file_obj = update.message.video.get_file()
        file_path = files_dir / f"{file_obj.file_id}.mp4"
        await file_obj.download_to_drive(custom_path=file_path)
        session["files"][step_name].append(file_path)
    else:
        return

    msg = await update.message.reply_text(f"Файл сохранён для '{step_name}'")
    await delete_message_later(context, msg.chat_id, msg.message_id, delay=1)
    await delete_message_later(context, update.message.chat_id, update.message.message_id, delay=1)

async def finish_upload(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    if user_id not in SESSIONS:
        await update.message.reply_text("Сначала выберите объект командой /object.")
        return ConversationHandler.END

    session = SESSIONS[user_id]
    object_number = session["object_number"]
    user_data = USERS.get(user_id)
    sender_info = f"{user_data['full_name']} ({user_data['phone']})" if user_data else f"ID {user_id}"

    chat_id = update.message.chat_id
    for step_name, files_list in session["files"].items():
        if not files_list:
            continue
        await context.bot.send_message(chat_id, f"**{step_name}:**", parse_mode="Markdown")
        for file_path in files_list:
            with open(file_path, "rb") as f:
                await context.bot.send_document(chat_id, f, caption=f"Объект {object_number}\nЗагружено: {sender_info}")

    shutil.rmtree(create_object_dir(object_number), ignore_errors=True)
    await update.message.reply_text(f"Загрузка для объекта {object_number} завершена ✅")
    del SESSIONS[user_id]
    return ConversationHandler.END

async def cancel_upload(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    if user_id in SESSIONS:
        shutil.rmtree(create_object_dir(SESSIONS[user_id]["object_number"]), ignore_errors=True)
        del SESSIONS[user_id]
    await update.message.reply_text("Загрузка отменена ❌")
    return ConversationHandler.END

app = ApplicationBuilder().token(os.environ.get("TOKEN")).build()

conv_handler = ConversationHandler(
    entry_points=[CommandHandler("start", start), CommandHandler("object", object_choice)],
    states={
        REGISTER: [MessageHandler(filters.TEXT & ~filters.COMMAND, register)],
        OBJECT: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_object_number)],
        FILE_UPLOAD: [
            MessageHandler(filters.ALL & ~filters.COMMAND, handle_files),
            MessageHandler(filters.Regex("✅ Завершить загрузку"), finish_upload),
            MessageHandler(filters.Regex("❌ Отмена"), cancel_upload)
        ],
    },
    fallbacks=[CommandHandler("cancel", cancel_upload)],
)

app.add_handler(conv_handler)

print("Бот запущен…")
app.run_polling()
