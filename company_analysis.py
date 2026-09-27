"""
Расширенная аналитика по организации: свод всех собранных сигналов
(DaData + Checko: основное, руководители/учредители, финансы, ФССП,
арбитраж, проверки) в два эвристических вывода:
  1) Признаки номинального директора
  2) Признаки технической/обнальной организации

Это эвристика поверх открытых данных, а не юридическое заключение —
она называет конкретные признаки и их источник, чтобы человек мог
сам оценить обоснованность вывода, а не просто получить готовый ярлык.
"""

from __future__ import annotations
from dataclasses import dataclass, field


@dataclass
class AnalysisFlag:
    label: str
    detail: str
    weight: int


@dataclass
class Verdict:
    score: int
    level: str  # "low" | "medium" | "high"
    flags: list[AnalysisFlag] = field(default_factory=list)

    @property
    def emoji(self) -> str:
        return {"low": "🟢", "medium": "🟡", "high": "🔴"}[self.level]

    @property
    def label(self) -> str:
        return {"low": "маловероятно", "medium": "есть отдельные признаки", "high": "признаки выражены сильно"}[self.level]


def _level(score: int) -> str:
    if score >= 50:
        return "high"
    if score >= 20:
        return "medium"
    return "low"


def analyze_nominal_director(checko, dadata) -> Verdict:
    flags = []

    if checko:
        if checko.flags.disqualified_person:
            flags.append(AnalysisFlag("Дисквалифицированное лицо", "в руководстве числится дисквалифицированное лицо", 50))
        if checko.flags.mass_director:
            flags.append(AnalysisFlag("Массовый руководитель", "директор числится руководителем ещё в нескольких организациях", 35))
        for d in checko.directors:
            if d.disqualified and not checko.flags.disqualified_person:
                flags.append(AnalysisFlag("Дисквалификация директора", f"{d.name} — запись о дисквалификации", 50))
            if d.is_mass and not checko.flags.mass_director:
                flags.append(AnalysisFlag("Массовый руководитель", f"{d.name} — признак массовости по данным Checko", 35))
            if d.unreliable:
                flags.append(AnalysisFlag("Недостоверность сведений о директоре", f"{d.name} — отметка недостоверности в ЕГРЮЛ", 30))
    has_director_inn = bool(dadata and dadata.management and dadata.management.inn)
    if not has_director_inn and checko and checko.directors:
        if dadata and dadata.management:
            match = next((di for di in checko.directors if di.name == dadata.management.name), None)
            has_director_inn = bool(match and match.inn)
        else:
            has_director_inn = any(di.inn for di in checko.directors)
    if dadata and dadata.management and not has_director_inn and not (checko and checko.managing_org):
        flags.append(AnalysisFlag("Нет ИНН директора в открытых данных", "затрудняет проверку, само по себе не доказательство", 5))

    score = min(100, sum(f.weight for f in flags))
    return Verdict(score=score, level=_level(score), flags=flags)


def analyze_shell_company(checko, dadata, finances, enforcements, legal_cases, inspections) -> Verdict:
    """Санкции сюда намеренно НЕ включены — это отдельная категория риска
    (уже показана в общем списке флагов), а не признак технической/обнальной фирмы.
    Проверки по ФССП/арбитражу/проверкам нормируются по масштабу организации —
    иначе крупный холдинг с тысячами контрагентов всегда выглядит "подозрительно"
    просто из-за абсолютного числа операций."""
    flags = []

    latest_revenue = finances[-1].revenue if finances else None
    # компания считается "крупной", если выручка >1 млрд ₽ или штат >250 чел. —
    # для такого масштаба абсолютные счётчики ФССП/арбитража/проверок неинформативны
    is_large = bool(
        (latest_revenue and latest_revenue > 1_000_000_000)
        or (checko and checko.employee_count and checko.employee_count > 250)
    )

    if checko:
        if checko.flags.illegal_finance:
            flags.append(AnalysisFlag("Признаки нелегальных финансовых операций", "прямой флаг Checko", 60))
        if checko.flags.unfair_supplier:
            flags.append(AnalysisFlag("Реестр недобросовестных поставщиков", "прямой флаг Checko", 30))
        if checko.flags.mass_founder:
            flags.append(AnalysisFlag("Массовый учредитель", "учредитель числится ещё во многих организациях", 20))
        if checko.mass_address_count and checko.mass_address_count >= 10:
            flags.append(AnalysisFlag("Массовый адрес", f"ещё {checko.mass_address_count} организаций на этом адресе", 20))
        if checko.address_unreliable:
            flags.append(AnalysisFlag("Недостоверность адреса", "отметка в ЕГРЮЛ", 25))
        if checko.capital is not None and checko.capital <= 10_000 and not is_large:
            flags.append(AnalysisFlag("Минимальный уставный капитал", f"{checko.capital:,.0f} ₽ — минимально допустимый".replace(",", " "), 10))

        if finances and checko.employee_count:
            latest = finances[-1]
            if latest.revenue and checko.employee_count > 0:
                rev_per_employee = latest.revenue / checko.employee_count
                # порог сам по себе тоже растёт с масштабом (крупный бизнес просто эффективнее
                # использует персонал) — 20 млн/чел для маленького штата подозрительно,
                # для тысяч сотрудников — нет
                threshold = 20_000_000 if not is_large else 60_000_000
                if rev_per_employee > threshold:
                    flags.append(AnalysisFlag(
                        "Высокая выручка на сотрудника",
                        f"{rev_per_employee:,.0f} ₽/чел за {latest.year} — нетипично для штата в {checko.employee_count} чел.".replace(",", " "),
                        25,
                    ))
        if checko.employee_count == 0 and finances and finances[-1].revenue and finances[-1].revenue > 0:
            flags.append(AnalysisFlag("Нет сотрудников при ненулевой выручке", "выручка есть, штат — 0 человек", 25))

    if enforcements:
        total_debt = sum(e.debt_remaining or e.debt_sum or 0 for e in enforcements)
        if is_large:
            # для крупной организации важна не штука случаев, а непогашенный долг
            # относительно выручки — если это заметная доля, это флаг, иначе нет
            if latest_revenue and total_debt / latest_revenue > 0.01:
                flags.append(AnalysisFlag(
                    "Существенная задолженность ФССП относительно выручки",
                    f"{len(enforcements)} произв., непогашено {total_debt:,.0f} ₽ — {total_debt / latest_revenue:.1%} от выручки".replace(",", " "),
                    20,
                ))
        else:
            w = min(25, 8 * len(enforcements))
            flags.append(AnalysisFlag(
                "Исполнительные производства ФССП",
                f"{len(enforcements)} шт., непогашено {total_debt:,.0f} ₽".replace(",", " "),
                w,
            ))

    if legal_cases and legal_cases.cases:
        as_defendant = [c for c in legal_cases.cases if c.role == "ответчик"]
        if as_defendant:
            if is_large:
                if latest_revenue and legal_cases.total_claim_sum / latest_revenue > 0.05:
                    flags.append(AnalysisFlag(
                        "Существенные иски к организации относительно выручки",
                        f"{len(as_defendant)} дел, сумма исков {legal_cases.total_claim_sum:,.0f} ₽ — "
                        f"{legal_cases.total_claim_sum / latest_revenue:.1%} от выручки".replace(",", " "),
                        20,
                    ))
            else:
                w = min(20, 5 * len(as_defendant))
                flags.append(AnalysisFlag(
                    "Ответчик в арбитражных делах",
                    f"{len(as_defendant)} дел, сумма исков {legal_cases.total_claim_sum:,.0f} ₽".replace(",", " "),
                    w,
                ))

    if inspections:
        with_violations = [i for i in inspections if i.violations_found]
        if with_violations:
            share = len(with_violations) / len(inspections)
            if is_large:
                # для крупной организации важна ДОЛЯ проверок с нарушениями, не штука
                if share > 0.5:
                    flags.append(AnalysisFlag(
                        "Высокая доля проверок с нарушениями",
                        f"{len(with_violations)} из {len(inspections)} ({share:.0%})",
                        15,
                    ))
            else:
                w = min(20, 7 * len(with_violations))
                flags.append(AnalysisFlag(
                    "Нарушения по итогам проверок",
                    f"{len(with_violations)} из {len(inspections)} проверок с нарушениями",
                    w,
                ))

    score = min(100, sum(f.weight for f in flags))
    return Verdict(score=score, level=_level(score), flags=flags)
