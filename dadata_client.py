"""
Клиент DaData — метод "Организация по ИНН" (findById/party).
Документация: https://dadata.ru/api/find-party/
Схема ответа хорошо задокументирована, поля указаны с высокой уверенностью.
"""

from __future__ import annotations
import aiohttp
from dataclasses import dataclass, field
from datetime import datetime, timezone

DADATA_URL = "https://suggestions.dadata.ru/suggestions/api/4_1/rs/findById/party"


class DaDataError(Exception):
    pass


@dataclass
class Manager:
    name: str
    post: str = ""
    inn: str = ""


@dataclass
class Founder:
    name: str
    inn: str = ""
    share: str = ""


@dataclass
class DaDataParty:
    inn: str
    kpp: str
    ogrn: str
    name_short: str
    opf_short: str            # ИП / ООО / АО и т.д. — для выбора формы фигуры на схеме
    status: str                # ACTIVE / LIQUIDATING / LIQUIDATED / ...
    registration_date: str
    address: str
    address_is_mass: bool       # массовый адрес регистрации
    okved: str
    okved_name: str
    management: Manager | None
    founders: list[Founder] = field(default_factory=list)
    branch_count: int = 0
    employee_count: int | None = None
    raw: dict = field(default_factory=dict)


async def get_party(inn: str, api_key: str, secret_key: str) -> DaDataParty | None:
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "Authorization": f"Token {api_key}",
        "X-Secret": secret_key,
    }
    async with aiohttp.ClientSession() as session:
        async with session.post(DADATA_URL, json={"query": inn}, headers=headers) as resp:
            if resp.status != 200:
                text = await resp.text()
                raise DaDataError(f"DaData вернул {resp.status}: {text[:300]}")
            payload = await resp.json()

    suggestions = payload.get("suggestions") or []
    if not suggestions:
        return None

    data = suggestions[0].get("data") or {}
    return _parse_party(data)


def _ms_to_date(ms) -> str:
    if not ms:
        return ""
    try:
        return datetime.fromtimestamp(int(ms) / 1000, tz=timezone.utc).strftime("%d.%m.%Y")
    except (ValueError, OSError):
        return str(ms)


def _parse_party(data: dict) -> DaDataParty:
    state = data.get("state") or {}
    address = data.get("address") or {}
    address_data = address.get("data") or {}
    opf = data.get("opf") or {}
    management_raw = data.get("management") or {}
    okveds = data.get("okveds") or []
    founders_raw = data.get("founders") or []

    management = None
    if management_raw.get("name"):
        management = Manager(
            name=management_raw.get("name", ""),
            post=management_raw.get("post", ""),
            inn=management_raw.get("inn", ""),
        )

    founders = [
        Founder(name=f.get("name", ""), inn=f.get("inn", ""), share=f.get("share", {}).get("value", ""))
        for f in founders_raw
        if f.get("name")
    ]

    return DaDataParty(
        inn=data.get("inn", ""),
        kpp=data.get("kpp", ""),
        ogrn=data.get("ogrn", ""),
        name_short=(data.get("name") or {}).get("short_with_opf", "") or (data.get("name") or {}).get("full_with_opf", ""),
        opf_short=opf.get("short", ""),
        status=state.get("status", ""),
        registration_date=_ms_to_date(state.get("registration_date")),
        address=address.get("value", ""),
        # qc == "2" означает "адрес не найден/массовый" в квалификаторе DaData;
        # это эвристика, при первом реальном ответе стоит свериться с document_context
        address_is_mass=str(address_data.get("qc") or "") == "2",
        okved=data.get("okved", ""),
        okved_name=next((o.get("name", "") for o in okveds if o.get("main")), ""),
        management=management,
        founders=founders,
        branch_count=data.get("branch_count", 0) or 0,
        employee_count=data.get("employee_count"),
        raw=data,
    )
