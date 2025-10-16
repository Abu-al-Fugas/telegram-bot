import os
import telebot
from telebot.types import (
    ReplyKeyboardMarkup, KeyboardButton,
    InlineKeyboardMarkup, InlineKeyboardButton,
    InputMediaPhoto, InputMediaVideo, InputMediaDocument
)
from flask import Flask, request
from datetime import datetime

# ========== НАСТРОЙКИ ==========
TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
ARCHIVE_CHAT_ID = os.environ.get("ARCHIVE_CHAT_ID", "-1003160855229")

bot = telebot.TeleBot(TOKEN)
app = Flask(__name__)

# ========== ГЛОБАЛЬНЫЕ ПЕРЕМЕННЫЕ ==========
user_state = {}
objects_data = {}

UPLOAD_STEPS = [
    "Общее фото помещения",
    "Фото корректора",
    "Фото существующей СТМ потребителя",
    "Фото места устанавливаемой СТМ",
    "Фото (ГРУ)",
    "Фото котлов относительно корректора и устанавливаемой СТМ",
    "Фото газового оборудования",
    "Фото точки подключения 220В",
    "Фото места прокладки кабелей",
    "Фото входных дверей снаружи",
    "Дополнительные фотографии"
]

MANDATORY_STEPS = {
    "Общее фото помещения",
    "Фото корректора",
    "Фото места устанавливаемой СТМ",
    "Фото места прокладки кабелей"
}

# ========== ХЕЛПЕРЫ ==========
def make_key(chat_id, thread_id, user_id):
    return (chat_id, thread_id, user_id)

def upload_keyboard(step_name):
    kb = InlineKeyboardMarkup(row_width=3)
    buttons = [InlineKeyboardButton("✅ OK", callback_data="upload_ok")]
    if step_name not in MANDATORY_STEPS:
        buttons.append(InlineKeyboardButton("➡️ Next", callback_data="upload_next"))
    buttons.append(InlineKeyboardButton("❌ Cancel", callback_data="upload_cancel"))
    kb.add(*buttons)
    return kb

def send_message(chat_id, text, reply_markup=None, thread_id=None):
    try:
        return bot.send_message(chat_id, text, reply_markup=reply_markup, message_thread_id=thread_id)
    except Exception as e:
        print(f"[send_message] Ошибка: {e}")

def delete_message(chat_id, msg_id):
    try:
        bot.delete_message(chat_id, msg_id)
    except Exception:
        pass

# ========== КОМАНДЫ ==========
@bot.message_handler(commands=["start"])
def start_message(message):
    text = (
        "🤖 Бот для управления объектами ИПУГ\n\n"
        "Доступные команды:\n"
        "/photo – загрузка файлов с чек-листом\n"
        "/addphoto – добавить файлы к существующему объекту\n"
        "/download – скачать файлы объекта\n"
        "/result – список обработанных объектов"
    )
    send_message(message.chat.id, text, thread_id=message.message_thread_id)

@bot.message_handler(commands=["photo"])
def cmd_photo(message):
    key = make_key(message.chat.id, message.message_thread_id, message.from_user.id)
    user_state[key] = {"command": "await_object"}
    send_message(message.chat.id, "Введите номер объекта для загрузки:", thread_id=message.message_thread_id)

@bot.message_handler(commands=["addphoto"])
def cmd_addphoto(message):
    key = make_key(message.chat.id, message.message_thread_id, message.from_user.id)
    user_state[key] = {"command": "await_addphoto_object"}
    send_message(message.chat.id, "Введите номер объекта, чтобы добавить фото:", thread_id=message.message_thread_id)

@bot.message_handler(commands=["download"])
def cmd_download(message):
    if not objects_data:
        send_message(message.chat.id, "📂 Нет сохранённых объектов.", thread_id=message.message_thread_id)
        return
    text = "📁 Доступные объекты:\n" + "\n".join([f"• {oid} – {len(data['steps'])} шагов" for oid, data in objects_data.items()])
    send_message(message.chat.id, text, thread_id=message.message_thread_id)

@bot.message_handler(commands=["result"])
def cmd_result(message):
    if not objects_data:
        send_message(message.chat.id, "📋 Нет завершённых загрузок.", thread_id=message.message_thread_id)
        return
    text = "✅ Завершённые загрузки:\n"
    for oid, data in objects_data.items():
        total_files = sum(len(s["files"]) for s in data["steps"])
        text += f"• Объект {oid}: {total_files} файлов\n"
    send_message(message.chat.id, text, thread_id=message.message_thread_id)

# ========== ОБРАБОТКА ТЕКСТА ==========
@bot.message_handler(func=lambda m: True, content_types=["text"])
def handle_text(message):
    key = make_key(message.chat.id, message.message_thread_id, message.from_user.id)
    state = user_state.get(key)

    if not state:
        return

    # начало загрузки
    if state["command"] == "await_object":
        object_id = message.text.strip()
        steps = [{"name": s, "files": []} for s in UPLOAD_STEPS]
        user_state[key] = {
            "command": "upload_steps",
            "object_id": object_id,
            "steps": steps,
            "step_index": 0,
            "chat_id": message.chat.id,
            "thread_id": message.message_thread_id
        }
        send_upload_step(key)

    # добавление фото к существующему объекту
    elif state["command"] == "await_addphoto_object":
        object_id = message.text.strip()
        user_state[key] = {
            "command": "add_photos",
            "object_id": object_id,
            "files": [],
            "chat_id": message.chat.id,
            "thread_id": message.message_thread_id
        }
        send_message(message.chat.id, f"📸 Отправьте фото для объекта {object_id}. Когда закончите, нажмите ✅ Завершить.", 
                     reply_markup=InlineKeyboardMarkup().add(
                         InlineKeyboardButton("✅ Завершить", callback_data="addphoto_done"),
                         InlineKeyboardButton("❌ Отмена", callback_data="upload_cancel")
                     ), thread_id=message.message_thread_id)

# ========== ОБРАБОТКА ФАЙЛОВ ==========
@bot.message_handler(content_types=["photo", "document", "video"])
def handle_files(message):
    key = make_key(message.chat.id, message.message_thread_id, message.from_user.id)
    state = user_state.get(key)
    if not state:
        return

    if state["command"] == "upload_steps":
        step = state["steps"][state["step_index"]]
        file_info = {}
        if message.photo:
            file_info = {"type": "photo", "file_id": message.photo[-1].file_id}
        elif message.document:
            file_info = {"type": "document", "file_id": message.document.file_id}
        elif message.video:
            file_info = {"type": "video", "file_id": message.video.file_id}
        step["files"].append(file_info)
        # удаляем старое сообщение и отправляем "Выберите действие"
        if "last_message_id" in state:
            delete_message(state["chat_id"], state["last_message_id"])
        msg = send_message(state["chat_id"], "Выберите действие:", 
                           reply_markup=upload_keyboard(step["name"]), 
                           thread_id=state["thread_id"])
        state["last_message_id"] = msg.message_id

    elif state["command"] == "add_photos":
        file_info = {}
        if message.photo:
            file_info = {"type": "photo", "file_id": message.photo[-1].file_id}
        elif message.document:
            file_info = {"type": "document", "file_id": message.document.file_id}
        elif message.video:
            file_info = {"type": "video", "file_id": message.video.file_id}
        state["files"].append(file_info)

# ========== CALLBACKS ==========
@bot.callback_query_handler(func=lambda call: call.data.startswith("upload_") or call.data.startswith("addphoto_"))
def handle_callback(call):
    key = make_key(call.message.chat.id, call.message.message_thread_id, call.from_user.id)
    state = user_state.get(key)
    if not state:
        bot.answer_callback_query(call.id, "Нет активной загрузки")
        return

    if call.data == "upload_ok":
        advance_step(key)
        bot.answer_callback_query(call.id, "✅ Шаг завершён")
    elif call.data == "upload_next":
        advance_step(key, skip=True)
        bot.answer_callback_query(call.id, "➡️ Пропущено")
    elif call.data == "upload_cancel":
        obj = state.get("object_id", "")
        user_state.pop(key, None)
        delete_message(call.message.chat.id, call.message.message_id)
        send_message(call.message.chat.id, f"❌ Загрузка для объекта {obj} отменена")
        bot.answer_callback_query(call.id, "Отменено")
    elif call.data == "addphoto_done":
        object_id = state["object_id"]
        if object_id not in objects_data:
            objects_data[object_id] = {"steps": []}
        save_to_archive(object_id, [{"name": "Дополнительные файлы", "files": state["files"]}], append=True)
        user_state.pop(key, None)
        delete_message(call.message.chat.id, call.message.message_id)
        send_message(call.message.chat.id, f"✅ Дополнительные файлы для объекта {object_id} сохранены.")
        bot.answer_callback_query(call.id, "Готово ✅")

# ========== ПРОДВИЖЕНИЕ ШАГОВ ==========
def send_upload_step(key):
    state = user_state[key]
    step = state["steps"][state["step_index"]]
    if "last_message_id" in state:
        delete_message(state["chat_id"], state["last_message_id"])
    msg = send_message(state["chat_id"], f"📸 Шаг: {step['name']}\nВыберите действие:", 
                       reply_markup=upload_keyboard(step["name"]),
                       thread_id=state["thread_id"])
    state["last_message_id"] = msg.message_id

def advance_step(key, skip=False):
    state = user_state[key]
    state["step_index"] += 1
    if state["step_index"] >= len(state["steps"]):
        object_id = state["object_id"]
        all_steps = state["steps"]
        save_to_archive(object_id, all_steps)
        objects_data[object_id] = {"steps": all_steps}
        user_state.pop(key, None)
        send_message(state["chat_id"], f"✅ Загрузка завершена для объекта {object_id}")
    else:
        send_upload_step(key)

# ========== СОХРАНЕНИЕ В АРХИВ ==========
def save_to_archive(object_id, steps, append=False):
    try:
        info_text = f"💾 ОБЪЕКТ #{object_id}\n🕒 {datetime.now().strftime('%d.%m.%Y %H:%M')}"
        bot.send_message(ARCHIVE_CHAT_ID, info_text)
        for s in steps:
            files = s["files"]
            if not files:
                continue
            media = []
            for f in files[:50]:
                if f["type"] == "photo":
                    media.append(InputMediaPhoto(f["file_id"]))
                elif f["type"] == "video":
                    media.append(InputMediaVideo(f["file_id"]))
                elif f["type"] == "document":
                    media.append(InputMediaDocument(f["file_id"]))
            if media:
                bot.send_media_group(ARCHIVE_CHAT_ID, media)
    except Exception as e:
        print(f"[save_to_archive] Ошибка: {e}")

# ========== WEBHOOK ==========
@app.route("/" + TOKEN, methods=["POST"])
def webhook():
    update = telebot.types.Update.de_json(request.get_data().decode("utf-8"))
    bot.process_new_updates([update])
    return "OK", 200

@app.route("/")
def index():
    return "🤖 Бот работает", 200

if __name__ == "__main__":
    print("🚀 Бот запущен...")
    WEBHOOK_URL = f"https://telegram-bot-b6pn.onrender.com/{TOKEN}"
    bot.remove_webhook()
    bot.set_webhook(url=WEBHOOK_URL)
    # команды видимы в группах и темах
    bot.set_my_commands([
        telebot.types.BotCommand("start", "Перезапустить бота"),
        telebot.types.BotCommand("photo", "Загрузить файлы по объекту"),
        telebot.types.BotCommand("addphoto", "Добавить файлы к объекту"),
        telebot.types.BotCommand("download", "Список объектов"),
        telebot.types.BotCommand("result", "Результаты загрузок")
    ])
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))
