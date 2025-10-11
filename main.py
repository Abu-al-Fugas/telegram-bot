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
