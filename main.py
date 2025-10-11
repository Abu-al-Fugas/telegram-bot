import os
import telebot
from flask import Flask, request
import pandas as pd

TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
bot = telebot.TeleBot(TOKEN)
app = Flask(__name__)

# Загрузка таблицы объектов при старте
OBJECTS_FILE = "objects.xlsx"
df_objects = pd.read_excel(OBJECTS_FILE)

# Хранение состояния ожидания номера объекта после команды /objects для каждого пользователя
user_waiting_for_objects = set()

@bot.message_handler(commands=['start'])
def start_message(message):
    bot.reply_to(message, "Привет! 👋 Я бот для работы с объектами. Используй команду /objects чтобы получить информацию об объектах.")

@bot.message_handler(commands=['help'])
def help_message(message):
    bot.reply_to(message, ("Команды:\n"
                          "/start - приветствие\n"
                          "/help - помощь\n"
                          "/objects - получить информацию по объектам (написать номера через запятую)"))

@bot.message_handler(commands=['objects'])
def objects_command(message):
    user_waiting_for_objects.add(message.from_user.id)
    bot.reply_to(message, "Напиши номера объектов через запятую, например: 5,7,10")

@bot.message_handler(func=lambda message: message.from_user.id in user_waiting_for_objects)
def send_objects_info(message):
    user_waiting_for_objects.discard(message.from_user.id)
    text = message.text.strip()
    # Ожидаем номера через запятую, очищаем
    try:
        nums = [int(x.strip()) for x in text.split(",") if x.strip().isdigit()]
    except Exception:
        bot.reply_to(message, "Пожалуйста, отправь номера объектов корректно, через запятую.")
        return
    
    if not nums:
        bot.reply_to(message, "Не найдено корректных номеров объектов, попробуй снова командой /objects.")
        return
    
    for num in nums:
        obj_row = df_objects[df_objects.iloc[:,0] == num]
        if obj_row.empty:
            bot.send_message(message.chat.id, f"Объект с номером {num} не найден.")
            continue
        # Предположим первый столбец - номер, второй - наименование, третий - адрес
        obj_name = obj_row.iloc[0,1]
        obj_address = obj_row.iloc[0,2]
        bot.send_message(message.chat.id, f"Объект №{num}\nНаименование: {obj_name}\nАдрес: {obj_address}")

@app.route('/' + TOKEN, methods=['POST'])
def receive_update():
    json_str = request.get_data().decode('utf-8')
    update = telebot.types.Update.de_json(json_str)
    bot.process_new_updates([update])
    return "OK", 200

@app.route('/')
def index():
    return "Бот работает! ✅", 200

if __name__ == "__main__":
    bot.remove_webhook()
    WEBHOOK_URL = f"https://telegram-bot-b6pn.onrender.com/{TOKEN}"
    bot.set_webhook(url=WEBHOOK_URL)
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))
