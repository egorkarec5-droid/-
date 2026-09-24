import asyncio
import logging
import os
import tempfile

from aiogram import Bot, Dispatcher, Router, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import Message

import json

from config import BOT_TOKEN, OWNER_ID, DADATA_API_KEY, DADATA_SECRET_KEY, CHECKO_API_KEY
from keyboards import main_menu
from book_detect import detect_book_type
from parser import parse_pokupki, parse_prodazhi, summarize, ParseError
from rules import assess_all
from report import format_summary_line, format_top_risks, DISCLAIMER
from card import build_card, format_card
from dadata_client import get_party, DaDataError
from checko_client import get_company, CheckoError

router = Router()


@router.error(F.update.message.as_("message"))
async def global_error_handler(event, message: Message, state: FSMContext):
    """Ловит вообще любую необработанную ошибку в любом хендлере: логирует,
    сбрасывает зависшее состояние и отвечает пользователю, вместо того
    чтобы бот молчал или требовал /start для восстановления."""
    logging.exception("Необработанная ошибка при обработке сообщения", exc_info=event.exception)
    try:
        await state.clear()
    except Exception:
        logging.exception("Не удалось сбросить состояние после ошибки")
    await message.answer(
        "⚠️ Что-то пошло не так при обработке запроса. Я сбросил состояние — можно продолжать.",
        reply_markup=main_menu,
    )
    return True

WELCOME = (
    "👋 <b>Привет! Я — Досье Плюс.</b>\n\n"
    "Проверяю компании и ИП на признаки риска: подставные фирмы, подозрительные платежи, "
    "скрытые связи между организациями. Умею разбирать целую книгу покупок/продаж "
    "или проверять одну организацию по ИНН.\n\n"
    "<b>Что я умею:</b>\n"
    "📊 Разобрать книгу покупок/продаж — найду подозрительные операции\n"
    "🔍 Дать карточку по ИНН — статус, финансы, риск-балл\n\n"
    "Все действия — кнопками ниже, ничего не нужно печатать вручную.\n\n"
    "⚠️ Оценка риска — автоматическая, по открытым данным, не является юридическим заключением."
)

HELP_TEXT = (
    "<b>Как пользоваться ботом</b>\n\n"
    "📊 <b>Проверить книгу</b>\n"
    "Пришлите два файла: книгу покупок (Раздел 8) и книгу продаж (Раздел 9) "
    "из декларации по НДС. Я разберу все операции, найду подозрительные и покажу сводку.\n\n"
    "🔍 <b>Проверить контрагента</b>\n"
    "Введите ИНН или название организации — пришлю карточку (появится на Этапе 2).\n\n"
    "📋 <b>Список ИНН</b>\n"
    "Пришлите список ИНН текстом или файлом — проверю каждый.\n\n"
    "⚠️ Оценка риска — автоматическая, по открытым данным, не является юридическим заключением."
)


class BookStates(StatesGroup):
    waiting_files = State()


class CardStates(StatesGroup):
    waiting_inn = State()


# Отслеживаем полученные файлы вне FSMContext, под блокировкой — чтобы два
# параллельных скачивания (покупки+продажи почти одновременно) не перезаписали
# друг друга при записи состояния (race condition).
_pending_books: dict[int, dict] = {}
_pending_lock = asyncio.Lock()


@router.message(CommandStart())
async def cmd_start(message: Message):
    await message.answer(WELCOME, reply_markup=main_menu)


@router.message(F.text == "ℹ️ Помощь")
async def cmd_help(message: Message):
    await message.answer(HELP_TEXT)


@router.message(F.text == "📊 Проверить книгу")
async def start_book_check(message: Message, state: FSMContext):
    await state.clear()
    await state.set_state(BookStates.waiting_files)
    _pending_books[message.from_user.id] = {}
    await message.answer(
        "Пришлите оба файла — книгу покупок (Раздел 8) и книгу продаж (Раздел 9). "
        "Можно в любом порядке, я сам определю, где какая."
    )


@router.message(BookStates.waiting_files, F.document)
async def receive_book_file(message: Message, state: FSMContext):
    document = message.document
    if not document.file_name.lower().endswith((".xlsx", ".xls")):
        await message.answer("Это не похоже на Excel-файл (.xlsx). Пришлите файл книги в формате .xlsx.")
        return

    user_dir = os.path.join(tempfile.gettempdir(), "dosje_plus", str(message.from_user.id))
    os.makedirs(user_dir, exist_ok=True)
    local_path = os.path.join(user_dir, document.file_name)

    file = await message.bot.get_file(document.file_id)
    await message.bot.download_file(file.file_path, destination=local_path)

    book_type = detect_book_type(local_path)
    if book_type is None:
        await message.answer(
            "Не смог определить, покупки это или продажи — не нашёл ожидаемых колонок в файле. "
            "Убедитесь, что это выгрузка Раздела 8 или 9 декларации по НДС."
        )
        return

    user_id = message.from_user.id
    async with _pending_lock:
        slot = _pending_books.setdefault(user_id, {})
        was_already_set = book_type in slot
        slot[book_type] = local_path
        both_ready = "purchases" in slot and "sales" in slot

    label = "покупок" if book_type == "purchases" else "продаж"
    other_label = "продаж" if book_type == "purchases" else "покупок"
    if was_already_set:
        await message.answer(f"⚠️ Это снова похоже на книгу {label} — уже получена ранее. Жду книгу {other_label}.")
    else:
        await message.answer(f"✅ Книга {label} получена.")

    if both_ready:
        await run_analysis(message, state, slot["purchases"], slot["sales"])
        async with _pending_lock:
            _pending_books.pop(user_id, None)


async def run_analysis(message: Message, state: FSMContext, purchases_path: str, sales_path: str):
    await message.answer("⏳ Обрабатываю, это займёт несколько секунд...")

    try:
        pokupki = parse_pokupki(purchases_path)
        prodazhi = parse_prodazhi(sales_path)
    except ParseError as e:
        await message.answer(f"❌ Не смог разобрать файл: {e}")
        await state.clear()
        return

    all_ops = pokupki + prodazhi
    summaries = summarize(all_ops)
    assessments = assess_all(summaries, all_ops)

    await message.answer(format_summary_line(assessments))
    await message.answer(format_top_risks(summaries, assessments) + DISCLAIMER, reply_markup=main_menu)

    await state.clear()


def _is_valid_inn(text: str) -> bool:
    return text.isdigit() and len(text) in (10, 12)


@router.message(F.text == "🔍 Проверить контрагента")
async def start_counterparty_check(message: Message, state: FSMContext):
    await state.clear()
    await state.set_state(CardStates.waiting_inn)
    await message.answer("Введите ИНН организации или ИП (10 или 12 цифр).")


@router.message(CardStates.waiting_inn, F.text, ~F.text.startswith("/"))
async def receive_inn(message: Message, state: FSMContext):
    inn = message.text.strip()
    if not _is_valid_inn(inn):
        await message.answer("Это не похоже на ИНН — нужно 10 цифр (юрлицо) или 12 цифр (ИП). Попробуйте ещё раз.")
        return

    await message.answer("⏳ Ищу данные...")
    try:
        result = await build_card(inn, DADATA_API_KEY, DADATA_SECRET_KEY, CHECKO_API_KEY)
        text = format_card(result)
    except Exception as e:
        logging.exception("Ошибка при построении карточки по ИНН %s", inn)
        text = f"❌ Не удалось получить данные по ИНН {inn}: {e}\nПопробуйте ещё раз или другой ИНН."

    await message.answer(text, reply_markup=main_menu)
    await message.answer("Введите следующий ИНН, или выберите другое действие кнопкой ниже.")
    # состояние остаётся waiting_inn — можно проверять несколько ИНН подряд
    # без повторного нажатия кнопки; любая другая кнопка меню сбросит его сама


async def _send_raw(message: Message, label: str, raw: dict):
    text = json.dumps(raw, ensure_ascii=False, indent=2)
    chunk_size = 3800
    chunks = [text[i:i + chunk_size] for i in range(0, len(text), chunk_size)] or [""]
    await message.answer(f"<b>{label} raw ({len(text)} chars, {len(chunks)} частей):</b>")
    for i, chunk in enumerate(chunks, 1):
        await message.answer(f"<code>{chunk}</code>")


@router.message(F.text.startswith("/debug"))
async def debug_raw(message: Message):
    if message.from_user.id != OWNER_ID:
        return

    parts = message.text.split(maxsplit=1)
    if len(parts) < 2 or not parts[1].strip().isdigit():
        await message.answer("Использование: /debug <ИНН>")
        return
    inn = parts[1].strip()

    try:
        party = await get_party(inn, DADATA_API_KEY, DADATA_SECRET_KEY)
        await _send_raw(message, "DaData", party.raw if party else {})
    except DaDataError as e:
        await message.answer(f"DaData error: {e}")

    try:
        company = await get_company(inn, CHECKO_API_KEY)
        await _send_raw(message, "Checko", company.raw if company else {})
    except CheckoError as e:
        await message.answer(f"Checko error: {e}")


async def main():
    logging.basicConfig(level=logging.INFO)

    bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher(storage=MemoryStorage())
    dp.include_router(router)

    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
