import os
from datetime import datetime
from flask import Flask, request
import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton

# ========== НАСТРОЙКИ ==========
TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
ARCHIVE_CHAT_ID = os.environ.get("ARCHIVE_CHAT_ID", "-1003160855229")

bot = telebot.TeleBot(TOKEN, parse_mode=None)
app = Flask(__name__)

# Состояние: ключ = (chat_id, thread_id, user_id)
user_state = {}

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

# ========== HELPERS ==========
def make_key_from_message(message):
    """Создаёт ключ состояния по message (учитывает thread_id, может быть None)."""
    thread_id = getattr(message, "message_thread_id", None)
    return (message.chat.id, thread_id, message.from_user.id)

def make_key_from_callback(call):
    """Создаёт ключ состояния по callback (call.message может иметь thread_id)."""
    thread_id = getattr(call.message, "message_thread_id", None)
    return (call.message.chat.id, thread_id, call.from_user.id)

def send_message(chat_id, text, reply_markup=None, thread_id=None):
    """Возвращает объект Message"""
    return bot.send_message(chat_id, text, reply_markup=reply_markup, message_thread_id=thread_id)

def send_file(chat_id, file_type, file_id, caption=None):
    if file_type == "photo":
        bot.send_photo(chat_id, file_id, caption=caption)
    elif file_type == "document":
        bot.send_document(chat_id, file_id, caption=caption)
    elif file_type == "video":
        bot.send_video(chat_id, file_id, caption=caption)

# ========== INLINE КЛАВИАТУРЫ ==========
def upload_inline_keyboard():
    kb = InlineKeyboardMarkup(row_width=3)
    kb.add(
        InlineKeyboardButton("✅ OK", callback_data="upload_ok"),
        InlineKeyboardButton("➡️ Next", callback_data="upload_next"),
        InlineKeyboardButton("❌ Cancel", callback_data="upload_cancel")
    )
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
        "Нажмите кнопку для действия."
    )
    send_message(message.chat.id, text, reply_markup=main_inline_keyboard(), thread_id=message.message_thread_id)

@bot.message_handler(commands=['photo'])
def start_upload(message):
    key = make_key_from_message(message)
    send_message(message.chat.id, "Введите номер объекта для загрузки файлов:", thread_id=message.message_thread_id)
    user_state[key] = {'command': 'await_object'}

# ========== ОБРАБОТКА ТЕКСТА ==========
@bot.message_handler(content_types=['text'])
def handle_text(message):
    key = make_key_from_message(message)
    state = user_state.get(key)
    if not state:
        return

    # Ввод номера объекта
    if state.get('command') == 'await_object':
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
            'seen_media_groups': set(),          # чтобы не спамить клавиатурой при медиагруппах
            'control_message_id': None           # message_id сообщения с инлайн-клавиатурой (если нужно редактировать)
        }

        # Отправляем первый шаг с инлайн-кнопками (в тему или основной чат)
        msg = send_message(
            message.chat.id,
            f"📸 Загрузите {steps[0]['name']}",
            reply_markup=upload_inline_keyboard(),
            thread_id=getattr(message, 'message_thread_id', None)
        )
        # Сохраним id сообщения с контролом
        user_state[key]['control_message_id'] = getattr(msg, 'message_id', None)

# ========== ОБРАБОТКА ФАЙЛОВ ==========
@bot.message_handler(content_types=['photo', 'document', 'video'])
def handle_files(message):
    """
    Логика:
    - Добавляем файл в текущий шаг пользователя (если есть активная загрузка).
    - НЕ шлём "Файл сохранён".
    - Отправляем инлайн-клавиатуру ОДИН РАЗ:
        - если message.media_group_id есть — только при первом файле группы (по user_state[...]['seen_media_groups'])
        - если media_group_id нет — отправляем клавиатуру сразу для одиночного файла
    """
    # Определяем ключ по отправителю/теме
    key = make_key_from_message(message)
    state = user_state.get(key)
    if not state or state.get('command') != 'upload_steps':
        return  # нет активной загрузки для этого пользователя

    # достаём текущий шаг
    step = state['steps'][state['step_index']]

    # сохраняем файл в шаг
    if message.photo:
        file_id = message.photo[-1].file_id
        step['files'].append({'type': 'photo', 'file_id': file_id})
    elif message.document:
        step['files'].append({'type': 'document', 'file_id': message.document.file_id})
    elif message.video:
        step['files'].append({'type': 'video', 'file_id': message.video.file_id})

    # Решаем, нужно ли показывать клавиатуру
    mgid = getattr(message, 'media_group_id', None)
    already_seen = state.get('seen_media_groups', set())

    should_send_keyboard = False
    if mgid:
        if mgid not in already_seen:
            should_send_keyboard = True
            already_seen.add(mgid)
            state['seen_media_groups'] = already_seen
    else:
        # одиночный файл — показываем клавиатуру (если уже не показана для этого шага)
        # защитимся от повторного показа: проверим control_message_id
        should_send_keyboard = True

    if should_send_keyboard:
        # Отправляем клавиатуру и сохраняем её message_id чтобы можно было редактировать/убрать
        msg = send_message(
            state['chat_id'],
            f"📸 Шаг: {step['name']}\nВыберите действие:",
            reply_markup=upload_inline_keyboard(),
            thread_id=state.get('thread_id')
        )
        state['control_message_id'] = getattr(msg, 'message_id', None)

# ========== ОБРАБОТКА CALLBACK: команды (cmd_) и upload_ ==========
@bot.callback_query_handler(func=lambda c: c.data and c.data.startswith("cmd_"))
def handle_cmd_callback(call):
    """Обработка кнопок из главного меню (cmd_info, cmd_photo и т.д.)"""
    data = call.data
    key = make_key_from_callback(call)

    # Для команд, которые требуют ввода/дальнейших действий
    if data == "cmd_photo":
        # эквивалент /photo
        send_message(call.message.chat.id, "Введите номер объекта для загрузки файлов:", thread_id=getattr(call.message, 'message_thread_id', None))
        user_state[key] = {'command': 'await_object'}
        bot.answer_callback_query(call.id, "Введите номер объекта")
        return

    # Заглушки для других команд (можно расширить)
    if data == "cmd_info":
        bot.answer_callback_query(call.id, "Info: пока заглушка")
        bot.send_message(call.message.chat.id, "ℹ️ Информация об объекте: (заглушка)", message_thread_id=getattr(call.message, 'message_thread_id', None))
    elif data == "cmd_download":
        bot.answer_callback_query(call.id, "Download: пока заглушка")
        bot.send_message(call.message.chat.id, "⬇️ Скачать файлы: (заглушка)", message_thread_id=getattr(call.message, 'message_thread_id', None))
    elif data == "cmd_result":
        bot.answer_callback_query(call.id, "Result: пока заглушка")
        bot.send_message(call.message.chat.id, "📋 Список обработанных объектов: (заглушка)", message_thread_id=getattr(call.message, 'message_thread_id', None))
    elif data == "cmd_help":
        bot.answer_callback_query(call.id, "Help")
        bot.send_message(call.message.chat.id, "❓ Помощь: используйте /photo для загрузки файлов или кнопки.", message_thread_id=getattr(call.message, 'message_thread_id', None))
    else:
        bot.answer_callback_query(call.id, "Неизвестная команда")

@bot.callback_query_handler(func=lambda c: c.data and c.data.startswith("upload_"))
def handle_upload_callback(call):
    """
    Обработка OK / Next / Cancel — по callback'ам.
    Важно: проверяем, принадлежит ли сессия пользователю (по key).
    Если нажал не владелец — выдаём уведомление и игнорируем.
    """
    key = make_key_from_callback(call)
    state = user_state.get(key)

    if not state or state.get('command') != 'upload_steps':
        # Если для этого пользователя нет активной загрузки — уведомляем
        bot.answer_callback_query(call.id, "Нет активной загрузки или сессия истекла.")
        return

    # Уберём инлайн-клавиатуру с предыдущего контрольного сообщения (чтобы не нажали дважды)
    ctrl_mid = state.get('control_message_id')
    if ctrl_mid:
        try:
            bot.edit_message_reply_markup(chat_id=state['chat_id'], message_id=ctrl_mid, reply_markup=None, message_thread_id=state.get('thread_id'))
        except Exception:
            pass  # не критично, может быть удалено или истекло

    if call.data == "upload_ok":
        advance_step(key)
        bot.answer_callback_query(call.id, "Шаг завершён ✅")
    elif call.data == "upload_next":
        advance_step(key, skip=True)
        bot.answer_callback_query(call.id, "Шаг пропущен ➡️")
    elif call.data == "upload_cancel":
        obj = state.get('object_id', '')
        user_state.pop(key, None)
        # отредактируем/заменим текст контрольного сообщения, если возможно
        try:
            bot.edit_message_text(f"❌ Загрузка объекта {obj} отменена", chat_id=call.message.chat.id, message_id=call.message.message_id, message_thread_id=getattr(call.message, 'message_thread_id', None))
        except Exception:
            # если не получилось редактировать — просто отправим сообщение
            send_message(call.message.chat.id, f"❌ Загрузка объекта {obj} отменена", thread_id=getattr(call.message, 'message_thread_id', None))
        bot.answer_callback_query(call.id, "Загрузка отменена ❌")
    else:
        bot.answer_callback_query(call.id, "Неизвестное действие")

# ========== ПРОГРЕСС ==========
def advance_step(key, skip=False):
    """
    Продвигает шаг в состоянии пользователя,
    и отправляет сообщение о следующем шаге с инлайн-клавиатурой.
    Если шагов больше нет — архивируем и отчищаем состояние.
    """
    state = user_state.get(key)
    if not state:
        return

    state['step_index'] += 1

    if state['step_index'] >= len(state['steps']):
        # Завершили все шаги
        object_id = state.get('object_id', '')
        all_steps = state.get('steps', [])
        save_to_archive(object_id, all_steps)

        report = f"✅ Загрузка завершена для объекта #{object_id}\n\n"
        for i, s in enumerate(all_steps, 1):
            report += f"{i}. {s['name']}: {len(s['files'])} файлов\n"

        send_message(state['chat_id'], report, thread_id=state.get('thread_id'))
        user_state.pop(key, None)
    else:
        # Отправляем сообщение со следующим шагом и клавиатурой
        next_step = state['steps'][state['step_index']]
        msg = send_message(
            state['chat_id'],
            f"📸 Загрузите {next_step['name']}",
            reply_markup=upload_inline_keyboard(),
            thread_id=state.get('thread_id')
        )
        state['control_message_id'] = getattr(msg, 'message_id', None)
        # очищаем seen_media_groups для нового шага, чтобы клавиатура могла появиться при новой медиагруппе
        state['seen_media_groups'] = set()

# ========== АРХИВ ==========
def save_to_archive(object_id, all_steps):
    total_files = sum(len(s['files']) for s in all_steps)
    info = f"💾 ОБЪЕКТ #{object_id}\n📁 {total_files} файлов\n🕒 {datetime.now().strftime('%d.%m.%Y %H:%M')}"
    send_message(ARCHIVE_CHAT_ID, info)

    for step in all_steps:
        for f in step['files']:
            send_file(ARCHIVE_CHAT_ID, f['type'], f['file_id'])

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
    bot.remove_webhook()
    WEBHOOK_URL = f"https://telegram-bot-b6pn.onrender.com/{TOKEN}"
    bot.set_webhook(url=WEBHOOK_URL)
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))
