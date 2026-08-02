import json
import random
import asyncio
import logging
import os
from typing import Dict
from dotenv import load_dotenv
load_dotenv()   # ищет файл .env в текущей папке и загружает его

from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery, Message
from aiogram.fsm.storage.memory import MemoryStorage

# -------------------- Настройки --------------------
BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN is not set in environment variables")
logging.basicConfig(level=logging.INFO)

# -------------------- Загрузка данных из JSON --------------------
with open("realities.json", "r", encoding="utf-8") as f:
    DATA = json.load(f)

TOPICS = DATA["topics"]
TOPICS_DICT = {topic["name"]: topic["questions"] for topic in TOPICS}

# -------------------- FSM (состояния) --------------------
class GameStates(StatesGroup):
    choosing_topic = State()
    choosing_question_count = State()
    playing = State()
    finished = State()

# -------------------- Хранилище сессий пользователей --------------------
user_sessions: Dict[int, Dict] = {}

def get_user_session(user_id: int) -> Dict:
    if user_id not in user_sessions:
        user_sessions[user_id] = {
            "topic": None,
            "questions": [],
            "current_index": 0,
            "total": 0,
            "correct": 0,
            "current_question_data": None,
            "current_options": [],
            "current_right": -1,
            "swap": False,
        }
    return user_sessions[user_id]

# -------------------- Вспомогательные функции --------------------
def get_random_options(correct_idx, all_questions, count=5):
    n = len(all_questions)
    if n <= count:
        indices = list(range(n))
        random.shuffle(indices)
        return indices
    indices = [correct_idx]
    pool = list(range(n))
    pool.remove(correct_idx)
    random.shuffle(pool)
    indices.extend(pool[:count-1])
    random.shuffle(indices)
    return indices

# -------------------- Инициализация бота --------------------
storage = MemoryStorage()
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(storage=storage)

# -------------------- Обработчики команд --------------------
@dp.message(Command("start"))
async def cmd_start(message: Message, state: FSMContext):
    user_id = message.from_user.id
    if user_id in user_sessions:
        del user_sessions[user_id]

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=topic["name"], callback_data=f"topic_{i}")]
            for i, topic in enumerate(TOPICS)
        ]
    )
    await message.answer(
        "Добро пожаловать в викторину «LearnTheBase»!\nВыберите тему:",
        reply_markup=keyboard
    )
    await state.set_state(GameStates.choosing_topic)

@dp.callback_query(StateFilter(GameStates.choosing_topic), F.data.startswith("topic_"))
async def process_topic_selection(callback: CallbackQuery, state: FSMContext):
    topic_index = int(callback.data.split("_")[1])
    topic_name = TOPICS[topic_index]["name"]
    questions = TOPICS_DICT[topic_name]

    user_id = callback.from_user.id
    session = get_user_session(user_id)
    session["topic"] = questions
    session["total"] = 0

    await callback.message.edit_text(
        f"Вы выбрали тему: {topic_name}\n"
        f"В теме {len(questions)} вопросов.\n"
        "Теперь введите количество вопросов (число):"
    )
    await state.set_state(GameStates.choosing_question_count)
    await callback.answer()

@dp.message(StateFilter(GameStates.choosing_question_count))
async def process_question_count(message: Message, state: FSMContext):
    user_id = message.from_user.id
    session = get_user_session(user_id)
    questions = session["topic"]
    if not questions:
        await message.answer("Ошибка: тема не выбрана. Начните заново командой /start")
        await state.clear()
        return

    text = message.text.strip()
    if not text.isdigit():
        await message.answer("Пожалуйста, введите целое положительное число.")
        return

    count = int(text)
    if count <= 0:
        await message.answer("Количество вопросов должно быть больше 0.")
        return
    if count > len(questions):
        count = len(questions)

    indices = list(range(len(questions)))
    random.shuffle(indices)
    session["questions"] = indices[:count]
    session["total"] = count
    session["current_index"] = 0
    session["correct"] = 0

    await message.answer(f"Начинаем викторину! Всего вопросов: {count}.")
    await state.set_state(GameStates.playing)
    await send_next_question(message, state)

# -------------------- Функция отправки вопроса --------------------
async def send_next_question(message: Message, state: FSMContext):
    user_id = message.chat.id
    session = get_user_session(user_id)

    if session["current_index"] >= session["total"]:
        await show_final_result(message, state, user_id)
        return

    questions = session["topic"]
    idx = session["questions"][session["current_index"]]
    question = questions[idx]

    options_indices = get_random_options(idx, questions, count=5)
    swap = random.choice([True, False])
    session["swap"] = swap
    session["current_question_data"] = question
    session["current_options"] = options_indices
    right_pos = options_indices.index(idx)
    session["current_right"] = right_pos

    if swap:
        question_text = question["name"]
        option_texts = [questions[opt_idx]["description"] for opt_idx in options_indices]
    else:
        question_text = question["description"]
        option_texts = [questions[opt_idx]["name"] for opt_idx in options_indices]

    options_message = "\n".join([f"{i+1}. {text}" for i, text in enumerate(option_texts)])
    full_message = (
        f"Вопрос {session['current_index']+1} из {session['total']}:\n\n"
        f"{question_text}\n\n"
        f"Варианты:\n{options_message}"
    )

    buttons = [[InlineKeyboardButton(text=str(i+1), callback_data=str(i))] for i in range(len(option_texts))]
    keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)

    await message.answer(full_message, reply_markup=keyboard)

# -------------------- Обработка ответа --------------------
@dp.callback_query(StateFilter(GameStates.playing), F.data.regexp(r'^\d+$'))
async def process_answer(callback: CallbackQuery, state: FSMContext):
    user_id = callback.from_user.id
    session = get_user_session(user_id)

    selected = int(callback.data)
    right = session["current_right"]

    if selected == right:
        session["correct"] += 1
        result_text = "✅ Правильно! В этот раз вам повезло, Пользователь."
    else:
        result_text = f"❌ Неправильно. Правильный ответ: {right+1}."

    await callback.message.edit_text(
        callback.message.text + "\n\n" + result_text,
        reply_markup=None
    )

    session["current_index"] += 1

    if session["current_index"] >= session["total"]:
        await show_final_result(callback.message, state, user_id)
    else:
        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="➡️ Следующий вопрос", callback_data="next_question")]
            ]
        )
        await callback.message.answer(
            "Нажмите «Следующий вопрос», чтобы продолжить.",
            reply_markup=keyboard
        )

    await callback.answer()

@dp.callback_query(StateFilter(GameStates.playing), F.data == "next_question")
async def next_question_callback(callback: CallbackQuery, state: FSMContext):
    await callback.message.delete()
    await send_next_question(callback.message, state)
    await callback.answer()

# -------------------- Завершение игры --------------------
async def show_final_result(message: Message, state: FSMContext, user_id: int):
    session = get_user_session(user_id)
    correct = session["correct"]
    total = session["total"]

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🔁 Играть заново", callback_data="restart")],
            [InlineKeyboardButton(text="🏠 Главное меню", callback_data="main_menu")]
        ]
    )

    await message.answer(
        f"🏁 Игра завершена!\nВаш результат: {correct} из {total} правильных ответов.\n\nВыберите действие:",
        reply_markup=keyboard
    )
    await state.set_state(GameStates.finished)

# -------------------- Исправленный обработчик "Заново" --------------------
@dp.callback_query(StateFilter(GameStates.finished), F.data == "restart")
async def restart_game(callback: CallbackQuery, state: FSMContext):
    user_id = callback.from_user.id
    session = get_user_session(user_id)

    # Сохраняем тему, сбрасываем всё остальное
    session["questions"] = []
    session["current_index"] = 0
    session["total"] = 0
    session["correct"] = 0

    # Удаляем сообщение с результатом
    await callback.message.delete()

    # Спрашиваем количество вопросов заново
    await callback.message.answer(
        "Введите количество вопросов (число):"
    )
    await state.set_state(GameStates.choosing_question_count)
    await callback.answer()

# -------------------- Обработчик "Главное меню" --------------------
@dp.callback_query(StateFilter(GameStates.finished), F.data == "main_menu")
async def go_to_main_menu(callback: CallbackQuery, state: FSMContext):
    user_id = callback.from_user.id
    if user_id in user_sessions:
        del user_sessions[user_id]

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=topic["name"], callback_data=f"topic_{i}")]
            for i, topic in enumerate(TOPICS)
        ]
    )
    await callback.message.edit_text("Выберите тему:", reply_markup=keyboard)
    await state.set_state(GameStates.choosing_topic)
    await callback.answer()

# -------------------- Запуск --------------------
async def main():
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())