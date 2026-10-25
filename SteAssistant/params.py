# -*- coding: utf-8 -*-
"""Параметры печати: значения из STE Slicer, правка пользователем, сочетания."""

import re

from . import materials as M
from . import rules


class _Workflow(object):
    """Остальной сценарий — через workflow при вызове: модули ссылаются друг на друга."""

    def __getattr__(self, name):
        from . import workflow
        return getattr(workflow, name)


W = _Workflow()


# ============================================================ параметры печати: правка из паспорта

# Что паспорт показывает сверх плана: скорость, температуры, обдув, откат.
PRINT_KEYS = ["speed_print", "speed_wall_0", "speed_infill", "speed_travel", "material_print_temperature",
              "material_bed_temperature", "cool_fan_speed", "retraction_amount", "retraction_speed"]
UNITS = {"mm": "мм", "mm/s": "мм/с", "s": "с", "mm²": "мм²", "mm³": "мм³", "°": "°", "°C": "°C", "%": "%"}


def show_value(v, info=None):
    """Значение параметра для человека: «да», «Кайма», «50 мм/с»."""
    info = info or {}
    if isinstance(v, bool):
        return "да" if v else "нет"
    for o in info.get("options") or []:
        if str(o[0]) == str(v):
            return o[1]
    unit = UNITS.get(info.get("unit") or "", info.get("unit") or "")
    if isinstance(v, (int, float)):
        txt = W._n(round(v, 3))
    else:
        txt = "—" if v in (None, "") else str(v)
    return (txt + " " + unit).strip() if unit and txt != "—" else txt


def coerce_value(info, raw):
    """Строку из поля паспорта — в тип параметра STE Slicer; ValueError с понятной причиной."""
    typ = info.get("type")
    if isinstance(raw, str):
        raw = raw.strip()
    if typ == "bool":
        if isinstance(raw, bool):
            return raw
        return str(raw).lower() in ("true", "1", "да", "yes", "вкл")
    if typ == "enum":
        keys = [str(o[0]) for o in info.get("options") or []]
        for o in info.get("options") or []:
            if str(raw).lower() in (str(o[0]).lower(), str(o[1]).lower()):
                return o[0]
        if keys:
            raise ValueError("«%s»: одно из — %s" % (info.get("label"), ", ".join(o[1] for o in info["options"])))
        return raw
    if typ in ("int", "float"):
        num = re.findall(r"-?\d+(?:[.,]\d+)?", str(raw).replace("−", "-"))
        if not num:
            raise ValueError("«%s»: нужно число" % info.get("label"))
        v = float(num[0].replace(",", "."))
        return int(round(v)) if typ == "int" else v
    return raw


def setting_infos(run, keys):
    try:
        return run.bridge.setting_info(list(keys)) or {}
    except Exception:
        return {}


def refresh_values(run):
    """Параметры печати для паспорта — как их сейчас видит STE Slicer (с подписями и единицами)."""
    st = run.st
    keys = list(PRINT_KEYS)
    keys += [r["key"] for r in st.get("rows", []) if r.get("key") and r["key"] not in keys]
    keys += [k for k in st.get("overrides") or {} if k not in keys]
    info = setting_infos(run, keys)
    st["slicer_values"] = dict((k, dict((f, i.get(f)) for f in ("label", "unit", "type", "min", "max", "options",
                                                                  "value", "description")))
                               for k, i in info.items())
    return st["slicer_values"]


def set_user_settings(run, values, why="Вы изменили в паспорте.", title=None):
    """Параметры, которые пользователь поменял сам (паспорт, «Все настройки», слова, LLM).

    Значение проверяет STE Slicer: что он не принял — откатывается с причиной. Принятое
    запоминается в overrides и переживает смену варианта; «Вернуть исходные» его убирает.
    """
    from . import plan as P
    st = run.st
    info = setting_infos(run, values)
    unknown = [k for k in values if k not in info]
    if unknown:
        raise ValueError("В STE Slicer нет параметра %s" % ", ".join(unknown))
    values = dict((k, coerce_value(info[k], v)) for k, v in values.items())
    locked = [r for r in st.get("rows", []) if r.get("key") in values and r.get("lock")]
    if locked:
        raise ValueError("«%s» задано ТЗ: %s" % (locked[0]["k"], locked[0]["why"]))
    if P.in_plan(run.part):  # до подтверждения — правка плана, в слайсер пока ничего не пишем
        P.set_settings(run, values)
        return []
    before = W.snapshot(run)
    combos = combo_warnings(st)
    names = ", ".join("«%s»" % info[k]["label"] for k in values)
    s = run.step("set_settings", title or "Изменил %s" % names, values)
    changes = run.bridge.set_settings(values)
    rejected = W.check_settings(run, changes)
    changes = [c for c in changes if c[0] not in rejected]
    ov = st.setdefault("overrides", {})
    applied = st.setdefault("settings_applied", {})
    rows_by = dict((r["key"], r) for r in st.get("rows", []) if r.get("key"))
    diff = []
    for k, old, new in changes:
        ov[k] = new
        applied[k] = new
        r = rows_by.get(k)
        if r:
            r["value"], r["v"], r["src"], r["why"] = new, show_value(new, info[k]), "words", why
        diff.append([info[k]["label"], show_value(old, info[k]), show_value(new, info[k]), why.rstrip(".")])
    run.done(s, ", ".join("%s %s" % (d[0], d[2]) for d in diff) if diff else "Без изменений", "", changes)
    refresh_values(run)
    warn_new_combos(run, combos)
    if diff and before["state"].get("totals"):
        st.pop("totals", None)
        W.slice_part(run, card=False)  # время и масса — только из новой нарезки
    if diff:
        W.diff_block(run, before, diff, "Изменил: " + ", ".join(d[0].lower() for d in diff))
    return diff


def reset_user_settings(run, keys=None):
    """«Вернуть исходные»: убрать правки пользователя (все или одну) — снова план ассистента."""
    from . import plan as P
    st = run.st
    ov = st.get("overrides") or {}
    keys = [k for k in (keys or list(ov)) if k in ov]
    if not keys:
        raise ValueError("Параметры и так исходные")
    if P.in_plan(run.part):
        for k in keys:
            ov.pop(k, None)
        P.build(run)
        return []
    before = W.snapshot(run)
    info = setting_infos(run, keys)
    old = run.bridge.get_settings(keys)
    for k in keys:
        ov.pop(k, None)
    W.apply_settings(run, title="Вернул исходные параметры")  # чужие плану ключи слайсер вернёт к профилю
    new = run.bridge.get_settings(keys)
    refresh_values(run)
    diff = [[(info.get(k) or {}).get("label", k), show_value(old.get(k), info.get(k)), show_value(new.get(k), info.get(k)),
             "Исходное значение"] for k in keys if old.get(k) != new.get(k)]
    if before["state"].get("totals"):
        W.slice_part(run, card=False)
    W.diff_block(run, before, diff, "Вернул исходные параметры" if len(keys) > 1 else "Вернул исходное значение")
    return diff


def combo_values(st):
    """Значения для проверки сочетаний: план, затем то, что сейчас в слайсере, затем правки пользователя."""
    vals = rules.settings_dict(st.get("rows") or [])
    for k, i in (st.get("slicer_values") or {}).items():
        if i.get("value") is not None:
            vals[k] = i["value"]
    vals.update(st.get("overrides") or {})
    return vals


def combo_warnings(st):
    m = M.by_id(st.get("material_id") or "") or {}
    return rules.combo_warnings(st.get("geo"), st.get("mode", "classic"), m, combo_values(st), st.get("quantity", 1))


def warn_new_combos(run, before):
    """После правки — сказать о сочетании, которого раньше не было (одно сообщение на правку)."""
    seen = set(w["text"] for w in before)
    new = [w for w in combo_warnings(run.st) if w["text"] not in seen]
    if new:
        bad = any(w["kind"] == "bad" for w in new)
        b = run.notice("warn", "Так печатать нельзя" if bad else "Проверьте сочетание", " ".join(w["text"] for w in new))
        b["combos"] = new  # у каждого — кнопки исправления, пока сочетание ещё есть
    return new


def apply_fix(run, wid, index):
    """Кнопка в предупреждении о сочетании: применить готовое исправление.

    Сочетания проверяются заново: если предупреждения уже нет (исправили иначе), ничего не меняем.
    Исправление идёт через те же инструменты, что и правка словами, — с проверкой STE Slicer,
    перенарезкой и карточкой «Было → стало», которую можно отменить.
    """
    from . import tools as T
    w = [x for x in combo_warnings(run.st) if x.get("id") == wid]
    if not w:
        raise ValueError("Этого предупреждения уже нет — сочетание исправлено")
    fixes = w[0].get("fixes") or []
    try:
        fx = fixes[int(index)]
    except (TypeError, ValueError, IndexError):
        raise ValueError("Нет такого исправления")
    why = "Исправил сочетание: %s" % w[0]["text"].split(" — ")[0].rstrip(".")
    if "mode" in fx:
        return T.execute(run, "set_printing_mode", {"mode": fx["mode"]})
    if "quantity" in fx:
        return T.execute(run, "set_quantity", {"count": int(fx["quantity"])})
    return T.execute(run, "set_settings", {"values": dict(fx.get("set") or {}), "reason": why})


def list_settings(bridge, query=""):
    if not hasattr(bridge, "list_settings"):
        return {"items": [], "total": 0}
    res = bridge.list_settings(query or "")
    for it in res.get("items", []):
        it["text"] = show_value(it.get("value"), it)
    return res
