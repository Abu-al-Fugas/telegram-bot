#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
bot.py — Telegram bot for uploading files per object with minimal prompts.
Features:
- registration (flexible input)
- per-user sessions (choose object, choose checklist item, upload files)
- temporary local storage under data/object_<id>/... (deleted after finish)
- minimal prompts & notifications; previous notifications deleted when moving steps
- send photos as media groups (batches of up to 10)
- flask /healthz for Render + UptimeRobot ping
- safe deletion of bot's helper messages to avoid chat spam
"""
import os
import json
import time
import logging
import threading
import shutil
from datetime import datetime
from typing import Dict, Any, List, Optional, Tuple

from flask import Flask

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    BotCommand,
    InputMediaPhoto,
    InputMediaVideo,
)
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)

# ---------------- Configuration ----------------
logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)

TOKEN = os.getenv("BOT_TOKEN")  # set this in Render / environment
if not TOKEN:
    log.error("BOT_TOKEN not set. Please set BOT_TOKEN env var before running.")
BASE_DIR = os.getenv("DATA_DIR", "data")
USERS_PATH = os.path.join(BASE_DIR, "users.json")

# Checklist items: key -> title
CHECKLIST = [
    ("photo_old_meter", "Фото заменяемого счётчика"),
    ("photo_old_seals", "Фото пломб старого счётчика"),
    ("photo_new_meter", "Фото нового счётчика"),
    ("photo_new_seals", "Фото пломб нового счётчика"),
    ("photo_passport", "Фото паспорта нового счётчика"),
    ("photo_after_install", "Фото после монтажа"),
    ("video_leak_test", "Видео проверки герметичности"),
]

# ensure base dir exists
os.makedirs(BASE_DIR, exist_ok=True)

# ---------------- In-memory session structures ----------------
# awaiting_object: user_id -> {"chat_id": int, "prompt_msg": (chat_id,msg_id)}
awaiting_object: Dict[int, Dict[str, Any]] = {}

# awaiting_registration: user_id -> {"chat_id": int, "prompt_msg": (chat_id,msg_id)}
awaiting_registration: Dict[int, Dict[str, Any]] = {}

# user_sessions: user_id -> session dict
# session structure:
# {
#   "object_id": str,
#   "pending_item": Optional[str],
#   "checklist_msg": (chat_id, message_id) or None,
#   "bot_messages": [(chat_id, message_id), ...]   -- temporary notifications to remove,
#   "last_notif": (chat_id, message_id) or None,  -- last "Файл сохранён." notif to delete on next step
#   "created_at": int
# }
user_sessions: Dict[int, Dict[str, Any]] = {}

# ---------------- Flask health server for Render / UptimeRobot ----------------
flask_app = Flask(__name__)

@flask_app.route("/healthz")
def health_check():
    return "OK", 200

def run_flask():
    port = int(os.environ.get("PORT", 10000))
    log.info("Starting Flask health server on port %s", port)
    flask_app.run(host="0.0.0.0", port=port)

# ---------------- Users storage helpers ----------------
def load_users() -> dict:
    if not os.path.exists(USERS_PATH):
        return {}
    try:
        with open(USERS_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        log.exception("Error reading users.json: %s", e)
        return {}

def save_users(d: dict):
    os.makedirs(os.path.dirname(USERS_PATH), exist_ok=True)
    with open(USERS_PATH, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, indent=2)

def is_registered(user_id: int) -> bool:
    users = load_users()
    return str(user_id) in users

def normalize_name(name: str) -> str:
    # Make reasonable title-casing for Russian names (simple approach)
    parts = [p.strip().capitalize() for p in name.split() if p.strip()]
    return " ".join(parts)

def normalize_phone(s: str) -> Optional[str]:
    # Keep only digits and convert to +7... if plausible
    digits = "".join(ch for ch in s if ch.isdigit())
    if not digits:
        return None
    # If 11 digits and starts with 8 or 7, convert to +7...
    if len(digits) == 11:
        if digits[0] == "8":
            digits = "7" + digits[1:]
        return "+" + digits
    # If 10 digits, assume Russian local number -> +7XXXXXXXXXX
    if len(digits) == 10:
        return "+7" + digits
    # For other lengths, keep with leading +
    return "+" + digits

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

# ---------------- File / metadata helpers ----------------
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
            log.exception("Error reading metadata.json: %s", e)
            return {"files": {}}
    # create structure
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

# ---------------- Utility helpers ----------------
def record_bot_message(session: Dict[str, Any], chat_id: int, msg_id: int):
    if session is None:
        return
    session.setdefault("bot_messages", []).append((chat_id, msg_id))

async def safe_delete(bot, chat_id: int, msg_id: int):
    try:
        await bot.delete_message(chat_id=chat_id, message_id=msg_id)
    except Exception:
        pass

def build_checklist_keyboard(obj_id: str) -> InlineKeyboardMarkup:
    # We keep only buttons (no long text). Buttons themselves show title with ✅/❌.
    obj_dir = object_dir(obj_id)
    md = load_metadata(obj_dir)
    buttons = []
    for key, title in CHECKLIST:
        got = bool(md.get("files", {}).get(key))
        mark = "✅" if got else "❌"
        buttons.append([InlineKeyboardButton(f"{mark} {title}", callback_data=f"choose|{key}")])
    # finish / cancel
    buttons.append([
        InlineKeyboardButton("✅ Завершить загрузку", callback_data="finish"),
        InlineKeyboardButton("❌ Отмена", callback_data="cancel")
    ])
    return InlineKeyboardMarkup(buttons)

# ---------------- Handlers ----------------
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "Привет! Для начала используйте /object, затем отправьте номер объекта.\n"
        "Регистрация: /register\nОтмена: /cancel"
    )
    await update.message.reply_text(text)

async def cmd_register(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id
    prompt = await update.message.reply_text("Отправьте: ФИО, телефон")
    awaiting_registration[user_id] = {"chat_id": chat_id, "prompt_msg": (prompt.chat_id, prompt.message_id)}
    try:
        await update.message.delete()
    except Exception:
        pass

async def cmd_object(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id

    # check registration
    if not is_registered(user_id):
        prompt = await update.message.reply_text("Сначала зарегистрируйтесь: /register")
        awaiting_registration[user_id] = {"chat_id": chat_id, "prompt_msg": (prompt.chat_id, prompt.message_id)}
        try:
            await update.message.delete()
        except Exception:
            pass
        return

    prompt = await update.message.reply_text("Введите номер объекта (например: 15)")
    awaiting_object[user_id] = {"chat_id": chat_id, "prompt_msg": (prompt.chat_id, prompt.message_id)}
    try:
        await update.message.delete()
    except Exception:
        pass

async def handle_text_next(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    text = (update.message.text or "").strip()
    if not text:
        return

    # registration flow
    reg = awaiting_registration.pop(user_id, None)
    if reg:
        parts = [p.strip() for p in text.split(",")]
        if len(parts) >= 2:
            raw_name = parts[0]
            raw_phone = parts[1]
            name = normalize_name(raw_name)
            phone = normalize_phone(raw_phone) or raw_phone
            register_user(user_id, name, phone, {
                "username": update.effective_user.username,
                "first_name": update.effective_user.first_name,
                "last_name": update.effective_user.last_name
            })
            # acknowledge
            reply = await update.message.reply_text("✅ Вы зарегистрированы")
            # delete prompt and user message
            try:
                if reg.get("prompt_msg"):
                    await context.bot.delete_message(chat_id=reg["prompt_msg"][0], message_id=reg["prompt_msg"][1])
            except Exception:
                pass
            try:
                await update.message.delete()
            except Exception:
                pass
            # delete ack after short time (optional) — keep minimal; here we remove after 6s
            try:
                await context.application.create_task(async_delete_later(context.bot, reply.chat_id, reply.message_id, delay=6))
            except Exception:
                pass
            return
        else:
            # try more relaxed parsing: if user just sent digits and name separated by space
            # keep awaiting_registration
            awaiting_registration[user_id] = reg
            warn = await update.message.reply_text("Неверно. Отправьте: ФИО, телефон")
            try:
                await context.application.create_task(async_delete_later(context.bot, warn.chat_id, warn.message_id, delay=6))
            except Exception:
                pass
            try:
                await update.message.delete()
            except Exception:
                pass
            return

    # object flow
    pending = awaiting_object.pop(user_id, None)
    if pending:
        chat_id = pending.get("chat_id")
        prompt_msg = pending.get("prompt_msg")
        obj_id = text.split()[0]
        if not obj_id.isdigit():
            awaiting_object[user_id] = pending
            warn = await update.message.reply_text("Введите, пожалуйста, только цифры.")
            try:
                await context.application.create_task(async_delete_later(context.bot, warn.chat_id, warn.message_id, delay=6))
            except Exception:
                pass
            try:
                await update.message.delete()
            except Exception:
                pass
            return

        # create session
        obj_dir = object_dir(obj_id)
        ensure_object_dirs(obj_dir)
        md = load_metadata(obj_dir)
        save_metadata(obj_dir, md)

        session = {
            "object_id": obj_id,
            "pending_item": None,
            "checklist_msg": None,
            "bot_messages": [],
            "last_notif": None,
            "created_at": int(time.time())
        }
        user_sessions[user_id] = session

        # send header + buttons (no long repeated text)
        header = await context.bot.send_message(chat_id=chat_id, text=f"Объект {obj_id}", reply_markup=build_checklist_keyboard(obj_id))
        session["checklist_msg"] = (header.chat_id, header.message_id)
        record_bot_message(session, header.chat_id, header.message_id)

        # delete prompt and user's message
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

    # otherwise regular text -> ignore
    return

async def choose_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    session = user_sessions.get(user_id)
    if not session:
        await query.message.reply_text("Сессия не найдена. /object")
        return
    data = query.data
    if data.startswith("choose|"):
        key = data.split("|", 1)[1]
        valid_keys = [k for k, _ in CHECKLIST]
        if key not in valid_keys:
            await query.message.reply_text("Неверный пункт.")
            return
        # delete previous short notification (if any) when switching to a new pending item
        last_notif = session.get("last_notif")
        if last_notif:
            try:
                await safe_delete(context.bot, last_notif[0], last_notif[1])
            except Exception:
                pass
            session["last_notif"] = None

        session["pending_item"] = key
        # minimal prompt as message reply (we try to keep chat clean by deleting user's original if applicable)
        # Instead of long text, send a very short instruction
        short = await query.message.reply_text(f"{dict(CHECKLIST)[key]} — отправьте файл(ы).")
        record_bot_message(session, short.chat_id, short.message_id)
        # delete the callback message? we keep header with buttons; no extra long text
    elif data == "finish":
        await handle_finish_by_user(user_id, query.message.chat_id, context)
    elif data == "cancel":
        await cleanup_session(user_id, context, notify=True)
    else:
        await query.message.reply_text("Неизвестная команда.")

async def handle_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    if not msg:
        return
    user_id = msg.from_user.id
    session = user_sessions.get(user_id)
    if not session:
        # prompt minimal and delete both
        warn = await msg.reply_text("Запустите /object")
        try:
            await context.application.create_task(async_delete_later(context.bot, warn.chat_id, warn.message_id, delay=5))
        except Exception:
            pass
        try:
            await msg.delete()
        except Exception:
            pass
        return

    pending = session.get("pending_item")
    if not pending:
        # ask to choose a checklist item (short)
        checklist_msg = session.get("checklist_msg")
        if checklist_msg:
            try:
                await context.bot.send_message(chat_id=msg.chat_id, text="Выберите пункт в чек-листе.", reply_markup=build_checklist_keyboard(session["object_id"]))
            except Exception:
                pass
        try:
            await msg.delete()
        except Exception:
            pass
        return

    obj_id = session.get("object_id")
    obj_dir = object_dir(obj_id)
    ensure_object_dirs(obj_dir)
    md = load_metadata(obj_dir)

    # determine file object
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
            # unsupported content
            try:
                await msg.delete()
            except Exception:
                pass
            return
    except Exception as e:
        log.exception("Error getting file: %s", e)
        try:
            await msg.delete()
        except Exception:
            pass
        return

    # save file
    timestamp = int(time.time())
    safe_name = (original_name or "file").replace(" ", "_")
    filename = f"{timestamp}_{file_obj.file_unique_id}_{safe_name}"
    save_folder = os.path.join(obj_dir, pending)
    os.makedirs(save_folder, exist_ok=True)
    save_path = os.path.join(save_folder, filename)
    try:
        # PTB file.download_to_drive custom_path available; fallback if TypeError
        await file_obj.download_to_drive(custom_path=save_path)
    except TypeError:
        await file_obj.download_to_drive(save_path)
    except Exception as e:
        log.exception("Error saving file: %s", e)
        try:
            await msg.delete()
        except Exception:
            pass
        return

    # metadata entry
    entry = {
        "filename": os.path.relpath(save_path, obj_dir),
        "uploader_id": user_id,
        "uploader_name": get_user_display(user_id) or (msg.from_user.full_name or ""),
        "ts": timestamp,
        "file_type": file_type,
        "original_name": original_name,
    }
    md.setdefault("files", {})
    md["files"].setdefault(pending, []).append(entry)
    save_metadata(obj_dir, md)

    # delete user's original message to keep chat clean
    try:
        await msg.delete()
    except Exception:
        pass

    # update checklist message (edit header buttons to show checkmarks)
    checklist = session.get("checklist_msg")
    if checklist:
        try:
            await context.bot.edit_message_text(chat_id=checklist[0], message_id=checklist[1], text=f"Объект {obj_id}", reply_markup=build_checklist_keyboard(obj_id))
        except Exception:
            pass

    # send a very short notification and remember it, so we can delete it on next step
    try:
        notif = await context.bot.send_message(chat_id=checklist[0] if checklist else msg.chat_id, text="Файл сохранён.")
        # delete previous notification if present (safety)
        prev_notif = session.get("last_notif")
        if prev_notif:
            try:
                await safe_delete(context.bot, prev_notif[0], prev_notif[1])
            except Exception:
                pass
        session["last_notif"] = (notif.chat_id, notif.message_id)
        record_bot_message(session, notif.chat_id, notif.message_id)
        # schedule auto-delete after some time (optional) — but user requested delete when moving to next stage; we'll also delete on next item selection
        await context.application.create_task(async_delete_later(context.bot, notif.chat_id, notif.message_id, delay=60))
    except Exception:
        pass

# ---------------- Finish: send media groups and cleanup ----------------
async def handle_finish_by_user(user_id: int, chat_id: int, context: ContextTypes.DEFAULT_TYPE):
    session = user_sessions.get(user_id)
    if not session:
        await context.bot.send_message(chat_id=chat_id, text="Сессия не найдена.")
        return

    obj_id = session.get("object_id")
    obj_dir = object_dir(obj_id)
    md = load_metadata(obj_dir)

    # try to get uploader display from users.json
    uploader_display = get_user_display(user_id) or (context.application.bot.get_me().username if False else None)
    # minimal summary message
    summary = await context.bot.send_message(chat_id=chat_id, text=f"Собираю файлы для объекта {obj_id}...")
    record_bot_message(session, summary.chat_id, summary.message_id)

    # Collect all photos across checklist into a single list (order: by ts)
    photo_entries: List[Dict[str, Any]] = []
    other_entries: List[Dict[str, Any]] = []
    for key, _ in CHECKLIST:
        for entry in md.get("files", {}).get(key, []):
            if entry.get("file_type") == "photo":
                photo_entries.append(entry)
            else:
                other_entries.append(entry)
    # sort by timestamp
    photo_entries.sort(key=lambda e: e.get("ts", 0))
    other_entries.sort(key=lambda e: e.get("ts", 0))

    # send photos in media groups batches of up to 10
    try:
        if photo_entries:
            # build batches
            batch = []
            for idx, entry in enumerate(photo_entries, start=1):
                path = os.path.join(obj_dir, entry["filename"])
                caption = None
                # Telegram allows caption only on first item of media group; we make caption minimal
                if len(batch) == 0:
                    uploader = entry.get("uploader_name") or ""
                    caption = f"Объект {obj_id} — Загружено: {uploader}"
                try:
                    batch.append(InputMediaPhoto(open(path, "rb"), caption=caption))
                except Exception as e:
                    log.exception("Error preparing photo %s: %s", path, e)
                # send when batch full or last
                if len(batch) == 10:
                    try:
                        await context.bot.send_media_group(chat_id=chat_id, media=batch)
                    except Exception as e:
                        log.exception("Error sending media group: %s", e)
                    # close files in batch
                    for m in batch:
                        try:
                            m.media.close()
                        except Exception:
                            pass
                    batch = []
            # send remainder
            if batch:
                try:
                    await context.bot.send_media_group(chat_id=chat_id, media=batch)
                except Exception as e:
                    log.exception("Error sending final media group: %s", e)
                for m in batch:
                    try:
                        m.media.close()
                    except Exception:
                        pass
    except Exception as e:
        log.exception("Error sending photo groups: %s", e)

    # send other files (videos / documents) individually with minimal caption
    for entry in other_entries:
        path = os.path.join(obj_dir, entry["filename"])
        uploader = entry.get("uploader_name") or ""
        caption = f"Объект {obj_id} — {uploader}"
        try:
            if entry.get("file_type") == "video":
                await context.bot.send_video(chat_id=chat_id, video=open(path, "rb"), caption=caption)
            else:
                await context.bot.send_document(chat_id=chat_id, document=open(path, "rb"), caption=caption)
        except Exception as e:
            log.exception("Error sending other file %s: %s", path, e)
            try:
                await context.bot.send_message(chat_id=chat_id, text=f"Не удалось отправить файл {entry.get('original_name')}")
            except Exception:
                pass

    # final done message (minimal)
    try:
        done = await context.bot.send_message(chat_id=chat_id, text="Готово.")
        record_bot_message(session, done.chat_id, done.message_id)
    except Exception:
        pass

    # cleanup local files for object
    try:
        shutil.rmtree(obj_dir, ignore_errors=True)
    except Exception as e:
        log.exception("Error deleting tmp files: %s", e)

    # delete temporary bot messages related to session (prompts, not final gallery)
    bot_msgs: List[Tuple[int, int]] = session.get("bot_messages", []) or []
    for (c, m_id) in bot_msgs:
        try:
            await safe_delete(context.bot, c, m_id)
        except Exception:
            pass
    # delete checklist header
    checklist = session.get("checklist_msg")
    if checklist:
        try:
            await safe_delete(context.bot, checklist[0], checklist[1])
        except Exception:
            pass
    # Also delete last_notif if exists
    last_notif = session.get("last_notif")
    if last_notif:
        try:
            await safe_delete(context.bot, last_notif[0], last_notif[1])
        except Exception:
            pass

    # remove session
    user_sessions.pop(user_id, None)

async def cleanup_session(user_id: int, context: ContextTypes.DEFAULT_TYPE, notify: bool = True):
    session = user_sessions.get(user_id)
    if not session:
        if notify:
            try:
                await context.bot.send_message(chat_id=context.application.bot.id, text="Сессия не найдена.")
            except Exception:
                pass
        return
    # delete bot msgs
    for c, m in (session.get("bot_messages") or []):
        try:
            await safe_delete(context.bot, c, m)
        except Exception:
            pass
    # delete checklist
    checklist = session.get("checklist_msg")
    if checklist:
        try:
            await safe_delete(context.bot, checklist[0], checklist[1])
        except Exception:
            pass
    # delete last notif
    last_notif = session.get("last_notif")
    if last_notif:
        try:
            await safe_delete(context.bot, last_notif[0], last_notif[1])
        except Exception:
            pass
    # delete temp folder
    obj_id = session.get("object_id")
    if obj_id:
        try:
            shutil.rmtree(object_dir(obj_id), ignore_errors=True)
        except Exception:
            pass
    # pop session
    user_sessions.pop(user_id, None)
    if notify:
        try:
            await context.bot.send_message(chat_id=checklist[0] if checklist else context.application.bot.id, text="Сессия отменена.")
        except Exception:
            pass

# ---------------- helper: async delete later ----------------
async def async_delete_later(bot, chat_id: int, message_id: int, delay: int = 6):
    # Sleep without blocking main thread in PTB context — use asyncio sleep via loop
    import asyncio
    await asyncio.sleep(delay)
    try:
        await bot.delete_message(chat_id=chat_id, message_id=message_id)
    except Exception:
        pass

# ---------------- Initialization & main ----------------
def main():
    # start flask thread to keep port open for Render
    flask_thread = threading.Thread(target=run_flask, daemon=True)
    flask_thread.start()

    if not TOKEN:
        log.error("BOT_TOKEN not set. Exiting.")
        return

    app = ApplicationBuilder().token(TOKEN).build()

    # command handlers
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("register", cmd_register))
    app.add_handler(CommandHandler("object", cmd_object))
    # cancel command — cleanup session
    async def cancel_wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        await cleanup_session(update.effective_user.id, context, notify=True)
    app.add_handler(CommandHandler("cancel", cancel_wrapper))

    # callback query for checklist
    app.add_handler(CallbackQueryHandler(choose_callback))

    # files handler
    app.add_handler(MessageHandler(filters.PHOTO | filters.VIDEO | filters.Document.ALL, handle_file))

    # text messages handler (for registration and object number)
    app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), handle_text_next))

    # set bot commands visible
    try:
        import asyncio
        cmds = [
            BotCommand("start", "Приветствие"),
            BotCommand("object", "Начать загрузку — затем номер объекта"),
            BotCommand("register", "Регистрация"),
            BotCommand("cancel", "Отмена сессии"),
        ]
        asyncio.get_event_loop().run_until_complete(app.bot.set_my_commands(cmds))
    except Exception as e:
        log.warning("Can't set commands: %s", e)

    log.info("Starting polling bot...")
    # Run polling (Flask keeps port open)
    app.run_polling()

if __name__ == "__main__":
    main()
