# bot.py — версия с персональными сессиями, вводом номера в следующем сообщении,
# удалением служебных сообщений по завершении и группировкой файлов с пометкой "Объект X".
import os
import json
import time
import logging
from datetime import datetime
from typing import Dict, Any, List, Optional

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    BotCommand,
)
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)

# ---------- Настройки ----------
logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)

TOKEN = os.getenv("BOT_TOKEN")
BASE_DIR = os.getenv("DATA_DIR", "data")  # папка для хранения файлов, можно переопределить
# чеклист: ключ папки => описание
CHECKLIST = [
    ("photo_old_meter", "Фото заменяемого счётчика"),
    ("photo_old_seals", "Фото пломб старого счётчика"),
    ("photo_new_meter", "Фото нового счётчика"),
    ("photo_new_seals", "Фото пломб нового счётчика"),
    ("photo_passport", "Фото паспорта нового счётчика"),
    ("photo_after_install", "Фото после монтажа"),
    ("video_leak_test", "Видео проверки герметичности"),
]

# ---------- Внутренние структуры ----------
# Ожидание ввода номера объекта после команды: user_id -> {"action": "object"|"show", "chat_id": int, "prompt_msg": (chat_id,msg_id)}
awaiting_object: Dict[int, Dict[str, Any]] = {}

# Сессии загрузки: user_id -> session
# session содержит:
#  - object_id (str)
#  - pending_item (ключ чеклиста или None)
#  - checklist_msg (chat_id, msg_id) — основное сообщение с чек-листом (ботское)
#  - bot_messages: list of (chat_id, msg_id) — все служебные сообщения бота для последующего удаления
user_sessions: Dict[int, Dict[str, Any]] = {}

os.makedirs(BASE_DIR, exist_ok=True)


# ---------- Вспомогательные функции ----------
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
    # инициализация
    md = {"files": {k: [] for k, _ in CHECKLIST}, "created_at": int(time.time())}
    return md


def save_metadata(obj_dir: str, md: dict):
    p = metadata_path(obj_dir)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        json.dump(md, f, ensure_ascii=False, indent=2)


def ensure_object_dirs(obj_dir: str):
    os.makedirs(obj_dir, exist_ok=True)
    for key, _ in CHECKLIST:
        os.makedirs(os.path.join(obj_dir, key), exist_ok=True)


def record_bot_message(session: Dict[str, Any], chat_id: int, msg_id: int):
    """Сохраняем сообщение бота, чтобы потом удалить."""
    if session is None:
        return
    session.setdefault("bot_messages", []).append((chat_id, msg_id))


def safe_delete(bot, chat_id: int, msg_id: int):
    try:
        return bot.delete_message(chat_id=chat_id, message_id=msg_id)
    except Exception:
        return None


def build_checklist_text_and_keyboard(obj_id: str):
    obj_dir = object_dir(obj_id)
    md = load_metadata(obj_dir)
    text_lines = [f"Объект {obj_id}\nЧек-лист по файлам:\n"]
    buttons = []
    for key, title in CHECKLIST:
        got = bool(md.get("files", {}).get(key))
        mark = "✅" if got else "❌"
        text_lines.append(f"{mark} {title}")
        buttons.append(InlineKeyboardButton(f"{mark} {title}", callback_data=f"choose|{key}"))
    # Разбиваем по одной кнопке в строке (лучше для длинных списков)
    keyboard = [[b] for b in buttons]
    keyboard.append([
        InlineKeyboardButton("✅ Завершить загрузку", callback_data="finish"),
        InlineKeyboardButton("❌ Отмена", callback_data="cancel")
    ])
    return "\n".join(text_lines), InlineKeyboardMarkup(keyboard)


# ---------- Команды и обработчики ----------

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    txt = (
        "Привет! Я помогу загружать и группировать файлы по объектам.\n\n"
        "Команды:\n"
        "/object — начать загрузку (в следующем сообщении укажи номер объекта)\n"
        "/show — показать файлы объекта (в следующем сообщении укажи номер объекта)\n"
        "/cancel — отменить текущую операцию\n\n"
        "Пример: отправь `/object`, затем в следующем сообщении `15`."
    )
    sent = await update.message.reply_text(txt)
    # не сохраняем это в сессии — это просто подсказка


async def cmd_object(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Шаг 1: команда /object — бот ждёт следующего текстового сообщения с номером"""
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id
    prompt = await update.message.reply_text("Введите номер объекта (например: 15).")
    awaiting_object[user_id] = {"action": "object", "chat_id": chat_id, "prompt_msg": (prompt.chat_id, prompt.message_id)}
    # prompt можно не сохранять в сессии — его удалим при завершении (если будет сессия)


async def cmd_show_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Шаг 1: команда /show — бот ждёт следующего сообщения с номером объекта"""
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id
    prompt = await update.message.reply_text("Введите номер объекта для показа (например: 15).")
    awaiting_object[user_id] = {"action": "show", "chat_id": chat_id, "prompt_msg": (prompt.chat_id, prompt.message_id)}


async def handle_text_next(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Центральный обработчик текста: если пользователь в awaiting_object,
    интерпретируем это сообщение как номер объекта.
    Иначе — игнорируем или можно добавить другую логику.
    """
    user_id = update.effective_user.id
    text = (update.message.text or "").strip()
    if not text:
        return

    pending = awaiting_object.pop(user_id, None)
    if not pending:
        return  # обычное сообщение — игнорируем

    action = pending.get("action")
    chat_id = pending.get("chat_id")
    prompt_msg = pending.get("prompt_msg")  # (chat_id,msg_id)

    # попытка извлечь номер объекта — разрешаем числа и комбинации без пробелов
    obj_id = text.split()[0]  # первое слово
    # можно добавить валидацию: только цифры
    if not obj_id.isdigit():
        # уведомление и возвращаем user в awaiting (попросим ввести правильно)
        warn = await update.message.reply_text("Неверный формат номера объекта. Введите, пожалуйста, только цифры, например: 15")
        # возвращаем ожидание
        awaiting_object[user_id] = pending
        # сохранять предупреждение в сессии не нужно — оно удалится через время при завершении
        return

    if action == "object":
        # запускаем сессию загрузки
        # создаём диры, metadata
        obj_dir = object_dir(obj_id)
        ensure_object_dirs(obj_dir)
        md = load_metadata(obj_dir)
        save_metadata(obj_dir, md)

        # формируем сессию
        session = {
            "object_id": obj_id,
            "pending_item": None,
            "checklist_msg": None,
            "bot_messages": [],
            "created_at": int(time.time())
        }
        user_sessions[user_id] = session

        # отправляем чек-лист (как одно бот-сообщение)
        text, markup = build_checklist_text_and_keyboard(obj_id)
        sent = await context.bot.send_message(chat_id=chat_id, text=text, reply_markup=markup)
        session["checklist_msg"] = (sent.chat_id, sent.message_id)
        record_bot_message(session, sent.chat_id, sent.message_id)

        # удаляем prompt (тот, что мы отправили после /object)
        try:
            if prompt_msg:
                await context.bot.delete_message(chat_id=prompt_msg[0], message_id=prompt_msg[1])
        except Exception:
            pass

        # удаляем сообщение пользователя, где он ввёл номер объекта (чтобы не засорял чат)
        try:
            await update.message.delete()
        except Exception:
            pass

    elif action == "show":
        # просто показать файлы объекта (без сессии)
        await show_object_by_id(obj_id, update.effective_chat.id, context)
        # удалим prompt и сообщение пользователя с номером, так чат не засорён
        try:
            if prompt_msg:
                await context.bot.delete_message(chat_id=prompt_msg[0], message_id=prompt_msg[1])
        except Exception:
            pass
        try:
            await update.message.delete()
        except Exception:
            pass


async def choose_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка нажатий в чек-листе (выбор пункта / finish / cancel)."""
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    session = user_sessions.get(user_id)
    if not session:
        await query.message.reply_text("Сессия не найдена. Сначала выберите объект командой /object <номер>.")
        return

    data = query.data
    if data.startswith("choose|"):
        key = data.split("|", 1)[1]
        valid_keys = [k for k, _ in CHECKLIST]
        if key not in valid_keys:
            await query.message.reply_text("Неправильный пункт чек-листа.")
            return
        session["pending_item"] = key
        item_title = dict(CHECKLIST)[key]
        # отправляем подсказку и сохраняем её id — чтобы удалить позднее
        sent = await query.message.reply_text(
            f"Отправьте файл(ы) для: {item_title}\n"
            "Можно отправлять несколько сообщений (фото/видео/документы).\n"
            "Когда всё отправите — нажмите '✅ Завершить загрузку'."
        )
        record_bot_message(session, sent.chat_id, sent.message_id)
    elif data == "finish":
        await handle_finish_by_user(user_id, query.message.chat_id, context)
    elif data == "cancel":
        # удаляем сессию и служебные сообщения
        await cleanup_session(user_id, context, notify=True)
    else:
        await query.message.reply_text("Неизвестная команда.")


async def handle_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Обрабатываем входящие файлы: фото / видео / document.
    Требуется, чтобы пользователь предварительно выбрал объект (/object N)
    и пункт чек-листа (нажал кнопку).
    """
    msg = update.message
    user_id = msg.from_user.id
    session = user_sessions.get(user_id)
    if not session:
        # попросим сначала выбрать объект
        warn = await msg.reply_text("Сначала выберите объект: отправьте /object, затем в следующем сообщении номер объекта.")
        # удалим подсказку через 10 сек? (не обязательно) — но запомним её, чтобы удалить при следующем завершении, если сессии не будет, просто удалим сразу
        try:
            await context.bot.delete_message(chat_id=warn.chat_id, message_id=warn.message_id)
        except Exception:
            pass
        return

    pending = session.get("pending_item")
    if not pending:
        # попросим выбрать пункт чек-листа
        obj_id = session.get("object_id")
        text, markup = build_checklist_text_and_keyboard(obj_id)
        sent = await msg.reply_text("Пожалуйста, сначала нажмите кнопку чек-листа — для какого пункта вы загружаете файл.", reply_markup=markup)
        record_bot_message(session, sent.chat_id, sent.message_id)
        # удалим исходное сообщение пользователя (если это файл, пользователь ожидает что файл сохранится — но мы просим выбрать пункт)
        try:
            await msg.delete()
        except Exception:
            pass
        return

    obj_id = session.get("object_id")
    obj_dir = object_dir(obj_id)
    ensure_object_dirs(obj_dir)
    md = load_metadata(obj_dir)

    # Определяем файл
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
            # не распознано — удаляем сообщение и уведомляем кратко
            try:
                await msg.delete()
            except Exception:
                pass
            return
    except Exception as e:
        log.exception("Ошибка получения файла: %s", e)
        try:
            await msg.delete()
        except Exception:
            pass
        return

    # Сохраняем файл
    timestamp = int(time.time())
    safe_name = original_name.replace(" ", "_")
    filename = f"{timestamp}_{file_obj.file_unique_id}_{safe_name}"
    save_folder = os.path.join(obj_dir, pending)
    os.makedirs(save_folder, exist_ok=True)
    save_path = os.path.join(save_folder, filename)
    try:
        # Иногда API принимает custom_path, иногда нет — пробуем оба
        await file_obj.download_to_drive(custom_path=save_path)
    except TypeError:
        await file_obj.download_to_drive(save_path)
    except Exception as e:
        log.exception("Ошибка сохранения файла: %s", e)
        try:
            await msg.delete()
        except Exception:
            pass
        return

    # Обновляем метаданные
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

    # Удаляем исходное сообщение пользователя (чтобы не засорять группу)
    try:
        await msg.delete()
    except Exception:
        pass

    # Обновляем чек-лист (редактируем сообщение бота)
    checklist = session.get("checklist_msg")
    if checklist:
        chat_id, msg_id = checklist
        text, markup = build_checklist_text_and_keyboard(obj_id)
        try:
            await context.bot.edit_message_text(text=text, chat_id=chat_id, message_id=msg_id, reply_markup=markup)
        except Exception:
            pass

    # отправляем краткое уведомление и сохраняем его id, чтобы удалить позднее
    notif = await context.bot.send_message(chat_id=msg.chat_id, text=f"Файл сохранён для '{dict(CHECKLIST)[pending]}' (Объект {obj_id}).")
    record_bot_message(session, notif.chat_id, notif.message_id)


async def handle_finish_by_user(user_id: int, chat_id: int, context: ContextTypes.DEFAULT_TYPE):
    """Собираем все файлы по объекту и отправляем сгруппированно, затем удаляем служебные сообщения и завершаем сессию."""
    session = user_sessions.get(user_id)
    if not session:
        await context.bot.send_message(chat_id=chat_id, text="Сессия не найдена.")
        return

    obj_id = session.get("object_id")
    obj_dir = object_dir(obj_id)
    md = load_metadata(obj_dir)

    # информируем пользователя / группу
    summary_msg = await context.bot.send_message(chat_id=chat_id, text=f"Собираю файлы по Объекту {obj_id}...")
    record_bot_message(session, summary_msg.chat_id, summary_msg.message_id)

    any_files = False
    for key, title in CHECKLIST:
        files = md.get("files", {}).get(key, [])
        if not files:
            continue
        any_files = True
        # Заголовок блока
        header_msg = await context.bot.send_message(chat_id=chat_id, text=f"🔹 {title}:")
        record_bot_message(session, header_msg.chat_id, header_msg.message_id)
        for entry in files:
            path = os.path.join(obj_dir, entry["filename"])
            caption = f"Объект {obj_id} — {title}\nОтправил: {entry.get('uploader_name','')} — {datetime.fromtimestamp(entry['ts']).strftime('%Y-%m-%d %H:%M')}"
            try:
                if entry["file_type"] == "photo":
                    sent = await context.bot.send_photo(chat_id=chat_id, photo=open(path, "rb"), caption=caption)
                elif entry["file_type"] == "video":
                    sent = await context.bot.send_video(chat_id=chat_id, video=open(path, "rb"), caption=caption)
                else:
                    sent = await context.bot.send_document(chat_id=chat_id, document=open(path, "rb"), caption=caption)
                # пометим сообщения, которые бот отправил — для удаления по просьбе (если нужно)
                # но по заданию сгруппированные файлы остаются в чате (порядок). Мы не удаляем их автоматически.
                # Если нужно, можно добавить опцию удаления этих сообщений тоже.
            except Exception as e:
                log.exception("Ошибка отправки файла: %s", e)
                err = await context.bot.send_message(chat_id=chat_id, text=f"Не удалось отправить файл {entry.get('original_name')}")
                record_bot_message(session, err.chat_id, err.message_id)

    if not any_files:
        none_msg = await context.bot.send_message(chat_id=chat_id, text="Файлы для этого объекта не найдены.")
        record_bot_message(session, none_msg.chat_id, none_msg.message_id)
    else:
        done_msg = await context.bot.send_message(chat_id=chat_id, text="Готово — файлы сгруппированы и выведены в чат.")
        record_bot_message(session, done_msg.chat_id, done_msg.message_id)

    # Удаляем все служебные сообщения бота, относящиеся к сессии
    await cleanup_session(user_id, context, notify=False, keep_grouped_outputs=True)


async def cleanup_session(user_id: int, context: ContextTypes.DEFAULT_TYPE, notify: bool = True, keep_grouped_outputs: bool = True):
    """
    Удаляет служебные сообщения бота, удаляет сессию.
    Если keep_grouped_outputs=True — не удаляет те сообщения, которые являются группированными результатами (мы их не помечали).
    Мы удаляем только те сообщения, которые были записаны в session['bot_messages'].
    """
    session = user_sessions.get(user_id)
    if not session:
        return
    bot_messages: List[Any] = session.get("bot_messages", []) or []
    for (chat_id, msg_id) in bot_messages:
        try:
            await context.bot.delete_message(chat_id=chat_id, message_id=msg_id)
        except Exception:
            pass

    # Также попробуем удалить чек-лист (если он остался)
    checklist = session.get("checklist_msg")
    if checklist:
        try:
            await context.bot.delete_message(chat_id=checklist[0], message_id=checklist[1])
        except Exception:
            pass

    # удаляем запись о сессии
    user_sessions.pop(user_id, None)
    if notify:
        try:
            await context.bot.send_message(chat_id=session.get("bot_messages", [(None, None)])[0][0] or session.get("checklist_msg", (session.get("bot_messages",[ (None,None) ])[0][0],None))[0],
                                           text="Сессия отменена.")
        except Exception:
            pass


async def show_object_by_id(obj_id: str, chat_id: int, context: ContextTypes.DEFAULT_TYPE):
    """Показываем файлы по объекту — читаем metadata и отправляем сгрупповано."""
    obj_dir = object_dir(obj_id)
    if not os.path.exists(obj_dir):
        await context.bot.send_message(chat_id=chat_id, text=f"Объект {obj_id} не найден.")
        return
    md = load_metadata(obj_dir)
    any_files = False
    await context.bot.send_message(chat_id=chat_id, text=f"Файлы по Объекту {obj_id}:")
    for key, title in CHECKLIST:
        files = md.get("files", {}).get(key, [])
        if not files:
            continue
        any_files = True
        await context.bot.send_message(chat_id=chat_id, text=f"🔹 {title}:")
        for entry in files:
            path = os.path.join(obj_dir, entry["filename"])
            try:
                caption = f"Объект {obj_id} — {title}\nОтправил: {entry.get('uploader_name','')} — {datetime.fromtimestamp(entry['ts']).strftime('%Y-%m-%d %H:%M')}"
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
        await context.bot.send_message(chat_id=chat_id, text="Файлы не найдены.")


async def cmd_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    await cleanup_session(user_id, context, notify=True)


# ---------- Инициализация приложения ----------
def main():
    if not TOKEN:
        log.error("BOT_TOKEN не задан. Установите переменную окружения BOT_TOKEN.")
        return

    app = ApplicationBuilder().token(TOKEN).build()

    # Регистрация команд
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("object", cmd_object))
    app.add_handler(CommandHandler("show", cmd_show_cmd))
    app.add_handler(CommandHandler("cancel", cmd_cancel))

    app.add_handler(CallbackQueryHandler(choose_callback))

    # Обработчик для файлов (фото, видео, документ)
    app.add_handler(MessageHandler(filters.PHOTO | filters.VIDEO | filters.Document.ALL, handle_file))

    # Обработчик текста, чтобы перехватить следующий ввод номера объекта после /object или /show
    app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), handle_text_next))

    # Устанавливаем список команд в меню (видно в Telegram)
    try:
        # методом run_until_complete — потому что set_my_commands — coroutine
        import asyncio
        cmds = [
            BotCommand("start", "Приветствие и инструкция"),
            BotCommand("object", "Начать загрузку — далее в следующем сообщении укажи номер объекта"),
            BotCommand("show", "Показать файлы по объекту — далее в следующем сообщении укажи номер"),
            BotCommand("cancel", "Отменить текущую сессию"),
        ]
        asyncio.get_event_loop().run_until_complete(app.bot.set_my_commands(cmds))
    except Exception as e:
        log.warning("Не удалось установить команды: %s", e)

    log.info("Starting bot...")
    app.run_polling()


if __name__ == "__main__":
    main()
