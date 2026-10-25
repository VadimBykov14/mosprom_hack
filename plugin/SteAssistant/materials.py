# -*- coding: utf-8 -*-
"""Материалы Stereotech: профили STE Slicer + файл данных data/materials.json.

Из профилей материалов STE Slicer (MaterialManager / ContainerRegistry, через мост
material_profiles) берутся: доступен ли материал текущему принтеру и соплу, название,
плотность. Свойств, которых в профилях нет (стойкость к маслу, износ, «не оставляет
следов» и т. п.), — в data/materials.json, а не в коде. Флаг там — [значение, источник]:
"profile" — прямо сказано в описании профиля материала, "handbook" — справочное свойство
базового полимера. Профиль важнее справочника.

id материала совпадает с root_material_id профиля STE Slicer, поэтому его можно сразу
передавать в MachineManager.setMaterialById.
"""
import io
import json
import os

DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "materials.json")


def _load():
    with io.open(DATA, encoding="utf-8") as f:
        data = json.load(f)
    for m in data["materials"]:
        m["flags"] = dict((k, tuple(v)) for k, v in m.get("flags", {}).items())
    return data


_DATA = _load()
MATERIALS = _DATA["materials"]                  # выбираются для детали
SUPPORT_MATERIALS = _DATA["support_materials"]  # только поддержки
SPECIAL_MATERIALS = _DATA["special_materials"]  # металл, керамика, непрерывное волокно
PREPARATION = _DATA["preparation"]              # подготовка к печати по базовому полимеру

SYNCED = {"done": False, "profiles": 0}


def sync(profiles):
    """Свести файл данных с профилями STE Slicer. profiles — ответ моста material_profiles().

    Материал без профиля для этого принтера помечается недоступным и в подбор не идёт.
    Пустой список (слайсер не ответил) ничего не ограничивает.
    """
    by = dict((p["id"], p) for p in profiles or [] if p.get("id"))
    for m in MATERIALS + SUPPORT_MATERIALS + SPECIAL_MATERIALS:
        p = by.get(m["id"])
        if not by:
            m.pop("available", None)
            continue
        m["available"] = p is not None
        if p:
            if p.get("density"):
                m["density"] = p["density"]
                m["density_src"] = "profile"
            if p.get("name"):
                m["profile_name"] = p["name"]
            if p.get("adhesion_info"):
                m["adhesion_info"] = p["adhesion_info"]
    SYNCED.update(done=True, profiles=len(by))
    return [m["id"] for m in MATERIALS if m.get("available") is False]


def candidates():
    """Материалы для подбора: всё из файла данных, у чего есть профиль на текущем принтере."""
    return [m for m in MATERIALS if m.get("available", True)]


def by_id(material_id):
    for m in MATERIALS + SUPPORT_MATERIALS + SPECIAL_MATERIALS:
        if m["id"] == material_id:
            return m
    return None


def flag(material, tag):
    """(значение, источник) или (None, None), если данных нет."""
    return material.get("flags", {}).get(tag, (None, None))


def preparation(material):
    out = list(PREPARATION.get(material.get("base"), []))
    if material.get("adhesion_info"):  # инструкция из профиля STE Slicer — первой
        out.insert(0, material["adhesion_info"])
    return out
