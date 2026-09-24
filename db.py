"""
Работа с базой данных: подключение, кэш карточек (сутки), история проверок
(7 дней), фоновая очистка. Postgres на Railway отдаёт DATABASE_URL сам —
достаточно подключить Postgres-плагин в проекте, переменная появится сама.
"""

from __future__ import annotations
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, delete
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession

from config import DATABASE_URL
from models import Base, User, Check, CounterpartyCache

CACHE_TTL = timedelta(hours=24)
HISTORY_RETENTION = timedelta(days=7)
CACHE_RETENTION = timedelta(days=2)  # с запасом сверх 24ч TTL, просто для чистки таблицы

_engine = None
_session_maker: async_sessionmaker[AsyncSession] | None = None


def _normalize_url(url: str) -> str:
    # Railway отдаёт postgresql://, а asyncpg-драйверу нужен postgresql+asyncpg://
    if url.startswith("postgresql://"):
        return url.replace("postgresql://", "postgresql+asyncpg://", 1)
    return url


async def init_db():
    global _engine, _session_maker
    if not DATABASE_URL:
        logging.warning("DATABASE_URL не задан — БД отключена, кэш/история работать не будут.")
        return
    _engine = create_async_engine(_normalize_url(DATABASE_URL), pool_pre_ping=True)
    _session_maker = async_sessionmaker(_engine, expire_on_commit=False)
    async with _engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    logging.info("БД инициализирована.")


def _enabled() -> bool:
    return _session_maker is not None


async def upsert_user(telegram_id: int, username: str | None, invited_by: int | None = None):
    if not _enabled():
        return
    async with _session_maker() as session:
        existing = await session.get(User, telegram_id)
        if existing is None:
            session.add(User(telegram_id=telegram_id, username=username, invited_by=invited_by))
            await session.commit()


async def log_check(requested_by: int, kind: str, inn: str | None, summary: str | None):
    if not _enabled():
        return
    async with _session_maker() as session:
        session.add(Check(requested_by=requested_by, kind=kind, inn=inn, summary=summary))
        await session.commit()


async def get_last_check(requested_by: int, inn: str) -> Check | None:
    """Для функции 'сравнить с прошлой проверкой' — последняя проверка этого ИНН этим пользователем."""
    if not _enabled():
        return None
    async with _session_maker() as session:
        result = await session.execute(
            select(Check)
            .where(Check.requested_by == requested_by, Check.inn == inn, Check.kind == "counterparty")
            .order_by(Check.created_at.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()


async def get_cached_card(inn: str) -> str | None:
    if not _enabled():
        return None
    async with _session_maker() as session:
        cached = await session.get(CounterpartyCache, inn)
        if cached is None:
            return None
        if datetime.now(timezone.utc) - cached.fetched_at > CACHE_TTL:
            return None
        return cached.card_text


async def set_cached_card(inn: str, card_text: str):
    if not _enabled():
        return
    async with _session_maker() as session:
        existing = await session.get(CounterpartyCache, inn)
        if existing:
            existing.card_text = card_text
            existing.fetched_at = datetime.now(timezone.utc)
        else:
            session.add(CounterpartyCache(inn=inn, card_text=card_text))
        await session.commit()


async def cleanup_old_data():
    if not _enabled():
        return
    now = datetime.now(timezone.utc)
    async with _session_maker() as session:
        await session.execute(delete(Check).where(Check.created_at < now - HISTORY_RETENTION))
        await session.execute(delete(CounterpartyCache).where(CounterpartyCache.fetched_at < now - CACHE_RETENTION))
        await session.commit()
    logging.info("Очистка старых данных выполнена (история > 7 дней, кэш > 2 дней).")


async def get_stats() -> dict:
    if not _enabled():
        return {"enabled": False}
    from sqlalchemy import func

    now = datetime.now(timezone.utc)
    async with _session_maker() as session:
        users_count = (await session.execute(select(func.count()).select_from(User))).scalar_one()
        checks_today = (await session.execute(
            select(func.count()).select_from(Check).where(Check.created_at > now - timedelta(days=1))
        )).scalar_one()
        checks_total = (await session.execute(select(func.count()).select_from(Check))).scalar_one()
    return {
        "enabled": True,
        "users": users_count,
        "checks_today": checks_today,
        "checks_total": checks_total,
    }
