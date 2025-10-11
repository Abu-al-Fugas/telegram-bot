import os
import json
import openpyxl
import telebot
from telebot.types import ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove
from flask import Flask, request
from datetime import datetime

# ========== НАСТРОЙКИ ==========
TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
bot = telebot.TeleBot(TOKEN)
app = Flask(__name__)

# Файлы для данных
OBJECTS_EXCEL_FILE = 'objects.xlsx'
OBJECTS_DATA_FILE = 'data/objects_data.json'
USER_STATE_FILE = 'data/user_state.json'

# Статусы объектов
STATUSES = {
    'not_started': '⚪ Не начат',
    'in_progress': '🟡 В работе', 
    'waiting_acceptance': '🟠 Ждет приемки',
    'accepted': '🟢 Принят',
    'problem': '🔴 Проблема'
}

# Загружаем данные об объектах из Excel
def load_objects_from_excel():
    try:
        workbook = openpyxl.load_workbook(OBJECTS_EXCEL_FILE)
        sheet = workbook.active
        
        objects_dict = {}
        for row in sheet.iter_rows(min_row=2, values_only=True):
            if row[0] is None:
                continue
            obj_number = str(row[0]).strip()
            objects_dict[obj_number] = {
                'name': row[1] or '',
                'address': row[2] or '',
                'status': 'not_started',
                'photos': {},
                'acts': {},
                'comments': [],
                'history': [],
                'equipment': {
                    'ipug_received': False,
                    'sim_received': False,
                    'seals_received': False,
                    'ipug_installed': False,
                    'sim_installed': False,
                    'seals_installed': False
                },
                'dates': {
                    'started': None,
                    'completed': None,
                    'accepted': None
                }
            }
        return objects_dict
    except Exception as e:
        print(f"Ошибка загрузки Excel файла: {e}")
        return {}

def load_objects_data():
    if os.path.exists(OBJECTS_DATA_FILE):
        with open(OBJECTS_DATA_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    else:
        return {}

def save_objects_data(data):
    os.makedirs('data', exist_ok=True)
    with open(OBJECTS_DATA_FILE, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=4)

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

# Функция для отправки сообщений с поддержкой тем
def send_message_with_topic(chat_id, text, message_thread_id=None, reply_markup=None, parse_mode=None):
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
        if "message thread not found" in str(e).lower():
            return bot.send_message(
                chat_id=chat_id,
                text=text,
                reply_markup=reply_markup,
                parse_mode=parse_mode
            )
        raise e

def add_object_history(object_id, action, user_id=None):
    """Добавляет запись в историю объекта"""
    if object_id in objects_data:
        history_entry = {
            'timestamp': datetime.now().isoformat(),
            'action': action,
            'user_id': user_id
        }
        objects_data[object_id]['history'].append(history_entry)
        save_objects_data(objects_data)

# Инициализация данных
objects_db = load_objects_from_excel()
objects_data = load_objects_data()
user_state = load_user_state()

# Обновляем objects_data данными из Excel
for obj_id, obj_info in objects_db.items():
    if obj_id not in objects_data:
        objects_data[obj_id] = obj_info
save_objects_data(objects_data)

# ========== КЛАВИАТУРЫ ==========
def main_menu_keyboard():
    keyboard = ReplyKeyboardMarkup(resize_keyboard=True)
    keyboard.add(KeyboardButton('/objects'), KeyboardButton('/start_work'))
    keyboard.add(KeyboardButton('/complete_work'), KeyboardButton('/report_problem'))
    keyboard.add(KeyboardButton('/equipment'), KeyboardButton('/help'))
    return keyboard

def cancel_keyboard():
    keyboard = ReplyKeyboardMarkup(resize_keyboard=True)
    keyboard.add(KeyboardButton('Отмена'))
    return keyboard

def equipment_keyboard():
    keyboard = ReplyKeyboardMarkup(resize_keyboard=True)
    keyboard.add(KeyboardButton('Получен ИПУГ'), KeyboardButton('Получены SIM-карты'))
    keyboard.add(KeyboardButton('Получены пломбы'), KeyboardButton('Установлен ИПУГ'))
    keyboard.add(KeyboardButton('Установлены SIM'), KeyboardButton('Установлены пломбы'))
    keyboard.add(KeyboardButton('Назад'))
    return keyboard

# ========== КОМАНДЫ БОТА ==========
@bot.message_handler(commands=['start'])
def start_message(message):
    send_message_with_topic(
        chat_id=message.chat.id,
        message_thread_id=message.message_thread_id,
        text="👋 Привет! Я бот для управления установкой ИПУГ.\n\n"
             "📋 *Основные команды:*\n"
             "/objects - Информация об объектах\n"
             "/start_work - Начать работу на объекте\n" 
             "/complete_work - Завершить работы и отправить на приемку\n"
             "/report_problem - Сообщить о проблеме\n"
             "/equipment - Управление оборудованием\n"
             "/help - Полная справка",
        parse_mode='Markdown',
        reply_markup=main_menu_keyboard()
    )

@bot.message_handler(commands=['help'])
def help_message(message):
    help_text = """
🔧 *ПОЛНЫЙ СПИСОК КОМАНД*

📊 *Информация:*
/objects - Информация об объектах (можно несколько через запятую)

🏗️ *Работы на объектах:*
/start_work - Начать работы на объекте (статус: В работе)
/complete_work - Завершить работы и отправить на приемку (статус: Ждет приемки)
/report_problem - Сообщить о проблеме на объекте

📦 *Оборудование:*
/equipment - Отметить получение/установку оборудования

🔄 *Статусы объектов:*
⚪ Не начат - Работы не начинались
🟡 В работе - Бригада на объекте
🟠 Ждет приемки - Работы завершены, ожидается приемка
🟢 Принят - Объект принят заказчиком
🔴 Проблема - Возникли проблемы

*Примеры:*
`/objects 5, 7, 10` - информация по трем объектам
`/start_work` - начать работу на объекте
"""
    send_message_with_topic(
        chat_id=message.chat.id,
        message_thread_id=message.message_thread_id,
        text=help_text,
        parse_mode='Markdown',
        reply_markup=main_menu_keyboard()
    )

@bot.message_handler(commands=['objects'])
def ask_object_number(message):
    msg = send_message_with_topic(
        chat_id=message.chat.id,
        message_thread_id=message.message_thread_id,
        text="Введите номер(а) объекта(ов) через запятую:",
        reply_markup=cancel_keyboard()
    )
    bot.register_next_step_handler(msg, process_object_numbers)

def process_object_numbers(message):
    if message.text.strip().lower() == 'отмена':
        send_message_with_topic(
            chat_id=message.chat.id,
            message_thread_id=message.message_thread_id,
            text="Отменено.",
            reply_markup=main_menu_keyboard()
        )
        return

    numbers = [num.strip() for num in message.text.split(',')]
    response = ""

    for num in numbers:
        if num in objects_data:
            obj = objects_data[num]
            status_display = STATUSES.get(obj['status'], '⚪ Не начат')
            
            response += f"""
{status_display} *Объект №{num}*
*Наименование:* {obj['name']}
*Адрес:* {obj['address']}
*Статус:* {status_display}
"""
            # Информация об оборудовании
            equipment = obj['equipment']
            equipment_info = []
            if equipment['ipug_received']: equipment_info.append('✅ ИПУГ получен')
            if equipment['sim_received']: equipment_info.append('✅ SIM получены') 
            if equipment['seals_received']: equipment_info.append('✅ Пломбы получены')
            if equipment['ipug_installed']: equipment_info.append('✅ ИПУГ установлен')
            if equipment['sim_installed']: equipment_info.append('✅ SIM установлены')
            if equipment['seals_installed']: equipment_info.append('✅ Пломбы установлены')
            
            if equipment_info:
                response += f"*Оборудование:* {', '.join(equipment_info)}\n"
            
            if obj.get('comments'):
                response += f"*Комментарии:* {', '.join(obj['comments'])}\n"
            
            response += "---\n"
        else:
            response += f"❌ Объект с номером {num} не найден.\n---\n"

    send_message_with_topic(
        chat_id=message.chat.id,
        message_thread_id=message.message_thread_id,
        text=response,
        parse_mode='Markdown',
        reply_markup=main_menu_keyboard()
    )

# ========== НАЧАТЬ РАБОТУ ==========
@bot.message_handler(commands=['start_work'])
def start_work(message):
    msg = send_message_with_topic(
        chat_id=message.chat.id,
        message_thread_id=message.message_thread_id,
        text="Введите номер объекта для начала работ:",
        reply_markup=cancel_keyboard()
    )
    bot.register_next_step_handler(msg, process_start_work)

def process_start_work(message):
    if message.text.strip().lower() == 'отмена':
        send_message_with_topic(
            chat_id=message.chat.id,
            message_thread_id=message.message_thread_id,
            text="Отменено.",
            reply_markup=main_menu_keyboard()
        )
        return

    object_id = message.text.strip()
    if object_id not in objects_data:
        msg = send_message_with_topic(
            chat_id=message.chat.id,
            message_thread_id=message.message_thread_id,
            text="Объект не найден. Введите правильный номер:",
            reply_markup=cancel_keyboard()
        )
        bot.register_next_step_handler(msg, process_start_work)
        return

    # Меняем статус
    objects_data[object_id]['status'] = 'in_progress'
    objects_data[object_id]['dates']['started'] = datetime.now().isoformat()
    save_objects_data(objects_data)
    
    # Добавляем в историю
    add_object_history(object_id, f"Работы начаты пользователем {message.from_user.id}")

    send_message_with_topic(
        chat_id=message.chat.id,
        message_thread_id=message.message_thread_id,
        text=f"✅ *Работы начаты на объекте {object_id}*\n"
             f"Статус изменен на: 🟡 В работе\n"
             f"Не забудьте отметить полученное оборудование командой /equipment",
        parse_mode='Markdown',
        reply_markup=main_menu_keyboard()
    )

# ========== ЗАВЕРШИТЬ РАБОТЫ ==========
@bot.message_handler(commands=['complete_work'])
def complete_work(message):
    msg = send_message_with_topic(
        chat_id=message.chat.id,
        message_thread_id=message.message_thread_id,
        text="Введите номер объекта для завершения работ:\n\n"
             "⚠️ *Перед завершением убедитесь, что:*\n"
             "• Все оборудование установлено\n"
             "• Сделаны все необходимые фотографии\n"
             "• Заполнены все документы",
        parse_mode='Markdown',
        reply_markup=cancel_keyboard()
    )
    bot.register_next_step_handler(msg, process_complete_work_number)

def process_complete_work_number(message):
    if message.text.strip().lower() == 'отмена':
        send_message_with_topic(
            chat_id=message.chat.id,
            message_thread_id=message.message_thread_id,
            text="Отменено.",
            reply_markup=main_menu_keyboard()
        )
        return

    object_id = message.text.strip()
    if object_id not in objects_data:
        msg = send_message_with_topic(
            chat_id=message.chat.id,
            message_thread_id=message.message_thread_id,
            text="Объект не найден. Введите правильный номер:",
            reply_markup=cancel_keyboard()
        )
        bot.register_next_step_handler(msg, process_complete_work_number)
        return

    # Сохраняем состояние для многошагового процесса
    user_state[str(message.chat.id)] = {
        'object_id': object_id,
        'step': 'waiting_complete_photos',
        'message_thread_id': message.message_thread_id
    }
    save_user_state(user_state)

    # Запрашиваем фотографии
    photo_requirements = """
📸 *СДЕЛАЙТЕ ФОТО ДЛЯ ПРИЕМКИ*

Для приемки работ необходимо предоставить фотографии:

1. 📍 Место установки ИПУГ *до* монтажа
2. 🔧 Общий вид существующего счетчика газа  
3. 🔒 Пломбы на старом счетчике (номера должны быть видны)
4. 🆕 Общий вид ИПУГ *после* монтажа
5. 🏷️ Пломбы на новом ИПУГ (номера + виден ИПУГ)
6. ➡️ Участок газопровода со стрелкой направления газа
7. 🔍 Информационная табличка ИПУГ (заводской номер)

*Отправьте все фотографии ОДНИМ сообщением*",
    """
    msg = send_message_with_topic(
        chat_id=message.chat.id,
        message_thread_id=message.message_thread_id,
        text=photo_requirements,
        parse_mode='Markdown',
        reply_markup=cancel_keyboard()
    )
    bot.register_next_step_handler(msg, process_complete_photos)

def process_complete_photos(message):
    user_id = str(message.chat.id)
    if user_id not in user_state:
        send_message_with_topic(
            chat_id=message.chat.id,
            message_thread_id=message.message_thread_id,
            text="Сессия устарела. Начните заново с /complete_work.",
            reply_markup=main_menu_keyboard()
        )
        return

    if message.text and message.text.strip().lower() == 'отмена':
        del user_state[user_id]
        save_user_state(user_state)
        send_message_with_topic(
            chat_id=message.chat.id,
            message_thread_id=message.message_thread_id,
            text="Отменено.",
            reply_markup=main_menu_keyboard()
        )
        return

    # Проверяем фото
    if message.photo is None:
        msg = send_message_with_topic(
            chat_id=message.chat.id,
            message_thread_id=message.message_thread_id,
            text="Пожалуйста, отправьте фотографии. Если возникли проблемы, введите 'Отмена'.",
            reply_markup=cancel_keyboard()
        )
        bot.register_next_step_handler(msg, process_complete_photos)
        return

    # Сохраняем фото
    object_id = user_state[user_id]['object_id']
    if 'photos' not in objects_data[object_id]:
        objects_data[object_id]['photos'] = {}

    # Сохраняем file_id фотографий
    for i, photo in enumerate(message.photo):
        photo_id = photo.file_id
        objects_data[object_id]['photos'][f'complete_photo_{i}'] = photo_id

    # Меняем статус
    objects_data[object_id]['status'] = 'waiting_acceptance'
    objects_data[object_id]['dates']['completed'] = datetime.now().isoformat()
    save_objects_data(objects_data)
    
    # Добавляем в историю
    add_object_history(object_id, f"Работы завершены, отправлены на приемку. Фото: {len(message.photo)} шт.")

    # Завершаем
    del user_state[user_id]
    save_user_state(user_state)

    send_message_with_topic(
        chat_id=message.chat.id,
        message_thread_id=message.message_thread_id,
        text=f"✅ *Работы завершены на объекте {object_id}*\n"
             f"Статус изменен на: 🟠 Ждет приемки\n"
             f"Фотографии сохранены: {len(message.photo)} шт.\n"
             f"Объект передан на проверку заказчику.",
        parse_mode='Markdown',
        reply_markup=main_menu_keyboard()
    )

# ========== ПРОБЛЕМЫ ==========
@bot.message_handler(commands=['report_problem'])
def report_problem(message):
    msg = send_message_with_topic(
        chat_id=message.chat.id,
        message_thread_id=message.message_thread_id,
        text="Введите номер объекта с проблемой:",
        reply_markup=cancel_keyboard()
    )
    bot.register_next_step_handler(msg, process_problem_number)

def process_problem_number(message):
    if message.text.strip().lower() == 'отмена':
        send_message_with_topic(
            chat_id=message.chat.id,
            message_thread_id=message.message_thread_id,
            text="Отменено.",
            reply_markup=main_menu_keyboard()
        )
        return

    object_id = message.text.strip()
    if object_id not in objects_data:
        msg = send_message_with_topic(
            chat_id=message.chat.id,
            message_thread_id=message.message_thread_id,
            text="Объект не найден. Введите правильный номер:",
            reply_markup=cancel_keyboard()
        )
        bot.register_next_step_handler(msg, process_problem_number)
        return

    # Сохраняем состояние
    user_state[str(message.chat.id)] = {
        'object_id': object_id,
        'step': 'waiting_problem_description',
        'message_thread_id': message.message_thread_id
    }
    save_user_state(user_state)

    msg = send_message_with_topic(
        chat_id=message.chat.id,
        message_thread_id=message.message_thread_id,
        text="Опишите проблему:",
        reply_markup=cancel_keyboard()
    )
    bot.register_next_step_handler(msg, process_problem_description)

def process_problem_description(message):
    user_id = str(message.chat.id)
    if user_id not in user_state:
        send_message_with_topic(
            chat_id=message.chat.id,
            message_thread_id=message.message_thread_id,
            text="Сессия устарела.",
            reply_markup=main_menu_keyboard()
        )
        return

    if message.text.strip().lower() == 'отмена':
        del user_state[user_id]
        save_user_state(user_state)
        send_message_with_topic(
            chat_id=message.chat.id,
            message_thread_id=message.message_thread_id,
            text="Отменено.",
            reply_markup=main_menu_keyboard()
        )
        return

    object_id = user_state[user_id]['object_id']
    
    # Меняем статус и добавляем комментарий
    objects_data[object_id]['status'] = 'problem'
    if 'comments' not in objects_data[object_id]:
        objects_data[object_id]['comments'] = []
    objects_data[object_id]['comments'].append(f"{datetime.now().strftime('%d.%m.%Y')}: {message.text}")
    save_objects_data(objects_data)
    
    # Добавляем в историю
    add_object_history(object_id, f"Зарегистрирована проблема: {message.text}")

    # Завершаем
    del user_state[user_id]
    save_user_state(user_state)

    send_message_with_topic(
        chat_id=message.chat.id,
        message_thread_id=message.message_thread_id,
        text=f"🔴 *Проблема зарегистрирована на объекте {object_id}*\n"
             f"Описание: {message.text}\n"
             f"Статус изменен на: 🔴 Проблема",
        parse_mode='Markdown',
        reply_markup=main_menu_keyboard()
    )

# ========== ОБОРУДОВАНИЕ ==========
@bot.message_handler(commands=['equipment'])
def equipment_menu(message):
    msg = send_message_with_topic(
        chat_id=message.chat.id,
        message_thread_id=message.message_thread_id,
        text="Выберите действие с оборудованием:",
        reply_markup=equipment_keyboard()
    )

@bot.message_handler(func=lambda message: message.text in [
    'Получен ИПУГ', 'Получены SIM-карты', 'Получены пломбы',
    'Установлен ИПУГ', 'Установлены SIM', 'Установлены пломбы'
])
def handle_equipment_action(message):
    if message.text == 'Назад':
        send_message_with_topic(
            chat_id=message.chat.id,
            message_thread_id=message.message_thread_id,
            text="Возврат в главное меню:",
            reply_markup=main_menu_keyboard()
        )
        return

    # Сохраняем действие для следующего шага
    user_state[str(message.chat.id)] = {
        'equipment_action': message.text,
        'step': 'waiting_equipment_object',
        'message_thread_id': message.message_thread_id
    }
    save_user_state(user_state)

    msg = send_message_with_topic(
        chat_id=message.chat.id,
        message_thread_id=message.message_thread_id,
        text=f"Введите номер объекта для действия '{message.text}':",
        reply_markup=cancel_keyboard()
    )
    bot.register_next_step_handler(msg, process_equipment_object)

def process_equipment_object(message):
    user_id = str(message.chat.id)
    if user_id not in user_state:
        send_message_with_topic(
            chat_id=message.chat.id,
            message_thread_id=message.message_thread_id,
            text="Сессия устарела.",
            reply_markup=main_menu_keyboard()
        )
        return

    if message.text.strip().lower() == 'отмена':
        del user_state[user_id]
        save_user_state(user_state)
        send_message_with_topic(
            chat_id=message.chat.id,
            message_thread_id=message.message_thread_id,
            text="Отменено.",
            reply_markup=main_menu_keyboard()
        )
        return

    object_id = message.text.strip()
    if object_id not in objects_data:
        msg = send_message_with_topic(
            chat_id=message.chat.id,
            message_thread_id=message.message_thread_id,
            text="Объект не найден. Введите правильный номер:",
            reply_markup=cancel_keyboard()
        )
        bot.register_next_step_handler(msg, process_equipment_object)
        return

    action = user_state[user_id]['equipment_action']
    
    # Обновляем статус оборудования
    equipment_map = {
        'Получен ИПУГ': 'ipug_received',
        'Получены SIM-карты': 'sim_received', 
        'Получены пломбы': 'seals_received',
        'Установлен ИПУГ': 'ipug_installed',
        'Установлены SIM': 'sim_installed',
        'Установлены пломбы': 'seals_installed'
    }
    
    field = equipment_map.get(action)
    if field:
        objects_data[object_id]['equipment'][field] = True
        save_objects_data(objects_data)
        
        # Добавляем в историю
        add_object_history(object_id, f"Оборудование: {action}")

    # Завершаем
    del user_state[user_id]
    save_user_state(user_state)

    send_message_with_topic(
        chat_id=message.chat.id,
        message_thread_id=message.message_thread_id,
        text=f"✅ *Оборудование обновлено на объекте {object_id}*\n"
             f"Действие: {action}",
        parse_mode='Markdown',
        reply_markup=main_menu_keyboard()
    )

# ========== ОБРАБОТКА ОСТАЛЬНЫХ СООБЩЕНИЙ ==========
@bot.message_handler(func=lambda message: True)
def handle_all_messages(message):
    if message.text == 'Назад':
        send_message_with_topic(
            chat_id=message.chat.id,
            message_thread_id=message.message_thread_id,
            text="Главное меню:",
            reply_markup=main_menu_keyboard()
        )
    else:
        send_message_with_topic(
            chat_id=message.chat.id,
            message_thread_id=message.message_thread_id,
            text="Используйте команды из меню или /help для справки.",
            reply_markup=main_menu_keyboard()
        )
@bot.message_handler(commands=['getid'])
def get_chat_id(message):
    chat_info = f"""
💬 Информация о чате:
Название: {message.chat.title if message.chat.title else 'Личные сообщения'}
Тип: {message.chat.type}
ID: {message.chat.id}
ID темы: {message.message_thread_id if message.message_thread_id else 'Нет'}
    """
    bot.reply_to(message, chat_info.strip())
# ========== WEBHOOK ЛОГИКА ==========
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
    os.makedirs('data', exist_ok=True)

    bot.remove_webhook()
    WEBHOOK_URL = f"https://telegram-bot-b6pn.onrender.com/{TOKEN}"
    bot.set_webhook(url=WEBHOOK_URL)
    print(f"Webhook установлен: {WEBHOOK_URL}")

    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))
