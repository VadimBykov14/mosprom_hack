# -*- coding: utf-8 -*-
"""Сценарий подготовки детали: загрузка, требования, материал, режим, положение,
настройки, нарезка, варианты, «было → стало», сохранение.

Каждое действие — шаг в карточке «Ход работы», каждый результат — карточка в ленте,
каждое решение — строка паспорта с источником. Функции вызываются и инструментами LLM,
и автопилотом без LLM, поэтому результат один и тот же.
"""
import os
import re
import time
import uuid

from . import catalog as C
from . import geometry as G
from . import materials as M
from . import requirements as R
from . import rules
from . import spec_reader
from .store import from_translit, name_from_text, short_name

SAVE_EVERY = 2.0  # с — во время хода деталь пишется на диск не чаще; в конце хода — всегда

SRC_WORDS = {"tz": "по ТЗ", "words": "ваш выбор", "geom": "геометрия", "rules": "правила", "def": "по умолчанию",
             "assume": "допущение", "catalog": "картотека", "profile": "профиль материала",
             "experience": "опыт"}

KIND_BY_WORD = [
    (r"колес|ролик|wheel|koleso", "wheel"), (r"вал|ось|shaft|val", "shaft"), (r"крыльчат|вентилят|fan", "fan"),
    (r"шестер|gear", "gear"), (r"насос|импеллер|pump|nasos", "impeller"), (r"пыльник|чехол|boot", "boot"),
    (r"фигур|статуэт|figur", "figure"), (r"кронштейн|bracket|kronsht", "bracket"),
]


class Stopped(Exception):
    pass


class Run(object):
    """Один ответ ассистента: сообщение в ленте, ход работы, доступ к мосту и хранилищу."""

    def __init__(self, app, part, external=False):
        self.app = app
        self.part = part
        self.bridge = app.bridge
        self.store = app.store
        last = part["messages"][-1] if part.get("messages") else None
        if last and last.get("role") == "ai" and not external:
            # Ход без слов пользователя (кнопка в паспорте, отмена, ползунок) продолжает прошлое сообщение:
            # шаги — в тот же «Ход работы», а не ещё одно «Выполнил N действий» ниже.
            self.msg = last
            self.msg["started"] = time.time() - (last.pop("duration", 0) or 0)
            tr = [b for b in last.get("blocks") or [] if b.get("type") == "trace"]
            self.trace = tr[0] if tr else None
            if self.trace is not None:
                self.trace["open"] = True
        else:
            self.msg = {"role": "ai", "blocks": [], "time": time.strftime("%H:%M"), "started": time.time()}
            part["messages"].append(self.msg)
            self.trace = None
        self.external = external
        self.stop_flag = False
        self.tool_calls = 0

    @property
    def st(self):
        return self.part.setdefault("state", {})

    # --------------------------------------------------------- лента
    def changed(self, force=False):
        # Ход шлёт десятки обновлений; браузеру — каждое, на диск — не чаще раза в SAVE_EVERY.
        now = time.time()
        save = force or now - getattr(self, "_saved_at", 0) >= SAVE_EVERY
        if save:
            self._saved_at = now
        self.store.changed(self.part, save=save)

    def check_stop(self):
        if self.stop_flag:
            raise Stopped()

    def step(self, tool, title, args=None):
        self.check_stop()
        if self.trace is None:
            self.trace = {"type": "trace", "open": True, "steps": []}
            self.msg["blocks"].insert(0, self.trace)
        s = {"id": "s" + uuid.uuid4().hex[:6], "tool": tool, "title": title, "sum": "Выполняется…", "status": "run",
             "args": args or {}, "result": "", "changes": [], "external": self.external, "t0": time.time()}
        self.trace["steps"].append(s)
        self.changed()
        return s

    def done(self, s, summary, result="", changes=None):
        s["status"] = "done"
        s["sum"] = summary
        s["result"] = result
        s["changes"] = [[k, _fmt(a), _fmt(b)] for k, a, b in (changes or [])]
        s["ms"] = int((time.time() - s.pop("t0", time.time())) * 1000)
        self.changed()

    def fail(self, s, error):
        s["status"] = "error"
        s["sum"] = error
        s["ms"] = int((time.time() - s.pop("t0", time.time())) * 1000)
        self.changed()

    def block(self, b):
        self.msg["blocks"].append(b)
        self.changed()
        return b

    def text(self, t):
        return self.block({"type": "text", "text": t})

    def notice(self, kind, title, text):
        return self.block({"type": "notice", "kind": kind, "title": title, "text": text})

    def finish(self):
        if self.trace:
            self.trace["open"] = False
            for s in self.trace["steps"]:
                if s["status"] == "run":
                    s["status"] = "error"
                    s["sum"] = "Остановлено"
        self.msg["duration"] = int(time.time() - self.msg.pop("started", time.time()))
        if not self.msg["blocks"]:
            self.part["messages"].remove(self.msg)
        self.changed(force=True)


def _fmt(v):
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, float):
        return ("%g" % v)
    return "" if v is None else str(v)


def _n(v):
    return ("%g" % v).replace(".", ",")


# ============================================================ модель

def ensure_scene(run):
    """Сцена STE Slicer одна на все детали: открыли другую деталь — её модель заменила эту.

    Перед действием возвращаем модель детали в сцену в том же положении, с материалом,
    настройками и копиями. Прежняя нарезка в слайсере уже другая — сохранение перенарежет.
    """
    st = run.st
    nid = st.get("node")
    if not nid or not st.get("model_file"):
        return False
    try:
        ids = [m["id"] for m in run.bridge.models()]
    except Exception:
        return False
    if nid in ids:
        return False
    fi = run.store.file(st["model_file"])
    if not fi:
        return False
    s = run.step("load_model", "Вернул деталь в сцену STE Slicer", {"file": fi["name"]})
    new = run.bridge.load_model(fi["path"])
    st["node"] = new
    turn = G.axis_angle(st["pose"]) if st.get("pose") else None
    if turn:
        run.bridge.rotate_vec(new, [float(x) for x in turn[0]], turn[1])
    five = st.get("mode", "classic") != "classic"
    run.bridge.place(new, center=five, offset=axis_offset(st.get("geo") or {}) if five else None)
    if st.get("material_id"):
        run.bridge.set_material(st["material_id"])
    if st.get("settings_applied"):
        run.bridge.set_settings(st["settings_applied"])
    if st.get("overrides"):
        run.bridge.set_settings(st["overrides"])
    if (st.get("copies_made") or 1) > 1:
        run.bridge.multiply(new, st["copies_made"])
    st["scene_stale"] = True
    run.done(s, "%s — положение, материал и настройки как были" % fi["name"])
    return True


def load_model(run, file_id):
    fi = run.store.file(file_id)
    if not fi:
        raise ValueError("Файл %s не найден" % file_id)
    s = run.step("load_model", "Загрузил модель в сцену", {"file": fi["name"]})
    try:
        nid = run.bridge.load_model(fi["path"])
    except Exception as e:
        run.fail(s, "Не удалось загрузить: %s" % e)
        raise
    st = run.st
    st["node"] = nid
    st["model_file"] = fi["id"]
    st["model_name"] = fi["name"]
    tris = run.bridge.mesh(nid)
    size = tris.reshape(-1, 3).max(axis=0) - tris.reshape(-1, 3).min(axis=0)
    run.done(s, "%s · %s мм" % (fi["name"], " × ".join(_n(round(float(v), 1)) for v in (size[0], size[2], size[1]))),
             "node_id %s" % nid)
    part = run.part
    if not part.get("name_user") and (part["name"] in ("Новая деталь", "") or part.get("kind") == "new"):
        # Название — из слов пользователя («ведущее колесо конвейерной ленты»), иначе из имени файла.
        said = " ".join(m.get("text") or "" for m in part.get("messages", []) if m.get("role") == "user")
        guess = os.path.splitext(fi["name"])[0].replace("_", " ").replace("-", " ").strip()
        part["name"] = name_from_text(said) or short_name(from_translit(guess)) or part["name"]
    part["kind"] = guess_kind(part["name"] + " " + fi["name"])
    return nid


def guess_kind(text):
    low = text.lower()
    for rx, kind in KIND_BY_WORD:
        if re.search(rx, low):
            return kind
    return "part"


def analyze(run):
    st = run.st
    s = run.step("analyze_geometry", "Разобрал геометрию", {"node": st.get("node")})
    tris = run.bridge.mesh(st["node"])
    geo = G.analyze(tris)
    st["geo"] = geo
    # Разбор в позе файла — от него считаются все положения; pose — поворот файла → сцена.
    st["geo_file"] = geo
    st["pose"] = [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]]
    try:
        sil = G.silhouette(tris, geo.get("rotation_axis"), geo.get("bore_mm"))
    except Exception:  # картинка не должна ломать разбор
        sil = None
    summary = G.TYPE_WORDS[geo["part_type"]].capitalize()
    if geo.get("bore_mm"):
        summary += ", отверстие Ø%s" % _n(geo["bore_mm"])
    if geo.get("lying_on_side"):
        summary += ", лежит на боку"
    run.done(s, summary, "axis=%s bore=%s type=%s" % (geo.get("rotation_axis"), geo.get("bore_mm"), geo["part_type"]))
    mat = M.by_id(st.get("material_id") or "") or {"density": 1.2}
    size = geo["size_mm"]
    note = None
    if geo.get("lying_on_side") and geo["part_type"] != "general":
        note = {"kind": "info", "text": "В файле деталь лежит на боку. Ось вращения поставлю вертикально, если выберу 5D или так выгоднее."}
    vol = run.app.build_volume()
    try:  # «вне рабочей зоны» решает сам слайсер (BuildVolume помечает узел)
        outside = run.bridge.model_info(st["node"]).get("outside")
    except Exception:
        outside = None
    if outside is None:
        outside = bool(vol and (size[0] > vol[0] or size[2] > vol[2] or size[1] > vol[1]))
    too_big = bool(vol and (size[0] > vol[0] or size[2] > vol[2] or size[1] > vol[1]))
    if outside and too_big:
        note = {"kind": "warn", "text": "Модель %s мм больше рабочей зоны принтера %s мм. Проверьте единицы в файле." % (
            " × ".join(_n(v) for v in size), " × ".join(_n(v) for v in vol))}
    elif outside:
        note = {"kind": "info", "text": "STE Slicer показывает модель за пределами рабочей зоны. При укладке поставлю её на стол."}
    if max(size) < 3:
        note = {"kind": "warn", "text": "Модель меньше 3 мм — похоже, файл в метрах или дюймах."}
    run.block({"type": "model", "name": st.get("model_name", ""), "dims": " × ".join(_n(v) for v in (size[0], size[2], size[1])),
               "volume": "%s см³" % _n(geo["volume_cm3"]), "mass": "%s г" % _n(round(geo["volume_cm3"] * mat["density"])),
               "massLabel": "Масса" if not st.get("material_id") else "Масса в %s" % M.by_id(st["material_id"])["abbr"],
               "features": G.features(geo), "note": note, "kind": run.part.get("kind", "part"),
               "partType": geo["part_type"], "sil": sil})
    return geo


# ============================================================ требования

def read_spec(run, file_id):
    fi = run.store.file(file_id)
    s = run.step("read_spec", "Прочитал ТЗ", {"file": fi["name"] if fi else file_id})
    try:
        text = spec_reader.read(fi["path"])
    except Exception as e:
        run.fail(s, "Не удалось прочитать %s: %s" % (fi["name"] if fi else file_id, e))
        raise
    st = run.st
    st["spec_file"] = fi["name"]
    st["spec_text"] = text[:30000]
    reqs = R.extract(text)
    st["requirements"] = R.merge(st.get("requirements_words", []), reqs)
    name = spec_reader.part_name(text)
    if name and not run.part.get("name_user"):
        run.part["name"] = short_name(name) or run.part["name"]
        run.part["kind"] = guess_kind(name)
    qty = spec_reader.quantity(text)
    if qty > 1:
        st["quantity"] = qty
    c = R.counts(st["requirements"])
    if not text.strip():
        run.fail(s, "В файле нет текста — пришлите ТЗ в DOCX или TXT")
        return text
    run.done(s, "%d требований: %d обязательных, %d запретов, %d желательных" % (
        len(st["requirements"]), c["must"], c["ban"], c["want"]), "%d символов" % len(text))
    show_requirements(run)
    return text


def show_requirements(run):
    st = run.st
    reqs = st.get("requirements", [])
    b = {"type": "reqs", "id": "q" + uuid.uuid4().hex[:6], "file": st.get("spec_file", "ваши слова"),
         "counts": R.counts(reqs), "groups": R.group(reqs)}
    # Карточка требований одна на деталь: обновляем последнюю, если есть.
    run.block(b)
    return b


def set_requirements(run, items, replace=False):
    st = run.st
    new = R.normalize(items, source="llm")
    s = run.step("set_requirements", "Уточнил требования", {"count": len(new)})
    old = st.get("requirements", [])
    st["requirements"] = new + [r for r in old if r["tag"] not in set(x["tag"] for x in new)] if not replace else new
    run.done(s, "%d требований" % len(st["requirements"]))
    return st["requirements"]


# ============================================================ слова пользователя

MATERIAL_WORDS = [
    (r"tpu\s*d\s*70|тпу\s*d70", "enduse_tpu_d70"), (r"tpu\s*c5", "fiberpart_tpu_c5"), (r"tpu\s*g30", "fiberpart_tpu_g30"),
    (r"tpv", "sealant_tpv80"), (r"pa\s*12|па\s*12", "fiberpart_pa12_g12"), (r"\bpa\s*6\b|па\s*6|полиамид|нейлон|nylon", "enduse_pa6"),
    (r"abs\s*/\s*pa|g8", "fiberpart_abspa_g8"), (r"pp\s*g30|pp-g", "fiberpart_pp_g30"), (r"\bpetg\b|петг|петж", "enduse_petg"),
    (r"\bpla\b|пла\b", "proto_pla"), (r"\babs\b|абс", "enduse_abs"), (r"\bpc\b|поликарбонат", "enduse_pc"),
    (r"\bpp\b|полипропилен", "enduse_pp"), (r"sebs", "sealant_sebs"), (r"\btpe\b", "sealant_tpe"),
]


def parse_words(text):
    """Что понятно из слов пользователя без LLM: материал, количество, вариант, режим, пожелания."""
    low = (text or "").lower()
    out = {}
    for rx, mid in MATERIAL_WORDS:
        if re.search(rx, low):
            out["material"] = mid
            break
    m = re.search(r"(\d+)\s*(?:шт|штук|копи|экземпляр|фигур)|[x×]\s*(\d+)\b|парти\w* из (\d+)", low)
    if m:
        out["quantity"] = int(m.group(1) or m.group(2) or m.group(3))
    if re.search(r"быстр|срочн", low):
        out["variant"] = "fast"
    elif re.search(r"дешев|дёшев|эконом|подешевле", low):
        out["variant"] = "cheap"
    elif re.search(r"качеств|точн|аккуратн", low):
        out["variant"] = "quality"
    if re.search(r"\b5d\b|5д|цилиндрич", low):
        out["mode"] = "5D"
    elif re.search(r"\b3d\b|3д|плоскими слоями", low):
        out["mode"] = "3D"
    if re.search(r"без поддерж", low):
        out["no_support"] = True
    reqs = R.extract(text or "")
    if reqs:
        for r in reqs:
            r["source"] = "words"
            r["clause"] = ""
        out["requirements"] = reqs
    return out


# ============================================================ вопрос без ТЗ

QUESTION_OPTIONS = [
    ("В помещении, без нагрузки", []),
    ("Нагрузка и удары", [("impact_resistant", "must"), ("fatigue_resistant", "must")]),
    ("На улице, солнце", [("uv_resistant", "must"), ("temp_service_min", "must", 60)]),
    ("Масло или топливо", [("oil_resistant", "must"), ("fuel_resistant", "must")]),
    ("Гибкая деталь", [("flexible", "must")]),
]


def need_question(run):
    st = run.st
    if st.get("requirements") or st.get("fixed_material") or st.get("asked", 0) >= 2:
        return False
    return bool(run.app.settings.get("ask_questions", True))


ANSWER_SEP = "; "
EXCLUSIVE_OPTIONS = ["В помещении, без нагрузки"]


def default_text(t):
    """Подпись под вопросом всегда в одном виде: «По умолчанию — …»."""
    t = (t or "").strip()
    if not t:
        return ""
    t = re.sub(r"^(без ответа|если не ответите|если не ответить|по умолчанию)\s*(возьму|будет)?\s*[:,—–-]?\s*", "", t, flags=re.I)
    if not t:
        return ""
    w = t.split()[0]
    if len(w) == 1 or w[1:].islower():  # «В помещении» → «в помещении», но PETG остаётся PETG
        t = t[:1].lower() + t[1:]
    return "По умолчанию — " + t


def ask(run, question=None, options=None, why=None, default=None, qid=None, multi=None):
    st = run.st
    st["asked"] = st.get("asked", 0) + 1
    if multi is None:
        multi = not options  # свой вопрос по условиям работы — можно отметить несколько
    b = {"type": "ask", "id": qid or ("a" + uuid.uuid4().hex[:6]),
         "multi": bool(multi), "exclusive": [o for o in EXCLUSIVE_OPTIONS if o in (options or [o[0] for o in QUESTION_OPTIONS])],
         "why": why or "Вопрос %d из 2. ТЗ нет, а от ответа зависят материал и заполнение — остальное решу сам." % st["asked"],
         "q": question or ("Где будет работать деталь и какая на неё нагрузка? Можно выбрать несколько" if multi else
                           "Где будет работать деталь и какая на неё нагрузка?"),
         "options": options or [o[0] for o in QUESTION_OPTIONS],
         "def": default_text(default) or "По умолчанию — EnduSe PETG, заполнение 40 %.",
         "answer": None}
    run.block(b)
    run.part["status"] = "ask"
    st["pending_question"] = b["id"]
    return b


def split_answer(answer):
    return [x.strip() for x in (answer or "").split(ANSWER_SEP.strip()) if x.strip()]


def answer_to_requirements(answer):
    """Ответ на вопрос (один вариант или несколько через «; ») → требования.

    Возвращает (требования, понят ли ответ). Свои слова разбирает извлекатель требований.
    """
    labels = dict(QUESTION_OPTIONS)
    out, known = [], False
    for part in split_answer(answer):
        if part in labels:
            known = True
            for t in labels[part]:
                out.append(R.make(t[0], t[1], value=t[2] if len(t) > 2 else None, quote="Ваш ответ: «%s»" % part,
                                  source="words"))
        else:
            extra = R.extract(part)
            for r in extra:
                r["source"] = "words"
            known = known or bool(extra)
            out.extend(extra)
    seen, uniq = set(), []
    for r in out:
        if r["tag"] not in seen:
            seen.add(r["tag"])
            uniq.append(r)
    return uniq, known


# ============================================================ материал

def select_material(run, fixed=None, apply=True):
    """Подбор материала по правилам. apply=False — только решение для плана, без записи в STE Slicer."""
    st = run.st
    reqs = st.get("requirements", [])
    fixed = fixed or st.get("fixed_material")
    s = run.step("select_material", "Подобрал материал", {"requirements": len(reqs), "fixed": fixed})
    try:  # какие материалы есть у этого принтера и сопла — из профилей STE Slicer
        missing = M.sync(run.bridge.material_profiles())
    except Exception:
        missing = []
    sel = rules.select_material(reqs, fixed=fixed)
    m = sel["pick"]
    st["material_id"] = m["id"]
    st["material_src"] = "words" if fixed else ("tz" if st.get("spec_file") else ("words" if reqs else "assume"))
    st["material_reason"] = sel["reason"]
    st["rejected_materials"] = ["%s — %s" % (a["name"], a["why"]) for a in sel["almost"]]
    st["material_alts"] = [x for x in sel["cols"] if x != m["name"]][:3]
    if apply:
        try:
            run.bridge.set_material(m["id"])
        except Exception as e:
            run.fail(s, "STE Slicer не принял материал %s: %s" % (m["name"], e))
            raise
    must = [r for r in reqs if r["kind"] in ("must", "ban")]
    summary = m["name"]
    if must:
        ok = len(must) - len(sel.get("pick_score", {}).get("violations", []))
        summary += " — проходит %d из %d обязательных" % (ok, len(must))
    run.done(s, summary, "%s · профилей STE Slicer: %d%s" % (
        m["id"], M.SYNCED["profiles"], ", нет на принтере: %s" % ", ".join(missing) if missing else ""))
    run.part["material"] = m["abbr"] if len(m["abbr"]) <= 6 else m["name"].split(" ", 1)[-1]
    view = {"type": "material", "pick": {"name": m["name"], "abbr": m["abbr"], "line": m["line"],
                                         "why": m.get("note", ""), "src": SRC_WORDS[st["material_src"]]},
            "cols": sel["cols"], "rows": sel["rows"], "almost": sel["almost"], "conflict": sel["conflict"],
            "allFail": sel.get("all_fail", False),
            # Каждый материал из таблицы — простыми словами: что это и чего боится.
            "plain": [[n, (_by_name(n) or {}).get("plain", "")] for n in sel["cols"]]}
    st["material_view"] = view if reqs else None
    st["material_conflict"] = sel.get("conflict")
    st["material_all_fail"] = bool(sel.get("all_fail"))
    if not apply:  # в плане таблица открывается из строки «Материал», а не отдельной карточкой
        return m
    if reqs:
        run.block(view)
    if sel.get("conflict"):
        run.notice("warn", "%s нарушает ТЗ" % sel["conflict"]["material"],
                   "; ".join(sel["conflict"]["violations"]) + ". Печатаю, как вы сказали, но отметил это в паспорте.")
    return m


def _by_name(name):
    for m in M.MATERIALS:
        if m["name"] == name:
            return m
    return None


# ============================================================ режим и положение

def choose_mode(run, forced=None, compare=True, emit=True):
    st = run.st
    geo = st.get("geo_file") or st["geo"]
    s = run.step("choose_mode", "Выбрал режим печати", {"forced": forced})
    dec = rules.choose_mode(geo, st.get("requirements"))
    if forced == "3D":
        dec = dict(dec, mode="classic", base=None, reason="Вы выбрали 3D.", confidence="high")
    elif forced == "5D" and dec["mode"] == "classic":
        base = dec.get("base5d")
        if base:
            reason = "Вы выбрали 5D: %s." % rules.base_words(geo, base)
        else:
            reason = ("Вы выбрали 5D, но оси вращения у детали нет: база Ø%s по умолчанию STE Slicer, "
                      "слои вокруг оси лягут неровно. Проверьте превью." % _n(rules.DEFAULT_BASE_5D))
        dec = dict(dec, mode="cylindrical_full", base=base or rules.DEFAULT_BASE_5D, reason=reason, confidence="medium")
    st["mode"] = dec["mode"]
    st["base"] = dec.get("base")
    st["mode_reason"] = dec["reason"]
    st["mode_src"] = "words" if forced else "geom"
    st["can5d"] = bool(dec.get("can5d"))
    run.done(s, "%s%s" % (rules.MODE_TITLE[dec["mode"]], ", база Ø%s" % _n(dec["base"]) if dec.get("base") else ""), dec["reason"])
    run.part["mode"] = rules.MODE_SHORT[dec["mode"]]
    st["mode_conf"] = dec.get("confidence", "high")
    st["modes_view"] = None
    if geo["part_type"].startswith("rotation"):
        cols = rules.mode_compare(geo, dec["mode"])
        st["modes_view"] = {"type": "modes", "chosen": rules.MODE_SHORT[dec["mode"]], "cols": cols,
                            "reason": dec["reason"], "confidence": dec.get("confidence", "high")}
        if emit:
            run.block(st["modes_view"])
    return dec


def axis_offset(geo):
    """Смещение оси детали от центра габарита по полу (x, z), мм, или None, если оно меньше 0,05 мм."""
    off = geo.get("axis_offset_mm")
    if not off or max(abs(off[0]), abs(off[2])) < 0.05:
        return None
    return [float(off[0]), 0.0, float(off[2])]


def orient(run, emit=True, choice=None):
    """Положить деталь на стол. Кандидаты считаются по разбору в позе файла (geo_file),
    поворот — от текущей позы (pose) к выбранной, поэтому повторный выбор не накапливает повороты.
    choice — номер кандидата (из вопроса или плана), иначе лучший по правилам."""
    import numpy as np
    st = run.st
    geo0 = st.get("geo_file") or st["geo"]
    mode = st.get("mode", "classic")
    cands = rules.orientation_candidates(geo0, mode)
    if choice is not None and 0 <= int(choice) < len(cands):
        for i, c in enumerate(cands):
            c["chosen"] = i == int(choice)
    chosen = [c for c in cands if c["chosen"]][0]
    s = run.step("rotate_model", "Положил деталь на стол", {"pose": chosen["title"], "center": chosen.get("center")})
    pose = np.array(st.get("pose") or np.eye(3), dtype=float)
    now = pose.dot(chosen["dir"])  # куда сейчас смотрит нужное направление файла
    axis, deg = G.rotation_to(now, chosen["target"])
    if deg > 0.5:
        run.bridge.rotate_vec(st["node"], axis, deg)
        pose = G.rot_matrix(axis, deg).dot(pose)
    st["pose"] = pose.tolist()
    run.bridge.place(st["node"], center=bool(chosen.get("center")))
    st["orientation"] = chosen["title"]
    st["orient_idx"] = cands.index(chosen)
    st["orient_options"] = [c["title"] for c in cands]
    # Разбор в новом положении: нависания и опора для поддержек и прилипания.
    st["geo"] = G.analyze(run.bridge.mesh(st["node"]))
    off = axis_offset(st["geo"]) if chosen.get("center") else None
    if off:  # в 5D центр стола — на оси детали, а не на центре габарита (вал с кулачком)
        run.bridge.place(st["node"], center=True, offset=off)
        # Сдвиг по столу форму не меняет — заново не разбираем, только ось теперь в центре.
        st["geo"] = dict(st["geo"], axis_offset_mm=[0.0, 0.0, 0.0])
    run.done(s, chosen["title"], "поворот %s° вокруг (%s)" % (_n(round(deg, 1)), ", ".join(_n(round(a, 3)) for a in axis)))
    if emit and mode == "classic" and len(cands) > 1 and cands[0]["overhang_mm2"] + 50 < cands[1]["overhang_mm2"] * 0.9 + 50:
        items = []
        for c in cands:
            items.append({"title": c["title"], "chosen": c["chosen"], "pic": c.get("down"),
                          "rows": [["Нависания", "%d мм²" % c["overhang_mm2"]], ["Опора", "%d мм²" % c.get("bed_mm2", 0)]],
                          "why": c["why"]})
        run.block({"type": "orient", "items": items})
    return chosen


def plural(n, one, few, many):
    n = abs(int(n)) % 100
    if 10 < n < 20:
        return many
    n %= 10
    return one if n == 1 else few if 2 <= n <= 4 else many


def multiply(run, count, total=None):
    st = run.st
    if not st.get("node"):
        raise ValueError("Сначала приложите модель — копировать нечего")
    if st.get("mode", "classic") != "classic":
        raise ValueError("В 5D печатается одна деталь — копии на одном столе возможны только в 3D")
    if count < 1 or count > 200:
        raise ValueError("Количество копий — от 1 до 200")
    had = bool(st.get("totals"))
    s = run.step("multiply", "Сделал копии для партии", {"count": count})
    removed = run.bridge.remove_copies(st["node"]) if hasattr(run.bridge, "remove_copies") else 0
    if count > 1:
        run.bridge.multiply(st["node"], count)
    st["quantity"] = total or count
    st["copies_made"] = count
    st.pop("totals", None)  # на столе другая партия — прежние время и расход не верны
    run.done(s, "%d шт. на столе, расставил слайсер%s" % (
        count, (" (партия %d шт. — %d запуска)" % (total, -(-total // count))) if total and total > count else ""),
        "убрал прежних копий: %d" % removed)
    if had:
        slice_part(run, card=False)


# ============================================================ настройки

def apply_overrides(rows, overrides):
    """Значения, которые пользователь выбрал в плане или назвал словами, поверх правил."""
    keys = set(r["key"] for r in rows if r["key"])
    for r in rows:
        if r["key"] in (overrides or {}):
            v = overrides[r["key"]]
            r["value"] = v
            label = [o[0] for o in r.get("opts") or [] if o[1] == v]
            if label:
                r["v"] = label[0]
            elif isinstance(v, bool):
                r["v"] = "да" if v else "нет"
            elif r["key"] == "infill_sparse_density":
                r["v"] = "%d %%" % int(v)
            else:
                r["v"] = _fmt(v)
            r["src"], r["why"] = "words", "Вы выбрали."
    # Ключи, которых нет среди строк правил (их назвали словами или LLM), — отдельными строками.
    for k, v in (overrides or {}).items():
        if k not in keys:
            rows.append({"key": k, "value": v, "k": k, "v": _fmt(v), "src": "words", "why": "Вы попросили.",
                         "lock": False, "opts": []})
    return rows


def apply_settings(run, variant=None, title="Задал настройки", dry=False):
    """Настройки по правилам. dry=True — только строки для плана, в STE Slicer ничего не пишется."""
    st = run.st
    m = M.by_id(st["material_id"])
    variant = variant or st.get("variant") or "quality"
    prefs = dict(st.get("prefs") or {})
    if st.get("no_support"):
        prefs["supports"] = "none"
    rows = rules.plan_settings(st["geo"], st.get("mode", "classic"), st.get("base"), m, st.get("requirements", []),
                               variant=variant, prefs=prefs)
    for r in rows:
        if r["key"] == "printing_mode":
            r["src"] = st.get("mode_src", "geom")
            r["why"] = st.get("mode_reason", "")
    learned(run, rows)
    st["recommended"] = rules.settings_dict(rows)  # что советуют правила и опыт — до правок пользователя
    apply_overrides(rows, st.get("overrides"))
    values = rules.settings_dict(rows)
    if dry:
        st["rows"] = rows
        st["variant"] = variant
        return rows, []
    s = run.step("set_settings", title, {"variant": variant})
    # То, что плагин записал раньше, а в новом плане не задано (другой режим, другой ответ), —
    # вернуть к профилю: иначе в слайсере остаются, например, кайма или база от прошлого режима.
    stale = [k for k in (st.get("settings_applied") or {}) if k not in values]
    if stale and hasattr(run.bridge, "reset_settings"):
        run.bridge.reset_settings(stale)
    changes = run.bridge.set_settings(values)
    rejected = check_settings(run, changes)
    if rejected:
        changes = [c for c in changes if c[0] not in rejected]
        for r in rows:
            if r["key"] in rejected:
                r["v"] = _fmt(rejected[r["key"]]["kept"])
                r["value"] = rejected[r["key"]]["kept"]
                r["src"], r["why"] = "rules", rejected[r["key"]]["why"]
        values = rules.settings_dict(rows)
    st["rows"] = rows
    st["variant"] = variant
    st["settings_applied"] = values
    run.done(s, "%d параметров изменено" % len(changes) if changes else "Без изменений", "", changes)
    st.pop("totals", None)
    slicer_warnings(run)
    refresh_values(run)
    return rows, changes


def learned(run, rows):
    """Опыт прошлых печатей того же материала и режима — в строки плана (источник «опыт»)."""
    from . import experience as E
    st = run.st
    try:
        items = E.lessons(getattr(run.app, "catalog", None), st.get("material_id"), st.get("mode"),
                          exclude=st.get("catalog_record"))
    except Exception:  # картотека — подсказка, а не условие: без неё подбираем по правилам
        return []
    done = E.apply(rows, items)
    if not done:
        return done
    info = setting_infos(run, [d["key"] for d in done])
    for r in rows:
        if r.get("src") == E.SRC and r["k"] == r["key"] and (info.get(r["key"]) or {}).get("label"):
            r["k"] = info[r["key"]]["label"]
    for d in done:
        d["label"] = (info.get(d["key"]) or {}).get("label") or d.get("label") or d["key"]
    sig = sorted("%s=%s" % (d["key"], d["to"]) for d in done)
    if st.get("experience_noted") != sig:  # сказать один раз, а не при каждом пересчёте плана
        st["experience_noted"] = sig
        run.text(E.note(done))
    return done


ERROR_STATES = {"MinimumError": "меньше допустимого", "MaximumError": "больше допустимого",
                "Exception": "STE Slicer не смог его посчитать", "Invalid": "недопустимое значение"}


def check_settings(run, changes):
    """Проверка изменённых параметров самим STE Slicer (validationState, как у MachineErrorChecker).

    Значение, которое слайсер считает ошибкой, возвращаем к прежнему и говорим, почему.
    Возвращает {key: {"kept": прежнее значение, "why": текст}}.
    """
    if not changes or not hasattr(run.bridge, "validate"):
        return {}
    try:
        states = run.bridge.validate([c[0] for c in changes])
    except Exception:
        return {}
    bad = dict((k, v) for k, v in states.items() if v in ERROR_STATES)
    if not bad:
        return {}
    try:
        info = run.bridge.setting_info(list(bad))
    except Exception:
        info = {}
    olds = dict((c[0], c[1]) for c in changes)
    news = dict((c[0], c[2]) for c in changes)
    run.bridge.set_settings(dict((k, olds[k]) for k in bad if olds.get(k) is not None))
    out, lines = {}, []
    for k, state in bad.items():
        i = info.get(k) or {}
        label = i.get("label") or k
        unit = (" " + i["unit"]) if i.get("unit") else ""
        lim = ""
        if state == "MinimumError" and i.get("min") is not None:
            lim = " (не меньше %s%s)" % (_fmt(i["min"]), unit)
        elif state == "MaximumError" and i.get("max") is not None:
            lim = " (не больше %s%s)" % (_fmt(i["max"]), unit)
        why = "STE Slicer не принял %s%s: %s%s." % (_fmt(news.get(k)), unit, ERROR_STATES[state], lim)
        out[k] = {"kept": olds.get(k), "why": why}
        lines.append("«%s» — %s%s: %s%s; оставил %s%s" % (label, _fmt(news.get(k)), unit, ERROR_STATES[state], lim,
                                                          _fmt(olds.get(k)), unit))
    seen = run.st.setdefault("rejected_seen", [])
    sig = sorted("%s=%s" % (k, news.get(k)) for k in bad)
    if sig not in seen:  # варианты печати режут деталь трижды — одно и то же сообщение один раз
        seen.append(sig)
        run.notice("warn", "STE Slicer не принял %d %s" % (len(bad), "параметр" if len(bad) == 1 else "параметра"),
                   "; ".join(lines) + ".")
    return out


def slicer_warnings(run):
    """Предупреждения ModelChecker STE Slicer (коробление) — один раз на материал и модель."""
    if not hasattr(run.bridge, "model_warnings"):
        return []
    try:
        warns = run.bridge.model_warnings() or []
    except Exception:
        return []
    st = run.st
    key = "%s|%s" % (st.get("material_id"), st.get("node"))
    if warns and st.get("warned_for") != key:
        st["warned_for"] = key
        run.notice("warn", "Риск коробления", " ".join(warns) + " Помогут кайма или подложка и закрытая камера.")
    st["slicer_warnings"] = warns
    return warns


# ============================================================ сохранение

def save(run, project=True, gcode=True, folder=None, name=None):
    st = run.st
    folder = folder or run.app.settings.get("folder") or os.path.expanduser("~")
    if not os.path.isdir(folder):
        os.makedirs(folder)
    m = M.by_id(st.get("material_id") or "") or {"name": ""}
    rows = dict((r["key"], r["value"]) for r in st.get("rows", []) if r["key"])
    tpl = run.app.settings.get("template") or C.DEFAULT_TEMPLATE
    vals = C.values_for(run.part["name"], run.part.get("mode") or "3D", m["name"],
                        rows.get("layer_height", 0.1), rows.get("infill_sparse_density", 20), rows.get("wall_line_count"),
                        printer=run.app.printer_name(), copies=st.get("quantity", 1))
    base = name or C.make_name(tpl, vals)
    s = run.step("save_project", "Сохранил файлы", {"folder": folder, "name": base})
    paths = []
    try:
        if project:
            paths.append(run.bridge.save_project(os.path.join(folder, base + ".3mf")))
        if gcode:
            if not st.get("totals") or st.get("scene_stale"):
                slice_part(run, card=False)
            if not st.get("totals"):
                raise ValueError("нарезка не удалась, G-code не записан" + (" (проект 3MF сохранён)" if paths else ""))
            paths.append(run.bridge.save_gcode(os.path.join(folder, base + ".gcode")))
    except Exception as e:
        run.fail(s, "Не удалось сохранить: %s" % e)
        raise
    run.done(s, ", ".join(os.path.basename(p) for p in paths))
    t = st.get("totals") or {}
    rec = run.app.catalog.add({
        "part_id": run.part["id"], "name": run.part["name"], "kind": run.part.get("kind", "part"),
        "mode": run.part.get("mode") or "3D", "material": m["name"], "profile": "слой %s" % _n(rows.get("layer_height", 0.1)),
        "infill": "%d %%" % rows.get("infill_sparse_density", 20), "walls": rows.get("wall_line_count", ""),
        "time": t.get("time", "—"), "mass": t.get("mass", "—"), "cost": t.get("cost", "—"),
        "file": base + (".3mf" if project else ".gcode"), "path": paths[0] if paths else "", "paths": paths,
        "model_path": (run.store.file(st.get("model_file")) or {}).get("path", ""),
        "settings": rows, "material_id": st.get("material_id"), "requirements": st.get("requirements", []),
    })
    run.part["status"] = "saved"
    st["catalog_record"] = rec["id"]  # разбор печати по фото допишет в эту запись, что пошло не так
    run.block({"type": "saved", "paths": paths, "catalog": rec["id"]})
    return paths, rec


# ============================================================ автопилот

def prepare(run, words=None):
    """Подготовка новой детали до плана печати.

    Возвращает "asked" — ждём ответов на вопросы, "plan" — план показан и ждёт подтверждения,
    "done" — нечего готовить. Нарезка — только после подтверждения (plan.confirm).
    """
    from . import plan as P
    st = run.st
    part = run.part
    words = words or {}
    if words.get("material"):
        st["fixed_material"] = words["material"]
    if words.get("variant"):
        st["variant"] = words["variant"]
        st["variant_src"] = "words"
    if words.get("mode"):
        st["forced_mode"] = words["mode"]
    if words.get("no_support"):
        st["no_support"] = True
    if words.get("quantity"):
        st["quantity"] = words["quantity"]
        st["qty_set"] = True  # количество названо словами — во втором раунде не спрашиваем
    if words.get("requirements"):
        st["requirements_words"] = words["requirements"]
        st["requirements"] = R.merge(st.get("requirements", []), words["requirements"])
    files = [run.store.file(f["id"]) for f in part.get("files", [])]
    files = [f for f in files if f]
    model = [f for f in files if f["kind"] == "model"]
    spec = [f for f in files if f["kind"] == "spec"]
    if not st.get("node"):
        if not model:
            run.text("Приложите модель — STL, OBJ или 3MF. Без неё нечего настраивать.")
            return "done"
        load_model(run, model[-1]["id"])
        analyze(run)
    if spec and not st.get("spec_file"):
        read_spec(run, spec[-1]["id"])
    elif st.get("requirements") and not st.get("spec_file") and not st.get("reqs_shown"):
        st["reqs_shown"] = True
        show_requirements(run)
    # Вопросы в два раунда: условия работы, затем «как делать». Пустой раунд пропускается.
    while not st.get("questions_done"):
        items = P.questions_for(run, words)
        if items:
            P.ask_all(run, items)
            return "asked"
        st["q_round"] = st.get("q_round", 0) + 1
        if st["q_round"] >= 2:
            st["questions_done"] = True
    P.build(run)
    return "plan"


# Вынесенные части сценария — здесь же, чтобы W.<функция> работало как раньше.
from .params import (  # noqa: E402,F401
    PRINT_KEYS, UNITS, apply_fix, coerce_value, combo_values, combo_warnings,
    list_settings, refresh_values, reset_user_settings, set_user_settings, setting_infos,
    show_value, warn_new_combos)
from .slicing import (  # noqa: E402,F401
    DEFAULT_SLICE_SECONDS, FEATURE_NAMES, SLICE_STAGES, _read_times, _slice_volume,
    _times_path, check_result, checks, cost_of, remember_slice_time,
    slice_expectation, slice_fix, slice_part, slice_stage, time_progress,
    totals_from, variants)
from .history import (  # noqa: E402,F401
    MAX_UNDO, SNAPSHOT_SKIP, _expire_snapshots, diff_block, set_value,
    set_variant, snapshot, undo)
from .passport import (  # noqa: E402,F401
    build_passport, suggestions, summary_text)
