# -*- coding: utf-8 -*-
"""Опыт печати: что пошло не так на прошлых деталях из того же материала и в том же режиме.

Источник — записи картотеки с отметкой «Были проблемы»:
- исправления, применённые в разборе по фото (ключ, было, стало, дефект), — точные;
- если исправлений нет, дефекты узнаются по заметке («по углам отклеилась») и исправления
  считаются от настроек, с которыми деталь печатали.

При подборе настроек новой детали эти значения ставятся сразу, с источником «опыт» и причиной.
Требования ТЗ (lock) и выбор пользователя (overrides) — главнее опыта.
"""
from . import materials as M
from . import review as RV

SRC = "experience"


def _mode_short(mode):
    """В картотеке режим записан как «3D»/«5D», в состоянии детали — classic/cylindrical…"""
    if mode in ("3D", "5D"):
        return mode
    return "3D" if mode in (None, "", "classic") else "5D"


def _fmt(v):
    if isinstance(v, bool):
        return "да" if v else "нет"
    if isinstance(v, float):
        return ("%g" % v).replace(".", ",")
    return str(v)


def from_record(rec):
    """Исправления из одной записи: [{"key", "from", "to", "defect", "label"}]."""
    if rec.get("lessons"):
        return [dict(x) for x in rec["lessons"] if x.get("key")]
    ids = RV.from_words(rec.get("note") or "")
    if not ids:
        return []
    values = rec.get("settings") if isinstance(rec.get("settings"), dict) else {}
    mode = "classic" if _mode_short(rec.get("mode")) == "3D" else "cylindrical"
    fx = RV.fixes(ids, values, M.by_id(rec.get("material_id") or ""), mode)
    return [{"key": f["key"], "from": f["from"], "to": f["to"], "defect": f["defect"], "label": f["key"]} for f in fx]


def lessons(catalog, material_id, mode, exclude=None):
    """Уроки для материала и режима: по одному на параметр, свежая запись важнее.

    [{"key", "from", "to", "defect", "label", "parts": [имена деталей], "count"}]
    """
    if catalog is None or not material_id:
        return []
    short = _mode_short(mode)
    out, order = {}, []
    for rec in catalog.all():  # картотека — от новых к старым
        if rec.get("state") != "issue" or rec.get("material_id") != material_id:
            continue
        if _mode_short(rec.get("mode")) != short or (exclude and rec.get("id") == exclude):
            continue
        for it in from_record(rec):
            k = it["key"]
            if k in out:
                out[k]["count"] += 1
                if rec.get("name") and rec["name"] not in out[k]["parts"]:
                    out[k]["parts"].append(rec["name"])
                continue
            out[k] = dict(it, parts=[rec["name"]] if rec.get("name") else [], count=1)
            order.append(k)
    return [out[k] for k in order]


def apply(rows, items):
    """Поставить уроки в строки плана. Возвращает применённые уроки.

    Число меняем, только если план ещё «по ту сторону» исправления: был поток 100 → 95, а правила
    дают 100 — ставим 95; правила уже дают 90 — не трогаем.
    """
    done = []
    by_key = dict((r["key"], r) for r in rows if r.get("key"))
    for it in items:
        k, to, was = it["key"], it.get("to"), it.get("from")
        row = by_key.get(k)
        if row is not None and row.get("lock"):
            continue
        cur = row["value"] if row is not None else None
        num = lambda v: isinstance(v, (int, float)) and not isinstance(v, bool)  # noqa: E731
        if row is not None and num(to) and num(cur) and num(was):
            step = to - was
            if step == 0 or (to - cur) * step <= 0:
                continue
        elif cur == to:
            continue
        parts = it.get("parts") or []
        where = ("на «%s»" % parts[0]) + (" и ещё %d" % (len(parts) - 1) if len(parts) > 1 else "") if parts else "раньше"
        why = "Опыт: %s из этого материала было «%s» — сразу ставлю %s." % (where, (it.get("defect") or "дефект").lower(), _fmt(to))
        if row is None:
            row = {"key": k, "value": to, "k": it.get("label") or k, "v": _fmt(to), "src": SRC, "why": why,
                   "lock": False, "opts": []}
            rows.append(row)
            by_key[k] = row
        else:
            row["value"] = to
            label = [o[0] for o in row.get("opts") or [] if o[1] == to]
            row["v"] = label[0] if label else ("%d %%" % int(to) if k == "infill_sparse_density" else _fmt(to))
            row["src"], row["why"] = SRC, why
        done.append(it)
    return done


def note(done):
    """Одна строка для чата: что взято из опыта."""
    if not done:
        return ""
    return "Учёл опыт прошлых печатей: " + "; ".join(
        "%s → %s (было «%s»)" % ((d.get("label") or d["key"]).lower(), _fmt(d["to"]), (d.get("defect") or "").lower())
        for d in done) + "."

