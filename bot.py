import asyncio
import logging
import os
from collections import defaultdict, deque

from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command, CommandStart
from aiogram.types import (
    BotCommand,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)
from aiogram.exceptions import TelegramBadRequest
from aiogram.utils.chat_action import ChatActionSender
from dotenv import load_dotenv
from google import genai
from google.genai import errors as genai_errors
from google.genai import types

import school_info
from formatting import latex_to_unicode, strip_html, to_telegram_html
from prompts import LINKS_MARKER, build_system_prompt

load_dotenv()

TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip()
# Можно указать несколько моделей через запятую: если первая перегружена
# или недоступна, бот автоматически попробует следующую.
DEFAULT_MODELS = "gemini-3.6-flash,gemini-3.5-flash,gemini-3.8-flash"
MODEL_TIMEOUT = 60  # секунд ждём одну модель, потом пробуем следующую
GEMINI_MODELS = [
    m.strip() for m in os.getenv("GEMINI_MODEL", DEFAULT_MODELS).split(",") if m.strip()
]
# Ошибки, при которых стоит попробовать другую модель
FALLBACK_CODES = {404, 429, 500, 503}
HISTORY_MESSAGES = 20  # сколько последних сообщений бот помнит в диалоге

if not TELEGRAM_TOKEN or not GEMINI_API_KEY:
    raise SystemExit(
        "Заполните TELEGRAM_BOT_TOKEN и GEMINI_API_KEY в файле .env"
    )

def make_client() -> genai.Client:
    # Отключаем встроенные повторы: иначе при перегрузке модели библиотека
    # долго повторяет запрос, и бот «печатает» без ответа.
    try:
        http_options = types.HttpOptions(
            timeout=MODEL_TIMEOUT * 1000,
            retry_options=types.HttpRetryOptions(attempts=1),
        )
        return genai.Client(api_key=GEMINI_API_KEY, http_options=http_options)
    except Exception:
        logging.warning("Не удалось задать http_options, использую настройки по умолчанию")
        return genai.Client(api_key=GEMINI_API_KEY)


client = make_client()
GEN_CONFIG = types.GenerateContentConfig(
    system_instruction=build_system_prompt(),
    temperature=0.3,  # ниже = точнее и спокойнее
)

dp = Dispatcher()
dp.message.filter(F.chat.type == "private")  # бот работает только в личке

# История диалога каждого ученика (в памяти; сбрасывается при перезапуске)
history: dict[int, deque] = defaultdict(lambda: deque(maxlen=HISTORY_MESSAGES))


# ---------------------------------------------------------------- тексты

TEXTS = {
    "start": {
        "ru": "Здравствуйте! 👋 Я ассистент учителя физики "
              "Хусанжанова Кодиржона.\n\n"
              "Задайте любой вопрос по физике за 7–11 класс: объясню простым "
              "языком с примерами, помогу решить задачу по шагам. Можно "
              "прислать фото задачи или голосовое сообщение.\n\n"
              "Пишите на русском, казахском или узбекском языке.\n\n"
              "/links — курсы и ссылки\n/new — начать новый диалог",
        "kk": "Сәлеметсіз бе! 👋 Мен физика пәнінің мұғалімі "
              "Хусанжанов Кодиржонның көмекшісімін.\n\n"
              "7–11 сыныптың физикасы бойынша кез келген сұрақ қойыңыз: "
              "қарапайым тілмен, мысалдармен түсіндіремін, есепті қадамдап "
              "шығаруға көмектесемін. Есептің суретін немесе дауыстық хабар "
              "жіберуге болады.\n\n"
              "Қазақ, орыс немесе өзбек тілінде жаза беріңіз.\n\n"
              "/links — курстар мен сілтемелер\n/new — жаңа диалог бастау",
        "uz": "Assalomu alaykum! 👋 Men fizika o'qituvchisi "
              "Xusanjanov Qodirjonning yordamchisiman.\n\n"
              "7–11-sinf fizikasi bo'yicha istalgan savol bering: oddiy tilda, "
              "misollar bilan tushuntiraman, masalani bosqichma-bosqich "
              "yechishga yordam beraman. Masala rasmini yoki ovozli xabar "
              "yuborishingiz mumkin.\n\n"
              "O'zbek, qozoq yoki rus tilida yozing.\n\n"
              "/links — kurslar va havolalar\n/new — yangi suhbat boshlash",
    },
    "new": {
        "ru": "Хорошо, начнём заново! Задайте вопрос по физике. 🙂",
        "kk": "Жақсы, басынан бастайық! Физика бойынша сұрақ қойыңыз. 🙂",
        "uz": "Yaxshi, qaytadan boshlaymiz! Fizika bo'yicha savol bering. 🙂",
    },
    "links": {
        "ru": "Курсы, видеоуроки и соцсети учителя 👇",
        "kk": "Мұғалімнің курстары, бейнесабақтары және әлеуметтік желілері 👇",
        "uz": "O'qituvchining kurslari, video darslari va ijtimoiy tarmoqlari 👇",
    },
    "no_links": {
        "ru": "Ссылки пока не добавлены. Скоро появятся!",
        "kk": "Сілтемелер әзірге қосылмаған. Жақында пайда болады!",
        "uz": "Havolalar hozircha qo'shilmagan. Tez orada paydo bo'ladi!",
    },
    "busy": {
        "ru": "Сейчас много вопросов 😅 Попробуйте, пожалуйста, через минуту.",
        "kk": "Қазір сұрақ көп 😅 Бір минуттан кейін қайталап көріңізші.",
        "uz": "Hozir savollar ko'p 😅 Iltimos, bir daqiqadan keyin urinib ko'ring.",
    },
    "error": {
        "ru": "Извините, произошла ошибка. Попробуйте ещё раз.",
        "kk": "Кешіріңіз, қате шықты. Қайталап көріңіз.",
        "uz": "Kechirasiz, xatolik yuz berdi. Qaytadan urinib ko'ring.",
    },
    "too_big": {
        "ru": "Файл слишком большой (больше 20 МБ). Пришлите, пожалуйста, фото поменьше.",
        "kk": "Файл тым үлкен (20 МБ-тан артық). Кішірек сурет жіберіңізші.",
        "uz": "Fayl juda katta (20 MB dan ortiq). Iltimos, kichikroq rasm yuboring.",
    },
    "unsupported": {
        "ru": "Пожалуйста, напишите вопрос текстом, пришлите фото задачи или голосовое сообщение.",
        "kk": "Сұрақты мәтінмен жазыңыз, есептің суретін немесе дауыстық хабар жіберіңіз.",
        "uz": "Iltimos, savolni matn bilan yozing, masala rasmini yoki ovozli xabar yuboring.",
    },
}


def lang_of(message: Message) -> str:
    code = (message.from_user.language_code or "").lower() if message.from_user else ""
    if code.startswith("kk"):
        return "kk"
    if code.startswith("uz"):
        return "uz"
    return "ru"


def t(key: str, message: Message) -> str:
    return TEXTS[key][lang_of(message)]


# ---------------------------------------------------------------- помощники

def links_keyboard() -> InlineKeyboardMarkup | None:
    rows = [[InlineKeyboardButton(text=label, url=url)] for label, url in school_info.links()]
    return InlineKeyboardMarkup(inline_keyboard=rows) if rows else None


def split_text(text: str, limit: int = 4000) -> list[str]:
    """Telegram ограничивает сообщение 4096 символами — режем по строкам."""
    chunks = []
    while len(text) > limit:
        cut = text.rfind("\n", 0, limit)
        if cut < limit // 2:
            cut = limit
        chunks.append(text[:cut])
        text = text[cut:].lstrip("\n")
    if text.strip():
        chunks.append(text)
    return chunks


async def send_pretty(message: Message, text: str, keyboard=None) -> None:
    """Отправляет ответ с жирным текстом; если разметка сломалась — простым текстом."""
    html_text = to_telegram_html(text)
    try:
        await message.answer(html_text, parse_mode="HTML", reply_markup=keyboard)
    except TelegramBadRequest:
        logging.warning("HTML не принят Telegram, отправляю простым текстом")
        await message.answer(strip_html(html_text), reply_markup=keyboard)


async def ask_gemini(user_id: int, parts: list[types.Part], history_note: str) -> str:
    """Отправляет вопрос в Gemini вместе с историей диалога."""
    past = history[user_id]
    contents = [*past, types.Content(role="user", parts=parts)]
    last_error: Exception | None = None
    for model in GEMINI_MODELS:
        try:
            response = await asyncio.wait_for(
                client.aio.models.generate_content(
                    model=model, contents=contents, config=GEN_CONFIG
                ),
                timeout=MODEL_TIMEOUT,
            )
            break
        except asyncio.TimeoutError as e:
            last_error = e
            logging.warning("Модель %s не ответила за %s с, пробую следующую", model, MODEL_TIMEOUT)
        except genai_errors.APIError as e:
            last_error = e
            if getattr(e, "code", None) not in FALLBACK_CODES:
                raise
            logging.warning("Модель %s недоступна (%s), пробую следующую", model, e.code)
        except Exception as e:  # обрыв сети, таймаут библиотеки и т.п.
            last_error = e
            logging.warning("Модель %s: %r, пробую следующую", model, e)
    else:
        raise last_error  # все модели не сработали

    answer = latex_to_unicode(response.text or "").strip()
    if answer:
        # В историю кладём только текст (без фото/аудио), чтобы экономить память
        past.append(types.Content(role="user", parts=[types.Part.from_text(text=history_note)]))
        past.append(types.Content(role="model", parts=[types.Part.from_text(text=answer)]))
    return answer


async def answer_student(message: Message, parts: list[types.Part], history_note: str) -> None:
    try:
        async with ChatActionSender.typing(bot=message.bot, chat_id=message.chat.id):
            answer = await ask_gemini(message.from_user.id, parts, history_note)
    except asyncio.TimeoutError:
        logging.error("Ни одна модель не ответила вовремя")
        await message.answer(t("busy", message))
        return
    except genai_errors.APIError as e:
        logging.exception("Gemini API error")
        key = "busy" if getattr(e, "code", None) in FALLBACK_CODES else "error"
        await message.answer(t(key, message))
        return
    except Exception:
        logging.exception("Unexpected error")
        await message.answer(t("error", message))
        return

    if not answer:
        await message.answer(t("error", message))
        return

    show_links = LINKS_MARKER in answer
    answer = answer.replace(LINKS_MARKER, "").strip()
    keyboard = links_keyboard() if show_links else None

    chunks = split_text(answer, limit=3500)  # запас под HTML-теги
    for i, chunk in enumerate(chunks):
        is_last = i == len(chunks) - 1
        await send_pretty(message, chunk, keyboard if is_last else None)


# ---------------------------------------------------------------- команды

@dp.message(CommandStart())
async def cmd_start(message: Message) -> None:
    history.pop(message.from_user.id, None)
    await message.answer(t("start", message))


@dp.message(Command("new"))
async def cmd_new(message: Message) -> None:
    history.pop(message.from_user.id, None)
    await message.answer(t("new", message))


@dp.message(Command("links"))
async def cmd_links(message: Message) -> None:
    keyboard = links_keyboard()
    if keyboard:
        await message.answer(t("links", message), reply_markup=keyboard)
    else:
        await message.answer(t("no_links", message))


# ---------------------------------------------------------------- сообщения

@dp.message(F.text)
async def on_text(message: Message) -> None:
    text = message.text.strip()
    await answer_student(message, [types.Part.from_text(text=text)], text)


IMAGE_PROMPT = (
    "Ученик прислал фото (или файл) с задачей или вопросом по физике. "
    "Внимательно прочитай условие, включая числа, единицы и рисунок. "
    "Если задач несколько, реши каждую по порядку. Решай по шагам: "
    "Дано → Формула → Решение → Ответ. Если текст на фото плохо видно, "
    "напиши, что именно не разобрал, и попроси прислать фото чётче. "
    "Отвечай на языке подписи ученика, а если подписи нет — на языке текста на фото."
)
VOICE_PROMPT = (
    "Ученик задал вопрос голосом. Внимательно послушай и пойми вопрос. "
    "Ответь ТЕКСТОМ на том же языке, на котором он говорил (казахский, русский "
    "или узбекский). Если это задача, реши её по шагам. Если речь неразборчива, "
    "вежливо попроси повторить или написать текстом."
)
MAX_FILE_MB = 20  # Telegram позволяет боту скачивать файлы до 20 МБ


async def answer_media(message: Message, file, mime: str, prompt: str, note: str) -> None:
    """Скачивает фото/аудио/PDF и отправляет его в Gemini вместе с инструкцией."""
    size = getattr(file, "file_size", 0) or 0
    if size > MAX_FILE_MB * 1024 * 1024:
        await message.answer(t("too_big", message))
        return
    try:
        data = (await message.bot.download(file)).read()
    except Exception:
        logging.exception("Не удалось скачать файл")
        await message.answer(t("error", message))
        return
    caption = (message.caption or "").strip()
    text = prompt + (f"\n\nПодпись ученика: {caption}" if caption else "")
    parts = [types.Part.from_bytes(data=data, mime_type=mime), types.Part.from_text(text=text)]
    await answer_student(message, parts, f"{note} {caption}".strip())


@dp.message(F.photo)
async def on_photo(message: Message) -> None:
    await answer_media(message, message.photo[-1], "image/jpeg", IMAGE_PROMPT, "[фото задачи]")


@dp.message(F.document)
async def on_document(message: Message) -> None:
    """Фото, отправленное «файлом» (без сжатия), или PDF с заданиями."""
    doc = message.document
    mime = (doc.mime_type or "").lower()
    if mime.startswith("image/") or mime == "application/pdf":
        await answer_media(message, doc, mime, IMAGE_PROMPT, "[файл с задачей]")
    else:
        await message.answer(t("unsupported", message))


@dp.message(F.voice)
async def on_voice(message: Message) -> None:
    await answer_media(message, message.voice, "audio/ogg", VOICE_PROMPT, "[голосовой вопрос]")


@dp.message(F.audio)
async def on_audio(message: Message) -> None:
    mime = message.audio.mime_type or "audio/mpeg"
    await answer_media(message, message.audio, mime, VOICE_PROMPT, "[аудио вопрос]")


@dp.message()
async def on_other(message: Message) -> None:
    await message.answer(t("unsupported", message))


# ---------------------------------------------------------------- запуск

async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    bot = Bot(TELEGRAM_TOKEN)
    await bot.set_my_commands([
        BotCommand(command="start", description="Начать / Бастау / Boshlash"),
        BotCommand(command="links", description="Курсы и ссылки / Курстар / Kurslar"),
        BotCommand(command="new", description="Новый диалог / Жаңа диалог / Yangi suhbat"),
    ])
    logging.info("Бот запущен. Модели: %s", ", ".join(GEMINI_MODELS))
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
