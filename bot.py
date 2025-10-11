import os
import telebot
from flask import Flask, request

# Берём токен из переменной окружения
TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
bot = telebot.TeleBot(TOKEN)

# Создаём Flask-сервер
app = Flask(__name__)

# === Команды бота ===
@bot.message_handler(commands=['start'])
def start_message(message):
    bot.reply_to(message, "Привет! 👋 Я бот, работаю через Render!")

@bot.message_handler(commands=['help'])
def help_message(message):
    bot.reply_to(message, "Я умею отвечать на команды /start и /help 😊")

@bot.message_handler(func=lambda message: True)
def echo_all(message):
    bot.reply_to(message, f"Ты написал: {message.text}")

# === Webhook маршруты ===
@app.route('/' + TOKEN, methods=['POST'])
def receive_update():
    json_str = request.get_data().decode('utf-8')
    update = telebot.types.Update.de_json(json_str)
    bot.process_new_updates([update])
    return "OK", 200

@app.route('/')
def index():
    return "Бот работает! ✅", 200

# === Запуск ===
if __name__ == "__main__":
    bot.remove_webhook()
    # ⚠️ Вставь сюда свой URL от Render:
    WEBHOOK_URL = f"https://твоё_имя_сервиса.onrender.com/{TOKEN}"
    bot.set_webhook(url=WEBHOOK_URL)
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))
