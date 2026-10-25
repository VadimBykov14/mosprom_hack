# -*- coding: utf-8 -*-
"""Правила: материал, режим 3D/5D, ориентация, настройки, варианты печати.

LLM здесь ничего не решает: правила детерминированы, одни и те же данные дают
один и тот же результат. LLM понимает текст, вызывает инструменты и объясняет.
"""
import math

from . import materials as M
from .requirements import TAG_INFO

# ---------------------------------------------------------------- материал

STATUS_WORD = {"yes": "да", "no": "нет", "nd": "нет данных"}
LINE_PRIORITY = {"EnduSe": 0, "FiberPart": 0, "Sealant": 0, "Proto": 1}


def evaluate(material, req):
    """Проверка одного требования: ("yes" | "no" | "nd", источник, пояснение)."""
    info = TAG_INFO.get(req["tag"])
    if not info or info["check"] == "none":
        return "nd", None, ""
    check = info["check"]
    if check == "flag":
        value, src = M.flag(material, req["tag"])
        if value is None:
            return "nd", None, ""
        return ("yes" if value else "no"), src, ""
    kind, field = check.split(":")
    actual = material.get(field)
    want = req.get("value")
    if actual is None or want is None:
        return "nd", None, ""
    if kind == "min":
        ok = actual > want if req["tag"] == "temp_peak_min" else actual >= want
    else:
        ok = actual <= want
    return ("yes" if ok else "no"), "handbook", "%g" % actual


def score(material, reqs):
    res = {"violations": [], "unknown": [], "want_yes": 0, "profile_yes": 0, "cells": []}
    for r in reqs:
        st, src, note = evaluate(material, r)
        res["cells"].append((st, src, note))
        if TAG_INFO.get(r["tag"], {}).get("check") == "none":
            continue
        if r["kind"] in ("must", "ban"):
            if st == "no":
                res["violations"].append(r)
            elif st == "nd":
                res["unknown"].append(r)
            elif src == "profile":
                res["profile_yes"] += 1
        elif st == "yes":
            res["want_yes"] += 1
    return res


def _rank_key(material, s):
    # Нарушение весит как четыре «нет данных»: материал, о котором почти ничего не известно,
    # не должен обходить проверенный материал с одним спорным пунктом.
    cost = 4 * len(s["violations"]) + len(s["unknown"])
    return (cost, len(s["violations"]), -s["want_yes"], -s["profile_yes"],
            LINE_PRIORITY.get(material["line"], 2))


def select_material(reqs, fixed=None):
    """Подбор материала. fixed — id материала, названного пользователем (проверяем, но не меняем)."""
    if not reqs:
        m = M.by_id("enduse_petg")
        return {"pick": m, "reason": "Требований нет — универсальный материал для корпусов и держателей.",
                "source": "def", "ranking": [], "cols": [], "rows": [], "almost": [], "conflict": None}
    scored = []
    for m in M.candidates():  # только материалы, профили которых есть у текущего принтера
        scored.append((m, score(m, reqs)))
    scored.sort(key=lambda x: _rank_key(x[0], x[1]))
    best, best_s = scored[0]
    conflict = None
    if fixed:
        fm = M.by_id(fixed)
        if fm:
            fs = score(fm, reqs)
            if fs["violations"]:
                conflict = {"material": fm["name"], "violations": [_viol(v) for v in fs["violations"]]}
            best, best_s = fm, fs
    # Почти подошли: нарушают 1–2 обязательных, но проходят больше всего остального.
    almost = []
    for m, s in scored[1:]:
        if m["id"] == best["id"]:
            continue
        if 1 <= len(s["violations"]) <= 2:
            passed = sum(1 for c in s["cells"] if c[0] == "yes")
            almost.append((passed, m, s))
    almost.sort(key=lambda x: (-x[0], len(x[2]["violations"])))
    almost = almost[:2]
    # Столбцы таблицы: выбранный, «почти подошедшие», затем следующий по рейтингу.
    cols = [(best, best_s)] + [(a[1], a[2]) for a in almost]
    for m, s in scored:
        if len(cols) >= 4:
            break
        if all(m["id"] != c[0]["id"] for c in cols):
            cols.append((m, s))
    rows = []
    for i, r in enumerate(reqs):
        rows.append({"text": r["text"], "kind": r["kind"], "clause": r.get("clause", ""),
                     "cells": [c[1]["cells"][i][0] for c in cols]})
    reason = _material_reason(best, best_s, reqs)
    return {
        "pick": best, "pick_score": best_s, "reason": reason,
        "source": "words" if fixed else "tz",
        "ranking": [(m["id"], _rank_key(m, s)) for m, s in scored[:6]],
        "cols": [c[0]["name"] for c in cols], "rows": rows,
        "almost": [{"name": a[1]["name"], "why": "; ".join(_viol(v) for v in a[2]["violations"])} for a in almost],
        "conflict": conflict,
        "all_fail": bool(best_s["violations"]) and not fixed,
    }


def _viol(r):
    t = r["text"]
    if r["kind"] == "ban":
        t = "нарушает запрет: " + t.lower()
    else:
        t = "не проходит: " + t.lower()
    if r.get("clause"):
        t += " (%s)" % r["clause"]
    return t


def _material_reason(m, s, reqs):
    must = [r for r in reqs if r["kind"] in ("must", "ban")]
    ok = len(must) - len(s["violations"]) - len(s["unknown"])
    parts = ["Проходит %d из %d обязательных требований" % (ok, len(must))]
    if s["unknown"]:
        parts.append("по %d нет данных" % len(s["unknown"]))
    if s["violations"]:
        parts.append("нарушает %d — подходящего материала нет" % len(s["violations"]))
    return ", ".join(parts) + ". " + m.get("note", "")


# ---------------------------------------------------------------- режим

# Как STE Slicer печатает 5D cylindrical_full (CuraEngineBackend/StartSliceJob и CLIParserBackend/StartSliceJob):
# модель режется цилиндром базы (cylindrical_mode_base_diameter) по вертикальной оси в центре стола.
# Что внутри базы, печатается обычными 3D-слоями от стола, остальное — слоями-цилиндрами вокруг оси.
# Поэтому сквозной канал не обязателен: сплошной вал тоже печатается в 5D (эксперты так и пометили вал Krones).
# Если канал есть, база = канал, и внутри базы печатать нечего. Без канала — база по умолчанию слайсера.
# Режим cylindrical (без «full») середину просто вырезает — автоматически его не выбираем.
MIN_BASE_5D = 4.0      # minimum_value в fdmprinter.def.json
DEFAULT_BASE_5D = 6.0  # default_value


def channel_mm(geo):
    """Диаметр сквозного канала по оси, мм (0 — канала нет)."""
    core = float(geo.get("core_mm") or 0)
    # STL хранит окружность многоугольником, а free_core меряет до его граней: канал Ø8 выходит 7,9.
    # Если вершины канала лежат на круге чуть шире (до 0,5 мм), диаметр канала — по вершинам.
    inner = geo.get("inner_min_mm")
    if core > 0 and inner and 0 <= inner - core <= 0.5:
        core = float(inner)
    bore = geo.get("bore_mm")
    return round(bore if bore and bore <= core + 0.5 else core, 1)


def five_d_base(geo):
    """Диаметр базы 5D: по сквозному каналу, если он есть, иначе по умолчанию STE Slicer. None — оси вращения нет."""
    if not (geo.get("part_type") or "").startswith("rotation"):
        return None
    ch = channel_mm(geo)
    return ch if ch >= MIN_BASE_5D - 0.05 else DEFAULT_BASE_5D


def base_words(geo, base):
    if base and channel_mm(geo) >= MIN_BASE_5D - 0.05:
        return "база Ø%s по каналу детали" % _n(base)
    return "база Ø%s: середина детали печатается обычными слоями" % _n(base or DEFAULT_BASE_5D)


def choose_mode(geo, reqs=None):
    """Режим печати: classic (3D) или cylindrical_full (5D) и диаметр базы.

    5D — для тела вращения, если в 5D заметно меньше нависаний, чем в лучшем положении 3D, или если
    нависаний примерно поровну, в 3D без поддержек не обойтись, а деталь передаёт крутящий момент
    (в 5D слои идут вокруг оси, и момент нагружает их вдоль, а не отрывает друг от друга).
    При почти равных нависаниях — confidence "low": выбор задаётся пользователю вопросом.
    """
    tags = set(r["tag"] for r in (reqs or []))
    best3d = geo["orientations"][0] if geo.get("orientations") else {"overhang_mm2": 0}
    o3d = best3d["overhang_mm2"]
    o5d = geo.get("overhang_5d_mm2")
    rot = (geo.get("part_type") or "").startswith("rotation")
    base = five_d_base(geo)
    out = {"mode": "classic", "base": None, "confidence": "high", "o3d": o3d, "o5d": o5d, "can5d": bool(base),
           "base5d": base}
    if not rot:
        out["reason"] = "Не тело вращения: в 5D слои ложатся вокруг оси, а оси у детали нет."
        return out
    better = o5d is not None and (o5d < 0.5 * o3d or (o5d < 50 and o3d >= 100))
    close = o5d is not None and not better and o5d < 1.25 * o3d + 200
    torque = close and o3d >= 150 and "torque_load" in tags
    if better:
        out.update(mode="cylindrical_full", base=base, confidence="high",
                   reason="Тело вращения: в 5D слои идут вокруг оси, нависаний %d мм² против %d мм² в 3D; %s."
                   % (o5d, o3d, base_words(geo, base)))
        return out
    if torque:
        out.update(mode="cylindrical_full", base=base, confidence="low",
                   reason="Деталь передаёт крутящий момент: в 5D слои идут вокруг оси, момент нагружает их вдоль, "
                          "а не отрывает друг от друга. Нависаний примерно поровну (%d мм² в 5D, %d мм² в 3D); %s."
                   % (o5d, o3d, base_words(geo, base)))
        return out
    out["confidence"] = "low" if close else "high"
    if close:
        out["reason"] = ("Тело вращения, но нависаний примерно поровну (%d мм² в 3D, %d мм² в 5D), а нагрузки, "
                         "ради которой стоит класть слои вокруг оси, в ТЗ нет — проще и быстрее в 3D." % (o3d, o5d or 0))
    else:
        out["reason"] = ("Тело вращения, но в 5D нависаний больше (%d мм² против %d мм² в 3D): "
                         "рабочие поверхности лучше печатаются вертикальными стенками." % (o5d or 0, o3d))
    return out


MODE_TITLE = {"classic": "3D · classic", "cylindrical": "5D · cylindrical", "cylindrical_full": "5D · cylindrical_full"}
MODE_SHORT = {"classic": "3D", "cylindrical": "5D", "cylindrical_full": "5D"}


def mode_compare(geo, mode, times=None):
    """Две колонки для карточки «3D или 5D»."""
    times = times or {}
    o3d = geo["orientations"][0]["overhang_mm2"]
    o5d = geo.get("overhang_5d_mm2") or 0
    base = five_d_base(geo)
    col3 = {"id": "3D", "title": "3D · classic", "time": times.get("classic"), "rows": []}
    col5 = {"id": "5D", "title": MODE_TITLE["cylindrical_full"], "time": times.get("5d"), "rows": []}
    # Поддержки — обычная часть печати, а не недостаток режима: без крестика.
    col3["rows"].append(["ok" if o3d < 150 else "info", "Поддержки", "не нужны" if o3d < 150 else "нужны: нависания %d мм²" % o3d])
    col5["rows"].append(["ok" if o5d < 150 else "info", "Поддержки", "не нужны" if o5d < 150 else "нужны: нависания %d мм²" % o5d])
    if (geo.get("part_type") or "").startswith("rotation"):
        col3["rows"].append(["warn", "Слои и нагрузка", "поперёк оси — возможен отрыв слоёв"])
        col5["rows"].append(["ok", "Слои и нагрузка", "вокруг оси — деталь монолитна по окружности"])
    if base and channel_mm(geo) >= MIN_BASE_5D - 0.05:
        col5["rows"].append(["ok", "База", "Ø%s по каналу детали" % _n(base)])
    elif base:
        col5["rows"].append(["info", "База", "Ø%s — середина печатается обычными слоями" % _n(base)])
    return [col3, col5]


# ---------------------------------------------------------------- ориентация

DIR_VEC = {"+X": (1.0, 0.0, 0.0), "-X": (-1.0, 0.0, 0.0), "+Y": (0.0, 1.0, 0.0), "-Y": (0.0, -1.0, 0.0),
           "+Z": (0.0, 0.0, 1.0), "-Z": (0.0, 0.0, -1.0)}
AXIS_VEC = {"X": (1.0, 0.0, 0.0), "Y": (0.0, 1.0, 0.0), "Z": (0.0, 0.0, 1.0)}


def orientation_candidates(geo, mode):
    """Кандидаты положения в системе координат файла (geo — разбор модели как в файле).

    3D: до трёх граней габарита «вниз» (dir — направление файла, которое должно смотреть на стол).
    5D: одна поза — ось детали вертикально (dir — ось, target — вверх), центр стола.
    """
    if mode != "classic":
        axis = geo.get("rotation_axis") or "Y"
        return [{"title": "Ось вертикально, центр стола", "dir": AXIS_VEC[axis], "target": (0.0, 1.0, 0.0),
                 "center": True, "chosen": True, "overhang_mm2": geo.get("overhang_5d_mm2") or 0,
                 "why": "В 5D ось детали совпадает с осью вращения стола."}]
    out = []
    for c in geo["orientations"][:3]:
        out.append({"title": c["words"].capitalize(), "down": c["down"], "dir": DIR_VEC[c["down"]],
                    "target": (0.0, -1.0, 0.0), "center": False,
                    "overhang_mm2": c["overhang_mm2"], "bed_mm2": c["bed_mm2"], "height_mm": c.get("height_mm"),
                    "chosen": False,
                    "why": ("нависаний нет" if c["overhang_mm2"] < 50 else "нависания %d мм²" % c["overhang_mm2"])
                    + ", опора %d мм²" % c["bed_mm2"]})
    if out:
        out[0]["chosen"] = True
    return out


def current_pose(geo):
    """Разбор текущего положения на столе: кандидат «вниз = -Y» (как деталь лежит сейчас)."""
    for c in geo.get("orientations") or []:
        if c["down"] == "-Y":
            return c
    return {"overhang_mm2": 0, "bed_mm2": 0, "height_mm": (geo.get("size_mm") or [0, 0, 0])[1]}


def per_plate(geo, volume, shape="rectangular", gap=10.0):
    """Сколько копий в текущем положении помещается на стол за один запуск (оценка).

    Копии ставятся сеткой k × l с зазором gap (кайма, юбка), сетка — по центру стола. Стол
    STE Hybrid круглый (machine_shape elliptic): сетка помещается, если её углы внутри эллипса.
    Слайсер, которому не хватило места, оставляет лишние копии за рабочей зоной и не режет их,
    поэтому партию делим на запуски.
    """
    size = geo.get("size_mm") or [0, 0, 0]
    w, d = float(volume[0]), float(volume[2])
    cx, cz = float(size[0]) + gap, float(size[2]) + gap
    if cx <= gap or cz <= gap or w <= 0 or d <= 0:
        return 1
    best = 1
    for k in range(1, int((w + gap) // cx) + 1):
        for l in range(1, int((d + gap) // cz) + 1):
            hx, hz = (k * cx - gap) / 2, (l * cz - gap) / 2
            if shape == "elliptic" and (hx / (w / 2)) ** 2 + (hz / (d / 2)) ** 2 > 1.0:
                continue
            best = max(best, k * l)
    return best


# ---------------------------------------------------------------- настройки

VARIANTS = {
    "fast": {"title": "Быстро", "layer": 0.2, "infill_k": 0.8},
    "cheap": {"title": "Дёшево", "layer": 0.15, "infill_k": 0.66},
    "quality": {"title": "Качественно", "layer": 0.1, "infill_k": 1.0},
}

VARIANT_ORDER = ("fast", "cheap", "quality")  # вершины треугольника «быстро — дёшево — качественно»


def blend(mix):
    """Точка в треугольнике приоритетов → параметры варианта.

    mix — веса [быстро, дёшево, качественно] (любые неотрицательные, нормируются). Слой и множитель
    заполнения — взвешенное среднее вершин; key — ближайшая вершина: от неё зависят остальные правила
    (рисунок «линии» для быстрого, стенка меньше для дешёвого, промежуточный слой поддержек для качественного).
    """
    w = [max(0.0, float(x)) for x in (list(mix or []) + [0, 0, 0])[:3]]
    tot = sum(w) or 1.0
    w = [x / tot for x in w]
    layer = sum(w[i] * VARIANTS[k]["layer"] for i, k in enumerate(VARIANT_ORDER))
    k_inf = sum(w[i] * VARIANTS[k]["infill_k"] for i, k in enumerate(VARIANT_ORDER))
    key = VARIANT_ORDER[max(range(3), key=lambda i: w[i])]
    if max(w) > 0.95:
        title = VARIANTS[key]["title"]
    else:
        parts = ["%d %% %s" % (round(w[i] * 100), VARIANTS[k]["title"].lower())
                 for i, k in sorted(enumerate(VARIANT_ORDER), key=lambda x: -w[x[0]]) if w[i] >= 0.05]
        title = ", ".join(parts)
    return {"key": key, "w": [round(x, 3) for x in w], "layer": round(layer, 2), "infill_k": round(k_inf, 2), "title": title}


def variant_params(variant, prefs=None):
    """Параметры варианта: смесь из треугольника (prefs["mix"]), если есть, иначе вершина."""
    mix = (prefs or {}).get("mix")
    if mix:
        return blend(mix)
    v = VARIANTS[variant]
    return {"key": variant, "w": [1.0 if k == variant else 0.0 for k in VARIANT_ORDER], "layer": v["layer"],
            "infill_k": v["infill_k"], "title": v["title"]}


# Ответы на вопросы о печати (state["prefs"]).
STRENGTH = {"normal": "Обычная", "high": "Повышенная", "max": "Максимальная"}
SURFACE = {"look": "Внешний вид", "size": "Точные размеры", "any": "Не важно"}
SUPPORTS = {"everywhere": "Везде", "buildplate": "Только от стола", "none": "Без поддержек"}

FLEX_BASES = ("TPU", "TPE", "TPV", "SEBS")
SHRINK_BASES = ("ABS", "ABS/PA", "PA6", "PA12", "PC", "PP")
LOAD_TAGS = ("impact_resistant", "fatigue_resistant", "rigid", "creep_resistant", "torque_load", "modulus_min")
SUPPORT_MIN_MM2 = 150

# Все ключи, которые может записать plan_settings: при смене режима или ответа то, что
# в новом плане не задано, сбрасывается к профилю (см. workflow.apply_settings).
MANAGED_KEYS = ("printing_mode", "cylindrical_mode_base_diameter", "cylindrical_raft_enabled", "wall_line_count",
                "top_bottom_thickness", "infill_sparse_density", "infill_pattern", "support_enable",
                "support_enable_cylindrical", "support_type", "support_angle", "support_interface_enable",
                "adhesion_type", "brim_width", "layer_height", "cylindrical_layer_height", "z_seam_type",
                "xy_offset_layer_0")


def plan_settings(geo, mode, base, material, reqs, variant="quality", prefs=None):
    """Настройки под задачу: список строк {key, value, k, v, src, why, lock, opts}.

    geo — разбор детали в текущем положении на столе (после поворота).
    key — ключ STE Slicer (None — строка только для показа), k/v — подпись и значение,
    src — источник (tz, words, geom, rules, def, assume, profile), lock — задано ТЗ и не меняется
    вариантами, opts — [[подпись, значение], …] для правки в плане.
    Температуру, обдув, откат и скорость задаёт профиль материала — их не трогаем, только показываем.
    """
    tags = dict((r["tag"], r) for r in reqs)
    prefs = prefs or {}
    vp = variant_params(variant, prefs)
    variant = vp["key"]
    strength = prefs.get("strength")
    surface = prefs.get("surface")
    auto = set(prefs.get("auto") or [])  # ответы, взятые по рекомендации, а не выбранные пользователем
    s_src = "rules" if "strength" in auto else "words"
    f_src = "rules" if "surface" in auto else "words"
    base_poly = material.get("base") or ""
    flexible = base_poly in FLEX_BASES
    decor = "fine_detail" in tags
    # Декоративной детали «вязкость» нужна от сколов, а не под нагрузку: стенки и рисунок — как для декора.
    loaded = bool(set(tags) & set(LOAD_TAGS)) and not (decor and not set(tags) & {"torque_load", "creep_resistant", "rigid"})
    pose = current_pose(geo)
    rows = []

    def add(key, value, k, v, src, why, lock=False, opts=None):
        rows.append({"key": key, "value": value, "k": k, "v": v, "src": src, "why": why, "lock": lock,
                     "opts": opts or []})

    add("printing_mode", mode, "Режим", MODE_TITLE[mode], "geom", "")
    if mode != "classic":
        add("cylindrical_mode_base_diameter", base or 6.0, "База 5D", "Ø%s мм" % _n(base or 6.0), "geom",
            "Диаметр базы = сквозной канал детали: внутри базы печатать нечего." if base and geo and
            channel_mm(geo) >= MIN_BASE_5D - 0.05 else
            "Канала по оси нет — база по умолчанию STE Slicer: середина детали внутри базы печатается обычными "
            "слоями, остальное — слоями вокруг оси.",
            lock=True)
        add("cylindrical_raft_enabled", True, None, None, "geom", "")

    # ---- стенки
    walls, wsrc, wwhy, wlock = 3, "def", "Три линии — обычный запас прочности стенки.", False
    allow = tags.get("wear_resistant", {}).get("value")
    if geo.get("shell") and (geo.get("mean_wall_mm") or 9) < 4:
        walls = max(2, int(math.ceil((geo.get("mean_wall_mm") or 1.6) / 0.4)))
        wsrc, wwhy = "geom", "Тонкая стенка ~%s мм печатается целиком линиями периметра." % _n(geo.get("mean_wall_mm") or 0)
    elif allow:
        walls = max(4, int(math.ceil(allow / 0.4)) + 1)
        wsrc, wwhy, wlock = "tz", "Запас под износ %s мм из ТЗ: %d линий ≈ %s мм." % (_n(allow), walls, _n(round(walls * 0.4, 1))), True
    elif strength == "max":
        walls, wsrc, wwhy = 5, s_src, "Максимальная прочность: толстая стенка из пяти линий."
    elif loaded or strength == "high":
        walls, wsrc, wwhy = 4, ("rules" if loaded else s_src), "Нагруженная деталь: 4 линии держат нагрузку и не дают слоям разойтись."
        wlock = loaded
    elif decor:
        if "impact_resistant" in tags:
            walls, wsrc, wwhy = 3, "rules", "Декоративная деталь с тонкими элементами: три линии — элементы не отламываются."
        else:
            walls, wsrc, wwhy = 2, "rules", "Декоративная деталь: тонкие стенки, мелкие элементы не заливаются."
    if variant == "cheap" and not wlock and wsrc in ("def", "rules") and walls > 2:
        walls -= 1
        wwhy += " Вариант «Дёшево»: на линию меньше."
    add("wall_line_count", walls, "Стенки", "%d линии" % walls if 2 <= walls <= 4 else "%d линий" % walls, wsrc, wwhy,
        lock=wlock, opts=[["%d" % n, n] for n in (2, 3, 4, 5, 6)])

    # ---- верх и низ
    tb, tsrc, twhy = 1.0, "def", "Сплошные верх и низ ~1 мм закрывают заполнение."
    if strength == "max":
        tb, tsrc, twhy = 1.6, s_src, "Максимальная прочность: толстые верх и низ."
    elif loaded or strength == "high":
        tb, tsrc, twhy = 1.2, ("rules" if loaded else s_src), "Нагрузка: верх и низ потолще, чтобы не продавились."
    add("top_bottom_thickness", tb, "Верх и низ", "%s мм" % _n(tb), tsrc, twhy,
        opts=[["%s мм" % _n(v), v] for v in (0.8, 1.0, 1.2, 1.6, 2.0)])

    # ---- заполнение
    infill, isrc, iwhy, ilock = 20, "def", "Обычная плотность для деталей без нагрузки.", False
    if geo.get("shell") and (geo.get("mean_wall_mm") or 9) < 4:
        infill, isrc, iwhy = 100, "geom", "Тонкая стенка печатается сплошной."
    elif tags.keys() & {"creep_resistant", "modulus_min", "torque_load"} or ("rigid" in tags and "impact_resistant" in tags):
        infill, isrc, iwhy, ilock = 100, "rules", "Крутящий момент и постоянная нагрузка: сплошное заполнение.", True
    elif "fatigue_resistant" in tags and ("friction_high" in tags or "dimensional_stability" in tags):
        infill, isrc, iwhy = 90, "assume", "ТЗ не задаёт плотность; для циклического сжатия правила берут 80–100 %."
    elif strength == "max":
        infill, isrc, iwhy = 100, s_src, "Максимальная прочность: сплошное заполнение."
    elif strength == "high":
        infill, isrc, iwhy = 50, s_src, "Повышенная прочность: половина объёма — заполнение."
    elif "density_max" in tags:
        infill, isrc, iwhy = 30, "rules", "Ограничение по массе: лёгкое заполнение, прочность дают стенки."
    elif "fine_detail" in tags:
        infill, isrc, iwhy = 15, "rules", "Декоративная деталь: заполнение только для жёсткости."
    elif loaded:
        infill, isrc, iwhy = 40, "rules", "Нагрузка без уточнений: средняя плотность."
    elif not reqs and not strength:
        infill, isrc, iwhy = 40, "assume", "Нагрузка неизвестна — средняя плотность."
    k = vp["infill_k"]
    if not ilock and isrc in ("assume", "def", "rules", "words") and infill < 100 and k < 0.999:
        infill = max(10, int(round(infill * k / 5.0) * 5))
        iwhy += " Вариант «%s»: плотность × %s." % (vp["title"], _n(k))
    add("infill_sparse_density", infill, "Заполнение", "%d %%" % infill, isrc, iwhy, lock=ilock,
        opts=[["%d %%" % v, v] for v in (15, 20, 40, 60, 90, 100)])

    # ---- рисунок заполнения
    if infill >= 90:
        pat, pwhy = "lines", "Почти сплошное заполнение кладётся линиями."
    elif flexible:
        pat, pwhy = "gyroid", "Гибкий материал: гироид пружинит одинаково во все стороны."
    elif loaded or strength in ("high", "max"):
        pat, pwhy = "cubic", "Кубы держат нагрузку во всех направлениях."
    elif variant == "fast":
        pat, pwhy = "lines", "Вариант «Быстро»: линии печатаются быстрее всего."
    else:
        pat, pwhy = "grid", "Сетка — обычный рисунок для деталей без особой нагрузки."
    add("infill_pattern", pat, "Рисунок заполнения", PATTERN_WORDS[pat], "rules", pwhy,
        opts=[[PATTERN_WORDS[p], p] for p in ("grid", "lines", "cubic", "gyroid")])

    # ---- поддержки
    sup_pref = prefs.get("supports")
    if mode == "classic":
        over = pose["overhang_mm2"]
        need = over >= SUPPORT_MIN_MM2
        closed = (geo.get("fill_ratio") or 1) < 0.3
        st_type = "buildplate" if closed else "everywhere"
        why = "Нависания %d мм² круче %d° в выбранном положении." % (over, int(G_SUPPORT_ANGLE))
        src = "geom"
        if sup_pref == "none":
            need, src, why = False, "words", "Вы выбрали без поддержек: нависания %d мм² напечатаются с провисанием." % over
        elif sup_pref in ("everywhere", "buildplate") and need:
            st_type, src = sup_pref, "words"
        add("support_enable", need, "Поддержки", "да" if need else "нет", src, why, opts=[["да", True], ["нет", False]])
        if need:
            add("support_type", st_type, "Где строить поддержки", SUPPORTS[st_type].lower(), src if sup_pref else "geom",
                "Деталь полая (материал занимает %d %% объёма) — поддержки только от стола: изнутри их не достать." % int(round(100 * (geo.get("fill_ratio") or 0))) if st_type == "buildplate" and closed else
                "Поддержки там, где нужны: и от стола, и на самой детали." if st_type == "everywhere" else
                "Только от стола: на детали не останется следов.",
                opts=[["везде", "everywhere"], ["только от стола", "buildplate"]])
            add("support_angle", int(G_SUPPORT_ANGLE), "Угол поддержек", "%d°" % int(G_SUPPORT_ANGLE), "rules",
                "Поддержки под всем, что нависает круче %d° от вертикали." % int(G_SUPPORT_ANGLE),
                opts=[["%d°" % a, a] for a in (40, 45, 50, 60)])
            iface = surface == "look" or variant == "quality"
            add("support_interface_enable", iface, "Слой между поддержкой и деталью", "да" if iface else "нет", "rules",
                "Плотный слой под нависанием: поверхность ровнее, поддержка снимается чище." if iface else
                "Без промежуточного слоя — быстрее и дешевле.", opts=[["да", True], ["нет", False]])
    else:
        over5 = geo.get("overhang_5d_mm2") or 0
        need5 = over5 >= SUPPORT_MIN_MM2 and sup_pref != "none"
        why5 = ("В 5D остаются нависания %d мм², обращённые к оси: нужны поддержки 5D." % over5 if need5 else
                "В 5D нависаний почти нет." if over5 < SUPPORT_MIN_MM2 else
                "Вы выбрали без поддержек: нависания %d мм² напечатаются с провисанием." % over5)
        add("support_enable", need5, "Поддержки", "да" if need5 else "нет", "words" if sup_pref == "none" else "geom", why5,
            opts=[["да", True], ["нет", False]])
        add("support_enable_cylindrical", need5, None, None, "geom", "")
        if need5:
            add("support_angle", int(G_SUPPORT_ANGLE), "Угол поддержек", "%d°" % int(G_SUPPORT_ANGLE), "rules",
                "Поддержки под всем, что нависает круче %d°." % int(G_SUPPORT_ANGLE),
                opts=[["%d°" % a, a] for a in (40, 45, 50, 60)])

    # ---- прилипание к столу
    if mode == "classic":
        foot = pose.get("bed_mm2") or 0
        height = pose.get("height_mm") or (geo.get("size_mm") or [0, 0, 0])[1]
        tall = height > 2.5 * math.sqrt(max(foot, 1))
        if base_poly == "PP":
            add("adhesion_type", "brim", "Прилипание", "кайма 8 мм", "rules", "PP сильно коробит — кайма держит углы.",
                opts=ADHESION_OPTS)
            add("brim_width", 8, None, None, "rules", "")
        elif surface == "size":
            add("adhesion_type", "skirt", "Прилипание", "юбка", f_src,
                "Нижняя кромка — рабочая поверхность: без каймы, размер первого слоя компенсирую.", opts=ADHESION_OPTS)
            add("xy_offset_layer_0", -0.1, "Первый слой", "−0,1 мм", f_src,
                "Компенсация «слоновьей ноги»: первый слой не выйдет за размер.",
                opts=[["0", 0.0], ["−0,1 мм", -0.1], ["−0,2 мм", -0.2]])
        elif foot < 400 or tall or geo.get("flat") or (base_poly in SHRINK_BASES and max(geo.get("size_mm") or [0]) > 100):
            why = ("Малая площадь опоры (%d мм²) — кайма не даст оторваться." % foot if foot < 400 else
                   "Высокая деталь на небольшой опоре — кайма держит её на столе." if tall else
                   "Плоская деталь — кайма не даст углам подняться." if geo.get("flat") else
                   "%s даёт усадку, деталь крупная — кайма держит края." % base_poly)
            add("adhesion_type", "brim", "Прилипание", "кайма 5 мм", "rules", why, opts=ADHESION_OPTS)
            add("brim_width", 5, None, None, "rules", "")
        else:
            add("adhesion_type", "skirt", "Прилипание", "юбка", "def", "Площадь опоры %d мм² — держится сама." % foot,
                opts=ADHESION_OPTS)
    else:
        add(None, None, "Прилипание", "цилиндрическая подложка", "def",
            "В 5D STE Slicer сам печатает цилиндрическую подложку (cylindrical raft) вокруг базы.")

    # ---- шов
    if surface == "look" or "fine_detail" in tags:
        seam = "back" if (geo.get("part_type") or "").startswith("rotation") else "sharpest_corner"
        add("z_seam_type", seam, "Шов", SEAM_WORDS[seam], f_src if surface == "look" else "rules",
            "Круглой детали негде спрятать шов — собираю его в одну линию сзади." if seam == "back" else
            "Шов прячется в углах, на гладких стенках его не видно.",
            opts=[[SEAM_WORDS[x], x] for x in ("sharpest_corner", "back", "shortest", "random")])

    # ---- слой
    layer = vp["layer"]
    lwhy = "Вариант «%s»." % vp["title"]
    if "fine_detail" in tags and variant == "quality":
        layer, lwhy = 0.06, "Декоративная деталь: тонкий слой, ступенек не видно."
    elif surface in ("look", "size") and variant == "quality":
        lwhy = "Тонкий слой: ровнее поверхность и точнее размер."
    add("layer_height", layer, "Слой", "%s мм" % _n(layer), "rules", lwhy,
        opts=[["%s мм" % _n(v), v] for v in (0.06, 0.1, 0.15, 0.2, 0.25)])
    if mode != "classic":
        add("cylindrical_layer_height", layer, None, None, "rules", "")

    # ---- профиль материала (только показ)
    prof = material.get("profile")
    if prof:
        add(None, None, "Профиль материала", "сопло %d °C, стол %d °C, обдув %d %%, %d мм/с" % (
            prof["print_temp"], prof["bed_temp"], prof["fan"], prof["speed"]), "profile",
            "Температуру, обдув, откат и скорость задаёт профиль %s в STE Slicer; поменять их можно в паспорте." % material["name"])
    return rows


G_SUPPORT_ANGLE = 50.0  # = geometry.SUPPORT_ANGLE
PATTERN_WORDS = {"grid": "сетка", "lines": "линии", "cubic": "кубы", "gyroid": "гироид", "triangles": "треугольники"}
SEAM_WORDS = {"sharpest_corner": "в углах", "back": "сзади, одной линией", "shortest": "кратчайший", "random": "случайный"}
ADHESION_OPTS = [["юбка", "skirt"], ["кайма", "brim"], ["подложка", "raft"]]
ADHESION_WORDS = {"skirt": "юбка", "brim": "кайма", "raft": "подложка", "none": "нет"}


def settings_dict(rows):
    return dict((r["key"], r["value"]) for r in rows if r["key"] and r["value"] is not None)


def locks(rows, material):
    out = ["Материал %s — по ТЗ" % material["name"]]
    for r in rows:
        if r.get("lock") and r["k"]:
            out.append("%s %s — %s" % (r["k"], r["v"], (r["why"] or "по ТЗ").rstrip(".")))
    return out


# ---------------------------------------------------------------- оценка без слайсера

def estimate(geo, material, rows):
    """Грубая оценка времени и массы, когда нарезки ещё нет (и для демо без слайсера)."""
    s = settings_dict(rows)
    vol = geo["volume_cm3"]
    area = geo["area_cm2"]
    shell_cm3 = min(vol, area * 0.04 * s.get("wall_line_count", 3))
    inner = max(vol - shell_cm3, 0)
    used = shell_cm3 + inner * s.get("infill_sparse_density", 20) / 100.0
    if s.get("support_enable"):
        used *= 1.12
    grams = used * material.get("density", 1.2)
    layer = s.get("layer_height", 0.15)
    minutes = used * 1000 / (layer * 0.45 * 40 * 60) * 60 / 60 + 6
    return {"time_min": int(round(minutes)), "grams": round(grams, 1)}


def fmt_time(minutes):
    minutes = int(round(minutes))
    h, m = divmod(minutes, 60)
    return ("%d ч %02d мин" % (h, m)) if h else ("%d мин" % m)


def fmt_money(rub):
    s = "%d" % int(round(rub))
    if len(s) > 3:
        s = s[:-3] + " " + s[-3:]
    return s + " ₽"


def _n(v):
    return ("%g" % v).replace(".", ",")


# ---------------------------------------------------------------- недопустимые и рискованные сочетания

SHRINK_BASES = ("PP", "ABS", "PA6", "PA12", "ABS/PA", "PC")
FLEX_BASES = ("TPU", "TPE", "TPV", "SEBS")


def combo_warnings(geo, mode, material, values, quantity=1):
    """Сочетания параметров, которые не напечатаются или напечатаются плохо.

    values — {ключ STE Slicer: значение} (план или то, что сейчас в слайсере).
    Возвращает [{"id", "kind": "bad" | "warn", "text", "fixes"}]: bad — так нельзя, warn — риск.
    fixes — готовые исправления для кнопок: {"label", "set": {ключ: значение}} — параметры,
    {"label", "mode": "classic"} — сменить режим, {"label", "quantity": 1} — количество.
    """
    geo, material, v = geo or {}, material or {}, values or {}
    base = material.get("base") or ""
    prof = material.get("profile") or {}
    out = []

    def add(wid, kind, text, fixes=None):
        out.append({"id": wid, "kind": kind, "text": text, "fixes": fixes or []})

    to_3d = {"label": "Печатать в 3D", "mode": "classic"}
    one = {"label": "Оставить 1 шт.", "quantity": 1}
    no_spiral = {"label": "Выключить спираль", "set": {"magic_spiralize": False}}
    five = mode not in (None, "classic")
    if five and quantity and quantity > 1:
        n = quantity % 100
        runs = "запусков" if 10 < n < 20 or n % 10 in (0, 5, 6, 7, 8, 9) else "запуск" if n % 10 == 1 else "запуска"
        add("five_qty", "bad", "В 5D печатается одна деталь за запуск: %d шт. — это %d %s подряд. "
                               "Чтобы напечатать партию на одном столе, выберите 3D." % (quantity, quantity, runs),
            [to_3d, one])
    if five and geo and five_d_base(geo) is None:
        add("five_core", "bad", "В 5D слои ложатся вокруг оси детали, а оси вращения у этой модели нет.", [to_3d])
    if five and v.get("ironing_enabled"):
        add("five_ironing", "warn", "Разглаживание в 5D не работает — плоского верхнего слоя нет.",
            [{"label": "Выключить разглаживание", "set": {"ironing_enabled": False}}])
    if five and v.get("magic_spiralize"):
        add("five_spiral", "bad", "Спиральная печать и 5D несовместимы — выключите одно из двух.", [no_spiral, to_3d])
    lkey = "cylindrical_layer_height" if five else "layer_height"
    layer = v.get(lkey)
    if isinstance(layer, (int, float)) and layer > 0.32:
        add("layer", "bad", "Слой %s мм толще 3/4 сопла 0,4 мм — слои не склеятся." % _n(layer),
            [{"label": "Слой 0,3 мм", "set": {lkey: 0.3}}])
    dens, walls = v.get("infill_sparse_density"), v.get("wall_line_count")
    if isinstance(dens, (int, float)) and dens < 5 and isinstance(walls, (int, float)) and walls < 2:
        add("shell", "warn", "Заполнение %s %% и одна стенка — деталь будет пустой оболочкой и не выдержит нагрузку." % _n(dens),
            [{"label": "3 стенки", "set": {"wall_line_count": 3}},
             {"label": "Заполнение 20 %", "set": {"infill_sparse_density": 20.0}}])
    if v.get("magic_spiralize") and quantity and quantity > 1:
        add("spiral_qty", "bad", "Спиральная печать — только для одной детали на столе.", [no_spiral, one])
    over = ((geo.get("orientations") or [{}])[0] or {}).get("overhang_mm2") or 0
    if not five and v.get("support_enable") is False and over > 400:
        add("supports", "warn", "Поддержки выключены, а нависаний около %d мм² — нижние поверхности провиснут." % int(over),
            [{"label": "Включить поддержки", "set": {"support_enable": True}}])
    if base in SHRINK_BASES and v.get("adhesion_type") in ("none", "skirt"):
        add("adhesion", "warn", "%s даёт усадку, а у края нет каймы или подложки — углы могут оторваться от стола." % base,
            [] if five else [{"label": "Добавить кайму", "set": {"adhesion_type": "brim"}}])
    temp = v.get("material_print_temperature")
    if isinstance(temp, (int, float)) and prof.get("print_temp") and abs(temp - prof["print_temp"]) > 25:
        add("temp", "warn", "Сопло %d °C, а профиль %s рассчитан на %d °C — %s." % (
            temp, material.get("name", base), prof["print_temp"],
            "пластик не проплавится и слои не склеятся" if temp < prof["print_temp"] else "пластик может деградировать и подтекать"),
            [{"label": "Сопло %d °C, как в профиле" % prof["print_temp"], "set": {"material_print_temperature": float(prof["print_temp"])}}])
    bed = v.get("material_bed_temperature")
    if isinstance(bed, (int, float)) and base in SHRINK_BASES and bed < 60:
        good = float(prof["bed_temp"]) if (prof.get("bed_temp") or 0) >= 60 else 80.0
        add("bed", "warn", "Стол %d °C холодный для %s — деталь оторвётся от стола." % (bed, base),
            [{"label": "Стол %d °C" % good, "set": {"material_bed_temperature": good}}])
    fan = v.get("cool_fan_speed")
    if isinstance(fan, (int, float)) and base in SHRINK_BASES and fan > 40:
        good = float(prof["fan"]) if prof.get("fan") is not None and prof["fan"] <= 40 else 20.0
        add("fan", "warn", "Обдув %d %% для %s — слои остынут слишком быстро, будет расслоение." % (fan, base),
            [{"label": "Обдув %d %%" % good, "set": {"cool_fan_speed": good}}])
    speed = v.get("speed_print")
    if isinstance(speed, (int, float)) and any(base.startswith(f) for f in FLEX_BASES) and speed > 40:
        add("flex_speed", "warn", "Гибкий %s на %d мм/с — нить сомнётся в подаче; для гибких до 30–40 мм/с." % (base, speed),
            [{"label": "Скорость 30 мм/с", "set": {"speed_print": 30.0}}])
    retr = v.get("retraction_amount")
    if isinstance(retr, (int, float)) and retr > 8:
        good = float(prof["retract"]) if 0 < (prof.get("retract") or 0) <= 8 else 6.0
        add("retract", "warn", "Откат %s мм — длинный, нить может сорваться в экструдере." % _n(retr),
            [{"label": "Откат %s мм" % _n(good), "set": {"retraction_amount": good}}])
    return out
