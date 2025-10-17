# bot.py
import os
import asyncio
from datetime import datetime
from typing import Tuple, Dict, Any, List

import pandas as pd  # Для /info (если не нужен, можно убрать)
from aiohttp import web

from aiogram import Bot, Dispatcher, types
from aiogram.types import (
    ReplyKeyboardMarkup, KeyboardButton,
    InlineKeyboardMarkup, InlineKeyboardButton,
    InputMediaPhoto, InputMediaVideo, InputMediaDocument, Message, CallbackQuery
)
from aiogram.filters import Command

# ========== НАСТРОЙКИ ==========
TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
if not TOKEN:
    raise RuntimeError("TELEGRAM_BOT_TOKEN env var is required")

ARCHIVE_CHAT_ID = os.environ.get("ARCHIVE_CHAT_ID", "-1003160855229")
# Полный URL, по которому Telegram будет доставлять обновления.
# Рекомендуется задавать на Render как: https://<your-service>.onrender.com/<TOKEN>
WEBHOOK_URL = os.environ.get("WEBHOOK_URL")  # пример: https://.../<TOKEN>

# Порт (Render обычно задаёт PORT env var)
PORT = int(os.environ.get("PORT", 10000))
WEBAPP_HOST = "0.0.0.0"

bot = Bot(token=TOKEN, parse_mode="HTML")
dp = Dispatcher()

# ========== ГЛОБАЛЬНЫЕ ПЕРЕМЕННЫЕ ==========
# ключ: (chat_id, thread_id, user_id)
user_state: Dict[Tuple[int, int, int], Dict[str, Any]] = {}
objects_data: Dict[str, Dict[str, Any]] = {}  # объект -> данные (в памяти)

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

MANDATORY_STEPS = {
    "Общее фото помещения",
    "Фото корректора",
    "Фото места устанавливаемой СТМ",
    "Фото места прокладки кабелей"
}

# ========== КЛАВИАТУРЫ ==========
def make_main_reply_keyboard() -> ReplyKeyboardMarkup:
    kb = ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=False)
    kb.add(KeyboardButton("/start"), KeyboardButton("/photo"))
    kb.add(KeyboardButton("/addphoto"), KeyboardButton("/download"))
    kb.add(KeyboardButton("/result"), KeyboardButton("/info"))
    return kb

MAIN_KB = make_main_reply_keyboard()

def upload_inline_keyboard(step_name: str) -> InlineKeyboardMarkup:
    kb = InlineKeyboardMarkup(row_width=3)
    # кнопки: Завершить / (возможно) След. / Отмена
    buttons = [InlineKeyboardButton(text="✅ Завершить", callback_data="upload_ok")]
    if step_name not in MANDATORY_STEPS:
        buttons.append(InlineKeyboardButton(text="➡️ След.", callback_data="upload_next"))
    buttons.append(InlineKeyboardButton(text="❌ Отмена", callback_data="upload_cancel"))
    kb.add(*buttons)
    return kb

def addphoto_markup() -> InlineKeyboardMarkup:
    kb = InlineKeyboardMarkup()
    kb.add(InlineKeyboardButton("✅ Завершить", callback_data="addphoto_done"),
           InlineKeyboardButton("❌ Отмена", callback_data="upload_cancel"))
    return kb

# ========== ХЕЛПЕРЫ ==========
def make_key(chat_id: int, thread_id: int, user_id: int) -> Tuple[int, int, int]:
    return (chat_id, thread_id or 0, user_id)

async def send_message(chat_id: int, text: str, thread_id: int = None,
                       reply_markup=None, **kwargs) -> types.Message:
    """Везде отправляем с MAIN_KB как нижней клавиатурой (если явно не передали None)."""
    try:
        rm = reply_markup if reply_markup is not None else MAIN_KB
        # message_thread_id используется в групповых темах (forum)
        params = {"chat_id": chat_id, "text": text, "reply_markup": rm}
        if thread_id:
            params["message_thread_id"] = thread_id
        params.update(kwargs)
        msg = await bot.send_message(**params)
        return msg
    except Exception as e:
        print(f"[send_message] Ошибка: {e}")

async def delete_message(chat_id: int, msg_id: int):
    try:
        await bot.delete_message(chat_id, msg_id)
    except Exception:
        pass

# ========== КОМАНДЫ ==========
@dp.message(Command(commands=["start"]))
async def cmd_start(message: Message):
    key = make_key(message.chat.id, message.message_thread_id or 0, message.from_user.id)
    # Сброс состояния пользователя (по желанию)
    user_state.pop(key, None)
    text = (
        "🤖 Бот для управления объектами ИПУГ\n\n"
        "Нижняя клавиатура всегда доступна. Используйте /photo, /addphoto и т.д."
    )
    await send_message(message.chat.id, text, thread_id=message.message_thread_id)

@dp.message(Command(commands=["photo"]))
async def cmd_photo(message: Message):
    key = make_key(message.chat.id, message.message_thread_id or 0, message.from_user.id)
    user_state[key] = {"command": "await_object"}
    await send_message(message.chat.id, "Введите номер объекта для загрузки:", thread_id=message.message_thread_id)

@dp.message(Command(commands=["addphoto"]))
async def cmd_addphoto(message: Message):
    key = make_key(message.chat.id, message.message_thread_id or 0, message.from_user.id)
    user_state[key] = {"command": "await_addphoto_object"}
    # отправляем подсказку и кнопки Завершить/Отмена
    await send_message(
        message.chat.id,
        "Введите номер объекта, чтобы добавить фото:",
        thread_id=message.message_thread_id
    )

@dp.message(Command(commands=["download"]))
async def cmd_download(message: Message):
    key = make_key(message.chat.id, message.message_thread_id or 0, message.from_user.id)
    if not objects_data:
        await send_message(message.chat.id, "📂 Нет сохранённых объектов.", thread_id=message.message_thread_id)
        return
    # ставим состояние ожидания id объекта
    user_state[key] = {"command": "await_download_object"}
    await send_message(message.chat.id, "Введите номер объекта для скачивания файлов:", thread_id=message.message_thread_id)

@dp.message(Command(commands=["result"]))
async def cmd_result(message: Message):
    if not objects_data:
        await send_message(message.chat.id, "📋 Нет завершённых загрузок.", thread_id=message.message_thread_id)
        return
    text = "✅ Завершённые загрузки:\n"
    for oid, data in objects_data.items():
        total_files = sum(len(s["files"]) for s in data["steps"])
        text += f"• Объект {oid}: {total_files} файлов\n"
    await send_message(message.chat.id, text, thread_id=message.message_thread_id)

@dp.message(Command(commands=["info"]))
async def cmd_info(message: Message):
    """
    Запрашивает номер объекта, затем пытается найти в objects.xlsx
    Ожидается, что файл objects.xlsx находится в рабочей директории.
    """
    key = make_key(message.chat.id, message.message_thread_id or 0, message.from_user.id)
    user_state[key] = {"command": "await_info_object"}
    await send_message(message.chat.id, "Введите номер объекта для получения информации:", thread_id=message.message_thread_id)

# ========== ОБРАБОТКА ТЕКСТА ==========
@dp.message()  # ловим любые текстовые сообщения
async def handle_text(message: Message):
    key = make_key(message.chat.id, message.message_thread_id or 0, message.from_user.id)
    state = user_state.get(key)
    if not state:
        # ничего не делаем, потому что нижняя клавиатура всегда доступна
        return

    cmd = state.get("command")

    # начало загрузки
    if cmd == "await_object":
        object_id = message.text.strip()
        steps = [{"name": s, "files": []} for s in UPLOAD_STEPS]
        user_state[key] = {
            "command": "upload_steps",
            "object_id": object_id,
            "steps": steps,
            "step_index": 0,
            "chat_id": message.chat.id,
            "thread_id": message.message_thread_id or 0
        }
        await send_upload_step(key)

    # добавление фото к существующему объекту
    elif cmd == "await_addphoto_object":
        object_id = message.text.strip()
        user_state[key] = {
            "command": "add_photos",
            "object_id": object_id,
            "files": [],
            "chat_id": message.chat.id,
            "thread_id": message.message_thread_id or 0
        }
        # присылаем инструкцию + inline кнопки
        await send_message(
            message.chat.id,
            f"📸 Отправьте дополнительные фотографии для объекта {object_id}.",
            thread_id=message.message_thread_id,
            reply_markup=addphoto_markup()
        )

    elif cmd == "await_download_object":
        object_id = message.text.strip()
        if object_id not in objects_data:
            await send_message(message.chat.id, f"❌ Объект {object_id} не найден в архиве.", thread_id=message.message_thread_id)
            user_state.pop(key, None)
            return
        # отправляем файлы объекта
        data = objects_data[object_id]
        await send_object_files(message.chat.id, object_id, data, thread_id=message.message_thread_id)
        user_state.pop(key, None)

    elif cmd == "await_info_object":
        object_id = message.text.strip()
        await handle_info_request(message.chat_id, object_id, thread_id=message.message_thread_id)
        user_state.pop(key, None)

# ========== ОБРАБОТКА ФАЙЛОВ ==========
@dp.message(content_types=["photo", "document", "video"])
async def handle_files(message: Message):
    key = make_key(message.chat.id, message.message_thread_id or 0, message.from_user.id)
    state = user_state.get(key)
    if not state:
        return

    cmd = state.get("command")

    def extract_file_info(msg: Message) -> Dict[str, str]:
        if msg.photo:
            return {"type": "photo", "file_id": msg.photo[-1].file_id}
        if msg.document:
            return {"type": "document", "file_id": msg.document.file_id}
        if msg.video:
            return {"type": "video", "file_id": msg.video.file_id}
        return {}

    if cmd == "upload_steps":
        step = state["steps"][state["step_index"]]
        f = extract_file_info(message)
        if f:
            step["files"].append(f)
        # Удалим старое "выберите действие" сообщение, если есть
        if "last_message_id" in state:
            try:
                await delete_message(state["chat_id"], state["last_message_id"])
            except Exception:
                pass
        # отправляем простую инструкцию + inline клавиатуру для шага
        step_name = step["name"]
        text = f"📸 Отправьте {step_name}"
        msg = await send_message(state["chat_id"], text,
                                 reply_markup=upload_inline_keyboard(step_name),
                                 thread_id=state["thread_id"])
        # сохраняем id, чтобы потом удалить
        state["last_message_id"] = msg.message_id

    elif cmd == "add_photos":
        f = extract_file_info(message)
        if f:
            state["files"].append(f)
        # просто не подтверждаем — пользователь нажмёт ✅ Завершить когда готов

# ========== CALLBACKS ==========
@dp.callback_query(lambda c: c.data and (c.data.startswith("upload_") or c.data.startswith("addphoto_")))
async def handle_callback(call: CallbackQuery):
    # thread id проницателен через call.message.message_thread_id
    thread_id = getattr(call.message, "message_thread_id", 0) or 0
    key = make_key(call.message.chat.id, thread_id, call.from_user.id)
    state = user_state.get(key)
    if not state:
        await call.answer("Нет активной загрузки", show_alert=True)
        return

    if call.data == "upload_ok":
        await advance_step(key)
        await call.answer("✅ Шаг завершён")
    elif call.data == "upload_next":
        await advance_step(key, skip=True)
        await call.answer("➡️ Пропущено")
    elif call.data == "upload_cancel":
        obj = state.get("object_id", "")
        user_state.pop(key, None)
        # удаляем сообщение с кнопками
        try:
            await delete_message(call.message.chat.id, call.message.message_id)
        except Exception:
            pass
        await send_message(call.message.chat.id, f"❌ Загрузка для объекта {obj} отменена")
        await call.answer("Отменено")
    elif call.data == "addphoto_done":
        object_id = state.get("object_id")
        files = state.get("files", [])
        if object_id not in objects_data:
            objects_data[object_id] = {"steps": []}
        # сохраняем как дополнительный шаг
        save_steps = [{"name": "Дополнительные файлы", "files": files}]
        await save_to_archive(object_id, save_steps, append=True)
        # merge into memory
        if "steps" not in objects_data[object_id]:
            objects_data[object_id]["steps"] = []
        objects_data[object_id]["steps"].extend(save_steps)
        user_state.pop(key, None)
        try:
            await delete_message(call.message.chat.id, call.message.message_id)
        except Exception:
            pass
        await send_message(call.message.chat.id, f"✅ Дополнительные файлы для объекта {object_id} сохранены.")
        await call.answer("Готово ✅")

# ========== ПРОДВИЖЕНИЕ ШАГОВ ==========
async def send_upload_step(key: Tuple[int, int, int]):
    state = user_state[key]
    step = state["steps"][state["step_index"]]
    if "last_message_id" in state:
        try:
            await delete_message(state["chat_id"], state["last_message_id"])
        except Exception:
            pass
    text = f"📸 Отправьте {step['name']}"
    msg = await send_message(state["chat_id"], text,
                             reply_markup=upload_inline_keyboard(step["name"]),
                             thread_id=state["thread_id"])
    state["last_message_id"] = msg.message_id

async def advance_step(key: Tuple[int, int, int], skip: bool = False):
    state = user_state.get(key)
    if not state:
        return
    state["step_index"] += 1
    if state["step_index"] >= len(state["steps"]):
        object_id = state["object_id"]
        all_steps = state["steps"]
        await save_to_archive(object_id, all_steps)
        objects_data[object_id] = {"steps": all_steps}
        user_state.pop(key, None)
        await send_message(state["chat_id"], f"✅ Загрузка завершена для объекта {object_id}")
    else:
        await send_upload_step(key)

# ========== СОХРАНЕНИЕ В АРХИВ ==========
async def save_to_archive(object_id: str, steps: List[Dict[str, Any]], append: bool = False):
    """
    Отправляет в ARCHIVE_CHAT_ID информацию и media_group'ы.
    Ограничения Telegram: media_group до 10 элементов, но в вашем старом коде было 50 — тут сгруппировано по 10.
    """
    try:
        info_text = f"💾 ОБЪЕКТ #{object_id}\n🕒 {datetime.now().strftime('%d.%m.%Y %H:%M')}"
        await bot.send_message(ARCHIVE_CHAT_ID, info_text)
        for s in steps:
            files = s.get("files", [])
            if not files:
                continue
            # подготовка по чанкам по 10
            idx = 0
            while idx < len(files):
                chunk = files[idx: idx + 10]  # Telegram ограничивает 10
                media = []
                for f in chunk:
                    if f["type"] == "photo":
                        media.append(InputMediaPhoto(media=f["file_id"]))
                    elif f["type"] == "video":
                        media.append(InputMediaVideo(media=f["file_id"]))
                    elif f["type"] == "document":
                        media.append(InputMediaDocument(media=f["file_id"]))
                if media:
                    await bot.send_media_group(ARCHIVE_CHAT_ID, media)
                idx += 10
    except Exception as e:
        print(f"[save_to_archive] Ошибка: {e}")

# ========== ОТПРАВКА ФАЙЛОВ ПО ОБЪЕКТУ (DOWNLOAD) ==========
async def send_object_files(chat_id: int, object_id: str, data: Dict[str, Any], thread_id: int = None):
    """
    Отправляет все файлы по объекту. Для каждого шага:
     - если файлов >1 => send_media_group (пакетно по 10)
     - если 1 => send_* в зависимости от типа
    """
    steps = data.get("steps", [])
    if not steps:
        await send_message(chat_id, f"❌ Для объекта {object_id} нет файлов.", thread_id=thread_id)
        return
    await send_message(chat_id, f"📁 Файлы для объекта {object_id}:", thread_id=thread_id)
    for s in steps:
        files = s.get("files", [])
        if not files:
            continue
        # если один файл — отправляем как одиночное сообщение
        if len(files) == 1:
            f = files[0]
            try:
                if f["type"] == "photo":
                    await bot.send_photo(chat_id, f["file_id"], caption=s.get("name", ""), message_thread_id=thread_id)
                elif f["type"] == "video":
                    await bot.send_video(chat_id, f["file_id"], caption=s.get("name", ""), message_thread_id=thread_id)
                elif f["type"] == "document":
                    await bot.send_document(chat_id, f["file_id"], caption=s.get("name", ""), message_thread_id=thread_id)
            except Exception as e:
                print(f"[send_object_files single] Ошибка: {e}")
        else:
            # группируем по 10
            idx = 0
            while idx < len(files):
                chunk = files[idx: idx + 10]
                media = []
                for f in chunk:
                    if f["type"] == "photo":
                        media.append(InputMediaPhoto(media=f["file_id"]))
                    elif f["type"] == "video":
                        media.append(InputMediaVideo(media=f["file_id"]))
                    elif f["type"] == "document":
                        media.append(InputMediaDocument(media=f["file_id"]))
                if media:
                    try:
                        await bot.send_media_group(chat_id, media, message_thread_id=thread_id)
                    except Exception as e:
                        print(f"[send_object_files group] Ошибка: {e}")
                idx += 10

# ========== /info: чтение objects.xlsx ==========
async def handle_info_request(chat_id: int, object_id: str, thread_id: int = None):
    filename = "objects.xlsx"
    if not os.path.exists(filename):
        await send_message(chat_id, "❌ Файл objects.xlsx не найден на сервере.", thread_id=thread_id)
        return
    try:
        df = pd.read_excel(filename, dtype=str)  # читаем как строки
        # допустим, в таблице есть колонка "object_id" или "Номер" — попробуем несколько вариантов
        possible_cols = [c for c in df.columns if any(x in c.lower() for x in ["object", "номер", "id"])]
        if not possible_cols:
            # просто ищем любой вхожд. object_id по всем ячейкам
            matched = df[df.apply(lambda row: row.astype(str).str.contains(object_id, case=False, na=False).any(), axis=1)]
        else:
            # ищем по найденным колонкам
            mask = False
            for col in possible_cols:
                mask = mask | df[col].astype(str).str.strip().eq(object_id)
            matched = df[mask]
        if matched.empty:
            await send_message(chat_id, f"ℹ️ Информация по объекту {object_id} не найдена в objects.xlsx.", thread_id=thread_id)
            return
        # форматируем строку(и)
        texts = []
        for _, row in matched.iterrows():
            parts = [f"{col}: {str(row[col])}" for col in df.columns if pd.notna(row[col])]
            texts.append("\n".join(parts))
        await send_message(chat_id, "ℹ️ Найдено:\n\n" + "\n\n---\n\n".join(texts), thread_id=thread_id)
    except Exception as e:
        print(f"[handle_info_request] Ошибка: {e}")
        await send_message(chat_id, "❌ Ошибка при чтении objects.xlsx", thread_id=thread_id)

# ========== WEBHOOK (aiohttp) ==========
async def on_startup():
    # Устанавливаем webhook если передан WEBHOOK_URL
    if WEBHOOK_URL:
        try:
            await bot.delete_webhook(drop_pending_updates=True)
            await bot.set_webhook(WEBHOOK_URL)
            print(f"Webhook установлен: {WEBHOOK_URL}")
        except Exception as e:
            print(f"[on_startup] Ошибка установки webhook: {e}")
    else:
        print("WEBHOOK_URL не задан. Вы можете задать переменную окружения WEBHOOK_URL для webhook режима.")

async def handle_webhook(request: web.Request):
    """Обработчик POST запросов от Telegram (webhook)."""
    try:
        data = await request.json()
    except Exception:
        return web.Response(text="no json", status=400)
    update = types.Update(**data)
    await dp.feed_update(update)
    return web.Response(text="OK")

async def index(request: web.Request):
    return web.Response(text="🤖 Бот работает", status=200)

def run():
    app = web.Application()
    app.router.add_post(f"/{TOKEN}", handle_webhook)  # Telegram будет постить сюда
    app.router.add_get("/", index)
    # Запуск on_startup
    loop = asyncio.get_event_loop()
    loop.create_task(on_startup())
    web.run_app(app, host=WEBAPP_HOST, port=PORT)

if __name__ == "__main__":
    print("🚀 Bot (aiogram 3.x) starting...")
    run()
