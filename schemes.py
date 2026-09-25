"""
Схемы для бота: платежи и связи. Рендерятся через matplotlib (чистый pip-пакет,
без системной зависимости от Graphviz/dot — надёжнее для деплоя на Railway).
"""

from __future__ import annotations
import io
import textwrap
from dataclasses import dataclass

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch, Polygon, FancyArrowPatch
from matplotlib.font_manager import FontProperties

RISK_COLORS = {
    "low": "#3B6D11",
    "medium": "#8A6D00",
    "high": "#A32D2D",
}
RISK_FILLS = {
    "low": "#D7ECC7",
    "medium": "#F4E3B8",
    "high": "#F7C1C1",
}
RISK_LABELS = {"low": "низкий риск", "medium": "средний риск", "high": "высокий риск"}


@dataclass
class FlowRow:
    name: str
    inn: str
    amount: float
    level: str  # "low" | "medium" | "high"


def _fmt_money(v: float) -> str:
    if abs(v) >= 1_000_000:
        return f"{v / 1_000_000:.1f} млн ₽"
    if abs(v) >= 1_000:
        return f"{v / 1_000:.0f} тыс ₽"
    return f"{v:.0f} ₽"


def _draw_box(ax, x, y, w, h, name, sub, color, fill, is_person=False, wrap_chars=13):
    """Прямоугольник (организация) или пятиугольник-'домик' (ИП, ИНН из 12 цифр).
    Название переносится по словам, чтобы никогда не вылезать за пределы блока."""
    wrapped = textwrap.fill(name, width=wrap_chars, max_lines=3, placeholder="…")
    n_lines = wrapped.count("\n") + 1
    h = max(h, 0.32 * n_lines + (0.28 if sub else 0.12))

    if is_person:
        pts = [
            (x, y), (x + w, y), (x + w, y + h * 0.6),
            (x + w / 2, y + h), (x, y + h * 0.6),
        ]
        ax.add_patch(Polygon(pts, closed=True, facecolor=fill, edgecolor=color, linewidth=1.5, zorder=2))
        cy_ = y + h * 0.32
    else:
        ax.add_patch(FancyBboxPatch(
            (x, y), w, h, boxstyle="round,pad=0,rounding_size=0.06",
            facecolor=fill, edgecolor=color, linewidth=1.5, zorder=2,
        ))
        cy_ = y + h / 2

    name_y = cy_ + (0.16 if sub else 0.0)
    ax.text(x + w / 2, name_y, wrapped, ha="center", va="center", fontsize=8.3, linespacing=1.25, zorder=3)
    if sub:
        ax.text(x + w / 2, cy_ - 0.32 * n_lines / 2 - 0.05, sub, ha="center", va="center", fontsize=7, color="#555555", zorder=3)
    return h


def render_money_flow(company_name: str, rows: list[FlowRow]) -> bytes:
    """Схема: наша организация -> топ контрагентов, форма по типу (ИП/организация),
    цвет по риск-уровню, подпись суммы на стрелке."""
    n = max(1, len(rows))
    fig_w = max(6.0, 1.9 * n)
    fig, ax = plt.subplots(figsize=(fig_w, 4.2))
    ax.set_xlim(0, fig_w)
    ax.set_ylim(0, 4.2)
    ax.axis("off")

    cx, cy, cw, ch = fig_w / 2 - 1.1, 3.4, 2.2, 0.6
    _draw_box(ax, cx, cy, cw, ch, company_name[:40], "", "#333333", "#EDEDED", wrap_chars=17)

    spacing = fig_w / n
    box_w, box_h = min(1.9, spacing * 0.85), 0.85
    ty = 1.5

    for i, row in enumerate(rows):
        tx = spacing * i + spacing / 2 - box_w / 2
        color = RISK_COLORS.get(row.level, "#555555")
        fill = RISK_FILLS.get(row.level, "#EEEEEE")
        is_person = len(row.inn) == 12
        real_h = _draw_box(ax, tx, ty, box_w, box_h, row.name, f"ИНН {row.inn}", color, fill, is_person)

        start = (cx + cw / 2, cy)
        end = (tx + box_w / 2, ty + real_h)
        arrow = FancyArrowPatch(start, end, arrowstyle="-|>", mutation_scale=12,
                                 color=color, linewidth=1.3, zorder=1)
        ax.add_patch(arrow)
        t = 0.6
        mx, my = start[0] + (end[0] - start[0]) * t, start[1] + (end[1] - start[1]) * t + 0.12
        ax.text(mx, my, _fmt_money(row.amount), ha="center", va="center", fontsize=7.5,
                color=color, zorder=3, bbox=dict(boxstyle="round,pad=0.15", fc="white", ec="none"))

    legend_y = 0.35
    legend_x = 0.3
    for level in ("low", "medium", "high"):
        ax.add_patch(plt.Rectangle((legend_x, legend_y - 0.08), 0.18, 0.18, facecolor=RISK_FILLS[level], edgecolor=RISK_COLORS[level]))
        ax.text(legend_x + 0.28, legend_y, RISK_LABELS[level], fontsize=7.5, va="center")
        legend_x += 1.9
    ax.text(fig_w - 0.3, legend_y, "домик = ИП, прямоугольник = организация", fontsize=7, va="center", ha="right", color="#666666")

    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=160, bbox_inches="tight")
    plt.close(fig)
    buf.seek(0)
    return buf.read()


@dataclass
class RelationRow:
    name: str
    inn: str
    active: bool


def render_relations(company_name: str, rows: list[RelationRow]) -> bytes:
    """Схема связей: наша организация -> связанные организации (по данным Checko)."""
    n = max(1, len(rows))
    fig_w = max(6.0, 1.9 * n)
    fig, ax = plt.subplots(figsize=(fig_w, 3.6))
    ax.set_xlim(0, fig_w)
    ax.set_ylim(0, 3.6)
    ax.axis("off")

    cx, cy, cw, ch = fig_w / 2 - 1.2, 2.8, 2.4, 0.6
    _draw_box(ax, cx, cy, cw, ch, company_name[:40], "", "#333333", "#EDEDED", wrap_chars=17)

    spacing = fig_w / n
    box_w, box_h = min(1.7, spacing * 0.82), 0.75
    ty = 1.0

    for i, row in enumerate(rows):
        tx = spacing * i + spacing / 2 - box_w / 2
        color = "#3B6D11" if row.active else "#A32D2D"
        fill = "#D7ECC7" if row.active else "#F7C1C1"
        real_h = _draw_box(ax, tx, ty, box_w, box_h, row.name, f"ИНН {row.inn}", color, fill)

        start = (cx + cw / 2, cy)
        end = (tx + box_w / 2, ty + real_h)
        arrow = FancyArrowPatch(start, end, arrowstyle="-", mutation_scale=10,
                                 color="#666666", linewidth=1.1, linestyle=(0, (4, 3)), zorder=1)
        ax.add_patch(arrow)

    legend_y = 0.15
    ax.add_patch(plt.Rectangle((0.3, legend_y - 0.08), 0.18, 0.18, facecolor="#D7ECC7", edgecolor="#3B6D11"))
    ax.text(0.58, legend_y, "действующая", fontsize=7.5, va="center")
    ax.add_patch(plt.Rectangle((2.1, legend_y - 0.08), 0.18, 0.18, facecolor="#F7C1C1", edgecolor="#A32D2D"))
    ax.text(2.38, legend_y, "не действует / ликвидирована", fontsize=7.5, va="center")

    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=160, bbox_inches="tight")
    plt.close(fig)
    buf.seek(0)
    return buf.read()
