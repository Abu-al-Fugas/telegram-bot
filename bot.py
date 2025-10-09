# bot.py - финальная версия: регистрация, временное хранилище, чек-лист, удаление временных файлов
# + встроенный мини-web-server (Flask) с /healthz чтобы Render видел открытый порт
import os
import json
import time
import logging
import threading
from datetime import datetime
from typing import Dict, Any, List, Optional
from flask import Flask

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

TOKEN = os.getenv("BOT_TOKEN")  # обязательно установить в Render env
if not TOKEN:
    log.error("BOT_TOKEN не задан. Установите переменную окружения BOT_TOKEN.")
BASE_DIR = os.getenv("DATA_DIR", "data")  # по умолчанию "./data"
USERS_PATH = os.path.join(BASE_DIR, "users.json")

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
awaiting_object: Dict[int, Dict[str, Any]] = {}
awaiting_registration: Dict[int, Dict[str, Any]] = {}
user_sessions: Dict[int, Dict[str, Any]] = {}

os.makedirs(BASE_DIR, exist_ok=True)


# ---------- Flask — health endpoint для Render / UptimeRobot ----------
flask_app = Flask(__name__)

@flask_app.route("/healthz")
def health_check():
    return "OK", 200

def run_flask():
    # Render экспортирует PORT, используем его. По умолчанию 10000
    port = int(os.environ.get("PORT", 10000))
    log.info("Starting Flask health server on port %s", port)
    # Запускаем без debug, чтобы не блокировать
    flask_app.run(host="0.0.0.0", port=port)


# ---------- Пользователи (users.json) ----------
def load_users() -> dict:
    if not os.path.exists(USERS_PATH):
        return {}
    try:
        with open(USERS_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        log.exception("Не удалось прочитать users.json: %s", e)
        return {}


def save_users(d: dict):
    os.makedirs(os.path.dirname(USERS_PATH), exist_ok=True)
    with open(USERS_PATH, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, indent=2)


def is_registered(user_id: int) -> bool:
    users = load_users()
    return str(user_id) in users


def register_user(user_id: int, full_name: str, phone: str, tg_user: Dict[str, Any]):
    users = load_users()
    users[str(user_id)] = {
        "full_name": full_name,
        "phone": phone,
        "username": tg_user.get("username"),
        "first_name": tg_user.get("first_name"),
        "last_name": tg_user.get("last_name"),
        "registered_at": datetime.utcnow().isoformat()
    }
    save_users(users)


def get_user_display(user_id: int) -> Optional[str]:
    users = load_users()
    u = users.get(str(user_id))
    if u:
        return f"{u.get('full_name')} ({u.get('phone')})"
    return None


# ---------- Файловая структура и метаданные ----------
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


# ---------- Вспомогательные функции ----------
def record_bot_message(session: Dict[str, Any], chat_id: int, msg_id: int):
    if session is None:
        return
    session.setdefault("bot_messages", []).append((chat_id, msg_id))


async def safe_delete(bot, chat_id: int, msg_id: int):
    try:
        await bot.delete_message(chat_id=chat_id, message_id=msg_id)
    except Exception:
        pass


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
    keyboard = [[b] for b in buttons]
    keyboard.append([
        InlineKeyboardButton("✅ Завершить загрузку", callback_data="finish"),
        InlineKeyboardButton("❌ Отмена", callback_data="cancel")
    ])
    return "\n".join(text_lines), InlineKeyboardMarkup(keyboard)


# ---------- Обработчики команд и сообщений ----------
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    txt = (
        "Привет! Я помогу загрузить файлы по объектам и автоматически выложить их в чат.\n\n"
        "Команды:\n"
        "/object — начать загрузку (в следующем сообщении укажи номер объекта)\n"
        "/register — зарегистрировать ФИО и телефон (если ещё не сделал(а))\n"
        "/cancel — отменить текущую сессию\n\n"
        "Пример: отправь `/object`, затем в следующем сообщении `15`."
    )
    await update.message.reply_text(txt)


async def cmd_register(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id
    prompt = await update.message.reply_text("Пожалуйста, отправьте в следующем сообщении свои данные в формате:\nИванов Иван Иванович, +79998887766")
    awaiting_registration[user_id] = {"chat_id": chat_id, "prompt_msg": (prompt.chat_id, prompt.message_id)}
    try:
        await update.message.delete()
    except Exception:
        pass


async def cmd_object(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id

    # Если пользователь не зарегистрирован — попросим зарегистрироваться
    if not is_registered(user_id):
        prompt = await update.message.reply_text("Вам нужно зарегистрироваться перед загрузкой. Отправьте /register или пришлите в ответ ФИО и телефон в формате:\nИванов Иван Иванович, +79998887766")
        awaiting_registration[user_id] = {"chat_id": chat_id, "prompt_msg": (prompt.chat_id, prompt.message_id)}
        try:
            await update.message.delete()
        except Exception:
            pass
        return

    prompt = await update.message.reply_text("Введите номер объекта (например: 15).")
    awaiting_object[user_id] = {"action": "object", "chat_id": chat_id, "prompt_msg": (prompt.chat_id, prompt.message_id)}
    try:
        await update.message.delete()
    except Exception:
        pass


async def handle_text_next(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обрабатываем текстовые сообщения как регистрацию или как номер объекта (если ожидается)."""
    user_id = update.effective_user.id
    text = (update.message.text or "").strip()
    if not text:
        return

    # 1) Если ожидается регистрация — принимаем
    reg = awaiting_registration.pop(user_id, None)
    if reg:
        # Ожидаем формат: "ФИО, +7999..." (но не строго — попытаемся распарсить)
        parts = [p.strip() for p in text.split(",")]
        if len(parts) >= 2:
            full_name = parts[0]
            phone = parts[1]
            register_user(user_id, full_name, phone, {
                "username": update.effective_user.username,
                "first_name": update.effective_user.first_name,
                "last_name": update.effective_user.last_name
            })
            # ответим пользователю (в том чате, где он ответил)
            reply = await update.message.reply_text("Спасибо! Вы зарегистрированы ✅")
            # удалим prompt о регистрации и сообщение пользователя
            try:
                if reg.get("prompt_msg"):
                    await context.bot.delete_message(chat_id=reg["prompt_msg"][0], message_id=reg["prompt_msg"][1])
            except Exception:
                pass
            try:
                await update.message.delete()
            except Exception:
                pass
            return
        else:
            # неверный формат — попросим повторить
            awaiting_registration[user_id] = reg  # вернуть ожидание
            warn = await update.message.reply_text("Неправильный формат. Пожалуйста, пришлите: Иванов Иван Иванович, +79998887766")
            try:
                await update.message.delete()
            except Exception:
                pass
            try:
                await context.bot.delete_message(chat_id=warn.chat_id, message_id=warn.message_id)
            except Exception:
                pass
            return

    # 2) Если ожидали номер объекта — обрабатываем
    pending = awaiting_object.pop(user_id, None)
    if pending:
        action = pending.get("action")
        chat_id = pending.get("chat_id")
        prompt_msg = pending.get("prompt_msg")
        obj_id = text.split()[0]
        if not obj_id.isdigit():
            # неверный формат — попросим ввести только цифры (и вернём ожидание)
            awaiting_object[user_id] = pending
            warn = await update.message.reply_text("Неверный формат номера объекта. Введите, пожалуйста, только цифры, например: 15")
            try:
                await update.message.delete()
            except Exception:
                pass
            try:
                await context.bot.delete_message(chat_id=warn.chat_id, message_id=warn.message_id)
            except Exception:
                pass
            return

        # создаём сессию загрузки
        obj_dir = object_dir(obj_id)
        ensure_object_dirs(obj_dir)
        md = load_metadata(obj_dir)
        save_metadata(obj_dir, md)

        session = {
            "object_id": obj_id,
            "pending_item": None,
            "checklist_msg": None,
            "bot_messages": [],
            "created_at": int(time.time())
        }
        user_sessions[user_id] = session

        text_msg, markup = build_checklist_text_and_keyboard(obj_id)
        sent = await context.bot.send_message(chat_id=chat_id, text=text_msg, reply_markup=markup)
        session["checklist_msg"] = (sent.chat_id, sent.message_id)
        record_bot_message(session, sent.chat_id, sent.message_id)

        # удаляем prompt (от /object) и сообщение пользователя с номером
        try:
            if prompt_msg:
                await context.bot.delete_message(chat_id=prompt_msg[0], message_id=prompt_msg[1])
        except Exception:
            pass
        try:
            await update.message.delete()
        except Exception:
            pass
        return

    # 3) Обычный текст — ничего не делаем
    return


async def choose_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка чек-листа: выбор пункта / finish / cancel."""
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    session = user_sessions.get(user_id)
    if not session:
        await query.message.reply_text("Сессия не найдена. Начните с /object <номер>.")
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
        sent = await query.message.reply_text(
            f"Отправьте файл(ы) для: {item_title}\n"
            "Можно отправлять несколько сообщений (фото/видео/документы).\n"
            "Когда всё отправите — нажмите '✅ Завершить загрузку'."
        )
        record_bot_message(session, sent.chat_id, sent.message_id)
    elif data == "finish":
        await handle_finish_by_user(user_id, query.message.chat_id, context)
    elif data == "cancel":
        await cleanup_session(user_id, context, notify=True)
    else:
        await query.message.reply_text("Неизвестная команда.")


async def handle_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Сохраняем файл во временную папку, удаляем исходное сообщение, обновляем чек-лист."""
    msg = update.message
    user_id = msg.from_user.id
    session = user_sessions.get(user_id)
    if not session:
        # запрос регистрации/инструкции
        warn = await msg.reply_text("Сначала начните с /object, затем в следующем сообщении введите номер объекта.")
        try:
            await context.bot.delete_message(chat_id=warn.chat_id, message_id=warn.message_id)
        except Exception:
            pass
        try:
            await msg.delete()
        except Exception:
            pass
        return

    pending = session.get("pending_item")
    if not pending:
        # просим выбрать пункт чек-листа
        obj_id = session.get("object_id")
        text_msg, markup = build_checklist_text_and_keyboard(obj_id)
        sent = await msg.reply_text("Пожалуйста, сначала нажмите кнопку чек-листа — для какого пункта вы загружаете файл.", reply_markup=markup)
        record_bot_message(session, sent.chat_id, sent.message_id)
        try:
            await msg.delete()
        except Exception:
            pass
        return

    obj_id = session.get("object_id")
    obj_dir = object_dir(obj_id)
    ensure_object_dirs(obj_dir)
    md = load_metadata(obj_dir)

    # Получаем файл
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
            # не распознано
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

    # Сохраняем файл временно
    timestamp = int(time.time())
    safe_name = original_name.replace(" ", "_")
    filename = f"{timestamp}_{file_obj.file_unique_id}_{safe_name}"
    save_folder = os.path.join(obj_dir, pending)
    os.makedirs(save_folder, exist_ok=True)
    save_path = os.path.join(save_folder, filename)
    try:
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

    # Сохраняем запись в metadata
    entry = {
        "filename": os.path.relpath(save_path, obj_dir),
        "uploader_id": user_id,
        "uploader_name": get_user_display(user_id) or msg.from_user.full_name or "",
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

    # Обновляем чек-лист (редактируем основное сообщение бота)
    checklist = session.get("checklist_msg")
    if checklist:
        chat_id, msg_id = checklist
        text_msg, markup = build_checklist_text_and_keyboard(obj_id)
        try:
            await context.bot.edit_message_text(text=text_msg, chat_id=chat_id, message_id=msg_id, reply_markup=markup)
        except Exception:
            pass

    # уведомление о сохранении (и записываем его id, чтобы удалить позже)
    notif = await context.bot.send_message(chat_id=msg.chat_id, text=f"Файл сохранён для '{dict(CHECKLIST)[pending]}' (Объект {obj_id}).")
    record_bot_message(session, notif.chat_id, notif.message_id)


async def handle_finish_by_user(user_id: int, chat_id: int, context: ContextTypes.DEFAULT_TYPE):
    """Формируем агрегированный отчёт, отправляем файлы, затем удаляем временные файлы и служебные сообщения."""
    session = user_sessions.get(user_id)
    if not session:
        await context.bot.send_message(chat_id=chat_id, text="Сессия не найдена.")
        return

    obj_id = session.get("object_id")
    obj_dir = object_dir(obj_id)
    md = load_metadata(obj_dir)

    # Подпись загрузчика: стараемся брать из users.json, иначе использовать tg name
    uploader_display = get_user_display(user_id) or (context.bot.get_chat_member(chat_id=user_id).user.full_name if False else None)

    summary_msg = await context.bot.send_message(chat_id=chat_id, text=f"Собираю файлы по Объекту {obj_id}...")
    record_bot_message(session, summary_msg.chat_id, summary_msg.message_id)

    any_files = False
    # Отправляем сгруппировано
    for key, title in CHECKLIST:
        files = md.get("files", {}).get(key, [])
        if not files:
            continue
        any_files = True
        header_msg = await context.bot.send_message(chat_id=chat_id, text=f"🔹 {title}:")
        record_bot_message(session, header_msg.chat_id, header_msg.message_id)
        for entry in files:
            path = os.path.join(obj_dir, entry["filename"])
            caption = f"Объект {obj_id} — {title}\nОтправил: {entry.get('uploader_name','(неизвестно)')} — {datetime.fromtimestamp(entry['ts']).strftime('%Y-%m-%d %H:%M')}"
            try:
                if entry["file_type"] == "photo":
                    await context.bot.send_photo(chat_id=chat_id, photo=open(path, "rb"), caption=caption)
                elif entry["file_type"] == "video":
                    await context.bot.send_video(chat_id=chat_id, video=open(path, "rb"), caption=caption)
                else:
                    await context.bot.send_document(chat_id=chat_id, document=open(path, "rb"), caption=caption)
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

    # После успешной отправки — удаляем временные локальные файлы для этого объекта
    try:
        shutil.rmtree(obj_dir, ignore_errors=True)
    except Exception as e:
        log.exception("Ошибка при удалении временных файлов: %s", e)

    # Удаляем все служебные сообщения бота, связанные с сессией
    bot_msgs: List[Any] = session.get("bot_messages", []) or []
    for (c, m_id) in bot_msgs:
        try:
            await safe_delete(context.bot, c, m_id)
        except Exception:
            pass

    # Попробуем удалить чек-лист
    checklist = session.get("checklist_msg")
    if checklist:
        try:
            await safe_delete(context.bot, checklist[0], checklist[1])
        except Exception:
            pass

    # Закрываем сессию
    user_sessions.pop(user_id, None)


async def cleanup_session(user_id: int, context: ContextTypes.DEFAULT_TYPE, notify: bool = True):
    """Отмена сессии: удаляем временные сообщения и директории (если нужно)"""
    session = user_sessions.get(user_id)
    if not session:
        # уведомим, если нужно
        if notify:
            try:
                await context.bot.send_message(chat_id=context.bot.id, text="Сессия не найдена.")
            except Exception:
                pass
        return

    # удаляем временные сообщения
    bot_msgs: List[Any] = session.get("bot_messages", []) or []
    for (c, m_id) in bot_msgs:
        try:
            await safe_delete(context.bot, c, m_id)
        except Exception:
            pass
    # удаляем чек-лист
    checklist = session.get("checklist_msg")
    if checklist:
        try:
            await safe_delete(context.bot, checklist[0], checklist[1])
        except Exception:
            pass
    # удаляем временную папку с файлами
    obj_id = session.get("object_id")
    if obj_id:
        try:
            shutil.rmtree(object_dir(obj_id), ignore_errors=True)
        except Exception:
            pass
    # удаляем сессию
    user_sessions.pop(user_id, None)
    if notify:
        # отправим краткое подтверждение в чат (попытка)
        try:
            await context.bot.send_message(chat_id=checklist[0] if checklist else context.bot.id, text="Сессия отменена.")
        except Exception:
            pass


# ---------- Инициализация ----------
def main():
    # Запускаем Flask health-сервер в отдельном демоническом потоке
    flask_thread = threading.Thread(target=run_flask, daemon=True)
    flask_thread.start()

    if not TOKEN:
        log.error("BOT_TOKEN не задан. Установите переменную окружения BOT_TOKEN.")
        return

    app = ApplicationBuilder().token(TOKEN).build()

    # Регистрация команд
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("register", cmd_register))
    app.add_handler(CommandHandler("object", cmd_object))
    app.add_handler(CommandHandler("cancel", lambda u, c: cleanup_session(u.effective_user.id, c)))

    # Хендлеры
    app.add_handler(CallbackQueryHandler(choose_callback))
    app.add_handler(MessageHandler(filters.PHOTO | filters.VIDEO | filters.Document.ALL, handle_file))
    app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), handle_text_next))

    # Устанавливаем видимые команды в меню
    try:
        import asyncio
        cmds = [
            BotCommand("start", "Приветствие и инструкция"),
            BotCommand("object", "Начать загрузку — далее в следующем сообщении укажи номер объекта"),
            BotCommand("register", "Зарегистрировать ФИО и телефон"),
            BotCommand("cancel", "Отменить текущую сессию"),
        ]
        asyncio.get_event_loop().run_until_complete(app.bot.set_my_commands(cmds))
    except Exception as e:
        log.warning("Не удалось установить команды: %s", e)

    log.info("Starting bot...")
    # Запускаем polling (процесс будет работать; Flask держит порт открытым)
    app.run_polling()

if __name__ == "__main__":
    main()
