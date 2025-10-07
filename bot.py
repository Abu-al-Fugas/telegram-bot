# bot.py — версия v2
import os
import json
import time
import logging
from datetime import datetime
from typing import Dict, Any

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)

logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)

TOKEN = os.getenv("BOT_TOKEN")
BASE_DIR = os.getenv("DATA_DIR", "data")  # можно переопределить переменной окружения

# Список пунктов чек-листа: (ключ_папки, отображаемое_название)
CHECKLIST = [
    ("photo_old_meter", "Фото заменяемого счётчика"),
    ("photo_old_seals", "Фото пломб старого счётчика"),
    ("photo_new_meter", "Фото нового счётчика"),
    ("photo_new_seals", "Фото пломб нового счётчика"),
    ("photo_passport", "Фото паспорта нового счётчика"),
    ("photo_after_install", "Фото после монтажа"),
    ("video_leak_test", "Видео проверки герметичности"),
]

# Сессии на пользователя: user_id -> {object_id, pending_item, checklist_msg: (chat_id,msg_id)}
user_sessions: Dict[int, Dict[str, Any]] = {}

os.makedirs(BASE_DIR, exist_ok=True)


def object_dir(object_id: str) -> str:
    return os.path.join(BASE_DIR, f"object_{object_id}")


def metadata_path(obj_dir: str) -> str:
    return os.path.join(obj_dir, "metadata.json")


def load_metadata(obj_dir: str) -> dict:
    p = metadata_path(obj_dir)
    if os.path.exists(p):
        try:
            with open(p, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            log.exception("Ошибка чтения metadata.json: %s", e)
            return {"files": {}}
    # инициализация структуры
    md = {"files": {k: [] for k, _ in CHECKLIST}, "created_at": int(time.time())}
    return md


def save_metadata(obj_dir: str, md: dict):
    p = metadata_path(obj_dir)
    with open(p, "w", encoding="utf-8") as f:
        json.dump(md, f, ensure_ascii=False, indent=2)


def ensure_object_dirs(obj_dir: str):
    os.makedirs(obj_dir, exist_ok=True)
    for key, _ in CHECKLIST:
        os.makedirs(os.path.join(obj_dir, key), exist_ok=True)


def build_checklist_text_and_keyboard(obj_id: str, user_id: int):
    obj_dir = object_dir(obj_id)
    md = load_metadata(obj_dir)
    text_lines = [f"Объект {obj_id}\nЧек-лист по файлам:\n"]
    buttons = []
    for key, title in CHECKLIST:
        got = bool(md.get("files", {}).get(key))
        mark = "✅" if got else "❌"
        text_lines.append(f"{mark} {title}")
        # callback: choose item to upload
        buttons.append(InlineKeyboardButton(f"{mark} {title}", callback_data=f"choose|{key}"))
    # разбиваем кнопки по 1 в строку (удобнее для длинного списка)
    keyboard = [[b] for b in buttons]
    # кнопка завершения и отмены
    keyboard.append([
        InlineKeyboardButton("✅ Завершить загрузку", callback_data="finish"),
        InlineKeyboardButton("❌ Отмена", callback_data="cancel")
    ])
    text = "\n".join(text_lines)
    return text, InlineKeyboardMarkup(keyboard)


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Привет! Для начала загрузки выбери объект командой:\n"
        "/object <номер>, например: /object 15\n\n"
        "Или покажи файлы объекта: /show 15"
    )


async def cmd_object(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # /object 15
    if not context.args:
        await update.message.reply_text("Использование: /object <номер_объекта>")
        return
    obj_id = context.args[0].strip()
    user_id = update.effective_user.id
    obj_dir = object_dir(obj_id)
    ensure_object_dirs(obj_dir)
    md = load_metadata(obj_dir)
    save_metadata(obj_dir, md)

    # Создаём сессию пользователя
    user_sessions[user_id] = {
        "object_id": obj_id,
        "pending_item": None,
        "checklist_msg": None,
    }

    text, markup = build_checklist_text_and_keyboard(obj_id, user_id)
    sent = await update.message.reply_text(text, reply_markup=markup)
    user_sessions[user_id]["checklist_msg"] = (sent.chat_id, sent.message_id)


async def choose_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    data = query.data  # format "choose|{key}" or "finish" etc.

    session = user_sessions.get(user_id)
    if not session:
        await query.message.reply_text("Сессия не найдена. Сначала выберите объект командой /object <номер>.")
        return

    if data.startswith("choose|"):
        key = data.split("|", 1)[1]
        # проверка валидности ключа
        valid_keys = [k for k, _ in CHECKLIST]
        if key not in valid_keys:
            await query.message.reply_text("Неправильный пункт чек-листа.")
            return
        session["pending_item"] = key
        item_title = dict(CHECKLIST)[key]
        await query.message.reply_text(
            f"Отправьте файл(ы) для: {item_title}\n"
            "Можно отправлять несколько сообщений (фото/видео/документы).\n"
            "Когда всё отправите — нажмите '✅ Завершить загрузку'."
        )
    elif data == "finish":
        await handle_finish_by_user(user_id, query.message.chat_id, context)
    elif data == "cancel":
        # отмена сессии
        user_sessions.pop(user_id, None)
        await query.message.reply_text("Сессия отменена. Чтобы начать заново — /object <номер>.")
    else:
        await query.message.reply_text("Неизвестная команда.")


async def handle_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Обрабатываем входящие файлы: фото / видео / document.
    Требуется, чтобы пользователь предварительно выбрал объект (/object N)
    и выбрал пункт чек-листа (нажал кнопку).
    """
    msg = update.message
    user_id = msg.from_user.id
    session = user_sessions.get(user_id)
    if not session:
        await msg.reply_text("Сначала выберите объект: /object <номер>")
        return
    obj_id = session.get("object_id")
    pending = session.get("pending_item")
    if not pending:
        # просим пользователя указать для какого пункта отправляет файл
        text, markup = build_checklist_text_and_keyboard(obj_id, user_id)
        await msg.reply_text("Пожалуйста, сначала выберите пункт чек-листа, для которого вы загружаете файл.", reply_markup=markup)
        return

    obj_dir = object_dir(obj_id)
    ensure_object_dirs(obj_dir)
    md = load_metadata(obj_dir)

    # определяем файл
    file_obj = None
    file_type = None
    original_name = None
    try:
        if msg.photo:
            file_obj = await msg.photo[-1].get_file()
            file_type = "photo"
            ext = "jpg"
            original_name = f"{file_obj.file_unique_id}.{ext}"
        elif msg.video:
            file_obj = await msg.video.get_file()
            file_type = "video"
            original_name = getattr(msg.video, "file_name", f"{file_obj.file_unique_id}.mp4")
        elif msg.document:
            file_obj = await msg.document.get_file()
            file_type = "document"
            original_name = getattr(msg.document, "file_name", None) or f"{file_obj.file_unique_id}.dat"
        else:
            await msg.reply_text("Я не распознал вложение — отправьте фото, видео или документ.")
            return
    except Exception as e:
        log.exception("Ошибка получения файла: %s", e)
        await msg.reply_text("Не удалось скачать файл с Telegram.")
        return

    # формируем имя файла и путь
    timestamp = int(time.time())
    safe_name = original_name.replace(" ", "_")
    filename = f"{timestamp}_{file_obj.file_unique_id}_{safe_name}"
    save_folder = os.path.join(obj_dir, pending)
    os.makedirs(save_folder, exist_ok=True)
    save_path = os.path.join(save_folder, filename)

    try:
        await file_obj.download_to_drive(custom_path=save_path)
    except TypeError:
        # для старых/разных версий API
        await file_obj.download_to_drive(save_path)
    except Exception as e:
        log.exception("Ошибка сохранения файла: %s", e)
        await msg.reply_text("Ошибка при сохранении файла на диск.")
        return

    # обновляем метаданные
    entry = {
        "filename": os.path.relpath(save_path, obj_dir),
        "uploader_id": user_id,
        "uploader_name": msg.from_user.full_name,
        "ts": timestamp,
        "file_type": file_type,
        "original_name": original_name,
    }
    md.setdefault("files", {})
    md["files"].setdefault(pending, []).append(entry)
    save_metadata(obj_dir, md)

    # удаляем исходное сообщение пользователя, чтобы в чате было чисто
    try:
        await msg.delete()
    except Exception:
        # возможно, бот не может удалить сообщение — игнорируем
        pass

    # обновляем чек-лист (в сообщении бота)
    chat_id, msg_id = session.get("checklist_msg") or (None, None)
    if chat_id and msg_id:
        text, markup = build_checklist_text_and_keyboard(obj_id, user_id)
        try:
            await context.bot.edit_message_text(text=text, chat_id=chat_id, message_id=msg_id, reply_markup=markup)
        except Exception:
            pass

    await context.bot.send_message(chat_id=msg.chat_id, text=f"Файл сохранён для '{dict(CHECKLIST)[pending]}'.")


async def handle_finish_by_user(user_id: int, chat_id: int, context: ContextTypes.DEFAULT_TYPE):
    session = user_sessions.get(user_id)
    if not session:
        await context.bot.send_message(chat_id=chat_id, text="Сессия не найдена.")
        return
    obj_id = session.get("object_id")
    obj_dir = object_dir(obj_id)
    md = load_metadata(obj_dir)

    # формируем и отправляем сгруппированный отчёт
    await context.bot.send_message(chat_id=chat_id, text=f"Собираю файлы по объекту {obj_id}...")

    any_files = False
    for key, title in CHECKLIST:
        files = md.get("files", {}).get(key, [])
        if not files:
            continue
        any_files = True
        # отправляем заголовок
        await context.bot.send_message(chat_id=chat_id, text=f"🔹 {title}:")
        for entry in files:
            path = os.path.join(obj_dir, entry["filename"])
            caption = f"{title} — отправил: {entry.get('uploader_name','')} — {datetime.fromtimestamp(entry['ts']).strftime('%Y-%m-%d %H:%M')}"
            try:
                if entry["file_type"] == "photo":
                    await context.bot.send_photo(chat_id=chat_id, photo=open(path, "rb"), caption=caption)
                elif entry["file_type"] == "video":
                    await context.bot.send_video(chat_id=chat_id, video=open(path, "rb"), caption=caption)
                else:
                    await context.bot.send_document(chat_id=chat_id, document=open(path, "rb"), caption=caption)
            except Exception as e:
                log.exception("Ошибка отправки файла: %s", e)
                await context.bot.send_message(chat_id=chat_id, text=f"Не удалось отправить файл {entry.get('original_name')}")

    if not any_files:
        await context.bot.send_message(chat_id=chat_id, text="Файлы для этого объекта не найдены.")
    else:
        await context.bot.send_message(chat_id=chat_id, text="Готово — файлы сгруппированы и выведены в чат.")

    # очищаем сессию пользователя (в т.ч. pending_item)
    user_sessions.pop(user_id, None)


async def cmd_show(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # /show 15
    if not context.args:
        await update.message.reply_text("Использование: /show <номер_объекта>")
        return
    obj_id = context.args[0].strip()
    obj_dir = object_dir(obj_id)
    if not os.path.exists(obj_dir):
        await update.message.reply_text(f"Объект {obj_id} не найден.")
        return
    md = load_metadata(obj_dir)
    any_files = False
    await update.message.reply_text(f"Файлы по объекту {obj_id}:")
    for key, title in CHECKLIST:
        files = md.get("files", {}).get(key, [])
        if not files:
            continue
        any_files = True
        await update.message.reply_text(f"🔹 {title}:")
        for entry in files:
            path = os.path.join(obj_dir, entry["filename"])
            try:
                if entry["file_type"] == "photo":
                    await context.bot.send_photo(chat_id=update.effective_chat.id, photo=open(path, "rb"))
                elif entry["file_type"] == "video":
                    await context.bot.send_video(chat_id=update.effective_chat.id, video=open(path, "rb"))
                else:
                    await context.bot.send_document(chat_id=update.effective_chat.id, document=open(path, "rb"))
            except Exception as e:
                log.exception("Ошибка отправки файла: %s", e)
                await update.message.reply_text(f"Не удалось отправить файл {entry.get('original_name')}")
    if not any_files:
        await update.message.reply_text("Файлы не найдены.")


async def cmd_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id in user_sessions:
        user_sessions.pop(user_id)
        await update.message.reply_text("Сессия отменена.")
    else:
        await update.message.reply_text("Сессия не найдена.")


def main():
    if not TOKEN:
        log.error("BOT_TOKEN не задан. Установите переменную окружения BOT_TOKEN.")
        return

    app = ApplicationBuilder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("object", cmd_object))
    app.add_handler(CommandHandler("show", cmd_show))
    app.add_handler(CommandHandler("cancel", cmd_cancel))

    app.add_handler(CallbackQueryHandler(choose_callback))
    app.add_handler(MessageHandler(filters.PHOTO | filters.VIDEO | filters.Document.ALL, handle_file))

    log.info("Starting bot...")
    app.run_polling()


if __name__ == "__main__":
    main()
