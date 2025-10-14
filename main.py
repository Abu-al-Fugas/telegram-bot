import os
import json
import telebot
from telebot.types import ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove
from flask import Flask, request
from datetime import datetime
import gspread
from google.oauth2.service_account import Credentials
import openpyxl

# ========== НАСТРОЙКИ ==========
TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
ARCHIVE_CHAT_ID = os.environ.get("ARCHIVE_CHAT_ID", "-1003160855229")
GOOGLE_SHEETS_KEY = os.environ.get("GOOGLE_SHEETS_KEY")

bot = telebot.TeleBot(TOKEN)
app = Flask(__name__)

# Глобальные переменные
user_state = {}      # теперь ключ — (chat_id, message_thread_id, user_id)
objects_data = {}
object_files = {}

# ========== ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ==========
def make_key(message):
    """Создаёт уникальный ключ состояния для пользователя в конкретной теме"""
    return (message.chat.id, message.message_thread_id, message.from_user.id)

def send_message_with_topic(chat_id, text, message_thread_id=None, reply_markup=None, parse_mode=None):
    try:
        if message_thread_id:
            return bot.send_message(chat_id, text, reply_markup=reply_markup, parse_mode=parse_mode, message_thread_id=message_thread_id)
        return bot.send_message(chat_id, text, reply_markup=reply_markup, parse_mode=parse_mode)
    except Exception as e:
        print(f"Ошибка отправки сообщения: {e}")

def send_photo_with_topic(chat_id, photo, message_thread_id=None, caption=None):
    try:
        if message_thread_id:
            return bot.send_photo(chat_id, photo, caption=caption, message_thread_id=message_thread_id)
        return bot.send_photo(chat_id, photo, caption=caption)
    except Exception as e:
        print(f"Ошибка отправки фото: {e}")

def send_document_with_topic(chat_id, document, message_thread_id=None, caption=None):
    try:
        if message_thread_id:
            return bot.send_document(chat_id, document, caption=caption, message_thread_id=message_thread_id)
        return bot.send_document(chat_id, document, caption=caption)
    except Exception as e:
        print(f"Ошибка отправки документа: {e}")

def send_video_with_topic(chat_id, video, message_thread_id=None, caption=None):
    try:
        if message_thread_id:
            return bot.send_video(chat_id, video, caption=caption, message_thread_id=message_thread_id)
        return bot.send_video(chat_id, video, caption=caption)
    except Exception as e:
        print(f"Ошибка отправки видео: {e}")

# ========== ЗАГРУЗКА ДАННЫХ ==========
def load_objects_from_excel():
    try:
        workbook = openpyxl.load_workbook('objects.xlsx')
        sheet = workbook.active
        data = {}
        for row in sheet.iter_rows(min_row=2, values_only=True):
            if row[0]:
                data[str(row[0]).strip()] = {'name': row[1] or '', 'address': row[2] or '', 'status': 'Не начат'}
        return data
    except Exception as e:
        print(f"Ошибка загрузки Excel: {e}")
        return {}

# ========== GOOGLE SHEETS ==========
def init_google_sheets():
    try:
        if GOOGLE_SHEETS_KEY:
            creds_dict = json.loads(GOOGLE_SHEETS_KEY)
            creds = Credentials.from_service_account_info(creds_dict)
            return gspread.authorize(creds)
    except Exception as e:
        print(f"Ошибка инициализации Google Sheets: {e}")
    return None

def update_google_sheets(object_id, status="✅ Обработан"):
    try:
        client = init_google_sheets()
        if not client:
            return False
        sheet = client.open("Объекты ИПУГ").sheet1
        data = sheet.get_all_values()
        for i, row in enumerate(data, start=1):
            if i == 1:
                continue
            if row and str(row[0]).strip() == str(object_id):
                sheet.update_cell(i, 4, status)
                return True
        return False
    except Exception as e:
        print(f"Ошибка обновления Google Sheets: {e}")
        return False

# ========== АРХИВ ==========
def save_to_archive(object_id, files, file_types):
    try:
        desc = []
        if file_types.get('photos'): desc.append(f"📸 {file_types['photos']} фото")
        if file_types.get('documents'): desc.append(f"📄 {file_types['documents']} док.")
        if file_types.get('videos'): desc.append(f"🎥 {file_types['videos']} видео")
        info_text = f"""
💾 ОБЪЕКТ #{object_id}
📁 {len(files)} {' + '.join(desc)}
🕒 {datetime.now().strftime('%d.%m.%Y %H:%M')}
        """
        bot.send_message(ARCHIVE_CHAT_ID, info_text.strip())
        for f in files:
            try:
                if f['type'] == 'photo':
                    bot.send_photo(ARCHIVE_CHAT_ID, f['file_id'])
                elif f['type'] == 'document':
                    bot.send_document(ARCHIVE_CHAT_ID, f['file_id'])
                elif f['type'] == 'video':
                    bot.send_video(ARCHIVE_CHAT_ID, f['file_id'])
            except Exception as e:
                print(f"Ошибка архивации файла: {e}")
        return True
    except Exception as e:
        print(f"Ошибка архива: {e}")
        return False

# ========== КЛАВИАТУРЫ ==========
def create_main_keyboard():
    kb = ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    kb.add(KeyboardButton('/info'), KeyboardButton('/upload'),
           KeyboardButton('/download'), KeyboardButton('/processed'),
           KeyboardButton('/help'))
    return kb

def create_upload_keyboard():
    kb = ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    kb.add(KeyboardButton('/done'), KeyboardButton('/cancel'), KeyboardButton('/help'))
    return kb

def create_processed_keyboard():
    kb = ReplyKeyboardMarkup(resize_keyboard=True, row_width=3)
    buttons = [KeyboardButton(f"📁 #{obj}") for obj in sorted(object_files.keys())]
    for i in range(0, len(buttons), 3):
        kb.add(*buttons[i:i+3])
    kb.add(KeyboardButton('/help'))
    return kb

# ========== КОМАНДЫ ==========
@bot.message_handler(commands=['start', 'help'])
def start_message(message):
    text = """
🤖 Бот для управления объектами ИПУГ

📋 Команды:
/info — информация об объекте
/upload — загрузить файлы  
/download — скачать файлы
/processed — список обработанных объектов
/help — помощь
    """
    send_message_with_topic(message.chat.id, text.strip(),
                            message.message_thread_id, create_main_keyboard())

@bot.message_handler(commands=['info'])
def ask_info_object(message):
    key = make_key(message)
    user_state[key] = {'command': 'info', 'chat_id': message.chat.id, 'message_thread_id': message.message_thread_id}
    send_message_with_topic(message.chat.id, "🔍 Введите номер объекта:", message.message_thread_id, create_main_keyboard())

@bot.message_handler(commands=['upload'])
def ask_upload_object(message):
    key = make_key(message)
    user_state[key] = {'command': 'upload', 'chat_id': message.chat.id, 'message_thread_id': message.message_thread_id}
    send_message_with_topic(message.chat.id, "📤 Введите номер объекта для загрузки файлов:", message.message_thread_id, create_main_keyboard())

@bot.message_handler(commands=['download'])
def handle_download(message):
    parts = message.text.split()
    key = make_key(message)
    if len(parts) == 1:
        user_state[key] = {'command': 'download', 'chat_id': message.chat.id, 'message_thread_id': message.message_thread_id}
        send_message_with_topic(message.chat.id, "📥 Введите номер объекта для скачивания:", message.message_thread_id, create_main_keyboard())
    else:
        download_object_files(message, parts[1])

@bot.message_handler(commands=['done'])
def handle_done(message):
    key = make_key(message)
    if key not in user_state or user_state[key].get('command') != 'upload_files':
        send_message_with_topic(message.chat.id, "❌ Нет активной загрузки", message.message_thread_id, create_main_keyboard())
        return
    state = user_state[key]
    object_id, files, file_types = state['object_id'], state['files'], state['file_types']
    if not files:
        send_message_with_topic(message.chat.id, "❌ Нет файлов", message.message_thread_id, create_main_keyboard())
        user_state.pop(key, None)
        return
    object_files[object_id] = files
    save_to_archive(object_id, files, file_types)
    update_google_sheets(object_id)
    user_state.pop(key, None)
    send_message_with_topic(message.chat.id, f"✅ Файлы сохранены для объекта #{object_id}", message.message_thread_id, create_main_keyboard())

@bot.message_handler(commands=['cancel'])
def cancel_upload(message):
    key = make_key(message)
    if key in user_state and user_state[key].get('command') == 'upload_files':
        obj = user_state[key]['object_id']
        user_state.pop(key, None)
        send_message_with_topic(message.chat.id, f"❌ Загрузка для #{obj} отменена", message.message_thread_id, create_main_keyboard())

@bot.message_handler(func=lambda m: True, content_types=['text'])
def handle_text(message):
    if message.text.startswith('/'):
        return
    key = make_key(message)
    if key not in user_state:
        return
    state = user_state[key]
    cmd = state.get('command')
    if cmd == 'info':
        process_info_object(message, state, key)
    elif cmd == 'upload':
        process_upload_object(message, state, key)
    elif cmd == 'download':
        process_download_object(message, state, key)

def process_info_object(message, state, key):
    ids = [i.strip() for i in message.text.split(',')]
    res = []
    for i in ids:
        obj = objects_data.get(i)
        if obj:
            processed = "✅" if i in object_files else "⏳"
            res.append(f"{processed} #{i}\n🏢 {obj['name']}\n📍 {obj['address']}\n📊 {obj['status']}")
        else:
            res.append(f"❌ Объект #{i} не найден")
    send_message_with_topic(state['chat_id'], "\n\n".join(res), state['message_thread_id'], create_main_keyboard())
    user_state.pop(key, None)

def process_upload_object(message, state, key):
    obj_id = message.text.strip()
    if obj_id not in objects_data:
        send_message_with_topic(state['chat_id'], f"❌ Объект #{obj_id} не найден", state['message_thread_id'], create_main_keyboard())
        user_state.pop(key, None)
        return
    user_state[key] = {
        'command': 'upload_files', 'object_id': obj_id,
        'chat_id': state['chat_id'], 'message_thread_id': state['message_thread_id'],
        'files': [], 'file_types': {'photos': 0, 'documents': 0, 'videos': 0}
    }
    send_message_with_topic(state['chat_id'], f"📎 Отправляйте файлы для объекта #{obj_id}\nКогда закончите — /done\nДля отмены — /cancel", state['message_thread_id'], create_upload_keyboard())

def process_download_object(message, state, key):
    obj_id = message.text.strip()
    user_state.pop(key, None)
    download_object_files(message, obj_id)

@bot.message_handler(content_types=['photo', 'document', 'video'])
def handle_files(message):
    key = make_key(message)
    if key not in user_state or user_state[key].get('command') != 'upload_files':
        return
    s = user_state[key]
    f_info = {}
    if message.photo:
        f_info = {'type': 'photo', 'file_id': message.photo[-1].file_id}
        s['file_types']['photos'] += 1
    elif message.document:
        f_info = {'type': 'document', 'file_id': message.document.file_id, 'name': message.document.file_name}
        s['file_types']['documents'] += 1
    elif message.video:
        f_info = {'type': 'video', 'file_id': message.video.file_id}
        s['file_types']['videos'] += 1
    s['files'].append(f_info)

def download_object_files(message, obj_id):
    if obj_id not in object_files:
        send_message_with_topic(message.chat.id, f"❌ Для #{obj_id} нет файлов", message.message_thread_id, create_main_keyboard())
        return
    send_message_with_topic(message.chat.id, f"📁 Отправляю файлы для #{obj_id}...", message.message_thread_id, create_main_keyboard())
    count = 0
    for f in object_files[obj_id]:
        try:
            if f['type'] == 'photo':
                send_photo_with_topic(message.chat.id, f['file_id'], message.message_thread_id)
            elif f['type'] == 'document':
                send_document_with_topic(message.chat.id, f['file_id'], message.message_thread_id)
            elif f['type'] == 'video':
                send_video_with_topic(message.chat.id, f['file_id'], message.message_thread_id)
            count += 1
        except Exception as e:
            print(f"Ошибка отправки файла: {e}")
    send_message_with_topic(message.chat.id, f"✅ Отправлено {count} файлов", message.message_thread_id, create_main_keyboard())

@bot.message_handler(commands=['processed'])
def show_processed_objects(message):
    if not object_files:
        send_message_with_topic(message.chat.id, "📭 Нет обработанных объектов", message.message_thread_id, create_main_keyboard())
        return
    kb = create_processed_keyboard()
    send_message_with_topic(message.chat.id, f"📊 Обработано объектов: {len(object_files)}\n👇 Выберите:", message.message_thread_id, kb)

@bot.message_handler(func=lambda m: m.text.startswith('📁 #'))
def handle_download_button(message):
    obj_id = message.text.replace('📁 #', '').strip()
    download_object_files(message, obj_id)

# ========== WEBHOOK ==========
@app.route('/' + TOKEN, methods=['POST'])
def receive_update():
    update = telebot.types.Update.de_json(request.get_data().decode('utf-8'))
    bot.process_new_updates([update])
    return "OK", 200

@app.route('/')
def index():
    return "🤖 Бот для управления объектами ИПУГ работает! ✅", 200

if __name__ == "__main__":
    print("🚀 Запуск бота...")
    objects_data = load_objects_from_excel()
    print(f"📊 Загружено объектов: {len(objects_data)}")
    if init_google_sheets():
        print("✅ Google Sheets подключен")
    bot.remove_webhook()
    WEBHOOK_URL = f"https://telegram-bot-b6pn.onrender.com/{TOKEN}"
    bot.set_webhook(url=WEBHOOK_URL)
    print(f"🌐 Webhook установлен: {WEBHOOK_URL}")
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))
