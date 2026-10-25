# -*- coding: utf-8 -*-
"""Картотека деталей и шаблон имени файла с параметрами печати.

Имя по шаблону «{деталь}_{режим}_{материал}_L{слой}_I{заполнение}» даёт, например,
koleso_5D_TPU-D70_L0.10_I90.3mf. Из такого имени параметры читаются обратно, поэтому
картотека узнаёт файл, даже если его переложили, и умеет импортировать старые папки.
"""
import json
import os
import re
import threading
import time
import uuid

FIELDS = ["деталь", "режим", "материал", "слой", "заполнение", "стенки", "профиль", "принтер", "копии", "дата"]
DEFAULT_TEMPLATE = "{деталь}_{режим}_{материал}_L{слой}_I{заполнение}"

TRANSLIT = dict(zip("абвгдеёжзийклмнопрстуфхцчшщъыьэюя",
                    ["a", "b", "v", "g", "d", "e", "e", "zh", "z", "i", "y", "k", "l", "m", "n", "o", "p", "r",
                     "s", "t", "u", "f", "h", "ts", "ch", "sh", "sch", "", "y", "", "e", "yu", "ya"]))


def slug(text, limit=24):
    text = text.lower()
    out = "".join(TRANSLIT.get(ch, ch) for ch in text)
    out = re.sub(r"[^a-z0-9]+", "-", out).strip("-")
    return out[:limit].strip("-") or "detal"


def material_code(name):
    """«EnduSe TPU D70» -> «TPU-D70», «FiberPart ABS/PA G8» -> «ABS-PA-G8»."""
    parts = name.split(" ", 1)
    code = parts[1] if len(parts) > 1 and parts[0] in ("EnduSe", "FiberPart", "Sealant", "Proto", "MetalCast", "Generic") else name
    return re.sub(r"[^A-Za-z0-9]+", "-", code).strip("-")


def check_template(tpl):
    names = re.findall(r"\{([^}]*)\}", tpl)
    for n in names:
        if n not in FIELDS:
            return "Поле {%s} не найдено. Доступны поля: %s" % (n, ", ".join(FIELDS))
    if not names:
        return "В шаблоне нет ни одного поля"
    if re.search(r'[\\/:*?"<>|]', re.sub(r"\{[^}]*\}", "", tpl)):
        return "В шаблоне есть символы, недопустимые в имени файла"
    return None


def make_name(tpl, values):
    def sub(m):
        v = values.get(m.group(1), "")
        return str(v)
    name = re.sub(r"\{([^}]*)\}", sub, tpl)
    name = re.sub(r'[\\/:*?"<>|\s]+', "-", name)
    return name.strip("-_") or "detal"


def values_for(part_name, mode_short, material_name, layer, infill, walls=None, profile="", printer="", copies=1):
    return {
        "деталь": slug(part_name), "режим": mode_short or "3D", "материал": material_code(material_name or ""),
        "слой": "%.2f" % float(layer or 0), "заполнение": "%d" % int(round(float(infill or 0))),
        "стенки": "%s" % (walls or ""), "профиль": slug(profile or "", 12), "принтер": slug(printer or "", 16),
        "копии": "x%d" % copies if copies and copies > 1 else "", "дата": time.strftime("%Y%m%d"),
    }


NAME_RX = re.compile(r"^(?P<name>.+?)_(?P<mode>3D|5D)_(?P<mat>[A-Za-z0-9-]+?)_L(?P<layer>\d+(?:\.\d+)?)_I(?P<infill>\d+)", re.I)


def parse_name(filename):
    """Параметры из имени по шаблону по умолчанию или None."""
    base = os.path.splitext(os.path.basename(filename))[0]
    m = NAME_RX.match(base)
    if not m:
        return None
    return {"name": m.group("name").replace("-", " "), "mode": m.group("mode").upper(), "material_code": m.group("mat"),
            "layer": float(m.group("layer")), "infill": int(m.group("infill"))}


class Catalog(object):
    def __init__(self, data_dir):
        self.path = os.path.join(data_dir, "catalog.json")
        self.lock = threading.RLock()
        self.records = []
        if os.path.isfile(self.path):
            try:
                with open(self.path, "r", encoding="utf-8") as f:
                    self.records = json.load(f)
            except (OSError, ValueError):
                self.records = []

    def _save(self):
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self.records, f, ensure_ascii=False, indent=1)
        os.replace(tmp, self.path)

    def all(self):
        return list(self.records)

    def get(self, rid):
        for r in self.records:
            if r["id"] == rid:
                return r
        return None

    def add(self, rec):
        with self.lock:
            rec = dict(rec)
            rec.setdefault("id", "r" + uuid.uuid4().hex[:8])
            rec.setdefault("date", time.strftime("%d.%m"))
            rec.setdefault("state", "new")
            rec.setdefault("fav", False)
            # Повторное сохранение той же детали обновляет запись.
            for i, r in enumerate(self.records):
                if r.get("file") == rec.get("file") or (rec.get("part_id") and r.get("part_id") == rec.get("part_id")):
                    rec["id"] = r["id"]
                    rec["fav"] = r.get("fav", False)
                    rec["state"] = r.get("state", "new")
                    rec["note"] = r.get("note", "")
                    if r.get("lessons"):
                        rec["lessons"] = r["lessons"]
                    self.records[i] = rec
                    self._save()
                    return rec
            self.records.insert(0, rec)
            self._save()
            return rec

    def update(self, rid, patch):
        with self.lock:
            r = self.get(rid)
            if not r:
                raise KeyError(rid)
            if "state" in patch:
                if patch["state"] not in ("printed", "issue", "new"):
                    raise ValueError("Неизвестное состояние записи")
                r["state"] = patch["state"]
            if "note" in patch:
                r["note"] = str(patch["note"] or "")[:2000]
            if "lessons" in patch:  # исправления из разбора по фото — опыт для следующих деталей
                r["lessons"] = [dict((k, x.get(k)) for k in ("key", "from", "to", "defect", "label"))
                                for x in (patch["lessons"] or []) if isinstance(x, dict) and x.get("key")][:30]
            if "fav" in patch:
                r["fav"] = bool(patch["fav"])
            if "name" in patch and str(patch["name"] or "").strip():
                r["name"] = str(patch["name"]).strip()[:80]
            self._save()
            return r

    def search(self, query="", limit=10):
        q = (query or "").lower().strip()
        if not q:
            return self.records[:limit]
        words = q.split()
        out = []
        for r in self.records:
            hay = " ".join(str(r.get(k, "")) for k in ("name", "material", "mode", "file", "note")).lower()
            if all(w in hay for w in words):
                out.append(r)
        return out[:limit]

    def import_folder(self, folder, materials_by_code, max_files=3000):
        added, from_name, skipped = 0, 0, []
        seen = 0
        for root, _dirs, files in os.walk(folder):
            seen += len(files)
            if seen > max_files:
                skipped.append([root, "проверены первые %d файлов — выберите папку поменьше" % max_files])
                break
            for fn in sorted(files):
                low = fn.lower()
                if not low.endswith((".3mf", ".stl", ".gcode", ".obj")):
                    continue
                path = os.path.join(root, fn)
                if low.endswith(".gcode") and not any(os.path.splitext(fn)[0] == os.path.splitext(x)[0] and x.lower().endswith((".3mf", ".stl")) for x in files):
                    skipped.append([fn, "G-code без модели и проекта"])
                    continue
                if low.endswith(".gcode"):
                    continue
                if any(r.get("path") == path for r in self.records):
                    skipped.append([fn, "уже есть в картотеке"])
                    continue
                parsed = parse_name(fn)
                rec = {"name": os.path.splitext(fn)[0], "file": fn, "path": path, "mode": "3D", "material": "",
                       "profile": "", "infill": "", "walls": "", "time": "—", "mass": "—", "cost": "—",
                       "state": "new", "kind": "new", "imported": True}
                if parsed:
                    from_name += 1
                    rec.update({"name": parsed["name"].capitalize(), "mode": parsed["mode"],
                                "material": materials_by_code.get(parsed["material_code"].upper(), parsed["material_code"]),
                                "profile": "слой %s" % ("%g" % parsed["layer"]).replace(".", ","),
                                "infill": "%d %%" % parsed["infill"]})
                self.add(rec)
                added += 1
        return {"added": added, "fromName": from_name, "skipped": len(skipped), "skippedFiles": skipped}
