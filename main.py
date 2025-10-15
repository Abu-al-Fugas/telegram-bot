import telebot
from telebot import types
from flask import Flask, request
import os
from collections import defaultdict

TOKEN = os.environ.get("BOT_TOKEN")
WEBHOOK_URL = os.environ.get("WEBHOOK_URL")
ARCHIVE_CHAT_ID = "-1003160855229"

bot = telebot.TeleBot(TOKEN, threaded=True)
app = Flask(__name__)

# Хранилище состояния пользователей
user_states = {}
user_uploads = defaultdict(lambda: defaultdict(list))

# Шаги загрузки
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

# Клавиатура для кнопок /OK и /next
def get_step_keyboard():
    keyboard = types.ReplyKeyboardMarkup(resize_keyboard=True)
    keyboard.row("/OK", "/next", "/cancel")
    return keyboard

# Начало работы
@bot.message_handler(commands=['start'])
def cmd_start(message):
    bot.send_message(message.chat.id, "Привет! Используйте /photo для загрузки фотографий.")

# Информация
@bot.message_handler(commands=['info'])
def cmd_info(message):
    bot.send_message(message.chat.id, "Бот для пошаговой загрузки фото с объектами.")

# Команда /photo
@bot.message_handler(commands=['photo'])
def cmd_photo(message):
    user_id = message.from_user.id
    user_states[user_id] = {"step": 0, "active": True}
    bot.send_message(message.chat.id, f"📸 Загрузите {UPLOAD_STEPS[0]}", reply_markup=get_step_keyboard())

# Обработка фото
@bot.message_handler(content_types=['photo'])
def handle_photo(message):
    user_id = message.from_user.id
    if user_id not in user_states or not user_states[user_id]["active"]:
        return

    step = user_states[user_id]["step"]
    if step >= len(UPLOAD_STEPS):
        return

    file_id = message.photo[-1].file_id
    user_uploads[user_id][UPLOAD_STEPS[step]].append(file_id)
    bot.send_message(message.chat.id, f"✅ Фото принято для: {UPLOAD_STEPS[step]}")

# Кнопки /OK, /next, /cancel
@bot.message_handler(commands=['OK', 'next', 'cancel'])
def handle_buttons(message):
    user_id = message.from_user.id
    if user_id not in user_states or not user_states[user_id]["active"]:
        return

    cmd = message.text.lower()
    step = user_states[user_id]["step"]

    if cmd == "/cancel":
        user_states[user_id]["active"] = False
        user_uploads[user_id].clear()
        bot.send_message(message.chat.id, "Загрузка отменена.")
        return

    # Переход к следующему шагу
    user_states[user_id]["step"] += 1
    step = user_states[user_id]["step"]

    if step >= len(UPLOAD_STEPS):
        user_states[user_id]["active"] = False
        send_archive(user_id)
        send_summary(message.chat.id, user_id)
        return

    bot.send_message(message.chat.id, f"📸 Загрузите {UPLOAD_STEPS[step]}", reply_markup=get_step_keyboard())

# Отправка файлов в архив
def send_archive(user_id):
    for step_name, files in user_uploads[user_id].items():
        for file_id in files:
            try:
                bot.send_photo(ARCHIVE_CHAT_ID, file_id, caption=f"{step_name}")
            except Exception as e:
                print(f"[archive error] {e}")

# Отправка отчета пользователю
def send_summary(chat_id, user_id):
    summary = "📑 Загрузка завершена. Фото по шагам:\n"
    for step_name in UPLOAD_STEPS:
        count = len(user_uploads[user_id][step_name])
        summary += f"{step_name}: {count} фото\n"
    bot.send_message(chat_id, summary, reply_markup=types.ReplyKeyboardRemove())
    user_uploads[user_id].clear()

# Webhook обработчик для Flask
@app.route(f"/{TOKEN}", methods=['POST'])
def webhook():
    json_str = request.get_data().decode('utf-8')
    update = telebot.types.Update.de_json(json_str)
    bot.process_new_updates([update])
    return '', 200

# Настройка webhook
if __name__ == "__main__":
    bot.remove_webhook()
    bot.set_webhook(url=WEBHOOK_URL)
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))
