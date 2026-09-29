"""
Поиск связей между организациями/ИП по уже собранным данным (DaData + Checko).

Что считаем связью:
  • один и тот же человек (по ИНН физлица) в ЛЮБОЙ роли: директор ↔ директор,
    директор ↔ учредитель, ИП ↔ директор/учредитель другой компании;
  • организация — учредитель другой организации из списка;
  • управляющая организация одной — другая организация из списка;
  • общий юридический адрес, телефон, e-mail;
  • общий госконтракт (заказчик↔поставщик или соисполнители одного контракта);
  • связь, уже зафиксированная Checko (общий учредитель/руководитель/упр. организация);
  • «исторические» связи через физлиц: человек связан с обеими организациями по данным /person,
    даже если в текущем списке одной из них его уже нет (enrich_person_links).

Поле kind — короткая подпись для линии на схеме, detail — подробности для текста.
"""

from __future__ import annotations
import asyncio
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


# ───────────────────────── вспомогательное ─────────────────────────

def _norm_addr(addr: str) -> str:
    return re.sub(r"[^\w]", "", (addr or "")).lower()


def company_name(result) -> str:
    if result.dadata and result.dadata.name_short:
        return result.dadata.name_short
    if result.checko and result.checko.name_short:
        return result.checko.name_short
    if getattr(result, "entrepreneur", None) and result.entrepreneur.fio:
        return f"ИП {result.entrepreneur.fio}"
    if getattr(result, "person", None) and result.person.fio:
        return result.person.fio
    return result.inn


def _address(result) -> str:
    if result.dadata and result.dadata.address:
        return result.dadata.address
    if result.checko and result.checko.address:
        return result.checko.address
    return ""


def _phones_emails(result) -> tuple[set[str], set[str]]:
    phones, emails = set(), set()
    if result.checko:
        phones |= set(result.checko.phones or [])
        emails |= set(result.checko.emails or [])
    e = getattr(result, "entrepreneur", None)
    if e:
        phones |= set(e.phones or [])
        emails |= set(e.emails or [])
    return phones, emails


def people_of(result) -> dict[str, dict]:
    """ИНН физлица -> {'name', 'roles': {'директор', 'учредитель', 'ИП'}} — все люди организации."""
    people: dict[str, dict] = {}

    def add(inn: str, name: str, role: str):
        if not inn:
            return
        p = people.setdefault(inn, {"name": name or inn, "roles": set()})
        p["roles"].add(role)

    c = result.checko
    if c:
        for d in c.directors:
            add(d.inn, d.name, "директор")
        for f in c.founders:
            if f.kind == "физлицо":
                add(f.inn, f.name, "учредитель")
    if result.dadata and result.dadata.management and result.dadata.management.inn:
        add(result.dadata.management.inn, result.dadata.management.name, "директор")

    e = getattr(result, "entrepreneur", None)
    if e:
        add(result.inn, e.fio, "ИП")                        # сам ИП — физлицо со своим ИНН
    elif len(result.inn) == 12 and getattr(result, "person", None):
        add(result.inn, result.person.fio, "физлицо")
    return people


def _org_links_of(result) -> dict[str, str]:
    """ИНН другой организации -> как она связана с этой (по данным самой карточки)."""
    links: dict[str, str] = {}
    c = result.checko
    if c:
        for f in c.founders:
            if f.kind != "физлицо" and f.inn:
                links[f.inn] = "организация-учредитель"
        if c.managing_org and c.managing_org.inn:
            links[c.managing_org.inn] = "управляющая организация"
        for r in c.related_orgs:
            if r.get("inn") and r["inn"] not in links:
                links[r["inn"]] = r.get("kind") or "связь по данным Checko"
    e = getattr(result, "entrepreneur", None) or getattr(result, "person", None)
    if e:
        for r in e.managed_orgs:
            if r.get("inn"):
                links[r["inn"]] = "ИП/физлицо — руководитель"
        for r in e.founded_orgs:
            if r.get("inn") and r["inn"] not in links:
                links[r["inn"]] = "ИП/физлицо — учредитель"
    return links


def _roles_str(roles: set[str]) -> str:
    return "/".join(sorted(roles))


# ───────────────────────── основной поиск ─────────────────────────

def find_connections(results: dict[str, "CardResult"]) -> list[Connection]:
    items = list(results.items())
    connections: list[Connection] = []

    for i in range(len(items)):
        inn_a, res_a = items[i]
        name_a = company_name(res_a)
        people_a = people_of(res_a)
        org_links_a = _org_links_of(res_a)
        phones_a, emails_a = _phones_emails(res_a)

        for j in range(i + 1, len(items)):
            inn_b, res_b = items[j]
            name_b = company_name(res_b)
            people_b = people_of(res_b)
            org_links_b = _org_links_of(res_b)
            phones_b, emails_b = _phones_emails(res_b)

            def add(kind: str, detail: str):
                connections.append(Connection(inn_a, name_a, inn_b, name_b, kind, detail))

            # 1) один и тот же человек в любой роли
            for pid in set(people_a) & set(people_b):
                ra, rb = people_a[pid]["roles"], people_b[pid]["roles"]
                name = people_a[pid]["name"]
                if ra == rb == {"директор"}:
                    kind = "общий директор"
                elif ra == rb == {"учредитель"}:
                    kind = "общий учредитель"
                elif ra == rb == {"директор", "учредитель"}:
                    kind = "общий директор и учредитель"
                elif "ИП" in ra or "ИП" in rb:
                    kind = "ИП↔" + _roles_str((ra | rb) - {"ИП"} or {"физлицо"})
                elif ra == rb:
                    kind = "общий человек: " + _roles_str(ra)
                else:
                    kind = "человек: " + _roles_str(ra) + " ↔ " + _roles_str(rb)
                add(kind, f"{name} (ИНН {pid}): в «{name_a}» — {_roles_str(ra)}, в «{name_b}» — {_roles_str(rb)}")

            # 2) организационные связи (учредитель-организация, упр. организация, Checko-связи)
            if inn_b in org_links_a:
                add(org_links_a[inn_b], f"«{name_b}» связана с «{name_a}»: {org_links_a[inn_b]}")
            if inn_a in org_links_b and org_links_b[inn_a] != org_links_a.get(inn_b):
                add(org_links_b[inn_a], f"«{name_a}» связана с «{name_b}»: {org_links_b[inn_a]}")

            # 3) общий адрес
            addr_a, addr_b = _address(res_a), _address(res_b)
            if addr_a and addr_b and _norm_addr(addr_a) == _norm_addr(addr_b):
                add("общий адрес", addr_a)

            # 4) общие контакты
            for p in phones_a & phones_b:
                add("общий телефон", p)
            for e in emails_a & emails_b:
                add("общий e-mail", e)

            # 5) общие госконтракты
            seen = set()
            for ct in (getattr(res_a, "contracts", None) or []):
                if ct.reg_number in seen:
                    continue
                if ct.customer_inn == inn_b or inn_b in ct.supplier_inns:
                    if ct.customer_inn == inn_a and inn_b in ct.supplier_inns:
                        detail = f"№{ct.reg_number} от {ct.date}: «{name_a}» заказчик, «{name_b}» поставщик"
                    elif ct.customer_inn == inn_b and inn_a in ct.supplier_inns:
                        detail = f"№{ct.reg_number} от {ct.date}: «{name_b}» заказчик, «{name_a}» поставщик"
                    else:
                        detail = f"№{ct.reg_number} от {ct.date}: обе в числе поставщиков одного контракта"
                    add("общий госконтракт", detail)
                    seen.add(ct.reg_number)

    return connections


async def enrich_person_links(
    results: dict[str, "CardResult"], checko_key: str, max_lookups: int = 20
) -> list[Connection]:
    """«Исторические»/скрытые связи через физлиц: для людей из карточек запрашиваем /person и смотрим,
    не числятся ли они (руководителем/учредителем) ещё и в ДРУГОЙ проверяемой организации, даже если в её
    текущем списке их нет. Каждый человек — один запрос, всего не больше max_lookups."""
    from checko_client import get_person, CheckoError

    people_by_company = {inn: people_of(res) for inn, res in results.items()}
    all_pids: list[str] = []
    for people in people_by_company.values():
        for pid in people:
            if pid not in all_pids and len(pid) == 12:
                all_pids.append(pid)
    all_pids = all_pids[:max_lookups]
    if not all_pids:
        return []

    sem = asyncio.Semaphore(5)

    async def fetch(pid: str):
        async with sem:
            try:
                return pid, await get_person(pid, checko_key)
            except CheckoError:
                return pid, None

    fetched = dict(await asyncio.gather(*(fetch(pid) for pid in all_pids)))

    extra: list[Connection] = []
    inns = list(results)
    for a in range(len(inns)):
        for b in range(a + 1, len(inns)):
            inn_a, inn_b = inns[a], inns[b]
            name_a, name_b = company_name(results[inn_a]), company_name(results[inn_b])
            for src, dst, name_src, name_dst in ((inn_a, inn_b, name_a, name_b), (inn_b, inn_a, name_b, name_a)):
                for pid, info in people_by_company[src].items():
                    person = fetched.get(pid)
                    if not person or pid in people_by_company[dst]:
                        continue                      # уже найдено прямым сравнением
                    roles_dst = []
                    if any(o.get("inn") == dst for o in person.managed_orgs):
                        roles_dst.append("руководитель")
                    if any(o.get("inn") == dst for o in person.founded_orgs):
                        roles_dst.append("учредитель")
                    if roles_dst:
                        extra.append(Connection(
                            src, name_src, dst, name_dst,
                            "связь через физлицо",
                            f"{person.fio or info['name']} (ИНН {pid}): в «{name_src}» — {_roles_str(info['roles'])}, "
                            f"также {'/'.join(roles_dst)} в «{name_dst}» (по данным Checko)",
                        ))
    # убираем дубли (A→B и B→A по одному и тому же человеку)
    unique, seen = [], set()
    for c in extra:
        key = (frozenset((c.inn_a, c.inn_b)), c.detail.split(" (ИНН")[0])
        if key not in seen:
            seen.add(key)
            unique.append(c)
    return unique


def graph_edges(connections: list[Connection]) -> list[tuple[str, str, str, str]]:
    return [(c.inn_a, c.inn_b, c.kind, c.detail) for c in connections]
