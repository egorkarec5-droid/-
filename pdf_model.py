"""
Структурная модель PDF-досье. Строится напрямую из CardResult (а не парсингом текста
карточки), JSON-сериализуема — поэтому её можно положить в кэш и рисовать PDF без
повторных запросов к API.

Ячейки таблиц могут быть:
  str                                — обычный текст
  {"t": str, "level": "low|medium|high|none"} — цветная метка риска
  {"inn": str}                       — ИНН (в PDF — ссылка на проверку в боте)
  {"link": url, "t": str}            — внешняя ссылка
  list[ячейка]                       — несколько фрагментов подряд
"""

from __future__ import annotations
import re
from datetime import date, datetime

LEVEL_ORDER = {"none": -1, "low": 0, "medium": 1, "high": 2}


# ───────────────────────── форматирование ─────────────────────────

NB = "\u00a0"


def money(v: float | None) -> str:
    """Короткий формат: 1,85 млрд ₽ / 413,3 млн ₽ / 44 тыс ₽."""
    if v is None:
        return "—"
    a, sign = abs(v), ("−" if v < 0 else "")
    if a >= 1e9:
        s = f"{a / 1e9:.2f}".rstrip("0").rstrip(".") + f"{NB}млрд{NB}₽"
    elif a >= 1e6:
        s = f"{a / 1e6:.1f}".rstrip("0").rstrip(".") + f"{NB}млн{NB}₽"
    elif a >= 1e3:
        s = f"{a / 1e3:.0f}{NB}тыс{NB}₽"
    else:
        s = f"{a:.0f}{NB}₽"
    return sign + s.replace(".", ",")


def money_full(v: float | None) -> str:
    if v is None:
        return "—"
    return f"{v:,.0f}".replace(",", NB) + f"{NB}₽"


def date_ru(s: str | None) -> str:
    """2022-08-12 -> 12.08.2022; остальное возвращаем как есть."""
    if not s:
        return ""
    m = re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})", s.strip())
    return f"{m.group(3)}.{m.group(2)}.{m.group(1)}" if m else s


def _parse_date(s: str | None) -> date | None:
    if not s:
        return None
    s = s.strip()
    for fmt in ("%d.%m.%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


def age_text(reg: str | None) -> str:
    d = _parse_date(reg)
    if not d:
        return ""
    days = (date.today() - d).days
    if days < 0:
        return ""
    years, months = days // 365, (days % 365) // 30
    if years >= 1:
        word = "год" if years % 10 == 1 and years % 100 != 11 else "года" if years % 10 in (2, 3, 4) and years % 100 not in (12, 13, 14) else "лет"
        return f"{years} {word}"
    return f"{months} мес." if months else "менее месяца"


def worst(*levels: str) -> str:
    best = "low"
    for lv in levels:
        if LEVEL_ORDER.get(lv, 0) > LEVEL_ORDER[best]:
            best = lv
    return best


def status_level(text: str | None) -> str:
    s = (text or "").lower()
    if any(w in s for w in ("не действ", "ликвид", "прекращ", "исключ", "банкрот")):
        return "high" if "процесс" not in s else "medium"
    if any(w in s for w in ("реорган", "приостан")):
        return "medium"
    return "low" if s else "none"


DADATA_STATUS = {
    "ACTIVE": ("Действует", "low"),
    "LIQUIDATING": ("В процессе ликвидации", "medium"),
    "LIQUIDATED": ("Ликвидирована", "high"),
    "BANKRUPT": ("Банкротство", "high"),
    "REORGANIZING": ("Реорганизация", "medium"),
}


# ───────────────────────── модель ─────────────────────────

def _chip(t: str, level: str) -> dict:
    return {"t": t, "level": level}


def _latest_fin(result):
    rows = [f for f in (result.finances or []) if f.revenue is not None or f.profit is not None or f.assets is not None]
    return sorted(rows, key=lambda f: f.year)


def _main_status(result) -> tuple[str, str]:
    d, c, e = result.dadata, result.checko, result.entrepreneur
    if d and d.status in DADATA_STATUS:
        return DADATA_STATUS[d.status]
    if e and e.status:
        return e.status, status_level(e.status)
    if c and c.status:
        return c.status, status_level(c.status)
    return "нет данных", "none"


def build_alerts(result) -> list[tuple[str, str]]:
    """[(level, text)] — что ещё нужно увидеть с первого взгляда, помимо оценок."""
    out: list[tuple[str, str]] = []
    d, c, e, p = result.dadata, result.checko, result.entrepreneur, result.person
    st_text, st_level = _main_status(result)
    if st_level in ("medium", "high"):
        out.append((st_level, f"Статус: {st_text}"))

    if c:
        f = c.flags
        if f.sanctions:
            out.append(("high", "Организация под санкциями"))
        if f.sanctions_founder:
            out.append(("high", "Учредитель под санкциями"))
        if f.illegal_finance:
            out.append(("high", "Признаки нелегальных финансовых операций"))
        if f.disqualified_person:
            out.append(("high", "В руководстве — дисквалифицированное лицо"))
        if f.unfair_supplier:
            out.append(("high", "В реестре недобросовестных поставщиков"))
        if c.address_unreliable:
            out.append(("high", "Недостоверность адреса в ЕГРЮЛ"))
        if f.mass_director:
            out.append(("medium", "Массовый руководитель"))
        if f.mass_founder:
            out.append(("medium", "Массовый учредитель"))
    if (d and d.address_is_mass) or (c and (c.mass_address_count or 0) >= 10):
        n = f" (ещё {c.mass_address_count} организаций)" if c and c.mass_address_count else ""
        out.append(("medium", f"Массовый адрес регистрации{n}"))

    if e:
        if e.unfair_supplier:
            out.append(("high", "ИП в реестре недобросовестных поставщиков"))
        if e.mass_director:
            out.append(("medium", "ИП — массовый руководитель"))
        if e.mass_founder:
            out.append(("medium", "ИП — массовый учредитель"))
    if p and not e and p.sanctions:
        out.append(("high", "Физлицо под санкциями: " + (", ".join(p.sanctions_countries) or "да")))

    if result.enforcements:
        debt = sum(x.debt_remaining or x.debt_sum or 0 for x in result.enforcements)
        fin_rev = [f.revenue for f in _latest_fin(result) if f.revenue]
        revenue = fin_rev[-1] if fin_rev else None
        # долг в 180 тыс ₽ у компании с выручкой в миллиарды — не повод для тревоги:
        # значимым считаем от 1 млн ₽ или от 0,5% выручки (если выручка неизвестна — любой долг)
        significant = debt >= 1_000_000 or (revenue and debt / revenue >= 0.005) or (not revenue and debt > 0)
        if significant:
            out.append(("medium", f"Исполнительные производства ФССП: {len(result.enforcements)}, долг {money(debt)}"))
    if result.bankruptcy_messages or (c and c.flags.in_bankruptcy_registry):
        n = len(result.bankruptcy_messages)
        out.append(("medium", "Есть записи в реестре банкротств (ЕФРСБ)" + (f": {n}" if n else "")))

    out.sort(key=lambda x: -LEVEL_ORDER[x[0]])
    return out


def build_verdict(result, alerts) -> dict:
    items: list[dict] = []
    levels: list[str] = []

    rel = result.reliability
    if rel:
        if rel.nominal_level:
            lv = {1: "low", 2: "medium", 3: "high"}.get(rel.nominal_level, "none")
            items.append({"label": "Риск номинальности (Checko)", "value": rel.nominal_label or f"уровень {rel.nominal_level}/3", "level": lv})
            levels.append(lv)
        if rel.fin_risk_level:
            lv = "low" if rel.fin_risk_level <= 1 else "medium" if rel.fin_risk_level <= 3 else "high"
            items.append({"label": "Финансовые риски (Checko)", "value": rel.fin_risk_label or f"уровень {rel.fin_risk_level}/5", "level": lv})
            levels.append(lv)
        if rel.positive_pct is not None:
            neg = rel.negative_pct or 0
            lv = "high" if neg >= 40 else "medium" if neg >= 15 else "low"
            items.append({
                "label": "Баланс факторов (Checko)",
                "value": f"+{rel.positive_pct}% · ±{rel.attention_pct or 0}% · −{neg}%",
                "level": lv,
            })
            levels.append(lv)

    nd, sc = result.nominal_director, result.shell_company
    if nd:
        items.append({"label": "Номинальный директор (эвристика бота)", "value": f"{nd.label}, {nd.score}/100", "level": nd.level})
        levels.append(nd.level)
    if sc:
        items.append({"label": "Техническая / обнальная (эвристика бота)", "value": f"{sc.label}, {sc.score}/100", "level": sc.level})
        levels.append(sc.level)

    levels += [lv for lv, _ in alerts]
    overall = worst(*levels) if levels else "none"
    headline = {"low": "НИЗКИЙ РИСК", "medium": "ТРЕБУЕТ ВНИМАНИЯ", "high": "ВЫСОКИЙ РИСК", "none": "НЕДОСТАТОЧНО ДАННЫХ"}[overall]
    return {"level": overall, "headline": headline, "items": items}


def build_kpis(result) -> list[dict]:
    d, c, e = result.dadata, result.checko, result.entrepreneur
    fin = _latest_fin(result)
    cands: list[dict] = []

    with_rev = [f for f in fin if f.revenue is not None]
    if with_rev:
        f = with_rev[-1]
        prev = next((x for x in reversed(with_rev[:-1]) if x.revenue), None)
        sub = ""
        if prev and prev.revenue:
            ch = (f.revenue - prev.revenue) / abs(prev.revenue) * 100
            sub = f"{'+' if ch >= 0 else '−'}{abs(ch):.0f}% к {prev.year}".replace(".", ",")
        cands.append({"label": f"Выручка {f.year}", "value": money(f.revenue), "sub": sub, "level": "none"})
        if f.profit is not None:
            margin = f"маржа {f.profit / f.revenue * 100:.1f}%".replace(".", ",") if f.revenue else ""
            cands.append({"label": f"Чистая прибыль {f.year}", "value": money(f.profit), "sub": margin,
                          "level": "high" if f.profit < 0 else "none"})
    elif fin and fin[-1].profit is not None:
        cands.append({"label": f"Чистая прибыль {fin[-1].year}", "value": money(fin[-1].profit), "sub": "",
                      "level": "high" if fin[-1].profit < 0 else "none"})

    emp = (c.employee_count if c else None) or (d.employee_count if d else None)
    if emp is not None:
        cands.append({"label": "Сотрудников", "value": str(emp), "sub": "среднесписочная", "level": "none"})

    reg = (d.registration_date if d else "") or (c.reg_date if c else "") or (e.reg_date if e else "")
    if reg:
        cands.append({"label": "Зарегистрирована", "value": date_ru(reg), "sub": age_text(reg), "level": "none"})

    if c and c.capital is not None:
        cands.append({"label": "Уставный капитал", "value": money(c.capital), "sub": "", "level": "none"})
    if c and c.taxes_paid is not None:
        cands.append({"label": "Уплачено налогов", "value": money(c.taxes_paid), "sub": "последний период", "level": "none"})
    if result.reliability and result.reliability.scale is not None:
        cands.append({"label": "Масштаб бизнеса", "value": f"{result.reliability.scale}/100", "sub": "по оценке Checko", "level": "none"})
    if e and e.tax_system:
        cands.append({"label": "Налоговый режим", "value": e.tax_system, "sub": e.smb_category.capitalize() if e.is_smb else "", "level": "none"})
    return cands[:4]


# ───────────────────────── разделы ─────────────────────────

def _sec_main(result) -> dict | None:
    d, c, e, p = result.dadata, result.checko, result.entrepreneur, result.person
    rows: list[list] = []
    st_text, st_level = _main_status(result)
    rows.append(["Статус", _chip(st_text, st_level)])

    inn_parts = [f"ИНН {result.inn}"]
    if d and d.kpp:
        inn_parts.append(f"КПП {d.kpp}")
    if d and d.ogrn:
        inn_parts.append(f"ОГРН {d.ogrn}")
    if e and e.ogrnip:
        inn_parts.append(f"ОГРНИП {e.ogrnip}")
    rows.append(["Реквизиты", " · ".join(inn_parts)])

    if e and e.fio:
        rows.append(["ФИО предпринимателя", e.fio])
    elif p and p.fio and not c and not d:
        rows.append(["ФИО", p.fio])

    addr = (d.address if d and d.address else "") or (c.address if c else "")
    if addr:
        parts: list = [addr]
        if (d and d.address_is_mass) or (c and (c.mass_address_count or 0) >= 10):
            parts.append(_chip("массовый адрес", "medium"))
        if c and c.address_unreliable:
            parts.append(_chip("недостоверность", "high"))
        rows.append(["Адрес", parts])
        if c and c.mass_address_count:
            rows.append(["Организаций на этом адресе", f"ещё {c.mass_address_count}"])

    okved_name = (d.okved_name if d and d.okved_name else "") or (c.okved_name if c and c.okved_name else "") or (e.okved_name if e else "")
    okved_code = (d.okved if d and d.okved else "") or (e.okved if e else "")
    extra = (c.okved_extra_count if c else 0) or (e.okved_extra_count if e else 0)
    if okved_name or okved_code:
        rows.append(["Вид деятельности", (okved_name or "") + (f" (+{extra} доп.)" if extra else "")])
    if okved_code:
        rows.append(["ОКВЭД", okved_code])

    reg = (d.registration_date if d else "") or (c.reg_date if c else "") or (e.reg_date if e else "")
    if reg:
        age = age_text(reg)
        rows.append(["Дата регистрации", date_ru(reg) + (f" · {age}" if age else "")])
    region = (c.region if c else "") or (e.region if e else "")
    if region:
        rows.append(["Регион", region])
    if c and c.tax_office_name:
        rows.append(["Налоговый орган", c.tax_office_name + (f" (с {date_ru(c.tax_office_reg_date)})" if c.tax_office_reg_date else "")])
    if c and c.reg_authority_name:
        rows.append(["Регистрирующий орган", c.reg_authority_name])
    if result.npd_status is not None:
        rows.append(["Самозанятый (НПД)", _chip("да", "medium") if result.npd_status else "нет"])

    phones = (c.phones if c else []) or (e.phones if e and e.phones else [])
    emails = (c.emails if c else []) or (e.emails if e and e.emails else [])
    if phones:
        rows.append(["Телефон", ", ".join(phones[:3])])
    if emails:
        rows.append(["E-mail", ", ".join(emails[:3])])
    return {"title": "Реквизиты и регистрация", "type": "kv", "rows": rows} if len(rows) > 1 else None


def _person_notes(unreliable: bool, is_mass: bool, disq: bool) -> list:
    out = []
    if disq:
        out.append(_chip("дисквалифицирован", "high"))
    if is_mass:
        out.append(_chip("массовый", "medium"))
    if unreliable:
        out.append(_chip("недостоверность", "high"))
    return out or ["—"]


def _sec_people(result) -> dict | None:
    d, c = result.dadata, result.checko
    rows: list[list] = []

    if c and c.managing_org:
        mo = c.managing_org
        rows.append(["Упр. организация", mo.name, {"inn": mo.inn}, "—", _person_notes(mo.unreliable, False, False)])

    if c and c.directors:
        current_list = [x for x in c.directors if not x.end_date]
        current = current_list[-1] if current_list else c.directors[-1]
        rows.append(["Руководитель", current.name, {"inn": current.inn}, current.post or "—",
                     _person_notes(current.unreliable, current.is_mass, current.disqualified)])
        for x in c.directors:
            if x is current:
                continue
            period = f"{date_ru(x.record_date)}" + (f" — {date_ru(x.end_date)}" if x.end_date else "")
            rows.append(["Бывший руководитель", x.name, {"inn": x.inn}, (x.post or "—") + (f"\n{period}" if period.strip() else ""),
                         _person_notes(x.unreliable, x.is_mass, x.disqualified)])
    elif d and d.management and d.management.name:
        m = d.management
        rows.append(["Руководитель", m.name, {"inn": m.inn} if m.inn else "—", m.post or "—", ["—"]])

    if c:
        for f in c.founders:
            rows.append([f"Учредитель ({f.kind})" if f.kind else "Учредитель", f.name, {"inn": f.inn} if f.inn else "—",
                         f.share or "—", _person_notes(f.unreliable, f.is_mass, False)])
    if not rows:
        return None
    return {
        "title": "Руководство и учредители", "type": "table",
        "columns": ["Роль", "Имя / организация", "ИНН", "Должность / доля", "Пометки"],
        "widths": [0.17, 0.29, 0.15, 0.22, 0.17], "align": ["L", "L", "L", "L", "L"],
        "rows": rows,
    }


def _sec_finance(result) -> list[dict]:
    fin = _latest_fin(result)
    out: list[dict] = []
    if fin:
        rows = []
        desc = list(reversed(fin))[:6]
        for i, f in enumerate(desc):
            older = desc[i + 1] if i + 1 < len(desc) else None
            delta: object = "—"
            if f.revenue is not None and older and older.revenue:
                ch = (f.revenue - older.revenue) / abs(older.revenue) * 100
                delta = _chip(f"{'+' if ch >= 0 else '−'}{abs(ch):.0f}%", "low" if ch >= 0 else "high")
            profit: object = money(f.profit)
            if f.profit is not None and f.profit < 0:
                profit = _chip(money(f.profit), "high")
            rows.append([str(f.year), money(f.revenue), delta, profit, money(f.assets)])
        out.append({
            "title": "Финансовые показатели", "type": "table",
            "columns": ["Год", "Выручка", "Изм.", "Чистая прибыль", "Активы"],
            "widths": [0.12, 0.26, 0.14, 0.26, 0.22], "align": ["L", "R", "R", "R", "R"],
            "rows": rows,
            "note": "Бухгалтерская отчётность (Росстат, ГИР БО ФНС). Изменение — выручка к предыдущему году.",
        })

    c, e = result.checko, result.entrepreneur
    kv: list[list] = []
    if c:
        if c.capital is not None:
            kv.append(["Уставный капитал", money_full(c.capital)])
        if c.tax_system:
            kv.append(["Налоговый режим", c.tax_system])
        if c.vat_paid is not None:
            kv.append(["НДС уплачено", money_full(c.vat_paid)])
        if c.taxes_paid is not None:
            kv.append(["Всего налогов и взносов", money_full(c.taxes_paid)])
        if c.employee_count is not None:
            kv.append(["Среднесписочная численность", str(c.employee_count)])
        if c.is_smb:
            kv.append(["Реестр МСП", c.smb_category.capitalize() if c.smb_category else "да"])
    elif e:
        kv.append(["Налоговый режим", e.tax_system or "—"])
        if e.is_smb:
            kv.append(["Реестр МСП", e.smb_category.capitalize() if e.smb_category else "да"])
    if kv:
        out.append({"title": "Налоги и ресурсы", "type": "kv", "rows": kv})
    return out


def _sec_reliability(result) -> dict | None:
    rel = result.reliability
    if not rel or not rel.factors:
        return None
    order = {"Негатив": 0, "ТрВним": 1, "Полож": 2}
    lvl = {"Негатив": "high", "ТрВним": "medium", "Полож": "low"}
    factors = sorted(rel.factors, key=lambda f: (order.get(f.kind, 3), -(f.weight or 0)))
    return {
        "title": "Оценка надёжности Checko (по подходам ФНС)", "type": "flags",
        "items": [{"level": lvl.get(f.kind, "none"), "text": f.text} for f in factors],
    }


def _sec_enforcements(result) -> dict | None:
    items = result.enforcements
    if not items:
        return None
    debt = sum(x.debt_remaining or x.debt_sum or 0 for x in items)
    rows = [[x.number or "—", date_ru(x.date), (x.subject or "—")[:90], money(x.debt_remaining if x.debt_remaining is not None else x.debt_sum)]
            for x in items[:8]]
    return {
        "title": "Исполнительные производства (ФССП)", "type": "table",
        "summary": f"Всего: {len(items)} · непогашенный долг: {money(debt)}",
        "columns": ["№ производства", "Дата", "Предмет исполнения", "Остаток долга"],
        "widths": [0.24, 0.14, 0.42, 0.20], "align": ["L", "L", "L", "R"], "rows": rows,
        "more": max(0, len(items) - 8),
    }


def _sec_cases(result) -> dict | None:
    lc = result.legal_cases
    if not lc or not lc.cases:
        return None
    defendant = sum(1 for x in lc.cases if x.role == "ответчик")
    rows = []
    for x in sorted(lc.cases, key=lambda z: z.date or "", reverse=True)[:8]:
        link = f"https://kad.arbitr.ru/Card/{x.uuid}" if x.uuid else x.kad_url
        rows.append([x.number or "—", date_ru(x.date),
                     _chip(x.role, "medium" if x.role == "ответчик" else "none"),
                     money(x.claim_sum) if x.claim_sum else "—",
                     {"link": link, "t": "открыть ↗"} if link else "—"])
    return {
        "title": "Арбитражные дела", "type": "table",
        "summary": f"Всего: {len(lc.cases)} (истец: {len(lc.cases) - defendant}, ответчик: {defendant}) · сумма исков: {money(lc.total_claim_sum)}",
        "columns": ["№ дела", "Дата", "Роль", "Сумма иска", "КАД"],
        "widths": [0.30, 0.15, 0.17, 0.22, 0.16], "align": ["L", "L", "L", "R", "L"], "rows": rows,
        "more": max(0, len(lc.cases) - 8),
    }


def _sec_inspections(result) -> dict | None:
    ins = result.inspections
    if not ins:
        return None
    bad = sum(1 for i in ins if i.violations_found)
    items = [{"level": "medium" if bad else "low",
              "text": f"Проверок в реестре: {len(ins)}, из них с выявленными нарушениями: {bad}"},
             {"level": "none", "text": "Подробности — портал proverki.gov.ru, поиск по ИНН " + result.inn}]
    return {"title": "Проверки (ЕРКНМ / ЕРП)", "type": "flags", "items": items}


def _sec_contracts(result) -> dict | None:
    cs = result.contracts
    if not cs:
        return None
    cust = sum(1 for x in cs if x.customer_inn == result.inn)
    total = sum(x.price or 0 for x in cs)
    rows = []
    for x in sorted(cs, key=lambda z: z.date or "", reverse=True)[:8]:
        role = "заказчик" if x.customer_inn == result.inn else "поставщик"
        rows.append([{"link": x.eis_url, "t": x.reg_number or "—"} if x.eis_url else (x.reg_number or "—"),
                     date_ru(x.date), role, money(x.price) if x.price else "—", (x.objects[0] if x.objects else "—")[:70]])
    return {
        "title": "Госконтракты (44-ФЗ)", "type": "table",
        "summary": f"Всего: {len(cs)} (заказчик: {cust}, поставщик: {len(cs) - cust}) · на сумму: {money(total)}",
        "columns": ["№ контракта", "Дата", "Роль", "Цена", "Предмет закупки"],
        "widths": [0.27, 0.12, 0.12, 0.15, 0.34], "align": ["L", "L", "L", "R", "L"], "rows": rows,
        "more": max(0, len(cs) - 8),
    }


def _sec_bankruptcy(result) -> dict | None:
    bm = result.bankruptcy_messages
    if not bm:
        return None
    rows = [[date_ru(x.date), x.type_name or "—", x.case_number or "—",
             {"link": x.url, "t": "открыть ↗"} if x.url else "—"]
            for x in sorted(bm, key=lambda z: z.date or "", reverse=True)[:8]]
    return {
        "title": "Записи ЕФРСБ (банкротство)", "type": "table", "summary": f"Всего: {len(bm)}",
        "columns": ["Дата", "Тип записи", "Дело", "Источник"],
        "widths": [0.15, 0.47, 0.22, 0.16], "align": ["L", "L", "L", "L"], "rows": rows,
        "more": max(0, len(bm) - 8),
    }


def _sec_fedresurs(result) -> dict | None:
    fs = result.fedresurs
    if not fs:
        return None
    rows = [[date_ru(x.date), x.type_name or "—", {"link": x.url, "t": "открыть ↗"} if x.url else "—"]
            for x in sorted(fs, key=lambda z: z.date or "", reverse=True)[:8]]
    return {
        "title": "Сообщения Федресурса", "type": "table", "summary": f"Всего: {len(fs)}",
        "columns": ["Дата", "Тип сообщения", "Источник"],
        "widths": [0.15, 0.68, 0.17], "align": ["L", "L", "L"], "rows": rows,
        "more": max(0, len(fs) - 8),
    }


def _sec_timeline(result) -> dict | None:
    tl = result.timeline
    if not tl:
        return None
    rows = [[date_ru(x.date), x.text] for x in sorted(tl, key=lambda z: z.date or "", reverse=True)[:8]]
    return {
        "title": "История изменений", "type": "table",
        "summary": f"Последние {len(rows)} из {len(tl)} событий",
        "columns": ["Дата", "Событие"], "widths": [0.14, 0.86], "align": ["L", "L"], "rows": rows,
        "more": max(0, len(tl) - 8),
    }


def _sec_related(result) -> dict | None:
    raw: list[dict] = []
    c, e, p = result.checko, result.entrepreneur, result.person
    if c:
        raw += c.related_orgs
    if e:
        raw += e.managed_orgs + e.founded_orgs
    elif p:
        raw += p.managed_orgs + p.founded_orgs
    merged: dict[str, dict] = {}
    for r in raw:
        key = r.get("inn") or r.get("name") or str(len(merged))
        kind = r.get("kind") or "связь"
        if key in merged:
            if kind not in merged[key]["kinds"]:
                merged[key]["kinds"].append(kind)
        else:
            merged[key] = {"name": r.get("name") or "—", "inn": r.get("inn") or "", "status": r.get("status") or "", "kinds": [kind]}
    if not merged:
        return None
    items = list(merged.values())
    rows = [[m["name"], {"inn": m["inn"]} if m["inn"] else "—",
             _chip(m["status"] or "—", status_level(m["status"])) if m["status"] else "—",
             "; ".join(m["kinds"])] for m in items[:15]]
    return {
        "title": "Связанные организации", "type": "table", "summary": f"Всего: {len(items)}",
        "columns": ["Организация", "ИНН", "Статус", "Чем связана"],
        "widths": [0.36, 0.16, 0.17, 0.31], "align": ["L", "L", "L", "L"], "rows": rows,
        "more": max(0, len(items) - 15),
    }


def _sec_heuristics(result) -> dict | None:
    nd, sc = result.nominal_director, result.shell_company
    if not nd and not sc:
        return None
    items: list[dict] = []
    for title, v in (("Номинальный директор", nd), ("Техническая / обнальная организация", sc)):
        if not v:
            continue
        items.append({"level": v.level, "text": f"{title}: {v.label} ({v.score}/100)", "bold": True})
        if v.flags:
            for f in v.flags:
                items.append({"level": "none", "text": f"{f.label}: {f.detail}", "indent": 1})
        else:
            items.append({"level": "none", "text": "Явных признаков по доступным данным не найдено", "indent": 1})
    return {
        "title": "Дополнительная эвристика бота", "type": "flags", "items": items,
        "note": "Собственная проверка бота сверх официальной оценки Checko — по совокупности косвенных признаков; это не установленный факт.",
    }


def build_pdf_model(result) -> dict:
    d, c, e, p = result.dadata, result.checko, result.entrepreneur, result.person
    title = (
        (d.name_short if d and d.name_short else None)
        or (c.name_short if c and c.name_short else None)
        or (f"ИП {e.fio}" if e and e.fio else None)
        or (p.fio if p and p.fio else None)
        or result.inn
    )
    sub = [f"ИНН {result.inn}"]
    if d and d.ogrn:
        sub.append(f"ОГРН {d.ogrn}")
    elif e and e.ogrnip:
        sub.append(f"ОГРНИП {e.ogrnip}")

    st_text, st_level = _main_status(result)
    alerts = build_alerts(result)

    sections: list[dict] = []
    for s in (
        _sec_main(result), _sec_people(result), *_sec_finance(result), _sec_reliability(result),
        _sec_enforcements(result), _sec_cases(result), _sec_inspections(result), _sec_contracts(result),
        _sec_bankruptcy(result), _sec_fedresurs(result), _sec_timeline(result), _sec_related(result),
        _sec_heuristics(result),
    ):
        if s:
            sections.append(s)

    return {
        "v": 1,
        "inn": result.inn,
        "title": title,
        "subtitle": " · ".join(sub),
        "generated": date.today().strftime("%d.%m.%Y"),
        "status": {"text": st_text, "level": st_level},
        "verdict": build_verdict(result, alerts),
        "alerts": [{"level": lv, "text": tx} for lv, tx in alerts],
        "kpis": build_kpis(result),
        "sections": sections,
    }
