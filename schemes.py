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
    purchase: float = 0.0   # сколько мы купили у контрагента (по книге покупок)
    sale: float = 0.0       # сколько мы продали контрагенту (по книге продаж)
    transit: bool = False   # найдены транзитные операции (тот же счёт-фактура и в покупках, и в продажах)


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
    """Схема: наша организация -> контрагенты. Форма по типу (ИП/организация), цвет по
    риск-уровню, на стрелке — ЧЕМ связаны: покупка / продажа / и то и другое (+ транзит)."""
    n = max(1, len(rows))
    fig_w = max(7.6, 2.0 * n)
    top, bottom = 5.0, -0.5
    fig, ax = plt.subplots(figsize=(fig_w, top - bottom))
    ax.set_xlim(0, fig_w)
    ax.set_ylim(bottom, top)
    ax.axis("off")

    cx, cy, cw, ch = fig_w / 2 - 1.1, 4.2, 2.2, 0.6
    _draw_box(ax, cx, cy, cw, ch, company_name[:40], "", "#333333", "#EDEDED", wrap_chars=17)

    spacing = fig_w / n
    box_w, box_h = min(1.9, spacing * 0.85), 0.85
    ty = 1.6

    for i, row in enumerate(rows):
        tx = spacing * i + spacing / 2 - box_w / 2
        color = RISK_COLORS.get(row.level, "#555555")
        fill = RISK_FILLS.get(row.level, "#EEEEEE")
        is_person = len(row.inn) == 12
        real_h = _draw_box(ax, tx, ty, box_w, box_h, row.name, f"ИНН {row.inn}", color, fill, is_person)

        start = (cx + cw / 2, cy)
        end = (tx + box_w / 2, ty + real_h)

        # чем связаны: покупка (мы платим) / продажа (нам платят) / и то и другое
        if row.purchase and row.sale:
            style = "<|-|>"
            label = f"покупка {_fmt_money(row.purchase)}\nпродажа {_fmt_money(row.sale)}"
        elif row.sale:
            style = "<|-"
            label = f"продажа {_fmt_money(row.sale)}"
        elif row.purchase:
            style = "-|>"
            label = f"покупка {_fmt_money(row.purchase)}"
        else:
            style = "-|>"
            label = _fmt_money(row.amount)
        if row.transit:
            label += "\n⇄ транзит"

        ax.add_patch(FancyArrowPatch(start, end, arrowstyle=style, mutation_scale=12,
                                     color=color, linewidth=1.3, zorder=1))
        t = 0.72  # ближе к блоку контрагента — там стрелки уже разошлись и подписи не слипаются
        mx, my = start[0] + (end[0] - start[0]) * t, start[1] + (end[1] - start[1]) * t
        ax.text(mx, my, label, ha="center", va="center", fontsize=7.2, linespacing=1.15,
                color=color, zorder=3, bbox=dict(boxstyle="round,pad=0.15", fc="white", ec="none"))

    # легенда — на отдельных строках, чтобы ничего не наезжало
    ly = 0.55
    lx = 0.3
    for level in ("low", "medium", "high"):
        ax.add_patch(plt.Rectangle((lx, ly - 0.08), 0.18, 0.18, facecolor=RISK_FILLS[level], edgecolor=RISK_COLORS[level]))
        ax.text(lx + 0.28, ly, RISK_LABELS[level], fontsize=7.5, va="center")
        lx += 1.9
    ax.text(0.3, ly - 0.35, "домик = ИП, прямоугольник = организация", fontsize=7, va="center", color="#666666")
    ax.text(0.3, ly - 0.6, "→ покупка (мы платим)   ← продажа (нам платят)   ↔ и то и другое   ⇄ транзит", fontsize=7, va="center", color="#666666")

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
    label: str = ""   # чем связана с проверяемой организацией (общий учредитель, руководитель...)


def render_relations(company_name: str, rows: list[RelationRow]) -> bytes:
    """Схема связей: наша организация -> связанные организации (по данным Checko)."""
    n = max(1, len(rows))
    fig_w = max(7.6, 2.3 * n)
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
        if row.label:
            t = 0.55
            mx, my = start[0] + (end[0] - start[0]) * t, start[1] + (end[1] - start[1]) * t
            label_text = "\n".join(
                textwrap.fill(part, width=18, max_lines=2, placeholder="…") for part in row.label.split("; ")[:3]
            )
            ax.text(mx, my, label_text,
                    ha="center", va="center", fontsize=7.2, color="#333333", zorder=3, linespacing=1.15,
                    bbox=dict(boxstyle="round,pad=0.15", fc="white", ec="#BBBBBB", lw=0.5))

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


# ───────────────────── общий граф связей между организациями ─────────────────────

def _est_box_h(name: str, wrap_chars: int, has_sub: bool = True) -> float:
    lines = textwrap.fill(name, width=wrap_chars, max_lines=3, placeholder="…").count("\n") + 1
    return max(0.85, 0.32 * lines + (0.28 if has_sub else 0.12))


def render_connections_graph(nodes: list[dict], edges: list[tuple[str, str, str]], title: str = "") -> bytes:
    """Граф: узлы — организации/ИП, линии — связи, на КАЖДОЙ линии подписано, чем связаны.
    nodes: [{"inn", "name", "level"(необязательно: low/medium/high)}]
    edges: [(inn_a, inn_b, "общий директор"), ...] — несколько связей одной пары объединяются в одну подпись."""
    import math

    n = max(1, len(nodes))
    box_w = 1.9
    if n == 1:
        pos = {nodes[0]["inn"]: (0.0, 0.0)}
        rx = ry = 0.0
    elif n == 2:
        rx, ry = 2.6, 0.0
        pos = {nodes[0]["inn"]: (-rx, 0.0), nodes[1]["inn"]: (rx, 0.0)}
    else:
        rx = max(2.6, 0.62 * n + 1.2)
        ry = max(1.9, 0.42 * n + 0.9)
        pos = {}
        for i, nd in enumerate(nodes):
            ang = math.pi / 2 + 2 * math.pi * i / n
            pos[nd["inn"]] = (rx * math.cos(ang), ry * math.sin(ang))

    pad_x, pad_y = box_w / 2 + 0.6, 1.1
    fig_w = 2 * (rx + pad_x)
    fig_h = 2 * (ry + pad_y) + (0.6 if title else 0.2) + 0.6
    fig, ax = plt.subplots(figsize=(max(6.0, fig_w), max(3.6, fig_h)))
    ax.set_xlim(-rx - pad_x, rx + pad_x)
    ax.set_ylim(-ry - pad_y - 0.5, ry + pad_y + (0.5 if title else 0.1))
    ax.axis("off")
    if title:
        ax.text(0, ry + pad_y + 0.2, title, ha="center", va="center", fontsize=9.5, fontweight="bold")

    # объединяем подписи по паре узлов
    merged: dict[tuple[str, str], list[str]] = {}
    for a, b, label in edges:
        if a not in pos or b not in pos or a == b:
            continue
        key = tuple(sorted((a, b)))
        if label and label not in merged.setdefault(key, []):
            merged[key].append(label)

    connected = set()
    for (a, b), labels in merged.items():
        connected.update((a, b))
        (x1, y1), (x2, y2) = pos[a], pos[b]
        ax.add_patch(FancyArrowPatch((x1, y1), (x2, y2), arrowstyle="-", color="#7A5AA6",
                                     linewidth=1.6, zorder=1))
        mx, my = (x1 + x2) / 2, (y1 + y2) / 2
        text = "\n".join(textwrap.fill(l, width=20, max_lines=2, placeholder="…") for l in labels)
        ax.text(mx, my, text, ha="center", va="center", fontsize=7.2, color="#3D2A63", zorder=4,
                linespacing=1.15, bbox=dict(boxstyle="round,pad=0.2", fc="#F3EEFA", ec="#7A5AA6", lw=0.6))

    for nd in nodes:
        x, y = pos[nd["inn"]]
        level = nd.get("level")
        color = RISK_COLORS.get(level, "#555555")
        fill = RISK_FILLS.get(level, "#EEEEEE")
        if nd["inn"] not in connected and len(nodes) > 1:
            fill = "#F7F7F7"
        h = _est_box_h(nd["name"], 15)
        _draw_box(ax, x - box_w / 2, y - h / 2, box_w, h, nd["name"], f"ИНН {nd['inn']}", color, fill,
                  is_person=len(nd["inn"]) == 12, wrap_chars=15)

    ly = -ry - pad_y - 0.2
    lx = -rx - pad_x + 0.3
    if any(nd.get("level") for nd in nodes):
        for level in ("low", "medium", "high"):
            ax.add_patch(plt.Rectangle((lx, ly - 0.08), 0.18, 0.18, facecolor=RISK_FILLS[level], edgecolor=RISK_COLORS[level]))
            ax.text(lx + 0.28, ly, RISK_LABELS[level], fontsize=7.2, va="center")
            lx += 1.7
    ax.text(rx + pad_x - 0.3, ly, "серая рамка/заливка — связей с остальными не найдено", fontsize=6.8,
            va="center", ha="right", color="#777777")

    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=160, bbox_inches="tight")
    plt.close(fig)
    buf.seek(0)
    return buf.read()
