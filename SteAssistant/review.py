# -*- coding: utf-8 -*-
"""Разбор напечатанной детали по фото: что пошло не так и какие параметры поменять.

Порядок:
1. Пользователь прикладывает фото напечатанной детали («Разобрать печать по фото»).
2. Модель для фото (или основная, если она видит изображения) отвечает JSON: какие дефекты
   из каталога видны и уверена ли она. Если фото непонятное — возвращает вопрос, и ассистент
   просит уточнить или переснять, а дефект можно выбрать самому.
3. Без LLM — пользователь выбирает дефект из каталога; слова из сообщения («паутина»,
   «отклеилась») отмечают подходящие заранее.
4. Исправления считаются от текущих значений STE Slicer по правилам каталога и применяются
   той же правкой, что в паспорте (проверка слайсером, перенарезка, «было → стало»).
"""
import json
import re
import uuid

from . import materials as M

# id, название, как выглядит, слова в сообщении, исправления [(ключ, шаг, предел, почему)].
# Шаг — число (прибавить), "=значение" (поставить) или None (только совет).
DEFECTS = [
    ("stringing", "Паутина и волоски", "тонкие нити между частями детали",
     ("паутин", "волос", "нитк", "сопл", "string"),
     [("retraction_amount", 1.0, 8.0, "длиннее откат — нить не тянется за соплом"),
      ("retraction_speed", 10.0, 60.0, "быстрее откат"),
      ("material_print_temperature", -10.0, None, "холоднее — пластик меньше течёт")]),
    ("warping", "Коробление, углы оторвались", "края и углы приподнялись от стола, дно выгнуто",
     ("короб", "углы", "оторв", "отклеил", "отлип", "выгнул", "warp"),
     [("adhesion_type", "=brim", None, "кайма держит углы"),
      ("brim_width", "=10", None, "кайма шире"),
      ("material_bed_temperature", 10.0, 110.0, "стол горячее — меньше усадка у дна"),
      ("cool_fan_speed", "=0", None, "без обдува деталь остывает ровнее")]),
    ("layers", "Расслоение по слоям", "трещины вдоль слоёв, слои расходятся",
     ("рассло", "слои расх", "трещ", "разош", "layer"),
     [("material_print_temperature", 10.0, None, "горячее — слои лучше спекаются"),
      ("cool_fan_speed", -30.0, 0.0, "меньше обдув — слой не успевает остыть"),
      ("speed_print", -10.0, 15.0, "медленнее — больше прогрев")]),
    ("under", "Недоэкструзия: пропуски и дыры", "тонкие стенки, щели между линиями, дыры в крышке",
     ("недоэкст", "пропуск", "дыр", "щел", "тонк", "не хватает"),
     [("material_flow", 5.0, 115.0, "чуть больше пластика"),
      ("material_print_temperature", 5.0, None, "пластик текучее"),
      ("speed_print", -10.0, 15.0, "медленнее — экструдер успевает")]),
    ("over", "Переэкструзия: наплывы", "раздутые стенки, наплывы, размеры больше",
     ("наплыв", "переэкст", "раздут", "лишн", "больше разм"),
     [("material_flow", -5.0, 85.0, "чуть меньше пластика")]),
    ("foot", "«Слоновья нога»", "первые слои расплющены и шире детали",
     ("слонов", "нога", "первый слой шир", "расплющ"),
     [("xy_offset_layer_0", -0.2, -1.0, "первый слой уже — компенсирует расплющивание"),
      ("material_bed_temperature", -5.0, 40.0, "стол чуть холоднее")]),
    ("sag", "Провисание нависаний", "нижние поверхности и мосты провисли, свисают нити",
     ("провис", "нависан", "мост", "свис"),
     [("support_enable", "=true", None, "поддержки под нависаниями"),
      ("cool_fan_speed", 30.0, 100.0, "сильнее обдув — нависание застывает быстрее")]),
    ("surface", "Неровная поверхность", "рябь, полосы, видны слои",
     ("ряб", "полос", "неровн", "шерохов", "видны слои", "поверхн"),
     [("speed_wall_0", -10.0, 10.0, "медленнее внешняя стенка — ровнее"),
      ("layer_height", -0.05, 0.06, "тоньше слой — меньше ступенек")]),
    ("weak", "Деталь хрупкая, сломалась", "лопнула, сломалась по слою или по заполнению",
     ("слом", "лопн", "хрупк", "треснул", "непрочн"),
     [("wall_line_count", 1.0, 6.0, "ещё одна стенка — прочнее оболочка"),
      ("infill_sparse_density", 20.0, 100.0, "плотнее заполнение"),
      ("material_print_temperature", 5.0, None, "лучше спекаются слои")]),
    ("seam", "Заметный шов", "вертикальная полоса из точек начала слоёв",
     ("шов", "точки начала", "прыщ", "zit"),
     [("z_seam_type", "=back", None, "шов одной линией сзади")]),
    ("size", "Размер или отверстие не те", "отверстие меньше, деталь не садится",
     ("размер", "отверст", "не сад", "не налез", "допуск"),
     [("material_flow", -3.0, 90.0, "меньше пластика — отверстие не заплывает")]),
]
BY_ID = dict((d[0], d) for d in DEFECTS)

PROMPT = """Ты технолог 3D-печати. На фото — деталь, напечатанная на FDM-принтере{ctx}.
Найди дефекты печати только из этого списка (id — название — как выглядит):
{catalog}

Ответь строго JSON без пояснений:
{{"clear": true|false, "question": "...", "defects": [{{"id": "...", "confidence": 0.0-1.0, "where": "где на детали"}}], "notes": "одна фраза"}}
clear=false и вопрос, если на фото не видно детали, она размыта, слишком далеко, засвечена или дефект нельзя
определить — тогда спроси, что именно не так или как переснять (крупнее, при боковом свете, с нужной стороны).
Если дефектов нет — defects пустой. Пиши по-русски."""


def catalog():
    return [{"id": d[0], "title": d[1], "signs": d[2]} for d in DEFECTS]


def from_words(text):
    """Дефекты, названные словами в сообщении."""
    low = (text or "").lower()
    return [d[0] for d in DEFECTS if any(w in low for w in d[3])]


def parse_reply(text):
    """JSON из ответа модели для фото (бывает в ```json … ```). None — не разобрать."""
    if not text:
        return None
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        return None
    try:
        data = json.loads(m.group(0))
    except ValueError:
        return None
    out = {"clear": bool(data.get("clear", True)), "question": str(data.get("question") or "").strip(),
           "notes": str(data.get("notes") or "").strip(), "defects": []}
    for d in data.get("defects") or []:
        if not isinstance(d, dict) or d.get("id") not in BY_ID:
            continue
        try:
            conf = float(d.get("confidence", 0.5))
        except (TypeError, ValueError):
            conf = 0.5
        out["defects"].append({"id": d["id"], "confidence": max(0.0, min(conf, 1.0)), "where": str(d.get("where") or "")[:120]})
    if not out["clear"] and not out["question"]:
        out["question"] = "По фото не понять, что не так. Сфотографируйте деталь крупнее при боковом свете или выберите дефект ниже."
    return out


# Чего в 5D нет: кайма и поддержки на столе, горизонтальное расширение первого слоя.
NOT_IN_5D = ("adhesion_type", "brim_width", "support_enable", "xy_offset_layer_0")


def fixes(ids, values, material=None, mode="classic"):
    """Исправления для выбранных дефектов от текущих значений: [{"key", "from", "to", "why", "defect"}].

    Если два дефекта тянут один параметр в разные стороны (например, температура: паутина — ниже,
    расслоение — выше), берём первый по списку выбранных и пишем об этом в причине.
    """
    values = values or {}
    prof = (material or {}).get("profile") or {}
    out, seen = [], {}
    for did in ids:
        d = BY_ID.get(did)
        if not d:
            continue
        for key, step, limit, why in d[4]:
            if mode not in (None, "classic") and key in NOT_IN_5D:
                continue
            if key in seen:
                if seen[key]["defect"] != d[1]:
                    seen[key]["why"] += "; для «%s» оставил так" % d[1].lower()
                continue
            cur = values.get(key)
            if isinstance(step, str) and step.startswith("="):
                raw = step[1:]
                new = {"true": True, "false": False}.get(raw, raw)
                try:
                    new = float(raw)
                except ValueError:
                    pass
                if cur == new:
                    continue
            else:
                if cur is None and key == "material_print_temperature":
                    cur = prof.get("print_temp")
                if not isinstance(cur, (int, float)) or isinstance(cur, bool):
                    continue
                new = cur + step
                if limit is not None:
                    new = min(new, limit) if step > 0 else max(new, limit)
                if key == "material_print_temperature" and prof.get("print_temp"):
                    new = max(prof["print_temp"] - 20, min(prof["print_temp"] + 20, new))  # в окне профиля
                if isinstance(cur, int):
                    new = int(round(new))
                else:
                    new = round(new, 3)
                if new == cur:
                    continue
            f = {"key": key, "from": cur, "to": new, "why": why, "defect": d[1]}
            seen[key] = f
            out.append(f)
    return out


# ------------------------------------------------------------------ ход разбора

def start(run, llm_client_factory, text, files):
    """Фото пришло — посмотреть его (если есть модель для фото) и показать карточку разбора."""
    from . import images as IMG
    st = run.st
    photos = [f for f in files if f.get("kind") == "image"]
    words = from_words(text)
    b = {"type": "review", "id": "rv" + uuid.uuid4().hex[:6], "photos": [f["name"] for f in photos],
         "catalog": catalog(), "selected": words, "defects": [], "question": "", "notes": "", "fixes": [],
         "state": "pick", "seen_by": ""}
    if not photos:
        run.text("Приложите фото напечатанной детали — крупно, при боковом свете, со стороны дефекта. "
                 "Или выберите, что не так, в карточке ниже.")
    if photos and llm_client_factory is not None:
        urls = []
        for f in photos[:3]:
            fi = run.store.file(f["id"])
            u = IMG.data_url(fi["path"], fi["name"]) if fi else None
            if u:
                urls.append(u)
        client, who = llm_client_factory()
        if client is not None and urls:
            s = run.step("review_photo", "Посмотрел фото детали", {"photos": len(urls)})
            try:
                m = M.by_id(st.get("material_id") or "") or {}
                ctx = (" из %s" % m["name"]) if m.get("name") else ""
                if text:
                    ctx += "; пользователь пишет: «%s»" % text[:300]
                cat = "\n".join("%s — %s — %s" % (d[0], d[1], d[2]) for d in DEFECTS)
                parts = [{"type": "text", "text": PROMPT.format(ctx=ctx, catalog=cat)}]
                parts += [{"type": "image_url", "image_url": {"url": u}} for u in urls]
                from .llm import text_of
                res = parse_reply(text_of(client.chat([{"role": "user", "content": parts}], max_tokens=600)))
            except Exception as e:  # модель не ответила — выбор вручную, без падения хода
                run.fail(s, "Модель для фото не ответила: %s" % e)
                res = None
            if res is not None:
                run.done(s, ", ".join(BY_ID[d["id"]][1] for d in res["defects"]) or ("непонятно" if not res["clear"] else "дефектов не видно"))
                b["seen_by"] = who
                b["notes"] = res["notes"]
                b["defects"] = res["defects"]
                if not res["clear"]:
                    b["question"] = res["question"]
                else:
                    sure = [d["id"] for d in res["defects"] if d["confidence"] >= 0.4]
                    b["selected"] = list(dict.fromkeys(words + sure))
            elif s.get("status") != "error":
                run.fail(s, "Ответ модели не разобрать — выберите дефект сами")
    if b["selected"] and not b["question"]:
        _fill(run, b)
    run.block(b)
    if b["question"]:
        run.text(b["question"])
    elif b["state"] == "fixes":
        run.text("Вижу: %s. Предлагаю исправления ниже — примените, и я перенарежу деталь." %
                 ", ".join(BY_ID[i][1].lower() for i in b["selected"]))
    elif photos and not b["defects"] and b["seen_by"]:
        run.text("Дефектов на фото не вижу. Если что-то не так — выберите в карточке, что именно.")
    st["review_id"] = b["id"]
    return b


def _fill(run, b):
    from . import workflow as W
    st = run.st
    m = M.by_id(st.get("material_id") or "") or {}
    vals = W.combo_values(st)
    # Параметры из каталога дефектов (поток, расширение первого слоя…) в паспорте не показаны —
    # их текущие значения берём прямо из слайсера, иначе исправление не от чего считать.
    keys = list(dict.fromkeys(fx[0] for did in b["selected"] if did in BY_ID for fx in BY_ID[did][4]))
    info = W.setting_infos(run, keys)
    for k, i in info.items():
        if vals.get(k) is None and i.get("value") is not None:
            vals[k] = i["value"]
    fx = fixes(b["selected"], vals, m, st.get("mode", "classic"))
    for f in fx:
        i = info.get(f["key"]) or {}
        f["label"] = i.get("label") or f["key"]
        f["fromText"] = W.show_value(f["from"], i)
        f["toText"] = W.show_value(f["to"], i)
    b["fixes"] = [f for f in fx if f["key"] in info]  # чего нет в этом слайсере — не предлагаем
    b["state"] = "fixes" if b["fixes"] else "nofix"


def find(part, bid):
    for msg in part.get("messages", []):
        for b in msg.get("blocks", []) or []:
            if b.get("type") == "review" and b.get("id") == bid:
                return b
    return None


def choose(run, bid, ids):
    """Пользователь отметил дефекты сам — пересчитать исправления в той же карточке."""
    b = find(run.part, bid)
    if not b or b.get("state") == "applied":
        raise ValueError("Разбор уже применён")
    b["selected"] = [i for i in ids if i in BY_ID]
    b["question"] = ""
    if not b["selected"]:
        b["fixes"], b["state"] = [], "pick"
    else:
        _fill(run, b)
    run.changed()
    return b


def apply(run, bid):
    """«Применить исправления»: в STE Slicer, с проверкой и перенарезкой — как правка в паспорте."""
    from . import workflow as W
    b = find(run.part, bid)
    if not b or not b.get("fixes"):
        raise ValueError("Нечего применять")
    if b.get("state") == "applied":
        raise ValueError("Исправления уже применены")
    names = ", ".join(BY_ID[i][1].lower() for i in b["selected"] if i in BY_ID)
    values = dict((f["key"], f["to"]) for f in b["fixes"])
    diff = W.set_user_settings(run, values, why="Исправление по фото: %s." % names, title="Исправил по фото: %s" % names)
    b["state"] = "applied"
    rec = run.st.get("catalog_record")
    if rec and hasattr(run.app, "catalog"):
        try:
            applied = set(d[0] for d in diff)  # что STE Slicer принял (подписи), остальное — не опыт
            old = (run.app.catalog.get(rec) or {}).get("lessons") or []
            new = [{"key": f["key"], "from": f["from"], "to": f["to"], "defect": f["defect"], "label": f.get("label") or f["key"]}
                   for f in b["fixes"] if (f.get("label") or f["key"]) in applied]
            keys = set(x["key"] for x in new)
            run.app.catalog.update(rec, {"state": "issue", "note": "По фото: %s. Исправлено: %s." % (
                names, ", ".join("%s %s → %s" % (d[0].lower(), d[1], d[2]) for d in diff)),
                "lessons": new + [x for x in old if x["key"] not in keys]})
        except Exception:
            pass
    return diff


INTENT = ("разбер", "напечатал", "получилось", "дефект", "брак", "что не так", "после печати", "исправ")


def is_review(part, text, files):
    """Фото без новой модели к уже нарезанной детали + слова «напечатал», «дефект»… — это разбор печати."""
    st = part.get("state") or {}
    low = (text or "").lower()
    if not files and re.search(r"разбер\w* (мою )?печат|разобрать печат|печать по фото", low):
        return bool(st.get("node"))  # «Разобрать печать по фото» без фото — карточка с выбором дефекта
    if any(f.get("kind") == "model" for f in files) or not any(f.get("kind") == "image" for f in files):
        return False
    if not (st.get("totals") or part.get("status") in ("sliced", "saved")):
        return False
    return not low.strip() or any(w in low for w in INTENT) or bool(from_words(low))
