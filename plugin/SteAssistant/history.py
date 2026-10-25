# -*- coding: utf-8 -*-
"""«Было → стало»: копия состояния до правки, отмена, вариант и значение из паспорта."""

import copy
import re
import uuid

from . import materials as M
from . import rules


class _Workflow(object):
    """Остальной сценарий — через workflow при вызове: модули ссылаются друг на друга."""

    def __getattr__(self, name):
        from . import workflow
        return getattr(workflow, name)


W = _Workflow()


MAX_UNDO = 15  # столько последних карточек «Было → стало» можно отменить; у старых копия убирается
# В копию для отмены не кладём то, что правки не меняют или что заново читается из слайсера.
SNAPSHOT_SKIP = ("spec_text", "slicer_values")

# ============================================================ «было → стало» и отмена

def snapshot(run):
    st = run.st
    snap = {"state": copy.deepcopy(dict((k, v) for k, v in st.items() if k not in SNAPSHOT_SKIP)),
            "material": run.part.get("material"),
            "mode": run.part.get("mode")}
    return snap


def diff_block(run, before_snap, rows, title, before_totals=None):
    after = run.st.get("totals")
    b = {"type": "diff", "id": "d" + uuid.uuid4().hex[:6], "title": title, "rows": rows,
         "before": before_totals or before_snap["state"].get("totals"), "after": after, "undone": False,
         "snapshot": before_snap}
    _expire_snapshots(run.part)
    return run.block(b)


def _expire_snapshots(part, keep=MAX_UNDO - 1):
    """Оставить копии только у последних карточек: иначе файл детали растёт на ~15 КБ с каждой правкой."""
    diffs = [b for m in part.get("messages", []) for b in m.get("blocks", []) or []
             if b.get("type") == "diff" and b.get("snapshot") is not None]
    for b in diffs[:max(0, len(diffs) - keep)]:
        b.pop("snapshot", None)
        if not b.get("undone"):
            b["expired"] = True


def set_variant(run, v):
    st = run.st
    if v not in rules.VARIANTS:
        raise ValueError("Вариант: fast, cheap или quality")
    before = snapshot(run)
    old_rows = dict((r["k"], r["v"]) for r in st.get("rows", []) if r["k"])
    (st.get("prefs") or {}).pop("mix", None)  # вершина треугольника заменяет смесь
    W.apply_settings(run, v)
    if (st.get("variants") and v in st["variants"]) or before["state"].get("totals"):
        W.slice_part(run, card=False)  # итоги — только из настоящей нарезки, не из прошлых расчётов
    rows = []
    for r in st["rows"]:
        if r["k"] and old_rows.get(r["k"]) not in (None, r["v"]):
            rows.append([r["k"], old_rows[r["k"]], r["v"], r["why"] or "Вариант «%s»" % rules.VARIANTS[v]["title"]])
    diff_block(run, before, rows, "Было → стало")
    return rows


def set_value(run, key_label, value):
    """Пользователь выбрал значение допущения в паспорте: «Заполнение» -> «60 %»."""
    st = run.st
    before = snapshot(run)
    rows = []
    if key_label == "Материал":
        mid = None
        for m in M.MATERIALS:
            if m["name"] == value or m["id"] == value:
                mid = m["id"]
        if not mid:
            raise ValueError("Материал %s не найден" % value)
        old = M.by_id(st["material_id"])["name"]
        st["fixed_material"] = mid
        W.select_material(run, fixed=mid)
        st["material_src"] = "words"
        W.apply_settings(run)
        rows.append(["Материал", old, M.by_id(mid)["name"], "Вы выбрали в паспорте"])
    else:
        target = [r for r in st.get("rows", []) if r["k"] == key_label]
        if not target:
            raise ValueError("Параметр %s не найден" % key_label)
        r = target[0]
        num = re.findall(r"\d+(?:[.,]\d+)?", value)
        if not num:
            raise ValueError("Не понял значение %s" % value)
        newv = float(num[0].replace(",", "."))
        if r["key"] in ("wall_line_count",):
            newv = int(newv)
        s = run.step("set_settings", "Изменил «%s»" % key_label, {r["key"]: newv})
        changes = run.bridge.set_settings({r["key"]: newv})
        rows.append([key_label, r["v"], value, "Вы выбрали в паспорте"])
        r["v"], r["value"], r["src"] = value, newv, "words"
        st.setdefault("overrides", {})[r["key"]] = newv
        run.done(s, "%s: %s" % (key_label, value), "", changes)
    if before["state"].get("totals"):
        W.slice_part(run, card=False)
    diff_block(run, before, rows, "Изменил: %s" % key_label.lower())
    return rows


def undo(run, block_id):
    target = None
    for m in run.part["messages"]:
        for b in m.get("blocks", []) or []:
            if b.get("type") == "diff" and b.get("id") == block_id:
                target = b
    if not target or target.get("undone"):
        raise ValueError("Нечего отменять")
    snap = target.get("snapshot")
    if snap is None:
        raise ValueError("Это изменение слишком давнее — отменить можно последние %d" % MAX_UNDO)
    s = run.step("undo", "Вернул как было")
    st = snap["state"]
    # Что задано после снимка (правка в паспорте, «Все настройки»), — вернуть к профилю.
    now = set(run.st.get("settings_applied") or {}) | set(run.st.get("overrides") or {})
    later = [k for k in now if k not in (st.get("settings_applied") or {}) and k not in (st.get("overrides") or {})]
    if later and hasattr(run.bridge, "reset_settings"):
        run.bridge.reset_settings(later)
    keep = dict((k, run.st[k]) for k in SNAPSHOT_SKIP if k in run.st)
    run.part["state"] = copy.deepcopy(st)
    run.part["state"].update(keep)
    if "passport" in snap:  # копии прежних версий; теперь паспорт строится заново в конце хода
        run.part["passport"] = copy.deepcopy(snap["passport"])
    run.part["material"] = snap.get("material")
    run.part["mode"] = snap.get("mode")
    if st.get("material_id"):
        run.bridge.set_material(st["material_id"])
    changes = run.bridge.set_settings(st.get("settings_applied", {}))
    if st.get("overrides"):
        run.bridge.set_settings(st["overrides"])
    target["undone"] = True
    target.pop("snapshot", None)  # отменённую карточку второй раз не отменяют — копия не нужна
    W.refresh_values(run)
    run.done(s, ", ".join("%s %s" % (r[0].lower(), r[1]) for r in target["rows"]), "", changes)
    had = bool(st.get("totals"))
    run.st.pop("totals", None)
    if had:
        W.slice_part(run, card=False)
    return target
