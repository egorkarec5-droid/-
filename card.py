from __future__ import annotations
import asyncio
from dataclasses import dataclass, field

from dadata_client import get_party, DaDataParty, DaDataError
from checko_client import (
    get_company, get_finances, get_enforcements, get_legal_cases, get_inspections, get_reliability, get_contracts,
    get_entrepreneur, get_person, get_fedresurs, get_bankruptcy_messages, get_timeline,
    CheckoCompany, FinanceYear, Enforcement, LegalCasesSummary, Inspection, Reliability, Contract,
    Entrepreneur, Person, FedresursMessage, BankruptcyMessage, TimelineEvent, CheckoError,
)
from company_analysis import analyze_nominal_director, analyze_shell_company, Verdict
from npd_check import check_npd_status
from links import inn_link


@dataclass
class CardResult:
    inn: str
    dadata: DaDataParty | None
    checko: CheckoCompany | None
    finances: list[FinanceYear] = field(default_factory=list)
    enforcements: list[Enforcement] = field(default_factory=list)
    legal_cases: LegalCasesSummary | None = None
    inspections: list[Inspection] = field(default_factory=list)
    reliability: Reliability | None = None
    contracts: list[Contract] = field(default_factory=list)
    npd_status: bool | None = None
    entrepreneur: Entrepreneur | None = None
    person: Person | None = None
    director_person: Person | None = None
    fedresurs: list[FedresursMessage] = field(default_factory=list)
    bankruptcy_messages: list[BankruptcyMessage] = field(default_factory=list)
    timeline: list[TimelineEvent] = field(default_factory=list)
    nominal_director: Verdict | None = None
    shell_company: Verdict | None = None
    dadata_error: str | None = None
    checko_error: str | None = None
    finances_error: str | None = None

    @property
    def found(self) -> bool:
        return self.dadata is not None or self.checko is not None or self.entrepreneur is not None or self.person is not None


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

    async def _reliability():
        try:
            return await get_reliability(inn, checko_key), None
        except CheckoError as e:
            return None, str(e)

    async def _npd():
        return await check_npd_status(inn)

    async def _contracts():
        try:
            return await get_contracts(inn, checko_key, law="44"), None
        except CheckoError as e:
            return [], str(e)

    is_person_inn = len(inn) == 12

    async def _entrepreneur():
        if not is_person_inn:
            return None, None
        try:
            return await get_entrepreneur(inn, checko_key), None
        except CheckoError as e:
            return None, str(e)

    async def _person():
        if not is_person_inn:
            return None, None
        try:
            return await get_person(inn, checko_key), None
        except CheckoError as e:
            return None, str(e)

    async def _fedresurs():
        try:
            return await get_fedresurs(inn, checko_key), None
        except CheckoError as e:
            return [], str(e)

    async def _bankruptcy():
        try:
            return await get_bankruptcy_messages(inn, checko_key), None
        except CheckoError as e:
            return [], str(e)

    async def _timeline():
        try:
            return await get_timeline(inn, checko_key), None
        except CheckoError as e:
            return [], str(e)

    (
        (dadata_res, dadata_err),
        (checko_res, checko_err),
        (fin_res, fin_err),
        (enf_res, _enf_err),
        (legal_res, _legal_err),
        (insp_res, _insp_err),
        (rel_res, _rel_err),
        npd_res,
        (contracts_res, _contracts_err),
        (entrepreneur_res, _entr_err),
        (person_res, _person_err),
        (fedresurs_res, _fedr_err),
        (bankruptcy_res, _bank_err),
        (timeline_res, _tl_err),
    ) = await asyncio.gather(
        _dadata(), _checko(), _finances(), _enforcements(), _legal_cases(), _inspections(), _reliability(), _npd(),
        _contracts(), _entrepreneur(), _person(), _fedresurs(), _bankruptcy(), _timeline(),
    )

    # ИНН руководителя известен только после того, как пришли основные данные —
    # догоняем отдельным запросом официальные флаги риска именно для человека
    director_person = None
    director_inn = None
    if checko_res and checko_res.directors:
        current = next((d for d in checko_res.directors if not d.end_date), checko_res.directors[-1])
        director_inn = current.inn
    if not director_inn and dadata_res and dadata_res.management:
        director_inn = dadata_res.management.inn
    if director_inn and len(director_inn) == 12:
        try:
            director_person = await get_person(director_inn, checko_key)
        except CheckoError:
            director_person = None

    nominal = analyze_nominal_director(checko_res, dadata_res, director_person)
    shell = analyze_shell_company(checko_res, dadata_res, fin_res, enf_res, legal_res, insp_res)

    return CardResult(
        inn=inn,
        dadata=dadata_res,
        checko=checko_res,
        finances=fin_res,
        enforcements=enf_res,
        legal_cases=legal_res,
        inspections=insp_res,
        reliability=rel_res,
        contracts=contracts_res,
        npd_status=npd_res,
        entrepreneur=entrepreneur_res,
        person=person_res,
        director_person=director_person,
        fedresurs=fedresurs_res,
        bankruptcy_messages=bankruptcy_res,
        timeline=timeline_res,
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


def _fmt_short(value: float | None) -> str:
    """Компактный формат суммы для таблицы: 158.1млн / 44т. / — """
    if value is None:
        return "—"
    sign = "-" if value < 0 else ""
    v = abs(value)
    if v >= 1_000_000:
        return f"{sign}{v / 1_000_000:.1f}млн"
    if v >= 1_000:
        return f"{sign}{v / 1_000:.0f}т."
    return f"{sign}{v:.0f}"


def format_card(result: CardResult) -> str:
    d = result.dadata
    c = result.checko

    if not result.found:
        if result.npd_status is not None:
            status_line = "✅ да" if result.npd_status else "нет"
            return (
                f"Организация/ИП по ИНН {result.inn} не найдены в ЕГРЮЛ/ЕГРИП — "
                f"похоже, это просто физлицо.\n\n"
                f"<b>Самозанятый (НПД):</b> {status_line}"
            )
        return (
            f"Не нашёл организацию по ИНН {result.inn}. "
            f"Проверьте номер или попробуйте позже."
        )

    name = (d.name_short if d else None) or (c.name_short if c else None) or (result.entrepreneur.fio if result.entrepreneur else None) or (result.person.fio if result.person else None) or result.inn
    lines = [f"<b>{name}</b>", f"ИНН {result.inn}"]
    if d and d.ogrn:
        lines.append(f"ОГРН {d.ogrn}")
    if result.entrepreneur and result.entrepreneur.ogrnip:
        lines.append(f"ОГРНИП {result.entrepreneur.ogrnip}")

    if result.entrepreneur:
        e = result.entrepreneur
        lines.append("")
        lines.append("<b>Данные ИП (ЕГРИП)</b>")
        lines.append(f"<b>Статус:</b> {e.status or '—'}")
        if e.reg_date:
            lines.append(f"<b>Регистрация:</b> {e.reg_date}")
        if e.region:
            lines.append(f"<b>Регион:</b> {e.region}")
        if e.okved_name:
            extra = f" (+{e.okved_extra_count})" if e.okved_extra_count else ""
            lines.append(f"<b>Вид деятельности:</b> {e.okved_name}{extra}")
        if e.tax_system:
            lines.append(f"<b>Налоговый режим:</b> {e.tax_system}")
        if e.is_smb:
            lines.append(f"<b>МСП:</b> да" + (f", {e.smb_category}" if e.smb_category else ""))
        if e.phones:
            lines.append(f"<b>Телефон:</b> {e.phones[0]}")
        if e.emails:
            lines.append(f"<b>E-mail:</b> {e.emails[0]}")
        if e.licenses:
            lines.append(f"<b>Лицензии:</b> {len(e.licenses)}")
            for lic in e.licenses[:2]:
                lines.append(f"  • №{lic['number']} от {lic['date']} — {lic['org']}")
        if e.trademarks:
            lines.append(f"<b>Товарные знаки:</b> {len(e.trademarks)}")
        marks = []
        if e.mass_director:
            marks.append("🔴 массовый руководитель")
        if e.mass_founder:
            marks.append("🔴 массовый учредитель")
        if e.unfair_supplier:
            marks.append("🔴 недобросовестный поставщик")
        if marks:
            lines.append(" ".join(marks))
        if e.managed_orgs:
            lines.append(f"<b>Руководитель в организациях:</b> {len(e.managed_orgs)}")
            for o in e.managed_orgs[:3]:
                lines.append(f"  • {o['name']} (ИНН {inn_link(o['inn'])}), {o['status']}")
            if len(e.managed_orgs) > 3:
                lines.append(f"  …и ещё {len(e.managed_orgs) - 3} — кнопка «Показать все» ниже")
        if e.founded_orgs:
            lines.append(f"<b>Учредитель в организациях:</b> {len(e.founded_orgs)}")
            for o in e.founded_orgs[:3]:
                lines.append(f"  • {o['name']} (ИНН {inn_link(o['inn'])}), {o['status']}")
            if len(e.founded_orgs) > 3:
                lines.append(f"  …и ещё {len(e.founded_orgs) - 3} — кнопка «Показать все» ниже")

    if result.person and not result.entrepreneur:
        p = result.person
        lines.append("")
        lines.append("<b>Данные физлица</b>")
        if p.sanctions:
            lines.append(f"🔴 <b>Под санкциями:</b> {', '.join(p.sanctions_countries) or 'да'}")
        if p.mass_director:
            lines.append("🔴 Массовый руководитель")
        if p.mass_founder:
            lines.append("🔴 Массовый учредитель")
        if p.unfair_supplier:
            lines.append("🔴 В реестре недобросовестных поставщиков")
        if p.managed_orgs:
            lines.append(f"<b>Руководитель в организациях:</b> {len(p.managed_orgs)}")
            for o in p.managed_orgs[:5]:
                lines.append(f"  • {o['name']} (ИНН {inn_link(o['inn'])}), {o['status']}")
            if len(p.managed_orgs) > 5:
                lines.append(f"  …и ещё {len(p.managed_orgs) - 5} — кнопка «Показать все» ниже")
        if p.founded_orgs:
            lines.append(f"<b>Учредитель в организациях:</b> {len(p.founded_orgs)}")
            for o in p.founded_orgs[:5]:
                lines.append(f"  • {o['name']} (ИНН {inn_link(o['inn'])}), {o['status']}")
            if len(p.founded_orgs) > 5:
                lines.append(f"  …и ещё {len(p.founded_orgs) - 5} — кнопка «Показать все» ниже")
        if p.ip_records:
            lines.append(f"<b>Статус ИП:</b> {len(p.ip_records)} запись(ей)")

    lines.append("")
    lines.append("<b>Основное</b>")
    if d:
        lines.append(f"<b>Статус:</b> {STATUS_LABELS.get(d.status, d.status or '—')}")
        addr_flags = []
        if d.address_is_mass:
            addr_flags.append("⚠️ признаки массового адреса")
        if c and c.address_unreliable:
            addr_flags.append("🔴 недостоверность в ЕГРЮЛ")
        addr_suffix = (" " + " ".join(addr_flags)) if addr_flags else ""
        lines.append(f"<b>Адрес:</b> {d.address or (c.address if c else '—')}{addr_suffix}")
        if c and c.mass_address_count:
            lines.append(f"<b>На этом адресе ещё организаций:</b> {c.mass_address_count}")
        okved_name = d.okved_name or (c.okved_name if c else "")
        okved_extra = f" (+{c.okved_extra_count})" if c and c.okved_extra_count else ""
        if okved_name:
            lines.append(f"<b>Вид деятельности:</b> {okved_name}{okved_extra}")
        if d.okved:
            lines.append(f"<b>ОКВЭД:</b> {d.okved}")
        if d.registration_date:
            lines.append(f"<b>Регистрация:</b> {d.registration_date}")
        if c and c.region:
            lines.append(f"<b>Регион:</b> {c.region}")
        if c and c.tax_office_name:
            lines.append(f"<b>Налоговый орган:</b> {c.tax_office_name} (с {c.tax_office_reg_date})")
        if c and c.reg_authority_name:
            lines.append(f"<b>Регистрирующий орган:</b> {c.reg_authority_name}")
        if result.npd_status is not None:
            lines.append(f"<b>Самозанятый (НПД):</b> {'✅ да' if result.npd_status else 'нет'}")
    else:
        if result.entrepreneur or result.person:
            lines.append("(основные данные уже показаны выше, из ЕГРИП/Checko)")
        else:
            lines.append(f"⚠️ DaData недоступна: {result.dadata_error or 'нет данных'}")

    # Текущий руководитель — берём имя из DaData (обычно первым приходит), но
    # ИНН/должность/дату — из Checko, у DaData поля ИНН в этом блоке нет вообще
    current_director = None
    director_history = []
    if c and c.directors:
        current_list = [x for x in c.directors if not x.end_date]
        if current_list:
            current_director = current_list[-1]
            director_history = [x for x in c.directors if x is not current_director]
        else:
            current_director = c.directors[-1]
            director_history = c.directors[:-1]

    if d and d.management:
        lines.append("")
        lines.append("<b>Руководитель</b>")
        m = d.management
        inn = m.inn or (current_director.inn if current_director and current_director.name == m.name else "")
        post = current_director.post if current_director and current_director.name == m.name else m.post
        lines.append(f"{m.name}" + (f" (ИНН {inn_link(inn)})" if inn else "") + (f", {post}" if post else ""))
        if c and c.managing_org and c.managing_org.inn == m.inn:
            tag = " 🔴 недостоверность" if c.managing_org.unreliable else ""
            lines.append(f"(управляющая организация{tag})")
    elif c and c.managing_org:
        lines.append("")
        lines.append("<b>Руководитель</b>")
        mo = c.managing_org
        tag = " 🔴 недостоверность" if mo.unreliable else ""
        lines.append(f"{mo.name} (ИНН {inn_link(mo.inn)}) — управляющая организация{tag}")
    elif current_director:
        lines.append("")
        lines.append("<b>Руководитель</b>")
        marks = ""
        if current_director.disqualified:
            marks += " 🔴 дисквалифицирован"
        if current_director.is_mass:
            marks += " 🔴 массовый руководитель"
        if current_director.unreliable:
            marks += " 🔴 недостоверность"
        lines.append(
            f"{current_director.name}"
            + (f" (ИНН {inn_link(current_director.inn)})" if current_director.inn else "")
            + (f", {current_director.post}" if current_director.post else "")
            + marks
        )

    if director_history:
        lines.append("")
        lines.append("<b>История руководителей</b>")
        for dir_ in director_history:
            period = ""
            if dir_.record_date:
                period = f" — с {dir_.record_date}" + (f" по {dir_.end_date}" if dir_.end_date else "")
            marks = ""
            if dir_.disqualified:
                marks += " 🔴 дисквалифицирован"
            if dir_.is_mass:
                marks += " 🔴 массовый руководитель"
            if dir_.unreliable:
                marks += " 🔴 недостоверность"
            lines.append(f"• {dir_.name}" + (f" (ИНН {inn_link(dir_.inn)})" if dir_.inn else "") + (f", {dir_.post}" if dir_.post else "") + period + marks)

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
            lines.append(f"• {f.name}" + (f" (ИНН {inn_link(f.inn)})" if f.inn else "") + share + kind + marks)

    if c:
        lines.append("")
        lines.append("<b>Финансы и налоги</b>")
        finances = result.finances or c.finances
        if finances:
            rows = finances[-4:]
            col_year = 6
            col_val = 11

            def cell(v):
                return _fmt_short(v).rjust(col_val)

            header = "Год".ljust(col_year) + "Выручка".rjust(col_val) + "Прибыль".rjust(col_val) + "Актив".rjust(col_val)
            table_lines = [header]
            for fy in rows:
                row = str(fy.year).ljust(col_year) + cell(fy.revenue) + cell(fy.profit) + cell(fy.assets)
                table_lines.append(row)
            lines.append("<pre>" + "\n".join(table_lines) + "</pre>")
        elif result.finances_error:
            lines.append(f"⚠️ Финансы недоступны: {result.finances_error}")

        lines.append("――――――――――――――――")
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
            status_mark = _status_mark(r.get("status"))
            kind_str = f" — {r['kind']}" if r.get("kind") else ""
            lines.append(f"{status_mark} {r.get('name', '')}" + (f" (ИНН {inn_link(r['inn'])})" if r.get("inn") else "") + kind_str)
        if len(c.related_orgs) > 5:
            lines.append(f"…и ещё {len(c.related_orgs) - 5} — кнопка «Показать все» ниже")

    if result.enforcements:
        total_debt = sum(e.debt_remaining or e.debt_sum or 0 for e in result.enforcements)
        lines.append("")
        lines.append(f"<b>Исполнительные производства (ФССП)</b> — {len(result.enforcements)}")
        lines.append(f"🔴 Общая непогашенная задолженность: {total_debt:,.0f} ₽".replace(",", " "))
        for e in result.enforcements[:3]:
            sum_str = f"{(e.debt_remaining or e.debt_sum or 0):,.0f} ₽".replace(",", " ")
            lines.append(f"• №{e.number} от {e.date} — {e.subject}, {sum_str}")
        if len(result.enforcements) > 3:
            lines.append(f"…и ещё {len(result.enforcements) - 3} — кнопка «Показать все» ниже")

    if result.legal_cases and result.legal_cases.cases:
        lc = result.legal_cases
        as_defendant = sum(1 for c in lc.cases if c.role == "ответчик")
        as_plaintiff = len(lc.cases) - as_defendant
        lines.append("")
        lines.append(f"<b>Арбитражные дела</b> — {len(lc.cases)} (истец: {as_plaintiff}, ответчик: {as_defendant})")
        lines.append(f"Общая сумма исков: {lc.total_claim_sum:,.0f} ₽".replace(",", " "))
        for c_ in sorted(lc.cases, key=lambda x: x.date, reverse=True)[:3]:
            sum_str = f", {c_.claim_sum:,.0f} ₽".replace(",", " ") if c_.claim_sum else ""
            link = f"https://kad.arbitr.ru/Card/{c_.uuid}" if c_.uuid else c_.kad_url
            lines.append(f"• №{c_.number} от {c_.date} — {c_.role}{sum_str}" + (f"\n  {link}" if link else ""))
        if len(lc.cases) > 3:
            lines.append(f"…и ещё {len(lc.cases) - 3} — кнопка «Показать все» ниже")

    if result.inspections:
        with_violations = [i for i in result.inspections if i.violations_found]
        lines.append("")
        lines.append(f"<b>Проверки</b> — {len(result.inspections)}" + (f", из них с нарушениями: {len(with_violations)}" if with_violations else ""))
        lines.append(f"Подробности по ИНН: https://proverki.gov.ru/portal (поиск по ИНН {result.inn})")

    if result.contracts:
        as_customer = [ct for ct in result.contracts if ct.customer_inn == result.inn]
        as_supplier = [ct for ct in result.contracts if result.inn in ct.supplier_inns]
        total_price = sum(ct.price or 0 for ct in result.contracts)
        lines.append("")
        lines.append(f"<b>Госконтракты (44-ФЗ)</b> — {len(result.contracts)} (заказчик: {len(as_customer)}, поставщик: {len(as_supplier)})")
        lines.append(f"Общая сумма: {total_price:,.0f} ₽".replace(",", " "))
        for ct in sorted(result.contracts, key=lambda x: x.date, reverse=True)[:3]:
            role = "заказчик" if ct.customer_inn == result.inn else "поставщик"
            obj = ct.objects[0] if ct.objects else ""
            price_str = f", {ct.price:,.0f} ₽".replace(",", " ") if ct.price else ""
            lines.append(f"• №{ct.reg_number} от {ct.date} — {role}{price_str}" + (f"\n  {obj}" if obj else "") + (f"\n  {ct.eis_url}" if ct.eis_url else ""))
        if len(result.contracts) > 3:
            lines.append(f"…и ещё {len(result.contracts) - 3} — кнопка «Показать все» ниже")

    if result.bankruptcy_messages:
        lines.append("")
        lines.append(f"<b>Записи ЕФРСБ (банкротство)</b> — {len(result.bankruptcy_messages)}")
        for b in sorted(result.bankruptcy_messages, key=lambda x: x.date, reverse=True)[:3]:
            case_str = f", дело №{b.case_number}" if b.case_number else ""
            lines.append(f"• {b.date} — {b.type_name}{case_str}" + (f"\n  {b.url}" if b.url else ""))
        if len(result.bankruptcy_messages) > 3:
            lines.append(f"…и ещё {len(result.bankruptcy_messages) - 3} — кнопка «Показать все» ниже")

    if result.fedresurs:
        lines.append("")
        lines.append(f"<b>Сообщения Федресурса</b> — {len(result.fedresurs)}")
        for f in sorted(result.fedresurs, key=lambda x: x.date, reverse=True)[:3]:
            lines.append(f"• {f.date} — {f.type_name}" + (f"\n  {f.url}" if f.url else ""))
        if len(result.fedresurs) > 3:
            lines.append(f"…и ещё {len(result.fedresurs) - 3} — кнопка «Показать все» ниже")

    if result.timeline:
        lines.append("")
        lines.append(f"<b>История изменений</b> (последние {min(5, len(result.timeline))} из {len(result.timeline)})")
        for ev in result.timeline[-5:]:
            lines.append(f"• {ev.date} — {ev.text}")

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

    rel = result.reliability
    if rel:
        lines.append("")
        lines.append("<b>📋 Оценка надёжности (Checko, по подходам ФНС)</b>")
        if rel.scale is not None:
            lines.append(f"Масштаб бизнеса: {rel.scale}/100")
        if rel.nominal_level is not None:
            nom_emoji = {1: "🟢", 2: "🟡", 3: "🔴"}.get(rel.nominal_level, "")
            lines.append(f"{nom_emoji} Риск номинальности: {rel.nominal_label} (уровень {rel.nominal_level}/3)")
        if rel.fin_risk_level is not None:
            fin_emoji = "🟢" if rel.fin_risk_level <= 1 else "🟡" if rel.fin_risk_level <= 3 else "🔴"
            lines.append(f"{fin_emoji} Финансовые риски: {rel.fin_risk_label} (уровень {rel.fin_risk_level}/5)")
        if rel.positive_pct is not None:
            lines.append(f"Баланс факторов: 🟢 {rel.positive_pct}% · 🟡 {rel.attention_pct}% · 🔴 {rel.negative_pct}%")
        if rel.factors:
            lines.append("")
            for f in rel.factors:
                mark = {"Полож": "🟢", "ТрВним": "🟡", "Негатив": "🔴"}.get(f.kind, "•")
                lines.append(f"{mark} {f.text}")

    if result.nominal_director or result.shell_company:
        lines.append("")
        lines.append("<b>📊 Дополнительная эвристика бота</b>")
        lines.append("<i>(своя проверка сверх официальной оценки Checko выше)</i>")

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


def split_for_telegram(text: str, max_len: int = 900) -> list[str]:
    """Разбивает длинную карточку на несколько сообщений по смысловым блокам
    (границы — пустая строка между секциями), а не как попало посередине текста —
    так на телефоне вместо одной бесконечной простыни приходит несколько
    компактных сообщений, каждое читается с одного взгляда."""
    sections = text.split("\n\n")
    chunks: list[str] = []
    current = ""
    for section in sections:
        candidate = (current + "\n\n" + section) if current else section
        if len(candidate) > max_len and current:
            chunks.append(current)
            current = section
        else:
            current = candidate
    if current:
        chunks.append(current)
    # страховка от лимита Telegram (4096): если одна секция (список без пустых строк)
    # оказалась гигантской — режем её по строкам
    safe: list[str] = []
    for ch in chunks or [text]:
        safe.extend(chunk_lines(ch, 3800) if len(ch) > 3800 else [ch])
    return safe


# ───────────────────────── общие мелочи ─────────────────────────

def _status_mark(status: str | None) -> str:
    """🔴 для недействующих/ликвидированных организаций, 🟢 для остальных."""
    s = (status or "").lower()
    bad = any(w in s for w in ("не действ", "ликвид", "прекращ", "исключ"))
    return "🔴" if bad else "🟢"


def _org_active(status: str | None) -> bool:
    return _status_mark(status) == "🟢"


def _org_row(r: dict) -> str:
    inn = r.get("inn") or ""
    kind = f" — {r['kind']}" if r.get("kind") else ""
    inn_part = f" (ИНН {inn_link(inn)})" if inn else ""
    status = f", {r['status']}" if r.get("status") else ""
    return f"{_status_mark(r.get('status'))} {r.get('name') or '—'}{inn_part}{status}{kind}"


# ─────────────── кнопки «Показать все» под карточкой ───────────────
# В карточке длинные списки обрезаны («…и ещё N»). Ключ секции + ИНН уходят в
# callback-кнопку, по нажатию бот заново запрашивает именно эту секцию и
# присылает её ПОЛНОСТЬЮ.

def expandable_sections(result: "CardResult") -> list[tuple[str, str]]:
    """[(ключ секции, подпись кнопки)] для списков, показанных в карточке не полностью."""
    out: list[tuple[str, str]] = []
    c = result.checko
    if c and len(c.related_orgs) > 5:
        out.append(("rel", f"➕ Все связанные организации ({len(c.related_orgs)})"))

    e, p = result.entrepreneur, result.person
    if e and (len(e.managed_orgs) > 3 or len(e.founded_orgs) > 3):
        out.append(("org", f"➕ Все организации ИП ({len(e.managed_orgs) + len(e.founded_orgs)})"))
    elif p and not e and (len(p.managed_orgs) > 5 or len(p.founded_orgs) > 5):
        out.append(("org", f"➕ Все организации ({len(p.managed_orgs) + len(p.founded_orgs)})"))

    if len(result.enforcements) > 3:
        out.append(("enf", f"➕ Все ФССП ({len(result.enforcements)})"))
    if result.legal_cases and len(result.legal_cases.cases) > 3:
        out.append(("cas", f"➕ Все арбитражные дела ({len(result.legal_cases.cases)})"))
    if len(result.contracts) > 3:
        out.append(("ctr", f"➕ Все госконтракты ({len(result.contracts)})"))
    if len(result.bankruptcy_messages) > 3:
        out.append(("bnk", f"➕ Все записи ЕФРСБ ({len(result.bankruptcy_messages)})"))
    if len(result.fedresurs) > 3:
        out.append(("fed", f"➕ Все сообщения Федресурса ({len(result.fedresurs)})"))
    if len(result.timeline) > 5:
        out.append(("tln", f"➕ Вся история изменений ({len(result.timeline)})"))
    return out


async def fetch_and_format_section(key: str, inn: str, checko_key: str) -> str:
    """Полный (не обрезанный) вид одной секции карточки. Запрашивает только нужный метод Checko."""
    if key == "rel":
        c = await get_company(inn, checko_key)
        rows = c.related_orgs if c else []
        head = f"<b>Связанные организации</b> — {len(rows)}"
        body = [_org_row(r) for r in rows]

    elif key == "org":
        e = await get_entrepreneur(inn, checko_key) if len(inn) == 12 else None
        p = await get_person(inn, checko_key) if len(inn) == 12 else None
        managed = list((e.managed_orgs if e else []) or (p.managed_orgs if p else []))
        founded = list((e.founded_orgs if e else []) or (p.founded_orgs if p else []))
        head = f"<b>Организации</b> — руководитель: {len(managed)}, учредитель: {len(founded)}"
        body = []
        if managed:
            body.append("<b>Руководитель в:</b>")
            body += [_org_row(r) for r in managed]
        if founded:
            body.append("")
            body.append("<b>Учредитель в:</b>")
            body += [_org_row(r) for r in founded]

    elif key == "enf":
        items = await get_enforcements(inn, checko_key)
        total = sum(x.debt_remaining or x.debt_sum or 0 for x in items)
        head = f"<b>Исполнительные производства (ФССП)</b> — {len(items)}, непогашено {total:,.0f} ₽".replace(",", " ")
        body = []
        for x in items:
            s = f"{(x.debt_remaining or x.debt_sum or 0):,.0f} ₽".replace(",", " ")
            body.append(f"• №{x.number} от {x.date} — {x.subject}, {s}")

    elif key == "cas":
        lc = await get_legal_cases(inn, checko_key)
        head = f"<b>Арбитражные дела</b> — {len(lc.cases)}, сумма исков {lc.total_claim_sum:,.0f} ₽".replace(",", " ")
        body = []
        for x in sorted(lc.cases, key=lambda z: z.date, reverse=True):
            s = f", {x.claim_sum:,.0f} ₽".replace(",", " ") if x.claim_sum else ""
            link = f"https://kad.arbitr.ru/Card/{x.uuid}" if x.uuid else x.kad_url
            body.append(f"• №{x.number} от {x.date} — {x.role}{s}" + (f"\n  {link}" if link else ""))

    elif key == "ctr":
        items = await get_contracts(inn, checko_key, law="44", limit=100)
        head = f"<b>Госконтракты (44-ФЗ)</b> — {len(items)}"
        body = []
        for x in sorted(items, key=lambda z: z.date, reverse=True):
            role = "заказчик" if x.customer_inn == inn else "поставщик"
            s = f", {x.price:,.0f} ₽".replace(",", " ") if x.price else ""
            obj = f"\n  {x.objects[0]}" if x.objects else ""
            link = f"\n  {x.eis_url}" if x.eis_url else ""
            body.append(f"• №{x.reg_number} от {x.date} — {role}{s}{obj}{link}")

    elif key == "bnk":
        items = await get_bankruptcy_messages(inn, checko_key, limit=100)
        head = f"<b>Записи ЕФРСБ (банкротство)</b> — {len(items)}"
        body = []
        for x in sorted(items, key=lambda z: z.date, reverse=True):
            case = f", дело №{x.case_number}" if x.case_number else ""
            body.append(f"• {x.date} — {x.type_name}{case}" + (f"\n  {x.url}" if x.url else ""))

    elif key == "fed":
        items = await get_fedresurs(inn, checko_key, limit=100)
        head = f"<b>Сообщения Федресурса</b> — {len(items)}"
        body = [f"• {x.date} — {x.type_name}" + (f"\n  {x.url}" if x.url else "")
                for x in sorted(items, key=lambda z: z.date, reverse=True)]

    elif key == "tln":
        items = await get_timeline(inn, checko_key)
        head = f"<b>История изменений</b> — {len(items)}"
        body = [f"• {x.date} — {x.text}" for x in items]

    else:
        return "Неизвестный раздел."

    if not body:
        return head + "\nДанных нет."
    return head + "\n" + "\n".join(body)


def chunk_lines(text: str, max_len: int = 3500) -> list[str]:
    """Режет длинный текст по строкам (не посреди строки/ссылки) на части до max_len —
    для полных списков, где 'пустых строк между секциями' нет вообще."""
    chunks, cur = [], ""
    for line in text.split("\n"):
        if cur and len(cur) + 1 + len(line) > max_len:
            chunks.append(cur)
            cur = line
        else:
            cur = f"{cur}\n{line}" if cur else line
    if cur:
        chunks.append(cur)
    return chunks or [text]


# ───────────── данные для схемы связей (с подписью типа связи) ─────────────

def relation_scheme_data(result: "CardResult") -> tuple[str, list[dict]]:
    """(название центра, строки схемы). Каждая строка: name, inn, active, label —
    label говорит, ЧЕМ организация связана с проверяемой (общий учредитель / руководитель и т.д.)."""
    center = (
        (result.dadata.name_short if result.dadata else None)
        or (result.checko.name_short if result.checko else None)
        or (result.entrepreneur.fio if result.entrepreneur else None)
        or (result.person.fio if result.person else None)
        or result.inn
    )
    raw: list[dict] = []
    if result.checko:
        raw += result.checko.related_orgs
    if result.entrepreneur:
        raw += result.entrepreneur.managed_orgs + result.entrepreneur.founded_orgs
    elif result.person:
        raw += result.person.managed_orgs + result.person.founded_orgs

    merged: dict[str, dict] = {}
    for r in raw:
        key = r.get("inn") or r.get("name") or str(len(merged))
        label = r.get("kind") or "связь"
        if key in merged:
            if label not in merged[key]["labels"]:
                merged[key]["labels"].append(label)
        else:
            merged[key] = {
                "name": r.get("name") or "—",
                "inn": r.get("inn") or "—",
                "active": _org_active(r.get("status")),
                "labels": [label],
            }
    rows = [
        {"name": m["name"], "inn": m["inn"], "active": m["active"], "label": "; ".join(m["labels"])}
        for m in merged.values()
    ]
    return center, rows


# ─────────── облегчённая сборка для «Найти связи» ───────────

async def build_light_card(inn: str, dadata_key: str, dadata_secret: str, checko_key: str) -> CardResult:
    """Только то, что нужно для сопоставления организаций между собой (руководители,
    учредители, адрес, контакты, госконтракты). Без финансов, ФССП, арбитража, проверок
    и оценки надёжности — в разы быстрее и дешевле по лимитам API, чем полная карточка."""
    is_person_inn = len(inn) == 12

    async def _dadata():
        try:
            return await get_party(inn, dadata_key, dadata_secret)
        except DaDataError:
            return None

    async def _checko():
        try:
            return await get_company(inn, checko_key)
        except CheckoError:
            return None

    async def _entrepreneur():
        if not is_person_inn:
            return None
        try:
            return await get_entrepreneur(inn, checko_key)
        except CheckoError:
            return None

    async def _person():
        if not is_person_inn:
            return None
        try:
            return await get_person(inn, checko_key)
        except CheckoError:
            return None

    async def _contracts():
        try:
            return await get_contracts(inn, checko_key, law="44", limit=100)
        except CheckoError:
            return []

    d, c, e, p, ct = await asyncio.gather(_dadata(), _checko(), _entrepreneur(), _person(), _contracts())
    return CardResult(inn=inn, dadata=d, checko=c, entrepreneur=e, person=p, contracts=ct)
