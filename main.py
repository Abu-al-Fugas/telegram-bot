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
    return (message.chat.id, message.from_user.id)

def send_message_with_topic(chat_id, text, message_thread_id=None, reply_markup=None):
    try:
        if message_thread_id:
            return bot.send_message(chat_id=chat_id, text=text, message_thread_id=message_thread_id, reply_markup=reply_markup)
        return bot.send_message(chat_id=chat_id, text=text, reply_markup=reply_markup)
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

# ========== EXCEL ==========
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
        print(f"[load_objects_from_excel] Ошибка: {e}")
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

# ========== АРХИВ ==========
def save_to_archive(object_id, all_files):
    try:
        type_description = {}
        for step in all_files:
            for file in step['files']:
                type_description[file['type']] = type_description.get(file['type'], 0) + 1

        files_desc = " + ".join([f"{k}:{v}" for k,v in type_description.items()]) if type_description else "файлы"

        info_text = f"""
💾 ОБЪЕКТ #{object_id}
📁 {sum(len(s['files']) for s in all_files)} {files_desc}
🕒 {datetime.now().strftime('%d.%m.%Y %H:%M')}
        """
        bot.send_message(ARCHIVE_CHAT_ID, info_text.strip())

        for step in all_files:
            for file in step['files']:
                try:
                    if file['type']=='photo':
                        bot.send_photo(ARCHIVE_CHAT_ID, file['file_id'])
                    elif file['type']=='document':
                        bot.send_document(ARCHIVE_CHAT_ID, file['file_id'])
                    elif file['type']=='video':
                        bot.send_video(ARCHIVE_CHAT_ID, file['file_id'])
                except Exception as e:
                    print(f"[save_to_archive] Ошибка: {e}")
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
        KeyboardButton('/cancel')
    )
    return keyboard

# ========== ШАГИ ЗАГРУЗКИ ==========
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

# ========== КОМАНДЫ ==========
@bot.message_handler(commands=['start', 'help'])
def start_message(message):
    help_text = """
🤖 Бот для управления объектами ИПУГ

Доступные команды:
/info - информация об объекте
/upload - загрузка файлов с чек-листом
/download - скачать файлы объекта
/processed - список обработанных объектов
"""
    send_message_with_topic(message.chat.id, help_text.strip(), message.message_thread_id, create_main_keyboard())

@bot.message_handler(commands=['upload'])
def start_upload(message):
    key = make_key_from_message(message)
    send_message_with_topic(message.chat.id, "Введите номер объекта для загрузки файлов:", message.message_thread_id)
    user_state[key] = {'command':'await_object'}

@bot.message_handler(func=lambda m: True, content_types=['text'])
def handle_text(message):
    key = make_key_from_message(message)
    state = user_state.get(key)

    if not state:
        return

    # ввод объекта
    if state['command']=='await_object':
        object_id = message.text.strip()
        if object_id not in objects_data:
            send_message_with_topic(message.chat.id, f"❌ Объект {object_id} не найден", message.message_thread_id)
            return
        # инициализация пошаговой загрузки
        steps = [{'name': s, 'files': []} for s in UPLOAD_STEPS]
        user_state[key] = {
            'command':'upload_steps',
            'object_id': object_id,
            'step_index':0,
            'steps':steps,
            'chat_id':message.chat.id,
            'thread_id':message.message_thread_id
        }
        send_message_with_topic(message.chat.id, f"📸 Загрузите {steps[0]['name']}", message.message_thread_id, create_upload_keyboard())
        return

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

# /done на шаге
@bot.message_handler(commands=['done'])
def handle_done(message):
    key = make_key_from_message(message)
    state = user_state.get(key)
    if not state or state.get('command')!='upload_steps':
        send_message_with_topic(message.chat.id, "❌ Нет активной загрузки", message.message_thread_id, create_main_keyboard())
        return
    # перейти к следующему шагу
    state['step_index']+=1
    if state['step_index']>=len(state['steps']):
        # все шаги завершены
        object_id = state['object_id']
        all_steps = state['steps']
        save_to_archive(object_id, all_steps)
        update_google_sheets(object_id)
        # отчет
        report = f"✅ Загрузка файлов завершена для объекта #{object_id}\n\n"
        for i, s in enumerate(all_steps,1):
            report += f"{i}. ✅ {s['name']}: {len(s['files'])} файлов\n"
        send_message_with_topic(state['chat_id'], report, state['thread_id'], create_main_keyboard())
        user_state.pop(key)
    else:
        next_step = state['steps'][state['step_index']]
        send_message_with_topic(state['chat_id'], f"📸 Загрузите {next_step['name']}", state['thread_id'], create_upload_keyboard())

# /cancel
@bot.message_handler(commands=['cancel'])
def handle_cancel(message):
    key = make_key_from_message(message)
    if key in user_state:
        obj = user_state[key].get('object_id','')
        send_message_with_topic(message.chat.id, f"❌ Загрузка для объекта {obj} отменена", message.message_thread_id, create_main_keyboard())
        user_state.pop(key)

# ========== WEBHOOK ==========
@app.route('/' + TOKEN, methods=['POST'])
def receive_update():
    json_str = request.get_data().decode('utf-8')
    update = telebot.types.Update.de_json(json_str)
    bot.process_new_updates([update])
    return "OK",200

@app.route('/')
def index():
    return "🤖 Бот работает",200

if __name__=="__main__":
    print("🚀 Бот запускается...")
    objects_data = load_objects_from_excel()
    bot.remove_webhook()
    WEBHOOK_URL = f"https://your-deploy-url/{TOKEN}"
    bot.set_webhook(url=WEBHOOK_URL)
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT",10000)))
