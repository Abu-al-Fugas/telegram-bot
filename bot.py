import os
import logging
import pandas as pd
from flask import Flask, request
from telegram import (
    Update, InlineKeyboardButton, InlineKeyboardMarkup,
    InputMediaPhoto, InputMediaVideo, Bot
)
from telegram.ext import (
    Dispatcher, CommandHandler, MessageHandler, CallbackQueryHandler,
    ContextTypes, filters
)
from telegram.ext import CallbackContext

# Настройка логов
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Flask-приложение
app = Flask(__name__)

# Telegram Bot
TOKEN = os.environ.get("TELEGRAM_TOKEN")
if not TOKEN:
    raise ValueError("TELEGRAM_TOKEN не найден в переменных окружения")
bot = Bot(token=TOKEN)
dispatcher = Dispatcher(bot=bot, update_queue=None, workers=0)

# Загрузка Excel
EXCEL_FILE = "objects.xlsx"
df = pd.read_excel(EXCEL_FILE)

# Словарь для хранения состояния пользователей
user_data = {}

# ===== Команды =====
async def start(update: Update, context: CallbackContext):
    await update.message.reply_text("Привет! Используй /object для выбора объекта.")

async def object_command(update: Update, context: CallbackContext):
    user_id = update.message.from_user.id
    user_data[user_id] = {"stage": "waiting_object", "files": [], "file_messages": []}
    await update.message.reply_text("Введите номер объекта:")

async def handle_text(update: Update, context: CallbackContext):
    user_id = update.message.from_user.id
    if user_id not in user_data:
        return
    stage = user_data[user_id].get("stage")

    if stage == "waiting_object":
        obj_number = update.message.text.strip()
        if not obj_number.isdigit():
            await update.message.reply_text("Номер неверен.")
            return
        obj_number = int(obj_number)
        row = df[df.iloc[:, 0] == obj_number]
        if row.empty:
            await update.message.reply_text("Объект не найден.")
            return

        user_data[user_id]["stage"] = "uploading_files"
        user_data[user_id]["object_number"] = obj_number
        user_data[user_id]["object_row"] = row.iloc[0]

        keyboard = [[InlineKeyboardButton("✅ Завершить загрузку", callback_data="finish_upload")]]
        await update.message.reply_text("Отправляйте файлы:", reply_markup=InlineKeyboardMarkup(keyboard))

# ===== Прием фото/видео =====
async def handle_media(update: Update, context: CallbackContext):
    user_id = update.message.from_user.id
    if user_id not in user_data or user_data[user_id].get("stage") != "uploading_files":
        return

    media = update.message.photo or update.message.video
    if not media:
        return

    # Для фото выбираем максимальное качество
    if isinstance(media, list):
        media = media[-1]

    user_data[user_id]["files"].append(media)
    user_data[user_id]["file_messages"].append(update.message)
    await update.message.reply_text(f"Файл получен ({len(user_data[user_id]['files'])})")

# ===== Завершение загрузки =====
async def finish_upload(update: Update, context: CallbackContext):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    data = user_data.get(user_id)
    if not data or data.get("stage") != "uploading_files":
        return

    obj_row = data["object_row"]
    obj_number = data["object_number"]

    caption = f"Объект {obj_number}\nНаименование: {obj_row[1]}\nАдрес: {obj_row[2] if pd.notna(obj_row[2]) else 'не указан'}"

    media_group = []
    for f in data["files"]:
        if f.file_type == "photo":
            media_group.append(InputMediaPhoto(f.file_id))
        elif f.file_type == "video":
            media_group.append(InputMediaVideo(f.file_id))

    if media_group:
        await bot.send_media_group(chat_id=query.message.chat_id, media=media_group)
        await bot.send_message(chat_id=query.message.chat_id, text=caption)

    # Удаляем исходные сообщения
    for msg in data["file_messages"]:
        try:
            await msg.delete()
        except:
            pass

    # Сбрасываем данные пользователя
    user_data[user_id] = {}

# ===== Настройка диспетчера =====
dispatcher.add_handler(CommandHandler("start", start))
dispatcher.add_handler(CommandHandler("object", object_command))
dispatcher.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
dispatcher.add_handler(MessageHandler(filters.PHOTO | filters.VIDEO, handle_media))
dispatcher.add_handler(CallbackQueryHandler(finish_upload, pattern="finish_upload"))

# ===== Flask Webhook =====
@app.route("/healthz", methods=["GET"])
def health_check():
    return "OK", 200

@app.route(f"/{TOKEN}", methods=["POST"])
def webhook():
    update = Update.de_json(request.get_json(force=True), bot)
    dispatcher.process_update(update)
    return "OK", 200

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
