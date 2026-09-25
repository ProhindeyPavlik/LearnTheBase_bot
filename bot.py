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


class GameStates(StatesGroup):
    choosing_topic = State()
    choosing_format = State()
    choosing_inventor_format = State()
    choosing_question_count = State()
    playing = State()
    waiting_next = State()
    finished = State()

ENHANCED_TOPIC_NAMES = {
    "Корабли",
    "Лошади",
    "Японцы",
    "Японские словечки",
    "Древняя Греция",
    "Греческие боги",
    "Немецкие города",
    "Изобретатели",
}

user_sessions: Dict[int, Dict] = {}


def get_user_session(user_id: int) -> Dict:
    if user_id not in user_sessions:
        user_sessions[user_id] = {
            "topic": None,
            "questions": [],
            "current_index": 0,
            "total": 0,
            "score": 0.0,
            "format": "options",
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

def get_topic_questions(topic):
    if topic.get("type") == "grouped":
        questions = []
        for group, words in topic["groups"].items():
            for word in words:
                questions.append({
                    "name": word,
                    "description": group
                })
        return questions

    return topic["questions"]

storage = MemoryStorage()
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(storage=storage)


def get_grouped_options(question, topic, swap):
    groups = topic["groups"]
    correct_group = question["description"]

    other_groups = [
        group for group in groups
        if group != correct_group
    ]

    if swap:
        selected_groups = random.sample(other_groups, 4)
        options = [correct_group] + selected_groups
        random.shuffle(options)
        right_pos = options.index(correct_group)
        return options, right_pos
    else:
        selected_groups = random.sample(other_groups, 4)
        options = [
            random.choice(groups[group])
            for group in selected_groups
        ]
        options.append(question["name"])
        random.shuffle(options)
        right_pos = options.index(question["name"])
        return options, right_pos


def normalize_answer(text: str) -> str:
    text = text.casefold()
    text = text.replace("ё", "е")
    text = text.replace("й", "и")
    text = "".join(char for char in text if not char.isspace())
    return text


def lcs_len(first: str, second: str) -> int:
    if len(first) < len(second):
        first, second = second, first

    previous = [0] * (len(second) + 1)

    for char_first in first:
        current = [0] * (len(second) + 1)

        for j, char_second in enumerate(second, start=1):
            if char_first == char_second:
                current[j] = previous[j - 1] + 1
            else:
                current[j] = max(previous[j], current[j - 1])

        previous = current

    return previous[-1]


def get_text_answer_score(user_answer: str, correct_answer: str) -> float:
    user_answer = normalize_answer(user_answer)
    correct_answer = normalize_answer(correct_answer)

    if not user_answer:
        return 0.0
    if user_answer == correct_answer:
        return 1.0
    if len(correct_answer) < 4:
        return 0.0

    common_len = lcs_len(user_answer, correct_answer)
    similarity = common_len / max(
        len(user_answer),
        len(correct_answer)
    )
    if similarity >= 0.8:
        return 0.9

    return 0.0


def get_expected_answer(question, topic, game_format):
    if topic["name"] == "Изобретатели" and game_format == "surname":
        return question["name"].split()[-1]

    return question["name"]


def format_score(score: float) -> str:
    score = round(score, 1)
    if score.is_integer():
        return str(int(score))
    return str(score).replace(".", ",")


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


async def ask_question_count(message: Message, state: FSMContext):
    user_id = message.chat.id
    session = get_user_session(user_id)
    topic = session["topic"]
    questions = get_topic_questions(topic)

    await message.edit_text(
        f"Вы выбрали тему: {topic['name']}\n"
        f"Вопросов в теме: {len(questions)}.\n"
        "Введите количество вопросов (число):"
    )

    await state.set_state(GameStates.choosing_question_count)


@dp.callback_query(StateFilter(GameStates.choosing_topic), F.data.startswith("topic_"))
async def process_topic_selection(callback: CallbackQuery, state: FSMContext):
    topic_index = int(callback.data.split("_")[1])
    topic = TOPICS[topic_index]
    topic_name = topic["name"]

    user_id = callback.from_user.id
    session = get_user_session(user_id)
    session["topic"] = topic
    session["total"] = 0

    if topic_name == "Изобретатели":
        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(
                    text="С вариантами ответов",
                    callback_data="inventor_format_options"
                )],
                [InlineKeyboardButton(
                    text="Вводить только фамилию",
                    callback_data="inventor_format_surname"
                )],
                [InlineKeyboardButton(
                    text="Вводить имя и фамилию",
                    callback_data="inventor_format_full_name"
                )],
            ]
        )

        await callback.message.edit_text(
            f"Вы выбрали тему: {topic_name}\n"
            "Выберите формат:",
            reply_markup=keyboard
        )

        await state.set_state(GameStates.choosing_inventor_format)

    elif topic_name in ENHANCED_TOPIC_NAMES:
        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(
                    text="С вариантами ответов",
                    callback_data="format_options"
                )],
                [InlineKeyboardButton(
                    text="Без вариантов ответов",
                    callback_data="format_text"
                )],
            ]
        )

        await callback.message.edit_text(
            f"Вы выбрали тему: {topic_name}\n"
            "Выберите формат:",
            reply_markup=keyboard
        )

        await state.set_state(GameStates.choosing_format)

    else:
        session["format"] = "options"
        await ask_question_count(callback.message, state)

    await callback.answer()


@dp.callback_query(StateFilter(GameStates.choosing_format), F.data.startswith("format_"))
async def process_format_selection(callback: CallbackQuery, state: FSMContext):
    user_id = callback.from_user.id
    session = get_user_session(user_id)

    if callback.data == "format_options":
        session["format"] = "options"
    else:
        session["format"] = "text"

    await ask_question_count(callback.message, state)
    await callback.answer()


@dp.callback_query(
    StateFilter(GameStates.choosing_inventor_format),
    F.data.startswith("inventor_format_")
)
async def process_inventor_format_selection(
    callback: CallbackQuery,
    state: FSMContext
):
    user_id = callback.from_user.id
    session = get_user_session(user_id)

    session["format"] = callback.data.replace("inventor_format_", "")

    await ask_question_count(callback.message, state)
    await callback.answer()


@dp.message(StateFilter(GameStates.choosing_question_count))
async def process_question_count(message: Message, state: FSMContext):
    user_id = message.from_user.id
    session = get_user_session(user_id)
    topic = session["topic"]
    questions = get_topic_questions(topic)
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
    session["score"] = 0.0

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

    topic = session["topic"]
    questions = get_topic_questions(topic)

    idx = session["questions"][session["current_index"]]
    question = questions[idx]
    game_format = session["format"]

    if game_format != "options":
        expected_answer = get_expected_answer(
            question,
            topic,
            game_format
        )

        session["current_question_data"] = question
        session["expected_answer"] = expected_answer

        if topic["name"] == "Изобретатели":
            if game_format == "surname":
                instruction = "Введите фамилию:"
            else:
                instruction = "Введите имя и фамилию:"
        else:
            instruction = "Введите название:"

        full_message = (
            f"Вопрос {session['current_index'] + 1} "
            f"из {session['total']}:\n\n"
            f"{question['description']}\n\n"
            f"{instruction}"
        )

        await message.answer(full_message)
        return

    swap = random.choice([True, False])
    session["swap"] = swap
    session["current_question_data"] = question

    if topic.get("type") == "grouped":
        option_texts, right_pos = get_grouped_options(
            question,
            topic,
            swap
        )

        if swap:
            question_text = question["name"]
        else:
            question_text = question["description"]

    else:
        options_indices = get_random_options(idx, questions, count=5)
        right_pos = options_indices.index(idx)

        if swap:
            question_text = question["name"]
            option_texts = [
                questions[opt_idx]["description"]
                for opt_idx in options_indices
            ]
        else:
            question_text = question["description"]
            option_texts = [
                questions[opt_idx]["name"]
                for opt_idx in options_indices
            ]

    session["current_right"] = right_pos
    session["current_options"] = option_texts

    options_message = "\n\n".join(
        f"{NUM_EMOJI[i]} {text}"
        for i, text in enumerate(option_texts)
    )

    full_message = (
        f"Вопрос {session['current_index'] + 1} из {session['total']}:\n\n"
        f"{question_text}\n\n"
        f"Варианты:\n\n{options_message}"
    )

    buttons = [
        [InlineKeyboardButton(
            text=NUM_EMOJI[i],
            callback_data=str(i)
        )]
        for i in range(len(option_texts))
    ]

    keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)

    await message.answer(
        full_message,
        reply_markup=keyboard
    )

@dp.callback_query(StateFilter(GameStates.playing), F.data.regexp(r'^\d+$'))
async def process_answer(callback: CallbackQuery, state: FSMContext):
    user_id = callback.from_user.id
    session = get_user_session(user_id)

    selected = int(callback.data)
    right = session["current_right"]

    if selected == right:
        session["score"] += 1.0
        result_text = "✅ Правильно! В этот раз вам повезло, Пользователь."
    else:
        result_text = (
            f"❌ Очень грустно, Пользователь. "
            f"Правильный ответ: {right + 1}."
        )

    await callback.message.edit_text(
        callback.message.text + "\n\n" + result_text,
        reply_markup=None
    )

    session["current_index"] += 1

    if session["current_index"] >= session["total"]:
        await show_final_result(callback.message, state, user_id)
    else:
        await state.set_state(GameStates.waiting_next)

        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(
                    text="Следующий вопрос",
                    callback_data="next_question"
                )]
            ]
        )

        await callback.message.answer(
            "Продолжаем?",
            reply_markup=keyboard
        )

    await callback.answer()


@dp.message(StateFilter(GameStates.playing))
async def process_text_answer(message: Message, state: FSMContext):
    user_id = message.from_user.id
    session = get_user_session(user_id)

    if session["format"] == "options":
        await message.answer("Выберите ответ кнопкой.")
        return

    if not message.text:
        await message.answer("Введите ответ текстом.")
        return

    topic = session["topic"]
    questions = get_topic_questions(topic)

    idx = session["questions"][session["current_index"]]
    question = questions[idx]

    correct_answer = get_expected_answer(
        question,
        topic,
        session["format"]
    )

    points = get_text_answer_score(
        message.text,
        correct_answer
    )

    session["score"] = round(
        session["score"] + points,
        1
    )

    if points == 1.0:
        result_text = (
            f"✅ Правильно! Правильный ответ: {correct_answer}."
        )
    elif points == 0.9:
        result_text = (
            f"Почти! Правильный ответ: {correct_answer}. "
            "Вот 0.9 балла за старание."
        )
    else:
        result_text = (
            f"❌ Неверно. Правильный ответ: {correct_answer}."
        )

    await message.answer(result_text)

    session["current_index"] += 1

    if session["current_index"] >= session["total"]:
        await show_final_result(message, state, user_id)
    else:
        await state.set_state(GameStates.waiting_next)

        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(
                    text="Следующий вопрос",
                    callback_data="next_question"
                )]
            ]
        )

        await message.answer(
            "Продолжаем?",
            reply_markup=keyboard
        )


@dp.callback_query(StateFilter(GameStates.waiting_next), F.data == "next_question")
async def next_question_callback(callback: CallbackQuery, state: FSMContext):
    await callback.message.delete()
    await state.set_state(GameStates.playing)
    await send_next_question(callback.message, state)
    await callback.answer()

async def show_final_result(message: Message, state: FSMContext, user_id: int):
    session = get_user_session(user_id)
    score = session["score"]
    total = session["total"]

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Заново", callback_data="restart")],
            [InlineKeyboardButton(text="Главное меню", callback_data="main_menu")]
        ]
    )

    await message.answer(
        f"Конец!\nРезультат: {format_score(score)} из {total}.\n\n",
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
    session["score"] = 0.0

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


async def index(request):
    return web.Response(text="Bot is running!", status=200)


async def on_startup(bot: Bot):
    webhook_url = os.getenv("RENDER_EXTERNAL_URL", "").rstrip("/")
    if not webhook_url:
        port = int(os.getenv("PORT", 10000))
        webhook_url = f"http://localhost:{port}"

    webhook_url = f"{webhook_url}/webhook"
    logging.info(f"Setting webhook to: {webhook_url}")
    try:
        await bot.delete_webhook(drop_pending_updates=True)
        await bot.set_webhook(webhook_url)
        logging.info("Webhook set successfully!")
    except Exception as e:
        logging.error(f"FAILED to set webhook: {e}")
        logging.exception("Full traceback:")

dp.startup.register(on_startup)


def main():
    PORT = int(os.getenv("PORT", 10000))

    app = web.Application()

    app.router.add_get("/", index)

    webhook_handler = SimpleRequestHandler(dispatcher=dp, bot=bot)
    webhook_handler.register(app, path="/webhook")

    setup_application(app, dp, bot=bot)

    logging.info(f"Starting bot webhook on port {PORT}")
    web.run_app(app, host="0.0.0.0", port=PORT)



if __name__ == "__main__":
    main()
