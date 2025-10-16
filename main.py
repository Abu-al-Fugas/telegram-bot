import os
import time
from datetime import datetime
from flask import Flask, request
import telebot
from telebot.types import (
    InlineKeyboardMarkup, InlineKeyboardButton,
    InputMediaPhoto, InputMediaVideo, InputMediaDocument, BotCommand
)

# ========== НАСТРОЙКИ ==========
TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
ARCHIVE_CHAT_ID = os.environ.get("ARCHIVE_CHAT_ID", "-1003160855229")

bot = telebot.TeleBot(TOKEN)
app = Flask(__name__)

# ========== ГЛОБАЛЫ ==========
# Состояние: ключ = (chat_id, thread_id, user_id)
user_state = {}

# Архив в памяти: object_id -> list of records {'type','file_id','step','archived_at'}
archive_records = {}

# Чек-лист шагов
UPLOAD_STEPS = [
    "Общее фото помещения",                              # обязательный
    "Фото корректора",                                   # обязательный
    "Фото существующей СТМ потребителя",
    "Фото места устанавливаемой СТМ",                   # обязательный
    "Фото (ГРУ)",
    "Фото котлов относительно корректора и устанавливаемой СТМ",
    "Фото газового оборудования",
    "Фото точки подключения 220В",
    "Фото места прокладки кабелей",                      # обязательный
    "Фото входных дверей снаружи",
    "Дополнительные фотографии"
]

# Нельзя пропускать эти шаги
MANDATORY_STEPS = {
    "Общее фото помещения",
    "Фото корректора",
    "Фото места устанавливаемой СТМ",
    "Фото места прокладки кабелей"
}

# ========== HELPERS ==========
def make_key_from_message(message):
    thread_id = getattr(message, "message_thread_id", None)
    return (message.chat.id, thread_id, message.from_user.id)

def make_key_from_callback(call):
    thread_id = getattr(call.message, "message_thread_id", None)
    return (call.message.chat.id, thread_id, call.from_user.id)

def find_session_in_chat(chat_id, thread_id):
    """
    Найти активную сессию в данном чате/теме (если есть) — возвращает ключ сессии.
    """
    for k in user_state.keys():
        if k[0] == chat_id and k[1] == thread_id:
            return k
    return None

def send_message(chat_id, text, reply_markup=None, thread_id=None):
    """
    Отправка сообщения. Возвращаем объект Message.
    """
    try:
        if thread_id is not None:
            # telebot may accept message_thread_id in kwargs for send_message in newer versions
            return bot.send_message(chat_id, text, reply_markup=reply_markup, message_thread_id=thread_id)
        return bot.send_message(chat_id, text, reply_markup=reply_markup)
    except TypeError:
        # fallback if message_thread_id not supported in this telebot version
        return bot.send_message(chat_id, text, reply_markup=reply_markup)

def delete_message_safe(chat_id, message_id):
    """
    Удаление сообщения с защитой от ошибок. Telegram API для удаления требует chat_id и message_id.
    """
    try:
        bot.delete_message(chat_id, message_id)
    except Exception:
        # Игнорируем ошибки удаления (например, недостаточно прав или сообщение уже удалено)
        pass

def send_media_group_safe(chat_id, media, thread_id=None):
    """
    Отправка медиагруппы, разбиваем по 10 элементов. Пытаемся передать message_thread_id, если возможно.
    """
    for i in range(0, len(media), 10):
        chunk = media[i:i+10]
        try:
            if thread_id is not None:
                bot.send_media_group(chat_id, chunk, message_thread_id=thread_id)
            else:
                bot.send_media_group(chat_id, chunk)
            time.sleep(0.25)
        except Exception:
            # как запас — попытаемся без message_thread_id
            try:
                bot.send_media_group(chat_id, chunk)
                time.sleep(0.25)
            except Exception:
                pass

def register_commands_global():
    """
    Регистрируем команды глобально, чтобы меню '/' отображалось и в группах/темах.
    """
    commands = [
        BotCommand("start", "Показать меню"),
        BotCommand("photo", "Загрузить файлы (чек-лист)"),
        BotCommand("download", "Скачать файлы объекта"),
        BotCommand("result", "Список обработанных объектов")
    ]
    try:
        bot.set_my_commands(commands)
    except Exception:
        pass

# ========== КЛАВИАТУРЫ ==========
def upload_inline_keyboard(allow_next=True):
    kb = InlineKeyboardMarkup(row_width=3)
    buttons = [InlineKeyboardButton("✅ OK", callback_data="upload_ok")]
    if allow_next:
        buttons.append(InlineKeyboardButton("➡️ Next", callback_data="upload_next"))
    buttons.append(InlineKeyboardButton("❌ Cancel", callback_data="upload_cancel"))
    kb.add(*buttons)
    return kb

def main_inline_keyboard():
    kb = InlineKeyboardMarkup(row_width=2)
    kb.add(
        InlineKeyboardButton("ℹ️ Info", callback_data="cmd_info"),
        InlineKeyboardButton("📸 Photo", callback_data="cmd_photo"),
        InlineKeyboardButton("⬇️ Download", callback_data="cmd_download"),
        InlineKeyboardButton("📋 Result", callback_data="cmd_result")
    )
    return kb

# ========== КОМАНДЫ ==========
@bot.message_handler(commands=['start'])
def cmd_start(message):
    text = "🤖 Бот для управления объектами ИПУГ\n\nВыберите действие или введите команду через `/`."
    send_message(message.chat.id, text, reply_markup=main_inline_keyboard(), thread_id=getattr(message, 'message_thread_id', None))

@bot.message_handler(commands=['photo'])
def cmd_photo(message):
    key = make_key_from_message(message)
    send_message(message.chat.id, "Введите номер объекта для загрузки файлов:", thread_id=getattr(message, 'message_thread_id', None))
    user_state[key] = {'command': 'await_object'}

@bot.message_handler(commands=['result'])
def cmd_result(message):
    if not archive_records:
        send_message(message.chat.id, "📁 Архив пуст.", thread_id=getattr(message, 'message_thread_id', None))
        return
    text = "📁 Обработанные объекты:\n\n"
    for obj_id, recs in archive_records.items():
        cnt = len(recs)
        ts = recs[0].get('archived_at') if recs else ''
        text += f"#{obj_id}: {cnt} файлов, сохранено {ts}\n"
    send_message(message.chat.id, text, thread_id=getattr(message, 'message_thread_id', None))

@bot.message_handler(commands=['download'])
def cmd_download(message):
    key = make_key_from_message(message)
    send_message(message.chat.id, "Введите номер объекта для скачивания файлов:", thread_id=getattr(message, 'message_thread_id', None))
    user_state[key] = {'command': 'await_download_object'}

# ========== ОБРАБОТКА ТЕКСТА ==========
@bot.message_handler(content_types=['text'])
def handle_text(message):
    key = make_key_from_message(message)
    state = user_state.get(key)
    if not state:
        return

    cmd = state.get('command')
    text = message.text.strip()

    if cmd == 'await_object':
        object_id = text
        if not object_id:
            send_message(message.chat.id, "❌ Укажите корректный номер объекта.", thread_id=getattr(message, 'message_thread_id', None))
            return

        steps = [{'name': s, 'files': []} for s in UPLOAD_STEPS]
        user_state[key] = {
            'command': 'upload_steps',
            'object_id': object_id,
            'step_index': 0,
            'steps': steps,
            'chat_id': message.chat.id,
            'thread_id': getattr(message, 'message_thread_id', None),
            'seen_media_groups': set(),
            'control_message_id': None
        }

        # отправляем первый шаг
        current_step = steps[0]
        allow_next = current_step['name'] not in MANDATORY_STEPS
        msg = send_message(message.chat.id, f"📸 Загрузите {current_step['name']}", reply_markup=upload_inline_keyboard(allow_next=allow_next), thread_id=getattr(message, 'message_thread_id', None))
        user_state[key]['control_message_id'] = getattr(msg, 'message_id', None)
        return

    if cmd == 'await_download_object':
        object_id = text
        if not object_id:
            send_message(message.chat.id, "❌ Укажите корректный номер объекта.", thread_id=getattr(message, 'message_thread_id', None))
            user_state.pop(key, None)
            return
        # отправляем файлы
        send_object_files_to_user(object_id, message.chat.id, thread_id=getattr(message, 'message_thread_id', None))
        user_state.pop(key, None)
        return

# ========== ОБРАБОТКА ФАЙЛОВ ==========
@bot.message_handler(content_types=['photo', 'video', 'document'])
def handle_files(message):
    """
    - Сохраняет файлы в текущем шаге.
    - Не спамит "файл сохранён".
    - Показывает инлайн-клавиатуру один раз для медиагруппы или для одиночного сообщения.
    """
    key = make_key_from_message(message)
    state = user_state.get(key)
    if not state or state.get('command') != 'upload_steps':
        return

    step = state['steps'][state['step_index']]

    # определим file_id и тип
    if message.photo:
        file_id = message.photo[-1].file_id
        ftype = 'photo'
    elif message.video:
        file_id = message.video.file_id
        ftype = 'video'
    elif message.document:
        file_id = message.document.file_id
        ftype = 'document'
    else:
        return

    # добавляем в текущий шаг
    step['files'].append({'type': ftype, 'file_id': file_id, 'step': step['name']})

    # решаем, показывать ли клавиатуру (медиагруппа единожды)
    mgid = getattr(message, 'media_group_id', None)
    seen = state.get('seen_media_groups', set())
    should_show = False
    if mgid:
        if mgid not in seen:
            should_show = True
            seen.add(mgid)
            state['seen_media_groups'] = seen
    else:
        # одиночный файл — показываем клавиатуру
        should_show = True

    if should_show:
        # удаляем предыдущую контрольную карточку, чтобы не накапливать сообщения
        prev_mid = state.get('control_message_id')
        if prev_mid:
            delete_message_safe(state['chat_id'], prev_mid)
            state['control_message_id'] = None

        allow_next = step['name'] not in MANDATORY_STEPS
        msg = send_message(state['chat_id'], f"📸 Шаг: {step['name']}\nВыберите действие:", reply_markup=upload_inline_keyboard(allow_next=allow_next), thread_id=state.get('thread_id'))
        state['control_message_id'] = getattr(msg, 'message_id', None)

# ========== CALLBACK ОБРАБОТКА (cmd_ и upload_) ==========
@bot.callback_query_handler(func=lambda c: c.data and c.data.startswith("cmd_"))
def handle_cmd_callback(call):
    data = call.data

    if data == "cmd_photo":
        send_message(call.message.chat.id, "Введите номер объекта для загрузки файлов:", thread_id=getattr(call.message, 'message_thread_id', None))
        key = (call.message.chat.id, getattr(call.message, 'message_thread_id', None), call.from_user.id)
        user_state[key] = {'command': 'await_object'}
        bot.answer_callback_query(call.id, "Введите номер объекта")
        return

    if data == "cmd_info":
        bot.answer_callback_query(call.id, "Информация (заглушка)")
        send_message(call.message.chat.id, "ℹ️ Информация об объекте: (пока заглушка)", thread_id=getattr(call.message,'message_thread_id',None))
        return

    if data == "cmd_download":
        send_message(call.message.chat.id, "Введите номер объекта для скачивания файлов:", thread_id=getattr(call.message, 'message_thread_id', None))
        key = (call.message.chat.id, getattr(call.message, 'message_thread_id', None), call.from_user.id)
        user_state[key] = {'command': 'await_download_object'}
        bot.answer_callback_query(call.id, "Введите номер объекта")
        return

    if data == "cmd_result":
        if not archive_records:
            bot.answer_callback_query(call.id, "Архив пуст")
            send_message(call.message.chat.id, "📁 Архив пуст.", thread_id=getattr(call.message,'message_thread_id',None))
            return
        text = "📁 Обработанные объекты:\n\n"
        for obj_id, recs in archive_records.items():
            cnt = len(recs)
            ts = recs[0].get('archived_at') if recs else ''
            text += f"#{obj_id}: {cnt} файлов, сохранено {ts}\n"
        bot.answer_callback_query(call.id, "Список объектов")
        send_message(call.message.chat.id, text, thread_id=getattr(call.message,'message_thread_id',None))
        return

    bot.answer_callback_query(call.id, "Неизвестная команда")

@bot.callback_query_handler(func=lambda c: c.data and c.data.startswith("upload_"))
def handle_upload_callback(call):
    # сначала пробуем получить сессию владельца по кнопке (точный ключ)
    key = make_key_from_callback(call)
    state = user_state.get(key)

    if not state:
        # если нет — возможно нажал не владелец; проверим, есть ли активная сессия в chat/thread
        owner_key = find_session_in_chat(call.message.chat.id, getattr(call.message, 'message_thread_id', None))
        if owner_key:
            bot.answer_callback_query(call.id, "Это не ваша сессия.")
            return
        bot.answer_callback_query(call.id, "Нет активной загрузки.")
        return

    action = call.data  # upload_ok / upload_next / upload_cancel

    # удаляем контрольное сообщение после нажатия (чтобы не оставлять старую клавиатуру)
    ctrl_mid = state.get('control_message_id')
    if ctrl_mid:
        delete_message_safe(state['chat_id'], ctrl_mid)
        state['control_message_id'] = None

    if action == "upload_cancel":
        obj = state.get('object_id', '')
        user_state.pop(key, None)
        # уведомим чат
        send_message(call.message.chat.id, f"❌ Загрузка объекта {obj} отменена", thread_id=getattr(call.message, 'message_thread_id', None))
        bot.answer_callback_query(call.id, "Загрузка отменена")
        return

    if action == "upload_ok":
        advance_step(key)
        bot.answer_callback_query(call.id, "Шаг завершён")
        return

    if action == "upload_next":
        # проверяем обязательный шаг
        step = state['steps'][state['step_index']]
        if step['name'] in MANDATORY_STEPS:
            bot.answer_callback_query(call.id, "Этот шаг обязательный и не может быть пропущен")
            return
        advance_step(key, skip=True)
        bot.answer_callback_query(call.id, "Шаг пропущен")
        return

    bot.answer_callback_query(call.id, "Неизвестное действие")

# ========== ПРОГРЕСС И АРХИВ ==========
def advance_step(key, skip=False):
    state = user_state.get(key)
    if not state:
        return

    # продвигаем шаг
    state['step_index'] += 1
    # очищаем seen_media_groups для следующего шага
    state['seen_media_groups'] = set()

    # если завершили все шаги
    if state['step_index'] >= len(state['steps']):
        object_id = state.get('object_id', '')
        all_steps = state.get('steps', [])
        save_to_archive(object_id, all_steps)
        # отчет пользователю
        report = f"✅ Загрузка завершена для объекта #{object_id}\n\n"
        for i, s in enumerate(all_steps, 1):
            report += f"{i}. {s['name']}: {len(s['files'])} файлов\n"
        send_message(state['chat_id'], report, thread_id=state.get('thread_id'))
        # удаляем сессию
        user_state.pop(key, None)
        return

    # отправляем сообщение о следующем шаге
    next_step = state['steps'][state['step_index']]
    allow_next = next_step['name'] not in MANDATORY_STEPS

    # удаляем предыдущую контрольную карточку (если осталась)
    prev_mid = state.get('control_message_id')
    if prev_mid:
        delete_message_safe(state['chat_id'], prev_mid)
        state['control_message_id'] = None

    msg = send_message(state['chat_id'], f"📸 Загрузите {next_step['name']}", reply_markup=upload_inline_keyboard(allow_next=allow_next), thread_id=state.get('thread_id'))
    state['control_message_id'] = getattr(msg, 'message_id', None)

def save_to_archive(object_id, all_steps):
    """
    Сохранение в archive_records и отправка в ARCHIVE_CHAT_ID:
    - Фото/видео — как медиагруппы (по 10)
    - Документы — отдельно
    """
    try:
        flat = []
        for s in all_steps:
            for f in s['files']:
                flat.append({'type': f['type'], 'file_id': f['file_id'], 'step': s['name'], 'archived_at': datetime.now().strftime('%d.%m.%Y %H:%M')})

        # сохраняем в память
        archive_records.setdefault(object_id, [])
        archive_records[object_id].extend(flat)

        if not flat:
            return True

        # отправляем в архив-чат
        send_message(ARCHIVE_CHAT_ID, f"💾 ОБЪЕКТ #{object_id}\n📁 {len(flat)} файлов\n🕒 {datetime.now().strftime('%d.%m.%Y %H:%M')}")

        # аккумулируем медиа (фото/видео) и отправляем батчами по 10
        media_batch = []
        for item in flat:
            if item['type'] == 'photo':
                media_batch.append(InputMediaPhoto(item['file_id']))
            elif item['type'] == 'video':
                media_batch.append(InputMediaVideo(item['file_id']))
            else:
                # документ: сначала отправим накопленный медиа-батч
                if media_batch:
                    send_media_group_safe(ARCHIVE_CHAT_ID, media_batch)
                    media_batch = []
                # затем документ
                try:
                    bot.send_document(ARCHIVE_CHAT_ID, item['file_id'])
                    time.sleep(0.15)
                except Exception:
                    pass

        # отправить остаток медиа
        if media_batch:
            send_media_group_safe(ARCHIVE_CHAT_ID, media_batch)
        return True
    except Exception as e:
        print("[save_to_archive] Ошибка:", e)
        return False

# ========== ОТПРАВКА ФАЙЛОВ ПОЛЬЗОВАТЕЛЮ (DOWNLOAD) ==========
def send_object_files_to_user(object_id, dest_chat_id, thread_id=None):
    recs = archive_records.get(object_id)
    if not recs:
        send_message(dest_chat_id, f"❌ Объект #{object_id} не найден в архиве.", thread_id=thread_id)
        return

    media_batch = []
    for item in recs:
        if item['type'] == 'photo':
            media_batch.append(InputMediaPhoto(item['file_id']))
        elif item['type'] == 'video':
            media_batch.append(InputMediaVideo(item['file_id']))
        else:
            # отправляем накопленный медиа батч
            if media_batch:
                send_media_group_safe(dest_chat_id, media_batch, thread_id=thread_id)
                media_batch = []
            try:
                if thread_id is not None:
                    bot.send_document(dest_chat_id, item['file_id'], message_thread_id=thread_id)
                else:
                    bot.send_document(dest_chat_id, item['file_id'])
                time.sleep(0.12)
            except Exception:
                pass

    if media_batch:
        send_media_group_safe(dest_chat_id, media_batch, thread_id=thread_id)

    send_message(dest_chat_id, f"✅ Отправлены файлы объекта #{object_id}", thread_id=thread_id)

# ========== WEBHOOK ==========
@app.route('/' + TOKEN, methods=['POST'])
def receive_update():
    update = telebot.types.Update.de_json(request.data.decode('utf-8'))
    bot.process_new_updates([update])
    return "OK", 200

@app.route('/')
def index():
    return "🤖 Бот работает", 200

# ========== RUN ==========
if __name__ == "__main__":
    print("🚀 Бот запускается...")
    register_commands_global = register_commands_global  # alias
    try:
        register_commands_global()
    except Exception:
        pass
    bot.remove_webhook()
    WEBHOOK_URL = f"https://telegram-bot-b6pn.onrender.com/{TOKEN}"
    try:
        bot.set_webhook(url=WEBHOOK_URL)
    except Exception:
        pass
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))
