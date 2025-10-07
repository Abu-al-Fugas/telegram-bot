import os
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler,
    CallbackQueryHandler, ContextTypes, filters
)

TOKEN = os.getenv("BOT_TOKEN")  # токен из переменной окружения
BASE_DIR = "data"

# Убедимся, что директория для данных существует
os.makedirs(BASE_DIR, exist_ok=True)

# Словарь для хранения текущего выбранного объекта каждым пользователем
user_object_selection = {}


# Команда /start
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Привет! 👋\nВыберите объект для загрузки файлов:",
        reply_markup=get_object_keyboard()
    )


# Генерация кнопок с номерами объектов
def get_object_keyboard():
    buttons = []
    for i in range(1, 21):  # пока 20 объектов, потом можно увеличить
        buttons.append(
            InlineKeyboardButton(f"Объект {i}", callback_data=f"object_{i}")
        )

    # Разбиваем кнопки на строки по 4
    keyboard = [buttons[i:i+4] for i in range(0, len(buttons), 4)]
    return InlineKeyboardMarkup(keyboard)


# Обработка выбора объекта
async def object_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    object_id = query.data.split("_")[1]
    user_object_selection[query.from_user.id] = object_id

    # Создаём папки для объекта
    object_dir = os.path.join(BASE_DIR, f"object_{object_id}")
    os.makedirs(os.path.join(object_dir, "photos"), exist_ok=True)
    os.makedirs(os.path.join(object_dir, "videos"), exist_ok=True)
    os.makedirs(os.path.join(object_dir, "docs"), exist_ok=True)

    await query.message.reply_text(
        f"✅ Вы выбрали объект {object_id}.\n"
        f"Теперь отправьте фото, видео или документы — я сохраню их."
    )


# Обработка файлов (фото, видео, документы)
async def handle_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    object_id = user_object_selection.get(user_id)

    if not object_id:
        await update.message.reply_text("⚠️ Сначала выберите объект командой /start.")
        return

    object_dir = os.path.join(BASE_DIR, f"object_{object_id}")

    # Определяем тип файла
    file = None
    file_type = None

    if update.message.photo:
        file = await update.message.photo[-1].get_file()
        file_type = "photos"
    elif update.message.video:
        file = await update.message.video.get_file()
        file_type = "videos"
    elif update.message.document:
        file = await update.message.document.get_file()
        file_type = "docs"

    if not file:
        await update.message.reply_text("Не удалось определить тип файла 😕")
        return

    # Сохраняем файл
    folder = os.path.join(object_dir, file_type)
    os.makedirs(folder, exist_ok=True)
    filename = os.path.join(folder, f"{file.file_unique_id}.dat")
    await file.download_to_drive(filename)

    # Удаляем исходное сообщение
    await update.message.delete()

    await context.bot.send_message(
        chat_id=update.message.chat_id,
        text=f"📁 Файл сохранён в объект {object_id} ({file_type})"
    )


# Команда для показа файлов по объекту
async def show_object(update: Update, context: ContextTypes.DEFAULT_TYPE):
    args = context.args
    if not args:
        await update.message.reply_text("Используй: /show 15 (номер объекта)")
        return

    object_id = args[0]
    object_dir = os.path.join(BASE_DIR, f"object_{object_id}")

    if not os.path.exists(object_dir):
        await update.message.reply_text(f"❌ Объект {object_id} не найден.")
        return

    await update.message.reply_text(f"📂 Объект {object_id}\nЗагруженные файлы:")

    # Фото
    photos_dir = os.path.join(object_dir, "photos")
    photos = os.listdir(photos_dir)
    if photos:
        await update.message.reply_text("📸 Фото:")
        for p in photos:
            await update.message.reply_photo(photo=open(os.path.join(photos_dir, p), "rb"))

    # Видео
    videos_dir = os.path.join(object_dir, "videos")
    videos = os.listdir(videos_dir)
    if videos:
        await update.message.reply_text("🎥 Видео:")
        for v in videos:
            await update.message.reply_video(video=open(os.path.join(videos_dir, v), "rb"))

    # Документы
    docs_dir = os.path.join(object_dir, "docs")
    docs = os.listdir(docs_dir)
    if docs:
        await update.message.reply_text("📑 Документы:")
        for d in docs:
            await update.message.reply_document(document=open(os.path.join(docs_dir, d), "rb"))


# Запуск приложения
def main():
    app = ApplicationBuilder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("show", show_object))
    app.add_handler(CallbackQueryHandler(object_selected))
    app.add_handler(MessageHandler(
        filters.PHOTO | filters.VIDEO | filters.Document.ALL,
        handle_file
    ))

    app.run_polling()


if __name__ == "__main__":
    main()
