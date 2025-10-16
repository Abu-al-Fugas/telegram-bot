import os
from datetime import datetime
from flask import Flask, request
import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton

# ========== НАСТРОЙКИ ==========
TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
ARCHIVE_CHAT_ID = os.environ.get("ARCHIVE_CHAT_ID", "-1003160855229")

bot = telebot.TeleBot(TOKEN)
app = Flask(__name__)

# Хранилище состояния пользователей: ключ = (chat_id, thread_id, user_id)
user_state = {}

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

# ========== HELPERS ==========
def make_key(message):
    """Создает уникальный ключ состояния для пользователя в теме"""
    thread_id = getattr(message, "message_thread_id", None)
    return (message.chat.id, thread_id, message.from_user.id)

def send_message(chat_id, text, reply_markup=None, thread_id=None):
    bot.send_message(chat_id, text, reply_markup=reply_markup, message_thread_id=thread_id)

def send_file(chat_id, file_type, file_id, caption=None):
    if file_type == "photo":
        bot.send_photo(chat_id, file_id, caption=caption)
    elif file_type == "document":
        bot.send_document(chat_id, file_id, caption=caption)
    elif file_type == "video":
        bot.send_video(chat_id, file_id, caption=caption)

# ========== INLINE КЛАВИАТУРЫ ==========
def upload_inline_keyboard():
    kb = InlineKeyboardMarkup(row_width=3)
    kb.add(
        InlineKeyboardButton("✅ OK", callback_data="upload_ok"),
        InlineKeyboardButton("➡️ Next", callback_data="upload_next"),
        InlineKeyboardButton("❌ Cancel", callback_data="upload_cancel")
    )
    return kb

def main_inline_keyboard():
    kb = InlineKeyboardMarkup(row_width=2)
    kb.add(
        InlineKeyboardButton("/info", callback_data="cmd_info"),
        InlineKeyboardButton("/photo", callback_data="cmd_photo"),
        InlineKeyboardButton("/download", callback_data="cmd_download"),
        InlineKeyboardButton("/result", callback_data="cmd_result"),
        InlineKeyboardButton("/help", callback_data="cmd_help")
    )
    return kb

# ========== КОМАНДЫ ==========
@bot.message_handler(commands=['start', 'help'])
def start_message(message):
    text = (
        "🤖 Бот для управления объектами ИПУГ\n\n"
        "Доступные команды:\n"
        "/info - информация об объекте\n"
        "/photo - загрузка файлов с чек-листом\n"
        "/download - скачать файлы объекта\n"
        "/result - список обработанных объектов\n"
    )
    send_message(message.chat.id, text, reply_markup=main_inline_keyboard(), thread_id=message.message_thread_id)

@bot.message_handler(commands=['photo'])
def start_upload(message):
    key = make_key(message)
    send_message(message.chat.id, "Введите номер объекта для загрузки файлов:", thread_id=message.message_thread_id)
    user_state[key] = {'command': 'await_object'}

# ========== ОБРАБОТКА ТЕКСТА ==========
@bot.message_handler(content_types=['text'])
def handle_text(message):
    key = make_key(message)
    state = user_state.get(key)
    if not state:
        return

    # Ввод номера объекта
    if state['command'] == 'await_object':
        object_id = message.text.strip()
        if not object_id:
            send_message(message.chat.id, "❌ Укажите корректный номер объекта.", thread_id=message.message_thread_id)
            return

        steps = [{'name': s, 'files': []} for s in UPLOAD_STEPS]
        user_state[key] = {
            'command': 'upload_steps',
            'object_id': object_id,
            'step_index': 0,
            'steps': steps,
            'chat_id': message.chat.id,
            'thread_id': message.message_thread_id
        }

        # Отправляем первый шаг с инлайн-кнопками
        send_message(
            message.chat.id,
            f"📸 Загрузите {steps[0]['name']}",
            reply_markup=upload_inline_keyboard(),
            thread_id=message.message_thread_id
        )

# ========== ОБРАБОТКА ФАЙЛОВ ==========
@bot.message_handler(content_types=['photo', 'document', 'video'])
def handle_files(message):
    key = make_key(message)
    state = user_state.get(key)
    if not state or state.get('command') != 'upload_steps':
        return

    step = state['steps'][state['step_index']]

    if message.photo:
        step['files'].append({'type': 'photo', 'file_id': message.photo[-1].file_id})
    elif message.document:
        step['files'].append({'type': 'document', 'file_id': message.document.file_id})
    elif message.video:
        step['files'].append({'type': 'video', 'file_id': message.video.file_id})

    send_message(message.chat.id, "✅ Файл сохранён.", thread_id=state['thread_id'])

# ========== ОБРАБОТКА CALLBACK ==========
@bot.callback_query_handler(func=lambda call: call.data.startswith("upload_"))
def handle_upload_callback(call):
    key = (call.message.chat.id, call.message.message_thread_id, call.from_user.id)
    state = user_state.get(key)
    if not state or state.get('command') != 'upload_steps':
        bot.answer_callback_query(call.id, "Нет активной загрузки")
        return

    if call.data == "upload_ok":
        advance_step(key)
        bot.answer_callback_query(call.id, "Шаг завершён ✅")
    elif call.data == "upload_next":
        advance_step(key, skip=True)
        bot.answer_callback_query(call.id, "Шаг пропущен ➡️")
    elif call.data == "upload_cancel":
        obj = state.get('object_id', '')
        user_state.pop(key, None)
        bot.edit_message_text(f"❌ Загрузка объекта {obj} отменена", call.message.chat.id, call.message.message_id)
        bot.answer_callback_query(call.id, "Загрузка отменена ❌")

# ========== ПРОГРЕСС ==========
def advance_step(key, skip=False):
    state = user_state[key]
    state['step_index'] += 1

    if state['step_index'] >= len(state['steps']):
        object_id = state['object_id']
        all_steps = state['steps']
        save_to_archive(object_id, all_steps)

        report = f"✅ Загрузка завершена для объекта #{object_id}\n\n"
        for i, s in enumerate(all_steps, 1):
            report += f"{i}. {s['name']}: {len(s['files'])} файлов\n"

        send_message(state['chat_id'], report, thread_id=state['thread_id'])
        user_state.pop(key)
    else:
        next_step = state['steps'][state['step_index']]
        send_message(
            state['chat_id'],
            f"📸 Загрузите {next_step['name']}",
            reply_markup=upload_inline_keyboard(),
            thread_id=state['thread_id']
        )

# ========== АРХИВ ==========
def save_to_archive(object_id, all_steps):
    total_files = sum(len(s['files']) for s in all_steps)
    info = f"💾 ОБЪЕКТ #{object_id}\n📁 {total_files} файлов\n🕒 {datetime.now().strftime('%d.%m.%Y %H:%M')}"
    send_message(ARCHIVE_CHAT_ID, info)

    for step in all_steps:
        for f in step['files']:
            send_file(ARCHIVE_CHAT_ID, f['type'], f['file_id'])

# ========== WEBHOOK ==========
@app.route('/' + TOKEN, methods=['POST'])
def receive_update():
    update = telebot.types.Update.de_json(request.data.decode('utf-8'))
    bot.process_new_updates([update])
    return "OK", 200

@app.route('/')
def index():
    return "🤖 Бот работает", 200

# ========== ЗАПУСК ==========
if __name__ == "__main__":
    print("🚀 Бот запускается...")
    bot.remove_webhook()
    WEBHOOK_URL = f"https://telegram-bot-b6pn.onrender.com/{TOKEN}"
    bot.set_webhook(url=WEBHOOK_URL)
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))
