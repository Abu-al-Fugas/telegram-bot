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
user_state = {}
processed_objects = set()
objects_data = {}

# ========== ЗАГРУЗКА ДАННЫХ ИЗ EXCEL ==========
def load_objects_from_excel():
    """Загружает объекты из Excel файла"""
    global objects_data
    try:
        workbook = openpyxl.load_workbook('objects.xlsx')
        sheet = workbook.active
        
        objects_dict = {}
        for row in sheet.iter_rows(min_row=2, values_only=True):
            if row[0] is not None:
                obj_number = str(row[0]).strip()
                objects_dict[obj_number] = {
                    'name': row[1] or '',
                    'address': row[2] or '',
                    'status': 'Не начат'
                }
        return objects_dict
    except Exception as e:
        print(f"Ошибка загрузки Excel: {e}")
        return {}

# ========== GOOGLE SHEETS ==========
def init_google_sheets():
    """Инициализация Google Sheets"""
    try:
        if GOOGLE_SHEETS_KEY:
            creds_dict = json.loads(GOOGLE_SHEETS_KEY)
            creds = Credentials.from_service_account_info(creds_dict)
            client = gspread.authorize(creds)
            return client
        return None
    except Exception as e:
        print(f"Ошибка инициализации Google Sheets: {e}")
        return None

def update_google_sheets(object_id, status="✅ Обработан"):
    """Обновляет Google Sheets - помечает объект как обработанный"""
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
                sheet.format(f"D{i}", {
                    "backgroundColor": {
                        "red": 0.7,
                        "green": 0.9, 
                        "blue": 0.7
                    }
                })
                return True
        return False
        
    except Exception as e:
        print(f"Ошибка обновления Google Sheets: {e}")
        return False

# ========== АРХИВ В TELEGRAM ==========
def save_to_archive(object_id, files_count, file_types):
    """Сохраняет информацию в архивный чат"""
    try:
        type_description = []
        if file_types.get('photos', 0) > 0:
            type_description.append(f"📸 {file_types['photos']} фото")
        if file_types.get('documents', 0) > 0:
            type_description.append(f"📄 {file_types['documents']} док.")
        if file_types.get('videos', 0) > 0:
            type_description.append(f"🎥 {file_types['videos']} видео")
        
        files_desc = " + ".join(type_description) if type_description else "файлы"
        
        message_text = f"""
💾 ОБЪЕКТ #{object_id}
📁 {files_count} {files_desc}
🕒 {datetime.now().strftime('%d.%m.%Y %H:%M')}
        """
        bot.send_message(ARCHIVE_CHAT_ID, message_text.strip())
        return True
    except Exception as e:
        print(f"Ошибка сохранения в архив: {e}")
        return False

# ========== КЛАВИАТУРА ==========
def create_main_keyboard():
    """Создает основную клавиатуру с командами"""
    keyboard = ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    keyboard.add(
        KeyboardButton('/info'),
        KeyboardButton('/upload'),
        KeyboardButton('/download'), 
        KeyboardButton('/processed'),
        KeyboardButton('/help')
    )
    return keyboard

# ========== КОМАНДЫ БОТА ==========
@bot.message_handler(commands=['start', 'help'])
def start_message(message):
    help_text = """
🤖 Бот для управления объектами ИПУГ

📋 Доступные команды:

/info - Информация об объекте
/upload - Загрузить файлы для объекта  
/download - Скачать файлы объекта
/processed - Список обработанных объектов

💡 Используйте кнопки ниже для быстрого доступа!
    """
    bot.reply_to(message, help_text.strip(), reply_markup=create_main_keyboard())

@bot.message_handler(commands=['info'])
def ask_info_object(message):
    msg = bot.reply_to(message, "🔍 Введите номер объекта для получения информации:", reply_markup=create_main_keyboard())
    bot.register_next_step_handler(msg, process_info_object)

def process_info_object(message):
    if message.text.startswith('/'):
        bot.reply_to(message, "❌ Пожалуйста, введите номер объекта, а не команду")
        return
        
    object_id = message.text.strip()
    obj_info = objects_data.get(object_id)
    
    if obj_info:
        is_processed = object_id in processed_objects
        status_icon = "✅" if is_processed else "⏳"
        
        response = f"""
{status_icon} ОБЪЕКТ #{object_id}
🏢 {obj_info['name']}
📍 {obj_info['address']}
📊 Статус: {obj_info['status']}
💾 Обработан: {"Да" if is_processed else "Нет"}
        """
        bot.reply_to(message, response.strip(), reply_markup=create_main_keyboard())
    else:
        bot.reply_to(message, f"❌ Объект #{object_id} не найден", reply_markup=create_main_keyboard())

@bot.message_handler(commands=['upload'])
def ask_upload_object(message):
    msg = bot.reply_to(message, "📤 Введите номер объекта для загрузки файлов:", reply_markup=create_main_keyboard())
    bot.register_next_step_handler(msg, process_upload_object)

def process_upload_object(message):
    if message.text.startswith('/'):
        bot.reply_to(message, "❌ Пожалуйста, введите номер объекта, а не команду")
        return
        
    object_id = message.text.strip()
    obj_info = objects_data.get(object_id)
    
    if not obj_info:
        bot.reply_to(message, f"❌ Объект #{object_id} не найден", reply_markup=create_main_keyboard())
        return
    
    user_state[message.chat.id] = {
        'object_id': object_id,
        'step': 'waiting_files',
        'files': [],
        'file_types': {'photos': 0, 'documents': 0, 'videos': 0}
    }
    
    bot.reply_to(message, f"""
📎 Отправьте файлы для объекта #{object_id}

Отправляйте файлы по одному или несколько сразу.
Когда закончите, введите /done
    """.strip())

@bot.message_handler(content_types=['photo', 'document', 'video'])
def handle_files(message):
    chat_id = message.chat.id
    
    if chat_id not in user_state or user_state[chat_id]['step'] != 'waiting_files':
        return
    
    object_id = user_state[chat_id]['object_id']
    files = user_state[chat_id]['files']
    file_types = user_state[chat_id]['file_types']
    
    file_info = {}
    
    if message.photo:
        file_id = message.photo[-1].file_id
        file_info = {'type': 'photo', 'file_id': file_id}
        file_types['photos'] += 1
    elif message.document:
        file_id = message.document.file_id
        file_info = {'type': 'document', 'file_id': file_id, 'name': message.document.file_name}
        file_types['documents'] += 1
    elif message.video:
        file_id = message.video.file_id
        file_info = {'type': 'video', 'file_id': file_id}
        file_types['videos'] += 1
    
    files.append(file_info)
    total_files = len(files)
    bot.reply_to(message, f"✅ Файл получен! Всего: {total_files} файлов\nВведите /done когда закончите")

@bot.message_handler(commands=['done'])
def finish_upload(message):
    chat_id = message.chat.id
    
    if chat_id not in user_state or user_state[chat_id]['step'] != 'waiting_files':
        bot.reply_to(message, "❌ Нет активной загрузки файлов", reply_markup=create_main_keyboard())
        return
    
    object_id = user_state[chat_id]['object_id']
    files = user_state[chat_id]['files']
    file_types = user_state[chat_id]['file_types']
    
    if not files:
        bot.reply_to(message, "❌ Не получено ни одного файла", reply_markup=create_main_keyboard())
        del user_state[chat_id]
        return
    
    save_to_archive(object_id, len(files), file_types)
    update_google_sheets(object_id)
    processed_objects.add(object_id)
    del user_state[chat_id]
    
    bot.reply_to(message, f"""
✅ УСПЕХ!

📁 Для объекта #{object_id} сохранено:
📸 Фото: {file_types['photos']}
📄 Документы: {file_types['documents']}  
🎥 Видео: {file_types['videos']}
📊 Всего: {len(files)} файлов

💾 Данные сохранены в архив
📈 Объект отмечен как обработанный
    """.strip(), reply_markup=create_main_keyboard())

@bot.message_handler(commands=['download'])
def ask_download_object(message):
    msg = bot.reply_to(message, "📥 Введите номер объекта для скачивания файлов:", reply_markup=create_main_keyboard())
    bot.register_next_step_handler(msg, process_download_object)

def process_download_object(message):
    if message.text.startswith('/'):
        bot.reply_to(message, "❌ Пожалуйста, введите номер объекта, а не команду")
        return
        
    object_id = message.text.strip()
    
    if object_id not in processed_objects:
        bot.reply_to(message, f"❌ Для объекта #{object_id} нет файлов в архиве", reply_markup=create_main_keyboard())
        return
    
    archive_info = f"""
📁 Файлы объекта #{object_id} в архиве:

💾 Архивный чат доступен администраторам
🕒 Объект обработан: {datetime.now().strftime('%d.%m.%Y %H:%M')}
📋 Для доступа к файлам обратитесь к администратору

✅ Объект успешно обработан и сохранен в системе
    """
    
    bot.reply_to(message, archive_info.strip(), reply_markup=create_main_keyboard())

@bot.message_handler(commands=['processed'])
def show_processed_objects(message):
    if not processed_objects:
        bot.reply_to(message, "📭 Нет обработанных объектов", reply_markup=create_main_keyboard())
        return
    
    objects_info = []
    for obj_id in sorted(processed_objects):
        obj_info = objects_data.get(obj_id)
        if obj_info:
            objects_info.append(f"• #{obj_id} - {obj_info['name']}")
        else:
            objects_info.append(f"• #{obj_id} - Неизвестный объект")
    
    objects_list = "\n".join(objects_info)
    
    bot.reply_to(message, f"""
📊 ОБРАБОТАННЫЕ ОБЪЕКТЫ:

{objects_list}

Всего: {len(processed_objects)} объектов
    """.strip(), reply_markup=create_main_keyboard())

@bot.message_handler(func=lambda message: True)
def handle_other_messages(message):
    if not message.text.startswith('/'):
        return
    bot.reply_to(message, "❌ Неизвестная команда. Используйте /help для списка команд", reply_markup=create_main_keyboard())

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
    
    sheets_client = init_google_sheets()
    if sheets_client:
        print("✅ Google Sheets подключен")
    
    bot.remove_webhook()
    WEBHOOK_URL = f"https://telegram-bot-b6pn.onrender.com/{TOKEN}"
    bot.set_webhook(url=WEBHOOK_URL)
    print(f"🌐 Webhook установлен: {WEBHOOK_URL}")

    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))
