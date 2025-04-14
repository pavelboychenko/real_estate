import os
import openai
import logging
import asyncio
import tempfile
from dotenv import load_dotenv
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, KeyboardButton
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, CallbackQueryHandler, ContextTypes, filters, ConversationHandler
import speech_recognition as sr
from pydub import AudioSegment
from gtts import gTTS

# Настройка логирования
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

# Загрузка переменных окружения
load_dotenv()

# Настройка API ключей
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
ADMIN_GROUP_ID = os.getenv("ADMIN_GROUP_ID")  # ID закрытой группы для брокеров

# Инициализация OpenAI
openai.api_key = OPENAI_API_KEY

# Состояния для ConversationHandler
MAIN_MENU, PROPERTY_SELECTION, DISTRICTS_INFO, MARKET_TRENDS, HISTORY, CONTACT_INFO = range(6)

# Системный промпт для ChatGPT
SYSTEM_PROMPT = """
Ты - эксперт по элитной недвижимости Москвы и Подмосковья. Твоя задача - помочь клиентам подобрать идеальный вариант жилья.
Ты знаешь всё о престижных районах, элитных жилых комплексах, особенностях рынка и ценовых трендах.

Информация, которую ты должен собрать для подбора:
1. Бюджет клиента
2. Предпочтительные районы
3. Тип недвижимости (квартира, дом, пентхаус и т.д.)
4. Необходимая площадь
5. Особые пожелания (вид, инфраструктура, безопасность)

Всегда будь вежлив, профессионален и предлагай конкретные варианты с описанием преимуществ.
В конце каждого ответа предлагай клиенту оставить контактные данные для связи с брокером.
"""

# Функция для создания красивой клавиатуры главного меню
def get_main_menu_keyboard():
    keyboard = [
        [InlineKeyboardButton("🏠 Подобрать недвижимость", callback_data="select_property")],
        [InlineKeyboardButton("🗺️ Узнать о районах", callback_data="districts_info")],
        [InlineKeyboardButton("📈 Тренды рынка", callback_data="market_trends")],
        [InlineKeyboardButton("📋 История запросов", callback_data="history")],
        [InlineKeyboardButton("👨‍💼 Связаться с брокером", callback_data="contact_broker")]
    ]
    return InlineKeyboardMarkup(keyboard)

# Функция для создания клавиатуры "Назад"
def get_back_keyboard():
    keyboard = [
        [InlineKeyboardButton("◀️ Вернуться в главное меню", callback_data="back_to_main")],
        [InlineKeyboardButton("🎙️ Голосовой ответ", callback_data="voice_response")],
        [InlineKeyboardButton("👨‍💼 Связаться с брокером", callback_data="contact_broker")]
    ]
    return InlineKeyboardMarkup(keyboard)

# Функция для создания клавиатуры контактной формы
def get_contact_keyboard():
    keyboard = [[KeyboardButton("📱 Поделиться контактом", request_contact=True)]]
    return ReplyKeyboardMarkup(keyboard, one_time_keyboard=True, resize_keyboard=True)

# Функция для преобразования голосового сообщения в текст
async def voice_to_text(voice_file_path):
    try:
        # Конвертируем голосовое сообщение в формат WAV
        audio = AudioSegment.from_file(voice_file_path)
        wav_file = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
        audio.export(wav_file.name, format="wav")
        
        # Распознаем речь
        recognizer = sr.Recognizer()
        with sr.AudioFile(wav_file.name) as source:
            audio_data = recognizer.record(source)
            text = recognizer.recognize_google(audio_data, language="ru-RU")
        
        # Удаляем временные файлы
        os.unlink(wav_file.name)
        return text
    except Exception as e:
        logger.error(f"Ошибка при распознавании речи: {e}")
        return None

# Функция для преобразования текста в голосовое сообщение
async def text_to_voice(text):
    try:
        tts = gTTS(text=text, lang="ru")
        voice_file = tempfile.NamedTemporaryFile(suffix=".mp3", delete=False)
        tts.save(voice_file.name)
        return voice_file.name
    except Exception as e:
        logger.error(f"Ошибка при создании голосового сообщения: {e}")
        return None

# Функция для отправки сообщения в группу брокеров
async def send_to_broker_group(context: ContextTypes.DEFAULT_TYPE, message: str, user_info: dict = None):
    try:
        if user_info:
            user_text = f"👤 Новый пользователь:\nID: {user_info['id']}\nИмя: {user_info['name']}\nUsername: @{user_info.get('username', 'Нет')}"
            await context.bot.send_message(chat_id=ADMIN_GROUP_ID, text=user_text)
        else:
            await context.bot.send_message(chat_id=ADMIN_GROUP_ID, text=message)
    except Exception as e:
        logger.error(f"Ошибка при отправке сообщения в группу брокеров: {e}")

# Функция для создания саммари запросов пользователя
async def create_summary(user_message):
    try:
        response = openai.ChatCompletion.create(
            model="gpt-3.5-turbo",
            messages=[
                {"role": "system", "content": "Создай краткое саммари запроса клиента о недвижимости."},
                {"role": "user", "content": user_message}
            ],
            max_tokens=150,
            temperature=0.5
        )
        return response.choices[0].message.content
    except Exception as e:
        logger.error(f"Ошибка при создании саммари: {e}")
        return f"Запрос клиента: {user_message[:100]}..."

# Обработчики команд
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Инициализация истории запросов пользователя
    if 'history' not in context.user_data:
        context.user_data['history'] = []
    
    # Отправляем информацию о новом пользователе в группу брокеров
    user = update.effective_user
    user_info = {
        'id': user.id,
        'name': f"{user.first_name} {user.last_name if user.last_name else ''}",
        'username': user.username
    }
    await send_to_broker_group(context, "", user_info)
    
    await update.message.reply_text(
        "👋 Добро пожаловать в бот по подбору элитной недвижимости Москвы и Подмосковья! "
        "Я помогу вам найти идеальный вариант жилья. Расскажите о ваших предпочтениях, "
        "и я предложу подходящие варианты."
    )
    
    await update.message.reply_text("Выберите, чем я могу вам помочь:", reply_markup=get_main_menu_keyboard())
    return MAIN_MENU

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    if query.data == "select_property":
        await query.message.reply_text(
            "🏠 Давайте подберем для вас идеальную недвижимость! "
            "Расскажите, пожалуйста, о ваших предпочтениях:\n"
            "- Какой у вас бюджет?\n"
            "- В каких районах хотели бы жить?\n"
            "- Какой тип недвижимости вас интересует?\n"
            "- Какая площадь вам необходима?\n"
            "- Есть ли особые пожелания?\n\n"
            "Вы можете отправить текстовое или голосовое сообщение.",
            reply_markup=get_back_keyboard()
        )
        return PROPERTY_SELECTION
        
    elif query.data == "districts_info":
        await query.message.reply_text(
            "🗺️ Какой район Москвы или Подмосковья вас интересует? "
            "Я могу рассказать о престижных локациях, инфраструктуре и особенностях.\n\n"
            "Вы можете отправить текстовое или голосовое сообщение.",
            reply_markup=get_back_keyboard()
        )
        return DISTRICTS_INFO
        
    elif query.data == "market_trends":
        await query.message.reply_text(
            "📈 Хотите узнать о текущих трендах рынка элитной недвижимости? "
            "Спрашивайте о ценах, спросе, новых проектах или инвестиционных перспективах.\n\n"
            "Вы можете отправить текстовое или голосовое сообщение.",
            reply_markup=get_back_keyboard()
        )
        return MARKET_TRENDS
        
    elif query.data == "history":
        if not context.user_data.get('history', []):
            await query.message.reply_text(
                "📋 У вас пока нет истории запросов.",
                reply_markup=get_back_keyboard()
            )
        else:
            history_text = "📋 Ваша история запросов:\n\n"
            for i, (q, a) in enumerate(context.user_data['history'][-10:], 1):
                history_text += f"{i}. Вопрос: {q}\nОтвет: {a[:100]}...\n\n"
            
            await query.message.reply_text(
                history_text,
                reply_markup=get_back_keyboard()
            )
        return HISTORY
        
    elif query.data == "contact_broker":
        await query.message.reply_text(
            "👨‍💼 Для связи с персональным брокером, пожалуйста, оставьте свои контактные данные.",
            reply_markup=get_contact_keyboard()
        )
        return CONTACT_INFO
        
    elif query.data == "voice_response":
        # Получаем последний ответ бота из истории
        if context.user_data.get('history', []):
            last_response = context.user_data['history'][-1][1]
            voice_file = await text_to_voice(last_response)
            if voice_file:
                await query.message.reply_voice(voice=open(voice_file, 'rb'), reply_markup=get_back_keyboard())
                os.unlink(voice_file)  # Удаляем временный файл
            else:
                await query.message.reply_text(
                    "Извините, не удалось создать голосовое сообщение.",
                    reply_markup=get_back_keyboard()
                )
        else:
            await query.message.reply_text(
                "У вас пока нет истории сообщений для озвучивания.",
                reply_markup=get_back_keyboard()
            )
        return MAIN_MENU
        
    elif query.data == "back_to_main":
        await query.message.reply_text(
            "Вернулись в главное меню. Чем я могу вам помочь?",
            reply_markup=get_main_menu_keyboard()
        )
        return MAIN_MENU

async def handle_property_selection(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_message = update.message.text
    await process_message(update, context, user_message)
    
    # Создаем и отправляем саммари в группу брокеров
    summary = await create_summary(user_message)
    user = update.effective_user
    broker_message = f"💬 Запрос от пользователя {user.first_name} (ID: {user.id}):\n\n{summary}"
    await send_to_broker_group(context, broker_message)
    
    return PROPERTY_SELECTION

async def handle_districts_info(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_message = update.message.text
    await process_message(update, context, user_message)
    
    # Создаем и отправляем саммари в группу брокеров
    summary = await create_summary(user_message)
    user = update.effective_user
    broker_message = f"💬 Запрос от пользователя {user.first_name} (ID: {user.id}):\n\n{summary}"
    await send_to_broker_group(context, broker_message)
    
    return DISTRICTS_INFO

async def handle_market_trends(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_message = update.message.text
    await process_message(update, context, user_message)
    
    # Создаем и отправляем саммари в группу брокеров
    summary = await create_summary(user_message)
    user = update.effective_user
    broker_message = f"💬 Запрос от пользователя {user.first_name} (ID: {user.id}):\n\n{summary}"
    await send_to_broker_group(context, broker_message)
    
    return MARKET_TRENDS

async def handle_voice_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Скачиваем голосовое сообщение
    voice = await update.message.voice.get_file()
    voice_file = tempfile.NamedTemporaryFile(suffix=".ogg", delete=False)
    await voice.download_to_drive(voice_file.name)
    
    # Конвертируем голосовое сообщение в текст
    text = await voice_to_text(voice_file.name)
    os.unlink(voice_file.name)  # Удаляем временный файл
    
    if text:
        # Отправляем распознанный текст пользователю
        await update.message.reply_text(f"🎙️ Распознано: {text}")
        
        # Обрабатываем текст как обычное сообщение
        await process_message(update, context, text)
        
        # Создаем и отправляем саммари в группу брокеров
        summary = await create_summary(text)
        user = update.effective_user
        broker_message = f"🎙️ Голосовой запрос от пользователя {user.first_name} (ID: {user.id}):\n\n{summary}"
        await send_to_broker_group(context, broker_message)
        
        # Определяем текущее состояние и возвращаем его
        if context.user_data.get('current_state') == PROPERTY_SELECTION:
            return PROPERTY_SELECTION
        elif context.user_data.get('current_state') == DISTRICTS_INFO:
            return DISTRICTS_INFO
        elif context.user_data.get('current_state') == MARKET_TRENDS:
            return MARKET_TRENDS
        else:
            return MAIN_MENU
    else:
        await update.message.reply_text(
            "Извините, не удалось распознать ваше голосовое сообщение. Пожалуйста, попробуйте еще раз или отправьте текстовое сообщение.",
            reply_markup=get_back_keyboard()
        )
        return context.user_data.get('current_state', MAIN_MENU)

async def handle_contact(update: Update, context: ContextTypes.DEFAULT_TYPE):
    contact = update.message.contact
    user = update.effective_user
    
    # Сохраняем контакт в данных пользователя
    context.user_data['contact'] = {
        'phone': contact.phone_number,
        'first_name': contact.first_name,
        'last_name': contact.last_name if contact.last_name else ""
    }
    
    # Отправляем контакт в группу брокеров
    broker_message = (
        f"📱 Новая заявка на связь с брокером!\n"
        f"Пользователь: {user.first_name} {user.last_name if user.last_name else ''}\n"
        f"ID: {user.id}\n"
        f"Телефон: {contact.phone_number}"
    )
    await send_to_broker_group(context, broker_message)
    
    await update.message.reply_text(
        "Спасибо! Ваши контактные данные получены. Наш брокер свяжется с вами в ближайшее время.",
        reply_markup=get_main_menu_keyboard()
    )
    return MAIN_MENU

async def process_message(update: Update, context: ContextTypes.DEFAULT_TYPE, user_message=None):
    if not user_message:
        user_message = update.message.text
    
    # Сохраняем текущее состояние для обработки голосовых сообщений
    if update.callback_query:
        if update.callback_query.data == "select_property":
            context.user_data['current_state'] = PROPERTY_SELECTION
        elif update.callback_query.data == "districts_info":
            context.user_data['current_state'] = DISTRICTS_INFO
        elif update.callback_query.data == "market_trends":
            context.user_data['current_state'] = MARKET_TRENDS
    
    # Получаем ответ от ChatGPT
    try:
        response = openai.ChatCompletion.create(
            model="gpt-4",  # или другая подходящая модель
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_message}
            ],
            max_tokens=1000,
            temperature=0.7
        )
        
        bot_response = response.choices[0].message.content
        
        # Сохраняем запрос и ответ в истории
        if 'history' not in context.user_data:
            context.user_data['history'] = []
        context.user_data['history'].append((user_message, bot_response))
        
        # Отправляем ответ с кнопками
        await update.message.reply_text(bot_response, reply_markup=get_back_keyboard())
    except Exception as e:
        logger.error(f"Ошибка при обработке запроса: {e}")
        await update.message.reply_text(
            "Извините, произошла ошибка при обработке вашего запроса. Пожалуйста, попробуйте еще раз.",
            reply_markup=get_back_keyboard()
        )

async def fallback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Извините, я не понял ваш запрос. Пожалуйста, воспользуйтесь меню.",
        reply_markup=get_main_menu_keyboard()
    )
    return MAIN_MENU

# Основная функция
def main():
    # Создаем приложение
    application = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
    
    # Создаем ConversationHandler
    conv_handler = ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states={
            MAIN_MENU: [
                CallbackQueryHandler(button_handler)
            ],
            PROPERTY_SELECTION: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_property_selection),
                MessageHandler(filters.VOICE, handle_voice_message),
                CallbackQueryHandler(button_handler)
            ],
            DISTRICTS_INFO: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_districts_info),
                MessageHandler(filters.VOICE, handle_voice_message),
                CallbackQueryHandler(button_handler)
            ],
            MARKET_TRENDS: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_market_trends),
                MessageHandler(filters.VOICE, handle_voice_message),
                CallbackQueryHandler(button_handler)
            ],
            HISTORY: [
                CallbackQueryHandler(button_handler)
            ],
            CONTACT_INFO: [
                MessageHandler(filters.CONTACT, handle_contact),
                CallbackQueryHandler(button_handler)
            ]
        },
        fallbacks=[MessageHandler(filters.ALL, fallback)]
    )
    
    application.add_handler(conv_handler)
    
    # Запускаем бота
    logger.info("Бот запущен")
    application.run_polling()

if __name__ == "__main__":
    main()
