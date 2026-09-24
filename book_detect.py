"""Определяет, какая это книга — покупок или продаж — по ключевым словам в заголовке,
не полагаясь на имя файла (пользователь может назвать файл как угодно)."""

from __future__ import annotations
import openpyxl


def detect_book_type(path: str) -> str | None:
    wb = openpyxl.load_workbook(path, data_only=True, read_only=True)
    ws = wb.active
    for row_idx, row in enumerate(ws.iter_rows(max_row=30, values_only=True), start=1):
        joined = " ".join(str(c) for c in row if c is not None)
        if "Стоимость покупок" in joined:
            return "purchases"
        if "в руб. и коп." in joined:
            return "sales"
    return None
