import json
import random
import logging
import os
from typing import Dict
from dotenv import load_dotenv
from aiohttp import web

from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery, Message
from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application

load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN is not set in environment variables")
logging.basicConfig(level=logging.INFO)

with open("realities.json", "r", encoding="utf-8") as f:
    DATA = json.load(f)

TOPICS = DATA["topics"]
TOPICS_DICT = {topic["name"]: topic["questions"] for topic in TOPICS}


class GameStates(StatesGroup):
    choosing_topic = State()
    choosing_question_count = State()
    playing = State()
    finished = State()


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
    indices.extend(pool[:count - 1])
    random.shuffle(indices)
    return indices


storage = MemoryStorage()
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(storage=storage)


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
        "Всем привет, всем привет, всем привет!\nВыберите тему:",
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
        f"Вопросов в теме: {len(questions)}.\n"
        "Введите количество вопросов (число):"
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
        await message.answer("Вы чо? Введите целое положительное число.")
        return

    count = int(text)
    if count <= 0:
        await message.answer("Вы чо? Количество вопросов должно быть больше 0.")
        return
    if count > len(questions):
        count = len(questions)

    indices = list(range(len(questions)))
    random.shuffle(indices)
    session["questions"] = indices[:count]
    session["total"] = count
    session["current_index"] = 0
    session["correct"] = 0

    await message.answer(f"Она сказала стартуем!")
    await state.set_state(GameStates.playing)
    await send_next_question(message, state)


NUM_EMOJI = ["1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣", "6️⃣", "7️⃣", "8️⃣", "9️⃣", "🔟"]


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

    options_message = "\n\n".join(
        f"{NUM_EMOJI[i]} {text}" for i, text in enumerate(option_texts)
    )
    full_message = (
        f"Вопрос {session['current_index'] + 1} из {session['total']}:\n\n"
        f"{question_text}\n\n"
        f"Варианты:\n\n{options_message}"
    )

    buttons = [
        [InlineKeyboardButton(text=NUM_EMOJI[i], callback_data=str(i))]
        for i in range(len(option_texts))
    ]
    keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)

    await message.answer(full_message, reply_markup=keyboard)


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
        result_text = f"❌ Очень грустно, Пользователь. Правильный ответ: {right + 1}."

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
                [InlineKeyboardButton(text="Следующий вопрос", callback_data="next_question")]
            ]
        )
        
        await callback.message.answer(
            "Продолжаем?",
            reply_markup=keyboard
        )

    await callback.answer()


@dp.callback_query(StateFilter(GameStates.playing), F.data == "next_question")
async def next_question_callback(callback: CallbackQuery, state: FSMContext):
    await callback.message.delete()
    await send_next_question(callback.message, state)
    await callback.answer()


async def show_final_result(message: Message, state: FSMContext, user_id: int):
    session = get_user_session(user_id)
    correct = session["correct"]
    total = session["total"]

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Заново", callback_data="restart")],
            [InlineKeyboardButton(text="Главное меню", callback_data="main_menu")]
        ]
    )

    await message.answer(
        f"Конец!\nРезультат: {correct} из {total}.\n\n",
        reply_markup=keyboard
    )
    await state.set_state(GameStates.finished)


@dp.callback_query(StateFilter(GameStates.finished), F.data == "restart")
async def restart_game(callback: CallbackQuery, state: FSMContext):
    user_id = callback.from_user.id
    session = get_user_session(user_id)

    session["questions"] = []
    session["current_index"] = 0
    session["total"] = 0
    session["correct"] = 0

    await callback.message.delete()
    await callback.message.answer("Введите количество вопросов (число):")
    await state.set_state(GameStates.choosing_question_count)
    await callback.answer()


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


async def on_startup(bot: Bot):
    domain = os.getenv("RENDER_EXTERNAL_URL", "").replace("https://", "")
    if not domain:
        domain = f"localhost:{os.getenv('PORT', 10000)}"
        webhook_url = f"http://{domain}/webhook"
    else:
        webhook_url = f"https://{domain}/webhook"

    logging.info(f"Setting webhook to: {webhook_url}")
    await bot.set_webhook(webhook_url)


async def on_shutdown(bot: Bot):
    await bot.delete_webhook()


dp.startup.register(on_startup)
dp.shutdown.register(on_shutdown)


def main():
    PORT = int(os.getenv("PORT", 10000))

    app = web.Application()

    webhook_handler = SimpleRequestHandler(dispatcher=dp, bot=bot)
    webhook_handler.register(app, path="/webhook")

    setup_application(app, dp, bot=bot)

    logging.info(f"Starting bot webhook on port {PORT}")
    web.run_app(app, host="0.0.0.0", port=PORT)


if __name__ == "__main__":
    main()
