import os
import time
from datetime import datetime
from flask import Flask, request
import telebot
from telebot.types import (
    InlineKeyboardMarkup, InlineKeyboardButton,
    InputMediaPhoto, InputMediaVideo, InputMediaDocument
)

# ========== НАСТРОЙКИ ==========
TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
ARCHIVE_CHAT_ID = os.environ.get("ARCHIVE_CHAT_ID", "-1003160855229")

bot = telebot.TeleBot(TOKEN, parse_mode=None)
app = Flask(__name__)

# Состояние: ключ = (chat_id, thread_id, user_id)
user_state = {}

# Архив: object_id -> list of file dicts {'type','file_id','step'}
archive_records = {}

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

# Шаги, которые НЕЛЬЗЯ пропустить
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

def find_session_by_message(call):
    """
    Если кто-то нажал кнопку не владея сессией, попробуем найти активную сессию
    в том же чате/теме и вернуть её ключ (owner), чтобы ответить "Это не ваша сессия".
    """
    chat_id = call.message.chat.id
    thread_id = getattr(call.message, "message_thread_id", None)
    for k in user_state.keys():
        if k[0] == chat_id and k[1] == thread_id:
            return k
    return None

def send_message(chat_id, text, reply_markup=None, thread_id=None):
    return bot.send_message(chat_id, text, reply_markup=reply_markup, message_thread_id=thread_id)

def send_file(chat_id, file_type, file_id, caption=None, thread_id=None):
    if file_type == "photo":
        return bot.send_photo(chat_id, file_id, caption=caption, message_thread_id=thread_id)
    elif file_type == "document":
        return bot.send_document(chat_id, file_id, caption=caption, message_thread_id=thread_id)
    elif file_type == "video":
        return bot.send_video(chat_id, file_id, caption=caption, message_thread_id=thread_id)
    return None

def register_bot_commands():
    """
    Регистрируем команды, чтобы в поле ввода появлялся '/' и команды
    """
    commands = [
        telebot.types.BotCommand("start", "Запустить бота / показать меню"),
        telebot.types.BotCommand("photo", "Загрузить файлы (чек-лист)"),
        telebot.types.BotCommand("download", "Скачать файлы объекта"),
        telebot.types.BotCommand("result", "Список обработанных объектов"),
        telebot.types.BotCommand("help", "Помощь")
    ]
    try:
        bot.set_my_commands(commands)
    except Exception:
        pass

# ========== INLINE КЛАВИАТУРЫ ==========
def upload_inline_keyboard(allow_next=True):
    kb = InlineKeyboardMarkup(row_width=3)
    kb_buttons = []
    kb_buttons.append(InlineKeyboardButton("✅ OK", callback_data="upload_ok"))
    if allow_next:
        kb_buttons.append(InlineKeyboardButton("➡️ Next", callback_data="upload_next"))
    kb_buttons.append(InlineKeyboardButton("❌ Cancel", callback_data="upload_cancel"))
    kb.add(*kb_buttons)
    return kb

def main_inline_keyboard():
    kb = InlineKeyboardMarkup(row_width=2)
    kb.add(
        InlineKeyboardButton("ℹ️ Info", callback_data="cmd_info"),
        InlineKeyboardButton("📸 Photo", callback_data="cmd_photo"),
        InlineKeyboardButton("⬇️ Download", callback_data="cmd_download"),
        InlineKeyboardButton("📋 Result", callback_data="cmd_result"),
        InlineKeyboardButton("❓ Help", callback_data="cmd_help")
    )
    return kb

# ========== КОМАНДЫ ==========
@bot.message_handler(commands=['start', 'help'])
def start_message(message):
    text = (
        "🤖 Бот для управления объектами ИПУГ\n\n"
        "Нажмите кнопку для действия или наберите команду через /"
    )
    send_message(message.chat.id, text, reply_markup=main_inline_keyboard(), thread_id=message.message_thread_id)

@bot.message_handler(commands=['photo'])
def start_upload(message):
    key = make_key_from_message(message)
    send_message(message.chat.id, "Введите номер объекта для загрузки файлов:", thread_id=message.message_thread_id)
    user_state[key] = {'command': 'await_object'}

@bot.message_handler(commands=['result'])
def cmd_result(message):
    # Вывести список архивированных объектов (in-memory)
    if not archive_records:
        send_message(message.chat.id, "📁 Архив пуст.", thread_id=message.message_thread_id)
        return
    text = "📁 Обработанные объекты:\n\n"
    for obj_id, rec in archive_records.items():
        cnt = len(rec)
        ts = rec[0].get('archived_at') if rec else ''
        text += f"#{obj_id}: {cnt} файлов, сохранено {ts}\n"
    send_message(message.chat.id, text, thread_id=message.message_thread_id)

@bot.message_handler(commands=['download'])
def cmd_download(message):
    key = make_key_from_message(message)
    send_message(message.chat.id, "Введите номер объекта для скачивания файлов:", thread_id=message.message_thread_id)
    user_state[key] = {'command': 'await_download_object'}

# ========== ОБРАБОТКА ТЕКСТА ==========
@bot.message_handler(content_types=['text'])
def handle_text(message):
    key = make_key_from_message(message)
    state = user_state.get(key)
    if not state:
        return

    cmd = state.get('command')

    if cmd == 'await_object':
        object_id = message.text.strip()
        if not object_id:
            send_message(message.chat.id, "❌ Укажите корректный номер объекта.", thread_id=message.message_thread_id)
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
        # отправляем первый шаг с клавиатурой (Next — выключен если обязательный)
        step = steps[0]
        allow_next = (step['name'] not in MANDATORY_STEPS)
        msg = send_message(
            message.chat.id,
            f"📸 Загрузите {step['name']}",
            reply_markup=upload_inline_keyboard(allow_next=allow_next),
            thread_id=getattr(message, 'message_thread_id', None)
        )
        user_state[key]['control_message_id'] = getattr(msg, 'message_id', None)
        return

    if cmd == 'await_download_object':
        object_id = message.text.strip()
        if not object_id:
            send_message(message.chat.id, "❌ Укажите корректный номер объекта для скачивания.", thread_id=message.message_thread_id)
            user_state.pop(key, None)
            return
        # Выполнить отправку архива пользователю
        send_object_files_to_user(object_id, message.chat.id, thread_id=getattr(message, 'message_thread_id', None))
        user_state.pop(key, None)
        return

# ========== ОБРАБОТКА ФАЙЛОВ ==========
@bot.message_handler(content_types=['photo', 'document', 'video'])
def handle_files(message):
    """
    Сохраняем файлы в состоянии шага, не спамим "файл сохранен".
    Показываем инлайн-клавиатуру:
     - при одиночном файле сразу
     - при медиагруппе — только один раз (по media_group_id)
    """
    key = make_key_from_message(message)
    state = user_state.get(key)
    if not state or state.get('command') != 'upload_steps':
        return

    step = state['steps'][state['step_index']]

    # определим тип и file_id
    if message.photo:
        fid = message.photo[-1].file_id
        ftype = 'photo'
    elif message.video:
        fid = message.video.file_id
        ftype = 'video'
    elif message.document:
        fid = message.document.file_id
        ftype = 'document'
    else:
        return

    # добавляем в шаг
    step['files'].append({'type': ftype, 'file_id': fid, 'step': step['name']})

    # помечаем в глобальный архив временно (чтобы не потерять при crash) — добавим только при финальном сохранении
    # показываем клавиатуру только один раз для медиагруппы
    mgid = getattr(message, 'media_group_id', None)
    seen = state.get('seen_media_groups', set())
    should_send = False
    if mgid:
        if mgid not in seen:
            should_send = True
            seen.add(mgid)
            state['seen_media_groups'] = seen
    else:
        # одиночный файл — отправляем клавиатуру (если ещё не показали control_message_id для этого шага)
        # если control_message_id существует и соответствует текущему шагу — не шлем повторно
        should_send = True

    if should_send:
        # Удаляем предыдущую контрольную карточку (если есть) — чтобы не накапливать сообщения
        prev_mid = state.get('control_message_id')
        if prev_mid:
            try:
                bot.delete_message(chat_id=state['chat_id'], message_id=prev_mid, message_thread_id=state.get('thread_id'))
            except Exception:
                pass

        allow_next = (step['name'] not in MANDATORY_STEPS)
        msg = send_message(
            state['chat_id'],
            f"📸 Шаг: {step['name']}\nВыберите действие:",
            reply_markup=upload_inline_keyboard(allow_next=allow_next),
            thread_id=state.get('thread_id')
        )
        state['control_message_id'] = getattr(msg, 'message_id', None)

# ========== CALLBACK HANDLERS ==========
@bot.callback_query_handler(func=lambda c: c.data and c.data.startswith("cmd_"))
def handle_cmd_callback(call):
    data = call.data
    # простая обработка команд меню
    if data == "cmd_photo":
        # эквивалент /photo
        send_message(call.message.chat.id, "Введите номер объекта для загрузки файлов:", thread_id=getattr(call.message, 'message_thread_id', None))
        # сохраняем ожидающее состояние
        key = (call.message.chat.id, getattr(call.message, 'message_thread_id', None), call.from_user.id)
        user_state[key] = {'command': 'await_object'}
        bot.answer_callback_query(call.id, "Введите номер объекта")
        return

    if data == "cmd_info":
        bot.answer_callback_query(call.id, "Info: заглушка")
        send_message(call.message.chat.id, "ℹ️ Информация об объекте: (пока заглушка)", thread_id=getattr(call.message,'message_thread_id',None))
        return

    if data == "cmd_download":
        # эквивалент /download
        send_message(call.message.chat.id, "Введите номер объекта для скачивания файлов:", thread_id=getattr(call.message, 'message_thread_id', None))
        key = (call.message.chat.id, getattr(call.message, 'message_thread_id', None), call.from_user.id)
        user_state[key] = {'command': 'await_download_object'}
        bot.answer_callback_query(call.id, "Введите номер объекта")
        return

    if data == "cmd_result":
        # эквивалент /result
        if not archive_records:
            bot.answer_callback_query(call.id, "Архив пуст")
            send_message(call.message.chat.id, "📁 Архив пуст.", thread_id=getattr(call.message,'message_thread_id',None))
            return
        text = "📁 Обработанные объекты:\n\n"
        for obj_id, rec in archive_records.items():
            cnt = len(rec)
            ts = rec[0].get('archived_at') if rec else ''
            text += f"#{obj_id}: {cnt} файлов, сохранено {ts}\n"
        bot.answer_callback_query(call.id, "Список объектов")
        send_message(call.message.chat.id, text, thread_id=getattr(call.message,'message_thread_id',None))
        return

    if data == "cmd_help":
        bot.answer_callback_query(call.id, "Помощь")
        send_message(call.message.chat.id, "❓ Помощь: используйте /photo для загрузки файлов или кнопки меню.", thread_id=getattr(call.message,'message_thread_id',None))
        return

    bot.answer_callback_query(call.id, "Неизвестная команда")

@bot.callback_query_handler(func=lambda c: c.data and c.data.startswith("upload_"))
def handle_upload_callback(call):
    # Найдём сессию: сначала — точный ключ по нажатому пользователю
    key = make_key_from_callback(call)
    state = user_state.get(key)

    if not state:
        # возможно нажал не владелец — посмотрим, есть ли сессия в этом chat/thread
        owner_key = find_session_by_message(call)
        if owner_key:
            # есть сессия другого пользователя
            bot.answer_callback_query(call.id, "Это не ваша сессия.")
            return
        # вообще нет сессии
        bot.answer_callback_query(call.id, "Нет активной загрузки или сессия истекла.")
        return

    # Теперь key владелец — обрабатываем действие
    action = call.data  # upload_ok / upload_next / upload_cancel

    # удаляем контрольную кнопку из старого сообщения (чтобы нельзя было нажать повторно)
    ctrl_mid = state.get('control_message_id')
    if ctrl_mid:
        try:
            bot.delete_message(chat_id=state['chat_id'], message_id=ctrl_mid, message_thread_id=state.get('thread_id'))
        except Exception:
            pass

    if action == "upload_cancel":
        obj = state.get('object_id', '')
        user_state.pop(key, None)
        # оповестим чат
        try:
            bot.edit_message_text(f"❌ Загрузка объекта {obj} отменена", chat_id=call.message.chat.id, message_id=call.message.message_id, message_thread_id=getattr(call.message, 'message_thread_id', None))
        except Exception:
            send_message(call.message.chat.id, f"❌ Загрузка объекта {obj} отменена", thread_id=getattr(call.message, 'message_thread_id', None))
        bot.answer_callback_query(call.id, "Загрузка отменена")
        return

    if action == "upload_ok":
        # завершаем текущий шаг (оставляя загруженные файлы)
        advance_step(key)
        bot.answer_callback_query(call.id, "Шаг отмечен как завершён")
        return

    if action == "upload_next":
        # проверим текущий шаг: если обязательный — запретим
        step = state['steps'][state['step_index']]
        if step['name'] in MANDATORY_STEPS:
            bot.answer_callback_query(call.id, "Этот шаг обязательный и не может быть пропущен")
            return
        # пропустить
        advance_step(key, skip=True)
        bot.answer_callback_query(call.id, "Шаг пропущен")
        return

    bot.answer_callback_query(call.id, "Неизвестное действие")

# ========== ПРОГРЕСС и АРХИВ ==========
def advance_step(key, skip=False):
    """
    Продвинуть шаг, удалить предыдущие контрольные сообщения,
    при завершении — сохранить в архив (и отправить в ARCHIVE_CHAT_ID медиагруппами).
    """
    state = user_state.get(key)
    if not state:
        return

    # продвинуть
    state['step_index'] += 1

    # очистим seen_media_groups для следующего шага
    state['seen_media_groups'] = set()

    # если вышли за пределы шагов — архивируем и очищаем
    if state['step_index'] >= len(state['steps']):
        object_id = state.get('object_id', '')
        all_steps = state.get('steps', [])
        # архивируем: добавляем в archive_records и отправляем медиагруппами
        save_to_archive(object_id, all_steps)
        # формируем отчет и шлём пользователю
        report = f"✅ Загрузка завершена для объекта #{object_id}\n\n"
        for i, s in enumerate(all_steps, 1):
            report += f"{i}. {s['name']}: {len(s['files'])} файлов\n"
        send_message(state['chat_id'], report, thread_id=state.get('thread_id'))
        # удаляем состояние
        user_state.pop(key, None)
        return

    # иначе — отправляем сообщение о следующем шаге с клавиатурой
    next_step = state['steps'][state['step_index']]
    allow_next = (next_step['name'] not in MANDATORY_STEPS)

    # перед отправкой удалим предыдущий контрольный (если ещё остался)
    prev_mid = state.get('control_message_id')
    if prev_mid:
        try:
            bot.delete_message(chat_id=state['chat_id'], message_id=prev_mid, message_thread_id=state.get('thread_id'))
        except Exception:
            pass

    msg = send_message(
        state['chat_id'],
        f"📸 Загрузите {next_step['name']}",
        reply_markup=upload_inline_keyboard(allow_next=allow_next),
        thread_id=state.get('thread_id')
    )
    state['control_message_id'] = getattr(msg, 'message_id', None)

def save_to_archive(object_id, all_steps):
    """
    Отправляем файлы в ARCHIVE_CHAT_ID сгруппированными:
    - Фото/видео — как медиагруппы по 10
    - Документы — по одному
    Также сохраняем мета в archive_records для /result и /download
    """
    try:
        # Соберём все файлы в один список и добавим отметку archived_at
        flat = []
        for s in all_steps:
            for f in s['files']:
                rec = {'type': f['type'], 'file_id': f['file_id'], 'step': s['name'], 'archived_at': datetime.now().strftime('%d.%m.%Y %H:%M')}
                flat.append(rec)

        if not flat:
            # пусто — просто записать мета
            archive_records.setdefault(object_id, [])
            return True

        # Сохраняем в память
        archive_records.setdefault(object_id, [])
        archive_records[object_id].extend(flat)

        # Отправляем в архив-чат
        info = f"💾 ОБЪЕКТ #{object_id}\n📁 {len(flat)} файлов\n🕒 {datetime.now().strftime('%d.%m.%Y %H:%M')}"
        send_message(ARCHIVE_CHAT_ID, info)

        # Группируем фото/video в медиагруппы (по 10)
        media_batch = []
        for item in flat:
            if item['type'] == 'photo':
                media_batch.append(InputMediaPhoto(item['file_id']))
            elif item['type'] == 'video':
                media_batch.append(InputMediaVideo(item['file_id']))
            else:
                # документ: сначала отправим текущ накопившийся медиабатч, затем документ отдельно
                if media_batch:
                    try:
                        # Telegram ограничивает 10 items per media_group
                        # разбиваем на куски по 10
                        for i in range(0, len(media_batch), 10):
                            bot.send_media_group(ARCHIVE_CHAT_ID, media_batch[i:i+10])
                            time.sleep(0.3)
                    except Exception:
                        pass
                    media_batch = []
                # отправляем документ отдельно
                try:
                    bot.send_document(ARCHIVE_CHAT_ID, item['file_id'])
                    time.sleep(0.2)
                except Exception:
                    pass

        # в конце отправим оставшийся медиа батч
        if media_batch:
            try:
                for i in range(0, len(media_batch), 10):
                    bot.send_media_group(ARCHIVE_CHAT_ID, media_batch[i:i+10])
                    time.sleep(0.3)
            except Exception:
                pass

        return True
    except Exception as e:
        print(f"[save_to_archive] Ошибка: {e}")
        return False

# ========== ФУНКЦИИ ДЛЯ ЗАПРОСА ФАЙЛОВ (DOWNLOAD) ==========
def send_object_files_to_user(object_id, dest_chat_id, thread_id=None):
    recs = archive_records.get(object_id)
    if not recs:
        send_message(dest_chat_id, f"❌ Объект #{object_id} не найден в архиве.", thread_id=thread_id)
        return

    # Группируем по типу: отправляем фото/video как медиа группы, документы отдельно
    media_batch = []
    for item in recs:
        if item['type'] == 'photo':
            media_batch.append(InputMediaPhoto(item['file_id']))
        elif item['type'] == 'video':
            media_batch.append(InputMediaVideo(item['file_id']))
        else:
            # отправляем накопленный медиа батч, затем документ
            if media_batch:
                try:
                    for i in range(0, len(media_batch), 10):
                        bot.send_media_group(dest_chat_id, media_batch[i:i+10], message_thread_id=thread_id)
                        time.sleep(0.2)
                except Exception:
                    pass
                media_batch = []
            try:
                bot.send_document(dest_chat_id, item['file_id'], message_thread_id=thread_id)
                time.sleep(0.15)
            except Exception:
                pass

    if media_batch:
        try:
            for i in range(0, len(media_batch), 10):
                bot.send_media_group(dest_chat_id, media_batch[i:i+10], message_thread_id=thread_id)
                time.sleep(0.2)
        except Exception:
            pass

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

# ========== ЗАПУСК ==========
if __name__ == "__main__":
    print("🚀 Бот запускается...")
    register_bot_commands()
    bot.remove_webhook()
    WEBHOOK_URL = f"https://telegram-bot-b6pn.onrender.com/{TOKEN}"
    bot.set_webhook(url=WEBHOOK_URL)
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))
