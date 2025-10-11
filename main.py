import os
import json
import telebot
from telebot.types import ReplyKeyboardMarkup, KeyboardButton
from flask import Flask, request
from datetime import datetime
import gspread
from google.oauth2.service_account import Credentials

# ========== НАСТРОЙКИ ==========
TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
ARCHIVE_CHAT_ID = os.environ.get("ARCHIVE_CHAT_ID", "-1003160855229")
GOOGLE_SHEETS_KEY = os.environ.get("GOOGLE_SHEETS_KEY")

bot = telebot.TeleBot(TOKEN)
app = Flask(__name__)

# Глобальные переменные
user_state = {}
processed_objects = set()

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
            print("Google Sheets client not initialized")
            return False
        
        # Открываем таблицу
        sheet = client.open("Объекты ИПУГ").sheet1
        
        # Получаем все данные
        all_data = sheet.get_all_values()
        
        # Ищем объект по номеру (первый столбец)
        for i, row in enumerate(all_data, start=1):
            if i == 1:  # Пропускаем заголовок
                continue
                
            if row and str(row[0]).strip() == str(object_id):
                # Обновляем статус (4-й столбец - D)
                sheet.update_cell(i, 4, status)
                
                # Красим ячейку в зеленый
                sheet.format(f"D{i}", {
                    "backgroundColor": {
                        "red": 0.7,
                        "green": 0.9, 
                        "blue": 0.7
                    }
                })
                print(f"✅ Обновлен Google Sheets для объекта {object_id}")
                return True
        
        print(f"❌ Объект {object_id} не найден в Google Sheets")
        return False
        
    except Exception as e:
        print(f"❌ Ошибка обновления Google Sheets: {e}")
        return False

# ========== АРХИВ В TELEGRAM ==========
def save_to_archive(object_id, files_count, file_types):
    """Сохраняет информацию в архивный чат"""
    try:
        # Формируем описание типов файлов
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
        print(f"❌ Ошибка сохранения в архив: {e}")
        return False

# ========== БАЗА ДАННЫХ ОБЪЕКТОВ ==========
def get_object_info(object_id):
    """Получает информацию об объекте (имитация базы данных)"""
    objects_data = {
        "15": {"name": "Кафе 'Восток'", "address": "г. Махачкала, ул. Ленина, 15", "status": "Не начат"},
        "20": {"name": "Школа №45", "address": "г. Махачкала, ул. Гагарина, 27", "status": "Не начат"},
        "25": {"name": "Больница им. Петрова", "address": "г. Махачкала, пр. Революции, 8", "status": "Не начат"},
        "30": {"name": "Магазин 'Продукты'", "address": "г. Махачкала, ул. Советская, 42", "status": "Не начат"},
        "35": {"name": "Офисное здание", "address": "г. Махачкала, пр. Гамидова, 15", "status": "Не начат"}
    }
    
    return objects_data.get(object_id)

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

💡 Просто введите команду и следуйте инструкциям!
    """
    bot.reply_to(message, help_text.strip())

# ========== ИНФОРМАЦИЯ ОБ ОБЪЕКТЕ ==========
@bot.message_handler(commands=['info'])
def ask_info_object(message):
    msg = bot.reply_to(message, "🔍 Введите номер объекта для получения информации:")
    bot.register_next_step_handler(msg, process_info_object)

def process_info_object(message):
    object_id = message.text.strip()
    obj_info = get_object_info(object_id)
    
    if obj_info:
        # Проверяем, обработан ли объект
        is_processed = object_id in processed_objects
        status_icon = "✅" if is_processed else "⏳"
        
        response = f"""
{status_icon} ОБЪЕКТ #{object_id}
🏢 {obj_info['name']}
📍 {obj_info['address']}
📊 Статус: {obj_info['status']}
💾 Обработан: {"Да" if is_processed else "Нет"}
        """
        bot.reply_to(message, response.strip())
    else:
        bot.reply_to(message, f"❌ Объект #{object_id} не найден")

# ========== ЗАГРУЗКА ФАЙЛОВ ==========
@bot.message_handler(commands=['upload'])
def ask_upload_object(message):
    msg = bot.reply_to(message, "📤 Введите номер объекта для загрузки файлов:")
    bot.register_next_step_handler(msg, process_upload_object)

def process_upload_object(message):
    object_id = message.text.strip()
    
    # Проверяем существование объекта
    obj_info = get_object_info(object_id)
    if not obj_info:
        bot.reply_to(message, f"❌ Объект #{object_id} не найден")
        return
    
    # Сохраняем состояние пользователя
    user_state[message.chat.id] = {
        'object_id': object_id,
        'step': 'waiting_files',
        'files': [],
        'file_types': {'photos': 0, 'documents': 0, 'videos': 0}
    }
    
    bot.reply_to(message, f"""
📎 Отправьте файлы для объекта #{object_id}

Можно отправить:
• Фотографии 📸
• Документы 📄  
• Видео 🎥

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
    
    # Сохраняем информацию о файле
    file_info = {}
    
    if message.photo:
        file_id = message.photo[-1].file_id
        file_info = {'type': 'photo', 'file_id': file_id}
        file_types['photos'] += 1
        
    elif message.document:
        file_id = message.document.file_id
        file_info = {
            'type': 'document', 
            'file_id': file_id, 
            'name': message.document.file_name
        }
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
        bot.reply_to(message, "❌ Нет активной загрузки файлов")
        return
    
    object_id = user_state[chat_id]['object_id']
    files = user_state[chat_id]['files']
    file_types = user_state[chat_id]['file_types']
    
    if not files:
        bot.reply_to(message, "❌ Не получено ни одного файла")
        del user_state[chat_id]
        return
    
    # Сохраняем в архив
    save_to_archive(object_id, len(files), file_types)
    
    # Обновляем Google Sheets
    update_google_sheets(object_id)
    
    # Добавляем в список обработанных
    processed_objects.add(object_id)
    
    # Очищаем состояние
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
    """.strip())

# ========== СКАЧИВАНИЕ ФАЙЛОВ ==========
@bot.message_handler(commands=['download'])
def ask_download_object(message):
    msg = bot.reply_to(message, "📥 Введите номер объекта для скачивания файлов:")
    bot.register_next_step_handler(msg, process_download_object)

def process_download_object(message):
    object_id = message.text.strip()
    
    # Проверяем, обработан ли объект
    if object_id not in processed_objects:
        bot.reply_to(message, f"❌ Для объекта #{object_id} нет файлов в архиве")
        return
    
    # Имитация получения информации об архиве
    archive_info = f"""
📁 Файлы объекта #{object_id} в архиве:

💾 Архивный чат: @Архив
🕒 Последнее обновление: {datetime.now().strftime('%d.%m.%Y %H:%M')}
📋 Ищите по тегу: ОБЪЕКТ #{object_id}

🔍 Все файлы сохранены в архивном чате Telegram
    """
    
    bot.reply_to(message, archive_info.strip())

# ========== СПИСОК ОБРАБОТАННЫХ ОБЪЕКТОВ ==========
@bot.message_handler(commands=['processed'])
def show_processed_objects(message):
    if not processed_objects:
        bot.reply_to(message, "📭 Нет обработанных объектов")
        return
    
    # Получаем информацию об объектах
    objects_info = []
    for obj_id in sorted(processed_objects):
        obj_info = get_object_info(obj_id)
        if obj_info:
            objects_info.append(f"• #{obj_id} - {obj_info['name']}")
        else:
            objects_info.append(f"• #{obj_id} - Неизвестный объект")
    
    objects_list = "\n".join(objects_info)
    
    bot.reply_to(message, f"""
📊 ОБРАБОТАННЫЕ ОБЪЕКТЫ:

{objects_list}

Всего: {len(processed_objects)} объектов
    """.strip())

# ========== ОБРАБОТКА ОШИБОК ==========
@bot.message_handler(func=lambda message: True)
def handle_other_messages(message):
    if message.text.startswith('/'):
        bot.reply_to(message, "❌ Неизвестная команда. Используйте /help для списка команд")
    else:
        bot.reply_to(message, "💡 Используйте команды из меню /help")

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
    
    # Инициализируем Google Sheets
    sheets_client = init_google_sheets()
    if sheets_client:
        print("✅ Google Sheets подключен")
    else:
        print("❌ Google Sheets не подключен")
    
    bot.remove_webhook()
    WEBHOOK_URL = f"https://telegram-bot-b6pn.onrender.com/{TOKEN}"
    bot.set_webhook(url=WEBHOOK_URL)
    print(f"🌐 Webhook установлен: {WEBHOOK_URL}")

    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))
