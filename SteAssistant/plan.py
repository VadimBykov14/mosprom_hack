# -*- coding: utf-8 -*-
"""Порядок работы: вопросы → план печати → подтверждение → нарезка.

1. После разбора файлов ассистент задаёт все вопросы одной карточкой (до 5) —
   только то, чего нет в ТЗ, словах и геометрии.
2. По ответам собирает план: материал, режим, положение, вариант, количество и ключевые
   настройки — значение и «почему». До подтверждения в STE Slicer меняется только сцена
   (положение модели); материал и настройки не записываются, нарезки нет.
3. Пользователь меняет строки плана (кнопкой или словами) — план пересобирается,
   изменённые строки отмечаются.
4. «Подтвердить и нарезать» — материал, копии, настройки уходят в STE Slicer, затем нарезка.

Фаза хранится в state["phase"]: "questions" → "plan" → "confirmed".
"""
import re
import uuid

from . import materials as M
from . import requirements as R
from . import rules
from . import workflow as W

MAX_QUESTIONS = 6
SELF = "Реши сам"
ENV_DEFAULT = "в помещении, без нагрузки"

VARIANT_BY_TITLE = dict((v["title"], k) for k, v in rules.VARIANTS.items())
STRENGTH_BY_TITLE = dict((v, k) for k, v in rules.STRENGTH.items())
SURFACE_BY_TITLE = dict((v, k) for k, v in rules.SURFACE.items())
SUPPORTS_BY_TITLE = dict((v, k) for k, v in rules.SUPPORTS.items())

SHORT = {"env": "Где работает", "strength": "Прочность", "surface": "Поверхность", "qty": "Сколько",
         "priority": "Что важнее", "material": "Материал", "mode": "Режим", "orientation": "Положение",
         "supports": "Поддержки"}


# ============================================================ вопросы

def questions_for(run, words=None):
    """Вопросы в два раунда.

    1. «Условия» — чего нет в ТЗ и словах: где работает деталь, прочность, поверхность,
       количество, что важнее.
    2. «Как делать» — после ответов: материал (с описанием простыми словами), режим 3D/5D,
       положение на столе, поддержки. У каждого вопроса — рекомендация правил; «Пропустить»
       оставляет рекомендованное.
    """
    st = run.st
    if not run.app.settings.get("ask_questions", True):
        return []
    if st.get("q_round", 0) == 0:
        return conditions_for(run, words or {})
    if st.get("q_round") == 1:
        return how_for(run)
    return []


def conditions_for(run, words):
    st = run.st
    has_spec = bool(st.get("spec_file"))
    reqs = st.get("requirements") or []
    tags = set(r["tag"] for r in reqs)
    geo = st.get("geo_file") or st.get("geo") or {}
    items = []
    if not reqs and not st.get("fixed_material"):
        items.append({"id": "env", "q": "Где будет работать деталь? Можно выбрать несколько",
                      "options": [o[0] for o in W.QUESTION_OPTIONS], "multi": True,
                      "exclusive": list(W.EXCLUSIVE_OPTIONS), "def": W.default_text(ENV_DEFAULT),
                      "why": "От ответа зависят материал и заполнение."})
    if not tags & set(rules.LOAD_TAGS) and not (st.get("prefs") or {}).get("strength"):
        items.append({"id": "strength", "q": "Насколько прочной должна быть деталь?",
                      "options": list(rules.STRENGTH.values()), "multi": False, "exclusive": [],
                      "notes": {"Обычная": "3 стенки, заполнение 20–40 %. Корпуса, держатели, крышки.",
                                "Повышенная": "4 стенки, заполнение 50 %, рисунок «кубы». Детали под нагрузкой.",
                                "Максимальная": "5 стенок, сплошное заполнение. Прочнее всего, но дольше и дороже."},
                      "rec": "Обычная", "def": W.default_text("обычная"), "widget": "levels",
                      "why": "От ответа зависят стенки, верх и низ, заполнение."})
    if not (st.get("prefs") or {}).get("surface"):
        rec = "Внешний вид" if "fine_detail" in tags else ("Точные размеры" if "dimensional_stability" in tags else "Не важно")
        items.append({"id": "surface", "q": "Приоритетные параметры поверхности",
                      "options": list(rules.SURFACE.values()), "multi": False, "exclusive": [],
                      "notes": {"Внешний вид": "Шов спрячу, под нависаниями — плотный слой, слой тоньше.",
                                "Точные размеры": "Без каймы, первый слой с компенсацией, тонкий слой.",
                                "Не важно": "Обычные настройки, быстрее."},
                      "rec": rec,  # без подписи «По умолчанию — …»: рекомендация видна точкой на кнопке
                      "widget": "segmented", "icons": {"Внешний вид": "eye", "Точные размеры": "ruler", "Не важно": "dash"},
                      "why": "От ответа зависят шов, прилипание к столу и слой."})
    if not words.get("variant") and not st.get("variant_src"):
        items.append({"id": "priority", "q": "Что важнее? Передвиньте точку между тремя углами", "widget": "triangle",
                      "options": ["Быстро", "Дёшево", "Качественно"], "multi": False, "exclusive": [],
                      "notes": {"Быстро": "слой 0,2 мм, заполнение × 0,8",
                                "Дёшево": "слой 0,15 мм, заполнение × 0,66, на стенку меньше",
                                "Качественно": "слой 0,1 мм"},
                      "rec": "Качественно", "recW": [0, 0, 1], "def": W.default_text("качественно, слой 0,1 мм"),
                      "why": "От ответа зависят слой и заполнение."})
    return items[:MAX_QUESTIONS]


def how_for(run):
    """Второй раунд: как делать. Рекомендации считают правила по ТЗ, ответам и геометрии."""
    st = run.st
    items = []
    m = W.select_material(run, fixed=st.get("fixed_material"), apply=False)
    if not st.get("fixed_material"):
        opts, notes = material_options(run, m)
        if len(opts) > 1:
            items.append({"id": "material", "q": "Какой материал взять?", "options": opts, "multi": False, "exclusive": [],
                          "notes": notes, "rec": m["name"], "def": W.default_text(m["name"]),
                          "why": "Первым — тот, что лучше всех проходит требования."})
    geo = st.get("geo_file") or st.get("geo") or {}
    dec = rules.choose_mode(geo, st.get("requirements"))
    mode_q = False
    if dec.get("can5d") and not st.get("forced_mode"):
        o3d, o5d = dec["o3d"], dec.get("o5d") or 0
        rec = "5D" if dec["mode"] != "classic" else "3D"
        notes = {"5D": "Слои вокруг оси, %s. Нависаний %d мм². Одна деталь за раз." % (rules.base_words(geo, dec["base5d"]), o5d),
                 "3D": "Плоские слои на столе. Нависаний %d мм²%s." % (o3d, ", нужны поддержки" if o3d >= rules.SUPPORT_MIN_MM2 else "")}
        items.append({"id": "mode", "q": "Печатать в 3D или в 5D?", "options": ["3D", "5D"], "widget": "mode",
                      "multi": False, "exclusive": [], "notes": notes, "rec": rec, "def": W.default_text(rec),
                      "why": dec["reason"], "o3d": o3d, "o5d": o5d})
        mode_q = True
    will_3d = (st.get("forced_mode") == "3D") or (not st.get("forced_mode") and dec["mode"] == "classic") or mode_q
    if will_3d:
        cands = rules.orientation_candidates(geo, "classic")
        if len(cands) > 1 and st.get("orient_choice") is None:
            notes = dict((c["title"], "нависания %d мм², опора %d мм², высота %s мм" % (
                c["overhang_mm2"], c["bed_mm2"], W._n(c.get("height_mm") or 0))) for c in cands)
            items.append({"id": "orientation", "q": "Как положить деталь на стол?" + (" (для 3D)" if mode_q else ""),
                          "widget": "tiles", "sils": orientation_silhouettes(run, cands),
                          "options": [c["title"] for c in cands], "multi": False, "exclusive": [], "notes": notes,
                          "rec": cands[0]["title"], "def": W.default_text(cands[0]["title"].lower()),
                          "why": "Первое — меньше всего нависаний и больше опоры."})
            best = cands[0]
            if best["overhang_mm2"] >= rules.SUPPORT_MIN_MM2 and not (st.get("prefs") or {}).get("supports") \
                    and not st.get("no_support"):
                closed = (geo.get("fill_ratio") or 1) < 0.3
                rec = rules.SUPPORTS["buildplate" if closed else "everywhere"]
                items.append({"id": "supports", "q": "Как быть с поддержками?", "multi": False, "exclusive": [],
                              "options": [rec] + [x for x in rules.SUPPORTS.values() if x != rec],
                              "notes": {"Везде": "Поддержки под всеми нависаниями — надёжно, но следы и на самой детали.",
                                        "Только от стола": "Опоры растут только от стола: на детали следов нет, внутри каналов поддержек не будет.",
                                        "Без поддержек": "Быстрее, но нависания %d мм² провиснут." % best["overhang_mm2"]},
                              "rec": rec, "def": W.default_text(rec.lower()), "widget": "segmented",
                              "icons": {"Везде": "sup-all", "Только от стола": "sup-plate", "Без поддержек": "sup-none"},
                              "why": "В лучшем положении нависания %d мм²." % best["overhang_mm2"]})
    # Количество — после режима: в 5D печатается одна деталь за запуск.
    q = qty_question(run, geo, mode_q, will_3d)
    if q:
        items.append(q)
    return items[:MAX_QUESTIONS]


def qty_question(run, geo, mode_q, will_3d):
    """Сколько штук. Режим уже 5D (без вопроса о режиме) — не спрашиваем: одна деталь за запуск.
    Если режим выбирают в той же карточке, при 5D счётчик скрывается (modeDep)."""
    st = run.st
    has_spec = bool(st.get("spec_file"))
    if (has_spec and st.get("quantity", 1) > 1) or st.get("qty_set") or not will_3d:
        return None
    why = "Партию слайсер расставит на одном столе."
    if mode_q:
        why += " В 5D печатается одна деталь за раз — для партии выберите 3D."
    try:
        pr = run.bridge.printer()
        per = rules.per_plate(geo, pr.get("volume") or [0, 0, 0], pr.get("shape") or "rectangular")
    except Exception:
        per = 0
    return {"id": "qty", "q": "Сколько штук нужно?", "options": ["1", "5", "10"], "multi": False,
            "exclusive": [], "def": W.default_text("1 шт."), "why": why, "widget": "stepper",
            "min": 1, "max": 200, "rec": "1", "perPlate": per, "modeDep": bool(mode_q)}


def orientation_silhouettes(run, cands):
    """Силуэт детали в каждом положении (вид спереди, стол снизу) — для плиток выбора. {title: sil}."""
    import numpy as np
    from . import geometry as G
    st = run.st
    out = {}
    try:
        tris = run.bridge.mesh(st["node"])
        pose = np.array(st.get("pose") or np.eye(3), dtype=float)
        pts = tris.reshape(-1, 3)
        c = (pts.min(axis=0) + pts.max(axis=0)) / 2
        file_tris = (tris - c).dot(pose)  # поворот сцены → поза файла (pose ортогональна: обратная = транспонированная)
        for cand in cands:
            axis, deg = G.rotation_to(cand["dir"], cand["target"])
            t = file_tris.dot(G.rot_matrix(axis, deg).T) if deg > 0.5 else file_tris
            out[cand["title"]] = G.silhouette(t, None, None, grid=40)
    except Exception:
        return {}
    return out


def material_options(run, pick):
    """До трёх материалов для вопроса: выбранный правилами и ближайшие, с описанием простыми словами."""
    st = run.st
    reqs = st.get("requirements") or []
    names = [pick["name"]] + [x for x in st.get("material_alts", []) if x != pick["name"]]
    if not reqs:
        names = [pick["name"]] + [n for n in ("EnduSe PA6", "FiberPart ABS/PA G8", "Proto PLA") if n != pick["name"]]
    opts, notes = [], {}
    for name in names[:3]:
        m = [x for x in M.MATERIALS if x["name"] == name]
        if not m:
            continue
        m = m[0]
        sc = rules.score(m, reqs) if reqs else None
        tail = ""
        if sc is not None:
            if sc["violations"]:
                tail = " Не проходит: %s." % "; ".join(r["text"].lower() for r in sc["violations"][:2])
            else:
                tail = " Проходит все обязательные требования."
        opts.append(name)
        notes[name] = (m.get("plain") or m.get("note", "")) + tail
    return opts, notes


def ask_all(run, items):
    st = run.st
    rnd = st.get("q_round", 0)
    b = {"type": "questions", "id": "q" + uuid.uuid4().hex[:6], "items": items, "answers": None,
         "title": "Как сделать" if rnd >= 1 else "Условия работы",
         "skip": "Оставить рекомендованное" if rnd >= 1 else "Пропустить"}
    run.block(b)
    st["phase"] = "questions"
    st["pending_question"] = b["id"]
    run.part["status"] = "ask"
    return b


def pending_questions(part):
    st = part.get("state") or {}
    if st.get("phase") != "questions":
        return None
    for m in reversed(part.get("messages", [])):
        for b in m.get("blocks", []) or []:
            if b.get("type") == "questions" and b.get("id") == st.get("pending_question") and b.get("answers") is None:
                return b
    return None


def answers_text(block, answers):
    """Ответы одной строкой для сообщения пользователя в ленте."""
    if not answers:
        return "Пропустить"
    if "text" in answers:
        return answers["text"]
    short = SHORT
    out = []
    for it in block.get("items", []):
        v = answers.get(it["id"])
        if v:
            out.append("%s: %s" % (short.get(it["id"], it["q"].rstrip("?")), v.replace("; ", ", ")))
    return " · ".join(out) or "Пропустить"


def apply_answers(run, answers, block=None):
    """Ответы карточки → требования, предпочтения и решения с источником «ваш ответ».
    Пустые и «Реши сам» — рекомендованное правилами."""
    st = run.st
    answers = dict(answers or {})
    prefs = st.setdefault("prefs", {})
    # Без ответа — то, что написано в «По умолчанию»: рекомендация правил (прочность, поверхность, вариант).
    auto = []
    if "text" not in answers:
        for it in (block or {}).get("items", []):
            if it["id"] in ("strength", "surface") and it.get("rec") and answers.get(it["id"]) in (None, "", SELF):
                answers[it["id"]] = it["rec"]
                auto.append(it["id"])
    prefs["auto"] = sorted(set(prefs.get("auto", [])) - set(answers) | set(auto))
    env = answers.get("env")
    if env and env != SELF:
        reqs, _ = W.answer_to_requirements(env)
        st["requirements"] = R.merge(st.get("requirements", []), reqs)
    qty = answers.get("qty")
    if qty and qty != SELF:
        m = re.search(r"\d+", qty)
        if m and 1 <= int(m.group(0)) <= 200:
            st["quantity"] = int(m.group(0))
            st["qty_set"] = True
    pr = answers.get("priority")
    if pr:
        set_priority(st, pr)
    if answers.get("strength") in STRENGTH_BY_TITLE:
        prefs["strength"] = STRENGTH_BY_TITLE[answers["strength"]]
    if answers.get("surface") in SURFACE_BY_TITLE:
        prefs["surface"] = SURFACE_BY_TITLE[answers["surface"]]
    if answers.get("supports") in SUPPORTS_BY_TITLE:
        prefs["supports"] = SUPPORTS_BY_TITLE[answers["supports"]]
    mat = answers.get("material")
    if mat and mat != SELF:
        for m in M.MATERIALS:
            if m["name"] == mat:
                st["fixed_material"] = m["id"]
                st["material_by_answer"] = True
    if answers.get("mode") in ("3D", "5D"):
        st["forced_mode"] = answers["mode"]
        if answers["mode"] == "5D" and st.get("quantity", 1) > 1 and "qty" in answers:
            st["quantity"] = 1  # в 5D одна деталь за запуск; счётчик в карточке был скрыт
    o = answers.get("orientation")
    if o and o != SELF:
        geo = st.get("geo_file") or st.get("geo") or {}
        titles = [c["title"] for c in rules.orientation_candidates(geo, "classic")]
        if o in titles:
            st["orient_choice"] = titles.index(o)
    if "text" in answers:  # ответ своими словами в поле ввода
        words = W.parse_words(answers["text"])
        if words.get("requirements"):
            st["requirements"] = R.merge(st.get("requirements", []), words["requirements"])
        if words.get("variant"):
            st["variant"] = words["variant"]
            st["variant_src"] = "words"
        if words.get("quantity"):
            st["quantity"] = words["quantity"]
            st["qty_set"] = True
        if words.get("material"):
            st["fixed_material"] = words["material"]
        if words.get("mode"):
            st["forced_mode"] = words["mode"]
        if words.get("no_support"):
            prefs["supports"] = "none"
    st["q_round"] = st.get("q_round", 0) + 1
    if st["q_round"] >= 2 or "text" in answers:
        st["questions_done"] = True
    st.pop("pending_question", None)


def parse_mix(value):
    """«w:0.5,0.2,0.3» — точка в треугольнике (быстро, дёшево, качественно) → веса или None."""
    m = re.match(r"^\s*w:\s*([\d.]+)\s*,\s*([\d.]+)\s*,\s*([\d.]+)\s*$", value or "")
    if not m:
        return None
    w = [float(x) for x in m.groups()]
    return w if sum(w) > 0 else None


def set_priority(st, value):
    """Вариант печати: вершина треугольника («Быстро») или точка внутри (смесь весов)."""
    prefs = st.setdefault("prefs", {})
    mix = parse_mix(value)
    if mix:
        b = rules.blend(mix)
        if max(b["w"]) > 0.95:  # у самой вершины — это просто вариант
            prefs.pop("mix", None)
        else:
            prefs["mix"] = b["w"]
        st["variant"] = b["key"]
    elif value in VARIANT_BY_TITLE or value in rules.VARIANTS:
        prefs.pop("mix", None)
        st["variant"] = VARIANT_BY_TITLE.get(value) or value
    else:
        raise ValueError("Вариант — Быстро, Дёшево, Качественно или точка в треугольнике")
    st["variant_src"] = "words"


def close_questions_ui(block, answers):
    """Сразу свернуть карточку в строку ответов (до того, как агент начнёт ход)."""
    block["answers"] = answers or {}


def close_questions(run, block, answers):
    close_questions_ui(block, answers)
    apply_answers(run, answers, block)
    run.changed()


# ============================================================ план

def build(run, edited=None):
    """Собрать план и показать карточку. Материал и настройки в STE Slicer не пишутся."""
    st = run.st
    part = run.part
    prev = dict((r["key"], r["v"]) for r in (st.get("plan_rows") or []))
    W.select_material(run, fixed=st.get("fixed_material"), apply=False)
    if not st.get("requirements") and not st.get("fixed_material"):
        st["material_src"] = "assume"
    old_mode = st.get("mode")
    W.choose_mode(run, forced=st.get("forced_mode"), emit=False)
    choice = st.get("orient_choice") if st.get("mode", "classic") == "classic" else None
    if old_mode != st.get("mode") or not st.get("orientation") or (choice is not None and choice != st.get("orient_idx")):
        W.orient(run, emit=False, choice=choice)  # положение — изменение сцены, его видно в слайсере сразу
    W.apply_settings(run, dry=True)
    rows = plan_rows(run)
    for r in rows:
        r["changed"] = bool(prev) and prev.get(r["key"]) not in (None, r["v"])
    st["plan_rows"] = rows
    st["phase"] = "plan"
    part["status"] = "plan"
    m = M.by_id(st["material_id"])
    head = "%s · %s · %d шт." % (part.get("name") or "Деталь", run.app.printer_name(), st.get("quantity", 1))
    block = {"type": "plan", "id": st.get("plan_id") or ("p" + uuid.uuid4().hex[:6]), "head": head, "rows": rows,
             "warnings": plan_warnings(run, m), "status": "wait",
             "material": st.get("material_view"), "modes": st.get("modes_view")}
    st["plan_id"] = block["id"]
    old = find_block(part, block["id"])
    if old is not None and old.get("status") == "wait":
        old.clear()
        old.update(block)
        run.changed()
    else:
        run.block(block)
    W.build_passport(part, run.app)
    return block


def find_block(part, bid):
    for msg in part.get("messages", []):
        for b in msg.get("blocks", []) or []:
            if b.get("id") == bid:
                return b
    return None


def plan_rows(run):
    st = run.st
    m = M.by_id(st["material_id"])
    geo = st.get("geo") or {}
    rows = []

    def add(key, k, v, why, src, options=None):
        rows.append({"key": key, "k": k, "v": v, "why": why or "", "src": src,
                     "srcText": W.SRC_WORDS.get(src, ""), "options": options or []})

    mats = [m["name"]] + [x for x in st.get("material_alts", []) if x != m["name"]]
    # Простыми словами, что это за пластик; как он проходит ТЗ — в таблице «почему этот».
    add("material", "Материал", m["name"], m.get("plain") or m.get("note", ""), st.get("material_src", "tz"), mats[:4])
    mode = st.get("mode", "classic")
    mode_v = rules.MODE_SHORT[mode] + ((", база Ø%s мм" % W._n(st["base"])) if st.get("base") and mode != "classic" else "")
    add("mode", "Режим", mode_v, st.get("mode_reason", ""), st.get("mode_src", "geom"),
        ["3D", "5D"] if (st.get("geo_file") or geo).get("part_type", "").startswith("rotation") else [])
    if rows[-1]["options"]:
        rows[-1].update(widget="mode", cur=rules.MODE_SHORT[mode])
    if st.get("orientation"):
        opts = st.get("orient_options") or [] if mode == "classic" else []
        add("orientation", "Положение", st["orientation"][:1].lower() + st["orientation"][1:],
            "Меньше нависаний и больше опоры на столе." if st.get("orient_choice") is None else "Вы выбрали.",
            "words" if st.get("orient_choice") is not None else "geom", opts)
    vp = rules.variant_params(st.get("variant", "quality"), st.get("prefs"))
    add("variant", "Вариант печати", vp["title"],
        "Слой %s мм, заполнение × %s; что задано ТЗ, не меняется." % (W._n(vp["layer"]), W._n(vp["infill_k"])),
        "words" if st.get("variant_src") == "words" else "def",
        [x["title"] for x in (rules.VARIANTS["fast"], rules.VARIANTS["cheap"], rules.VARIANTS["quality"])])
    rows[-1].update(widget="triangle", w=vp["w"])
    if mode == "classic":
        q = st.get("quantity", 1)
        per = fit_per_plate(run)
        why = ""
        if q > per:
            runs = -(-q // per)
            why = "По оценке на стол помещается %d шт. — %d %s: на столе %d шт., время и масса — за один запуск." % (
                per, runs, W.plural(runs, "запуск", "запуска", "запусков"), per)
        elif q > 1:
            why = "Все %d шт. помещаются на стол, расставит слайсер." % q
        add("quantity", "Количество", "%d шт." % q, why,
            "tz" if st.get("spec_file") and q > 1 and not st.get("qty_set") else ("words" if st.get("qty_set") or q > 1 else "def"),
            ["1 шт.", "5 шт.", "10 шт."])
        rows[-1].update(widget="stepper", n=q, perPlate=per, min=1, max=200)
    for r in st.get("rows", []):
        if not r["k"] or r["key"] == "printing_mode":
            continue
        opts = [o[0] for o in r.get("opts") or []]
        if opts and r["v"] not in opts:
            opts = [r["v"]] + opts
        add(r["key"] or ("info_" + r["k"]), r["k"], r["v"], r["why"], r["src"], opts if not r.get("lock") else [])
        if r["key"] == "infill_sparse_density" and not r.get("lock"):
            rec = (st.get("recommended") or {}).get("infill_sparse_density")
            rows[-1].update(widget="slider", n=int(r["value"]), min=0, max=100, step=5,
                            stops=[o[1] for o in r.get("opts") or []],
                            rec=int(rec) if isinstance(rec, (int, float)) else None)
    return rows


def fit_per_plate(run):
    """Сколько копий помещается на стол в текущем положении (по рабочей зоне принтера)."""
    st = run.st
    try:
        pr = run.bridge.printer()
        vol, shape = pr.get("volume") or [0, 0, 0], pr.get("shape") or "rectangular"
    except Exception:
        return max(1, st.get("quantity", 1))
    per = rules.per_plate(st.get("geo") or {}, vol, shape)
    st["per_plate"] = per
    return per


def plan_warnings(run, m):
    st = run.st
    out = []
    c = st.get("material_conflict")
    if c:
        out.append("%s нарушает ТЗ: %s." % (c["material"], "; ".join(c["violations"])))
    elif st.get("material_all_fail"):
        out.append("Ни один материал не проходит все обязательные требования — взял ближайший. Проверьте требования.")
    size = (st.get("geo") or {}).get("size_mm") or [0, 0, 0]
    if m.get("base", "") in ("PP", "ABS", "PA6", "PA12", "ABS/PA", "PC") and max(size) > 150:
        out.append("Крупная деталь из %s может коробиться — нужна кайма и закрытая камера." % m.get("base"))
    heavy = heavy_note(st)
    if heavy:
        out.append(heavy)
    # Сочетания — со своими кнопками исправления; прочие предупреждения — строкой.
    out += [w for w in W.combo_warnings(st) if w["text"] not in out]
    return out


HEAVY_TRIANGLES = 1000000


def heavy_note(st):
    """Предупреждение о долгой нарезке: детальная модель в нескольких копиях — это миллионы треугольников."""
    tris = int((st.get("geo") or {}).get("triangles") or 0)
    q = max(1, int(st.get("quantity", 1) or 1))
    on_plate = max(1, int(min(q, st.get("per_plate") or q)))
    total = tris * on_plate
    if total < HEAVY_TRIANGLES:
        return None
    word = "копии" if on_plate % 10 in (2, 3, 4) and on_plate % 100 not in (12, 13, 14) else "копий"
    copies = " × %d %s на столе" % (on_plate, word) if on_plate > 1 else ""
    return ("Модель очень детальная: %s тыс. треугольников%s. Одна нарезка займёт несколько минут, "
            "пробные варианты режут трижды — дождитесь полосы прогресса." % (format(tris // 1000, ",").replace(",", " "), copies))


def edit(run, key, value):
    """Правка строки плана: материал, режим, положение, вариант, количество или любая настройка с вариантами."""
    st = run.st
    value = (value or "").strip()
    for r in st.get("plan_rows") or []:  # подпись строки («Заполнение») → ключ
        if key == r["k"]:
            key = r["key"]
    if key in ("material", "Материал"):
        mid = None
        for m in M.MATERIALS:
            if value in (m["name"], m["id"]):
                mid = m["id"]
        if not mid:
            raise ValueError("Материал %s не найден" % value)
        st["fixed_material"] = mid
    elif key in ("mode", "Режим"):
        if value not in ("3D", "5D"):
            raise ValueError("Режим — 3D или 5D")
        st["forced_mode"] = value
        st["orientation"] = None
    elif key in ("orientation", "Положение"):
        titles = st.get("orient_options") or []
        low = [t.lower() for t in titles]
        if value.lower() not in low:
            raise ValueError("Положение — одно из: %s" % ", ".join(titles))
        st["orient_choice"] = low.index(value.lower())
    elif key in ("variant", "Вариант печати"):
        set_priority(st, value)
    elif key in ("quantity", "Количество"):
        m = re.search(r"\d+", value)
        if not m or not 1 <= int(m.group(0)) <= 200:
            raise ValueError("Количество — от 1 до 200")
        st["quantity"] = int(m.group(0))
        st["qty_set"] = True
    else:
        row = [r for r in st.get("rows", []) if r["key"] == key]
        if not row:
            raise ValueError("Эту строку плана меняют словами")
        r = row[0]
        if r.get("lock"):
            raise ValueError("«%s» задано ТЗ: %s" % (r["k"], r["why"]))
        hit = [o for o in r.get("opts") or [] if o[0].lower() == value.lower()]
        if hit:
            newv = hit[0][1]
        else:
            num = re.findall(r"-?\d+(?:[.,]\d+)?", value.replace("−", "-"))
            if not num:
                raise ValueError("Не понял значение «%s»" % value)
            newv = float(num[0].replace(",", "."))
            if isinstance(r["value"], int) and not isinstance(r["value"], bool):
                newv = int(round(newv))
        st.setdefault("overrides", {})[key] = newv
    return build(run, edited=key)


def set_requirements(run, reqs):
    st = run.st
    st["requirements"] = R.merge(st.get("requirements", []), reqs)
    return build(run)


def set_settings(run, values):
    """Настройки, названные пользователем или LLM в фазе плана, — поверх правил."""
    st = run.st
    st.setdefault("overrides", {}).update(values or {})
    return build(run)


def confirm(run):
    """«Подтвердить и нарезать»: материал, копии и настройки — в STE Slicer, затем нарезка."""
    st = run.st
    block = find_block(run.part, st.get("plan_id")) if st.get("plan_id") else None
    if st.get("phase") != "plan":
        raise ValueError("Нечего подтверждать: плана печати нет")
    st["phase"] = "confirmed"
    if block is not None:
        block["status"] = "confirmed"
        run.changed()
    m = M.by_id(st["material_id"])
    s = run.step("set_material", "Поставил материал", {"material": m["id"]})
    try:
        run.bridge.set_material(m["id"])
    except Exception as e:
        run.fail(s, "STE Slicer не принял материал %s: %s" % (m["name"], e))
        raise
    run.done(s, m["name"])
    q = st.get("quantity", 1)
    on_plate = min(q, st.get("per_plate") or q)  # лишние копии слайсер не режет — ставим, сколько помещается
    if st.get("mode") == "classic" and (on_plate > 1 or st.get("copies_made", 1) > 1):
        W.multiply(run, on_plate, total=q)
    W.apply_settings(run)
    return W.slice_part(run)


def in_plan(part):
    return (part.get("state") or {}).get("phase") == "plan"


CONFIRM_WORDS = r"^(да|ок|ok|хорошо|верно|всё верно|все верно|подтверж\w*|нарежь\w*|режь|запускай|поехали|давай)[\s.!]*$"


def is_confirm(text):
    return bool(re.search(CONFIRM_WORDS, (text or "").strip().lower()))
