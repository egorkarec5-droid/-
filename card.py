from __future__ import annotations
import asyncio
from dataclasses import dataclass

from dadata_client import get_party, DaDataParty, DaDataError
from checko_client import get_company, CheckoCompany, CheckoError


@dataclass
class CardResult:
    inn: str
    dadata: DaDataParty | None
    checko: CheckoCompany | None
    dadata_error: str | None = None
    checko_error: str | None = None

    @property
    def found(self) -> bool:
        return self.dadata is not None or self.checko is not None


async def build_card(inn: str, dadata_key: str, dadata_secret: str, checko_key: str) -> CardResult:
    """Запрашивает оба источника параллельно, не даёт сбою одного уронить другой."""

    async def _dadata():
        try:
            return await get_party(inn, dadata_key, dadata_secret), None
        except DaDataError as e:
            return None, str(e)

    async def _checko():
        try:
            return await get_company(inn, checko_key), None
        except CheckoError as e:
            return None, str(e)

    (dadata_res, dadata_err), (checko_res, checko_err) = await asyncio.gather(_dadata(), _checko())

    return CardResult(
        inn=inn,
        dadata=dadata_res,
        checko=checko_res,
        dadata_error=dadata_err,
        checko_error=checko_err,
    )


STATUS_LABELS = {
    "ACTIVE": "🟢 действует",
    "LIQUIDATING": "🟡 в процессе ликвидации",
    "LIQUIDATED": "🔴 ликвидирована",
    "BANKRUPT": "🔴 банкротство",
    "REORGANIZING": "🟡 реорганизация",
}


def format_card(result: CardResult) -> str:
    d = result.dadata
    c = result.checko

    if not result.found:
        return (
            f"Не нашёл организацию по ИНН {result.inn}. "
            f"Проверьте номер или попробуйте позже."
        )

    name = (d.name_short if d else None) or (c.name_short if c else None) or result.inn
    lines = [f"<b>{name}</b>", f"ИНН {result.inn}"]
    if d and d.ogrn:
        lines.append(f"ОГРН {d.ogrn}")

    lines.append("")
    lines.append("<b>Основное</b>")
    if d:
        lines.append(f"Статус: {STATUS_LABELS.get(d.status, d.status or '—')}")
        addr_flags = []
        if d.address_is_mass:
            addr_flags.append("⚠️ признаки массового адреса")
        if c and c.address_unreliable:
            addr_flags.append("🔴 недостоверность в ЕГРЮЛ")
        addr_suffix = (" " + " ".join(addr_flags)) if addr_flags else ""
        lines.append(f"Адрес: {d.address or (c.address if c else '—')}{addr_suffix}")
        if c and c.mass_address_count:
            lines.append(f"На этом адресе ещё организаций: {c.mass_address_count}")
        okved_name = d.okved_name or (c.okved_name if c else "")
        if d.okved or okved_name:
            lines.append(f"ОКВЭД: {d.okved} {okved_name}".strip())
        if d.registration_date:
            lines.append(f"Регистрация: {d.registration_date}")
        if c and c.region:
            lines.append(f"Регион: {c.region}")
    else:
        lines.append(f"⚠️ DaData недоступна: {result.dadata_error or 'нет данных'}")

    if d and d.management:
        lines.append("")
        lines.append("<b>Руководитель</b>")
        m = d.management
        lines.append(f"{m.name}" + (f" (ИНН {m.inn})" if m.inn else ""))

    if d and d.founders:
        lines.append("")
        lines.append("<b>Учредители</b>")
        for f in d.founders:
            share = f" — {f.share}" if f.share else ""
            lines.append(f"{f.name}" + (f" (ИНН {f.inn})" if f.inn else "") + share)

    if c:
        lines.append("")
        lines.append("<b>Финансы и налоги</b>")
        if c.finances:
            for fy in c.finances[-3:]:
                rev = f"{fy.revenue:,.0f} ₽".replace(",", " ") if fy.revenue is not None else "—"
                profit = f"{fy.profit:,.0f} ₽".replace(",", " ") if fy.profit is not None else "—"
                lines.append(f"{fy.year}: выручка {rev}, прибыль {profit}")
        if c.capital is not None:
            lines.append(f"Уставный капитал: {c.capital:,.0f} ₽".replace(",", " "))
        if c.tax_system:
            lines.append(f"Налоговый режим: {c.tax_system}")
        if c.taxes_paid is not None:
            lines.append(f"Уплачено налогов: {c.taxes_paid:,.0f} ₽".replace(",", " "))
        if c.employee_count is not None:
            lines.append(f"Сотрудники: {c.employee_count}")
        if c.is_smb:
            lines.append(f"МСП: да" + (f", {c.smb_category}" if c.smb_category else ""))
        if c.gov_contracts_sum:
            lines.append(f"Госконтракты: {c.gov_contracts_sum:,.0f} ₽".replace(",", " "))
        if c.phones:
            lines.append(f"Телефон: {c.phones[0]}")
        if c.emails:
            lines.append(f"E-mail: {c.emails[0]}")
    elif result.checko_error:
        lines.append("")
        lines.append(f"⚠️ Checko недоступна: {result.checko_error}")

    lines.append("")
    lines.append(
        "⚠️ Оценка сформирована автоматически на основе открытых данных "
        "и не является юридическим заключением."
    )

    return "\n".join(lines)
