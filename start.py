import asyncio
import logging
import os
import tempfile

from aiogram import Bot, Dispatcher, Router, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import CommandStart, CommandObject
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import Message, BufferedInputFile, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton

import json
import re

from config import BOT_TOKEN, OWNER_ID, DADATA_API_KEY, DADATA_SECRET_KEY, CHECKO_API_KEY
from keyboards import main_menu
from book_detect import detect_book_type
from parser import parse_pokupki, parse_prodazhi, summarize, ParseError
from rules import assess_all
from report import format_summary_line, format_top_risks, DISCLAIMER
from card import build_card, format_card, split_for_telegram
from schemes import render_money_flow, FlowRow, render_relations, RelationRow
from pdf_export import build_dossier_pdf, build_multi_dossier_pdf
from relations_search import find_connections
import db
from dadata_client import get_party, DaDataError
from checko_client import get_company, get_finances, get_reliability, search_by_name, CheckoError

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


class ListStates(StatesGroup):
    waiting_list = State()


class RelationsSearchStates(StatesGroup):
    waiting_inns = State()


class BroadcastStates(StatesGroup):
    waiting_text = State()


# Отслеживаем полученные файлы вне FSMContext, под блокировкой — чтобы два
# параллельных скачивания (покупки+продажи почти одновременно) не перезаписали
# друг друга при записи состояния (race condition).
_pending_books: dict[int, dict] = {}
_pending_lock = asyncio.Lock()

MENU_TEXTS = {"📊 Проверить книгу", "🔍 Проверить контрагента", "📋 Список ИНН", "🔗 Найти связи", "⚙️ Настройки", "ℹ️ Помощь"}


@router.message(CommandStart())
async def cmd_start(message: Message, command: CommandObject | None = None):
    invited_by = None
    if command and command.args == "friend":
        invited_by = OWNER_ID
    await db.upsert_user(message.from_user.id, message.from_user.username, invited_by)
    await message.answer(WELCOME, reply_markup=main_menu)


@router.message(F.text == "ℹ️ Помощь")
async def cmd_help(message: Message):
    await message.answer(HELP_TEXT)


@router.message(F.text == "⚙️ Настройки")
async def cmd_settings(message: Message):
    is_owner = message.from_user.id == OWNER_ID
    lines = [
        "<b>⚙️ Настройки</b>",
        "",
        "Доступ: без ограничений (тестовый режим, подписка ещё не подключена).",
    ]
    if is_owner:
        lines.append("")
        lines.append("Вы — владелец бота. Админ-панель: /admin")
    await message.answer("\n".join(lines))


@router.message(F.text.startswith("/clearcache"))
async def cmd_clearcache(message: Message):
    if message.from_user.id != OWNER_ID:
        return
    parts = message.text.split(maxsplit=1)
    if len(parts) < 2 or not parts[1].strip().isdigit():
        await message.answer("Использование: /clearcache <ИНН>")
        return
    inn = parts[1].strip()
    removed = await db.invalidate_cache(inn)
    await message.answer(f"Кэш по ИНН {inn} {'очищен' if removed else 'и так был пуст'}.")


STATUS_EMOJI = {"paid": "💳", "free_invite": "🎁", "free": "⚪", "blocked": "🚫"}
STATUS_LABEL_RU = {"paid": "платный", "free_invite": "бесплатный (инвайт)", "free": "бесплатный", "blocked": "заблокирован"}


def _admin_main_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="👥 Пользователи", callback_data="adm:users:0"),
         InlineKeyboardButton(text="📊 Статистика", callback_data="adm:stats")],
        [InlineKeyboardButton(text="📢 Рассылка", callback_data="adm:bcast"),
         InlineKeyboardButton(text="🔗 Пригласить", callback_data="adm:invite")],
    ])


@router.message(F.text.startswith("/admin"))
async def cmd_admin(message: Message):
    if message.from_user.id != OWNER_ID:
        return
    await message.answer("<b>🛠 Админ-панель</b>", reply_markup=_admin_main_kb())


@router.callback_query(F.data == "adm:menu")
async def cb_admin_menu(callback: CallbackQuery):
    if callback.from_user.id != OWNER_ID:
        return await callback.answer()
    await callback.message.edit_text("<b>🛠 Админ-панель</b>", reply_markup=_admin_main_kb())
    await callback.answer()


@router.callback_query(F.data == "adm:stats")
async def cb_admin_stats(callback: CallbackQuery):
    if callback.from_user.id != OWNER_ID:
        return await callback.answer()
    stats = await db.get_stats()
    lines = ["<b>📊 Статистика</b>", ""]
    if stats["enabled"]:
        lines.append(f"Всего пользователей: {stats['users']}")
        lines.append(f"Проверок за сутки: {stats['checks_today']}")
        lines.append(f"Проверок всего: {stats['checks_total']}")
        lines.append("")
        lines.append("<b>По статусам:</b>")
        for status, count in stats["by_status"].items():
            lines.append(f"{STATUS_EMOJI.get(status, '•')} {STATUS_LABEL_RU.get(status, status)}: {count}")
    else:
        lines.append("⚠️ БД не подключена.")
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="⬅ Назад", callback_data="adm:menu")]])
    await callback.message.edit_text("\n".join(lines), reply_markup=kb)
    await callback.answer()


@router.callback_query(F.data == "adm:invite")
async def cb_admin_invite(callback: CallbackQuery):
    if callback.from_user.id != OWNER_ID:
        return await callback.answer()
    me = await callback.bot.get_me()
    link = f"https://t.me/{me.username}?start=friend"
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="⬅ Назад", callback_data="adm:menu")]])
    await callback.message.edit_text(
        f"<b>🔗 Пригласить</b>\n\nПереход по этой ссылке даёт бесплатный доступ навсегда:\n{link}",
        reply_markup=kb,
    )
    await callback.answer()


USERS_PAGE_SIZE = 8


@router.callback_query(F.data.startswith("adm:users:"))
async def cb_admin_users(callback: CallbackQuery):
    if callback.from_user.id != OWNER_ID:
        return await callback.answer()
    offset = int(callback.data.split(":")[2])
    users = await db.list_users(limit=USERS_PAGE_SIZE, offset=offset)

    rows = []
    for u in users:
        label = f"{STATUS_EMOJI.get(u.status, '•')} {u.username or u.telegram_id}"
        rows.append([InlineKeyboardButton(text=label, callback_data=f"adm:user:{u.telegram_id}")])

    nav = []
    if offset > 0:
        nav.append(InlineKeyboardButton(text="⬅ Пред.", callback_data=f"adm:users:{max(0, offset - USERS_PAGE_SIZE)}"))
    if len(users) == USERS_PAGE_SIZE:
        nav.append(InlineKeyboardButton(text="След. ➡", callback_data=f"adm:users:{offset + USERS_PAGE_SIZE}"))
    if nav:
        rows.append(nav)
    rows.append([InlineKeyboardButton(text="⬅ В меню", callback_data="adm:menu")])

    total = await db.count_users()
    text = f"<b>👥 Пользователи</b> ({total} всего)\n\nВыберите пользователя для управления:"
    if not users:
        text = "<b>👥 Пользователи</b>\n\nПока никого нет."
    await callback.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))
    await callback.answer()


@router.callback_query(F.data.startswith("adm:user:"))
async def cb_admin_user_detail(callback: CallbackQuery):
    if callback.from_user.id != OWNER_ID:
        return await callback.answer()
    target_id = int(callback.data.split(":")[2])
    user = await db.get_user(target_id)
    if not user:
        return await callback.answer("Пользователь не найден", show_alert=True)

    lines = [
        f"<b>{user.username or '(без username)'}</b>",
        f"ID: <code>{user.telegram_id}</code>",
        f"Статус: {STATUS_EMOJI.get(user.status, '•')} {STATUS_LABEL_RU.get(user.status, user.status)}",
    ]
    if user.paid_until:
        lines.append(f"Оплачено до: {user.paid_until.strftime('%d.%m.%Y')}")
    lines.append(f"Первый визит: {user.first_seen.strftime('%d.%m.%Y')}")
    if user.invited_by:
        lines.append(f"Приглашён: {user.invited_by}")

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ +30 дней", callback_data=f"adm:appr:{target_id}:30"),
         InlineKeyboardButton(text="✅ +90 дней", callback_data=f"adm:appr:{target_id}:90"),
         InlineKeyboardButton(text="✅ +365", callback_data=f"adm:appr:{target_id}:365")],
        [InlineKeyboardButton(text="🚫 Отозвать доступ", callback_data=f"adm:revoke:{target_id}")],
        [InlineKeyboardButton(text="⬅ К списку", callback_data="adm:users:0")],
    ])
    await callback.message.edit_text("\n".join(lines), reply_markup=kb)
    await callback.answer()


@router.callback_query(F.data.startswith("adm:appr:"))
async def cb_admin_approve(callback: CallbackQuery):
    if callback.from_user.id != OWNER_ID:
        return await callback.answer()
    _, _, target_id_str, days_str = callback.data.split(":")
    target_id, days = int(target_id_str), int(days_str)
    new_until = await db.extend_paid(target_id, days)
    try:
        await callback.bot.send_message(
            target_id,
            f"✅ Ваш платный доступ продлён до {new_until.strftime('%d.%m.%Y')}.",
        )
    except Exception:
        logging.exception("Не удалось уведомить пользователя %s о продлении", target_id)
    await callback.answer(f"Продлено до {new_until.strftime('%d.%m.%Y')}", show_alert=True)
    await cb_admin_user_detail(callback)


@router.callback_query(F.data.startswith("adm:revoke:"))
async def cb_admin_revoke(callback: CallbackQuery):
    if callback.from_user.id != OWNER_ID:
        return await callback.answer()
    target_id = int(callback.data.split(":")[2])
    await db.set_user_status(target_id, "free", None)
    try:
        await callback.bot.send_message(target_id, "⚠️ Ваш платный доступ был отозван.")
    except Exception:
        logging.exception("Не удалось уведомить пользователя %s об отзыве", target_id)
    await callback.answer("Доступ отозван", show_alert=True)
    await cb_admin_user_detail(callback)


@router.callback_query(F.data == "adm:bcast")
async def cb_admin_broadcast_start(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id != OWNER_ID:
        return await callback.answer()
    await state.clear()
    await state.set_state(BroadcastStates.waiting_text)
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="⬅ Отмена", callback_data="adm:menu")]])
    await callback.message.edit_text("<b>📢 Рассылка</b>\n\nПришлите текст следующим сообщением — разошлю его всем пользователям.", reply_markup=kb)
    await callback.answer()


@router.message(BroadcastStates.waiting_text, F.text, ~F.text.startswith("/"), ~F.text.in_(MENU_TEXTS))
async def receive_broadcast_text(message: Message, state: FSMContext):
    if message.from_user.id != OWNER_ID:
        return
    text = message.text
    users = await db.list_users(limit=10_000)
    sent, failed = 0, 0
    for u in users:
        try:
            await message.bot.send_message(u.telegram_id, text)
            sent += 1
        except Exception:
            failed += 1
    await message.answer(f"Рассылка завершена: доставлено {sent}, не удалось {failed}.", reply_markup=main_menu)
    await state.clear()


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

    summary_line = format_summary_line(assessments)
    await message.answer(summary_line)
    await message.answer(format_top_risks(summaries, assessments) + DISCLAIMER, reply_markup=main_menu)
    await db.log_check(message.from_user.id, "book", None, summary_line)

    ranked = sorted(assessments.items(), key=lambda kv: kv[1].score, reverse=True)
    top_for_scheme = [(inn, a) for inn, a in ranked if a.level in ("high", "medium")][:6]
    if len(top_for_scheme) >= 2:
        try:
            flow_rows = [
                FlowRow(name=summaries[inn].name, inn=inn, amount=summaries[inn].total_turnover, level=a.level)
                for inn, a in top_for_scheme
            ]
            png = render_money_flow("Наша организация", flow_rows)
            await message.answer_photo(BufferedInputFile(png, filename="money_flow.png"), caption="📈 Схема платежей — топ рискованных контрагентов")
        except Exception:
            logging.exception("Ошибка при рендере схемы платежей")

    try:
        report_text = f"<b>Отчёт по книге покупок/продаж</b>\n{summary_line}\n\n" + format_top_risks(summaries, assessments) + DISCLAIMER
        pdf_bytes = build_dossier_pdf("Отчёт по книге", "сформировано ботом Досье Плюс", report_text)
        await message.answer_document(
            BufferedInputFile(pdf_bytes, filename="Отчёт_по_книге.pdf"),
            caption="📥 PDF-отчёт по книге",
        )
    except Exception:
        logging.exception("Ошибка при генерации PDF-отчёта по книге")

    await state.clear()


def _is_valid_inn(text: str) -> bool:
    return text.isdigit() and len(text) in (10, 12)


@router.message(F.text == "🔍 Проверить контрагента")
async def start_counterparty_check(message: Message, state: FSMContext):
    await state.clear()
    await state.set_state(CardStates.waiting_inn)
    await message.answer("Введите ИНН организации или ИП (10 или 12 цифр).")





@router.message(CardStates.waiting_inn, F.text, ~F.text.startswith("/"), ~F.text.in_(MENU_TEXTS))
async def receive_inn(message: Message, state: FSMContext):
    query = message.text.strip()

    if not _is_valid_inn(query):
        if len(query) < 4:
            await message.answer("Слишком короткий запрос — нужно минимум 4 символа для поиска по названию, либо ИНН.")
            return
        await message.answer("⏳ Ищу по названию...")
        try:
            hits = await search_by_name(query, CHECKO_API_KEY)
        except Exception as e:
            logging.exception("Ошибка поиска по названию %s", query)
            await message.answer(f"❌ Не удалось выполнить поиск: {e}")
            return

        if not hits:
            await message.answer("Ничего не нашёл по этому названию. Попробуйте иначе или введите ИНН напрямую.")
            return
        if len(hits) == 1:
            inn = hits[0].inn
        else:
            lines = ["Нашёл несколько совпадений — пришлите ИНН нужной организации:\n"]
            for h in hits:
                lines.append(f"{h.name_short} — ИНН {h.inn}, {h.status}, {h.address}")
            await message.answer("\n".join(lines))
            return
    else:
        inn = query

    cached = await db.get_cached_card(inn)
    result = None
    if cached:
        text = cached + "\n\n<i>(из кэша, обновлено в течение суток)</i>"
    else:
        await message.answer("⏳ Ищу данные...")
        try:
            result = await build_card(inn, DADATA_API_KEY, DADATA_SECRET_KEY, CHECKO_API_KEY)
            text = format_card(result)
            await db.set_cached_card(inn, text)
        except Exception as e:
            logging.exception("Ошибка при построении карточки по ИНН %s", inn)
            text = f"❌ Не удалось получить данные по ИНН {inn}: {e}\nПопробуйте ещё раз или другой ИНН."

    await db.log_check(message.from_user.id, "counterparty", inn, text.split("\n")[0][:200])
    chunks = split_for_telegram(text)
    for i, chunk in enumerate(chunks):
        is_last = i == len(chunks) - 1
        await message.answer(chunk, reply_markup=main_menu if is_last else None)

    title_match = re.search(r"<b>(.*?)</b>", text)
    pdf_title = title_match.group(1) if title_match else inn
    try:
        pdf_bytes = build_dossier_pdf(pdf_title, f"ИНН {inn} · сформировано ботом Досье Плюс", text)
        await message.answer_document(
            BufferedInputFile(pdf_bytes, filename=f"Досье_{inn}.pdf"),
            caption="📥 PDF-досье (не официальная выписка из ЕГРЮЛ)",
        )
    except Exception:
        logging.exception("Ошибка при генерации PDF для ИНН %s", inn)

    if result and result.checko and result.checko.related_orgs:
        try:
            rel_rows = [
                RelationRow(
                    name=r.get("name") or "—",
                    inn=r.get("inn") or "—",
                    active="не действ" not in (r.get("status") or "").lower(),
                )
                for r in result.checko.related_orgs[:6]
            ]
            center_name = (result.dadata.name_short if result.dadata else None) or result.checko.name_short or inn
            png = render_relations(center_name, rel_rows)
            await message.answer_photo(BufferedInputFile(png, filename="relations.png"), caption="🔗 Схема связей")
        except Exception:
            logging.exception("Ошибка при рендере схемы связей")

    await message.answer("Введите следующий ИНН или название, или выберите другое действие кнопкой ниже.")
    # состояние остаётся waiting_inn — можно проверять несколько ИНН подряд
    # без повторного нажатия кнопки; любая другая кнопка меню сбросит его сама


MAX_LIST_SIZE = 20


@router.message(F.text == "📋 Список ИНН")
async def start_inn_list(message: Message, state: FSMContext):
    await state.clear()
    await state.set_state(ListStates.waiting_list)
    await message.answer(
        f"Пришлите список ИНН — каждый на новой строке, через запятую или пробел "
        f"(до {MAX_LIST_SIZE} штук за раз). Соберу один PDF со всеми компаниями (каждая с новой страницы)."
    )


@router.message(ListStates.waiting_list, F.text, ~F.text.startswith("/"), ~F.text.in_(MENU_TEXTS))
async def receive_inn_list(message: Message, state: FSMContext):
    inns = re.findall(r"\b\d{10}(?:\d{2})?\b", message.text)
    inns = list(dict.fromkeys(inns))  # убираем дубли, сохраняя порядок

    if not inns:
        await message.answer("Не нашёл ни одного ИНН (10 или 12 цифр) в сообщении. Пришлите ещё раз.")
        return
    if len(inns) > MAX_LIST_SIZE:
        await message.answer(f"Многовато за раз — нашёл {len(inns)}, лимит {MAX_LIST_SIZE}. Разбейте на части.")
        return

    await message.answer(f"⏳ Проверяю {len(inns)} ИНН, соберу один PDF на все компании сразу...")

    sections = []
    summary_lines = []
    for inn in inns:
        try:
            result = await build_card(inn, DADATA_API_KEY, DADATA_SECRET_KEY, CHECKO_API_KEY)
        except Exception:
            logging.exception("Ошибка при проверке ИНН %s в списке", inn)
            summary_lines.append(f"❌ {inn} — ошибка проверки")
            continue

        if not result.found:
            summary_lines.append(f"❓ {inn} — не найдено")
            continue

        text = format_card(result)
        await db.set_cached_card(inn, text)
        await db.log_check(message.from_user.id, "counterparty", inn, text.split("\n")[0][:200])

        name = (result.dadata.name_short if result.dadata else None) or (result.checko.name_short if result.checko else inn)
        levels = [v.level for v in (result.nominal_director, result.shell_company) if v]
        worst = "high" if "high" in levels else "medium" if "medium" in levels else "low"
        emoji = {"low": "🟢", "medium": "🟡", "high": "🔴"}[worst]
        summary_lines.append(f"{emoji} {name} (ИНН {inn})")

        sections.append((name, f"ИНН {inn} · сформировано ботом Досье Плюс", text))

    await message.answer("\n".join(summary_lines) or "Ничего не найдено.")

    if sections:
        try:
            pdf_bytes = build_multi_dossier_pdf(sections)
            await message.answer_document(
                BufferedInputFile(pdf_bytes, filename=f"Досье_список_{len(sections)}_компаний.pdf"),
                caption=f"📥 Сводное PDF-досье — {len(sections)} компаний (каждая с новой страницы)",
                reply_markup=main_menu,
            )
        except Exception:
            logging.exception("Ошибка при генерации сводного PDF для списка ИНН")
            await message.answer("⚠️ Данные получены, но не удалось собрать общий PDF.", reply_markup=main_menu)
    else:
        await message.answer("Не по чему формировать PDF.", reply_markup=main_menu)

    await state.clear()


MAX_RELATIONS_SIZE = 10


@router.message(F.text == "🔗 Найти связи")
async def start_relations_search(message: Message, state: FSMContext):
    await state.clear()
    await state.set_state(RelationsSearchStates.waiting_inns)
    await message.answer(
        f"Пришлите от 2 до {MAX_RELATIONS_SIZE} ИНН — каждый на новой строке, через запятую или пробел. "
        f"Проверю их между собой: общий директор/учредитель (в т.ч. бывшие), общий адрес, "
        f"совпадающий телефон/e-mail, известные связи по данным Checko."
    )


@router.message(RelationsSearchStates.waiting_inns, F.text, ~F.text.startswith("/"), ~F.text.in_(MENU_TEXTS))
async def receive_relations_inns(message: Message, state: FSMContext):
    inns = re.findall(r"\b\d{10}(?:\d{2})?\b", message.text)
    inns = list(dict.fromkeys(inns))

    if len(inns) < 2:
        await message.answer("Нужно минимум 2 ИНН, чтобы искать связи между ними. Пришлите ещё раз.")
        return
    if len(inns) > MAX_RELATIONS_SIZE:
        await message.answer(f"Многовато — нашёл {len(inns)}, лимит {MAX_RELATIONS_SIZE}.")
        return

    await message.answer(f"⏳ Собираю данные по {len(inns)} организациям и сверяю их между собой...")

    results = {}
    names = {}
    for inn in inns:
        try:
            result = await build_card(inn, DADATA_API_KEY, DADATA_SECRET_KEY, CHECKO_API_KEY)
        except Exception:
            logging.exception("Ошибка при проверке ИНН %s для поиска связей", inn)
            await message.answer(f"❌ {inn} — ошибка проверки, пропускаю")
            continue
        if not result.found:
            await message.answer(f"❓ {inn} — не найдено, пропускаю")
            continue
        results[inn] = result
        name = result.dadata.name_short if result.dadata and result.dadata.name_short else (
            result.checko.name_short if result.checko else inn
        )
        names[inn] = name
        await db.log_check(message.from_user.id, "counterparty", inn, f"{name} — поиск связей")

    if len(results) < 2:
        await message.answer("Не набралось хотя бы двух проверенных организаций для сравнения.", reply_markup=main_menu)
        await state.clear()
        return

    connections = find_connections(results)

    lines = [f"<b>🔗 Связи между {len(results)} организациями</b>", ""]
    for inn, name in names.items():
        lines.append(f"• {name} (ИНН {inn})")
    lines.append("")

    if not connections:
        lines.append("Явных связей между этими организациями не найдено по доступным данным.")
    else:
        lines.append(f"<b>Найдено связей: {len(connections)}</b>")
        for conn in connections:
            lines.append(f"\n🔸 <b>{conn.name_a}</b> ↔ <b>{conn.name_b}</b>")
            lines.append(f"   {conn.kind}: {conn.detail}")

    lines.append("")
    lines.append("⚠️ Оценка сформирована автоматически на основе открытых данных и не является юридическим заключением.")

    chunks = split_for_telegram("\n".join(lines))
    for i, chunk in enumerate(chunks):
        is_last = i == len(chunks) - 1
        await message.answer(chunk, reply_markup=main_menu if is_last else None)

    await state.clear()


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

    try:
        _years, fin_raw = await get_finances(inn, CHECKO_API_KEY)
        await _send_raw(message, "Checko finances", fin_raw)
    except CheckoError as e:
        await message.answer(f"Checko finances error: {e}")

    try:
        rel = await get_reliability(inn, CHECKO_API_KEY)
        await _send_raw(message, "Checko reliability", rel.raw if rel else {})
    except CheckoError as e:
        await message.answer(f"Checko reliability error: {e}")


async def _cleanup_loop():
    while True:
        try:
            await db.cleanup_old_data()
        except Exception:
            logging.exception("Ошибка при очистке старых данных")
        await asyncio.sleep(24 * 60 * 60)


async def main():
    logging.basicConfig(level=logging.INFO)

    await db.init_db()
    asyncio.create_task(_cleanup_loop())

    bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher(storage=MemoryStorage())
    dp.include_router(router)

    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
