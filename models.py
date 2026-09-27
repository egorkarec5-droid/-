from datetime import datetime, timezone

from sqlalchemy import BigInteger, String, DateTime, Text, Index
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    telegram_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    username: Mapped[str | None] = mapped_column(String, nullable=True)
    first_seen: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    invited_by: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    status: Mapped[str] = mapped_column(String, default="free")   # free | paid | free_invite | blocked
    paid_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Check(Base):
    """История проверок — хранится 7 дней, дальше чистится фоновой задачей."""
    __tablename__ = "checks"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    requested_by: Mapped[int] = mapped_column(BigInteger, index=True)
    kind: Mapped[str] = mapped_column(String)          # "counterparty" | "book"
    inn: Mapped[str | None] = mapped_column(String, nullable=True)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, index=True)

    __table_args__ = (
        Index("ix_checks_requested_by_created_at", "requested_by", "created_at"),
    )


class CounterpartyCache(Base):
    """Кэш готовой карточки контрагента на сутки — экономит запросы к DaData/Checko."""
    __tablename__ = "counterparty_cache"

    inn: Mapped[str] = mapped_column(String, primary_key=True)
    card_text: Mapped[str] = mapped_column(Text)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, index=True)
