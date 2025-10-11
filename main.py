import os
import telebot
from flask import Flask, request

TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
bot = telebot.TeleBot(TOKEN)
app = Flask(__name__)

@bot.message_handler(commands=['start', 'getid'])
def get_chat_id(message):
    chat_info = f"""
💬 ИНФОРМАЦИЯ О ЧАТЕ:

📝 Название: {message.chat.title or 'Личные сообщения'}
🔧 Тип: {message.chat.type}
🆔 Chat ID: `{message.chat.id}`
🧵 Topic ID: {message.message_thread_id or 'Нет'}

📋 Для архивной группы используйте:
ARCHIVE_CHAT_ID = {message.chat.id}
    """
    bot.reply_to(message, chat_info, parse_mode='Markdown')

# Обработка всех сообщений
@bot.message_handler(func=lambda message: True)
def echo_all(message):
    bot.reply_to(message, "Напишите /getid чтобы получить ID чата")

# Webhook для Render
@app.route('/' + TOKEN, methods=['POST'])
def receive_update():
    json_str = request.get_data().decode('utf-8')
    update = telebot.types.Update.de_json(json_str)
    bot.process_new_updates([update])
    return "OK", 200

@app.route('/')
def index():
    return "Бот для получения ID чата работает! ✅", 200

if __name__ == "__main__":
    bot.remove_webhook()
    WEBHOOK_URL = f"https://telegram-bot-b6pn.onrender.com/{TOKEN}"  # Замените на ваш URL
    bot.set_webhook(url=WEBHOOK_URL)
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))

