"""
Схемы «Досье Плюс»: платежи, связи организации, граф связей между организациями.

Дизайн-принципы (единые токены — theme.py):
  • цвет несёт смысл: рейка слева у строки = риск, синий/фиолетовый = направление денег,
    цвет линии на графе = тип связи; всё остальное — нейтральное;
  • толщина стрелки пропорциональна сумме; форма компании — иконкой (домик — ИП,
    здание — ООО, башня — АО);
  • шрифты: IBM Plex Serif (fonts/, есть запасной DejaVu Serif) для названий, DejaVu Sans для подписей;
  • картинка рассчитана на экран телефона: узкий портретный формат, крупные подписи;
  • текст измеряется реальным шрифтом и переносится/обрезается с «…» — ничего не выходит за рамки.
"""

from __future__ import annotations
import io
import math
import os
from dataclasses import dataclass, field
from functools import lru_cache

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.font_manager import FontProperties
from matplotlib.patches import ArrowStyle, Circle, FancyArrowPatch, FancyBboxPatch, PathPatch, Polygon, Rectangle
from matplotlib.path import Path
from matplotlib.textpath import TextPath

from pdf_model import money as _money_nb
from theme import (
    EDGE_KINDS, HAIRLINE, INFLOW, INK, LEVEL_BG, LEVEL_FG, LEVEL_SOLID, LEVEL_WORD, NAVY, OUTFLOW, STEEL, BAND,
)

W_IN = 6.6          # ширина картинки, дюймов (1 единица координат = 1 дюйм)
MARGIN = 0.28
DPI = 200

# ───────────────────────── шрифты и измерение текста ─────────────────────────

_FONT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fonts")


@lru_cache(maxsize=None)
def _base(role: str) -> FontProperties:
    if role in ("serif_b", "serif"):
        fname = "IBMPlexSerif-Bold.ttf" if role == "serif_b" else "IBMPlexSerif-Regular.ttf"
        path = os.path.join(_FONT_DIR, fname)
        if os.path.exists(path):
            return FontProperties(fname=path)
        return FontProperties(family="DejaVu Serif", weight="bold" if role == "serif_b" else "normal")
    return FontProperties(family="DejaVu Sans", weight="bold" if role == "sans_b" else "normal")


def _f(role: str, size: float) -> FontProperties:
    fp = _base(role).copy()
    fp.set_size(size)
    return fp


def _tw(s: str, fp: FontProperties) -> float:
    """Ширина строки в дюймах — по реальному шрифту, без отрисовки."""
    s = (s or "").replace("\u00a0", " ")
    if not s.strip():
        return 0.0
    return TextPath((0, 0), s, size=fp.get_size_in_points(), prop=fp).get_extents().width / 72.0 * 1.02


def _wrap(s: str, fp: FontProperties, max_w: float, max_lines: int) -> list[str]:
    """Перенос по словам под ширину; слишком длинное слово режется, хвост заменяется «…»."""
    words = [w for w in (s or "").split(" ") if w]      # только по обычным пробелам: NBSP склеивает слова
    lines: list[str] = []
    cur = ""
    for w in words:
        cand = f"{cur} {w}".strip()
        if _tw(cand, fp) <= max_w:
            cur = cand
            continue
        if cur:
            lines.append(cur)
            cur = ""
        while _tw(w, fp) > max_w and len(w) > 1:
            k = len(w)
            while k > 1 and _tw(w[:k], fp) > max_w:
                k -= 1
            nb = w.rfind("\u00a0", 1, k)                # 1) разрыв по неразрывному пробелу («ООО | «Название»»)
            hy = w.rfind("-", 1, k)                     # 2) разрыв после дефиса
            if nb > 0:
                lines.append(w[:nb])
                w = w[nb + 1:]
                continue
            if hy > 0 and hy + 1 >= 3:
                k = hy + 1
            lines.append(w[:k])
            w = w[k:]
        cur = w
    if cur:
        lines.append(cur)
    if len(lines) > max_lines:
        lines = lines[:max_lines]
        last = lines[-1]
        while last and _tw(last + "…", fp) > max_w:
            last = last[:-1]
        lines[-1] = last.rstrip() + "…"
    return lines or [""]


def _fit(text: str, role: str, size: float, min_size: float, max_w: float) -> FontProperties:
    """Шрифт нужного размера, уменьшенный (до min_size), чтобы самое длинное слово не рвалось."""
    longest = max(((text or "").replace("\u00a0", " ").split(" ")), key=len, default="")   # NBSP тут — допустимое место разрыва
    while size > min_size and _tw(longest, _f(role, size)) > max_w:
        size -= 0.3
    return _f(role, size)


def _t(ax, x, y, s, fp, color, ha="left", va="baseline", z=6):
    return ax.text(x, y, (s or "").replace("\u00a0", " "), fontproperties=fp, color=color, ha=ha, va=va, zorder=z)


def _money(v: float | None) -> str:
    return _money_nb(v)          # с неразрывными пробелами: сумма не рвётся при переносе


def _plural(n: int, one: str, few: str, many: str) -> str:
    n10, n100 = n % 10, n % 100
    if n10 == 1 and n100 != 11:
        return one
    if n10 in (2, 3, 4) and n100 not in (12, 13, 14):
        return few
    return many


_FORMS = ("ООО", "АО", "ПАО", "ОАО", "ЗАО", "НАО", "ИП", "АНО", "ФГУП", "МУП", "ГУП", "БФ", "НПФ")


def typo(text: str) -> str:
    """Прямые кавычки -> «ёлочки» (вложенные — „лапки“); учитывает уже стоящие «…»."""
    out, depth, prev = [], 0, " "
    for ch in (text or ""):
        if ch == "«":
            depth += 1
        elif ch == "»":
            depth = max(0, depth - 1)
        if ch == '"':
            if prev in " \u00a0(" or prev in "«„":
                out.append("«" if depth == 0 else "„")
                depth += 1
            else:
                depth = max(0, depth - 1)
                out.append("»" if depth == 0 else "“")
        else:
            out.append(ch)
        prev = out[-1]
    return "".join(out)


def nm(name: str) -> str:
    """Название для показа: типографские кавычки «…», форма (ООО/АО/ИП) не отрывается от названия."""
    s = typo((name or "").strip())
    for f in _FORMS:
        if s.startswith(f + " "):
            return f + "\u00a0" + s[len(f) + 1:]
    return s


def org_form(name: str, inn: str = "") -> str:
    """'ip' — ИП/физлицо, 'ao' — акционерное общество, 'ooo' — всё остальное."""
    n = (name or "").strip().upper()
    if len(str(inn)) == 12 or n.startswith(("ИП ", "ИНДИВИДУАЛЬНЫЙ ПРЕДПРИНИМАТЕЛЬ")):
        return "ip"
    if n.startswith(("АО ", "ПАО ", "ОАО ", "ЗАО ", "НАО ", "АКЦИОНЕРНОЕ", "ПУБЛИЧНОЕ")):
        return "ao"
    return "ooo"


# ───────────────────────── общие детали ─────────────────────────

def _canvas(h: float):
    fig = plt.figure(figsize=(W_IN, h), dpi=DPI, facecolor="white")
    ax = fig.add_axes([0, 0, 1, 1])
    ax.set_xlim(0, W_IN)
    ax.set_ylim(h, 0)          # ось Y вниз — раскладка сверху вниз
    ax.axis("off")
    return fig, ax


def _png(fig) -> bytes:
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=DPI, facecolor="white")
    plt.close(fig)
    return buf.getvalue()


def _plan_header(title: str, subtitle: str) -> dict:
    tfp, sfp = _f("serif_b", 17), _f("sans", 9)
    tlines = _wrap(title, tfp, W_IN - 2 * MARGIN, 2)
    slines = _wrap(subtitle, sfp, W_IN - 2 * MARGIN, 4) if subtitle else []
    t_base = [0.50 + 0.32 * i for i in range(len(tlines))]
    s_base = [t_base[-1] + 0.31 + 0.19 * j for j in range(len(slines))]
    end = s_base[-1] if s_base else t_base[-1]
    rule_y = end + 0.17
    return {"tlines": tlines, "tfp": tfp, "t_base": t_base, "slines": slines, "sfp": sfp, "s_base": s_base,
            "rule_y": rule_y, "h": rule_y + 0.22}


def _draw_header(ax, hd: dict):
    for line, y in zip(hd["tlines"], hd["t_base"]):
        _t(ax, MARGIN, y, line, hd["tfp"], NAVY)
    for line, y in zip(hd["slines"], hd["s_base"]):
        _t(ax, MARGIN, y, line, hd["sfp"], STEEL)
    ax.plot([MARGIN, W_IN - MARGIN], [hd["rule_y"]] * 2, color=NAVY, lw=1.3, solid_capstyle="butt", zorder=2)


def _icon(ax, form: str, x: float, y: float, s: float, color: str = NAVY):
    """Иконка формы организации в квадрате s×s с левым верхним углом (x, y)."""
    z = 5
    win = dict(fc="white", ec="none", zorder=z + 1)
    if form == "ip":       # домик
        ax.add_patch(Polygon([(x, y + 0.48 * s), (x + 0.5 * s, y + 0.06 * s), (x + s, y + 0.48 * s)],
                             closed=True, fc=color, ec="none", zorder=z))
        ax.add_patch(Rectangle((x + 0.14 * s, y + 0.46 * s), 0.72 * s, 0.50 * s, fc=color, ec="none", zorder=z))
        ax.add_patch(Rectangle((x + 0.43 * s, y + 0.66 * s), 0.14 * s, 0.30 * s, **win))
    elif form == "ao":     # башня
        ax.add_patch(Rectangle((x + 0.30 * s, y + 0.12 * s), 0.40 * s, 0.86 * s, fc=color, ec="none", zorder=z))
        ax.add_patch(Rectangle((x + 0.40 * s, y + 0.00 * s), 0.20 * s, 0.14 * s, fc=color, ec="none", zorder=z))
        for r in range(4):
            for c in range(2):
                ax.add_patch(Rectangle((x + (0.37 + 0.14 * c) * s, y + (0.22 + 0.17 * r) * s), 0.08 * s, 0.09 * s, **win))
    else:                  # здание
        ax.add_patch(Rectangle((x + 0.08 * s, y + 0.22 * s), 0.84 * s, 0.76 * s, fc=color, ec="none", zorder=z))
        ax.add_patch(Polygon([(x + 0.02 * s, y + 0.24 * s), (x + 0.98 * s, y + 0.24 * s), (x + 0.90 * s, y + 0.08 * s),
                              (x + 0.10 * s, y + 0.08 * s)], closed=True, fc=color, ec="none", zorder=z))
        for r in range(3):
            for c in range(3):
                ax.add_patch(Rectangle((x + (0.19 + 0.24 * c) * s, y + (0.34 + 0.17 * r) * s), 0.13 * s, 0.10 * s, **win))


def _card_frame(ax, x, y, w, h, rail):
    ax.add_patch(FancyBboxPatch((x, y), w, h, boxstyle="round,pad=0,rounding_size=0.04", fc="white",
                                ec=HAIRLINE, lw=0.9, zorder=3))
    ax.add_patch(FancyBboxPatch((x, y), 0.08, h, boxstyle="round,pad=0,rounding_size=0.035", fc=rail, ec="none", zorder=4))


def _pill(ax, x, y, text, fg, bg, fp, ha="left", pad=0.075, h=0.21, ec=None, z=5) -> float:
    w = _tw(text, fp) + 2 * pad
    x0 = x if ha == "left" else x - w if ha == "right" else x - w / 2
    ax.add_patch(FancyBboxPatch((x0, y - h / 2), w, h, boxstyle=f"round,pad=0,rounding_size={h / 2}",
                                fc=bg, ec=ec or "none", lw=0.8, zorder=z))
    _t(ax, x0 + w / 2, y, text, fp, fg, ha="center", va="center_baseline", z=z + 1)
    return w


# ───────────────────────── легенда ─────────────────────────

_LEG_FP_SIZE = 8.2


def _legend_plan(items: list[dict]) -> dict:
    fp = _f("sans", _LEG_FP_SIZE)
    sym_w = {"rail": 0.08, "arrow_out": 0.42, "arrow_in": 0.42, "icon": 0.26, "edge": 0.36}
    laid, x, line = [], MARGIN, 0
    for it in items:
        w = sym_w[it["type"]] + 0.09 + _tw(it["label"], fp)
        if (it.get("nl") or x + w > W_IN - MARGIN) and x > MARGIN:
            x, line = MARGIN, line + 1
        laid.append((it, x, line))
        x += w + 0.26
    n_lines = (max(l for _, _, l in laid) + 1) if laid else 0
    return {"laid": laid, "fp": fp, "sym_w": sym_w, "h": n_lines * 0.28 + 0.12}


def _draw_legend(ax, plan: dict, y0: float):
    for it, x, line in plan["laid"]:
        yc = y0 + 0.14 + line * 0.28
        kind = it["type"]
        if kind == "rail":
            ax.add_patch(Rectangle((x, yc - 0.1), 0.08, 0.2, fc=it["color"], ec="none", zorder=4))
        elif kind in ("arrow_out", "arrow_in"):
            a, b = ((x, yc), (x + 0.42, yc)) if kind == "arrow_out" else ((x + 0.42, yc), (x, yc))
            ax.add_patch(FancyArrowPatch(a, b, arrowstyle=ArrowStyle("-|>", head_length=0.55, head_width=0.3),
                                         mutation_scale=13, lw=2.2, color=it["color"], shrinkA=0, shrinkB=0, zorder=4))
        elif kind == "icon":
            _icon(ax, it["form"], x, yc - 0.12, 0.24)
        elif kind == "edge":
            ax.plot([x, x + 0.36], [yc, yc], color=it["color"], lw=2.6, solid_capstyle="round", zorder=4)
        _t(ax, x + plan["sym_w"][kind] + 0.09, yc + 0.045, it["label"], plan["fp"], INK)


def _plan_note(text: str) -> dict:
    fp = _f("sans", 7.6)
    lines = _wrap(text, fp, W_IN - 2 * MARGIN, 3)
    return {"fp": fp, "lines": lines, "h": 0.12 + 0.16 * len(lines) + 0.14}


def _draw_note(ax, note: dict, y0: float):
    for i, ln in enumerate(note["lines"]):
        _t(ax, MARGIN, y0 + 0.2 + 0.16 * i, ln, note["fp"], STEEL)


def _risk_legend(labels=("низкий риск", "средний риск", "высокий риск")) -> list[dict]:
    return [{"type": "rail", "color": LEVEL_SOLID[lv], "label": lb} for lv, lb in zip(("low", "medium", "high"), labels)]


def _form_legend() -> list[dict]:
    return [{"type": "icon", "form": "ip", "label": "ИП"}, {"type": "icon", "form": "ooo", "label": "ООО"},
            {"type": "icon", "form": "ao", "label": "АО"}]


# ───────────────────────── общий «журнал»: хаб слева, строки справа ─────────────────────────

_HUB_X, _HUB_W = MARGIN, 1.32
_TRUNK_X = 1.80
_TRUNK_COLOR = "#9AA7BA"


def _hub_plan(name: str, stats: list[tuple[str, str]]) -> dict:
    text = nm(name)
    inner = _HUB_W - 0.26
    size = 11.0
    longest = max((w for w in text.replace("\u00a0", " ").split(" ")), key=len, default="")
    while size > 8.5 and _tw(longest, _f("serif_b", size)) > inner:
        size -= 0.5
    nfp = _f("serif_b", size)
    lines = _wrap(text, nfp, inner, 4)
    h = 0.32 + 0.24 * len(lines) + (0.10 + 0.46 * len(stats) if stats else 0) + 0.16
    return {"lines": lines, "nfp": nfp, "stats": stats, "h": max(h, 1.3)}


def _draw_hub(ax, plan: dict, cy: float):
    h = plan["h"]
    y = cy - h / 2
    ax.add_patch(FancyBboxPatch((_HUB_X, y), _HUB_W, h, boxstyle="round,pad=0,rounding_size=0.08", fc=NAVY, ec="none", zorder=3))
    yy = y + 0.36
    for ln in plan["lines"]:
        _t(ax, _HUB_X + 0.14, yy, ln, plan["nfp"], "white")
        yy += 0.24
    if plan["stats"]:
        ax.plot([_HUB_X + 0.14, _HUB_X + _HUB_W - 0.14], [yy - 0.06, yy - 0.06], color="#41537A", lw=0.9, zorder=4)
        yy += 0.16
        for label, value in plan["stats"]:
            _t(ax, _HUB_X + 0.14, yy, label, _f("sans", 7.8), "#AFC0DD")
            _t(ax, _HUB_X + 0.14, yy + 0.2, value, _f("sans_b", 9.4), "white")
            yy += 0.46
    return (_HUB_X + _HUB_W, cy)


def _draw_trunk(ax, hub_edge_x: float, area_cy: float, first_cy: float, last_cy: float):
    ax.plot([hub_edge_x, _TRUNK_X], [area_cy, area_cy], color=_TRUNK_COLOR, lw=1.6, zorder=2, solid_capstyle="round")
    ax.plot([_TRUNK_X, _TRUNK_X], [min(first_cy, area_cy), max(last_cy, area_cy)], color=_TRUNK_COLOR, lw=1.6, zorder=2)
    ax.add_patch(Circle((_TRUNK_X, area_cy), 0.05, fc=NAVY, ec="none", zorder=4))


# ───────────────────────── 1. схема платежей ─────────────────────────

@dataclass
class FlowRow:
    name: str
    inn: str
    amount: float
    level: str                       # "low" | "medium" | "high"
    purchase: float = 0.0            # сколько мы заплатили контрагенту (книга покупок)
    sale: float = 0.0                # сколько нам заплатил контрагент (книга продаж)
    transit: bool = False            # найдены транзитные операции
    score: int | None = None         # риск-балл 0-100
    reasons: list[str] = field(default_factory=list)   # за что дан риск (названия флагов)


# короткие формулировки причин — чтобы влезали в карточку целиком
_SHORT_REASON = {
    "Транзитная операция": None,                 # показываем отдельной меткой «транзит»
    "Резкий скачок суммы": "скачок суммы",
    "Суммы у порога контроля": "суммы у порога",
    "Круглые суммы": "круглые суммы",
    "Много сторно-операций": "много сторно",
    "Оборот только в одном направлении": "односторонний оборот",
}


def _short_reasons(reasons: list[str]) -> list[str]:
    out = []
    for r in reasons:
        if r in _SHORT_REASON:
            if _SHORT_REASON[r]:
                out.append(_SHORT_REASON[r])
        elif r:
            out.append(r[:1].lower() + r[1:])
    return out


_PITCH, _CARD_H = 1.30, 1.16


def render_money_flow(company_name: str, rows: list[FlowRow]) -> bytes:
    rows = list(rows)[:8]
    n = len(rows)
    p_tot = sum(r.purchase for r in rows)
    s_tot = sum(r.sale for r in rows)
    turnover = sum((r.purchase + r.sale) or r.amount for r in rows)

    subtitle = (f"{n} {_plural(n, 'контрагент', 'контрагента', 'контрагентов')} с наибольшим риском по книге покупок и продаж. "
                f"Оборот с ними: {_money(turnover)}.")
    hd = _plan_header("Схема платежей", subtitle)

    stats = [("Мы платим", _money(p_tot)), ("Нам платят", _money(s_tot))] if (p_tot or s_tot) else []
    hub = _hub_plan(company_name or "Наша организация", stats)
    rows_h = max(n * _PITCH, hub["h"] + 0.2)
    legend = _legend_plan(
        _risk_legend()
        + [{"type": "arrow_out", "color": OUTFLOW, "label": "мы платим", "nl": True},
           {"type": "arrow_in", "color": INFLOW, "label": "нам платят"}]
        + _form_legend()
    )
    note = _plan_note("Толщина стрелки пропорциональна сумме. Оценка автоматическая, по данным книги, не является юридическим заключением.")
    total_h = hd["h"] + 0.1 + rows_h + 0.12 + legend["h"] + note["h"]

    card_x = 3.06
    card_w = W_IN - MARGIN - card_x
    fig, ax = _canvas(total_h)
    _draw_header(ax, hd)

    rows_top = hd["h"] + 0.1 + (rows_h - n * _PITCH) / 2
    area_cy = hd["h"] + 0.1 + rows_h / 2
    hub_edge = _draw_hub(ax, hub, area_cy)
    if n:
        _draw_trunk(ax, hub_edge[0], area_cy, rows_top + _PITCH / 2, rows_top + (n - 0.5) * _PITCH)

    max_flow = max([r.purchase for r in rows] + [r.sale for r in rows] + [r.amount for r in rows] + [1.0])
    lab_fp = _f("sans_b", 7.7)
    mid_x = (_TRUNK_X + card_x) / 2

    for i, r in enumerate(rows):
        cy = rows_top + (i + 0.5) * _PITCH
        y = cy - _CARD_H / 2
        col = LEVEL_SOLID.get(r.level, LEVEL_SOLID["none"])

        flows = []
        if r.purchase:
            flows.append(("покупка", r.purchase, OUTFLOW, "out"))
        if r.sale:
            flows.append(("продажа", r.sale, INFLOW, "in"))
        if not flows:
            flows.append(("оборот", r.amount, "#5B6675", "out"))
        ys = [cy] if len(flows) == 1 else [cy - 0.24, cy + 0.24]
        for (word, amt, color, direction), yy in zip(flows, ys):
            lw = 1.1 + 2.8 * math.sqrt(max(amt, 0) / max_flow)
            a, b = ((_TRUNK_X, yy), (card_x - 0.02, yy)) if direction == "out" else ((card_x - 0.02, yy), (_TRUNK_X, yy))
            ax.add_patch(FancyArrowPatch(a, b, arrowstyle=ArrowStyle("-|>", head_length=0.5, head_width=0.28),
                                         mutation_scale=5 + 2.4 * lw, lw=lw, color=color, shrinkA=0, shrinkB=0, zorder=3))
            ax.add_patch(Circle((_TRUNK_X, yy), 0.04, fc=color, ec="none", zorder=4))
            # подпись всегда выше наконечника, даже у самой толстой стрелки
            _t(ax, mid_x, yy - (0.085 + 0.018 * lw), f"{word} {_money(amt)}", lab_fp, color, ha="center")

        _card_frame(ax, card_x, y, card_w, _CARD_H, col)
        _icon(ax, org_form(r.name, r.inn), card_x + 0.21, y + 0.15, 0.34)

        tx = card_x + 0.66
        tw = card_w - 0.66 - 0.72
        name_fp = _fit(nm(r.name), "serif_b", 10.2, 8.6, tw)
        by = y + 0.31
        name_lines = _wrap(nm(r.name), name_fp, tw, 2)
        for ln in name_lines:
            _t(ax, tx, by, ln, name_fp, INK)
            by += 0.19
        by += 0.02
        inn_fp = _f("sans", 8.2)
        _t(ax, tx, by, f"ИНН {r.inn}", inn_fp, STEEL)
        if r.transit:
            _pill(ax, tx + _tw(f"ИНН {r.inn}", inn_fp) + 0.12, by - 0.045, "транзит", LEVEL_FG["medium"],
                  LEVEL_BG["medium"], _f("sans_b", 7.4), h=0.19, pad=0.06)
        by += 0.2
        reasons = _short_reasons(r.reasons)
        text = ("Признаки: " + ", ".join(reasons)) if reasons else ("" if r.transit else "Явных признаков по книге нет")
        max_reason_lines = 3 if len(name_lines) == 1 else 2       # короткое название — больше места под причины
        for ln in (_wrap(text, _f("sans", 7.9), tw, max_reason_lines) if text else []):
            _t(ax, tx, by, ln, _f("sans", 7.9), INK if reasons else STEEL)
            by += 0.165

        rx = card_x + card_w - 0.16
        if r.score is not None:
            _t(ax, rx, y + 0.52, str(r.score), _f("serif_b", 21), col, ha="right")
            _t(ax, rx, y + 0.74, LEVEL_WORD.get(r.level, ""), _f("sans_b", 8), col, ha="right")
        else:
            _t(ax, rx, y + 0.60, LEVEL_WORD.get(r.level, ""), _f("sans_b", 9), col, ha="right")

    ly = hd["h"] + 0.1 + rows_h + 0.12
    _draw_legend(ax, legend, ly)
    _draw_note(ax, note, ly + legend["h"])
    return _png(fig)


# ───────────────────────── 2. схема связей организации ─────────────────────────

@dataclass
class RelationRow:
    name: str
    inn: str
    active: bool
    label: str = ""     # чем связана («общий учредитель; общий руководитель»)
    status: str = ""    # статус словами («Действует», «Ликвидировано»)


_REL_PITCH, _REL_CARD_H = 1.08, 0.92


def render_relations(company_name: str, rows: list[RelationRow], total: int | None = None) -> bytes:
    rows = list(rows)[:8]
    n = len(rows)
    inactive = sum(1 for r in rows if not r.active)
    subtitle = f"Организаций, связанных с проверяемой: {total if total is not None else n}"
    if total and total > n:
        subtitle += f" (показаны {n})"
    subtitle += f". Не действуют или ликвидированы: {inactive}." if inactive else ". Все показанные — действующие."
    hd = _plan_header("Связи организации", subtitle)

    hub = _hub_plan(company_name, [])
    rows_h = max(n * _REL_PITCH, hub["h"] + 0.2)
    legend = _legend_plan(
        [{"type": "rail", "color": LEVEL_SOLID["low"], "label": "действует"},
         {"type": "rail", "color": LEVEL_SOLID["high"], "label": "не действует или ликвидирована"}]
        + [dict(it, nl=(k == 0)) for k, it in enumerate(_form_legend())]
    )
    note = _plan_note("Связи по данным ЕГРЮЛ и Checko. Оценка автоматическая, не является юридическим заключением.")
    total_h = hd["h"] + 0.1 + rows_h + 0.12 + legend["h"] + note["h"]

    card_x = 3.42                                # шире зона линий: подписи «чем связаны» помещаются целиком
    card_w = W_IN - MARGIN - card_x
    fig, ax = _canvas(total_h)
    _draw_header(ax, hd)
    rows_top = hd["h"] + 0.1 + (rows_h - n * _REL_PITCH) / 2
    area_cy = hd["h"] + 0.1 + rows_h / 2
    hub_edge = _draw_hub(ax, hub, area_cy)
    if n:
        _draw_trunk(ax, hub_edge[0], area_cy, rows_top + _REL_PITCH / 2, rows_top + (n - 0.5) * _REL_PITCH)

    kind_fp = _f("sans_b", 7.8)
    zone_w = card_x - _TRUNK_X - 0.2
    for i, r in enumerate(rows):
        cy = rows_top + (i + 0.5) * _REL_PITCH
        y = cy - _REL_CARD_H / 2
        col = LEVEL_SOLID["low"] if r.active else LEVEL_SOLID["high"]
        ax.plot([_TRUNK_X, card_x], [cy, cy], color="#7A8799", lw=1.8, zorder=2)
        ax.add_patch(Circle((_TRUNK_X, cy), 0.04, fc="#7A8799", ec="none", zorder=4))

        # подпись «чем связаны» — одной рамкой, каждая связь с новой строки, без обрезки
        kinds = [s.strip() for s in (r.label or "связь").split(";") if s.strip()]
        shown, hidden = kinds[:2], max(0, len(kinds) - 2)
        lines: list[str] = []
        for k in shown:
            lines += _wrap(k, kind_fp, zone_w - 0.16, 2)
        if hidden:
            lines.append(f"и ещё {hidden}")
        w = min(zone_w, max(_tw(s, kind_fp) for s in lines) + 0.16)
        h = 0.16 * len(lines) + 0.09
        bx = (_TRUNK_X + card_x) / 2 + 0.02
        ax.add_patch(FancyBboxPatch((bx - w / 2, cy - h / 2), w, h, boxstyle="round,pad=0,rounding_size=0.06",
                                    fc="white", ec="#B5BFCC", lw=0.9, zorder=5))
        for q, s in enumerate(lines):
            _t(ax, bx, cy - h / 2 + 0.045 + 0.16 * (q + 0.8), s, kind_fp, INK, ha="center")

        _card_frame(ax, card_x, y, card_w, _REL_CARD_H, col)
        _icon(ax, org_form(r.name, r.inn), card_x + 0.2, y + 0.14, 0.32)
        tx, tw = card_x + 0.62, card_w - 0.62 - 0.14
        name_fp = _fit(nm(r.name), "serif_b", 10, 8.4, tw)
        by = y + 0.31
        for ln in _wrap(nm(r.name), name_fp, tw, 2):
            _t(ax, tx, by, ln, name_fp, INK)
            by += 0.19
        by += 0.03
        _t(ax, tx, by, f"ИНН {r.inn}", _f("sans", 8.2), STEEL)
        status = (r.status or ("Действует" if r.active else "Не действует")).lower()
        _pill(ax, card_x + card_w - 0.14, by - 0.045, status, LEVEL_FG["low" if r.active else "high"],
              LEVEL_BG["low" if r.active else "high"], _f("sans_b", 7.6), ha="right", h=0.2)

    ly = hd["h"] + 0.1 + rows_h + 0.12
    _draw_legend(ax, legend, ly)
    _draw_note(ax, note, ly + legend["h"])
    return _png(fig)


# ───────────────────────── 3. граф связей между организациями ─────────────────────────

def edge_category(kind: str) -> str:
    k = (kind or "").lower()
    if "госконтракт" in k:
        return "contract"
    if "адрес" in k:
        return "address"
    if "телефон" in k or "e-mail" in k:
        return "contact"
    if any(w in k for w in ("организация-учредитель", "упр. организац", "управляющая", "checko", "предшественник")):
        return "struct"
    if any(w in k for w in ("директор", "учредител", "руковод", "физлиц", "человек", "общее лицо", "ип↔", "ип/", "ип ")):
        return "people"
    return "struct"


def _order_nodes(inns: list[str], adj: dict[str, dict[str, int]]) -> list[str]:
    """Расставляем по кругу так, чтобы связанные организации стояли рядом — меньше пересечений."""
    deg = {i: sum(adj.get(i, {}).values()) for i in inns}
    left = sorted(inns, key=lambda i: -deg[i])
    if not left:
        return []
    order = [left.pop(0)]
    while left:
        last = order[-1]
        nxt = max(left, key=lambda i: (adj.get(last, {}).get(i, 0), deg[i]))
        left.remove(nxt)
        order.append(nxt)
    return order


def _bezier_mid(p0, c, p2):
    return (0.25 * p0[0] + 0.5 * c[0] + 0.25 * p2[0], 0.25 * p0[1] + 0.5 * c[1] + 0.25 * p2[1])


def _ring_positions(order: list[str], cx: float, cy: float, rx: float, ry: float) -> dict[str, tuple[float, float]]:
    n = len(order)
    if n == 1:
        return {order[0]: (cx, cy)}
    return {i: (cx + rx * math.cos(-math.pi / 2 + 2 * math.pi * k / n), cy + ry * math.sin(-math.pi / 2 + 2 * math.pi * k / n))
            for k, i in enumerate(order)}


def _overlaps(pos, card_w, card_h, margin) -> bool:
    pts = list(pos.values())
    for a in range(len(pts)):
        for b in range(a + 1, len(pts)):
            if abs(pts[a][0] - pts[b][0]) < card_w + margin and abs(pts[a][1] - pts[b][1]) < card_h + margin:
                return True
    return False


def render_connections_graph(nodes: list[dict], edges: list[tuple], title: str = "") -> bytes:
    """nodes: [{"inn", "name", "level"(необяз.)}]; edges: [(inn_a, inn_b, "общий директор"[, "подробности"]), ...].
    На схеме — организации кольцом и пронумерованные линии цвета типа связи; под схемой — список
    «что именно связывает» (человек, адрес, телефон, номер контракта)."""
    nodes = list(nodes)[:12]
    ids = [nd["inn"] for nd in nodes]
    by_id = {nd["inn"]: nd for nd in nodes}
    valid = []
    for e in edges:
        a, b, kind = e[0], e[1], e[2]
        detail = e[3] if len(e) > 3 else ""
        if a in by_id and b in by_id and a != b:
            valid.append((a, b, kind, detail))
    valid = valid[:24]

    adj: dict[str, dict[str, int]] = {}
    for a, b, *_ in valid:
        adj.setdefault(a, {})[b] = adj.setdefault(a, {}).get(b, 0) + 1
        adj.setdefault(b, {})[a] = adj.setdefault(b, {}).get(a, 0) + 1
    degree = {i: sum(adj.get(i, {}).values()) for i in ids}
    isolated = sum(1 for i in ids if degree[i] == 0)

    n = len(nodes)
    subtitle = f"Найдено связей: {len(valid)}, организаций в проверке: {n}."
    if isolated:
        subtitle += f" Без связей: {isolated}."
    subtitle += " Число в кружке у организации — сколько у неё связей."
    hd = _plan_header(title or "Связи между организациями", subtitle)

    card_w = 1.95 if n <= 5 else 1.7 if n <= 8 else 1.5
    card_h = 0.86 if n <= 8 else 0.78
    rx = (W_IN - card_w - 0.5) / 2
    ry = max(1.7, 0.36 * n + 0.75) if n > 2 else 1.3
    order = _order_nodes(ids, adj)
    for _ in range(14):                                     # раздвигаем по вертикали, пока карточки не перестанут задевать друг друга
        pos = _ring_positions(order, 0, 0, rx, ry)
        if not _overlaps(pos, card_w, card_h, 0.42):
            break
        ry += 0.25

    # список связей под графом
    num_fp, hd_fp, kind_fp, det_fp = _f("sans_b", 8.6), _f("serif_b", 9), _f("sans_b", 8.2), _f("sans", 7.9)
    text_x = MARGIN + 0.42
    text_w = W_IN - MARGIN - text_x
    listing = []
    for k, (a, b, kind, detail) in enumerate(valid[:12], start=1):
        pair = _wrap(f"{nm(by_id[a]['name'])} — {nm(by_id[b]['name'])}", hd_fp, text_w, 1)[0]
        body = _wrap(typo(detail), det_fp, text_w, 2) if detail else []
        listing.append((k, a, b, kind, pair, body, 0.24 + 0.165 * (1 + len(body)) + 0.1))
    list_h = (0.34 + sum(x[6] for x in listing) + (0.2 if len(valid) > 12 else 0)) if listing else 0

    cats_present = sorted({edge_category(k) for _, _, k, _ in valid}, key=list(EDGE_KINDS).index)
    legend_items = [{"type": "edge", "color": EDGE_KINDS[c][1], "label": EDGE_KINDS[c][0]} for c in cats_present]
    if any(nd.get("level") for nd in nodes):
        legend_items += [dict(it, nl=(k == 0)) for k, it in enumerate(_risk_legend())]
    legend_items += [dict(it, nl=(k == 0)) for k, it in enumerate(_form_legend())]
    legend = _legend_plan(legend_items)
    note = _plan_note("Связи по открытым данным ЕГРЮЛ, Checko и ЕИС. Оценка автоматическая, не является юридическим заключением.")

    graph_h = 2 * ry + card_h + 0.5
    total_h = hd["h"] + 0.1 + graph_h + legend["h"] + 0.1 + list_h + note["h"]
    fig, ax = _canvas(total_h)
    _draw_header(ax, hd)

    cx, cy = W_IN / 2, hd["h"] + 0.1 + graph_h / 2
    pos = {i: (cx + p[0], cy + p[1]) for i, p in _ring_positions(order, 0, 0, rx, ry).items()}

    groups: dict[tuple[str, str], list[int]] = {}
    for idx, (a, b, *_rest) in enumerate(valid):
        groups.setdefault(tuple(sorted((a, b))), []).append(idx)
    markers = []
    for (a, b), idxs in groups.items():
        p0, p2 = pos[a], pos[b]
        mx, my = (p0[0] + p2[0]) / 2, (p0[1] + p2[1]) / 2
        dx, dy = p2[0] - p0[0], p2[1] - p0[1]
        ln = math.hypot(dx, dy) or 1.0
        nx, ny = -dy / ln, dx / ln
        m = len(idxs)
        for j, idx in enumerate(idxs):
            off = (j - (m - 1) / 2) * 0.46
            ctrl = (mx + (cx - mx) * 0.22 + nx * off, my + (cy - my) * 0.22 + ny * off)
            color = EDGE_KINDS[edge_category(valid[idx][2])][1]
            ax.add_patch(PathPatch(Path([p0, ctrl, p2], [Path.MOVETO, Path.CURVE3, Path.CURVE3]),
                                   fc="none", ec=color, lw=2.4, capstyle="round", zorder=2))
            t = 0.5 if m <= 3 else (0.38 if j % 2 == 0 else 0.62)
            bx_ = (1 - t) ** 2 * p0[0] + 2 * (1 - t) * t * ctrl[0] + t ** 2 * p2[0]
            by_ = (1 - t) ** 2 * p0[1] + 2 * (1 - t) * t * ctrl[1] + t ** 2 * p2[1]
            markers.append(((bx_, by_), idx + 1, color))
    for (mx_, my_), num, color in markers:
        ax.add_patch(Circle((mx_, my_), 0.135, fc=color, ec="white", lw=1.6, zorder=6))
        _t(ax, mx_, my_, str(num), num_fp, "white", ha="center", va="center_baseline", z=7)

    for i in ids:
        nd = by_id[i]
        x, y = pos[i][0] - card_w / 2, pos[i][1] - card_h / 2
        lvl = nd.get("level")
        rail = LEVEL_SOLID[lvl] if lvl in LEVEL_SOLID else "#9AA7BA"
        _card_frame(ax, x, y, card_w, card_h, rail)
        icon_s = 0.30
        _icon(ax, org_form(nd["name"], i), x + 0.17, y + 0.13, icon_s)
        tx = x + 0.17 + icon_s + 0.09
        tw = card_w - (tx - x) - 0.1
        nfp = _fit(nm(nd["name"]), "serif_b", 8.9 if n <= 8 else 8.2, 7.4, tw)
        by = y + 0.28
        for ln in _wrap(nm(nd["name"]), nfp, tw, 3 if n <= 5 else 2):
            _t(ax, tx, by, ln, nfp, INK)
            by += 0.165
        _t(ax, x + 0.17, y + card_h - 0.09, f"ИНН {i}", _f("sans", 7.4), STEEL)
        if degree[i]:
            bx, by_ = x + card_w - 0.02, y + 0.02
            ax.add_patch(Circle((bx, by_), 0.135, fc=NAVY, ec="white", lw=1.6, zorder=8))
            _t(ax, bx, by_, str(degree[i]), _f("sans_b", 8.6), "white", ha="center", va="center_baseline", z=9)

    ly0 = hd["h"] + 0.1 + graph_h
    _draw_legend(ax, legend, ly0)

    y = ly0 + legend["h"] + 0.1
    if listing:
        ax.plot([MARGIN, W_IN - MARGIN], [y + 0.02, y + 0.02], color=HAIRLINE, lw=1.0, zorder=2)
        _t(ax, MARGIN, y + 0.24, "Что именно связывает организации", _f("serif_b", 11.5), NAVY)
        y += 0.34
        for k, a, b, kind, pair, body, rh in listing:
            color = EDGE_KINDS[edge_category(kind)][1]
            ax.add_patch(Circle((MARGIN + 0.135, y + 0.15), 0.135, fc=color, ec="none", zorder=4))
            _t(ax, MARGIN + 0.135, y + 0.15, str(k), num_fp, "white", ha="center", va="center_baseline", z=7)
            _t(ax, text_x, y + 0.13, pair, hd_fp, INK)
            _t(ax, text_x, y + 0.30, kind[:1].upper() + kind[1:], kind_fp, color)
            for q, s in enumerate(body):
                _t(ax, text_x, y + 0.465 + 0.165 * q, s, det_fp, STEEL)
            y += rh
        if len(valid) > 12:
            _t(ax, text_x, y + 0.1, f"…и ещё связей: {len(valid) - 12}. Полный перечень — в сообщении бота.", det_fp, STEEL)
    _draw_note(ax, note, total_h - note["h"])
    return _png(fig)
