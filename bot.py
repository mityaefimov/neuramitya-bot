import asyncio
import os
import json
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from openai import AsyncOpenAI
from dotenv import load_dotenv
import aiosqlite

# ==========================================
# 1. ЗАГРУЗКА ПЕРЕМЕННЫХ ОКРУЖЕНИЯ
# ==========================================
load_dotenv()

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")

if not TELEGRAM_TOKEN or not GROQ_API_KEY:
    raise ValueError("Не найдены токены! Проверь файл .env")

# ==========================================
# 2. ИНИЦИАЛИЗАЦИЯ
# ==========================================
bot = Bot(token=TELEGRAM_TOKEN)
dp = Dispatcher()

client = AsyncOpenAI(
    base_url="https://api.groq.com/openai/v1",
    api_key=GROQ_API_KEY,
)

# ==========================================
# 3. НАСТРОЙКИ
# ==========================================
MAX_HISTORY_LENGTH = 30
DB_NAME = "bot_database.db"

# Режимы памяти
MEMORY_PERSISTENT = "persistent"   # Память сохраняется в БД
MEMORY_INCOGNITO = "incognito"     # Память только в оперативке

# Список доступных моделей
AVAILABLE_MODELS = {
    "🧠 Qwen 27B (Рекомендуется)": "qwen/qwen3.8-27b",
    "🚀 GPT-OSS 120B (Максимальный ум)": "openai/gpt-oss-120b",
    "⚡ Groq Compound (Быстрый и сбалансированный)": "groq/compound"
}
DEFAULT_MODEL = "qwen/qwen3.8-27b"

# Системный промпт (одинаковый для всех)
SYSTEM_PROMPT = {
    "role": "system",
    "content": "Ты полезный, дружелюбный и технически подкованный ИИ-ассистент. Отвечай четко, структурированно и по делу. Если просят код, пиши на Python."
}

# Словарь для хранения истории в режиме "инкогнито" (в оперативной памяти)
incognito_history = {}

# ==========================================
# 4. РАБОТА С БАЗОЙ ДАННЫХ
# ==========================================

async def init_db():
    """Создаёт таблицу пользователей с поддержкой режима памяти"""
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                current_model TEXT NOT NULL,
                memory_mode TEXT NOT NULL,
                history TEXT NOT NULL
            )
        """)
        await db.commit()

async def get_or_create_user(user_id: int):
    """Возвращает (model, memory_mode, history) для пользователя"""
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute(
            "SELECT current_model, memory_mode, history FROM users WHERE user_id = ?", 
            (user_id,)
        ) as cursor:
            row = await cursor.fetchone()
            
            if row:
                return row[0], row[1], json.loads(row[2])
            else:
                # Новый пользователь — создаём запись
                default_history = json.dumps([SYSTEM_PROMPT])
                await db.execute(
                    "INSERT INTO users (user_id, current_model, memory_mode, history) VALUES (?, ?, ?, ?)",
                    (user_id, DEFAULT_MODEL, MEMORY_PERSISTENT, default_history)
                )
                await db.commit()
                return DEFAULT_MODEL, MEMORY_PERSISTENT, json.loads(default_history)

async def save_user(user_id: int, model: str, memory_mode: str, history: list):
    """Сохраняет данные пользователя в базу"""
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute(
            "UPDATE users SET current_model = ?, memory_mode = ?, history = ? WHERE user_id = ?",
            (model, memory_mode, json.dumps(history, ensure_ascii=False), user_id)
        )
        await db.commit()

async def delete_user(user_id: int):
    """Полностью удаляет пользователя из базы данных"""
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("DELETE FROM users WHERE user_id = ?", (user_id,))
        await db.commit()

# ==========================================
# 5. КЛАВИАТУРЫ
# ==========================================

def get_model_keyboard(current_model_id: str) -> InlineKeyboardMarkup:
    keyboard = InlineKeyboardMarkup(inline_keyboard=[])
    for name, model_id in AVAILABLE_MODELS.items():
        prefix = "✅ " if model_id == current_model_id else ""
        keyboard.inline_keyboard.append([
            InlineKeyboardButton(text=f"{prefix}{name}", callback_data=f"set_model_{model_id}")
        ])
    return keyboard

def get_memory_keyboard(current_mode: str) -> InlineKeyboardMarkup:
    """Клавиатура для выбора режима памяти"""
    prefix_persistent = "✅ " if current_mode == MEMORY_PERSISTENT else ""
    prefix_incognito = "✅ " if current_mode == MEMORY_INCOGNITO else ""
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text=f"{prefix_persistent}🧠 Стандартный (память сохраняется)", 
            callback_data="set_memory_persistent"
        )],
        [InlineKeyboardButton(
            text=f"{prefix_incognito}🕵️ Инкогнито (не сохранять)", 
            callback_data="set_memory_incognito"
        )]
    ])
    return keyboard

# ==========================================
# 6. ОБРАБОТЧИКИ КОМАНД
# ==========================================

@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    user_id = message.from_user.id
    current_model, memory_mode, _ = await get_or_create_user(user_id)
    
    # Формируем описание режима памяти
    if memory_mode == MEMORY_INCOGNITO:
        memory_desc = "🕵️ **Режим Инкогнито** — переписка НЕ сохраняется в базу данных"
    else:
        memory_desc = "💾 **Стандартный режим** — переписка сохраняется в базу данных"
    
    text = (
        f"Привет, {message.from_user.first_name}! 👋\n\n"
        f"Я твой ИИ-помощник на базе Groq.\n"
        f"🔹 Текущая модель: `{current_model}`\n"
        f"🔹 В каждом диалоге я анализирую последние {MAX_HISTORY_LENGTH} сообщений.\n"
        f"🔐 {memory_desc}\n\n"
        f"📋 **Команды:**\n"
        f"/model — сменить модель ИИ\n"
        f"/memory — сменить режим памяти\n"
        f"/clear — очистить историю\n"
        f"/delete — удалить все данные\n\n"
        f"Нажми кнопку ниже, чтобы сменить модель ИИ, или просто напиши мне что-нибудь!"
    )
    
    await message.answer(text, reply_markup=get_model_keyboard(current_model), parse_mode="Markdown")

@dp.message(Command("model"))
async def cmd_model(message: types.Message):
    """Показать меню выбора модели"""
    user_id = message.from_user.id
    current_model, _, _ = await get_or_create_user(user_id)
    await message.answer(
        "Выберите модель для ответов:", 
        reply_markup=get_model_keyboard(current_model)
    )

@dp.message(Command("memory"))
async def cmd_memory(message: types.Message):
    """Показать меню выбора режима памяти"""
    user_id = message.from_user.id
    _, memory_mode, _ = await get_or_create_user(user_id)
    
    text = (
        "🔐 **Выбери режим памяти:**\n\n"
        "🧠 **Стандартный** — я буду сохранять нашу переписку в базу данных. "
        "Даже если меня перезапустят, я всё вспомню.\n\n"
        "🕵️ **Инкогнито** — я НЕ буду сохранять переписку в базу данных. "
        "Память работает только пока бот включен. "
        "После перезапуска всё забудется. Максимальная приватность.\n\n"
        "⚠️ При смене режима текущая история будет очищена."
    )
    
    await message.answer(text, reply_markup=get_memory_keyboard(memory_mode), parse_mode="Markdown")

@dp.message(Command("clear"))
async def cmd_clear(message: types.Message):
    """Очистка истории"""
    user_id = message.from_user.id
    current_model, memory_mode, _ = await get_or_create_user(user_id)
    
    new_history = [SYSTEM_PROMPT]
    
    if memory_mode == MEMORY_PERSISTENT:
        # Сохраняем очищенную историю в БД
        await save_user(user_id, current_model, memory_mode, new_history)
    else:
        # Очищаем оперативную память для инкогнито
        if user_id in incognito_history:
            incognito_history[user_id] = new_history
    
    await message.answer("🧹 История переписки очищена! Давай начнем с чистого листа.")

@dp.message(Command("delete"))
async def cmd_delete(message: types.Message):
    """Команда для полного удаления данных пользователя"""
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✅ Да, удалить всё", callback_data="confirm_delete"),
            InlineKeyboardButton(text="❌ Отмена", callback_data="cancel_delete")
        ]
    ])
    
    await message.answer(
        "⚠️ **Внимание!**\n\n"
        "Это действие **полностью удалит** все твои данные:\n"
        "• Всю историю переписки\n"
        "• Выбранную модель\n"
        "• Настройки памяти\n\n"
        "Это действие **необратимо**. Ты уверен?",
        reply_markup=keyboard,
        parse_mode="Markdown"
    )

# ==========================================
# 7. ОБРАБОТЧИКИ КНОПОК
# ==========================================

@dp.callback_query(F.data.startswith("set_model_"))
async def handle_model_selection(callback: types.CallbackQuery):
    """Обработка нажатия на кнопку выбора модели"""
    user_id = callback.from_user.id
    new_model = callback.data.replace("set_model_", "")
    
    current_model, memory_mode, _ = await get_or_create_user(user_id)
    new_history = [SYSTEM_PROMPT]
    
    if memory_mode == MEMORY_PERSISTENT:
        await save_user(user_id, new_model, memory_mode, new_history)
    else:
        # В инкогнито обновляем оперативную память
        incognito_history[user_id] = new_history
        # В БД тоже обновим модель (но не историю)
        await save_user(user_id, new_model, memory_mode, [SYSTEM_PROMPT])
    
    await callback.answer(f"Модель изменена на: {new_model}")
    await callback.message.edit_text(
        f"✅ Модель успешно изменена!\nТекущая модель: `{new_model}`\n\nМожешь продолжать общение.",
        reply_markup=get_model_keyboard(new_model),
        parse_mode="Markdown"
    )

@dp.callback_query(F.data.startswith("set_memory_"))
async def handle_memory_selection(callback: types.CallbackQuery):
    """Обработка выбора режима памяти"""
    user_id = callback.from_user.id
    new_mode = callback.data.replace("set_memory_", "")
    
    current_model, _, _ = await get_or_create_user(user_id)
    new_history = [SYSTEM_PROMPT]
    
    # Сохраняем новый режим и очищаем историю
    await save_user(user_id, current_model, new_mode, new_history)
    
    # Если перешли в инкогнито — очищаем оперативную память
    if new_mode == MEMORY_INCOGNITO and user_id in incognito_history:
        del incognito_history[user_id]
    
    mode_text = "🕵️ Инкогнито" if new_mode == MEMORY_INCOGNITO else "💾 Стандартный"
    
    await callback.answer(f"Режим изменен: {mode_text}")
    await callback.message.edit_text(
        f"✅ Режим памяти изменён!\n\n"
        f"Текущий режим: **{mode_text}**\n\n"
        f"История очищена. Можешь продолжать общение.",
        reply_markup=get_memory_keyboard(new_mode),
        parse_mode="Markdown"
    )

@dp.callback_query(F.data.in_(["confirm_delete", "cancel_delete"]))
async def handle_delete_confirmation(callback: types.CallbackQuery):
    """Обработка подтверждения удаления"""
    user_id = callback.from_user.id
    
    if callback.data == "confirm_delete":
        await delete_user(user_id)
        # Также очищаем оперативную память, если была
        if user_id in incognito_history:
            del incognito_history[user_id]
        await callback.answer("Данные удалены")
        await callback.message.edit_text(
            "✅ Все твои данные успешно удалены из базы данных.\n\n"
            "Если захочешь продолжить общение, просто напиши мне — я начну с чистого листа."
        )
    else:
        await callback.answer("Удаление отменено")
        await callback.message.edit_text("❌ Удаление отменено. Все данные сохранены.")

# ==========================================
# 8. ОБРАБОТКА СООБЩЕНИЙ
# ==========================================

@dp.message(F.text)
async def handle_message(message: types.Message):
    """Обработка текстовых сообщений"""
    user_id = message.from_user.id
    user_text = message.text

    if user_text.startswith('/'):
        return

    # Загружаем данные пользователя
    current_model, memory_mode, history = await get_or_create_user(user_id)

    # Если режим инкогнито — используем оперативную память вместо БД
    if memory_mode == MEMORY_INCOGNITO:
        if user_id not in incognito_history:
            incognito_history[user_id] = [SYSTEM_PROMPT]
        history = incognito_history[user_id]

    # Добавляем сообщение пользователя
    history.append({"role": "user", "content": user_text})

    await message.bot.send_chat_action(message.chat.id, "typing")

    try:
        response = await client.chat.completions.create(
            model=current_model,
            messages=history,
            temperature=0.7,
            max_tokens=1500
        )
        
        ai_reply = response.choices[0].message.content
        history.append({"role": "assistant", "content": ai_reply})

        # Обрезаем историю, если она слишком длинная
        if len(history) > MAX_HISTORY_LENGTH + 1:
            history = [history[0]] + history[-(MAX_HISTORY_LENGTH):]

        # Сохраняем в зависимости от режима
        if memory_mode == MEMORY_PERSISTENT:
            await save_user(user_id, current_model, memory_mode, history)
        else:
            # В инкогнито обновляем только оперативную память
            incognito_history[user_id] = history

        await message.answer(ai_reply)

    except Exception as e:
        error_text = str(e)
        print(f"Ошибка Groq: {error_text}")
        await message.answer(f"⚠️ Ошибка от нейросети:\n\n`{error_text}`", parse_mode="Markdown")

# ==========================================
# 9. ЗАПУСК
# ==========================================

async def main():
    await init_db()
    print("✅ База данных готова. Бот запущен и ожидает сообщения...")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())