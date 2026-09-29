"""
Кликабельные ИНН. В HTML-режиме Telegram делаем из ИНН ссылку вида
https://t.me/<бот>?start=inn_<ИНН> — при нажатии Telegram открывает чат с ботом
и сам отправляет /start inn_<ИНН>, а бот запускает проверку этого ИНН.
Имя бота узнаём один раз при старте (set_bot_username).
"""

import re

_BOT_USERNAME = ""

_LINK_RE = re.compile(r'<a href="https://t\.me/[^"]*\?start=inn_\d+">(.*?)</a>', re.S)


def set_bot_username(username: str | None) -> None:
    global _BOT_USERNAME
    _BOT_USERNAME = username or ""


def inn_url(inn: str) -> str:
    """Ссылка на проверку ИНН в боте (для PDF) или пустая строка, если имя бота ещё не известно."""
    if not _BOT_USERNAME or not inn or not str(inn).isdigit():
        return ""
    return f"https://t.me/{_BOT_USERNAME}?start=inn_{inn}"


def inn_link(inn: str, label: str | None = None) -> str:
    """ИНН в виде кликабельной ссылки; без имени бота или без ИНН — обычный текст."""
    text = label or inn
    if not _BOT_USERNAME or not inn or not str(inn).isdigit():
        return text
    return f'<a href="https://t.me/{_BOT_USERNAME}?start=inn_{inn}">{text}</a>'


def strip_inn_links(html_text: str) -> str:
    """Для PDF: возвращаем обычный текст вместо ссылок на бота."""
    return _LINK_RE.sub(r"\1", html_text)
