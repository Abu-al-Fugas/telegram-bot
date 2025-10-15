import os
import json
import telebot
from telebot.types import ReplyKeyboardMarkup, KeyboardButton
from flask import Flask, request
from datetime import datetime
import gspread
from google.oauth2.service_account import Credentials
import openpyxl
import traceback

# ========== НАСТРОЙКИ ==========
TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
# если у тебя есть переменная WEBHOOK_URL, бот будет использовать её; иначе падать обратно на known URL
WEBHOOK_BASE = os.environ.get("WEBHOOK_URL", None)
ARCHIVE_CHAT_ID = os.environ.get("ARCHIVE_CHAT_ID", "-1003160855229")
GOOGLE_SHEETS_KEY = os.environ.get("GOOGLE_SHEETS_KEY")

bot = telebot.TeleBot(TOKEN, parse_mode=None)
app = Flask(__name__)

# ========== Глобальные состояния ==========
# ключ состояния: (chat_id, user_id)
user_state = {}      # для пошаговой загрузки: хранит step_index, steps, object_id и т.д.
objects_data = {}    # загруженное из excel
object_files = {}    # сохранённые обработанные объекты (если нужно)

# ========== HELPERS ==========
def make_key_from_message(message):
    """Возвращает ключ (chat_id, user_id). Везде используем именно этот ключ."""
    # message может быть Update, Message -- предполагаем message.chat и message.from_user существуют
    return (message.chat.id, message.from_user.id)

def send_message_with_topic(chat_id, text, message_thread_id=None, reply_markup=None):
    try:
        if message_thread_id:
            return bot.send_message(chat_id=chat_id, text=text, message_thread_id=message_thread_id, reply_markup=reply_markup)
        return bot.send_message(chat_id=chat_id, text=text, reply_markup=reply_markup)
    except Exception as e:
        print(f"[send_message_with_topic] Ошибка: {e}\n{traceback.format_exc()}")

def send_photo_with_topic(chat_id, photo, message_thread_id=None, caption=None):
    try:
        if message_thread_id:
            return bot.send_photo(chat_id=chat_id, photo=photo, message_thread_id=message_thread_id, caption=caption)
        return bot.send_photo(chat_id=chat_id, photo=photo, caption=caption)
    except Exception as e:
        print(f"[send_photo_with_topic] Ошибка: {e}\n{traceback.format_exc()}")

def send_document_with_topic(chat_id, document, message_thread_id=None, caption=None):
    try:
        if message_thread_id:
            return bot.send_document(chat_id=chat_id, document=document, message_thread_id=message_thread_id, caption=caption)
        return bot.send_document(chat_id=chat_id, document=document, caption=caption)
    except Exception as e:
        print(f"[send_document_with_topic] Ошибка: {e}\n{traceback.format_exc()}")

def send_video_with_topic(chat_id, video, message_thread_id=None, caption=None):
    try:
        if message_thread_id:
            return bot.send_video(chat_id=chat_id, video=video, message_thread_id=message_thread_id, caption=caption)
        return bot.send_video(chat_id=chat_id, video=video, caption=caption)
    except Exception as e:
        print(f"[send_video_with_topic] Ошибка: {e}\n{traceback.format_exc()}")

# ========== EXCEL ==========
def load_objects_from_excel():
    try:
        workbook = openpyxl.load_workbook('objects.xlsx')
        sheet = workbook.active
        data = {}
        for row in sheet.iter_rows(min_row=2, values_only=True):
            if row and row[0] is not None:
                key = str(row[0]).strip()
                data[key] = {
                    'name': row[1] or '',
                    'address': row[2] or '',
                    'status': 'Не начат'
                }
        print(f"[load_objects_from_excel] Loaded {len(data)} objects")
        return data
    except Exception as e:
        print(f"[load_objects_from_excel] Ошибка: {e}\n{traceback.format_exc()}")
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
        print(f"[init_google_sheets] Ошибка: {e}\n{traceback.format_exc()}")
        return None

def update_google_sheets(object_id, status="✅ Обработан"):
    try:
        client = init_google_sheets()
        if not client:
            print("[update_google_sheets] No Google client configured")
            return False
        sheet = client.open("Объекты ИПУГ").sheet1
        all_data = sheet.get_all_values()
        for i, row in enumerate(all_data, start=1):
            if i == 1:
                continue
            if row and str(row[0]).strip() == str(object_id):
                sheet.update_cell(i, 4, status)
                print(f"[update_google_sheets] Updated object {object_id} -> {status}")
                return True
        return False
    except Exception as e:
        print(f"[update_google_sheets] Ошибка: {e}\n{traceback.format_exc()}")
        return False

# ========== АРХИВ ==========
def save_to_archive(object_id, all_steps):
    """Отправляет в архивную группу сгруппированно: сначала сообщение-инфо, затем все файлы."""
    try:
        total_files = sum(len(s['files']) for s in all_steps)
        # составим краткое описание типов
        types_count = {}
        for s in all_steps:
            for f in s['files']:
                types_count[f['type']] = types_count.get(f['type'], 0) + 1
        types_str = " + ".join([f"{k}:{v}" for k, v in types_count.items()]) if types_count else "файлы"

        info_text = f"💾 ОБЪЕКТ #{object_id}\n📁 {total_files} {types_str}\n🕒 {datetime.now().strftime('%d.%m.%Y %H:%M')}"
        bot.send_message(ARCHIVE_CHAT_ID, info_text)

        # отправляем файлы группами по шагам (можно далее группировать)
        for s in all_steps:
            if not s['files']:
                continue
            # подпись для группы файлов (имя шага)
            bot.send_message(ARCHIVE_CHAT_ID, f"--- {s['name']} ({len(s['files'])}) ---")
            for f in s['files']:
                try:
                    if f['type'] == 'photo':
                        bot.send_photo(ARCHIVE_CHAT_ID, f['file_id'])
                    elif f['type'] == 'document':
                        bot.send_document(ARCHIVE_CHAT_ID, f['file_id'])
                    elif f['type'] == 'video':
                        bot.send_video(ARCHIVE_CHAT_ID, f['file_id'])
                except Exception as e:
                    print(f"[save_to_archive] Ошибка отправки файла: {e}\n{traceback.format_exc()}")
        print(f"[save_to_archive] Архивирован объект {object_id}, файлов: {total_files}")
        return True
    except Exception as e:
        print(f"[save_to_archive] Ошибка общего: {e}\n{traceback.format_exc()}")
        return False

# ========== КЛАВИАТУРЫ ==========
def create_main_keyboard():
    kb = ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    kb.add(
        KeyboardButton('/info'),
        KeyboardButton('/photo'),
        KeyboardButton('/download'),
        KeyboardButton('/result'),
        KeyboardButton('/help')
    )
    return kb

def create_upload_keyboard():
    kb = ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    kb.add(KeyboardButton('/OK'), KeyboardButton('/cancel'))
    return kb

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

# ========== КОМАНДЫ БОТА (переименованные) ==========
@bot.message_handler(commands=['start', 'help'])
def cmd_start(message):
    text = (
        "🤖 Бот для управления объектами ИПУГ\n\n"
        "Доступные команды:\n"
        "/info - информация об объекте\n"
        "/photo - пошаговая загрузка фото (раньше /upload)\n"
        "/download - скачать файлы объекта\n"
        "/result - показать обработанные объекты (раньше /processed)\n"
        "/help - показать это сообщение\n"
    )
    send_message_with_topic(message.chat.id, text, message.message_thread_id, create_main_keyboard())

@bot.message_handler(commands=['photo'])
def cmd_photo(message):
    """Запускает процесс: просит ввести номер объекта"""
    key = make_key_from_message(message)
    user_state[key] = {'command': 'await_object'}
    print(f"[STATE CREATE] {key} -> await_object (waiting for object id)")
    send_message_with_topic(message.chat.id, "📤 Введите номер объекта для загрузки файлов:", message.message_thread_id, create_main_keyboard())

@bot.message_handler(commands=['download'])
def cmd_download(message):
    parts = message.text.strip().split()
    if len(parts) == 1:
        key = make_key_from_message(message)
        user_state[key] = {'command': 'download_wait'}
        print(f"[STATE CREATE] {key} -> download_wait")
        send_message_with_topic(message.chat.id, "📥 Введите номер объекта для скачивания файлов:", message.message_thread_id, create_main_keyboard())
    else:
        object_id = parts[1]
        # download immediately
        download_object_files(message, object_id)

@bot.message_handler(commands=['result'])
def cmd_result(message):
    if not object_files:
        send_message_with_topic(message.chat.id, "📭 Нет обработанных объектов", message.message_thread_id, create_main_keyboard())
        return
    # отправляем краткий список
    lines = [f"{i+1}. #{obj} — {len(files)} файлов" for i, (obj, files) in enumerate(object_files.items())]
    send_message_with_topic(message.chat.id, "📊 Обработанные объекты:\n\n" + "\n".join(lines), message.message_thread_id, create_main_keyboard())

# /OK replaces /done
@bot.message_handler(commands=['OK'])
def cmd_OK(message):
    key = make_key_from_message(message)
    state = user_state.get(key)
    if not state:
        send_message_with_topic(message.chat.id, "❌ Нет активной загрузки", message.message_thread_id, create_main_keyboard())
        return

    if state.get('command') == 'upload_steps':
        # завершение текущего шага — перейти к следующему
        state['step_index'] += 1
        print(f"[STEP ADVANCE] {key} -> now step_index {state['step_index']}")
        if state['step_index'] >= len(state['steps']):
            # завершили все шаги
            object_id = state['object_id']
            all_steps = state['steps']

            # сохраняем в глобальный объект (для /result и быстрого доступа)
            object_files[object_id] = []
            for s in all_steps:
                object_files[object_id].extend(s['files'])

            # отправляем в архив
            save_to_archive(object_id, all_steps)
            update_google_sheets(object_id)

            # формируем финальный отчет
            report_lines = [f"✅ Загрузка файлов завершена для объекта #{object_id}\n"]
            for i, s in enumerate(all_steps, start=1):
                report_lines.append(f"{i}. ✅ {s['name']}: {len(s['files'])} файлов")
            report = "\n".join(report_lines)

            send_message_with_topic(state['chat_id'], report, state['thread_id'], create_main_keyboard())
            user_state.pop(key, None)
            print(f"[STATE REMOVE] {key} -> upload complete for {object_id}")
        else:
            # переходим к следующему шагу (без лишних уведомлений между загрузками, только приглашение к следующему шагу)
            next_step = state['steps'][state['step_index']]
            send_message_with_topic(state['chat_id'], f"📸 Загрузите {next_step['name']}", state['thread_id'], create_upload_keyboard())
    else:
        # если ожидали id объекта или другое — допустим, обрабатываем /OK не применимо
        send_message_with_topic(message.chat.id, "❌ Невозможно применить /OK в текущем состоянии", message.message_thread_id, create_main_keyboard())

@bot.message_handler(commands=['cancel'])
def cmd_cancel(message):
    key = make_key_from_message(message)
    if key in user_state:
        state = user_state.pop(key, None)
        obj = None
        if state:
            obj = state.get('object_id')
        send_message_with_topic(message.chat.id, f"❌ Загрузка для объекта {obj or ''} отменена", message.message_thread_id, create_main_keyboard())
        print(f"[STATE REMOVE] {key} -> cancel")

# ========== ОБРАБОТКА ТЕКСТА (ввод ID объекта, т.д.) ==========
@bot.message_handler(func=lambda m: m.text is not None, content_types=['text'])
def handle_text_messages(message):
    text = message.text.strip()
    # игнорируем команды тут — они идут отдельными обработчиками
    if text.startswith('/'):
        return

    key = make_key_from_message(message)
    state = user_state.get(key)
    if not state:
        return

    cmd = state.get('command')

    # ввод id для photo
    if cmd == 'await_object':
        object_id = text
        if object_id not in objects_data:
            send_message_with_topic(message.chat.id, f"❌ Объект #{object_id} не найден", message.message_thread_id, create_main_keyboard())
            # оставляем состояние, чтобы пользователь мог ввести заново
            return

        # Инициализируем шаги
        steps = [{'name': s, 'files': []} for s in UPLOAD_STEPS]
        user_state[key] = {
            'command': 'upload_steps',
            'object_id': object_id,
            'step_index': 0,
            'steps': steps,
            'chat_id': message.chat.id,
            'thread_id': message.message_thread_id
        }
        print(f"[STATE UPDATE] {key} -> upload_steps for object {object_id}")
        # приглашаем загрузить первый шаг
        send_message_with_topic(message.chat.id, f"📸 Загрузите {steps[0]['name']}", message.message_thread_id, create_upload_keyboard())
        return

    # ввод id для download
    if cmd == 'download_wait':
        object_id = text
        user_state.pop(key, None)
        download_object_files(message, object_id)
        return

# ========== ОБРАБОТКА ФАЙЛОВ ==========
@bot.message_handler(content_types=['photo', 'document', 'video'])
def handle_media(message):
    key = make_key_from_message(message)
    state = user_state.get(key)
    if not state or state.get('command') != 'upload_steps':
        # нет активной пошаговой загрузки для этого пользователя
        return

    try:
        step = state['steps'][state['step_index']]
    except Exception:
        print(f"[handle_media] Неверный step_index for {key} state: {state}")
        return

    file_info = {}
    if message.photo:
        file_info = {'type': 'photo', 'file_id': message.photo[-1].file_id}
    elif message.document:
        file_info = {'type': 'document', 'file_id': message.document.file_id, 'name': message.document.file_name}
    elif message.video:
        file_info = {'type': 'video', 'file_id': message.video.file_id}
    else:
        return

    # Добавляем файл к текущему шагу
    step['files'].append(file_info)
    step_count = len(step['files'])
    print(f"[FILE RECEIVED] {key} step {state['step_index']} ({step['name']}) -> total files this step: {step_count}")

    # НЕ отправляем никаких дополнительных уведомлений в чат (как просил)
    # Просто принимаем файлы; пользователь завершит шаг командой /OK

# ========== Скачивание файлов объекта ==========
def download_object_files(message, object_id):
    if object_id not in object_files:
        send_message_with_topic(message.chat.id, f"❌ Для объекта #{object_id} нет файлов", message.message_thread_id, create_main_keyboard())
        return

    files = object_files[object_id]
    send_message_with_topic(message.chat.id, f"📁 Отправляю файлы объекта #{object_id}...", message.message_thread_id, create_main_keyboard())
    sent = 0
    for f in files:
        try:
            if f['type'] == 'photo':
                send_photo_with_topic(message.chat.id, f['file_id'], message.message_thread_id)
            elif f['type'] == 'document':
                send_document_with_topic(message.chat.id, f['file_id'], message.message_thread_id)
            elif f['type'] == 'video':
                send_video_with_topic(message.chat.id, f['file_id'], message.message_thread_id)
            sent += 1
        except Exception as e:
            print(f"[download_object_files] Ошибка отправки: {e}\n{traceback.format_exc()}")
    send_message_with_topic(message.chat.id, f"✅ Отправлено {sent} файлов", message.message_thread_id, create_main_keyboard())

# ========== HANDLER for processed buttons (if needed) ==========
@bot.message_handler(func=lambda m: m.text is not None and m.text.startswith('📁 #'), content_types=['text'])
def handle_processed_button(message):
    # формат "📁 #<id>"
    object_id = message.text.replace('📁 #', '').strip()
    download_object_files(message, object_id)

# ========== WEBHOOK (Flask) ==========
@app.route('/' + TOKEN, methods=['POST'])
def receive_update():
    try:
        json_str = request.get_data().decode('utf-8')
        update = telebot.types.Update.de_json(json_str)
        bot.process_new_updates([update])
    except Exception as e:
        print(f"[receive_update] Ошибка: {e}\n{traceback.format_exc()}")
    return "OK", 200

@app.route('/')
def index():
    return "🤖 Бот для управления объектами ИПУГ (running)", 200

# ========== START ==========
if __name__ == "__main__":
    print("🚀 Бот запускается...")
    objects_data = load_objects_from_excel()

    # Установим вебхук
    try:
        bot.remove_webhook()
        if WEBHOOK_BASE:
            WEBHOOK_URL = f"{WEBHOOK_BASE.rstrip('/')}/{TOKEN}"
        else:
            # fallback (твоя ссылка)
            WEBHOOK_URL = f"https://telegram-bot-b6pn.onrender.com/{TOKEN}"
        print(f"[WEBHOOK] Setting webhook to: {WEBHOOK_URL}")
        bot.set_webhook(url=WEBHOOK_URL)
        print("[WEBHOOK] Webhook установлен")
    except Exception as e:
        print(f"[WEBHOOK] Ошибка установки webhook: {e}\n{traceback.format_exc()}")
        # не падаем — всё равно запускаем Flask

    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
