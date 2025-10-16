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

# Архив: object_id -> list of records {'type','file_id','step','archived_at','additional'}
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
    for k in user_state.keys():
        if k[0] == chat_id and k[1] == thread_id:
            return k
    return None

def send_message(chat_id, text, reply_markup=None, thread_id=None):
    try:
        if thread_id is not None:
            return bot.send_message(chat_id, text, reply_markup=reply_markup, message_thread_id=thread_id)
        return bot.send_message(chat_id, text, reply_markup=reply_markup)
    except TypeError:
        return bot.send_message(chat_id, text, reply_markup=reply_markup)

def delete_message_safe(chat_id, message_id):
    try:
        bot.delete_message(chat_id, message_id)
    except Exception:
        pass

def send_media_group_safe(chat_id, media, thread_id=None):
    for i in range(0, len(media), 10):
        chunk = media[i:i+10]
        try:
            if thread_id is not None:
                bot.send_media_group(chat_id, chunk, message_thread_id=thread_id)
            else:
                bot.send_media_group(chat_id, chunk)
            time.sleep(0.25)
        except Exception:
            try:
                bot.send_media_group(chat_id, chunk)
                time.sleep(0.25)
            except Exception:
                pass

def register_commands_global():
    commands = [
        BotCommand("start", "Показать меню"),
        BotCommand("photo", "Загрузить файлы (чек-лист)"),
        BotCommand("download", "Скачать файлы объекта"),
        BotCommand("result", "Список обработанных объектов"),
        BotCommand("addphoto", "Добавить фото к существующему объекту")
    ]
    try:
        bot.set_my_commands(commands)
    except Exception:
        pass

# ========== КЛАВИАТУРЫ ==========
def upload_inline_keyboard(allow_next=True, include_header=True, addphoto_mode=False):
    # include_header контролирует формат текста, но клавиатура сама одинакова
    kb = InlineKeyboardMarkup(row_width=3)
    buttons = [InlineKeyboardButton("✅ OK", callback_data="upload_ok")]
    if allow_next:
        buttons.append(InlineKeyboardButton("➡️ Next", callback_data="upload_next"))
    buttons.append(InlineKeyboardButton("❌ Cancel", callback_data="upload_cancel"))
    if addphoto_mode:
        # для режима addphoto управление: Finish и Cancel
        kb = InlineKeyboardMarkup(row_width=2)
        kb.add(InlineKeyboardButton("✅ Finish", callback_data="add_finish"),
               InlineKeyboardButton("❌ Cancel", callback_data="add_cancel"))
        return kb
    kb.add(*buttons)
    return kb

def main_inline_keyboard():
    kb = InlineKeyboardMarkup(row_width=2)
    kb.add(
        InlineKeyboardButton("ℹ️ Info", callback_data="cmd_info"),
        InlineKeyboardButton("📸 Photo", callback_data="cmd_photo"),
        InlineKeyboardButton("⬇️ Download", callback_data="cmd_download"),
        InlineKeyboardButton("📋 Result", callback_data="cmd_result"),
        InlineKeyboardButton("➕ AddPhoto", callback_data="cmd_addphoto")
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

@bot.message_handler(commands=['addphoto'])
def cmd_addphoto(message):
    key = make_key_from_message(message)
    send_message(message.chat.id, "Введите номер объекта, к которому хотите добавить файлы:", thread_id=getattr(message, 'message_thread_id', None))
    user_state[key] = {'command': 'await_addphoto_object'}

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
            'control_message_id': None,
            'step_header_sent': True  # при инициализации показываем заголовок
        }

        current_step = steps[0]
        allow_next = current_step['name'] not in MANDATORY_STEPS
        msg = send_message(message.chat.id, f"📸 Загрузите {current_step['name']}", reply_markup=upload_inline_keyboard(allow_next=allow_next), thread_id=getattr(message, 'message_thread_id', None))
        user_state[key]['control_message_id'] = getattr(msg, 'message_id', None)
        # step_header_sent True означает следующее (после файлов) показать клавиатуру уже без заголовка
        return

    if cmd == 'await_download_object':
        object_id = text
        if not object_id:
            send_message(message.chat.id, "❌ Укажите корректный номер объекта.", thread_id=getattr(message, 'message_thread_id', None))
            user_state.pop(key, None)
            return
        send_object_files_to_user(object_id, message.chat.id, thread_id=getattr(message, 'message_thread_id', None))
        user_state.pop(key, None)
        return

    if cmd == 'await_addphoto_object':
        object_id = text
        if not object_id:
            send_message(message.chat.id, "❌ Укажите корректный номер объекта.", thread_id=getattr(message, 'message_thread_id', None))
            user_state.pop(key, None)
            return
        # Проверим существование объекта; если нет — создаём запись, но пометим
        archive_records.setdefault(object_id, [])
        # Сессия addphoto: собираем файлы в add_files
        user_state[key] = {
            'command': 'addphoto_collect',
            'object_id': object_id,
            'add_files': [],   # list of {'type','file_id'}
            'chat_id': message.chat.id,
            'thread_id': getattr(message, 'message_thread_id', None),
            'control_message_id': None
        }
        # отправим подсказку с клавишами Finish/Cancel
        msg = send_message(message.chat.id, "Теперь отправьте файлы (фото/медиа) — затем нажмите **Finish**.", reply_markup=upload_inline_keyboard(addphoto_mode=True), thread_id=getattr(message, 'message_thread_id', None))
        user_state[key]['control_message_id'] = getattr(msg, 'message_id', None)
        return

# ========== ОБРАБОТКА ФАЙЛОВ ==========
@bot.message_handler(content_types=['photo', 'video', 'document'])
def handle_files(message):
    key = make_key_from_message(message)
    state = user_state.get(key)

    # Если это режим добавления в существующий объект
    if state and state.get('command') == 'addphoto_collect':
        add_files = state['add_files']
        if message.photo:
            fid = message.photo[-1].file_id
            add_files.append({'type': 'photo', 'file_id': fid})
        elif message.video:
            add_files.append({'type': 'video', 'file_id': message.video.file_id})
        elif message.document:
            add_files.append({'type': 'document', 'file_id': message.document.file_id})

        state['add_files'] = add_files
        # для addphoto — показываем одну контрольную кнопку (Finish) только один раз per media_group or single
        mgid = getattr(message, 'media_group_id', None)
        seen = state.get('seen_media_groups', set())
        should_show = False
        if mgid:
            if mgid not in seen:
                should_show = True
                seen.add(mgid)
                state['seen_media_groups'] = seen
        else:
            should_show = True

        if should_show:
            prev_mid = state.get('control_message_id')
            if prev_mid:
                delete_message_safe(state['chat_id'], prev_mid)
                state['control_message_id'] = None
            msg = send_message(state['chat_id'], "Выберите действие:\n\n✅ Finish   ❌ Cancel", reply_markup=upload_inline_keyboard(addphoto_mode=True), thread_id=state.get('thread_id'))
            state['control_message_id'] = getattr(msg, 'message_id', None)
        return

    # Иначе — обычный пошаговый режим
    if not state or state.get('command') != 'upload_steps':
        return

    step = state['steps'][state['step_index']]

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

    step['files'].append({'type': ftype, 'file_id': fid, 'step': step['name']})

    mgid = getattr(message, 'media_group_id', None)
    seen = state.get('seen_media_groups', set())
    should_show = False
    if mgid:
        if mgid not in seen:
            should_show = True
            seen.add(mgid)
            state['seen_media_groups'] = seen
    else:
        should_show = True

    if should_show:
        prev_mid = state.get('control_message_id')
        if prev_mid:
            delete_message_safe(state['chat_id'], prev_mid)
            state['control_message_id'] = None

        # Если header был показан ранее при старте шага, то теперь показываем клавиши БЕЗ заголовка
        # (пользователь хочет, чтобы повторное сообщение было без "📸 Шаг: ...")
        allow_next = step['name'] not in MANDATORY_STEPS
        msg_text = "Выберите действие:\n\n✅ OK"
        if allow_next:
            msg_text += "   ➡️ Next"
        msg_text += "   ❌ Cancel"
        msg = send_message(state['chat_id'], msg_text, reply_markup=upload_inline_keyboard(allow_next=allow_next), thread_id=state.get('thread_id'))
        state['control_message_id'] = getattr(msg, 'message_id', None)
        # пометка что заголовки для текущего шага уже показаны (в следующий раз снова без заголовка)
        state['step_header_sent'] = True

# ========== CALLBACK ОБРАБОТКА ==========
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

    if data == "cmd_addphoto":
        send_message(call.message.chat.id, "Введите номер объекта, к которому хотите добавить файлы:", thread_id=getattr(call.message, 'message_thread_id', None))
        key = (call.message.chat.id, getattr(call.message, 'message_thread_id', None), call.from_user.id)
        user_state[key] = {'command': 'await_addphoto_object'}
        bot.answer_callback_query(call.id, "Введите номер объекта")
        return

    bot.answer_callback_query(call.id, "Неизвестная команда")

@bot.callback_query_handler(func=lambda c: c.data and (c.data.startswith("upload_") or c.data.startswith("add_")))
def handle_upload_callback(call):
    # сначала пробуем найти точную сессию владельца
    key = make_key_from_callback(call)
    state = user_state.get(key)

    if not state:
        owner_key = find_session_in_chat(call.message.chat.id, getattr(call.message, 'message_thread_id', None))
        if owner_key:
            bot.answer_callback_query(call.id, "Это не ваша сессия.")
            return
        bot.answer_callback_query(call.id, "Нет активной сессии.")
        return

    # Если это режим addphoto
    if state.get('command') == 'addphoto_collect' or state.get('command') == 'addphoto_collect' or call.data in ("add_finish", "add_cancel"):
        # handle addphoto callbacks
        if call.data == "add_cancel":
            obj = state.get('object_id', '')
            user_state.pop(key, None)
            send_message(call.message.chat.id, f"❌ Добавление к объекту #{obj} отменено", thread_id=getattr(call.message, 'message_thread_id', None))
            bot.answer_callback_query(call.id, "Отменено")
            return

        if call.data == "add_finish":
            # сохранение add_files в архив_records с пометкой additional=True
            obj = state.get('object_id')
            add_files = state.get('add_files', [])
            if not add_files:
                bot.answer_callback_query(call.id, "Нет добавленных файлов.")
                return
            # добавляем в archive_records и отправляем в ARCHIVE_CHAT_ID в батчах
            flat = []
            for f in add_files:
                flat.append({'type': f['type'], 'file_id': f['file_id'], 'step': 'additional', 'archived_at': datetime.now().strftime('%d.%m.%Y %H:%M'), 'additional': True})
            archive_records.setdefault(obj, []).extend(flat)

            # отправляем в архив-чат батчами
            send_message(ARCHIVE_CHAT_ID, f"💾 ДОПОЛНИТЕЛЬНЫЕ ФАЙЛЫ к объекту #{obj}\n📁 {len(flat)} файлов\n🕒 {datetime.now().strftime('%d.%m.%Y %H:%M')}")
            media_batch = []
            for item in flat:
                if item['type'] == 'photo':
                    media_batch.append(InputMediaPhoto(item['file_id']))
                elif item['type'] == 'video':
                    media_batch.append(InputMediaVideo(item['file_id']))
                else:
                    if media_batch:
                        send_media_group_safe(ARCHIVE_CHAT_ID, media_batch)
                        media_batch = []
                    try:
                        bot.send_document(ARCHIVE_CHAT_ID, item['file_id'])
                        time.sleep(0.12)
                    except Exception:
                        pass
            if media_batch:
                send_media_group_safe(ARCHIVE_CHAT_ID, media_batch)

            send_message(call.message.chat.id, f"✅ Добавлено {len(flat)} файлов к объекту #{obj}", thread_id=getattr(call.message, 'message_thread_id', None))
            user_state.pop(key, None)
            bot.answer_callback_query(call.id, "Готово")
            return

    # Обычные upload_ callbacks (OK/Next/Cancel)
    action = call.data  # upload_ok / upload_next / upload_cancel
    # удалить контрольную карточку
    ctrl_mid = state.get('control_message_id')
    if ctrl_mid:
        delete_message_safe(state['chat_id'], ctrl_mid)
        state['control_message_id'] = None

    if action == "upload_cancel":
        obj = state.get('object_id', '')
        user_state.pop(key, None)
        send_message(call.message.chat.id, f"❌ Загрузка объекта {obj} отменена", thread_id=getattr(call.message, 'message_thread_id', None))
        bot.answer_callback_query(call.id, "Загрузка отменена")
        return

    if action == "upload_ok":
        advance_step(key)
        bot.answer_callback_query(call.id, "Шаг завершён")
        return

    if action == "upload_next":
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

    state['step_index'] += 1
    state['seen_media_groups'] = set()

    if state['step_index'] >= len(state['steps']):
        object_id = state.get('object_id', '')
        all_steps = state.get('steps', [])
        save_to_archive(object_id, all_steps)
        report = f"✅ Загрузка завершена для объекта #{object_id}\n\n"
        for i, s in enumerate(all_steps, 1):
            report += f"{i}. {s['name']}: {len(s['files'])} файлов\n"
        send_message(state['chat_id'], report, thread_id=state.get('thread_id'))
        user_state.pop(key, None)
        return

    next_step = state['steps'][state['step_index']]
    allow_next = next_step['name'] not in MANDATORY_STEPS

    prev_mid = state.get('control_message_id')
    if prev_mid:
        delete_message_safe(state['chat_id'], prev_mid)
        state['control_message_id'] = None

    # при переходе на новый шаг показываем заголовок (пользователь просил, чтобы именно при стартовом сообщении показывался заголовок)
    msg = send_message(state['chat_id'], f"📸 Загрузите {next_step['name']}", reply_markup=upload_inline_keyboard(allow_next=allow_next), thread_id=state.get('thread_id'))
    state['control_message_id'] = getattr(msg, 'message_id', None)
    state['step_header_sent'] = True

def save_to_archive(object_id, all_steps):
    try:
        flat = []
        for s in all_steps:
            for f in s['files']:
                rec = {'type': f['type'], 'file_id': f['file_id'], 'step': s['name'], 'archived_at': datetime.now().strftime('%d.%m.%Y %H:%M'), 'additional': False}
                flat.append(rec)

        archive_records.setdefault(object_id, [])
        archive_records[object_id].extend(flat)

        if not flat:
            return True

        send_message(ARCHIVE_CHAT_ID, f"💾 ОБЪЕКТ #{object_id}\n📁 {len(flat)} файлов\n🕒 {datetime.now().strftime('%d.%m.%Y %H:%M')}")

        media_batch = []
        for item in flat:
            if item['type'] == 'photo':
                media_batch.append(InputMediaPhoto(item['file_id']))
            elif item['type'] == 'video':
                media_batch.append(InputMediaVideo(item['file_id']))
            else:
                if media_batch:
                    send_media_group_safe(ARCHIVE_CHAT_ID, media_batch)
                    media_batch = []
                try:
                    bot.send_document(ARCHIVE_CHAT_ID, item['file_id'])
                    time.sleep(0.12)
                except Exception:
                    pass

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
