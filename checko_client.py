"""
Клиент Checko API v2 — карточка компании (финансы, налоги, спецрежим, МСП).
ВАЖНО: точная схема ответа Checko не полностью подтверждена документацией —
структура ниже основана на наиболее достоверной известной разметке (кириллические
ключи). При первом реальном вызове стоит свериться с фактическим JSON и поправить
пути в _parse_company, если что-то разойдётся — raw-ответ сохраняется в поле .raw
специально для этой сверки.
"""

from __future__ import annotations
import aiohttp
from dataclasses import dataclass, field

CHECKO_URL = "https://api.checko.ru/v2/company"


class CheckoError(Exception):
    pass


@dataclass
class FinanceYear:
    year: int
    revenue: float | None = None
    profit: float | None = None


@dataclass
class CheckoCompany:
    name_short: str
    status: str
    capital: float | None
    tax_system: str            # ОСН / УСН и т.д.
    is_smb: bool                # МСП
    smb_category: str          # микро / малое / среднее
    employee_count: int | None
    taxes_paid: float | None
    gov_contracts_sum: float | None
    finances: list[FinanceYear] = field(default_factory=list)
    phones: list[str] = field(default_factory=list)
    emails: list[str] = field(default_factory=list)
    raw: dict = field(default_factory=dict)


async def get_company(inn: str, api_key: str) -> CheckoCompany | None:
    async with aiohttp.ClientSession() as session:
        async with session.get(CHECKO_URL, params={"key": api_key, "inn": inn}) as resp:
            if resp.status != 200:
                text = await resp.text()
                raise CheckoError(f"Checko вернул {resp.status}: {text[:300]}")
            payload = await resp.json()

    if payload.get("meta", {}).get("status") not in (None, "ok"):
        raise CheckoError(f"Checko: {payload.get('meta')}")

    data = payload.get("data")
    if not data:
        return None
    return _parse_company(data)


def _parse_company(data: dict) -> CheckoCompany:
    finances_raw = data.get("Финансы") or {}
    finance_years = []
    for year_str, values in sorted(finances_raw.items()):
        if not year_str.isdigit():
            continue
        finance_years.append(FinanceYear(
            year=int(year_str),
            revenue=(values or {}).get("Выручка"),
            profit=(values or {}).get("ЧистПрибыль"),
        ))

    contacts = data.get("Контакты") or {}

    return CheckoCompany(
        name_short=data.get("НаимСокрЮЛ", "") or data.get("НаимСокрИП", ""),
        status=(data.get("Статус") or {}).get("Наим", ""),
        capital=(data.get("УстКап") or {}).get("Сумма"),
        tax_system=(data.get("СведНО") or {}).get("СпецРежим", ""),
        is_smb=bool(data.get("РМСП")),
        smb_category=(data.get("РМСП") or {}).get("Кат", "") if isinstance(data.get("РМСП"), dict) else "",
        employee_count=(data.get("Числ") or {}).get("Числ"),
        taxes_paid=(data.get("СведНО") or {}).get("СумУпл"),
        gov_contracts_sum=(data.get("Контракты") or {}).get("СумГК"),
        finances=finance_years,
        phones=contacts.get("Тел", []) or [],
        emails=contacts.get("Емэйл", []) or [],
        raw=data,
    )
