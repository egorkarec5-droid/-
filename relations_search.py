"""
Поиск связей между несколькими организациями по уже собранным данным
(DaData + Checko): общий директор/учредитель (в т.ч. бывшие), общий адрес,
совпадающий телефон/e-mail, известная связь напрямую от Checko.
"""

from __future__ import annotations
import re
from dataclasses import dataclass


@dataclass
class Connection:
    inn_a: str
    name_a: str
    inn_b: str
    name_b: str
    kind: str
    detail: str


def _norm_addr(addr: str) -> str:
    return re.sub(r"[^\w]", "", (addr or "")).lower()


def _company_name(result) -> str:
    if result.dadata and result.dadata.name_short:
        return result.dadata.name_short
    if result.checko and result.checko.name_short:
        return result.checko.name_short
    return result.inn


def _address(result) -> str:
    if result.dadata and result.dadata.address:
        return result.dadata.address
    if result.checko and result.checko.address:
        return result.checko.address
    return ""


def find_connections(results: dict[str, "CardResult"]) -> list[Connection]:
    items = list(results.items())
    connections: list[Connection] = []

    for i in range(len(items)):
        inn_a, res_a = items[i]
        name_a = _company_name(res_a)
        c_a = res_a.checko

        for j in range(i + 1, len(items)):
            inn_b, res_b = items[j]
            name_b = _company_name(res_b)
            c_b = res_b.checko

            # общий директор (текущий или бывший — весь список Руковод, не только текущий)
            if c_a and c_b:
                dirs_a = {d.inn: d.name for d in c_a.directors if d.inn}
                dirs_b = {d.inn: d.name for d in c_b.directors if d.inn}
                for common_inn in set(dirs_a) & set(dirs_b):
                    connections.append(Connection(
                        inn_a, name_a, inn_b, name_b, "общий директор",
                        f"{dirs_a[common_inn]} (ИНН {common_inn})",
                    ))

                # общий учредитель
                f_a = {f.inn: f.name for f in c_a.founders if f.inn}
                f_b = {f.inn: f.name for f in c_b.founders if f.inn}
                for common_inn in set(f_a) & set(f_b):
                    connections.append(Connection(
                        inn_a, name_a, inn_b, name_b, "общий учредитель",
                        f"{f_a[common_inn]} (ИНН {common_inn})",
                    ))

                # управляющая организация совпадает с другой компанией из списка
                if c_a.managing_org and c_a.managing_org.inn == inn_b:
                    connections.append(Connection(inn_a, name_a, inn_b, name_b, "управляющая организация", f"{name_b} управляет {name_a}"))
                if c_b.managing_org and c_b.managing_org.inn == inn_a:
                    connections.append(Connection(inn_a, name_a, inn_b, name_b, "управляющая организация", f"{name_a} управляет {name_b}"))

                # телефон/e-mail
                common_phones = set(c_a.phones) & set(c_b.phones)
                for p in common_phones:
                    connections.append(Connection(inn_a, name_a, inn_b, name_b, "общий телефон", p))
                common_emails = set(c_a.emails) & set(c_b.emails)
                for e in common_emails:
                    connections.append(Connection(inn_a, name_a, inn_b, name_b, "общий e-mail", e))

                # уже известная связь напрямую от Checko (СвязУчред)
                related_a = {r.get("inn") for r in c_a.related_orgs}
                related_b = {r.get("inn") for r in c_b.related_orgs}
                if inn_b in related_a or inn_a in related_b:
                    connections.append(Connection(inn_a, name_a, inn_b, name_b, "известная связь (Checko)", "предшественник/преемник или иная зафиксированная связь"))

            # общий адрес — сравниваем независимо от наличия Checko
            addr_a, addr_b = _address(res_a), _address(res_b)
            if addr_a and addr_b and _norm_addr(addr_a) == _norm_addr(addr_b):
                connections.append(Connection(inn_a, name_a, inn_b, name_b, "общий адрес", addr_a))

    return connections
