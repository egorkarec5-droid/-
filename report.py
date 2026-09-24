from parser import CounterpartySummary
from rules import RiskAssessment

DISCLAIMER = (
    "\n\n⚠️ Оценка сформирована автоматически на основе открытых данных "
    "и не является юридическим заключением. Пока не учитывает реальный масштаб "
    "бизнеса контрагента (выручку) — крупные компании могут получать завышенный "
    "риск-балл на операциях, которые для их оборота типичны."
)


def format_summary_line(assessments: dict[str, RiskAssessment]) -> str:
    high = sum(1 for a in assessments.values() if a.level == "high")
    medium = sum(1 for a in assessments.values() if a.level == "medium")
    low = sum(1 for a in assessments.values() if a.level == "low")
    total = len(assessments)
    return f"Найдено {total} контрагентов: 🔴 {high} · 🟡 {medium} · 🟢 {low}"


def format_top_risks(
    summaries: dict[str, CounterpartySummary],
    assessments: dict[str, RiskAssessment],
    limit: int = 10,
) -> str:
    ranked = sorted(assessments.items(), key=lambda kv: kv[1].score, reverse=True)
    risky = [(inn, a) for inn, a in ranked if a.level in ("high", "medium")][:limit]

    if not risky:
        return "Контрагентов с повышенным риском не найдено."

    lines = ["📋 Список рисков:\n"]
    for inn, a in risky:
        s = summaries[inn]
        lines.append(f"{a.emoji} {s.name} (ИНН {inn}) — {a.score}/100")
        for flag in a.flags:
            lines.append(f"   • {flag.label}: {flag.detail}")
        lines.append("")

    hidden = sum(1 for _, a in ranked if a.level in ("high", "medium")) - len(risky)
    if hidden > 0:
        lines.append(f"…и ещё {hidden} контрагентов с риском — используйте экспорт для полного списка.")

    return "\n".join(lines)
