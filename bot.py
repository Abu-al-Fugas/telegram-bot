# bot.py — финальная версия с исправлениями: регистрация с гибкой проверкой, временное хранение, медиа-группы
import os
import json
import time
import logging
from datetime import datetime
from typing import Dict, Any, List
import shutil

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    BotCommand,
    InputMediaPhoto,
    InputMediaVideo,
    InputMediaDocument,
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
BASE_DIR = os.getenv("DATA_DIR", "data")
USERS_PATH = os.path.join(BASE_DIR, "users.json")

CHECKLIST = [
    ("photo_old_meter", "Фото заменяемого счётчика"),
    ("photo_old_seals", "Фото пломб старого счётчика"),
    ("photo_new_meter", "Фото нового счётчика"),
    ("photo_new_seals", "Фото пломб нового счётчика"),
    ("photo_passport", "Фото паспорта нового счётчика"),
    ("photo_after_install", "Фото после монтажа"),
    ("video_leak_test", "Видео проверки герметичности"),
]

awaiting_object: Dict[int, Dict[str, Any]] = {}
awaiting_registration: Dict[int, Dict[str, Any]] = {}
user_sessions: Dict[int, Dict[str, Any]] = {}

os.makedirs(BASE_DIR, exist_ok=True)

# ---------- Пользователи ----------
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
    full_name = " ".join([w.capitalize() for w in full_name.split()])
    phone_digits = "".join(filter(str.isdigit, phone))
    phone = f"+{phone_digits}" if not phone_digits.startswith("+") else phone_digits
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

def get_user_display(user_id: int) -> str:
    users = load_users()
    u = users.get(str(user_id))
    if u:
        return f"{u.get('full_name')} ({u.get('phone')})"
    return None

# ---------- Файловая структура ----------
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
    return {"files": {k: [] for k, _ in CHECKLIST}, "created_at": int(time.time())}

def save_metadata(obj_dir: str, md: dict):
    p = metadata_path(obj_dir)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        json.dump(md, f, ensure_ascii=False, indent=2)

def ensure_object_dirs(obj_dir: str):
    os.makedirs(obj_dir, exist_ok=True)
    for key, _ in CHECKLIST:
        os.makedirs(os.path.join(obj_dir, key), exist_ok=True)

# ---------- Вспомогательные ----------
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

# ---------- Обработчики ----------
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    txt = (
        "Привет! Я помогу загрузить файлы по объектам.\n\n"
        "Команды:\n"
        "/object — начать загрузку (укажи номер объекта)\n"
        "/register — зарегистрировать ФИО и телефон\n"
        "/cancel — отменить текущую сессию"
    )
    await update.message.reply_text(txt)

async def cmd_register(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id
    prompt = await update.message.reply_text(
        "Пожалуйста, отправьте свои данные в формате:\nИванов Иван Иванович, +79998887766"
    )
    awaiting_registration[user_id] = {"chat_id": chat_id, "prompt_msg": (prompt.chat_id, prompt.message_id)}
    try:
        await update.message.delete()
    except Exception:
        pass

async def cmd_object(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id
    if not is_registered(user_id):
        prompt = await update.message.reply_text(
            "Вам нужно зарегистрироваться перед загрузкой. Отправьте /register или пришлите ФИО и телефон."
        )
        awaiting_registration[user_id] = {"chat_id": chat_id, "prompt_msg": (prompt.chat_id, prompt.message_id)}
        try:
            await update.message.delete()
        except Exception:
            pass
        return
    prompt = await update.message.reply_text("Введите номер объекта:")
    awaiting_object[user_id] = {"action": "object", "chat_id": chat_id, "prompt_msg": (prompt.chat_id, prompt.message_id)}
    try:
        await update.message.delete()
    except Exception:
        pass

async def handle_text_next(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    text = (update.message.text or "").strip()
    if not text:
        return
    reg = awaiting_registration.pop(user_id, None)
    if reg:
        parts = [p.strip() for p in text.split(",")]
        if len(parts) >= 2:
            full_name, phone = parts[0], parts[1]
            register_user(user_id, full_name, phone, {
                "username": update.effective_user.username,
                "first_name": update.effective_user.first_name,
                "last_name": update.effective_user.last_name
            })
            reply = await update.message.reply_text("Спасибо! Вы зарегистрированы ✅")
            try:
                await context.bot.delete_message(chat_id=reg["prompt_msg"][0], message_id=reg["prompt_msg"][1])
            except Exception:
                pass
            try:
                await update.message.delete()
            except Exception:
                pass
            await safe_delete(context.bot, reply.chat_id, reply.message_id)
            return
        else:
            awaiting_registration[user_id] = reg
            warn = await update.message.reply_text("Неправильный формат. Попробуйте: Иванов Иван Иванович, 9998887766")
            try:
                await update.message.delete()
            except Exception:
                pass
            await safe_delete(context.bot, warn.chat_id, warn.message_id)
            return
    pending = awaiting_object.pop(user_id, None)
    if pending:
        obj_id = text.split()[0]
        if not obj_id.isdigit():
            awaiting_object[user_id] = pending
            warn = await update.message.reply_text("Неверный формат номера объекта. Только цифры, например: 15")
            try:
                await update.message.delete()
            except Exception:
                pass
            await safe_delete(context.bot, warn.chat_id, warn.message_id)
            return
        obj_dir = object_dir(obj_id)
        ensure_object_dirs(obj_dir)
        md = load_metadata(obj_dir)
        save_metadata(obj_dir, md)
        session = {"object_id": obj_id, "pending_item": None, "checklist_msg": None, "bot_messages": [], "created_at": int(time.time())}
        user_sessions[user_id] = session
        text_msg, markup = build_checklist_text_and_keyboard(obj_id)
        sent = await context.bot.send_message(chat_id=pending["chat_id"], text=text_msg, reply_markup=markup)
        session["checklist_msg"] = (sent.chat_id, sent.message_id)
        record_bot_message(session, sent.chat_id, sent.message_id)
        try:
            if pending.get("prompt_msg"):
                await context.bot.delete_message(chat_id=pending["prompt_msg"][0], message_id=pending["prompt_msg"][1])
        except Exception:
            pass
        try:
            await update.message.delete()
        except Exception:
            pass
        return

async def choose_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    session = user_sessions.get(user_id)
    if not session:
        await query.message.reply_text("Сессия не найдена. Начните с /object.")
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
            f"Отправьте файл(ы) для: {item_title}\nМожно отправлять несколько сообщений.\nКогда всё — нажмите ✅ Завершить загрузку."
        )
        record_bot_message(session, sent.chat_id, sent.message_id)
    elif data == "finish":
        await handle_finish_by_user(user_id, query.message.chat_id, context)
    elif data == "cancel":
        await cleanup_session(user_id, context)
    else:
        await query.message.reply_text("Неизвестная команда.")

# ---------- Файл ----------

async def handle_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    user_id = msg.from_user.id
    session = user_sessions.get(user_id)
    if not session:
        try:
            await msg.delete()
        except Exception:
            pass
        return
    pending = session.get("pending_item")
    if not pending:
        text_msg, markup = build_checklist_text_and_keyboard(session["object_id"])
        sent = await msg.reply_text("Сначала выберите пункт чек-листа.", reply_markup=markup)
        record_bot_message(session, sent.chat_id, sent.message_id)
        try:
            await msg.delete()
        except Exception:
            pass
        return

    obj_dir = object_dir(session["object_id"])
    ensure_object_dirs(obj_dir)
    md = load_metadata(obj_dir)

    file_obj = None
    file_type = None
    original_name = None
    try:
        if msg.photo:
            file_obj = await msg.photo[-1].get_file()
            file_type = "photo"
            original_name = f"{file_obj.file_unique_id}.jpg"
        elif msg.video:
            file_obj = await msg.video.get_file()
            file_type = "video"
            original_name = getattr(msg.video, "file_name", f"{file_obj.file_unique_id}.mp4")
        elif msg.document:
            file_obj = await msg.document.get_file()
            file_type = "document"
            original_name = getattr(msg.document, "file_name", None) or f"{file_obj.file_unique_id}.dat"
        else:
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

    timestamp = int(time.time())
    filename = f"{timestamp}_{original_name.replace(' ', '_')}"
    save_path = os.path.join(obj_dir, pending, filename)
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

    md.setdefault("files", {}).setdefault(pending, []).append({
        "filename": os.path.relpath(save_path, obj_dir),
        "uploader_id": user_id,
        "uploader_name": get_user_display(user_id) or msg.from_user.full_name or "",
        "ts": timestamp,
        "file_type": file_type,
        "original_name": original_name
    })
    save_metadata(obj_dir, md)
    try:
        await msg.delete()
    except Exception:
        pass

    # обновление чек-листа
    checklist = session.get("checklist_msg")
    if checklist:
        text_msg, markup = build_checklist_text_and_keyboard(session["object_id"])
        try:
            await context.bot.edit_message_text(text=text_msg, chat_id=checklist[0], message_id=checklist[1], reply_markup=markup)
        except Exception:
            pass

    # уведомление (сразу удаляется)
    notif = await context.bot.send_message(chat_id=msg.chat_id, text=f"Файл сохранён для '{dict(CHECKLIST)[pending]}'")
    record_bot_message(session, notif.chat_id, notif.message_id)
    await safe_delete(context.bot, notif.chat_id, notif.message_id)

# ---------- Отправка медиагруппы ----------
async def handle_finish_by_user(user_id: int, chat_id: int, context: ContextTypes.DEFAULT_TYPE):
    session = user_sessions.get(user_id)
    if not session:
        await context.bot.send_message(chat_id=chat_id, text="Сессия не найдена.")
        return

    obj_dir = object_dir(session["object_id"])
    md = load_metadata(obj_dir)
    for key, title in CHECKLIST:
        files = md.get("files", {}).get(key, [])
        if not files:
            continue
        media_group = []
        for entry in files:
            path = os.path.join(obj_dir, entry["filename"])
            caption = f"{title}\nОбъект {session['object_id']}\nОтправил: {entry['uploader_name']}"
            try:
                if entry["file_type"] == "photo":
                    media_group.append(InputMediaPhoto(open(path, "rb"), caption=caption))
                elif entry["file_type"] == "video":
                    media_group.append(InputMediaVideo(open(path, "rb"), caption=caption))
                else:
                    media_group.append(InputMediaDocument(open(path, "rb"), caption=caption))
            except Exception as e:
                log.exception("Ошибка отправки файла: %s", e)
        if media_group:
            try:
                await context.bot.send_media_group(chat_id=chat_id, media=media_group)
            except Exception as e:
                log.exception("Ошибка отправки медиагруппы: %s", e)

    # удаляем временные файлы и сессию
    try:
        shutil.rmtree(obj_dir, ignore_errors=True)
    except Exception:
        pass
    for c, m in session.get("bot_messages", []):
        try:
            await safe_delete(context.bot, c, m)
        except Exception:
            pass
    checklist_msg = session.get("checklist_msg")
    if checklist_msg:
        try:
            await safe_delete(context.bot, checklist_msg[0], checklist_msg[1])
        except Exception:
            pass
    user_sessions.pop(user_id, None)

async def cleanup_session(user_id: int, context: ContextTypes.DEFAULT_TYPE):
    session = user_sessions.get(user_id)
    if not session:
        return
    for c, m in session.get("bot_messages", []):
        try:
            await safe_delete(context.bot, c, m)
        except Exception:
            pass
    checklist_msg = session.get("checklist_msg")
    if checklist_msg:
        try:
            await safe_delete(context.bot, checklist_msg[0], checklist_msg[1])
        except Exception:
            pass
    obj_id = session.get("object_id")
    if obj_id:
        try:
            shutil.rmtree(object_dir(obj_id), ignore_errors=True)
        except Exception:
            pass
    user_sessions.pop(user_id, None)

# ---------- Инициализация ----------
def main():
    if not TOKEN:
        log.error("BOT_TOKEN не задан")
        return
    app = ApplicationBuilder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("register", cmd_register))
    app.add_handler(CommandHandler("object", cmd_object))
    app.add_handler(CommandHandler("cancel", lambda u, c: cleanup_session(u.effective_user.id, c)))
    app.add_handler(CallbackQueryHandler(choose_callback))
    app.add_handler(MessageHandler(filters.PHOTO | filters.VIDEO | filters.Document.ALL, handle_file))
    app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), handle_text_next))
    try:
        import asyncio
        cmds = [
            BotCommand("start", "Приветствие"),
            BotCommand("object", "Начать загрузку"),
            BotCommand("register", "Регистрация"),
            BotCommand("cancel", "Отмена сессии"),
        ]
        asyncio.get_event_loop().run_until_complete(app.bot.set_my_commands(cmds))
    except Exception:
        pass
    app.run_polling()

if __name__ == "__main__":
    main()
