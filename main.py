import os
import telebot
from telebot.types import ReplyKeyboardMarkup, KeyboardButton
from flask import Flask, request
from datetime import datetime

# ========== НАСТРОЙКИ ==========
TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
ARCHIVE_CHAT_ID = os.environ.get("ARCHIVE_CHAT_ID", "-1003160855229")

bot = telebot.TeleBot(TOKEN)
app = Flask(__name__)

# Глобальные переменные
user_state = {}  # ключ: (chat_id, user_id)
objects_data = {}  # тут можно подгрузить объекты из Excel если нужно
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
def make_key_from_message(message):
    return (message.chat.id, message.from_user.id)

def send_message(chat_id, text, reply_markup=None, thread_id=None):
    try:
        if thread_id:
            return bot.send_message(chat_id, text, reply_markup=reply_markup, message_thread_id=thread_id)
        return bot.send_message(chat_id, text, reply_markup=reply_markup)
    except Exception as e:
        print(f"[send_message] Ошибка: {e}")

def send_file(chat_id, file_type, file_id, caption=None):
    try:
        if file_type=='photo':
            bot.send_photo(chat_id, file_id, caption=caption)
        elif file_type=='document':
            bot.send_document(chat_id, file_id, caption=caption)
        elif file_type=='video':
            bot.send_video(chat_id, file_id, caption=caption)
    except Exception as e:
        print(f"[send_file] Ошибка: {e}")

# ========== КЛАВИАТУРЫ ==========
def main_keyboard():
    kb = ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    kb.add(
        KeyboardButton('/info'),
        KeyboardButton('/photo'),
        KeyboardButton('/download'),
        KeyboardButton('/result'),
        KeyboardButton('/help')
    )
    return kb

def upload_keyboard():
    kb = ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    kb.add(
        KeyboardButton('/OK'),  # завершить текущий шаг
        KeyboardButton('/next'),  # пропустить текущий шаг
        KeyboardButton('/cancel')
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
    send_message(message.chat.id, text, reply_markup=main_keyboard(), thread_id=message.message_thread_id)

@bot.message_handler(commands=['photo'])
def start_upload(message):
    key = make_key_from_message(message)
    send_message(message.chat.id, "Введите номер объекта для загрузки файлов:", thread_id=message.message_thread_id)
    user_state[key] = {'command': 'await_object'}

@bot.message_handler(func=lambda m: True, content_types=['text'])
def handle_text(message):
    key = make_key_from_message(message)
    state = user_state.get(key)
    if not state:
        return

    # Ввод объекта
    if state['command']=='await_object':
        object_id = message.text.strip()
        # для примера, все объекты разрешаем
        if not object_id:
            send_message(message.chat.id, "❌ Объект не найден", thread_id=message.message_thread_id)
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
        send_message(message.chat.id, f"📸 Загрузите {steps[0]['name']}", reply_markup=upload_keyboard(), thread_id=message.message_thread_id)

# Обработка файлов
@bot.message_handler(content_types=['photo','document','video'])
def handle_files(message):
    key = make_key_from_message(message)
    state = user_state.get(key)
    if not state or state.get('command')!='upload_steps':
        return
    step = state['steps'][state['step_index']]
    file_info = {}
    if message.photo:
        file_info = {'type':'photo','file_id':message.photo[-1].file_id}
    elif message.document:
        file_info = {'type':'document','file_id':message.document.file_id,'name':message.document.file_name}
    elif message.video:
        file_info = {'type':'video','file_id':message.video.file_id}
    step['files'].append(file_info)

# /OK - завершить шаг
@bot.message_handler(commands=['OK'])
def handle_ok(message):
    key = make_key_from_message(message)
    state = user_state.get(key)
    if not state or state.get('command')!='upload_steps':
        send_message(message.chat.id, "❌ Нет активной загрузки", reply_markup=main_keyboard(), thread_id=message.message_thread_id)
        return
    advance_step(key)

# /next - пропустить шаг
@bot.message_handler(commands=['next'])
def handle_next(message):
    key = make_key_from_message(message)
    state = user_state.get(key)
    if not state or state.get('command')!='upload_steps':
        send_message(message.chat.id, "❌ Нет активной загрузки", reply_markup=main_keyboard(), thread_id=message.message_thread_id)
        return
    advance_step(key, skip=True)

# /cancel
@bot.message_handler(commands=['cancel'])
def handle_cancel(message):
    key = make_key_from_message(message)
    state = user_state.pop(key, None)
    if state:
        obj = state.get('object_id','')
        send_message(message.chat.id, f"❌ Загрузка для объекта {obj} отменена", reply_markup=main_keyboard(), thread_id=message.message_thread_id)

# ========== ЛОГИКА ПРОДВИЖЕНИЯ ШАГОВ ==========
def advance_step(key, skip=False):
    state = user_state[key]
    state['step_index'] += 1
    if state['step_index'] >= len(state['steps']):
        # все шаги завершены
        object_id = state['object_id']
        all_steps = state['steps']
        save_to_archive(object_id, all_steps)
        report = f"✅ Загрузка файлов завершена для объекта #{object_id}\n\n"
        for i, s in enumerate(all_steps, 1):
            report += f"{i}. ✅ {s['name']}: {len(s['files'])} файлов\n"
        send_message(state['chat_id'], report, reply_markup=main_keyboard(), thread_id=state['thread_id'])
        user_state.pop(key)
    else:
        next_step = state['steps'][state['step_index']]
        send_message(state['chat_id'], f"📸 Загрузите {next_step['name']}", reply_markup=upload_keyboard(), thread_id=state['thread_id'])

# ========== АРХИВ ==========
def save_to_archive(object_id, all_steps):
    try:
        info_text = f"💾 ОБЪЕКТ #{object_id}\n📁 {sum(len(s['files']) for s in all_steps)} файлов\n🕒 {datetime.now().strftime('%d.%m.%Y %H:%M')}"
        send_message(ARCHIVE_CHAT_ID, info_text)
        for step in all_steps:
            for f in step['files']:
                send_file(ARCHIVE_CHAT_ID, f['type'], f['file_id'])
        return True
    except Exception as e:
        print(f"[save_to_archive] Ошибка: {e}")
        return False

# ========== WEBHOOK ==========
@app.route('/' + TOKEN, methods=['POST'])
def receive_update():
    json_str = request.get_data().decode('utf-8')
    update = telebot.types.Update.de_json(json_str)
    bot.process_new_updates([update])
    return "OK", 200

@app.route('/')
def index():
    return "🤖 Бот работает", 200

# ========== RUN ==========
if __name__=="__main__":
    print("🚀 Бот запускается...")
    bot.remove_webhook()
    WEBHOOK_URL = f"https://telegram-bot-b6pn.onrender.com/{TOKEN}"
    bot.set_webhook(url=WEBHOOK_URL)
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT",10000)))

