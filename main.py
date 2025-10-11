import os
import json
import openpyxl
import telebot
from telebot.types import ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove
from flask import Flask, request

# ========== НАСТРОЙКИ ==========
TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
bot = telebot.TeleBot(TOKEN)
app = Flask(__name__)

# Файлы для данных
OBJECTS_EXCEL_FILE = 'objects.xlsx'
OBJECTS_DATA_FILE = 'data/objects_data.json'
USER_STATE_FILE = 'data/user_state.json'

# Загружаем данные об объектах из Excel с использованием openpyxl
def load_objects_from_excel():
    try:
        workbook = openpyxl.load_workbook(OBJECTS_EXCEL_FILE)
        sheet = workbook.active
        
        objects_dict = {}
        # Пропускаем заголовок (первую строку) и читаем данные
        for row in sheet.iter_rows(min_row=2, values_only=True):
            if row[0] is None:  # Если пустая строка
                continue
            obj_number = str(row[0]).strip()
            objects_dict[obj_number] = {
                'name': row[1] or '',
                'address': row[2] or '',
                'status': 'Не начат',  # Статус по умолчанию
                'photos': {},          # Для хранения file_id фото
                'acts': {},            # Для хранения file_id актов
                'comments': []         # Для хранения комментариев/проблем
            }
        return objects_dict
    except Exception as e:
        print(f"Ошибка загрузки Excel файла: {e}")
        return {}

# Загружаем или создаем файл с дополнительными данными по объектам
def load_objects_data():
    if os.path.exists(OBJECTS_DATA_FILE):
        with open(OBJECTS_DATA_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    else:
        return {}

# Сохраняем данные об объектах
def save_objects_data(data):
    os.makedirs('data', exist_ok=True)
    with open(OBJECTS_DATA_FILE, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=4)

# Работа с состояниями пользователей (для многошаговых сценариев)
def load_user_state():
    if os.path.exists(USER_STATE_FILE):
        with open(USER_STATE_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    else:
        return {}

def save_user_state(data):
    os.makedirs('data', exist_ok=True)
    with open(USER_STATE_FILE, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=4)

# Инициализация данных
objects_db = load_objects_from_excel()
objects_data = load_objects_data()
user_state = load_user_state()

# Обновляем objects_data данными из Excel, если добавились новые объекты
for obj_id, obj_info in objects_db.items():
    if obj_id not in objects_data:
        objects_data[obj_id] = obj_info
save_objects_data(objects_data)

# ========== КЛАВИАТУРЫ ==========
def main_menu_keyboard():
    keyboard = ReplyKeyboardMarkup(resize_keyboard=True)
    keyboard.add(KeyboardButton('/objects'), KeyboardButton('/report_object'))
    keyboard.add(KeyboardButton('/help'))
    return keyboard

def object_status_keyboard():
    keyboard = ReplyKeyboardMarkup(resize_keyboard=True)
    keyboard.add(KeyboardButton('В работе'), KeyboardButton('Проблема'))
    keyboard.add(KeyboardButton('Ждет приемки'))
    return keyboard

def cancel_keyboard():
    keyboard = ReplyKeyboardMarkup(resize_keyboard=True)
    keyboard.add(KeyboardButton('Отмена'))
    return keyboard

# ========== КОМАНДЫ БОТА ==========
@bot.message_handler(commands=['start'])
def start_message(message):
    bot.send_message(message.chat.id,
                     "Привет! 👋 Я бот для управления проектом установки ИПУГ.\n"
                     "Я помогу найти информацию об объектах и зафиксировать выполнение работ.",
                     reply_markup=main_menu_keyboard())

@bot.message_handler(commands=['help'])
def help_message(message):
    help_text = """
📋 **Доступные команды:**

/start - Начать работу
/help - Показать эту справку

/objects - Найти информацию об объекте по номеру. Можно указать несколько номеров через запятую (например: 5, 7, 10).

/report_object - Начать процесс сдачи объекта. Бот запросит номер объекта, фото и другую необходимую информацию.

*Для монтажников:*
Используйте /report_object, чтобы отчитаться о выполненной работе. Бот проведет вас по всем шагам.
    """
    bot.send_message(message.chat.id, help_text, reply_markup=main_menu_keyboard())

@bot.message_handler(commands=['objects'])
def ask_object_number(message):
    msg = bot.send_message(message.chat.id, "Введите номер(а) объекта(ов) через запятую:", reply_markup=cancel_keyboard())
    bot.register_next_step_handler(msg, process_object_numbers)

def process_object_numbers(message):
    if message.text.strip().lower() == 'отмена':
        bot.send_message(message.chat.id, "Отменено.", reply_markup=main_menu_keyboard())
        return

    numbers = [num.strip() for num in message.text.split(',')]
    response = ""

    for num in numbers:
        if num in objects_data:
            obj = objects_data[num]
            status_emoji = {
                'Не начат': '⚪',
                'В работе': '🟡',
                'Ждет приемки': '🟠',
                'Принят': '🟢',
                'Проблема': '🔴'
            }.get(obj['status'], '⚪')

            response += f"""
{status_emoji} *Объект №{num}*
*Наименование:* {obj['name']}
*Адрес:* {obj['address']}
*Статус:* {obj['status']}
"""
            if obj.get('comments'):
                response += f"*Комментарии:* {', '.join(obj['comments'])}\n"
            response += "---\n"
        else:
            response += f"❌ Объект с номером {num} не найден.\n---\n"

    bot.send_message(message.chat.id, response, parse_mode='Markdown', reply_markup=main_menu_keyboard())

# ========== ПРОЦЕСС СДАЧИ ОБЪЕКТА ==========
@bot.message_handler(commands=['report_object'])
def start_object_report(message):
    msg = bot.send_message(message.chat.id, "Введите номер объекта для отчета:", reply_markup=cancel_keyboard())
    bot.register_next_step_handler(msg, process_report_object_number)

def process_report_object_number(message):
    if message.text.strip().lower() == 'отмена':
        bot.send_message(message.chat.id, "Отменено.", reply_markup=main_menu_keyboard())
        return

    object_id = message.text.strip()
    if object_id not in objects_data:
        msg = bot.send_message(message.chat.id, "Объект с таким номером не найден. Проверьте номер и введите снова:", reply_markup=cancel_keyboard())
        bot.register_next_step_handler(msg, process_report_object_number)
        return

    # Сохраняем состояние пользователя
    user_state[str(message.chat.id)] = {'object_id': object_id, 'step': 'waiting_photos'}
    save_user_state(user_state)

    # Запрашиваем фото
    photo_requirements = """
📸 *ШАГ 1: СДЕЛАЙТЕ ФОТО*

Для приемки работ необходимо предоставить следующие фотографии (согласно Техническому заданию):

1.  Место установки ИПУГ *до* монтажа.
2.  Общий вид существующего счетчика газа.
3.  Пломбы Поставщика на существующем счетчике (номера должны быть видны).
4.  Общий вид ИПУГ *после* монтажа.
5.  Пломбы Поставщика на новом ИПУГ (номера, должен быть виден сам ИПУГ).
6.  Участок газопровода перед ИПУГ с нанесенной стрелкой направления газа.
7.  Информационная табличка (шильдик) ИПУГ крупным планом (должен быть виден заводской номер).

*Отправьте все фотографии одним сообщением.*
    """
    msg = bot.send_message(message.chat.id, photo_requirements, parse_mode='Markdown', reply_markup=cancel_keyboard())
    bot.register_next_step_handler(msg, process_report_photos)

def process_report_photos(message):
    user_id = str(message.chat.id)
    if user_id not in user_state:
        bot.send_message(message.chat.id, "Сессия устарела. Начните заново с /report_object.", reply_markup=main_menu_keyboard())
        return

    if message.text and message.text.strip().lower() == 'отмена':
        del user_state[user_id]
        save_user_state(user_state)
        bot.send_message(message.chat.id, "Отменено.", reply_markup=main_menu_keyboard())
        return

    # Проверяем, что прислали фото
    if message.photo is None:
        msg = bot.send_message(message.chat.id, "Пожалуйста, отправьте фотографии. Если что-то пошло не так, введите 'Отмена'.", reply_markup=cancel_keyboard())
        bot.register_next_step_handler(msg, process_report_photos)
        return

    # Сохраняем file_id фотографий
    object_id = user_state[user_id]['object_id']
    if 'photos' not in objects_data[object_id]:
        objects_data[object_id]['photos'] = {}

    # Сохраняем последнее (самое качественное) фото
    photo_id = message.photo[-1].file_id
    objects_data[object_id]['photos'][f'photo_{len(objects_data[object_id]["photos"])}'] = photo_id

    # Переходим к следующему шагу
    user_state[user_id]['step'] = 'waiting_status'
    save_user_state(user_state)

    msg = bot.send_message(message.chat.id, "Фото получены. Теперь укажите статус объекта:", reply_markup=object_status_keyboard())
    bot.register_next_step_handler(msg, process_report_status)

def process_report_status(message):
    user_id = str(message.chat.id)
    if user_id not in user_state:
        bot.send_message(message.chat.id, "Сессия устарела. Начните заново с /report_object.", reply_markup=main_menu_keyboard())
        return

    if message.text.strip().lower() == 'отмена':
        del user_state[user_id]
        save_user_state(user_state)
        bot.send_message(message.chat.id, "Отменено.", reply_markup=main_menu_keyboard())
        return

    object_id = user_state[user_id]['object_id']
    new_status = message.text

    # Обновляем статус
    objects_data[object_id]['status'] = new_status
    save_objects_data(objects_data)

    # Если статус "Проблема", запрашиваем комментарий
    if new_status == 'Проблема':
        user_state[user_id]['step'] = 'waiting_problem_comment'
        save_user_state(user_state)
        msg = bot.send_message(message.chat.id, "Опишите проблему:", reply_markup=cancel_keyboard())
        bot.register_next_step_handler(msg, process_problem_comment)
    else:
        # Завершаем отчет
        finalize_object_report(message.chat.id, user_id, object_id)

def process_problem_comment(message):
    user_id = str(message.chat.id)
    if user_id not in user_state:
        bot.send_message(message.chat.id, "Сессия устарела.", reply_markup=main_menu_keyboard())
        return

    if message.text.strip().lower() == 'отмена':
        del user_state[user_id]
        save_user_state(user_state)
        bot.send_message(message.chat.id, "Отменено.", reply_markup=main_menu_keyboard())
        return

    object_id = user_state[user_id]['object_id']
    if 'comments' not in objects_data[object_id]:
        objects_data[object_id]['comments'] = []
    objects_data[object_id]['comments'].append(message.text)
    save_objects_data(objects_data)

    # Завершаем отчет
    finalize_object_report(message.chat.id, user_id, object_id)

def finalize_object_report(chat_id, user_id, object_id):
    # Завершаем процесс, чистим состояние
    del user_state[user_id]
    save_user_state(user_state)

    obj_name = objects_data[object_id]['name']
    status = objects_data[object_id]['status']

    bot.send_message(chat_id,
                     f"✅ Отчет по объекту *{object_id} - {obj_name}* сохранен!\n"
                     f"Статус установлен: *{status}*",
                     parse_mode='Markdown',
                     reply_markup=main_menu_keyboard())

    # ОПОВЕЩЕНИЕ ДЛЯ ОФИСА/РУКОВОДИТЕЛЯ (можно вынести в отдельный чат)
    # admin_chat_id = os.environ.get("ADMIN_CHAT_ID")
    # if admin_chat_id:
    #     bot.send_message(admin_chat_id, f"📢 Новый отчет по объекту {object_id}. Статус: {status}")

# ========== ОБРАБОТКА ТЕКСТА И НЕИЗВЕСТНЫХ КОМАНД ==========
@bot.message_handler(func=lambda message: True)
def handle_all_messages(message):
    if message.text in ['В работе', 'Проблема', 'Ждет приемки']:
        # Если пользователь нажал на кнопку статуса вне контекста
        bot.send_message(message.chat.id, "Чтобы изменить статус объекта, используйте команду /report_object", reply_markup=main_menu_keyboard())
    else:
        bot.send_message(message.chat.id, "Я не понимаю эту команду. Используйте /help для списка команд.", reply_markup=main_menu_keyboard())

# ========== WEBHOOK ЛОГИКА (для Render) ==========
@app.route('/' + TOKEN, methods=['POST'])
def receive_update():
    json_str = request.get_data().decode('utf-8')
    update = telebot.types.Update.de_json(json_str)
    bot.process_new_updates([update])
    return "OK", 200

@app.route('/')
def index():
    return "Бот для управления проектом ИПУГ работает! ✅", 200

if __name__ == "__main__":
    print("Бот запускается...")
    # Создаем папку для данных, если её нет
    os.makedirs('data', exist_ok=True)

    bot.remove_webhook()
    # ⚠️ ЗАМЕНИТЕ НА ВАШ URL РENDER
    WEBHOOK_URL = f"https://telegram-bot-b6pn.onrender.com/{TOKEN}"
    bot.set_webhook(url=WEBHOOK_URL)
    print(f"Webhook установлен: {WEBHOOK_URL}")

    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))
