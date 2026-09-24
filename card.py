from __future__ import annotations
import asyncio
from dataclasses import dataclass, field

from dadata_client import get_party, DaDataParty, DaDataError
from checko_client import (
    get_company, get_finances, get_enforcements, get_legal_cases, get_inspections,
    CheckoCompany, FinanceYear, Enforcement, LegalCasesSummary, Inspection, CheckoError,
)
from company_analysis import analyze_nominal_director, analyze_shell_company, Verdict


@dataclass
class CardResult:
    inn: str
    dadata: DaDataParty | None
    checko: CheckoCompany | None
    finances: list[FinanceYear] = field(default_factory=list)
    enforcements: list[Enforcement] = field(default_factory=list)
    legal_cases: LegalCasesSummary | None = None
    inspections: list[Inspection] = field(default_factory=list)
    nominal_director: Verdict | None = None
    shell_company: Verdict | None = None
    dadata_error: str | None = None
    checko_error: str | None = None
    finances_error: str | None = None

    @property
    def found(self) -> bool:
        return self.dadata is not None or self.checko is not None


async def build_card(inn: str, dadata_key: str, dadata_secret: str, checko_key: str) -> CardResult:
    """Запрашивает все источники параллельно, не даёт сбою одного уронить другие."""

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

    async def _finances():
        try:
            years, _raw = await get_finances(inn, checko_key)
            return years, None
        except CheckoError as e:
            return [], str(e)

    async def _enforcements():
        try:
            return await get_enforcements(inn, checko_key), None
        except CheckoError as e:
            return [], str(e)

    async def _legal_cases():
        try:
            return await get_legal_cases(inn, checko_key), None
        except CheckoError as e:
            return None, str(e)

    async def _inspections():
        try:
            return await get_inspections(inn, checko_key), None
        except CheckoError as e:
            return [], str(e)

    (
        (dadata_res, dadata_err),
        (checko_res, checko_err),
        (fin_res, fin_err),
        (enf_res, _enf_err),
        (legal_res, _legal_err),
        (insp_res, _insp_err),
    ) = await asyncio.gather(_dadata(), _checko(), _finances(), _enforcements(), _legal_cases(), _inspections())

    nominal = analyze_nominal_director(checko_res, dadata_res)
    shell = analyze_shell_company(checko_res, dadata_res, fin_res, enf_res, legal_res, insp_res)

    return CardResult(
        inn=inn,
        dadata=dadata_res,
        checko=checko_res,
        finances=fin_res,
        enforcements=enf_res,
        legal_cases=legal_res,
        inspections=insp_res,
        nominal_director=nominal,
        shell_company=shell,
        dadata_error=dadata_err,
        checko_error=checko_err,
        finances_error=fin_err,
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
        okved_extra = f" (+{c.okved_extra_count} видов деятельности)" if c and c.okved_extra_count else ""
        if d.okved or okved_name:
            lines.append(f"ОКВЭД: {d.okved} {okved_name}{okved_extra}".strip())
        if d.registration_date:
            lines.append(f"Регистрация: {d.registration_date}")
        if c and c.region:
            lines.append(f"Регион: {c.region}")
        if c and c.tax_office_name:
            lines.append(f"Налоговый орган: {c.tax_office_name} (с {c.tax_office_reg_date})")
        if c and c.reg_authority_name:
            lines.append(f"Регистрирующий орган: {c.reg_authority_name}")
    else:
        lines.append(f"⚠️ DaData недоступна: {result.dadata_error or 'нет данных'}")

    if d and d.management:
        lines.append("")
        lines.append("<b>Руководитель</b>")
        m = d.management
        lines.append(f"{m.name}" + (f" (ИНН {m.inn})" if m.inn else ""))
        if c and c.managing_org and c.managing_org.inn == m.inn:
            tag = " 🔴 недостоверность" if c.managing_org.unreliable else ""
            lines.append(f"(управляющая организация{tag})")
    elif c and c.managing_org:
        lines.append("")
        lines.append("<b>Руководитель</b>")
        mo = c.managing_org
        tag = " 🔴 недостоверность" if mo.unreliable else ""
        lines.append(f"{mo.name} (ИНН {mo.inn}) — управляющая организация{tag}")

    if c and c.directors:
        if not (d and d.management) and not (c and c.managing_org):
            lines.append("")
            lines.append("<b>Руководитель</b>")
        lines.append("")
        lines.append("<b>История руководителей</b>")
        for dir_ in c.directors:
            period = ""
            if dir_.record_date:
                period = f" — с {dir_.record_date}" + (f" по {dir_.end_date}" if dir_.end_date else " (текущий)")
            marks = ""
            if dir_.disqualified:
                marks += " 🔴 дисквалифицирован"
            if dir_.is_mass:
                marks += " 🔴 массовый руководитель"
            if dir_.unreliable:
                marks += " 🔴 недостоверность"
            lines.append(f"{dir_.name}" + (f" (ИНН {dir_.inn})" if dir_.inn else "") + (f", {dir_.post}" if dir_.post else "") + period + marks)

    founders_all = list(d.founders) if d and d.founders else []
    if c and c.founders:
        known_inns = {f.inn for f in founders_all if f.inn}
        for cf in c.founders:
            if cf.inn and cf.inn in known_inns:
                continue
            founders_all.append(cf)

    if founders_all:
        lines.append("")
        lines.append("<b>Учредители</b>")
        for f in founders_all:
            share = f" — {f.share}" if getattr(f, "share", "") else ""
            kind = f" ({f.kind})" if getattr(f, "kind", "") else ""
            marks = ""
            if getattr(f, "is_mass", False):
                marks += " 🔴 массовый учредитель"
            if getattr(f, "unreliable", False):
                marks += " 🔴 недостоверность"
            lines.append(f"{f.name}" + (f" (ИНН {f.inn})" if f.inn else "") + share + kind + marks)

    if c:
        lines.append("")
        lines.append("<b>Финансы и налоги</b>")
        finances = result.finances or c.finances
        if finances:
            for fy in finances[-3:]:
                rev = f"{fy.revenue:,.0f} ₽".replace(",", " ") if fy.revenue is not None else "—"
                profit = f"{fy.profit:,.0f} ₽".replace(",", " ") if fy.profit is not None else "—"
                assets = f", актив {fy.assets:,.0f} ₽".replace(",", " ") if fy.assets is not None else ""
                lines.append(f"{fy.year}: выручка {rev}, прибыль {profit}{assets}")
        elif result.finances_error:
            lines.append(f"⚠️ Финансы недоступны: {result.finances_error}")
        if c.capital is not None:
            lines.append(f"Уставный капитал: {c.capital:,.0f} ₽".replace(",", " "))
        if c.tax_system:
            lines.append(f"Налоговый режим: {c.tax_system}")
        if c.vat_paid is not None:
            lines.append(f"НДС уплачено: {c.vat_paid:,.0f} ₽".replace(",", " "))
        if c.taxes_paid is not None:
            lines.append(f"Всего уплачено налогов: {c.taxes_paid:,.0f} ₽".replace(",", " "))
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

    if c and c.related_orgs:
        lines.append("")
        lines.append(f"<b>Связанные организации</b> ({len(c.related_orgs)})")
        for r in c.related_orgs[:5]:
            status_mark = "🔴" if r.get("status") and "не действ" in r.get("status", "").lower() else "🟢"
            lines.append(f"{status_mark} {r.get('name', '')}" + (f" (ИНН {r['inn']})" if r.get("inn") else ""))
        if len(c.related_orgs) > 5:
            lines.append(f"…и ещё {len(c.related_orgs) - 5}")

    if result.enforcements:
        total_debt = sum(e.debt_remaining or e.debt_sum or 0 for e in result.enforcements)
        lines.append("")
        lines.append(f"<b>Исполнительные производства (ФССП)</b> — {len(result.enforcements)}")
        lines.append(f"🔴 Общая непогашенная задолженность: {total_debt:,.0f} ₽".replace(",", " "))
        for e in result.enforcements[:3]:
            sum_str = f"{(e.debt_remaining or e.debt_sum or 0):,.0f} ₽".replace(",", " ")
            lines.append(f"• №{e.number} от {e.date} — {e.subject}, {sum_str}")
        if len(result.enforcements) > 3:
            lines.append(f"…и ещё {len(result.enforcements) - 3}")

    if result.legal_cases and result.legal_cases.cases:
        lc = result.legal_cases
        as_defendant = sum(1 for c in lc.cases if c.role == "ответчик")
        as_plaintiff = len(lc.cases) - as_defendant
        lines.append("")
        lines.append(f"<b>Арбитражные дела</b> — {len(lc.cases)} (истец: {as_plaintiff}, ответчик: {as_defendant})")
        lines.append(f"Общая сумма исков: {lc.total_claim_sum:,.0f} ₽".replace(",", " "))
        for c_ in sorted(lc.cases, key=lambda x: x.date, reverse=True)[:3]:
            sum_str = f", {c_.claim_sum:,.0f} ₽".replace(",", " ") if c_.claim_sum else ""
            lines.append(f"• №{c_.number} от {c_.date} — {c_.role}{sum_str}")
        if len(lc.cases) > 3:
            lines.append(f"…и ещё {len(lc.cases) - 3}")

    if result.inspections:
        with_violations = [i for i in result.inspections if i.violations_found]
        lines.append("")
        lines.append(f"<b>Проверки</b> — {len(result.inspections)}" + (f", из них с нарушениями: {len(with_violations)}" if with_violations else ""))

    if c:
        flag_lines = []
        if c.flags.disqualified_person:
            flag_lines.append("🔴 В руководстве — дисквалифицированное лицо")
        if c.flags.mass_director:
            flag_lines.append("🔴 Директор — массовый (числится ещё во многих организациях)")
        if c.flags.mass_founder:
            flag_lines.append("🔴 Учредитель — массовый")
        if c.flags.unfair_supplier:
            flag_lines.append("🔴 В реестре недобросовестных поставщиков")
        if c.flags.sanctions:
            flag_lines.append("🔴 Организация под санкциями")
        if c.flags.sanctions_founder:
            flag_lines.append("🟡 Учредитель под санкциями")
        if c.flags.illegal_finance:
            flag_lines.append("🔴 Признаки нелегальных финансовых операций")
        if c.flags.in_bankruptcy_registry:
            flag_lines.append("🔴 Есть записи в реестре банкротств (ЕФРСБ)")
        if flag_lines:
            lines.append("")
            lines.append("<b>Флаги риска</b>")
            lines.extend(flag_lines)

    if result.nominal_director or result.shell_company:
        lines.append("")
        lines.append("<b>📊 Расширенная аналитика</b>")

        nd = result.nominal_director
        if nd:
            lines.append(f"\n{nd.emoji} <b>Номинальный директор:</b> {nd.label} ({nd.score}/100)")
            for f in nd.flags:
                lines.append(f"   • {f.label}: {f.detail}")
            if not nd.flags:
                lines.append("   Явных признаков не найдено по доступным данным")

        sc = result.shell_company
        if sc:
            lines.append(f"\n{sc.emoji} <b>Техническая/обнальная организация:</b> {sc.label} ({sc.score}/100)")
            for f in sc.flags:
                lines.append(f"   • {f.label}: {f.detail}")
            if not sc.flags:
                lines.append("   Явных признаков не найдено по доступным данным")

    lines.append("")
    lines.append(
        "⚠️ Оценка сформирована автоматически на основе открытых данных "
        "и не является юридическим заключением. Расширенная аналитика — эвристика "
        "по совокупности признаков, а не установленный факт."
    )

    return "\n".join(lines)
