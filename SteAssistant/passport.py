# -*- coding: utf-8 -*-
"""Паспорт печати, итоговый текст и подсказки после хода."""

from . import materials as M
from . import rules


class _Workflow(object):
    """Остальной сценарий — через workflow при вызове: модули ссылаются друг на друга."""

    def __getattr__(self, name):
        from . import workflow
        return getattr(workflow, name)


W = _Workflow()


# ============================================================ паспорт

def build_passport(part, app):
    st = part.get("state") or {}
    if not st.get("material_id"):
        part["passport"] = None
        return None
    m = M.by_id(st["material_id"])
    mode = st.get("mode", "classic")
    sv = st.get("slicer_values") or {}
    ov = st.get("overrides") or {}

    def edit(key, value):
        i = sv.get(key)
        if key == "printing_mode":
            return {"type": "mode", "value": rules.MODE_SHORT.get(mode, "3D")}
        if not i or i.get("type") not in ("float", "int", "bool", "enum"):
            return None
        return {"type": i["type"], "unit": W.UNITS.get(i.get("unit") or "", i.get("unit") or ""), "min": i.get("min"),
                "max": i.get("max"), "options": i.get("options") or [], "value": value}

    rows = [{"k": "Материал", "v": m["name"], "src": st.get("material_src", "tz"), "why": st.get("material_reason", "")}]
    for r in st.get("rows", []):
        if not r.get("k"):
            continue
        i = sv.get(r["key"]) or {}
        k, v = r["k"], r["v"]
        if r["key"] and k == r["key"] and i.get("label"):  # ключ, названный словами, — подписью слайсера
            k, v = i["label"], W.show_value(r["value"], i)
        rows.append({"k": k, "v": v, "src": r["src"], "why": r["why"], "key": r["key"], "lock": bool(r.get("lock")),
                     "changed": r["key"] in ov, "edit": None if r.get("lock") else edit(r["key"], r["value"])})
    shown = set(r.get("key") for r in rows)
    params = []
    for k in W.PRINT_KEYS + sorted(x for x in ov if x not in W.PRINT_KEYS):
        i = sv.get(k)
        if k in shown or not i:
            continue
        val = ov.get(k, i.get("value"))
        params.append({"key": k, "k": i.get("label") or k, "v": W.show_value(val, i), "changed": k in ov, "src": "profile",
                       "why": i.get("description") or "", "edit": edit(k, val)})
    if params:  # строка-сводка «Профиль материала» не нужна: те же значения — по отдельности ниже
        rows = [r for r in rows if not (r.get("src") == "profile" and not r.get("key"))]
    if st.get("orientation"):
        rows.append({"k": "Положение", "v": st["orientation"][:1].lower() + st["orientation"][1:], "src": "geom",
                     "why": "Выбрано по нависаниям и опоре на столе."})
    if st.get("quantity", 1) > 1:
        rows.append({"k": "Копии", "v": "%d шт." % st["quantity"], "src": "tz", "why": "Партия расставлена слайсером на одном столе."})
    assumptions = []
    if st.get("material_src") == "assume":
        assumptions.append({"id": "material", "key": "Материал", "value": m["name"],
                            "text": "Материал %s — нагрузка и среда неизвестны." % m["name"],
                            "options": [m["name"]] + [x for x in ("EnduSe PETG", "EnduSe PA6", "FiberPart ABS/PA G8") if x != m["name"]][:2]})
    for r in st.get("rows", []):
        if r["src"] == "assume" and r["k"]:
            opts = {"Заполнение": ["40 %", "60 %", "90 %", "100 %"]}.get(r["k"], [r["v"]])
            if r["v"] not in opts:
                opts = [r["v"]] + opts
            assumptions.append({"id": r["key"], "key": r["k"], "value": r["v"], "text": "%s %s: %s" % (r["k"], r["v"], r["why"]),
                                "options": opts[:3] if r["v"] in opts[:3] else [opts[0]] + opts[1:3]})
    rejected = list(st.get("rejected_materials", []))
    if mode == "classic" and (st.get("geo") or {}).get("part_type", "").startswith("rotation"):
        rejected.append("5D — " + st.get("mode_reason", ""))
    elif mode != "classic":
        rejected.append("3D classic — " + "больше нависаний, слои поперёк оси")
    status = part.get("status", "draft")
    pstatus = {"sliced": "sliced", "saved": "saved", "work": "work", "failed": "failed"}.get(status, "draft")
    qual = [r for r in st.get("rows", []) if r["key"] == "layer_height"]
    v = st.get("variants")
    part["passport"] = {
        "status": pstatus, "mode": mode, "modeShort": rules.MODE_SHORT[mode], "printer": app.printer_name(),
        "material": m["name"], "quality": "слой " + (qual[0]["v"] if qual else "—"), "variant": st.get("variant", "quality"),
        "mix": rules.blend((st.get("prefs") or {})["mix"])["title"] if (st.get("prefs") or {}).get("mix") else None,
        "rows": rows, "params": params, "overridden": sorted(ov), "warnings": W.combo_warnings(st), "assumptions": assumptions, "rejected": rejected, "prep": M.preparation(m),
        "totals": dict((k, st["totals"][k]) for k in ("time", "mass", "cost")) if st.get("totals") else None,
        "variants": dict((k, {"layer": x["layer"], "time": x["time"], "mass": x["mass"], "cost": x["cost"]}) for k, x in v.items()) if v else None,
        "locks": rules.locks(st.get("rows", []), m) if st.get("rows") else [],
        "conflict": None,
        "failed": st.get("slice_error"),
    }
    return part["passport"]


def summary_text(part):
    """Итог автопилота человеческим языком (когда нет LLM или она не ответила)."""
    st = part.get("state") or {}
    if st.get("slice_error"):
        return "Параметры печати не показываю: без нарезки они не проверены. Исправьте причину по шагам в карточке выше и нажмите «Нарезать заново»."
    if not st.get("material_id"):
        return ""
    m = M.by_id(st["material_id"])
    mode = st.get("mode", "classic")
    lines = []
    if mode == "classic":
        lines.append("Печатаю **в 3D** из **%s**. %s" % (m["name"], st.get("mode_reason", "")))
    else:
        lines.append("Печатаю **в 5D (%s)** из **%s**, база Ø%s. %s" % (mode, m["name"], W._n(st.get("base") or 0),
                                                                         st.get("mode_reason", "")))
    if st.get("rejected_materials"):
        lines.append("Почти подошёл " + st["rejected_materials"][0].split(";")[0] + ".")
    assumes = [r for r in st.get("rows", []) if r["src"] == "assume" and r["k"]]
    if st.get("material_src") == "assume":
        lines.append("Материал выбран без данных о нагрузке — это допущение, его можно поменять в паспорте.")
    if assumes:
        lines.append("Допущения: " + ", ".join("%s %s" % (r["k"].lower(), r["v"]) for r in assumes) + " — поменять можно в паспорте справа.")
    if st.get("totals"):
        t = st["totals"]
        lines.append("Нарезано: %s, %s, %s." % (t["time"], t["mass"], t["cost"]))
    return "\n\n".join(lines)


def suggestions(part):
    st = part.get("state") or {}
    if part.get("status") in ("ask", "plan"):
        return []
    if not st.get("node"):
        return []
    if st.get("slice_error"):
        return [a[1] for a in st["slice_error"]["actions"]]
    out = []
    if not st.get("totals"):
        out.append("Нарежь")
    v = st.get("variant", "quality")
    if v != "cheap":
        out.append("Сделай дешевле")
    out.append("Покажи варианты печати")
    if st.get("mode", "classic") != "classic":
        out.append("Почему 5D, а не 3D?")
    else:
        out.append("Почему такое положение?")
    if st.get("totals"):
        out.append("Сохрани проект")
    if part.get("status") == "saved":  # напечатали — можно разобрать результат по фото
        out = ["Разобрать печать по фото"] + out
    return out[:4]
