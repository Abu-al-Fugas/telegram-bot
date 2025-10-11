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
user_state = {}
objects_data = {}
object_files = {}  # Хранит файлы для каждого объекта

# ========== ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ==========
def send_message_with_topic(chat_id, text, message_thread_id=None, reply_markup=None, parse_mode=None):
    """Отправляет сообщение с учетом темы (topic)"""
    try:
        if message_thread_id:
            return bot.send_message(
                chat_id=chat_id,
                message_thread_id=message_thread_id,
                text=text,
                reply_markup=reply_markup,
                parse_mode=parse_mode
            )
        else:
            return bot.send_message(
                chat_id=chat_id,
                text=text,
                reply_markup=reply_markup,
                parse_mode=parse_mode
            )
    except Exception as e:
        print(f"Ошибка отправки сообщения: {e}")
        return bot.send_message(chat_id, text, reply_markup=reply_markup, parse_mode=parse_mode)

def send_photo_with_topic(chat_id, photo, message_thread_id=None, caption=None):
    """Отправляет фото с учетом темы"""
    try:
        if message_thread_id:
            return bot.send_photo(
                chat_id=chat_id,
                message_thread_id=message_thread_id,
                photo=photo,
                caption=caption
            )
        else:
            return bot.send_photo(
                chat_id=chat_id,
                photo=photo,
                caption=caption
            )
    except Exception as e:
        print(f"Ошибка отправки фото: {e}")
        return bot.send_photo(chat_id, photo, caption=caption)

def send_document_with_topic(chat_id, document, message_thread_id=None, caption=None):
    """Отправляет документ с учетом темы"""
    try:
        if message_thread_id:
            return bot.send_document(
                chat_id=chat_id,
                message_thread_id=message_thread_id,
                document=document,
                caption=caption
            )
        else:
            return bot.send_document(
                chat_id=chat_id,
                document=document,
                caption=caption
            )
    except Exception as e:
        print(f"Ошибка отправки документа: {e}")
        return bot.send_document(chat_id, document, caption=caption)

def send_video_with_topic(chat_id, video, message_thread_id=None, caption=None):
    """Отправляет видео с учетом темы"""
    try:
        if message_thread_id:
            return bot.send_video(
                chat_id=chat_id,
                message_thread_id=message_thread_id,
                video=video,
                caption=caption
            )
        else:
            return bot.send_video(
                chat_id=chat_id,
                video=video,
                caption=caption
            )
    except Exception as e:
        print(f"Ошибка отправки видео: {e}")
        return bot.send_video(chat_id, video, caption=caption)

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
def save_to_archive(object_id, files, file_types):
    """Сохраняет информацию и файлы в архивный чат"""
    try:
        # Отправляем информационное сообщение
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
        
        # Отправляем все файлы в архив
        for file_info in files:
            try:
                if file_info['type'] == 'photo':
                    bot.send_photo(ARCHIVE_CHAT_ID, file_info['file_id'])
                elif file_info['type'] == 'document':
                    bot.send_document(ARCHIVE_CHAT_ID, file_info['file_id'])
                elif file_info['type'] == 'video':
                    bot.send_video(ARCHIVE_CHAT_ID, file_info['file_id'])
            except Exception as e:
                print(f"Ошибка отправки файла в архив: {e}")
        
        return True
    except Exception as e:
        print(f"Ошибка сохранения в архив: {e}")
        return False

# ========== КЛАВИАТУРЫ ==========
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

def create_upload_keyboard():
    """Создает клавиатуру для загрузки файлов"""
    keyboard = ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    keyboard.add(
        KeyboardButton('/done'),
        KeyboardButton('/cancel'),
        KeyboardButton('/help')
    )
    return keyboard

def create_processed_keyboard():
    """Создает клавиатуру с обработанными объектами"""
    keyboard = ReplyKeyboardMarkup(resize_keyboard=True, row_width=3)
    
    # Добавляем кнопки для каждого обработанного объекта
    processed_objects = sorted(object_files.keys())
    buttons = [KeyboardButton(f"📁 #{obj}") for obj in processed_objects]
    
    # Разбиваем на ряды по 3 кнопки
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
    msg = send_message_with_topic(
        chat_id=message.chat.id,
        message_thread_id=message.message_thread_id,
        text="🔍 Введите номер объекта для получения информации (можно несколько через запятую):",
        reply_markup=create_main_keyboard()
    )
    bot.register_next_step_handler(msg, process_info_object)

def process_info_object(message):
    if message.text.startswith('/'):
        send_message_with_topic(
            chat_id=message.chat.id,
            message_thread_id=message.message_thread_id,
            text="❌ Пожалуйста, введите номер объекта, а не команду",
            reply_markup=create_main_keyboard()
        )
        return
        
    # Обрабатываем несколько объектов через запятую
    object_ids = [obj_id.strip() for obj_id in message.text.split(',')]
    responses = []
    
    for object_id in object_ids:
        obj_info = objects_data.get(object_id)
        
        if obj_info:
            is_processed = object_id in object_files
            status_icon = "✅" if is_processed else "⏳"
            
            response = f"""
{status_icon} ОБЪЕКТ #{object_id}
🏢 {obj_info['name']}
📍 {obj_info['address']}
📊 Статус: {obj_info['status']}
💾 Обработан: {"Да" if is_processed else "Нет"}
---"""
            responses.append(response)
        else:
            responses.append(f"❌ Объект #{object_id} не найден\n---")
    
    final_response = "\n".join(responses)
    send_message_with_topic(
        chat_id=message.chat.id,
        message_thread_id=message.message_thread_id,
        text=final_response,
        reply_markup=create_main_keyboard()
    )

@bot.message_handler(commands=['upload'])
def ask_upload_object(message):
    msg = send_message_with_topic(
        chat_id=message.chat.id,
        message_thread_id=message.message_thread_id,
        text="📤 Введите номер объекта для загрузки файлов:",
        reply_markup=create_main_keyboard()
    )
    bot.register_next_step_handler(msg, process_upload_object)

def process_upload_object(message):
    if message.text.startswith('/'):
        send_message_with_topic(
            chat_id=message.chat.id,
            message_thread_id=message.message_thread_id,
            text="❌ Пожалуйста, введите номер объекта, а не команду",
            reply_markup=create_main_keyboard()
        )
        return
        
    object_id = message.text.strip()
    obj_info = objects_data.get(object_id)
    
    if not obj_info:
        send_message_with_topic(
            chat_id=message.chat.id,
            message_thread_id=message.message_thread_id,
            text=f"❌ Объект #{object_id} не найден",
            reply_markup=create_main_keyboard()
        )
        return
    
    # Сохраняем состояние пользователя с его user_id и данными темы
    user_id = message.from_user.id
    user_state[user_id] = {
        'object_id': object_id,
        'chat_id': message.chat.id,
        'message_thread_id': message.message_thread_id,
        'files': [],
        'file_types': {'photos': 0, 'documents': 0, 'videos': 0},
        'last_file_count': 0
    }
    
    send_message_with_topic(
        chat_id=message.chat.id,
        message_thread_id=message.message_thread_id,
        text=f"""
📎 Загрузка файлов для объекта #{object_id}

Отправляйте файлы (фото, документы, видео).
Когда закончите, нажмите /done
Для отмены - /cancel

✅ Файлы будут автоматически сохранены
        """.strip(),
        reply_markup=create_upload_keyboard()
    )

@bot.message_handler(commands=['cancel'])
def cancel_upload(message):
    user_id = message.from_user.id
    if user_id in user_state:
        object_id = user_state[user_id]['object_id']
        chat_id = user_state[user_id]['chat_id']
        message_thread_id = user_state[user_id].get('message_thread_id')
        
        del user_state[user_id]
        send_message_with_topic(
            chat_id=chat_id,
            message_thread_id=message_thread_id,
            text=f"❌ Загрузка файлов для объекта #{object_id} отменена", 
            reply_markup=create_main_keyboard()
        )

@bot.message_handler(content_types=['photo', 'document', 'video'])
def handle_files(message):
    user_id = message.from_user.id
    
    if user_id not in user_state:
        return
    
    object_id = user_state[user_id]['object_id']
    files = user_state[user_id]['files']
    file_types = user_state[user_id]['file_types']
    
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
    user_state[user_id]['last_file_count'] = len(files)

@bot.message_handler(commands=['done'])
def finish_upload(message):
    user_id = message.from_user.id
    
    if user_id not in user_state:
        send_message_with_topic(
            chat_id=message.chat.id,
            message_thread_id=message.message_thread_id,
            text="❌ Нет активной загрузки файлов", 
            reply_markup=create_main_keyboard()
        )
        return
    
    object_id = user_state[user_id]['object_id']
    chat_id = user_state[user_id]['chat_id']
    message_thread_id = user_state[user_id].get('message_thread_id')
    files = user_state[user_id]['files']
    file_types = user_state[user_id]['file_types']
    
    if not files:
        send_message_with_topic(
            chat_id=chat_id,
            message_thread_id=message_thread_id,
            text="❌ Не получено ни одного файла", 
            reply_markup=create_main_keyboard()
        )
        del user_state[user_id]
        return
    
    # Сохраняем файлы для объекта
    object_files[object_id] = files
    
    # Сохраняем в архив (информация + все файлы)
    save_to_archive(object_id, files, file_types)
    
    # Обновляем Google Sheets
    update_google_sheets(object_id)
    
    # Очищаем состояние пользователя
    del user_state[user_id]
    
    send_message_with_topic(
        chat_id=chat_id,
        message_thread_id=message_thread_id,
        text=f"""
✅ УСПЕХ!

📁 Для объекта #{object_id} сохранено:
📸 Фото: {file_types['photos']}
📄 Документы: {file_types['documents']}  
🎥 Видео: {file_types['videos']}
📊 Всего: {len(files)} файлов

💾 Все файлы сохранены в архив
📈 Объект отмечен как обработанный
        """.strip(),
        reply_markup=create_main_keyboard()
    )

@bot.message_handler(commands=['download'])
def handle_download(message):
    # Обработка команды /download без номера
    if len(message.text.split()) == 1:
        send_message_with_topic(
            chat_id=message.chat.id,
            message_thread_id=message.message_thread_id,
            text="📥 Введите номер объекта для скачивания файлов:",
            reply_markup=create_main_keyboard()
        )
        return
    
    # Обработка команды /download с номером
    try:
        object_id = message.text.split()[1]
        download_object_files(message, object_id)
    except IndexError:
        send_message_with_topic(
            chat_id=message.chat.id,
            message_thread_id=message.message_thread_id,
            text="❌ Укажите номер объекта: /download 16",
            reply_markup=create_main_keyboard()
        )

def download_object_files(message, object_id):
    """Отправляет файлы объекта пользователю в ту же тему"""
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
    
    # Отправляем файлы пользователю в ту же тему
    sent_count = 0
    for file_info in files:
        try:
            if file_info['type'] == 'photo':
                send_photo_with_topic(
                    chat_id=message.chat.id,
                    message_thread_id=message.message_thread_id,
                    photo=file_info['file_id']
                )
            elif file_info['type'] == 'document':
                send_document_with_topic(
                    chat_id=message.chat.id,
                    message_thread_id=message.message_thread_id,
                    document=file_info['file_id']
                )
            elif file_info['type'] == 'video':
                send_video_with_topic(
                    chat_id=message.chat.id,
                    message_thread_id=message.message_thread_id,
                    video=file_info['file_id']
                )
            sent_count += 1
        except Exception as e:
            print(f"Ошибка отправки файла: {e}")
    
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
    
    # Создаем клавиатуру с кнопками объектов
    keyboard = create_processed_keyboard()
    
    response = f"""
📊 ОБРАБОТАННЫЕ ОБЪЕКТЫ:

Всего: {len(object_files)} объектов

👇 Выберите объект для скачивания файлов:
    """
    send_message_with_topic(
        chat_id=message.chat.id,
        message_thread_id=message.message_thread_id,
        text=response.strip(),
        reply_markup=keyboard
    )

# Обработка кнопок скачивания (формат: "📁 #16")
@bot.message_handler(func=lambda message: message.text.startswith('📁 #'))
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

@bot.message_handler(func=lambda message: True)
def handle_other_messages(message):
    if not message.text.startswith('/'):
        return
    send_message_with_topic(
        chat_id=message.chat.id,
        message_thread_id=message.message_thread_id,
        text="❌ Неизвестная команда. Используйте /help для списка команд",
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
    
    sheets_client = init_google_sheets()
    if sheets_client:
        print("✅ Google Sheets подключен")
    
    bot.remove_webhook()
    WEBHOOK_URL = f"https://telegram-bot-b6pn.onrender.com/{TOKEN}"
    bot.set_webhook(url=WEBHOOK_URL)
    print(f"🌐 Webhook установлен: {WEBHOOK_URL}")

    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))
