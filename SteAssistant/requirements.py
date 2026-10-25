# -*- coding: utf-8 -*-
"""Требования к детали: словарь свойств и извлечение требований из текста ТЗ.

Требование — словарь:
  {"kind": "must" | "ban" | "want", "tag": <ключ свойства>, "value": <число или None>,
   "text": "как звучит для человека", "quote": "цитата из ТЗ", "clause": "п. 2.4",
   "category": "Нагрузка" | "Среда" | "Точность" | "Прочее", "source": "tz" | "words" | "llm"}

Ключи свойств (TAGS) — общий язык между LLM, правилами и базой материалов.
LLM получает их список в описании инструмента set_requirements. Без LLM требования
находит извлекатель по ключевым словам ниже — он же страхует LLM от пропусков.
"""
import re

# tag -> (подпись, категория, как проверять: "flag" | "min:<поле>" | "max:<поле>")
TAGS = [
    ("friction_high", "Высокое трение, сцепление", "Нагрузка", "flag"),
    ("low_friction", "Скольжение без смазки", "Нагрузка", "flag"),
    ("wear_resistant", "Износостойкость", "Нагрузка", "flag"),
    ("impact_resistant", "Ударопрочность, вязкость", "Нагрузка", "flag"),
    ("fatigue_resistant", "Циклические нагрузки", "Нагрузка", "flag"),
    ("creep_resistant", "Без ползучести под нагрузкой", "Нагрузка", "flag"),
    ("rigid", "Жёсткость, высокий модуль", "Нагрузка", "flag"),
    ("flexible", "Гибкость, эластичность", "Нагрузка", "flag"),
    ("no_shatter", "Не колется на осколки", "Нагрузка", "flag"),
    ("oil_resistant", "Масла, смазки, СОЖ", "Среда", "flag"),
    ("fuel_resistant", "Бензин", "Среда", "flag"),
    ("diesel_resistant", "Дизельное топливо", "Среда", "flag"),
    ("alkali_resistant", "Щёлочи", "Среда", "flag"),
    ("acid_resistant", "Кислоты", "Среда", "flag"),
    ("alcohol_resistant", "Спирты, IPA", "Среда", "flag"),
    ("hydrolysis_resistant", "Горячая вода, гидролиз", "Среда", "flag"),
    ("uv_resistant", "Ультрафиолет", "Среда", "flag"),
    ("ozone_resistant", "Озон", "Среда", "flag"),
    ("hydrophobic", "Не впитывает влагу", "Среда", "flag"),
    ("food_safe", "Контакт с пищей", "Среда", "flag"),
    ("non_marking", "Без следов на продукции", "Чистота", "flag"),
    ("low_dust", "Без пыли и абразивного износа", "Чистота", "flag"),
    ("low_voc", "Без летучих веществ", "Чистота", "flag"),
    ("no_abrasive_filler", "Не стачивает ответные детали", "Чистота", "flag"),
    ("no_conductive_filler", "Без металлов и проводящих наполнителей", "Чистота", "flag"),
    ("antistatic", "Антистатика", "Прочее", "flag"),
    ("dimensional_stability", "Точность размеров", "Точность", "flag"),
    ("fine_detail", "Мелкие детали, гладкая поверхность", "Точность", "flag"),
    ("temp_service_min", "Рабочая температура", "Среда", "min:service_temp"),
    ("temp_peak_min", "Кратковременный нагрев", "Среда", "min:peak_temp"),
    ("tg_min", "Температура стеклования", "Среда", "min:tg"),
    ("vicat_min", "Температура размягчения по Вика", "Среда", "min:vicat"),
    ("density_max", "Плотность не больше", "Прочее", "max:density"),
    ("water_absorption_max", "Водопоглощение не больше", "Среда", "max:water_absorption"),
    ("modulus_min", "Модуль упругости не меньше", "Нагрузка", "min:modulus"),
    ("torque_load", "Крутящий момент", "Нагрузка", "none"),
]
TAG_INFO = dict((t[0], {"label": t[1], "category": t[2], "check": t[3]}) for t in TAGS)
TAG_KEYS = [t[0] for t in TAGS]

# Свойства, которые по смыслу — запреты («не должна оставлять следов»).
BAN_TAGS = ("non_marking", "low_dust", "low_voc", "no_abrasive_filler", "no_conductive_filler", "no_shatter")


def _num(s):
    return float(s.replace(",", "."))


# Правила извлечения: (регулярное выражение, tag, вид, функция значения или None, подпись).
# Ищем по абзацам, поэтому выражения короткие и терпят разные формы слов.
RULES = [
    (r"сцеплени|проскальз|высок\w* (?:коэффициент\w* )?трени|трени\w* (?:о|по|с) ", "friction_high", "must", None, None),
    (r"скольз|без смазки|в паре с роликами|ответн\w+ (pom|роликов|полимерн)", "low_friction", "must", None, None),
    (r"износ|истира|абразивн", "wear_resistant", "must", None, None),
    (r"удар|вязкост|не отламывал|выкрашива", "impact_resistant", "must", None, None),
    (r"циклич|усталост|24/7|непрерывн|изгиб|старт-стоп|деформаци", "fatigue_resistant", "must", None, None),
    (r"ползуч", "creep_resistant", "must", None, None),
    (r"модул|жёстк|жестк|эвольвент|размягчаться при \+?\d", "rigid", "must", None, None),
    (r"эластичн|гибк|растяжение|изгиб до|поворот\w* колеса", "flexible", "must", None, None),
    (r"осколк|колоть|колется|хрупк", "no_shatter", "must", None, None),
    (r"масл|сож|смазк", "oil_resistant", "must", None, None),
    (r"бензин", "fuel_resistant", "must", None, None),
    (r"дизел", "diesel_resistant", "must", None, None),
    (r"щелоч|щёлоч|ph\s*\d", "alkali_resistant", "must", None, None),
    (r"кислот", "acid_resistant", "must", None, None),
    (r"спирт|ipa|изопропил", "alcohol_resistant", "must", None, None),
    (r"гидролиз|гликол|тосол|теплоносител", "hydrolysis_resistant", "must", None, None),
    (r"ультрафиолет|уф[- ]|\bуф\b|солнечн", "uv_resistant", "must", None, None),
    (r"озон", "ozone_resistant", "must", None, None),
    (r"гидрофоб|влагопоглощ|не впитыва|водопоглощ|влажност\w*:? *(8|9)\d", "hydrophobic", "must", None, None),
    (r"пищев|токсичн", "food_safe", "want", None, None),
    (r"след\w* на|осесть на бумаге|черн\w+ след|марк", "non_marking", "ban", None, None),
    (r"микропыл|пыль\b.*недопуст|не должна выделять микропыль", "low_dust", "ban", None, None),
    (r"летуч|voc", "low_voc", "ban", None, None),
    (r"ускоренн\w* износ ответн|стачива|не должна вызывать ускоренный износ", "no_abrasive_filler", "ban", None, None),
    (r"гальванич|проводящ|металлическ\w+ наполнител|без металл", "no_conductive_filler", "ban", None, None),
    (r"статическ|антистат|esd", "antistatic", "want", None, None),
    (r"допуск|точност|±\s*0[.,]\d|разностенност", "dimensional_stability", "must", None, None),
    (r"видимых слоёв|видимых слоев|следов от поддержек|декоратив", "fine_detail", "must", None, None),
    (r"крутящ\w* момент|н\s*·\s*м", "torque_load", "must", None, None),
    (r"(?:плотност|тяжел)\w*[^.\d]{0,40}?(?:≤|<=|не более|не выше|до|тяжелее)?\s*(\d+[.,]\d+)\s*г", "density_max", "must", "g1", None),
    (r"вика.*?(?:≥|>=|не ниже|не менее)\s*\+?(\d{2,3})", "vicat_min", "must", "g1", None),
    (r"водопоглощ\w*[^.\d]{0,30}?(?:≤|<=|не более|до)?\s*(\d+[.,]\d+)\s*%", "water_absorption_max", "must", "g1", None),
    (r"модул\w* (?:упругост\w*|юнга)?[^.\d]{0,30}?(?:≥|>=|не менее|от)?\s*(\d{3,5})\s*мпа", "modulus_min", "must", "g1", None),
    (r"(?:tg|стеклован)\w*[^.\d]{0,30}?(?:≥|>=|не ниже|не менее)?\s*(\d{2,3})", "tg_min", "must", "g1", None),
]
RULES = [(re.compile(r[0], re.I), r[1], r[2], r[3], r[4]) for r in RULES]

_TEMP = re.compile(r"\+?(\d{2,3})\s*°\s*c", re.I)
_CLAUSE = re.compile(r"^\s*(\d+(?:\.\d+)*)[.)]?\s")
_WANT = re.compile(r"желательн|по возможности|допускается", re.I)


def _sentences(paragraph):
    parts = re.split(r"(?<=[.;!?])\s+", paragraph)
    return [p.strip() for p in parts if p.strip()]


def extract(text):
    """Находит требования в тексте ТЗ по ключевым словам. Возвращает список требований без повторов тегов."""
    found = {}
    clause = ""
    max_service_temp = None
    temp_quote = None
    for para in text.splitlines():
        if not para.strip():
            continue
        m = _CLAUSE.match(para)
        if m:
            clause = "п. " + m.group(1)
        low = para.lower()
        # Рабочая температура: максимум из строк про температуру среды и режим.
        peak = re.search(r"капан|капл|клей|кратковрем|расплав", low)
        if (re.search(r"температур|°", low) and not re.search(r"печат|сопл|стол|вика|стеклован|\btg\b", low)
                and not peak and re.search(r"температур|размягч|нагрев|режим", low)):
            temps = [int(t) for t in _TEMP.findall(para)]
            if temps:
                t = max(temps)
                if max_service_temp is None or t > max_service_temp:
                    max_service_temp, temp_quote = t, para.strip()
        if peak and "temp_peak_min" not in found:
            temps = [int(t) for t in _TEMP.findall(para)]
            if temps:
                found["temp_peak_min"] = make("temp_peak_min", "must", value=max(temps), quote=para.strip()[:220],
                                              clause=clause, source="tz")
        for sent in _sentences(para):
            sl = sent.lower()
            for rx, tag, kind, val, _ in RULES:
                mm = rx.search(sl)
                if not mm or tag in found:
                    continue
                value = None
                if val == "g1":
                    try:
                        value = _num(mm.group(1))
                    except (ValueError, IndexError):
                        continue
                k = kind
                if k == "must" and _WANT.search(sl):
                    k = "want"
                found[tag] = make(tag, k, value=value, quote=sent[:220], clause=clause, source="tz")
    if max_service_temp is not None and max_service_temp >= 40:
        found["temp_service_min"] = make("temp_service_min", "must", value=max_service_temp,
                                         quote=temp_quote[:220], clause="", source="tz")
    wear = re.search(r"(?:износ|выработк)[^.]{0,80}?(\d+[.,]?\d*)\s*[–-]\s*(\d+[.,]?\d*)\s*мм", text, re.I)
    if wear and "wear_resistant" in found:
        found["wear_resistant"]["value"] = _num(wear.group(2))
        found["wear_resistant"]["text"] = "Износостойкость, запас под износ %s мм" % wear.group(2)
    # Гибкость и жёсткость вместе не бывают: побеждает то, что встретилось в тексте сильнее.
    if "flexible" in found and "rigid" in found:
        if re.search(r"жестк\w+ материал\w* не допуска|применение жестких", text.lower()):
            del found["rigid"]
        else:
            del found["flexible"]
    if "low_friction" in found and "friction_high" in found:
        del found["friction_high"]
    return [found[k] for k in TAG_KEYS if k in found]


def make(tag, kind, value=None, quote="", clause="", source="tz", text=None):
    info = TAG_INFO.get(tag, {"label": tag, "category": "Прочее"})
    if text is None:
        text = info["label"]
        if value is not None:
            unit = {"temp_service_min": " °C", "temp_peak_min": " °C", "tg_min": " °C", "vicat_min": " °C",
                    "density_max": " г/см³", "water_absorption_max": " %", "modulus_min": " МПа"}.get(tag, "")
            sign = "≤ " if tag.endswith("_max") else "≥ "
            text = "%s %s%s%s" % (info["label"], sign, ("%g" % value).replace(".", ","), unit)
    if tag in BAN_TAGS and kind == "must":
        kind = "ban"
    return {"kind": kind, "tag": tag, "value": value, "text": text, "quote": quote,
            "clause": clause, "category": info["category"], "source": source}


def merge(primary, extra):
    """Объединяет требования: из primary (обычно LLM) берём всё, из extra — теги, которых нет."""
    have = set(r["tag"] for r in primary)
    out = list(primary)
    for r in extra:
        if r["tag"] not in have:
            out.append(r)
            have.add(r["tag"])
    return out


def normalize(items, source="llm"):
    """Приводит требования от LLM к нашему виду, отбрасывает неизвестные теги."""
    out = []
    if not isinstance(items, (list, tuple)):
        return out
    for it in items:
        if not isinstance(it, dict):  # LLM иногда присылает строки вместо объектов
            continue
        tag = it.get("tag")
        if tag not in TAG_INFO:
            continue
        kind = it.get("kind", "must")
        if kind not in ("must", "ban", "want"):
            kind = "must"
        value = it.get("value")
        try:
            value = float(value) if value not in (None, "") else None
        except (TypeError, ValueError):
            value = None
        out.append(make(tag, kind, value=value, quote=str(it.get("quote") or "")[:300],
                        clause=str(it.get("clause") or ""), source=source, text=str(it["text"]) if it.get("text") else None))
    return out


def group(reqs):
    """Группы для карточки «Требования из ТЗ»."""
    order = ["Нагрузка", "Среда", "Чистота", "Точность", "Прочее"]
    groups = []
    for cat in order:
        items = [r for r in reqs if r.get("category") == cat]
        if items:
            groups.append({"name": cat, "items": items})
    return groups


def counts(reqs):
    c = {"must": 0, "ban": 0, "want": 0}
    for r in reqs:
        c[r["kind"]] = c.get(r["kind"], 0) + 1
    return c
