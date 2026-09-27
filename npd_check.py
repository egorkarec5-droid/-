"""
Проверка статуса самозанятого (плательщика НПД) по ИНН — официальный бесплатный
API ФНС (statusnpd.nalog.ru). Актуально только для 12-значных ИНН (физлицо/ИП) —
у самозанятых нет отдельной организации, это статус физлица.
ФНС не гарантирует стабильность этого API — используем с обработкой ошибок,
падение этого запроса не должно ронять всю остальную проверку.
"""

from __future__ import annotations
import datetime as dt
import aiohttp

NPD_URL = "https://statusnpd.nalog.ru/api/v1/tracker/taxpayer_status"


async def check_npd_status(inn: str) -> bool | None:
    """Возвращает True/False, если ФНС ответила определённо, None — если запрос не удался."""
    if len(inn) != 12:
        return None
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                NPD_URL,
                json={"inn": inn, "requestDate": dt.date.today().isoformat()},
                timeout=aiohttp.ClientTimeout(total=8),
            ) as resp:
                if resp.status != 200:
                    return None
                data = await resp.json()
                return bool(data.get("status"))
    except Exception:
        return None
