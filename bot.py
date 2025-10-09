import logging
import pandas as pd
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, InputMediaPhoto, InputMediaVideo
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, CallbackQueryHandler, ContextTypes, filters

# Настройка логов
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Загрузка Excel
EXCEL_FILE = "objects.xlsx"  # файл с данными
df = pd.read_excel(EXCEL_FILE)

# Словарь для хранения текущего состояния пользователей
user_data = {}

# Команда /start
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Привет! Используй команду /object чтобы выбрать объект."
    )

# Команда /object
async def object_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    user_data[user_id] = {"stage": "waiting_object", "files": [], "file_messages": []}
    await update.message.reply_text("Введите номер объекта:")

# Получение номера объекта
async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    if user_id not in user_data:
        return
    stage = user_data[user_id].get("stage")

    if stage == "waiting_object":
        obj_number = update.message.text.strip()
        if not obj_number.isdigit():
            await update.message.reply_text("Неверный номер объекта. Попробуйте ещё раз.")
            return
        obj_number = int(obj_number)
        row = df[df.iloc[:, 0] == obj_number]
        if row.empty:
            await update.message.reply_text("Объект не найден.")
            return
        user_data[user_id]["stage"] = "uploading_files"
        user_data[user_id]["object_number"] = obj_number
        user_data[user_id]["object_row"] = row.iloc[0]
        # Кнопка завершения загрузки
        keyboard = [
            [InlineKeyboardButton("✅ Завершить загрузку", callback_data="finish_upload")]
        ]
        await update.message.reply_text("Отправляйте файлы (фото/видео)", reply_markup=InlineKeyboardMarkup(keyboard))

# Прием файлов (фото и видео)
async def handle_media(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    if user_id not in user_data:
        return
    stage = user_data[user_id].get("stage")
    if stage != "uploading_files":
        return

    media = update.message.photo or update.message.video
    if not media:
        return

    user_data[user_id]["files"].append(media[-1] if isinstance(media, list) else media)
    user_data[user_id]["file_messages"].append(update.message)
    await update.message.reply_text(f"Файл получен ({len(user_data[user_id]['files'])})")

# Кнопка завершения загрузки
async def finish_upload(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    data = user_data.get(user_id)
    if not data or data.get("stage") != "uploading_files":
        return

    obj_row = data["object_row"]
    obj_number = data["object_number"]

    caption = f"Объект {obj_number}\nНаименование: {obj_row[1]}\nАдрес: {obj_row[2] if pd.notna(obj_row[2]) else 'не указан'}"

    # Формируем медиагруппу
    media_group = []
    for f in data["files"]:
        if f.file_type == "photo":
            media_group.append(InputMediaPhoto(f.file_id))
        elif f.file_type == "video":
            media_group.append(InputMediaVideo(f.file_id))

    if media_group:
        await context.bot.send_media_group(chat_id=query.message.chat_id, media=media_group)
        await context.bot.send_message(chat_id=query.message.chat_id, text=caption)

    # Удаляем сообщения пользователя с файлами
    for msg in data["file_messages"]:
        try:
            await msg.delete()
        except:
            pass

    # Сбрасываем данные пользователя
    user_data[user_id] = {}

# Основная функция
def main():
    TOKEN = "YOUR_BOT_TOKEN"  # замените на токен вашего бота
    app = ApplicationBuilder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("object", object_command))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    app.add_handler(MessageHandler(filters.PHOTO | filters.VIDEO, handle_media))
    app.add_handler(CallbackQueryHandler(finish_upload, pattern="finish_upload"))

    print("Бот запущен...")
    app.run_polling()

if __name__ == "__main__":
    main()
