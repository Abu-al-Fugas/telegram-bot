import os
import json
import asyncio
from datetime import datetime
from pathlib import Path
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, InputMediaPhoto, InputMediaVideo
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, CallbackQueryHandler, ContextTypes, filters

# Путь для хранения временных файлов
DATA_DIR = Path("data")
USERS_FILE = DATA_DIR / "users.json"
DATA_DIR.mkdir(exist_ok=True)
if not USERS_FILE.exists():
    USERS_FILE.write_text("{}")

# Чек-лист файлов
CHECKLIST = [
    "Фото заменяемого счетчика",
    "Фото пломб старого счетчика",
    "Фото нового счетчика",
    "Фото пломб нового счетчика",
    "Фото паспорта нового счетчика",
    "Фото после монтажа",
    "Видео проверки герметичности"
]

# Хранение состояния каждого пользователя
user_sessions = {}

def load_users():
    with open(USERS_FILE, "r", encoding="utf-8") as f:
        return json.load(f)

def save_users(users):
    with open(USERS_FILE, "w", encoding="utf-8") as f:
        json.dump(users, f, indent=2, ensure_ascii=False)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    users = load_users()
    if user_id not in users:
        await update.message.reply_text(
            "Привет! Для работы с ботом нужно зарегистрироваться.\n"
            "Отправьте ваши данные в формате: ФИО и номер телефона\n"
            "Пример: Иванов Иван Иванович +79998887766"
        )
    else:
        await update.message.reply_text("Вы уже зарегистрированы! Используйте /object для загрузки файлов.")

async def register_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    text = update.message.text.strip()
    parts = text.split()
    if len(parts) < 2:
        await update.message.reply_text("Неправильный формат. Попробуйте снова: ФИО и номер телефона")
        return
    phone_digits = ''.join(filter(str.isdigit, text))
    if len(phone_digits) < 10:
        await update.message.reply_text("Неправильный номер телефона. Попробуйте снова.")
        return
    full_name = ' '.join([p.upper() for p in parts[:-1]])
    phone = '+' + phone_digits[-11:]  # оставляем последние 11 цифр
    users = load_users()
    users[user_id] = {
        "full_name": full_name,
        "phone": phone,
        "username": update.effective_user.username,
        "first_name": update.effective_user.first_name,
        "last_name": update.effective_user.last_name,
        "registered_at": datetime.now().isoformat()
    }
    save_users(users)
    await update.message.reply_text(f"Регистрация успешна! Добро пожаловать, {full_name}. Используйте /object для загрузки файлов.")

async def object_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    users = load_users()
    if user_id not in users:
        await start(update, context)
        return
    await update.message.reply_text("Введите номер объекта для загрузки файлов:")
    user_sessions[user_id] = {"step": "awaiting_object"}

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    session = user_sessions.get(user_id, {})
    if session.get("step") == "awaiting_object":
        object_number = update.message.text.strip()
        session.update({
            "object_number": object_number,
            "step": "awaiting_file",
            "files": [],
            "current_file_type_index": 0,
            "messages_to_delete": []
        })
        user_sessions[user_id] = session
        await prompt_next_file(update, context, user_id)
    elif session.get("step") == "register":
        await register_user(update, context)

async def prompt_next_file(update: Update, context: ContextTypes.DEFAULT_TYPE, user_id):
    session = user_sessions[user_id]
    if session["current_file_type_index"] >= len(CHECKLIST):
        # Все типы файлов загружены
        await update.message.reply_text("Все файлы загружены. Нажмите '✅ Завершить загрузку', чтобы отправить в чат.")
        keyboard = [[InlineKeyboardButton("✅ Завершить загрузку", callback_data="finish_upload")]]
        msg = await update.message.reply_text("Завершить загрузку?", reply_markup=InlineKeyboardMarkup(keyboard))
        session["messages_to_delete"].append(msg.message_id)
        user_sessions[user_id] = session
        return
    current_type = CHECKLIST[session["current_file_type_index"]]
    msg = await update.message.reply_text(f"Отправьте файл(ы) для: {current_type}\nМожно несколько сообщений.")
    session["messages_to_delete"].append(msg.message_id)
    user_sessions[user_id] = session

async def handle_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    session = user_sessions.get(user_id)
    if not session or session.get("step") != "awaiting_file":
        return
    file_type = CHECKLIST[session["current_file_type_index"]]
    files = []

    if update.message.photo:
        for photo in update.message.photo:
            f = await photo.get_file()
            files.append(f)
    elif update.message.video:
        f = await update.message.video.get_file()
        files.append(f)
    elif update.message.document:
        f = await update.message.document.get_file()
        files.append(f)
    else:
        await update.message.reply_text("Пожалуйста, отправьте фото, видео или документ.")
        return

    object_dir = DATA_DIR / f"object_{session['object_number']}"
    object_dir.mkdir(exist_ok=True)

    for f in files:
        filename = os.path.join(object_dir, f"{file_type}_{f.file_id}")
        await f.download_to_drive(filename)
        session["files"].append({"path": filename, "type": file_type})

    # Удаляем сообщение пользователя и уведомления бота
    try:
        await update.message.delete()
    except:
        pass

    # Перейти к следующему типу файла
    session["current_file_type_index"] += 1
    user_sessions[user_id] = session
    await prompt_next_file(update, context, user_id)

async def finish_upload(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = str(query.from_user.id)
    session = user_sessions.get(user_id)
    if not session:
        await query.message.reply_text("Нет активной сессии.")
        return
    object_number = session["object_number"]
    users = load_users()
    user_info = users.get(user_id)
    if not user_info:
        username_str = query.from_user.username or ""
        user_display = f"{query.from_user.first_name} {query.from_user.last_name} ({username_str})"
    else:
        user_display = f"{user_info['full_name']} ({user_info['phone']})"

    media_group = []
    for f in session["files"]:
        path = f["path"]
        if path.lower().endswith((".jpg", ".jpeg", ".png")):
            media_group.append(InputMediaPhoto(open(path, "rb"), caption=f"Объект {object_number}\nЗагружено: {user_display}"))
        elif path.lower().endswith((".mp4", ".mov", ".mkv")):
            media_group.append(InputMediaVideo(open(path, "rb"), caption=f"Объект {object_number}\nЗагружено: {user_display}"))
        else:
            await query.message.reply_document(open(path, "rb"), caption=f"Объект {object_number}\nЗагружено: {user_display}")

    if media_group:
        await context.bot.send_media_group(chat_id=query.message.chat_id, media=media_group)

    # Удаляем временные файлы
    for f in session["files"]:
        try:
            os.remove(f["path"])
        except:
            pass
    try:
        os.rmdir(DATA_DIR / f"object_{object_number}")
    except:
        pass

    # Удаляем служебные сообщения бота
    for msg_id in session["messages_to_delete"]:
        try:
            await context.bot.delete_message(chat_id=query.message.chat_id, message_id=msg_id)
        except:
            pass

    user_sessions.pop(user_id)
    await query.message.reply_text(f"Файлы по объекту {object_number} успешно отправлены!")

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    session = user_sessions.get(user_id)
    if session:
        for f in session.get("files", []):
            try:
                os.remove(f["path"])
            except:
                pass
        for msg_id in session.get("messages_to_delete", []):
            try:
                await context.bot.delete_message(chat_id=update.message.chat_id, message_id=msg_id)
            except:
                pass
        user_sessions.pop(user_id)
        await update.message.reply_text("Загрузка отменена и временные файлы удалены.")
    else:
        await update.message.reply_text("Нет активной сессии.")

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "/start — начать работу с ботом\n"
        "/object — выбрать объект для загрузки файлов\n"
        "/cancel — отменить текущую загрузку\n"
        "/help — показать справку"
    )

def main():
    TOKEN = os.getenv("TOKEN")
    app = ApplicationBuilder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("object", object_command))
    app.add_handler(CommandHandler("cancel", cancel))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    app.add_handler(MessageHandler(filters.Document.ALL | filters.PHOTO | filters.VIDEO, handle_file))
    app.add_handler(CallbackQueryHandler(finish_upload, pattern="^finish_upload$"))

    print("Бот запущен...")
    app.run_polling()

if __name__ == "__main__":
    main()
