"""
Клиент Checko API v2 — карточка компании.
Схема ниже основана на реальном подтверждённом ответе API (не догадке) —
см. /debug в боте для сверки при появлении новых расхождений.
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
    assets: float | None = None


@dataclass
class ManagingOrg:
    inn: str
    name: str
    unreliable: bool = False


@dataclass
class CheckoDirector:
    name: str
    inn: str = ""
    post: str = ""
    record_date: str = ""
    end_date: str = ""
    unreliable: bool = False
    is_mass: bool = False
    disqualified: bool = False


@dataclass
class CheckoFounder:
    name: str
    inn: str = ""
    share: str = ""
    kind: str = ""   # физлицо / рос. организация / иностр. организация / ПИФ / РФ
    unreliable: bool = False
    is_mass: bool = False


@dataclass
class RiskFlags:
    disqualified_person: bool = False    # ДисквЛица — в руководстве дисквалифицированное лицо
    mass_director: bool = False           # МассРуковод — директор массовый
    mass_founder: bool = False            # МассУчред — учредитель массовый
    unfair_supplier: bool = False         # НедобПост — реестр недобросовестных поставщиков
    sanctions: bool = False               # Санкции — сама организация под санкциями
    sanctions_founder: bool = False       # СанкцУчр — учредитель под санкциями
    illegal_finance: bool = False         # НелегалФин
    in_bankruptcy_registry: bool = False  # ЕФРСБ непусто


@dataclass
class CheckoCompany:
    name_short: str
    status: str
    capital: float | None
    tax_system: str
    is_smb: bool
    smb_category: str
    employee_count: int | None
    taxes_paid: float | None
    gov_contracts_sum: float | None
    reg_date: str
    region: str
    tax_office_name: str
    tax_office_code: str
    tax_office_reg_date: str
    reg_authority_name: str
    okved_extra_count: int
    vat_paid: float | None
    address: str
    address_unreliable: bool
    mass_address_count: int
    okved_name: str
    managing_org: ManagingOrg | None
    related_orgs: list[dict] = field(default_factory=list)
    directors: list[CheckoDirector] = field(default_factory=list)
    founders: list[CheckoFounder] = field(default_factory=list)
    flags: RiskFlags = field(default_factory=RiskFlags)
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


def _to_float(val) -> float | None:
    if val is None or val == "":
        return None
    try:
        return float(val)
    except (ValueError, TypeError):
        return None


def _parse_company(data: dict) -> CheckoCompany:
    finances_raw = data.get("Финансы") or {}
    finance_years = []
    for year_str, values in sorted(finances_raw.items()):
        if not str(year_str).isdigit():
            continue
        finance_years.append(FinanceYear(
            year=int(year_str),
            revenue=_to_float((values or {}).get("Выручка")),
            profit=_to_float((values or {}).get("ЧистПрибыль")),
            assets=_to_float((values or {}).get("Актив")),
        ))

    contacts = data.get("Контакты") or {}
    yur_adres = data.get("ЮрАдрес") or {}
    okved = data.get("ОКВЭД") or {}
    smb = data.get("РМСП") or {}
    nalogi = data.get("Налоги") or {}
    upr_org_raw = data.get("УпрОрг")
    tek_fns = data.get("ТекФНС") or {}
    reg_fns = data.get("РегФНС") or {}
    okved_extra = data.get("ОКВЭДДоп") or []
    sved_upl = nalogi.get("СведУпл") or []
    vat_paid = None
    for item in sved_upl:
        if "добавленную стоимость" in (item.get("Наим") or ""):
            vat_paid = _to_float(item.get("Сумма"))
            break
    related_orgs = [
        {"inn": r.get("ИНН", ""), "name": r.get("НаимСокр", ""), "status": r.get("Статус", "")}
        for r in (data.get("СвязУчред") or [])
    ]

    managing_org = None
    if upr_org_raw:
        managing_org = ManagingOrg(
            inn=upr_org_raw.get("ИНН", ""),
            name=upr_org_raw.get("НаимСокр", "") or upr_org_raw.get("НаимПолн", ""),
            unreliable=bool(upr_org_raw.get("Недост")),
        )

    # ВАЖНО: поля внутри "Руковод" и "Учред" не подтверждены реальным примером —
    # у тестовой компании оба списка были пустыми. Разметка ниже — обоснованное
    # предположение по стилю остальных полей Checko, нужно сверить на компании,
    # где эти списки реально заполнены (обычное ООО, не АО с регистратором).
    directors = []
    for item in (data.get("Руковод") or []):
        directors.append(CheckoDirector(
            name=item.get("ФИО", ""),
            inn=item.get("ИНН", ""),
            post=item.get("НаимДолжн", "") or item.get("ВидДолжн", ""),
            record_date=item.get("ДатаЗаписи", ""),
            end_date=item.get("ДатаКон", ""),
            unreliable=bool(item.get("Недост")),
            is_mass=bool(item.get("МассРуковод")),
            disqualified=bool(item.get("ДисквЛицо")),
        ))

    founders = []
    uchred = data.get("Учред") or {}

    def _share(item: dict) -> str:
        d = item.get("Доля")
        if isinstance(d, dict):
            pct = d.get("Процент")
            return f"{pct}%" if pct is not None else ""
        return str(d) if d else ""

    for item in (uchred.get("ФЛ") or []):
        founders.append(CheckoFounder(
            name=item.get("ФИО", ""), inn=item.get("ИНН", ""), share=_share(item), kind="физлицо",
            unreliable=bool(item.get("Недост")), is_mass=bool(item.get("МассУчред")),
        ))
    for item in (uchred.get("РосОрг") or []):
        founders.append(CheckoFounder(
            name=item.get("НаимСокр", "") or item.get("НаимПолн", ""),
            inn=item.get("ИНН", ""), share=_share(item), kind="рос. организация",
            unreliable=bool(item.get("Недост")), is_mass=bool(item.get("МассУчред")),
        ))
    for item in (uchred.get("ИнОрг") or []):
        founders.append(CheckoFounder(name=item.get("НаимПолн", ""), share=_share(item), kind="иностр. организация"))
    for item in (uchred.get("ПИФ") or []):
        founders.append(CheckoFounder(name=item.get("Наим", "") or item.get("НаимПолн", ""), share=_share(item), kind="ПИФ"))

    flags = RiskFlags(
        disqualified_person=bool(data.get("ДисквЛица")),
        mass_director=bool(data.get("МассРуковод")),
        mass_founder=bool(data.get("МассУчред")),
        unfair_supplier=bool(data.get("НедобПост")),
        sanctions=bool(data.get("Санкции")),
        sanctions_founder=bool(data.get("СанкцУчр")),
        illegal_finance=bool(data.get("НелегалФин")),
        in_bankruptcy_registry=bool(data.get("ЕФРСБ")),
    )

    return CheckoCompany(
        name_short=data.get("НаимСокр", "") or data.get("НаимПолн", ""),
        status=(data.get("Статус") or {}).get("Наим", ""),
        capital=_to_float((data.get("УстКап") or {}).get("Сумма")),
        # ОсобРежим — список спецрежимов; пусто значит ОСН (общий режим)
        tax_system=", ".join(nalogi.get("ОсобРежим") or []) or "ОСН",
        is_smb=bool(smb),
        smb_category=smb.get("Кат", "") if isinstance(smb, dict) else "",
        employee_count=data.get("СЧР"),
        taxes_paid=_to_float(nalogi.get("СумУпл")),
        gov_contracts_sum=_to_float((data.get("Контракты") or {}).get("СумГК")),
        reg_date=data.get("ДатаРег", ""),
        region=(data.get("Регион") or {}).get("Наим", ""),
        tax_office_name=tek_fns.get("НаимОрг", ""),
        tax_office_code=tek_fns.get("КодОрг", ""),
        tax_office_reg_date=tek_fns.get("ДатаПостУч", ""),
        reg_authority_name=reg_fns.get("НаимОрг", ""),
        okved_extra_count=len(okved_extra),
        vat_paid=vat_paid,
        related_orgs=related_orgs,
        address=yur_adres.get("АдресРФ", ""),
        address_unreliable=bool(yur_adres.get("Недост")),
        mass_address_count=len(yur_adres.get("МассАдрес") or []),
        okved_name=okved.get("Наим", ""),
        managing_org=managing_org,
        directors=directors,
        founders=founders,
        flags=flags,
        finances=finance_years,
        phones=contacts.get("Тел", []) or [],
        emails=contacts.get("Емэйл", []) or [],
        raw=data,
    )   
