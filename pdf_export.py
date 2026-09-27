"""
Экспорт карточки контрагента (и отчёта по книге) в PDF.
ВАЖНО: это НЕ официальная выписка из ЕГРЮЛ с ЭЦП ФНС — такую бот выдать не может,
для этого нужен прямой доступ к сервису налоговой. Это собственное PDF-досье
бота на основе уже собранных данных DaData/Checko.
"""

from __future__ import annotations
import io
import os
import re

import matplotlib
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.enums import TA_LEFT, TA_CENTER
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, HRFlowable, PageBreak
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

_FONT_READY = False


def _ensure_fonts():
    """Регистрирует DejaVu Sans (кириллица) — берём готовый шрифт из matplotlib,
    чтобы не тащить отдельный файл шрифта в репозиторий."""
    global _FONT_READY
    if _FONT_READY:
        return
    base = os.path.join(os.path.dirname(matplotlib.__file__), "mpl-data", "fonts", "ttf")
    pdfmetrics.registerFont(TTFont("DejaVuSans", os.path.join(base, "DejaVuSans.ttf")))
    pdfmetrics.registerFont(TTFont("DejaVuSans-Bold", os.path.join(base, "DejaVuSans-Bold.ttf")))
    pdfmetrics.registerFont(TTFont("DejaVuSansMono", os.path.join(base, "DejaVuSansMono.ttf")))
    pdfmetrics.registerFontFamily(
        "DejaVuSans", normal="DejaVuSans", bold="DejaVuSans-Bold",
        italic="DejaVuSans", boldItalic="DejaVuSans-Bold",
    )
    _FONT_READY = True


# У DejaVu Sans нет цветных эмодзи-глифов (будут пустые квадраты в PDF) —
# заменяем на короткие текстовые пометки перед рендером.
_EMOJI_MAP = {
    "🟢": "[низкий] ", "🟡": "[средний] ", "🔴": "[высокий] ",
    "⚠️": "[!] ", "✅": "[OK] ", "❌": "[X] ", "⏳": "", "📊": "", "📈": "", "🔗": "",
    "📥": "", "📝": "", "🛠": "", "👥": "", "🔍": "", "ℹ️": "", "⚙️": "",
}


def _strip_emoji(text: str) -> str:
    for emo, repl in _EMOJI_MAP.items():
        text = text.replace(emo, repl)
    return text


def _telegram_html_to_reportlab(text: str) -> list[str]:
    """Наш текст карточки использует Telegram-разметку (<b>, <i>, <pre>, переносы строк).
    Готовим абзацы под reportlab: <pre> -> моноширинный блок, остальное построчно."""
    text = _strip_emoji(text)
    blocks = []
    pre_pattern = re.compile(r"<pre>(.*?)</pre>", re.S)

    pos = 0
    for m in pre_pattern.finditer(text):
        before = text[pos:m.start()]
        if before.strip():
            blocks.extend(_split_paragraphs(before))
        pre_content = m.group(1).replace("\n", "<br/>").replace(" ", "&nbsp;")
        blocks.append(f'<font face="DejaVuSansMono" size="8">{pre_content}</font>')
        pos = m.end()
    tail = text[pos:]
    if tail.strip():
        blocks.extend(_split_paragraphs(tail))
    return blocks


def _split_paragraphs(text: str) -> list[str]:
    parts = [p for p in text.split("\n\n") if p.strip()]
    result = []
    for p in parts:
        p = p.replace("\n", "<br/>")
        result.append(p)
    return result


_STYLES_READY = None


def _styles():
    global _STYLES_READY
    if _STYLES_READY is None:
        _STYLES_READY = {
            "title": ParagraphStyle("title", fontName="DejaVuSans-Bold", fontSize=16, leading=20, alignment=TA_LEFT),
            "subtitle": ParagraphStyle("subtitle", fontName="DejaVuSans", fontSize=9, leading=12, textColor="#666666"),
            "body": ParagraphStyle("body", fontName="DejaVuSans", fontSize=9.5, leading=13.5, alignment=TA_LEFT, spaceAfter=6),
            "footer": ParagraphStyle("footer", fontName="DejaVuSans", fontSize=7.5, leading=10, textColor="#888888", alignment=TA_CENTER),
        }
    return _STYLES_READY


_FOOTER_TEXT = (
    "Это НЕ официальная выписка из ЕГРЮЛ и не документ с электронной подписью ФНС — "
    "собственное досье на основе открытых данных DaData/Checko, не является юридическим заключением."
)


def _section_elements(title: str, subtitle: str, body_text: str) -> list:
    s = _styles()
    elements = [
        Paragraph("Досье Плюс — досье контрагента", s["subtitle"]),
        Paragraph(title, s["title"]),
        Paragraph(subtitle, s["subtitle"]),
        Spacer(1, 6),
        HRFlowable(width="100%", color="#CCCCCC", thickness=0.7),
        Spacer(1, 8),
    ]
    for block in _telegram_html_to_reportlab(body_text):
        elements.append(Paragraph(block, s["body"]))
    elements.append(Spacer(1, 10))
    elements.append(HRFlowable(width="100%", color="#CCCCCC", thickness=0.7))
    elements.append(Spacer(1, 6))
    elements.append(Paragraph(_FOOTER_TEXT, s["footer"]))
    return elements


def build_dossier_pdf(title: str, subtitle: str, body_text: str) -> bytes:
    _ensure_fonts()
    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=A4,
        topMargin=16 * mm, bottomMargin=16 * mm, leftMargin=18 * mm, rightMargin=18 * mm,
        title=title,
    )
    doc.build(_section_elements(title, subtitle, body_text))
    buf.seek(0)
    return buf.read()


def build_multi_dossier_pdf(sections: list[tuple[str, str, str]]) -> bytes:
    """Одна PDF-книжка на несколько компаний сразу — каждая начинается с новой
    страницы. sections — список (title, subtitle, body_text) на компанию."""
    _ensure_fonts()
    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=A4,
        topMargin=16 * mm, bottomMargin=16 * mm, leftMargin=18 * mm, rightMargin=18 * mm,
        title="Досье Плюс — сводное досье",
    )
    elements = []
    for i, (title, subtitle, body_text) in enumerate(sections):
        if i > 0:
            elements.append(PageBreak())
        elements.extend(_section_elements(title, subtitle, body_text))
    doc.build(elements)
    buf.seek(0)
    return buf.read()
