# -*- coding: utf-8 -*-
"""Инструменты ассистента: схемы для LLM и MCP и их выполнение.

Один и тот же список отдаётся LLM (tools в Chat Completions) и внешним клиентам
по MCP (tools/list). Выполнение всегда идёт через workflow, поэтому результат
одинаков, кто бы ни вызвал инструмент.
"""
import json

from . import materials as M
from . import requirements as R
from . import rules
from . import plan as P
from . import workflow as W

MATERIAL_IDS = [m["id"] for m in M.MATERIALS]


def _obj(props, required=None):
    return {"type": "object", "properties": props, "required": required or []}


REQ_ITEM = _obj({
    "kind": {"type": "string", "enum": ["must", "ban", "want"], "description": "must — обязательно, ban — запрещено, want — желательно"},
    "tag": {"type": "string", "enum": R.TAG_KEYS, "description": "Свойство материала из словаря"},
    "value": {"type": "number", "description": "Число для tag с порогом: температура °C, плотность г/см³, модуль МПа, водопоглощение %"},
    "text": {"type": "string", "description": "Требование коротко по-русски"},
    "quote": {"type": "string", "description": "Цитата из ТЗ или слов пользователя"},
    "clause": {"type": "string", "description": "Пункт ТЗ, например «п. 3.4»"},
}, ["kind", "tag"])

TAGS_HELP = "; ".join("%s — %s" % (t[0], t[1]) for t in R.TAGS)

SPECS = [
    ("prepare_part", "Подготовка новой детали по приложенным файлам и словам пользователя: загрузить модель, "
     "прочитать ТЗ, задать вопросы двумя карточками (условия работы, затем как делать: материал, 3D/5D, положение, "
     "поддержки — с рекомендацией), затем показать план печати. Не режет: нарезка — только после подтверждения "
     "плана пользователем. Вызывай один раз для новой детали; что пользователь уже сказал — передай, это не спросят.",
     _obj({"material": {"type": "string", "enum": MATERIAL_IDS, "description": "Только если пользователь сам назвал материал"},
           "mode": {"type": "string", "enum": ["3D", "5D"], "description": "Только если пользователь сам назвал режим"},
           "variant": {"type": "string", "enum": ["fast", "cheap", "quality"], "description": "Быстро, дёшево или качественно"},
           "quantity": {"type": "integer", "description": "Сколько штук напечатать"},
           "strength": {"type": "string", "enum": ["normal", "high", "max"], "description": "Прочность, если пользователь сказал"},
           "surface": {"type": "string", "enum": ["look", "size", "any"], "description": "Что важнее: внешний вид, точные размеры или не важно"},
           "supports": {"type": "string", "enum": ["everywhere", "buildplate", "none"], "description": "Поддержки, если пользователь сказал"},
           "requirements": {"type": "array", "items": REQ_ITEM, "description": "Требования из слов пользователя или фото, если ТЗ нет"}})),
    ("confirm_plan", "Подтвердить план печати и нарезать. Только если пользователь сам подтвердил план "
     "(«да», «подтверждаю», «нарезай»).", _obj({})),
    ("get_state", "Текущее состояние детали: файлы, геометрия, требования, материал, режим, настройки, итоги нарезки.", _obj({})),
    ("read_spec", "Текст ТЗ из приложенного DOCX или TXT и найденные в нём требования.",
     _obj({"file_id": {"type": "string"}}, ["file_id"])),
    ("set_requirements", "Задать или уточнить требования к детали (из ТЗ, слов, фото) и заново подобрать материал. "
     "Словарь tag: " + TAGS_HELP,
     _obj({"requirements": {"type": "array", "items": REQ_ITEM}, "replace": {"type": "boolean"}}, ["requirements"])),
    ("select_material", "Подобрать материал по требованиям или поставить названный пользователем (проверив его по ТЗ).",
     _obj({"material_id": {"type": "string", "enum": MATERIAL_IDS}})),
    ("set_printing_mode", "Переключить режим: 3D (classic) или 5D (cylindrical_full / cylindrical). Перекладывает деталь и пересчитывает настройки.",
     _obj({"mode": {"type": "string", "enum": ["3D", "5D"]}}, ["mode"])),
    ("explain_setting", "Описание параметров из определений STE Slicer: подпись, описание, единицы, допустимые значения. "
     "Вызывай, прежде чем объяснять параметр пользователю, — цитируй, не выдумывай.",
     _obj({"keys": {"type": "array", "items": {"type": "string"}}}, ["keys"])),
    ("lay_flat", "«Положить плашмя» средствами STE Slicer (LayFlatOperation): деталь ложится на самую большую плоскую грань.", _obj({})),
    ("get_settings", "Значения настроек STE Slicer по ключам.",
     _obj({"keys": {"type": "array", "items": {"type": "string"}}}, ["keys"])),
    ("set_settings", "Изменить настройки STE Slicer по ключам (например infill_sparse_density, wall_line_count, support_enable, "
     "layer_height, adhesion_type, brim_width, speed_print, material_print_temperature, cool_fan_speed, retraction_amount). Значение проверяет STE Slicer. Показывает пользователю «было → стало».",
     _obj({"values": {"type": "object", "description": "{ключ: значение}"}, "reason": {"type": "string"}}, ["values", "reason"])),
    ("list_slicer_settings", "Найти параметры STE Slicer по слову (ключ, подпись или раздел: «скорость», «температура», "
     "«откат», «обдув»). Пустой запрос — параметры, которые уже изменены вручную.",
     _obj({"query": {"type": "string"}}, [])),
    ("reset_settings", "Вернуть исходные параметры: убрать правки пользователя (все или по ключам) — снова план ассистента.",
     _obj({"keys": {"type": "array", "items": {"type": "string"}}}, [])),
    ("slice", "Нарезать в STE Slicer и вернуть время, массу, стоимость и проверки.", _obj({})),
    ("show_variants", "Посчитать пробной нарезкой три варианта: быстро, дёшево, качественно — и показать карточки.", _obj({})),
    ("apply_variant", "Применить вариант печати и показать «было → стало».",
     _obj({"variant": {"type": "string", "enum": ["fast", "cheap", "quality"]}}, ["variant"])),
    ("set_quantity", "Сделать копии детали для партии и расставить их на столе.",
     _obj({"count": {"type": "integer"}}, ["count"])),
    ("rotate_model", "Повернуть модель вокруг оси сцены (Y — вверх) и положить на стол.",
     _obj({"axis": {"type": "string", "enum": ["X", "Y", "Z"]}, "degrees": {"type": "number"}}, ["axis", "degrees"])),
    ("ask_user", "Задать пользователю уточняющий вопрос с вариантами ответа (как делать: материал, режим, положение, "
     "поддержки, прочность). Первым вариантом — рекомендованный. После вызова жди ответа, не продолжай.",
     _obj({"question": {"type": "string"}, "options": {"type": "array", "items": {"type": "string"}},
           "why": {"type": "string", "description": "На что повлияет ответ"},
           "default": {"type": "string", "description": "Ответ по умолчанию, коротко, например «в помещении, без нагрузки»"},
           "multiple": {"type": "boolean", "description": "Можно выбрать несколько вариантов (ответ придёт через «; »)"}}, ["question", "options"])),
    ("undo_last", "Отменить последнее изменение («было → стало»).", _obj({})),
    ("save_files", "Сохранить проект 3MF и/или G-code в папку из настроек и добавить деталь в картотеку.",
     _obj({"project": {"type": "boolean"}, "gcode": {"type": "boolean"}})),
    ("catalog_search", "Найти детали в картотеке по имени, материалу или режиму.",
     _obj({"query": {"type": "string"}}, ["query"])),
]

TITLES = dict((s[0], s[1]) for s in SPECS)


def llm_tools():
    return [{"type": "function", "function": {"name": n, "description": d, "parameters": p}} for n, d, p in SPECS]


def mcp_tools():
    out = [{"name": n, "description": d, "inputSchema": p} for n, d, p in SPECS if n != "ask_user"]
    out.append({"name": "load_file", "description": "Приложить к детали файл с этого компьютера (модель, ТЗ, фото) по полному пути.",
                "inputSchema": _obj({"path": {"type": "string"}}, ["path"])})
    return out


class AskPending(Exception):
    """Инструмент задал вопрос: ход агента заканчивается до ответа пользователя."""


def state_summary(part):
    st = part.get("state") or {}
    geo = st.get("geo") or {}
    out = {
        "part": part.get("name"), "status": part.get("status"),
        "files": [{"id": f["id"], "name": f["name"], "kind": f["kind"]} for f in part.get("files", [])],
        "model_loaded": bool(st.get("node")),
        "geometry": {k: geo.get(k) for k in ("size_mm", "volume_cm3", "part_type", "rotation_axis", "bore_mm",
                                               "overhang_5d_mm2", "mean_wall_mm")} if geo else None,
        "spec": st.get("spec_file"),
        "requirements": [{"kind": r["kind"], "tag": r["tag"], "value": r.get("value"), "text": r["text"]} for r in st.get("requirements", [])],
        "material": st.get("material_id"), "material_reason": st.get("material_reason"),
        "material_plain": (M.by_id(st.get("material_id") or "") or {}).get("plain"),
        "prefs": st.get("prefs"), "phase": st.get("phase"),
        "rejected_materials": st.get("rejected_materials"),
        "mode": st.get("mode"), "base_diameter": st.get("base"), "mode_reason": st.get("mode_reason"),
        "orientation": st.get("orientation"), "variant": st.get("variant"), "quantity": st.get("quantity", 1),
        "settings": [{"key": r["key"], "label": r["k"], "value": r["v"], "source": W.SRC_WORDS.get(r["src"]), "why": r["why"]}
                     for r in st.get("rows", []) if r["k"]],
        "totals": dict((k, st["totals"][k]) for k in ("time", "mass", "cost")) if st.get("totals") else None,
        "variants": dict((k, {"time": v["time"], "cost": v["cost"], "layer": v["layer"]}) for k, v in st.get("variants", {}).items()) or None,
        "questions_asked": st.get("asked", 0),
        "slice_error": st.get("slice_error"),
    }
    return out


PLAN_TOOLS = ("select_material", "set_printing_mode", "apply_variant", "set_quantity", "set_requirements",
              "set_settings", "reset_settings", "slice", "show_variants")


def plan_summary(part):
    st = part.get("state") or {}
    return [{"k": r["k"], "v": r["v"], "why": r["why"]} for r in st.get("plan_rows", [])]


def plan_tool(run, name, args):
    """Пока план не подтверждён, правки меняют план, а нарезки нет."""
    if name in ("slice", "show_variants"):
        return {"ok": False, "error": "План печати ещё не подтверждён. Попроси пользователя проверить план и подтвердить; "
                                      "после «да» вызови confirm_plan."}
    if name == "select_material":
        P.edit(run, "material", (M.by_id(args.get("material_id") or "") or {}).get("name", ""))
    elif name == "set_printing_mode":
        P.edit(run, "mode", args.get("mode"))
    elif name == "apply_variant":
        P.edit(run, "variant", args.get("variant"))
    elif name == "set_quantity":
        P.edit(run, "quantity", str(args.get("count")))
    elif name == "set_requirements":
        P.set_requirements(run, R.normalize(args.get("requirements", []), source="words"))
    elif name == "set_settings":
        W.set_user_settings(run, args.get("values") or {})
    elif name == "reset_settings":
        W.reset_user_settings(run, args.get("keys") or None)
    return {"plan": plan_summary(run.part), "changed": [r["k"] for r in run.st.get("plan_rows", []) if r.get("changed")]}


def execute(run, name, args):
    """Выполнить инструмент. Возвращает короткий JSON-совместимый результат для LLM."""
    st = run.st
    args = args or {}
    if name == "prepare_part":
        words = {"material": args.get("material"), "mode": args.get("mode"), "variant": args.get("variant"),
                 "quantity": args.get("quantity")}
        prefs = dict((k, args[k]) for k in ("strength", "surface", "supports") if args.get(k))
        if prefs:
            st.setdefault("prefs", {}).update(prefs)
        if args.get("requirements"):
            words["requirements"] = R.normalize(args["requirements"], source="words")
        words = dict((k, v) for k, v in words.items() if v)
        res = W.prepare(run, words)
        if res == "asked":
            raise AskPending()
        if res == "plan":
            return {"plan": plan_summary(run.part),
                    "next": "План показан карточкой. Коротко поясни 1–2 главных решения и попроси подтвердить; не режь сам."}
        return {"ok": True, "state": state_summary(run.part)}
    if name == "confirm_plan":
        t = P.confirm(run)
        if not t:
            return {"ok": False, "slice_error": run.st.get("slice_error")}
        return {"ok": True, "totals": dict((k, t[k]) for k in ("time", "mass", "cost"))}
    if P.in_plan(run.part) and name in PLAN_TOOLS:
        return plan_tool(run, name, args)
    if name == "get_state":
        return state_summary(run.part)
    if name == "read_spec":
        text = W.read_spec(run, args["file_id"])
        return {"text": text[:8000], "found_requirements": [{"kind": r["kind"], "tag": r["tag"], "value": r.get("value"), "text": r["text"]}
                                                           for r in st.get("requirements", [])]}
    if name == "set_requirements":
        had = bool(st.get("totals"))
        W.set_requirements(run, args.get("requirements", []), replace=bool(args.get("replace")))
        W.show_requirements(run)
        if st.get("node") and st.get("geo"):
            W.select_material(run)
            W.apply_settings(run)
            if had:
                W.slice_part(run, card=False)
        return {"ok": True, "material": st.get("material_id"), "requirements": len(st.get("requirements", []))}
    if name == "select_material":
        mid = args.get("material_id")
        if mid:
            st["fixed_material"] = mid
        had = bool(st.get("totals"))
        m = W.select_material(run, fixed=mid)
        if st.get("geo"):
            W.apply_settings(run)
            if had:
                W.slice_part(run, card=False)
        return {"material": m["id"], "name": m["name"], "reason": st.get("material_reason")}
    if name == "set_printing_mode":
        before = W.snapshot(run)
        W.choose_mode(run, forced=args["mode"])
        W.orient(run)
        W.apply_settings(run)
        if before["state"].get("totals"):
            W.slice_part(run, card=False)
        W.diff_block(run, before, [["Режим", rules.MODE_TITLE.get(before["state"].get("mode", "classic")),
                                    rules.MODE_TITLE[st["mode"]], "Вы попросили %s" % args["mode"]]], "Сменил режим")
        return {"mode": st["mode"], "base": st.get("base"), "reason": st.get("mode_reason")}
    if name == "get_settings":
        return run.bridge.get_settings(args.get("keys") or [])
    if name == "set_settings":
        why = (args.get("reason") or "Вы попросили").rstrip(".") + "."
        diff = W.set_user_settings(run, args.get("values") or {}, why=why)
        return {"changed": [[d[0], d[1], d[2]] for d in diff]}
    if name == "reset_settings":
        diff = W.reset_user_settings(run, args.get("keys") or None)
        return {"reset": [[d[0], d[1], d[2]] for d in diff]}
    if name == "list_slicer_settings":
        res = W.list_settings(run.bridge, args.get("query") or "")
        return {"total": res["total"], "items": [dict((f, it.get(f)) for f in ("key", "label", "category", "text", "min", "max",
                                                                               "options", "changed"))
                                                 for it in res["items"][:30]]}
    if name == "slice":
        t = W.slice_part(run)
        if not t:
            return {"ok": False, "slice_error": run.st.get("slice_error")}
        return {"ok": True, "totals": dict((k, t[k]) for k in ("time", "mass", "cost"))}
    if name == "show_variants":
        v = W.variants(run)
        return dict((k, {"time": x["time"], "mass": x["mass"], "cost": x["cost"], "layer": x["layer"]}) for k, x in v.items())
    if name == "apply_variant":
        rows = W.set_variant(run, args["variant"])
        return {"changed": rows, "totals": state_summary(run.part)["totals"]}
    if name == "set_quantity":
        W.multiply(run, int(args["count"]))
        return {"quantity": int(args["count"])}
    if name == "explain_setting":
        return run.bridge.setting_info([str(k) for k in args.get("keys") or []])
    if name == "lay_flat":
        if not st.get("node"):
            raise ValueError("Сначала приложите модель")
        had = bool(st.get("totals"))
        st.pop("totals", None)
        s = run.step("lay_flat", "Положил плашмя", {})
        run.bridge.lay_flat(st["node"])
        run.bridge.place(st["node"], center=st.get("mode", "classic") != "classic")
        from . import geometry as G
        st["geo"] = G.analyze(run.bridge.mesh(st["node"]))
        # Поворот сделал сам слайсер — от этого положения и считаем дальше.
        st["geo_file"] = st["geo"]
        st["pose"] = [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]]
        st["orientation"] = "плашмя (STE Slicer)"
        cur = [c for c in st["geo"]["orientations"] if c["down"] == "-Y"]
        run.done(s, "Нависания %d мм²" % (cur[0]["overhang_mm2"] if cur else 0))
        if had:
            W.slice_part(run, card=False)
        return {"overhang_mm2": cur[0]["overhang_mm2"] if cur else None}
    if name == "rotate_model":
        if not st.get("node"):
            raise ValueError("Сначала приложите модель")
        if args.get("axis") not in ("X", "Y", "Z"):
            raise ValueError("Ось поворота — X, Y или Z")
        had = bool(st.get("totals"))
        st.pop("totals", None)  # деталь легла иначе — прежняя нарезка не верна
        s = run.step("rotate_model", "Повернул модель", args)
        run.bridge.rotate(st["node"], args["axis"], float(args["degrees"]))
        run.bridge.place(st["node"], center=st.get("mode", "classic") != "classic")
        from . import geometry as G
        import numpy as np
        st["pose"] = G.rot_matrix(rules.AXIS_VEC[args["axis"]], float(args["degrees"])).dot(
            np.array(st.get("pose") or np.eye(3))).tolist()
        st["geo"] = G.analyze(run.bridge.mesh(st["node"]))
        st["orientation"] = "повёрнута вручную"
        over = st["geo"]["orientations"]
        cur = [c for c in over if c["down"] == "-Y"]
        run.done(s, "Нависания %d мм²" % (cur[0]["overhang_mm2"] if cur else 0))
        if cur and cur[0]["overhang_mm2"] > over[0]["overhang_mm2"] + 300:
            run.notice("warn", "В этом положении больше нависаний",
                       "Нависания %d мм² против %d мм² в лучшем положении — понадобятся поддержки." % (cur[0]["overhang_mm2"], over[0]["overhang_mm2"]))
        if had:
            W.slice_part(run, card=False)
        return {"overhang_mm2": cur[0]["overhang_mm2"] if cur else None}
    if name == "ask_user":
        W.ask(run, args.get("question"), args.get("options"), args.get("why"), args.get("default"), multi=bool(args.get("multiple")))
        raise AskPending()
    if name == "undo_last":
        last = None
        for m in run.part["messages"]:
            for b in m.get("blocks", []) or []:
                if b.get("type") == "diff" and not b.get("undone"):
                    last = b
        if not last:
            return {"ok": False, "error": "Нечего отменять"}
        W.undo(run, last["id"])
        return {"ok": True}
    if name == "save_files":
        paths, rec = W.save(run, project=args.get("project", True), gcode=args.get("gcode", True))
        return {"paths": paths, "catalog_id": rec["id"]}
    if name == "catalog_search":
        recs = run.app.catalog.search(args.get("query", ""))
        return [{"id": r["id"], "name": r["name"], "mode": r.get("mode"), "material": r.get("material"),
                 "infill": r.get("infill"), "state": r.get("state"), "note": r.get("note", "")} for r in recs]
    if name == "load_file":
        fi = run.store.add_file_from_path(args["path"])
        run.part["files"].append(dict((k, fi[k]) for k in ("id", "name", "size", "kind")))
        return {"file_id": fi["id"], "kind": fi["kind"]}
    raise ValueError("Неизвестный инструмент %s" % name)


def compact(result, limit=6000):
    s = json.dumps(result, ensure_ascii=False, default=str)
    if len(s) > limit:
        s = s[:limit] + "…"
    return s
