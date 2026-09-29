"""
PDF-досье: карточка контрагента (по структурной модели из pdf_model.py) и текстовые отчёты.

ВАЖНО: это НЕ официальная выписка из ЕГРЮЛ с ЭЦП ФНС — собственное досье бота
на основе открытых данных DaData/Checko.

Публичный API:
  render_card_pdf(model)            — досье одной организации/ИП (красивое, из модели)
  render_multi_card_pdf(models)     — сводное досье: страница-сводка + по странице(ам) на компанию
  build_dossier_pdf(title, subtitle, body_text)       — текстовый отчёт (например, по книге покупок/продаж)
  build_multi_dossier_pdf(sections)                   — то же для нескольких текстов (старый формат кэша)
"""

from __future__ import annotations
import io
import os
import re
from xml.sax.saxutils import escape

import matplotlib
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas as rl_canvas
from reportlab.platypus import (
    BaseDocTemplate, CondPageBreak, Frame, PageBreak, PageTemplate, Paragraph, Spacer, Table, TableStyle,
)

from links import inn_url

# ───────────────────────── палитра ─────────────────────────

from theme import (
    NAVY, ACCENT, INK, MUTED, LINE, ZEBRA, CARD, LINK, LEVEL_FG, LEVEL_BG, LEVEL_SOLID,
)

LEVEL_ORDER = {"none": -1, "low": 0, "medium": 1, "high": 2}

PAGE_W, PAGE_H = A4
MARGIN_X = 14 * mm
FRAME_W = PAGE_W - 2 * MARGIN_X

FOOTER_TEXT = (
    "Это НЕ официальная выписка из ЕГРЮЛ и не документ с электронной подписью ФНС — собственное досье бота "
    "на основе открытых данных DaData/Checko. Оценки формируются автоматически, не являются юридическим "
    "заключением и не устанавливают фактов."
)

# ───────────────────────── шрифты и стили ─────────────────────────

_FONTS_READY = False
_STYLES: dict[str, ParagraphStyle] | None = None


def _ensure_fonts():
    global _FONTS_READY
    if _FONTS_READY:
        return
    base = os.path.join(os.path.dirname(matplotlib.__file__), "mpl-data", "fonts", "ttf")
    pdfmetrics.registerFont(TTFont("DejaVuSans", os.path.join(base, "DejaVuSans.ttf")))
    pdfmetrics.registerFont(TTFont("DejaVuSans-Bold", os.path.join(base, "DejaVuSans-Bold.ttf")))
    pdfmetrics.registerFont(TTFont("DejaVuSansMono", os.path.join(base, "DejaVuSansMono.ttf")))
    pdfmetrics.registerFontFamily(
        "DejaVuSans", normal="DejaVuSans", bold="DejaVuSans-Bold", italic="DejaVuSans", boldItalic="DejaVuSans-Bold",
    )
    _FONTS_READY = True


def _S() -> dict[str, ParagraphStyle]:
    global _STYLES
    if _STYLES is None:
        def ps(name, **kw):
            base = dict(fontName="DejaVuSans", fontSize=8.6, leading=11.6, textColor=colors.HexColor(INK), alignment=TA_LEFT)
            base.update(kw)
            return ParagraphStyle(name, **base)

        _STYLES = {
            "kicker": ps("kicker", fontSize=7.2, leading=9, textColor=colors.HexColor("#9FB4D6")),
            "b_title": ps("b_title", fontName="DejaVuSans-Bold", fontSize=17, leading=21, textColor=colors.white),
            "b_sub": ps("b_sub", fontSize=8.6, leading=11, textColor=colors.HexColor("#C9D4E6")),
            "b_stat": ps("b_stat", fontSize=7.2, leading=9, textColor=colors.HexColor("#9FB4D6"), alignment=TA_RIGHT),
            "b_chip": ps("b_chip", fontName="DejaVuSans-Bold", fontSize=9, leading=12, textColor=colors.white, alignment=TA_RIGHT),
            "v_kicker": ps("v_kicker", fontSize=7, leading=9, textColor=colors.HexColor("#FFFFFF")),
            "v_head": ps("v_head", fontName="DejaVuSans-Bold", fontSize=13.5, leading=16.5, textColor=colors.white),
            "v_note": ps("v_note", fontSize=7.4, leading=9.6, textColor=colors.HexColor("#FFFFFF")),
            "k_label": ps("k_label", fontSize=7, leading=9, textColor=colors.HexColor(MUTED)),
            "k_value": ps("k_value", fontName="DejaVuSans-Bold", fontSize=12.5, leading=15.5),
            "k_sub": ps("k_sub", fontSize=7.4, leading=9.4, textColor=colors.HexColor(MUTED)),
            "h2": ps("h2", fontName="DejaVuSans-Bold", fontSize=10.2, leading=13, textColor=colors.HexColor(NAVY)),
            "th_l": ps("th_l", fontName="DejaVuSans-Bold", fontSize=7.6, leading=9.6, textColor=colors.HexColor(NAVY)),
            "th_r": ps("th_r", fontName="DejaVuSans-Bold", fontSize=7.6, leading=9.6, textColor=colors.HexColor(NAVY), alignment=TA_RIGHT),
            "td_l": ps("td_l"),
            "td_r": ps("td_r", alignment=TA_RIGHT),
            "kv_k": ps("kv_k", fontSize=8.2, leading=11, textColor=colors.HexColor(MUTED)),
            "kv_v": ps("kv_v"),
            "flag": ps("flag"),
            "flag_b": ps("flag_b", fontName="DejaVuSans-Bold", fontSize=9, leading=12),
            "flag_i": ps("flag_i", fontSize=8.2, leading=11, textColor=colors.HexColor("#3B4655"), leftIndent=6),
            "small": ps("small", fontSize=7.6, leading=10, textColor=colors.HexColor(MUTED)),
            "body": ps("body", fontSize=9.2, leading=13, spaceAfter=5),
            "disc": ps("disc", fontSize=7.4, leading=9.8, textColor=colors.HexColor(MUTED)),
            "foot": ps("foot", fontSize=7, leading=9, textColor=colors.HexColor(MUTED)),
        }
    return _STYLES


# ───────────────────────── разметка ячеек ─────────────────────────

def _esc(s) -> str:
    return escape(str(s)).replace("\n", "<br/>")


def _chip(text: str, level: str) -> str:
    fg, bg = LEVEL_FG.get(level, LEVEL_FG["none"]), LEVEL_BG.get(level, LEVEL_BG["none"])
    if level == "none":
        return _esc(text)
    return f'<font color="{fg}" backColor="{bg}">&nbsp;{_esc(text)}&nbsp;</font>'


def _cell_markup(c) -> str:
    if c is None or c == "":
        return "—"
    if isinstance(c, str):
        return _esc(c)
    if isinstance(c, list):
        return " ".join(_cell_markup(x) for x in c)
    if isinstance(c, dict):
        if "inn" in c:
            inn = c["inn"]
            if not inn:
                return "—"
            url = inn_url(inn)
            return f'<a href="{url}" color="{LINK}">{_esc(inn)}</a>' if url else _esc(inn)
        if "link" in c:
            return f'<a href="{escape(c["link"], {chr(34): "&quot;"})}" color="{LINK}">{_esc(c.get("t", "ссылка"))}</a>'
        if "t" in c:
            return _chip(c["t"], c.get("level", "none"))
    return _esc(c)


# ───────────────────────── блоки ─────────────────────────

def _banner(title: str, subtitle: str, kicker: str, status: dict | None = None) -> Table:
    s = _S()
    left = [Paragraph(_esc(kicker), s["kicker"]), Spacer(1, 2), Paragraph(_esc(title), s["b_title"]),
            Spacer(1, 2), Paragraph(_esc(subtitle), s["b_sub"])]
    if status:
        lv = status.get("level", "none")
        right = [Paragraph("СТАТУС", s["b_stat"]), Spacer(1, 3),
                 Paragraph(f'<font backColor="{LEVEL_SOLID.get(lv, LEVEL_SOLID["none"])}">&nbsp;{_esc(status["text"])}&nbsp;</font>', s["b_chip"])]
        t = Table([[left, right]], colWidths=[FRAME_W * 0.66, FRAME_W * 0.34])
    else:
        t = Table([[left]], colWidths=[FRAME_W])
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor(NAVY)),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 12), ("RIGHTPADDING", (0, 0), (-1, -1), 12),
        ("TOPPADDING", (0, 0), (-1, -1), 11), ("BOTTOMPADDING", (0, 0), (-1, -1), 11),
        ("LINEBELOW", (0, 0), (-1, -1), 3, colors.HexColor(ACCENT)),
    ]))
    return t


def _verdict_block(verdict: dict, alerts: list[dict]) -> list:
    s = _S()
    lv = verdict.get("level", "none")
    left = [Paragraph("ИТОГОВАЯ ОЦЕНКА", s["v_kicker"]), Spacer(1, 4),
            Paragraph(_esc(verdict.get("headline", "")), s["v_head"]), Spacer(1, 4),
            Paragraph("Сводит официальную оценку Checko и собственные проверки бота", s["v_note"])]

    items = verdict.get("items") or []
    if items:
        inner_w = FRAME_W * 0.68 - 16
        rows = [[Paragraph(_esc(i["label"]), s["kv_k"]), Paragraph(_chip(i["value"], i.get("level", "none")), s["kv_v"])] for i in items]
        right = Table(rows, colWidths=[inner_w * 0.47, inner_w * 0.53])
        right.setStyle(TableStyle([
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("TOPPADDING", (0, 0), (-1, -1), 2.5), ("BOTTOMPADDING", (0, 0), (-1, -1), 2.5),
            ("LEFTPADDING", (0, 0), (-1, -1), 0), ("RIGHTPADDING", (0, 0), (-1, -1), 0),
        ]))
    else:
        right = Paragraph("Недостаточно данных для оценки: источники не вернули сведений.", s["kv_v"])

    outer = Table([[left, right]], colWidths=[FRAME_W * 0.32, FRAME_W * 0.68])
    outer.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (0, 0), colors.HexColor(LEVEL_SOLID.get(lv, LEVEL_SOLID["none"]))),
        ("BACKGROUND", (1, 0), (1, 0), colors.HexColor(CARD)),
        ("VALIGN", (0, 0), (0, 0), "MIDDLE"), ("VALIGN", (1, 0), (1, 0), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (0, 0), 11), ("RIGHTPADDING", (0, 0), (0, 0), 9),
        ("LEFTPADDING", (1, 0), (1, 0), 10), ("RIGHTPADDING", (1, 0), (1, 0), 8),
        ("TOPPADDING", (0, 0), (-1, -1), 9), ("BOTTOMPADDING", (0, 0), (-1, -1), 9),
    ]))
    out: list = [outer]

    if alerts:
        rows = []
        for a in alerts[:8]:
            fg = LEVEL_FG.get(a["level"], LEVEL_FG["none"])
            rows.append([Paragraph(f'<font color="{fg}">●</font>', s["flag"]), Paragraph(_esc(a["text"]), s["flag"])])
        t = Table(rows, colWidths=[7 * mm, FRAME_W - 7 * mm])
        worst = max((a["level"] for a in alerts), key=lambda x: LEVEL_ORDER.get(x, 0))
        t.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor(LEVEL_BG.get(worst, LEVEL_BG["none"]))),
            ("LINEBEFORE", (0, 0), (0, -1), 3, colors.HexColor(LEVEL_SOLID.get(worst, LEVEL_SOLID["none"]))),
            ("LEFTPADDING", (0, 0), (0, -1), 8), ("RIGHTPADDING", (0, 0), (0, -1), 0),
            ("TOPPADDING", (0, 0), (-1, -1), 2.5), ("BOTTOMPADDING", (0, 0), (-1, -1), 2.5),
        ]))
        out += [Spacer(1, 3), Paragraph("<b>Обратите внимание</b>", s["small"]), Spacer(1, 2), t]
    return out


def _kpi_tiles(kpis: list[dict]) -> Table | None:
    if not kpis:
        return None
    s = _S()
    n = len(kpis)
    gap = 3 * mm
    tile_w = (FRAME_W - gap * (n - 1)) / n
    cells, widths, tile_cols = [], [], []
    for i, k in enumerate(kpis):
        fg = LEVEL_FG["high"] if k.get("level") == "high" else INK
        value_style = ParagraphStyle("kv_dyn", parent=s["k_value"], textColor=colors.HexColor(fg))
        cell = [Paragraph(_esc(k["label"]).upper(), s["k_label"]), Spacer(1, 2), Paragraph(_esc(k["value"]), value_style)]
        if k.get("sub"):
            cell += [Spacer(1, 1), Paragraph(_esc(k["sub"]), s["k_sub"])]
        tile_cols.append(len(cells))
        cells.append(cell)
        widths.append(tile_w)
        if i < n - 1:
            cells.append("")
            widths.append(gap)
    t = Table([cells], colWidths=widths)
    style = [("VALIGN", (0, 0), (-1, -1), "TOP")]
    for c in tile_cols:
        style += [
            ("BACKGROUND", (c, 0), (c, 0), colors.HexColor(CARD)),
            ("LINEABOVE", (c, 0), (c, 0), 2, colors.HexColor(ACCENT)),
            ("LEFTPADDING", (c, 0), (c, 0), 8), ("RIGHTPADDING", (c, 0), (c, 0), 6),
            ("TOPPADDING", (c, 0), (c, 0), 7), ("BOTTOMPADDING", (c, 0), (c, 0), 8),
        ]
    for c in range(1, len(widths), 2):
        style += [("LEFTPADDING", (c, 0), (c, 0), 0), ("RIGHTPADDING", (c, 0), (c, 0), 0)]
    t.setStyle(TableStyle(style))
    return t


def _section_header(title: str) -> Table:
    t = Table([[Paragraph(_esc(title), _S()["h2"])]], colWidths=[FRAME_W])
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor(CARD)),
        ("LINEBEFORE", (0, 0), (0, -1), 3, colors.HexColor(ACCENT)),
        ("LEFTPADDING", (0, 0), (-1, -1), 9), ("TOPPADDING", (0, 0), (-1, -1), 5), ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ]))
    return t


def _base_table_style(header: bool) -> list:
    st = [
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 5), ("RIGHTPADDING", (0, 0), (-1, -1), 5),
        ("TOPPADDING", (0, 0), (-1, -1), 3.6), ("BOTTOMPADDING", (0, 0), (-1, -1), 3.6),
        ("LINEBELOW", (0, 0), (-1, -1), 0.4, colors.HexColor(LINE)),
    ]
    if header:
        st += [("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#E3EAF5")),
               ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor(ZEBRA)])]
    else:
        st += [("ROWBACKGROUNDS", (0, 0), (-1, -1), [colors.white, colors.HexColor(ZEBRA)])]
    return st


def _table_body(sec: dict) -> Table:
    s = _S()
    align = sec.get("align") or ["L"] * len(sec["columns"])
    widths = [FRAME_W * w for w in sec["widths"]]
    head = [Paragraph(_esc(c), s["th_r" if a == "R" else "th_l"]) for c, a in zip(sec["columns"], align)]
    body = [[Paragraph(_cell_markup(c), s["td_r" if a == "R" else "td_l"]) for c, a in zip(row, align)] for row in sec["rows"]]
    t = Table([head] + body, colWidths=widths, repeatRows=1)
    t.setStyle(TableStyle(_base_table_style(True)))
    return t


def _kv_body(sec: dict) -> Table:
    s = _S()
    rows = [[Paragraph(_esc(k), s["kv_k"]), Paragraph(_cell_markup(v), s["kv_v"])] for k, v in sec["rows"]]
    t = Table(rows, colWidths=[FRAME_W * 0.27, FRAME_W * 0.73])
    t.setStyle(TableStyle(_base_table_style(False)))
    return t


def _flags_body(sec: dict) -> Table:
    s = _S()
    rows = []
    for it in sec["items"]:
        lv = it.get("level", "none")
        indent = it.get("indent")
        if indent:
            dot, style = "", s["flag_i"]
        else:
            dot = f'<font color="{LEVEL_SOLID.get(lv, LEVEL_SOLID["none"])}">●</font>' if lv != "none" else '<font color="#9AA5B4">–</font>'
            style = s["flag_b"] if it.get("bold") else s["flag"]
        rows.append([Paragraph(dot, s["flag"]), Paragraph(_esc(it["text"]), style)])
    t = Table(rows, colWidths=[7 * mm, FRAME_W - 7 * mm])
    st = _base_table_style(False)
    st.append(("LEFTPADDING", (0, 0), (0, -1), 8))
    t.setStyle(TableStyle(st))
    return t


def _section(sec: dict) -> list:
    s = _S()
    fl: list = [CondPageBreak(42 * mm), _section_header(sec["title"]), Spacer(1, 3)]
    if sec.get("summary"):
        fl += [Paragraph(_esc(sec["summary"]), s["small"]), Spacer(1, 2)]
    kind = sec["type"]
    fl.append({"table": _table_body, "kv": _kv_body, "flags": _flags_body}[kind](sec))
    if sec.get("more"):
        fl += [Spacer(1, 2), Paragraph(f"…и ещё {sec['more']} — полный список доступен в боте по кнопке «Показать все».", s["small"])]
    if sec.get("note"):
        fl += [Spacer(1, 2), Paragraph(_esc(sec["note"]), s["small"])]
    fl.append(Spacer(1, 9))
    return fl


def _disclaimer() -> Table:
    t = Table([[Paragraph(_esc(FOOTER_TEXT), _S()["disc"])]], colWidths=[FRAME_W])
    t.setStyle(TableStyle([
        ("BOX", (0, 0), (-1, -1), 0.6, colors.HexColor(LINE)), ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor(ZEBRA)),
        ("LEFTPADDING", (0, 0), (-1, -1), 8), ("RIGHTPADDING", (0, 0), (-1, -1), 8),
        ("TOPPADDING", (0, 0), (-1, -1), 6), ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
    ]))
    return t


def _company_flowables(m: dict, with_disclaimer: bool = True) -> list:
    fl: list = [_banner(m["title"], f'{m["subtitle"]}  ·  сформировано {m["generated"]}', "ДОСЬЕ ПЛЮС  ·  ДОСЬЕ КОНТРАГЕНТА", m.get("status")), Spacer(1, 8)]
    fl += _verdict_block(m["verdict"], m.get("alerts") or [])
    tiles = _kpi_tiles(m.get("kpis") or [])
    if tiles is not None:
        fl += [Spacer(1, 8), tiles]
    fl.append(Spacer(1, 10))
    for sec in m["sections"]:
        fl += _section(sec)
    if with_disclaimer:
        fl += [Spacer(1, 2), _disclaimer()]
    return fl


# ───────────────────────── документ ─────────────────────────

def _numbered_canvas(footer_left: str):
    class NumberedCanvas(rl_canvas.Canvas):
        def __init__(self, *a, **kw):
            super().__init__(*a, **kw)
            self._saved: list[dict] = []

        def showPage(self):
            self._saved.append(dict(self.__dict__))
            self._startPage()

        def save(self):
            total = len(self._saved)
            for st in self._saved:
                self.__dict__.update(st)
                self._footer(total)
                super().showPage()
            super().save()

        def _footer(self, total: int):
            self.setStrokeColor(colors.HexColor(LINE))
            self.setLineWidth(0.6)
            self.line(MARGIN_X, 13 * mm, PAGE_W - MARGIN_X, 13 * mm)
            self.setFillColor(colors.HexColor(MUTED))
            self.setFont("DejaVuSans", 7)
            self.drawString(MARGIN_X, 8.6 * mm, footer_left)
            self.drawRightString(PAGE_W - MARGIN_X, 8.6 * mm, f"Стр. {self._pageNumber} из {total}")

    return NumberedCanvas


def _build(story: list, title: str, footer_left: str) -> bytes:
    _ensure_fonts()
    buf = io.BytesIO()
    doc = BaseDocTemplate(
        buf, pagesize=A4, title=title, author="Досье Плюс",
        leftMargin=MARGIN_X, rightMargin=MARGIN_X, topMargin=13 * mm, bottomMargin=18 * mm,
    )
    frame = Frame(MARGIN_X, 18 * mm, FRAME_W, PAGE_H - 13 * mm - 18 * mm, id="main",
                  leftPadding=0, rightPadding=0, topPadding=0, bottomPadding=0)
    doc.addPageTemplates([PageTemplate(id="p", frames=[frame])])
    doc.build(story, canvasmaker=_numbered_canvas(footer_left))
    buf.seek(0)
    return buf.read()


def render_card_pdf(model: dict) -> bytes:
    _ensure_fonts()
    story = _company_flowables(model)
    return _build(story, model["title"], f'Досье Плюс  ·  {model["title"]}  ·  ИНН {model["inn"]}')


def render_multi_card_pdf(models: list[dict]) -> bytes:
    _ensure_fonts()
    s = _S()
    ordered = sorted(models, key=lambda m: -LEVEL_ORDER.get(m["verdict"]["level"], 0))
    counts = {lv: sum(1 for m in models if m["verdict"]["level"] == lv) for lv in ("high", "medium", "low", "none")}

    from datetime import date
    story: list = [
        _banner(f"Проверено организаций: {len(models)}", f'сформировано {date.today().strftime("%d.%m.%Y")}',
                "ДОСЬЕ ПЛЮС  ·  СВОДНОЕ ДОСЬЕ"),
        Spacer(1, 8),
        Paragraph("  ".join([
            _chip("высокий риск: " + str(counts["high"]), "high"),
            _chip("требует внимания: " + str(counts["medium"]), "medium"),
            _chip("низкий риск: " + str(counts["low"]), "low"),
        ]), s["body"]),
        Spacer(1, 4),
    ]
    rows = []
    for m in ordered:
        top = (m.get("alerts") or [{"text": "—"}])[0]["text"]
        rows.append([
            m["title"], {"inn": m["inn"]},
            {"t": m["status"]["text"], "level": m["status"]["level"]},
            {"t": m["verdict"]["headline"].capitalize(), "level": m["verdict"]["level"]}, top,
        ])
    story.append(_table_body({
        "columns": ["Организация", "ИНН", "Статус", "Итог", "Главное"],
        "widths": [0.25, 0.155, 0.15, 0.17, 0.275], "align": ["L"] * 5, "rows": rows,
    }))
    story += [Spacer(1, 10), _disclaimer()]
    for m in models:
        story.append(PageBreak())
        story += _company_flowables(m, with_disclaimer=False)
    return _build(story, "Сводное досье", "Досье Плюс  ·  сводное досье")


# ───────────────────── текстовые отчёты (книга покупок/продаж и т.п.) ─────────────────────

_EMOJI_MAP = {
    "🟢": f'<font color="{LEVEL_SOLID["low"]}">●</font>',
    "🟡": f'<font color="{LEVEL_SOLID["medium"]}">●</font>',
    "🔴": f'<font color="{LEVEL_SOLID["high"]}">●</font>',
    "⚠": f'<font color="{LEVEL_SOLID["medium"]}">▲</font>',
    "✅": f'<font color="{LEVEL_SOLID["low"]}">✓</font>',
    "❌": f'<font color="{LEVEL_SOLID["high"]}">✗</font>',
}
_EMOJI_RE = re.compile("[\U0001F000-\U0001FAFF\u2600-\u27BF\uFE0F\u200D\u2B00-\u2BFF]")


def _strip_emoji(text: str) -> str:
    """Цветные эмодзи в PDF не рисуются (пустые квадраты) — светофор заменяем цветными
    значками из шрифта, остальные эмодзи убираем."""
    return _EMOJI_RE.sub(lambda m: _EMOJI_MAP.get(m.group(0), ""), text)


def _text_flowables(text: str) -> list:
    """Текст в Telegram-разметке -> абзацы. Блок, первая строка которого целиком <b>Заголовок</b>,
    оформляется как заголовок раздела."""
    s = _S()
    text = _strip_emoji(text)
    fl: list = []
    pre_re = re.compile(r"<pre>(.*?)</pre>", re.S)

    def add_paragraphs(chunk: str):
        for block in [b for b in chunk.split("\n\n") if b.strip()]:
            lines = block.strip("\n").split("\n")
            m = re.fullmatch(r"\s*<b>(.*?)</b>\s*", lines[0])
            if m and len(lines) >= 1 and len(m.group(1)) < 90:
                fl.extend([CondPageBreak(30 * mm), _section_header(re.sub(r"<[^>]+>", "", m.group(1))), Spacer(1, 3)])
                rest = "\n".join(lines[1:]).strip()
                if rest:
                    fl.append(Paragraph(rest.replace("\n", "<br/>"), s["body"]))
            else:
                fl.append(Paragraph(block.replace("\n", "<br/>"), s["body"]))

    pos = 0
    for m in pre_re.finditer(text):
        add_paragraphs(text[pos:m.start()])
        content = m.group(1).replace("\n", "<br/>").replace(" ", "&nbsp;")
        fl.append(Paragraph(f'<font face="DejaVuSansMono" size="8.2">{content}</font>', s["body"]))
        pos = m.end()
    add_paragraphs(text[pos:])
    return fl


def build_dossier_pdf(title: str, subtitle: str, body_text: str) -> bytes:
    _ensure_fonts()
    story = [_banner(title, subtitle, "ДОСЬЕ ПЛЮС  ·  ОТЧЁТ"), Spacer(1, 10)]
    story += _text_flowables(body_text)
    story += [Spacer(1, 4), _disclaimer()]
    return _build(story, title, f"Досье Плюс  ·  {title}")


def build_multi_dossier_pdf(sections: list[tuple[str, str, str]]) -> bytes:
    """Несколько текстовых досье в одном файле (старый формат — без структурной модели)."""
    _ensure_fonts()
    story: list = []
    for i, (title, subtitle, body_text) in enumerate(sections):
        if i:
            story.append(PageBreak())
        story += [_banner(title, subtitle, "ДОСЬЕ ПЛЮС  ·  ДОСЬЕ КОНТРАГЕНТА"), Spacer(1, 10)]
        story += _text_flowables(body_text)
    story += [Spacer(1, 6), _disclaimer()]
    return _build(story, "Сводное досье", "Досье Плюс  ·  сводное досье")
