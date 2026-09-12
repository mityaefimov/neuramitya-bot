import asyncio
import os
import json
from datetime import date
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from openai import AsyncOpenAI
from dotenv import load_dotenv
import aiosqlite
from aiohttp import web

# ==========================================
# 1. ЗАГРУЗКА ПЕРЕМЕННЫХ ОКРУЖЕНИЯ
# ==========================================
load_dotenv()

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")

if not TELEGRAM_TOKEN or not GROQ_API_KEY:
    raise ValueError("Не найдены токены! Проверь переменные окружения")

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
MEMORY_PERSISTENT = "persistent"
MEMORY_INCOGNITO = "incognito"

# Лимиты для защиты
DAILY_LIMIT = 50  # Сообщений в день на пользователя
user_limits = {}  # {user_id: {"count": 0, "date": "2024-01-01"}}

# Список доступных моделей
AVAILABLE_MODELS = {
    "🧠 Qwen 27B (Рекомендуется)": "qwen/qwen3.8-27b",
    "🚀 GPT-OSS 120B (Максимальный ум)": "openai/gpt-oss-120b",
    "⚡ Groq Compound (Быстрый)": "groq/compound"
}
DEFAULT_MODEL = "qwen/qwen3.8-27b"

# Системный промпт
SYSTEM_PROMPT = {
    "role": "system",
    "content": "Ты полезный, дружелюбный и технически подкованный ИИ-ассистент. Отвечай четко, структурированно и по делу."
}

# Словарь для истории в режиме инкогнито
incognito_history = {}

# ==========================================
# 4. РАБОТА С БАЗОЙ ДАННЫХ
# ==========================================

async def init_db():
    """Инициализация базы данных с миграцией"""
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
        
        # Миграция: добавляем колонку memory_mode если её нет
        async with db.execute("PRAGMA table_info(users)") as cursor:
            columns = await cursor.fetchall()
            column_names = [col[1] for col in columns]
            
            if "memory_mode" not in column_names:
                print("⚙️ Добавляю колонку memory_mode...")
                await db.execute(
                    "ALTER TABLE users ADD COLUMN memory_mode TEXT NOT NULL DEFAULT 'persistent'"
                )
                await db.commit()
                print("✅ Миграция завершена!")

async def get_or_create_user(user_id: int):
    """Получает или создаёт пользователя"""
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute(
            "SELECT current_model, memory_mode, history FROM users WHERE user_id = ?", 
            (user_id,)
        ) as cursor:
            row = await cursor.fetchone()
            
            if row:
                return row[0], row[1], json.loads(row[2])
            else:
                default_history = json.dumps([SYSTEM_PROMPT])
                await db.execute(
                    "INSERT INTO users (user_id, current_model, memory_mode, history) VALUES (?, ?, ?, ?)",
                    (user_id, DEFAULT_MODEL, MEMORY_PERSISTENT, default_history)
                )
                await db.commit()
                return DEFAULT_MODEL, MEMORY_PERSISTENT, json.loads(default_history)

async def save_user(user_id: int, model: str, memory_mode: str, history: list):
    """Сохраняет данные пользователя"""
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute(
            "UPDATE users SET current_model = ?, memory_mode = ?, history = ? WHERE user_id = ?",
            (model, memory_mode, json.dumps(history, ensure_ascii=False), user_id)
        )
        await db.commit()

async def delete_user(user_id: int):
    """Удаляет пользователя"""
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
    prefix_persistent = "✅ " if current_mode == MEMORY_PERSISTENT else ""
    prefix_incognito = "✅ " if current_mode == MEMORY_INCOGNITO else ""
    
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"{prefix_persistent}🧠 Стандартный", callback_data="set_memory_persistent")],
        [InlineKeyboardButton(text=f"{prefix_incognito}🕵️ Инкогнито", callback_data="set_memory_incognito")]
    ])

# ==========================================
# 6. ОБРАБОТЧИКИ КОМАНД
# ==========================================

@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    user_id = message.from_user.id
    current_model, memory_mode, _ = await get_or_create_user(user_id)
    
    memory_desc = "️ **Инкогнито**" if memory_mode == MEMORY_INCOGNITO else " **Стандартный**"
    
    text = (
        f"Привет, {message.from_user.first_name}! 👋\n\n"
        f"Я твой ИИ-помощник на базе Groq.\n"
        f"🔹 Модель: `{current_model}`\n"
        f" Режим: {memory_desc}\n"
        f"🔹 Лимит: {DAILY_LIMIT} сообщений/день\n\n"
        f"📋 **Команды:**\n"
        f"/model — сменить модель\n"
        f"/memory — режим памяти\n"
        f"/clear — очистить историю\n"
        f"/delete — удалить данные\n\n"
        f"Просто напиши мне что-нибудь!"
    )
    
    await message.answer(text, reply_markup=get_model_keyboard(current_model), parse_mode="Markdown")

@dp.message(Command("model"))
async def cmd_model(message: types.Message):
    user_id = message.from_user.id
    current_model, _, _ = await get_or_create_user(user_id)
    await message.answer("Выберите модель:", reply_markup=get_model_keyboard(current_model))

@dp.message(Command("memory"))
async def cmd_memory(message: types.Message):
    user_id = message.from_user.id
    _, memory_mode, _ = await get_or_create_user(user_id)
    
    text = (
        "🔐 **Режим памяти:**\n\n"
        "🧠 **Стандартный** — сохраняю переписку навсегда\n"
        "🕵️ **Инкогнито** — не сохраняю (только пока бот включен)"
    )
    
    await message.answer(text, reply_markup=get_memory_keyboard(memory_mode), parse_mode="Markdown")

@dp.message(Command("clear"))
async def cmd_clear(message: types.Message):
    user_id = message.from_user.id
    current_model, memory_mode, _ = await get_or_create_user(user_id)
    new_history = [SYSTEM_PROMPT]
    
    if memory_mode == MEMORY_PERSISTENT:
        await save_user(user_id, current_model, memory_mode, new_history)
    else:
        incognito_history[user_id] = new_history
    
    await message.answer("🧹 История очищена!")

@dp.message(Command("delete"))
async def cmd_delete(message: types.Message):
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Да, удалить", callback_data="confirm_delete"),
         InlineKeyboardButton(text="❌ Отмена", callback_data="cancel_delete")]
    ])
    
    await message.answer(
        "⚠️ **Удалить все данные?**\nЭто необратимо!",
        reply_markup=keyboard,
        parse_mode="Markdown"
    )

# ==========================================
# 7. ОБРАБОТЧИКИ КНОПОК
# ==========================================

@dp.callback_query(F.data.startswith("set_model_"))
async def handle_model_selection(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    new_model = callback.data.replace("set_model_", "")
    
    current_model, memory_mode, _ = await get_or_create_user(user_id)
    new_history = [SYSTEM_PROMPT]
    
    if memory_mode == MEMORY_PERSISTENT:
        await save_user(user_id, new_model, memory_mode, new_history)
    else:
        incognito_history[user_id] = new_history
        await save_user(user_id, new_model, memory_mode, [SYSTEM_PROMPT])
    
    await callback.answer(f"Модель: {new_model}")
    await callback.message.edit_text(
        f"✅ Модель: `{new_model}`",
        reply_markup=get_model_keyboard(new_model),
        parse_mode="Markdown"
    )

@dp.callback_query(F.data.startswith("set_memory_"))
async def handle_memory_selection(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    new_mode = callback.data.replace("set_memory_", "")
    
    current_model, _, _ = await get_or_create_user(user_id)
    new_history = [SYSTEM_PROMPT]
    
    await save_user(user_id, current_model, new_mode, new_history)
    
    if new_mode == MEMORY_INCOGNITO and user_id in incognito_history:
        del incognito_history[user_id]
    
    mode_text = "️ Инкогнито" if new_mode == MEMORY_INCOGNITO else "💾 Стандартный"
    
    await callback.answer(f"Режим: {mode_text}")
    await callback.message.edit_text(
        f"✅ Режим: **{mode_text}**\nИстория очищена.",
        reply_markup=get_memory_keyboard(new_mode),
        parse_mode="Markdown"
    )

@dp.callback_query(F.data.in_(["confirm_delete", "cancel_delete"]))
async def handle_delete_confirmation(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    
    if callback.data == "confirm_delete":
        await delete_user(user_id)
        if user_id in incognito_history:
            del incognito_history[user_id]
        await callback.answer("Удалено")
        await callback.message.edit_text("✅ Все данные удалены!")
    else:
        await callback.answer("Отменено")
        await callback.message.edit_text("❌ Отменено.")

# ==========================================
# 8. ОБРАБОТКА СООБЩЕНИЙ
# ==========================================

@dp.message(F.text)
async def handle_message(message: types.Message):
    user_id = message.from_user.id
    user_text = message.text

    if user_text.startswith('/'):
        return

    # Проверка лимита
    today = str(date.today())
    if user_id not in user_limits or user_limits[user_id]["date"] != today:
        user_limits[user_id] = {"count": 0, "date": today}
    
    if user_limits[user_id]["count"] >= DAILY_LIMIT:
        await message.answer(f"⏳ Лимит {DAILY_LIMIT} сообщений исчерпан. Приходи завтра!")
        return
    
    user_limits[user_id]["count"] += 1

    # Загрузка данных
    current_model, memory_mode, history = await get_or_create_user(user_id)

    if memory_mode == MEMORY_INCOGNITO:
        if user_id not in incognito_history:
            incognito_history[user_id] = [SYSTEM_PROMPT]
        history = incognito_history[user_id]

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

        if len(history) > MAX_HISTORY_LENGTH + 1:
            history = [history[0]] + history[-(MAX_HISTORY_LENGTH):]

        if memory_mode == MEMORY_PERSISTENT:
            await save_user(user_id, current_model, memory_mode, history)
        else:
            incognito_history[user_id] = history

        await message.answer(ai_reply)

    except Exception as e:
        error_text = str(e)
        print(f"❌ Groq Error: {error_text}")
        await message.answer(f"⚠️ Ошибка:\n`{error_text}`", parse_mode="Markdown")

# ==========================================
# 9. WEBHOOK И ЗАПУСК
# ==========================================

WEBHOOK_PATH = "/webhook"
WEBHOOK_HOST = os.getenv("RENDER_EXTERNAL_URL", "https://neuramitya-bot.onrender.com")
WEBHOOK_URL = f"{WEBHOOK_HOST}{WEBHOOK_PATH}"

async def on_startup(app):
    await init_db()
    await bot.set_webhook(WEBHOOK_URL)
    print(f"✅ Webhook установлен: {WEBHOOK_URL}")
    print("✅ Бот запущен!")

async def on_shutdown(app):
    await bot.delete_webhook()
    print("🛑 Бот остановлен")

async def webhook_handler(request):
    if request.content_type == "application/json":
        json_data = await request.json()
        update = types.Update(**json_data)
        await dp.feed_update(bot, update)
        return web.Response(text="OK")
    return web.Response(status=400)

def create_app():
    app = web.Application()
    app.router.add_post(WEBHOOK_PATH, webhook_handler)
    app.on_startup.append(on_startup)
    app.on_shutdown.append(on_shutdown)
    return app

if __name__ == "__main__":
    web.run_app(create_app(), host="0.0.0.0", port=8080)