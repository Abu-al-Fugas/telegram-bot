import os
import asyncio
from datetime import datetime
from aiogram import Bot, Dispatcher, Router, F
from aiogram.types import (
    Message,
    CallbackQuery,
    ReplyKeyboardMarkup,
    KeyboardButton,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    InputMediaPhoto,
    InputMediaVideo,
    InputMediaDocument,
    BotCommand
)
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiohttp import web
import openpyxl

# ========== НАСТРОЙКИ ==========
TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
ARCHIVE_CHAT_ID = os.environ.get("ARCHIVE_CHAT_ID", "-1003160855229")
WEBHOOK_URL = "https://telegram-bot-b6pn.onrender.com"
PORT = int(os.environ.get("PORT", 10000))

# ========== ИНИЦИАЛИЗАЦИЯ БОТА ==========
bot = Bot(token=TOKEN)
storage = MemoryStorage()
dp = Dispatcher(storage=storage)
router = Router()

# ========== КОНСТАНТЫ ==========
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

# ========== СОСТОЯНИЯ FSM ==========
class UploadStates(StatesGroup):
    waiting_object_id = State()
    uploading_steps = State()

class AddPhotoStates(StatesGroup):
    waiting_object_id = State()
    uploading_files = State()

class DownloadStates(StatesGroup):
    waiting_object_id = State()

class InfoStates(StatesGroup):
    waiting_object_id = State()

# ========== ГЛОБАЛЬНЫЕ ДАННЫЕ ==========
objects_data = {}

# ========== КЛАВИАТУРЫ ==========
def get_main_keyboard():
    """Reply-клавиатура с основными командами"""
    keyboard = ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="/start"), KeyboardButton(text="/photo")],
            [KeyboardButton(text="/addphoto"), KeyboardButton(text="/download")],
            [KeyboardButton(text="/result"), KeyboardButton(text="/info")]
        ],
        resize_keyboard=True,
        persistent=True
    )
    return keyboard

def get_upload_keyboard(step_name, has_files=False):
    """Inline-клавиатура для шагов загрузки"""
    buttons = []
    
    if has_files:
        # После загрузки файла показываем ✅ Завершить и ❌ Отмена
        buttons.append([
            InlineKeyboardButton(text="✅ Завершить", callback_data="upload_ok"),
            InlineKeyboardButton(text="❌ Отмена", callback_data="upload_cancel")
        ])
    else:
        # До загрузки файла
        if step_name in MANDATORY_STEPS:
            # Для ОБЯЗАТЕЛЬНЫХ шагов - только кнопка "Отмена"
            buttons.append([
                InlineKeyboardButton(text="❌ Отмена", callback_data="upload_cancel")
            ])
        else:
            # Для НЕОБЯЗАТЕЛЬНЫХ шагов - кнопки "След." и "Отмена"
            buttons.append([
                InlineKeyboardButton(text="➡️ След.", callback_data="upload_next"),
                InlineKeyboardButton(text="❌ Отмена", callback_data="upload_cancel")
            ])
    
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def get_addphoto_keyboard():
    """Inline-клавиатура для добавления фото"""
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✅ Завершить", callback_data="addphoto_done"),
            InlineKeyboardButton(text="❌ Отмена", callback_data="upload_cancel")
        ]
    ])
    return keyboard

# ========== КОМАНДЫ ==========
@router.message(Command("start"))
async def cmd_start(message: Message):
    text = (
        "🤖 Бот для управления объектами ИПУГ\n\n"
        "Доступные команды:\n"
        "/start – запуск/перезапуск бота\n"
        "/photo – загрузка файлов с чек-листом\n"
        "/addphoto – добавить файлы к существующему объекту\n"
        "/download – скачать файлы объекта из архива\n"
        "/result – список обработанных объектов\n"
        "/info – получить информацию об объекте из objects.xlsx"
    )
    await message.answer(text, reply_markup=get_main_keyboard())

@router.message(Command("photo"))
async def cmd_photo(message: Message, state: FSMContext):
    await state.set_state(UploadStates.waiting_object_id)
    await message.answer(
        "📝 Введите номер объекта для загрузки:",
        reply_markup=get_main_keyboard()
    )

@router.message(Command("addphoto"))
async def cmd_addphoto(message: Message, state: FSMContext):
    await state.set_state(AddPhotoStates.waiting_object_id)
    await message.answer(
        "📝 Введите номер объекта, чтобы добавить фото:",
        reply_markup=get_main_keyboard()
    )

@router.message(Command("download"))
async def cmd_download(message: Message, state: FSMContext):
    await state.set_state(DownloadStates.waiting_object_id)
    await message.answer(
        "📝 Введите номер объекта для скачивания файлов:",
        reply_markup=get_main_keyboard()
    )

@router.message(Command("result"))
async def cmd_result(message: Message):
    if not objects_data:
        await message.answer("📋 Нет завершённых загрузок в текущей сессии.", reply_markup=get_main_keyboard())
        return
    
    text = "✅ Завершённые загрузки (текущая сессия):\n"
    for oid, data in objects_data.items():
        total_files = sum(len(s["files"]) for s in data["steps"])
        text += f"• Объект {oid}: {total_files} файлов\n"
    
    await message.answer(text, reply_markup=get_main_keyboard())

@router.message(Command("info"))
async def cmd_info(message: Message, state: FSMContext):
    await state.set_state(InfoStates.waiting_object_id)
    await message.answer(
        "📝 Введите номер объекта для получения информации:",
        reply_markup=get_main_keyboard()
    )

# ========== ОБРАБОТКА НОМЕРА ОБЪЕКТА ==========
@router.message(UploadStates.waiting_object_id)
async def process_upload_object_id(message: Message, state: FSMContext):
    object_id = message.text.strip()
    
    steps = [{"name": s, "files": []} for s in UPLOAD_STEPS]
    
    await state.update_data(
        object_id=object_id,
        steps=steps,
        step_index=0,
        last_message_id=None
    )
    await state.set_state(UploadStates.uploading_steps)
    
    await send_upload_step(message, state)

@router.message(AddPhotoStates.waiting_object_id)
async def process_addphoto_object_id(message: Message, state: FSMContext):
    object_id = message.text.strip()
    
    await state.update_data(
        object_id=object_id,
        files=[],
        last_message_id=None
    )
    await state.set_state(AddPhotoStates.uploading_files)
    
    msg = await message.answer(
        f"📸 Отправьте файлы для объекта {object_id}.\nКогда закончите, нажмите ✅ Завершить.",
        reply_markup=get_addphoto_keyboard()
    )
    await state.update_data(last_message_id=msg.message_id)

@router.message(DownloadStates.waiting_object_id)
async def process_download_object_id(message: Message, state: FSMContext):
    """Извлечение файлов из архивного чата по номеру объекта"""
    object_id = message.text.strip()
    
    await message.answer(f"🔍 Ищу файлы объекта {object_id} в архиве...")
    
    try:
        # Ищем сообщения в архивном чате с номером объекта
        found_messages = []
        files_count = 0
        
        # Получаем последние 1000 сообщений из архивного чата
        # Telegram API ограничивает получение истории, поэтому используем поиск
        offset_id = 0
        search_limit = 100  # Количество сообщений для поиска
        
        # Пытаемся найти сообщение с заголовком объекта
        async for msg in bot.iter_history(ARCHIVE_CHAT_ID, limit=search_limit):
            if msg.text and f"ОБЪЕКТ #{object_id}" in msg.text:
                found_messages.append(msg)
                # Начинаем собирать файлы после найденного заголовка
                offset_id = msg.message_id
                break
        
        if not found_messages:
            await message.answer(
                f"❌ Объект {object_id} не найден в архиве.",
                reply_markup=get_main_keyboard()
            )
            await state.clear()
            return
        
        # Собираем все сообщения после заголовка до следующего объекта
        collecting = True
        async for msg in bot.iter_history(ARCHIVE_CHAT_ID, offset_id=offset_id, limit=200):
            if msg.message_id == offset_id:
                continue
            
            # Если встретили новый объект, прекращаем сбор
            if msg.text and "ОБЪЕКТ #" in msg.text and f"#{object_id}" not in msg.text:
                break
            
            # Собираем файлы
            if msg.photo or msg.document or msg.video or msg.media_group_id:
                found_messages.append(msg)
                files_count += 1
        
        if files_count == 0:
            await message.answer(
                f"❌ Файлы для объекта {object_id} не найдены в архиве.",
                reply_markup=get_main_keyboard()
            )
            await state.clear()
            return
        
        # Пересылаем все найденные файлы пользователю
        await message.answer(f"📂 Найдено файлов: {files_count}. Отправляю...")
        
        media_group_ids = set()
        for msg in reversed(found_messages):
            try:
                # Пропускаем текстовые сообщения (заголовки)
                if msg.text and not msg.photo and not msg.document and not msg.video:
                    if "📁" in msg.text:
                        await message.answer(msg.text)
                    continue
                
                # Обрабатываем медиа-группы (альбомы)
                if msg.media_group_id and msg.media_group_id not in media_group_ids:
                    media_group_ids.add(msg.media_group_id)
                    await bot.copy_message(
                        chat_id=message.chat.id,
                        from_chat_id=ARCHIVE_CHAT_ID,
                        message_id=msg.message_id
                    )
                elif not msg.media_group_id:
                    # Обычные одиночные файлы
                    await bot.copy_message(
                        chat_id=message.chat.id,
                        from_chat_id=ARCHIVE_CHAT_ID,
                        message_id=msg.message_id
                    )
            except Exception as e:
                print(f"Ошибка при пересылке сообщения {msg.message_id}: {e}")
                continue
        
        await message.answer(
            f"✅ Все файлы объекта {object_id} отправлены.",
            reply_markup=get_main_keyboard()
        )
        
    except Exception as e:
        print(f"[process_download_object_id] Ошибка: {e}")
        await message.answer(
            f"❌ Произошла ошибка при поиске файлов: {e}",
            reply_markup=get_main_keyboard()
        )
    
    await state.clear()

@router.message(InfoStates.waiting_object_id)
async def process_info_object_id(message: Message, state: FSMContext):
    object_id = message.text.strip()
    
    try:
        # Читаем файл objects.xlsx
        workbook = openpyxl.load_workbook("objects.xlsx")
        sheet = workbook.active
        
        # Ищем объект в файле
        found = False
        info_text = f"📋 Информация об объекте {object_id}:\n\n"
        
        for row in sheet.iter_rows(min_row=2, values_only=True):
            if str(row[0]).strip() == object_id:
                found = True
                # Предполагаем структуру: номер объекта, наименование потребителя, наименование объекта, адрес
                info_text += f"🏢 Потребитель: {row[1] if len(row) > 1 else 'Н/Д'}\n"
                info_text += f"📍 Объект: {row[2] if len(row) > 2 else 'Н/Д'}\n"
                info_text += f"🗺 Адрес: {row[3] if len(row) > 3 else 'Н/Д'}\n"
                break
        
        if found:
            await message.answer(info_text, reply_markup=get_main_keyboard())
        else:
            await message.answer(
                f"❌ Объект {object_id} не найден в файле objects.xlsx",
                reply_markup=get_main_keyboard()
            )
        
    except FileNotFoundError:
        await message.answer(
            "❌ Файл objects.xlsx не найден.",
            reply_markup=get_main_keyboard()
        )
    except Exception as e:
        await message.answer(
            f"❌ Ошибка при чтении файла: {e}",
            reply_markup=get_main_keyboard()
        )
    
    await state.clear()

# ========== ОБРАБОТКА ФАЙЛОВ ==========
@router.message(UploadStates.uploading_steps, F.photo | F.video | F.document)
async def handle_upload_files(message: Message, state: FSMContext):
    data = await state.get_data()
    step_index = data["step_index"]
    steps = data["steps"]
    current_step = steps[step_index]
    
    # Определяем тип файла
    file_info = {}
    if message.photo:
        file_info = {"type": "photo", "file_id": message.photo[-1].file_id}
    elif message.document:
        file_info = {"type": "document", "file_id": message.document.file_id}
    elif message.video:
        file_info = {"type": "video", "file_id": message.video.file_id}
    
    current_step["files"].append(file_info)
    
    # Удаляем предыдущее сообщение с кнопками
    if data.get("last_message_id"):
        try:
            await bot.delete_message(message.chat.id, data["last_message_id"])
        except:
            pass
    
    # Отправляем новое сообщение с кнопками (ПОСЛЕ загрузки файла has_files=True)
    msg = await message.answer(
        "Выберите действие:",
        reply_markup=get_upload_keyboard(current_step["name"], has_files=True)
    )
    
    await state.update_data(steps=steps, last_message_id=msg.message_id)

@router.message(AddPhotoStates.uploading_files, F.photo | F.video | F.document)
async def handle_addphoto_files(message: Message, state: FSMContext):
    data = await state.get_data()
    files = data["files"]
    
    # Определяем тип файла
    file_info = {}
    if message.photo:
        file_info = {"type": "photo", "file_id": message.photo[-1].file_id}
    elif message.document:
        file_info = {"type": "document", "file_id": message.document.file_id}
    elif message.video:
        file_info = {"type": "video", "file_id": message.video.file_id}
    
    files.append(file_info)
    await state.update_data(files=files)

# ========== CALLBACKS ==========
@router.callback_query(F.data == "upload_ok")
async def callback_upload_ok(callback: CallbackQuery, state: FSMContext):
    await callback.answer("✅ Шаг завершён")
    await advance_step(callback.message, state)

@router.callback_query(F.data == "upload_next")
async def callback_upload_next(callback: CallbackQuery, state: FSMContext):
    await callback.answer("➡️ Пропущено")
    await advance_step(callback.message, state, skip=True)

@router.callback_query(F.data == "upload_cancel")
async def callback_upload_cancel(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    object_id = data.get("object_id", "")
    
    await state.clear()
    
    try:
        await callback.message.delete()
    except:
        pass
    
    await callback.message.answer(
        f"❌ Загрузка для объекта {object_id} отменена.",
        reply_markup=get_main_keyboard()
    )
    await callback.answer("Отменено")

@router.callback_query(F.data == "addphoto_done")
async def callback_addphoto_done(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    object_id = data["object_id"]
    files = data["files"]
    
    if not files:
        await callback.answer("❌ Не загружено ни одного файла")
        return
    
    # Сохраняем в архив
    if object_id not in objects_data:
        objects_data[object_id] = {"steps": []}
    
    await save_to_archive(object_id, [{"name": "Дополнительные файлы", "files": files}])
    
    await state.clear()
    
    try:
        await callback.message.delete()
    except:
        pass
    
    await callback.message.answer(
        f"✅ Дополнительные файлы для объекта {object_id} сохранены ({len(files)} шт.).",
        reply_markup=get_main_keyboard()
    )
    await callback.answer("Готово ✅")

# ========== ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ==========
async def send_upload_step(message: Message, state: FSMContext):
    """Отправляет сообщение с текущим шагом загрузки"""
    data = await state.get_data()
    step_index = data["step_index"]
    steps = data["steps"]
    current_step = steps[step_index]
    
    # Удаляем предыдущее сообщение
    if data.get("last_message_id"):
        try:
            await bot.delete_message(message.chat.id, data["last_message_id"])
        except:
            pass
    
    msg = await message.answer(
        f"📸 Отправьте {current_step['name']}",
        reply_markup=get_upload_keyboard(current_step["name"], has_files=False)
    )
    
    await state.update_data(last_message_id=msg.message_id)

async def advance_step(message: Message, state: FSMContext, skip=False):
    """Переход к следующему шагу или завершение загрузки"""
    data = await state.get_data()
    step_index = data["step_index"] + 1
    steps = data["steps"]
    object_id = data["object_id"]
    
    if step_index >= len(steps):
        # Загрузка завершена
        await save_to_archive(object_id, steps)
        objects_data[object_id] = {"steps": steps}
        
        total_files = sum(len(s["files"]) for s in steps)
        
        try:
            await message.delete()
        except:
            pass
        
        await message.answer(
            f"✅ Загрузка завершена для объекта {object_id}\n"
            f"Всего файлов: {total_files}",
            reply_markup=get_main_keyboard()
        )
        
        await state.clear()
    else:
        # Переход к следующему шагу
        await state.update_data(step_index=step_index)
        await send_upload_step(message, state)

async def save_to_archive(object_id: str, steps: list):
    """Сохранение файлов в архивный чат"""
    try:
        # Отправляем информационное сообщение
        info_text = f"💾 ОБЪЕКТ #{object_id}\n🕒 {datetime.now().strftime('%d.%m.%Y %H:%M')}"
        await bot.send_message(ARCHIVE_CHAT_ID, info_text)
        
        # Отправляем файлы по шагам
        for step in steps:
            files = step["files"]
            if not files:
                continue
            
            await bot.send_message(ARCHIVE_CHAT_ID, f"📁 {step['name']}")
            
            # Отправляем файлы группами по 10
            for i in range(0, len(files), 10):
                batch = files[i:i+10]
                media = []
                
                for file_info in batch:
                    if file_info["type"] == "photo":
                        media.append(InputMediaPhoto(media=file_info["file_id"]))
                    elif file_info["type"] == "video":
                        media.append(InputMediaVideo(media=file_info["file_id"]))
                    elif file_info["type"] == "document":
                        media.append(InputMediaDocument(media=file_info["file_id"]))
                
                if media:
                    await bot.send_media_group(ARCHIVE_CHAT_ID, media)
    
    except Exception as e:
        print(f"[save_to_archive] Ошибка: {e}")

# ========== WEBHOOK ==========
async def on_startup():
    """Настройка webhook при запуске"""
    webhook_url = f"{WEBHOOK_URL}/{TOKEN}"
    await bot.delete_webhook(drop_pending_updates=True)
    await bot.set_webhook(webhook_url)
    
    # Устанавливаем команды бота
    commands = [
        BotCommand(command="start", description="Перезапустить бота"),
        BotCommand(command="photo", description="Загрузить файлы по объекту"),
        BotCommand(command="addphoto", description="Добавить файлы к объекту"),
        BotCommand(command="download", description="Скачать файлы объекта"),
        BotCommand(command="result", description="Результаты загрузок"),
        BotCommand(command="info", description="Информация об объекте")
    ]
    await bot.set_my_commands(commands)
    
    print("🚀 Бот запущен с webhook:", webhook_url)

async def on_shutdown():
    """Очистка при остановке"""
    await bot.session.close()

async def handle_webhook(request):
    """Обработка входящих обновлений от Telegram"""
    update = await request.json()
    from aiogram.types import Update
    telegram_update = Update(**update)
    await dp.feed_update(bot, telegram_update)
    return web.Response(text="OK")

async def health_check(request):
    """Проверка работоспособности бота"""
    return web.Response(text="🤖 Бот работает")

# ========== ЗАПУСК ==========
def main():
    # Подключаем роутер
    dp.include_router(router)
    
    # Создаём веб-приложение
    app = web.Application()
    app.router.add_post(f"/{TOKEN}", handle_webhook)
    app.router.add_get("/", health_check)
    
    # Запускаем startup
    app.on_startup.append(lambda app: asyncio.create_task(on_startup()))
    app.on_shutdown.append(lambda app: asyncio.create_task(on_shutdown()))
    
    # Запускаем сервер
    web.run_app(app, host="0.0.0.0", port=PORT)

if __name__ == "__main__":
    main()
