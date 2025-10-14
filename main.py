import os
import json
import telebot
from telebot.types import ReplyKeyboardMarkup, KeyboardButton
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
# ключ состояния: (chat_id, user_id)
user_state = {}
objects_data = {}
object_files = {}

# ========== HELPERS ==========
def make_key_from_message(message):
    """Уникальный ключ состояния: (chat_id, user_id)"""
    # chat.id одинаков для всех в теме, from_user.id уникален для пользователя
    return (message.chat.id, message.from_user.id)

def send_message_with_topic(chat_id, text, message_thread_id=None, reply_markup=None, parse_mode=None):
    try:
        if message_thread_id:
            return bot.send_message(chat_id=chat_id, text=text, message_thread_id=message_thread_id,
                                    reply_markup=reply_markup, parse_mode=parse_mode)
        return bot.send_message(chat_id=chat_id, text=text, reply_markup=reply_markup, parse_mode=parse_mode)
    except Exception as e:
        print(f"[send_message_with_topic] Ошибка: {e}")

def send_photo_with_topic(chat_id, photo, message_thread_id=None, caption=None):
    try:
        if message_thread_id:
            return bot.send_photo(chat_id=chat_id, photo=photo, message_thread_id=message_thread_id, caption=caption)
        return bot.send_photo(chat_id=chat_id, photo=photo, caption=caption)
    except Exception as e:
        print(f"[send_photo_with_topic] Ошибка: {e}")

def send_document_with_topic(chat_id, document, message_thread_id=None, caption=None):
    try:
        if message_thread_id:
            return bot.send_document(chat_id=chat_id, document=document, message_thread_id=message_thread_id, caption=caption)
        return bot.send_document(chat_id=chat_id, document=document, caption=caption)
    except Exception as e:
        print(f"[send_document_with_topic] Ошибка: {e}")

def send_video_with_topic(chat_id, video, message_thread_id=None, caption=None):
    try:
        if message_thread_id:
            return bot.send_video(chat_id=chat_id, video=video, message_thread_id=message_thread_id, caption=caption)
        return bot.send_video(chat_id=chat_id, video=video, caption=caption)
    except Exception as e:
        print(f"[send_video_with_topic] Ошибка: {e}")

# ========== ЗАГРУЗКА ДАННЫХ ИЗ EXCEL ==========
def load_objects_from_excel():
    global objects_data
    try:
        workbook = openpyxl.load_workbook('objects.xlsx')
        sheet = workbook.active
        data = {}
        for row in sheet.iter_rows(min_row=2, values_only=True):
            if row[0] is not None:
                key = str(row[0]).strip()
                data[key] = {
                    'name': row[1] or '',
                    'address': row[2] or '',
                    'status': 'Не начат'
                }
        return data
    except Exception as e:
        print(f"[load_objects_from_excel] Ошибка загрузки Excel: {e}")
        return {}

# ========== GOOGLE SHEETS ==========
def init_google_sheets():
    try:
        if not GOOGLE_SHEETS_KEY:
            return None
        creds_dict = json.loads(GOOGLE_SHEETS_KEY)
        creds = Credentials.from_service_account_info(creds_dict)
        client = gspread.authorize(creds)
        return client
    except Exception as e:
        print(f"[init_google_sheets] Ошибка: {e}")
        return None

def update_google_sheets(object_id, status="✅ Обработан"):
    try:
        client = init_google_sheets()
        if not client:
            return False
        sheet = client.open("Объекты ИПУГ").sheet1
        all_data = sheet.get_all_values()
        for i, row in enumerate(all_data, start=1):
            if i == 1:
                continue
            if row and str(row[0]).strip() == str(object_id):
                sheet.update_cell(i, 4, status)
                return True
        return False
    except Exception as e:
        print(f"[update_google_sheets] Ошибка: {e}")
        return False

# ========== АРХИВ В TELEGRAM ==========
def save_to_archive(object_id, files, file_types):
    try:
        type_description = []
        if file_types.get('photos', 0) > 0:
            type_description.append(f"📸 {file_types['photos']} фото")
        if file_types.get('documents', 0) > 0:
            type_description.append(f"📄 {file_types['documents']} док.")
        if file_types.get('videos', 0) > 0:
            type_description.append(f"🎥 {file_types['videos']} видео")

        files_desc = " + ".join(type_description) if type_description else "файлы"

        info_text = f"""
💾 ОБЪЕКТ #{object_id}
📁 {len(files)} {files_desc}
🕒 {datetime.now().strftime('%d.%m.%Y %H:%M')}
        """
        bot.send_message(ARCHIVE_CHAT_ID, info_text.strip())

        for file_info in files:
            try:
                if file_info['type'] == 'photo':
                    bot.send_photo(ARCHIVE_CHAT_ID, file_info['file_id'])
                elif file_info['type'] == 'document':
                    bot.send_document(ARCHIVE_CHAT_ID, file_info['file_id'])
                elif file_info['type'] == 'video':
                    bot.send_video(ARCHIVE_CHAT_ID, file_info['file_id'])
            except Exception as e:
                print(f"[save_to_archive] Ошибка отправки файла в архив: {e}")

        return True
    except Exception as e:
        print(f"[save_to_archive] Ошибка: {e}")
        return False

# ========== КЛАВИАТУРЫ ==========
def create_main_keyboard():
    keyboard = ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    keyboard.add(
        KeyboardButton('/info'),
        KeyboardButton('/upload'),
        KeyboardButton('/download'),
        KeyboardButton('/processed'),
        KeyboardButton('/help')
    )
    return keyboard

def create_upload_keyboard():
    keyboard = ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    keyboard.add(
        KeyboardButton('/done'),
        KeyboardButton('/cancel'),
        KeyboardButton('/help')
    )
    return keyboard

def create_processed_keyboard():
    keyboard = ReplyKeyboardMarkup(resize_keyboard=True, row_width=3)
    processed_objects = sorted(object_files.keys())
    buttons = [KeyboardButton(f"📁 #{obj}") for obj in processed_objects]
    for i in range(0, len(buttons), 3):
        keyboard.add(*buttons[i:i+3])
    keyboard.add(KeyboardButton('/help'))
    return keyboard

# ========== КОМАНДЫ БОТА ==========
@bot.message_handler(commands=['start', 'help'])
def start_message(message):
    help_text = """
🤖 Бот для управления объектами ИПУГ

📋 Доступные команды:

/info - Информация об объекте (можно несколько через запятую)
/upload - Загрузить файлы для объекта  
/download - Скачать файлы объекта
/processed - Список обработанных объектов

💡 Используйте кнопки ниже для быстрого доступа!
    """
    send_message_with_topic(
        chat_id=message.chat.id,
        message_thread_id=message.message_thread_id,
        text=help_text.strip(),
        reply_markup=create_main_keyboard()
    )

@bot.message_handler(commands=['info'])
def ask_info_object(message):
    key = make_key_from_message(message)
    user_state[key] = {
        'command': 'info',
        'chat_id': message.chat.id,
        'message_thread_id': message.message_thread_id
    }
    print(f"[STATE CREATE] {key} -> info")
    send_message_with_topic(
        chat_id=message.chat.id,
        message_thread_id=message.message_thread_id,
        text="🔍 Введите номер объекта для получения информации (можно несколько через запятую):",
        reply_markup=create_main_keyboard()
    )

@bot.message_handler(commands=['upload'])
def ask_upload_object(message):
    key = make_key_from_message(message)
    user_state[key] = {
        'command': 'upload',
        'chat_id': message.chat.id,
        'message_thread_id': message.message_thread_id
    }
    print(f"[STATE CREATE] {key} -> upload")
    send_message_with_topic(
        chat_id=message.chat.id,
        message_thread_id=message.message_thread_id,
        text="📤 Введите номер объекта для загрузки файлов:",
        reply_markup=create_main_keyboard()
    )

@bot.message_handler(commands=['download'])
def handle_download(message):
    key = make_key_from_message(message)
    parts = message.text.split()
    if len(parts) == 1:
        user_state[key] = {
            'command': 'download',
            'chat_id': message.chat.id,
            'message_thread_id': message.message_thread_id
        }
        print(f"[STATE CREATE] {key} -> download")
        send_message_with_topic(
            chat_id=message.chat.id,
            message_thread_id=message.message_thread_id,
            text="📥 Введите номер объекта для скачивания файлов:",
            reply_markup=create_main_keyboard()
        )
        return
    # /download <id>
    object_id = parts[1]
    download_object_files(message, object_id)

@bot.message_handler(commands=['done'])
def handle_done(message):
    key = make_key_from_message(message)
    if key not in user_state or user_state[key].get('command') != 'upload_files':
        send_message_with_topic(
            chat_id=message.chat.id,
            message_thread_id=message.message_thread_id,
            text="❌ Нет активной загрузки файлов",
            reply_markup=create_main_keyboard()
        )
        return

    state = user_state[key]
    object_id = state['object_id']
    files = state['files']
    file_types = state['file_types']
    if not files:
        send_message_with_topic(
            chat_id=message.chat.id,
            message_thread_id=message.message_thread_id,
            text="❌ Не получено ни одного файла",
            reply_markup=create_main_keyboard()
        )
        user_state.pop(key, None)
        print(f"[STATE REMOVE] {key} (no files)")
        return

    object_files[object_id] = files
    save_to_archive(object_id, files, file_types)
    update_google_sheets(object_id)
    user_state.pop(key, None)
    print(f"[STATE REMOVE] {key} -> done (saved {len(files)} files)")

    send_message_with_topic(
        chat_id=message.chat.id,
        message_thread_id=message.message_thread_id,
        text=f"✅ Файлы сохранены для объекта #{object_id}\nВсего: {len(files)} файлов",
        reply_markup=create_main_keyboard()
    )

@bot.message_handler(commands=['cancel'])
def cancel_upload(message):
    key = make_key_from_message(message)
    if key in user_state and user_state[key].get('command') == 'upload_files':
        object_id = user_state[key]['object_id']
        user_state.pop(key, None)
        print(f"[STATE REMOVE] {key} -> cancel")
        send_message_with_topic(
            chat_id=message.chat.id,
            message_thread_id=message.message_thread_id,
            text=f"❌ Загрузка файлов для объекта #{object_id} отменена",
            reply_markup=create_main_keyboard()
        )

@bot.message_handler(func=lambda message: True, content_types=['text'])
def handle_text_messages(message):
    # пропускаем команды
    if message.text.startswith('/'):
        return

    key = make_key_from_message(message)
    if key not in user_state:
        return

    state = user_state[key]
    command = state.get('command')

    if command == 'info':
        process_info_object(message, state, key)
    elif command == 'upload':
        process_upload_object(message, state, key)
    elif command == 'download':
        process_download_object(message, state, key)

def process_info_object(message, state, key):
    object_ids = [obj_id.strip() for obj_id in message.text.split(',')]
    responses = []
    for object_id in object_ids:
        obj_info = objects_data.get(object_id)
        if obj_info:
            is_processed = object_id in object_files
            status_icon = "✅" if is_processed else "⏳"
            responses.append(f"{status_icon} ОБЪЕКТ #{object_id}\n🏢 {obj_info['name']}\n📍 {obj_info['address']}\n📊 Статус: {obj_info['status']}\n---")
        else:
            responses.append(f"❌ Объект #{object_id} не найден\n---")

    final_response = "\n".join(responses)
    send_message_with_topic(
        chat_id=state['chat_id'],
        message_thread_id=state.get('message_thread_id'),
        text=final_response,
        reply_markup=create_main_keyboard()
    )

    user_state.pop(key, None)
    print(f"[STATE REMOVE] {key} -> info done")

def process_upload_object(message, state, key):
    object_id = message.text.strip()
    obj_info = objects_data.get(object_id)
    if not obj_info:
        send_message_with_topic(
            chat_id=state['chat_id'],
            message_thread_id=state.get('message_thread_id'),
            text=f"❌ Объект #{object_id} не найден",
            reply_markup=create_main_keyboard()
        )
        user_state.pop(key, None)
        print(f"[STATE REMOVE] {key} -> upload (not found)")
        return

    # переключаем состояние на режим загрузки файлов для этого пользователя
    user_state[key] = {
        'command': 'upload_files',
        'object_id': object_id,
        'chat_id': state['chat_id'],
        'message_thread_id': state.get('message_thread_id'),
        'files': [],
        'file_types': {'photos': 0, 'documents': 0, 'videos': 0}
    }
    print(f"[STATE UPDATE] {key} -> upload_files for object {object_id}")

    send_message_with_topic(
        chat_id=state['chat_id'],
        message_thread_id=state.get('message_thread_id'),
        text=f"📎 Загрузка файлов для объекта #{object_id}\n\nОтправляйте фото/документы/видео. Когда закончите — /done. Для отмены — /cancel.",
        reply_markup=create_upload_keyboard()
    )

def process_download_object(message, state, key):
    object_id = message.text.strip()
    # удаляем состояние пользователя (если было) и выполняем отправку
    user_state.pop(key, None)
    print(f"[STATE REMOVE] {key} -> download request for {object_id}")
    download_object_files(message, object_id)

@bot.message_handler(content_types=['photo', 'document', 'video'])
def handle_files(message):
    key = make_key_from_message(message)
    if key not in user_state or user_state[key].get('command') != 'upload_files':
        return

    state = user_state[key]
    file_info = {}
    if message.photo:
        file_info = {'type': 'photo', 'file_id': message.photo[-1].file_id}
        state['file_types']['photos'] += 1
    elif message.document:
        file_info = {'type': 'document', 'file_id': message.document.file_id, 'name': message.document.file_name}
        state['file_types']['documents'] += 1
    elif message.video:
        file_info = {'type': 'video', 'file_id': message.video.file_id}
        state['file_types']['videos'] += 1

    state['files'].append(file_info)
    state['last_file_count'] = len(state['files'])
    print(f"[FILE RECEIVED] {key} -> {file_info['type']} (total {state['last_file_count']})")

def download_object_files(message, object_id):
    if object_id not in object_files:
        send_message_with_topic(
            chat_id=message.chat.id,
            message_thread_id=message.message_thread_id,
            text=f"❌ Для объекта #{object_id} нет файлов",
            reply_markup=create_main_keyboard()
        )
        return

    files = object_files[object_id]
    send_message_with_topic(
        chat_id=message.chat.id,
        message_thread_id=message.message_thread_id,
        text=f"📁 Отправляю файлы объекта #{object_id}...",
        reply_markup=create_main_keyboard()
    )

    sent_count = 0
    for file_info in files:
        try:
            if file_info['type'] == 'photo':
                send_photo_with_topic(message.chat.id, file_info['file_id'], message.message_thread_id)
            elif file_info['type'] == 'document':
                send_document_with_topic(message.chat.id, file_info['file_id'], message.message_thread_id)
            elif file_info['type'] == 'video':
                send_video_with_topic(message.chat.id, file_info['file_id'], message.message_thread_id)
            sent_count += 1
        except Exception as e:
            print(f"[download_object_files] Ошибка отправки файла: {e}")

    send_message_with_topic(
        chat_id=message.chat.id,
        message_thread_id=message.message_thread_id,
        text=f"✅ Отправлено {sent_count} файлов из {len(files)}",
        reply_markup=create_main_keyboard()
    )

@bot.message_handler(commands=['processed'])
def show_processed_objects(message):
    if not object_files:
        send_message_with_topic(
            chat_id=message.chat.id,
            message_thread_id=message.message_thread_id,
            text="📭 Нет обработанных объектов",
            reply_markup=create_main_keyboard()
        )
        return

    keyboard = create_processed_keyboard()
    response = f"📊 ОБРАБОТАННЫЕ ОБЪЕКТЫ:\n\nВсего: {len(object_files)} объектов\n\n👇 Выберите объект для скачивания файлов:"
    send_message_with_topic(
        chat_id=message.chat.id,
        message_thread_id=message.message_thread_id,
        text=response,
        reply_markup=keyboard
    )

@bot.message_handler(func=lambda message: message.text and message.text.startswith('📁 #'))
def handle_download_button(message):
    try:
        object_id = message.text.replace('📁 #', '').strip()
        download_object_files(message, object_id)
    except Exception as e:
        send_message_with_topic(
            chat_id=message.chat.id,
            message_thread_id=message.message_thread_id,
            text="❌ Ошибка при обработке объекта",
            reply_markup=create_main_keyboard()
        )

# ========== WEBHOOK ==========
@app.route('/' + TOKEN, methods=['POST'])
def receive_update():
    json_str = request.get_data().decode('utf-8')
    update = telebot.types.Update.de_json(json_str)
    bot.process_new_updates([update])
    return "OK", 200

@app.route('/')
def index():
    return "🤖 Бот для управления объектами ИПУГ работает! ✅", 200

if __name__ == "__main__":
    print("🚀 Бот запускается...")
    objects_data = load_objects_from_excel()
    print(f"📊 Загружено объектов: {len(objects_data)}")
    if init_google_sheets():
        print("✅ Google Sheets подключен")
    bot.remove_webhook()
    WEBHOOK_URL = f"https://telegram-bot-b6pn.onrender.com/{TOKEN}"
    bot.set_webhook(url=WEBHOOK_URL)
    print(f"🌐 Webhook установлен: {WEBHOOK_URL}")
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))
