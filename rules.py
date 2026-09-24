"""
Правила красных флагов по операциям книги покупок/продаж.
Работает только с данными самой книги — без внешних API (это Этап 1).
Флаги, требующие DaData/Checko (массовый адрес, совпадение директора и т.д.)
добавятся на Этапе 2 в отдельном модуле counterparty_rules.py.
"""

from __future__ import annotations
from dataclasses import dataclass
from statistics import median

from parser import CounterpartySummary, Operation


# Пороги контроля, рядом с которыми суммы выглядят подозрительно (± 5%)
CONTROL_THRESHOLDS = [100_000, 600_000, 1_000_000]
THRESHOLD_TOLERANCE = 0.05

# Круглая сумма — делится на это значение без остатка
ROUND_SUM_DIVISOR = 10_000

# Во сколько раз сумма операции должна превышать медиану операций
# этого контрагента, чтобы считаться "резким скачком"
SPIKE_MULTIPLIER = 3

# Круглые суммы и суммы у порога осмысленны только для операций в диапазоне,
# актуальном для контроля по 115-ФЗ — не для рутинного крупного корпоративного
# документооборота (там счета на сотни миллионов круглыми суммами — норма,
# а не признак обнала). Выше этого потолка обе проверки просто не применяются.
RELEVANT_SCALE_CEILING = 3_000_000


@dataclass
class RiskFlag:
    code: str
    label: str
    weight: int          # вклад в риск-балл (0-100 суммарно ограничивается сверху)
    detail: str = ""


@dataclass
class RiskAssessment:
    score: int                      # 0-100, чем выше — тем рискованнее
    level: str                      # "low" | "medium" | "high"
    flags: list[RiskFlag]

    @property
    def emoji(self) -> str:
        return {"low": "🟢", "medium": "🟡", "high": "🔴"}[self.level]


def _is_round_sum(amount: float) -> bool:
    return amount != 0 and amount % ROUND_SUM_DIVISOR == 0


def _near_threshold(amount: float) -> float | None:
    if amount > RELEVANT_SCALE_CEILING:
        return None
    for threshold in CONTROL_THRESHOLDS:
        if threshold * (1 - THRESHOLD_TOLERANCE) <= amount < threshold:
            return threshold
    return None


def _check_round_sums(summary: CounterpartySummary) -> RiskFlag | None:
    round_ops = [
        op for op in summary.operations
        if _is_round_sum(op.amount) and abs(op.amount) <= RELEVANT_SCALE_CEILING
    ]
    # для мелких/средних сумм 2+ совпадений уже заметно; при большом числе
    # операций у контрагента требуем и заметную долю, не только счётчик
    relevant_ops = [op for op in summary.operations if abs(op.amount) <= RELEVANT_SCALE_CEILING]
    if len(round_ops) >= 2 and (len(relevant_ops) < 10 or len(round_ops) / len(relevant_ops) >= 0.3):
        return RiskFlag(
            code="round_sums",
            label="Круглые суммы",
            weight=10,
            detail=f"{len(round_ops)} операций с суммой, кратной {ROUND_SUM_DIVISOR:,} ₽ (в диапазоне до {RELEVANT_SCALE_CEILING:,} ₽)".replace(",", " "),
        )
    return None


def _check_threshold_sums(summary: CounterpartySummary) -> RiskFlag | None:
    hits = []
    for op in summary.operations:
        threshold = _near_threshold(op.amount)
        if threshold:
            hits.append((op, threshold))
    if hits:
        thresholds_str = ", ".join(f"{t:,.0f} ₽".replace(",", " ") for _, t in hits[:3])
        return RiskFlag(
            code="threshold_sums",
            label="Суммы у порога контроля",
            weight=20,
            detail=f"{len(hits)} операций чуть ниже порогов: {thresholds_str}",
        )
    return None


def _check_spike(summary: CounterpartySummary) -> RiskFlag | None:
    amounts = [abs(op.amount) for op in summary.operations if op.amount != 0]
    if len(amounts) < 3:
        return None
    med = median(amounts)
    if med == 0:
        return None
    spikes = [a for a in amounts if a > med * SPIKE_MULTIPLIER]
    if spikes:
        return RiskFlag(
            code="spike",
            label="Резкий скачок суммы",
            weight=25,
            detail=(
                f"Операция на {max(spikes):,.2f} ₽ в {max(spikes) / med:.1f} раза "
                f"выше типичной для этого контрагента ({med:,.2f} ₽)"
            ).replace(",", " "),
        )
    return None


def _check_storno_share(summary: CounterpartySummary) -> RiskFlag | None:
    negative = [op for op in summary.operations if op.amount < 0]
    if not summary.operations:
        return None
    share = len(negative) / len(summary.operations)
    if share >= 0.3 and len(negative) >= 2:
        return RiskFlag(
            code="storno_share",
            label="Много сторно-операций",
            weight=15,
            detail=f"{len(negative)} из {len(summary.operations)} операций — отрицательные (корректировки)",
        )
    return None


def _check_direction_mismatch(summary: CounterpartySummary) -> RiskFlag | None:
    """Контрагент почти полностью в одном направлении при крупном обороте —
    само по себе не страшно, но полезная деталь для контекста."""
    if summary.total_turnover < 1_000_000:
        return None
    if summary.purchase_count > 0 and summary.sale_count > 0:
        return None
    direction = "покупках" if summary.purchase_count > 0 else "продажах"
    return RiskFlag(
        code="single_direction",
        label="Оборот только в одном направлении",
        weight=5,
        detail=f"Весь оборот ({summary.total_turnover:,.2f} ₽) — только в {direction}".replace(",", " "),
    )


CHECKS = [
    _check_round_sums,
    _check_threshold_sums,
    _check_spike,
    _check_storno_share,
    _check_direction_mismatch,
]


def find_transit_operations(all_ops: list[Operation]) -> dict[str, list[Operation]]:
    """Ищет операции, где тот же номер счёта-фактуры и та же сумма встречаются
    и в покупках, и в продажах — признак транзитной/пропускной схемы
    (купили и тут же перепродали по той же цене).
    Возвращает {inn: [операции-совпадения]} для обеих сторон сделки.
    """
    purchases_by_sig = {}
    for op in all_ops:
        if op.kind == "purchase":
            sig = (op.invoice_no, op.invoice_date, round(op.amount, 2))
            purchases_by_sig.setdefault(sig, []).append(op)

    result: dict[str, list[Operation]] = {}
    for op in all_ops:
        if op.kind != "sale":
            continue
        sig = (op.invoice_no, op.invoice_date, round(op.amount, 2))
        matches = purchases_by_sig.get(sig)
        if not matches:
            continue
        result.setdefault(op.inn, []).append(op)
        for m in matches:
            result.setdefault(m.inn, []).append(m)
    return result


def assess(summary: CounterpartySummary, transit_ops: dict[str, list[Operation]] | None = None) -> RiskAssessment:
    flags = []
    for check in CHECKS:
        flag = check(summary)
        if flag:
            flags.append(flag)

    if transit_ops and summary.inn in transit_ops:
        ops = transit_ops[summary.inn]
        flags.append(RiskFlag(
            code="transit",
            label="Транзитная операция",
            weight=35,
            detail=(
                f"Счёт-фактура №{ops[0].invoice_no} на {ops[0].amount:,.2f} ₽ проходит "
                f"и в покупках, и в продажах той же суммой — признак пропускной схемы"
            ).replace(",", " "),
        ))

    score = min(100, sum(f.weight for f in flags))
    if score >= 50:
        level = "high"
    elif score >= 20:
        level = "medium"
    else:
        level = "low"

    return RiskAssessment(score=score, level=level, flags=flags)


def assess_all(
    summaries: dict[str, CounterpartySummary],
    all_ops: list[Operation] | None = None,
) -> dict[str, RiskAssessment]:
    transit_ops = find_transit_operations(all_ops) if all_ops else None
    return {inn: assess(s, transit_ops) for inn, s in summaries.items()}
