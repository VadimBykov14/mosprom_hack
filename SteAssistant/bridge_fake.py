# -*- coding: utf-8 -*-
"""Поддельный слайсер: тот же интерфейс, что у SlicerBridge, без STE Slicer.

Нужен для разработки чата, тестов и демонстрации на машине без слайсера.
Модели читает настоящим разбором STL, «нарезку» оценивает по объёму модели.
Интерфейс моста (все методы вызываются из рабочего потока агента):

  printer() -> {"name", "definition", "volume": [x, y, z], "shape"}
  load_model(path) -> node_id
  models() -> [{"id", "name", "size_mm"}]
  model_info(node_id) -> {"size_mm", "outside"}   габарит и «вне рабочей зоны» от слайсера
  mesh(node_id) -> numpy (n, 3, 3) в мировых координатах сцены (Y вверх)
  lay_flat(node_id)                    «Положить плашмя» (LayFlatOperation)
  rotate(node_id, axis "X"|"Y"|"Z", degrees) ; rotate_vec(node_id, (x, y, z), degrees)
  place(node_id, center: bool)        опустить на стол; center — в центр (для 5D)
  arrange() ; multiply(node_id, count) ; remove_copies(node_id) ; clear()
  get_settings(keys) -> {key: value}
  set_settings({key: value}) -> [(key, old, new)]
  reset_settings(keys) -> [(key, old, new)]    убрать значения плагина, вернуть к профилю
  setting_info(keys) -> {key: {"label", "description", "unit", "type", "min", "max", "options", "enabled", "value", "changed"}}
  list_settings(query, limit) -> {"items": [{"key", "category", ...setting_info}], "total"}   все параметры с поиском
  validate(keys) -> {key: "ok" | "MinimumError" | "MaximumError" | ...}
  model_warnings() -> [текст]          предупреждения ModelChecker
  material_profiles() -> [{"id", "name", "brand", "type", "density", "diameter", "description", "adhesion_info"}]
  material() -> root_material_id ; set_material(root_material_id)
  set_quality(quality_type)
  slice(timeout) -> {"ok", "error", "time_s", "features": {name: s}, "grams", "meters", "warnings": []}
  save_project(path) ; save_gcode(path)
"""
import copy
import math

import numpy as np

from . import geometry

DEFAULTS = {
    "printing_mode": "classic", "cylindrical_mode_base_diameter": 6.0, "cylindrical_raft_enabled": True,
    "support_enable": False, "support_type": "everywhere", "support_enable_cylindrical": False,
    "infill_sparse_density": 20.0, "wall_line_count": 2, "layer_height": 0.1, "cylindrical_layer_height": 0.1,
    "adhesion_type": "brim", "brim_width": 8.0, "skirt_line_count": 1, "support_angle": 50.0,
    "material_print_temperature": 210.0, "top_bottom_thickness": 0.8, "infill_pattern": "grid",
    "support_interface_enable": False, "z_seam_type": "sharpest_corner", "xy_offset_layer_0": 0.0,
    "speed_print": 50.0, "speed_wall_0": 25.0, "speed_wall_x": 50.0, "speed_infill": 50.0, "speed_topbottom": 25.0,
    "speed_layer_0": 20.0, "speed_travel": 150.0, "material_bed_temperature": 60.0,
    "material_print_temperature_layer_0": 210.0, "cool_fan_enabled": True, "cool_fan_speed": 100.0,
    "retraction_enable": True, "retraction_amount": 3.0, "retraction_speed": 35.0, "retraction_hop_enabled": False,
    "infill_line_distance": 6.0, "top_layers": 4, "bottom_layers": 4, "ironing_enabled": False,
    "support_infill_rate": 15.0, "support_z_distance": 0.2, "raft_margin": 15.0, "magic_spiralize": False,
    "wall_thickness": 0.8, "infill_overlap": 30.0, "cool_min_layer_time": 10.0, "material_flow": 100.0,
}

# Подписи и пределы параметров — как в определениях STE Slicer (fdmprinter.def.json, ru_RU).
SETTING_INFO = {
    "printing_mode": ("Режим печати", "Классическая 3D-печать или 5D со слоями вокруг оси.", "", "enum", None, None),
    "cylindrical_mode_base_diameter": ("Диаметр базы", "Диаметр цилиндра в центре детали: внутри него — обычные слои, снаружи — слои вокруг оси.", "mm", "float", 3, 60),
    "support_enable": ("Поддержки", "Печатать поддержки под нависающими частями.", "", "bool", None, None),
    "support_enable_cylindrical": ("Поддержки в 5D", "Поддержки при печати в 5D.", "", "bool", None, None),
    "support_angle": ("Угол нависания поддержек", "Минимальный угол нависания, при котором печатаются поддержки.", "°", "float", 0, 90),
    "infill_sparse_density": ("Плотность заполнения", "Сколько процентов объёма занимает заполнение.", "%", "float", 0, 100),
    "wall_line_count": ("Количество линий стенки", "Число периметров. Больше — прочнее стенка.", "", "int", 0, 20),
    "layer_height": ("Высота слоя", "Толщина каждого слоя в миллиметрах.", "mm", "float", 0.04, 0.4),
    "cylindrical_layer_height": ("Высота слоя 5D", "Толщина слоя при печати в 5D.", "mm", "float", 0.04, 0.4),
    "adhesion_type": ("Тип прилипания", "Кайма, юбка или подложка для лучшего прилипания к столу.", "", "enum", None, None),
    "brim_width": ("Ширина каймы", "Расстояние от модели до внешней линии каймы.", "mm", "float", 0, 50),
    "material_print_temperature": ("Температура печати", "Температура сопла при печати.", "°C", "float", 150, 300),
    "top_bottom_thickness": ("Толщина верха/низа", "Толщина сплошных слоёв сверху и снизу детали.", "mm", "float", 0, 300),
    "infill_pattern": ("Шаблон заполнения", "Рисунок, которым печатается заполнение.", "", "enum", None, None),
    "support_interface_enable": ("Связующий слой поддержек", "Плотный слой между поддержкой и деталью.", "", "bool", None, None),
    "z_seam_type": ("Выравнивание шва по Z", "Где начинается каждый слой — там остаётся шов.", "", "enum", None, None),
    "xy_offset_layer_0": ("Горизонтальное расширение первого слоя", "Сдвиг контура первого слоя; минус компенсирует «слоновью ногу».", "mm", "float", -2, 2),
    "cylindrical_raft_enabled": ("Цилиндрическая подложка", "Цилиндрическая подложка вокруг базы в 5D.", "", "bool", None, None),
    "speed_print": ("Скорость печати", "Скорость, с которой происходит печать.", "mm/s", "float", 1, 150),
    "speed_wall_0": ("Скорость внешней стенки", "Медленнее — ровнее видимая поверхность.", "mm/s", "float", 1, 150),
    "speed_wall_x": ("Скорость внутренних стенок", "Скорость печати внутренних периметров.", "mm/s", "float", 1, 150),
    "speed_infill": ("Скорость заполнения", "Скорость печати заполнения.", "mm/s", "float", 1, 150),
    "speed_topbottom": ("Скорость верха/низа", "Скорость печати сплошных слоёв сверху и снизу.", "mm/s", "float", 1, 150),
    "speed_layer_0": ("Скорость первого слоя", "Медленнее — лучше прилипает к столу.", "mm/s", "float", 1, 100),
    "speed_travel": ("Скорость перемещения", "Скорость холостых перемещений сопла.", "mm/s", "float", 10, 300),
    "material_bed_temperature": ("Температура стола", "Температура нагреваемого стола.", "°C", "float", 0, 130),
    "material_print_temperature_layer_0": ("Температура печати первого слоя", "Температура сопла на первом слое.", "°C", "float", 150, 300),
    "material_flow": ("Поток", "Поправка количества выдавливаемого материала.", "%", "float", 50, 150),
    "cool_fan_enabled": ("Включить обдув", "Вентилятор обдува детали во время печати.", "", "bool", None, None),
    "cool_fan_speed": ("Скорость вентилятора", "Скорость вращения вентилятора обдува.", "%", "float", 0, 100),
    "cool_min_layer_time": ("Минимальное время слоя", "Слой печатается не быстрее этого — успевает остыть.", "s", "float", 0, 60),
    "retraction_enable": ("Разрешить откат", "Втягивать нить при перемещении над пустотой.", "", "bool", None, None),
    "retraction_amount": ("Длина отката", "Насколько втягивается нить при откате.", "mm", "float", 0, 10),
    "retraction_speed": ("Скорость отката", "Скорость, с которой нить втягивается при откате.", "mm/s", "float", 1, 100),
    "retraction_hop_enabled": ("Подъём оси Z при откате", "Сопло приподнимается над деталью при перемещении.", "", "bool", None, None),
    "infill_line_distance": ("Расстояние между линиями заполнения", "Считается из плотности заполнения.", "mm", "float", 0, 50),
    "infill_overlap": ("Перекрытие заполнения", "Насколько заполнение заходит на стенки.", "%", "float", 0, 100),
    "top_layers": ("Слои крышки", "Число сплошных слоёв сверху.", "", "int", 0, 50),
    "bottom_layers": ("Слои дна", "Число сплошных слоёв снизу.", "", "int", 0, 50),
    "wall_thickness": ("Толщина стенки", "Толщина горизонтальных стенок.", "mm", "float", 0, 10),
    "ironing_enabled": ("Разглаживание", "Ещё один проход по верхнему слою — гладкая крышка.", "", "bool", None, None),
    "support_infill_rate": ("Плотность поддержек", "Плотность заполнения поддержек.", "%", "float", 0, 100),
    "support_z_distance": ("Зазор поддержки по Z", "Расстояние от поддержки до детали сверху и снизу.", "mm", "float", 0, 2),
    "raft_margin": ("Дополнительное поле подложки", "Насколько подложка шире детали.", "mm", "float", 0, 30),
    "magic_spiralize": ("Спиральная печать", "Печать одной непрерывной спиралью — для полых ваз.", "", "bool", None, None),
}

# Раздел панели настроек STE Slicer для каждого параметра (как в fdmprinter.def.json).
CATEGORIES = [("Качество", ("layer_height", "cylindrical_layer_height")),
              ("Стенки", ("wall_line_count", "wall_thickness", "top_bottom_thickness", "top_layers", "bottom_layers",
                          "z_seam_type", "ironing_enabled")),
              ("Заполнение", ("infill_sparse_density", "infill_pattern", "infill_line_distance", "infill_overlap")),
              ("Материал", ("material_print_temperature", "material_print_temperature_layer_0",
                            "material_bed_temperature", "material_flow", "retraction_enable", "retraction_amount",
                            "retraction_speed")),
              ("Скорость", ("speed_print", "speed_wall_0", "speed_wall_x", "speed_infill", "speed_topbottom",
                            "speed_layer_0", "speed_travel")),
              ("Перемещение", ("retraction_hop_enabled",)),
              ("Охлаждение", ("cool_fan_enabled", "cool_fan_speed", "cool_min_layer_time")),
              ("Поддержки", ("support_enable", "support_type", "support_angle", "support_infill_rate", "support_z_distance",
                             "support_interface_enable", "support_enable_cylindrical")),
              ("Прилипание к столу", ("adhesion_type", "brim_width", "skirt_line_count", "raft_margin", "xy_offset_layer_0")),
              ("Режимы", ("printing_mode", "magic_spiralize")),
              ("5D", ("cylindrical_mode_base_diameter", "cylindrical_raft_enabled"))]
CATEGORY = dict((k, c) for c, keys in CATEGORIES for k in keys)
OPTIONS = {
    "printing_mode": [["classic", "Классический 3D"], ["cylindrical_full", "Цилиндрический 5D"]],
    "adhesion_type": [["skirt", "Юбка"], ["brim", "Кайма"], ["raft", "Подложка"], ["none", "Нет"]],
    "infill_pattern": [["grid", "Сетка"], ["lines", "Линии"], ["triangles", "Треугольники"], ["cubic", "Куб"],
                       ["gyroid", "Гироид"]],
    "z_seam_type": [["back", "Задан пользователем"], ["shortest", "Кратчайший"], ["random", "Случайно"],
                    ["sharpest_corner", "Самый острый угол"]],
    "support_type": [["buildplate", "От стола"], ["everywhere", "Везде"]],
}
# Что из профиля материала (data/materials.json → profile) ложится в какой параметр.
PROFILE_KEYS = {"print_temp": ("material_print_temperature", "material_print_temperature_layer_0"),
                "bed_temp": ("material_bed_temperature",), "fan": ("cool_fan_speed",),
                "retract": ("retraction_amount",), "speed": ("speed_print", "speed_infill", "speed_wall_x")}
SHRINKING = ("PP", "ABS", "PA", "PC")

DENSITY = {"enduse_tpu_d70": 1.23, "enduse_pa6": 1.05, "fiberpart_pa12_g12": 1.05, "fiberpart_abspa_g8": 1.12,
           "fiberpart_pp_g30": 1.18, "sealant_tpv80": 1.13, "enduse_petg": 1.3, "generic_pla": 1.24}


def _rot(axis, deg):
    a = math.radians(deg)
    c, s = math.cos(a), math.sin(a)
    if axis == "X":
        return np.array([[1, 0, 0], [0, c, -s], [0, s, c]])
    if axis == "Y":
        return np.array([[c, 0, s], [0, 1, 0], [-s, 0, c]])
    return np.array([[c, -s, 0], [s, c, 0], [0, 0, 1]])


class FakeBridge(object):
    def __init__(self, slice_delay=0.0):
        self.nodes = {}
        self.order = []
        self.settings = dict(DEFAULTS)
        self.user = set()  # ключи, заданные «пользовательскими изменениями», как userChanges в STE Slicer
        self.material_id = "generic_pla"
        self.quality = "normal"
        self.next_id = 1
        self.slice_delay = slice_delay
        self.last_slice = None
        self.sliced = False

    # ------------------------------------------------------------ принтер и сцена
    def printer(self):
        return {"name": "Stereotech Hybrid 530 V5.2", "definition": "hte530v5.2", "volume": [315, 300, 300],
                "shape": "elliptic"}

    def load_model(self, path):
        low = path.lower()
        if not low.endswith(".stl"):
            raise ValueError("Демо-режим без слайсера читает только STL")
        tris = geometry.stl_to_scene(geometry.load_stl(path))
        self.clear()  # как SlicerBridge: каждая деталь — отдельная сцена
        nid = "n%d" % self.next_id
        self.next_id += 1
        self.nodes[nid] = {"name": path.replace("\\", "/").split("/")[-1], "tris": tris}
        self.order.append(nid)
        self.place(nid, center=False)
        self.sliced = False
        return nid

    def models(self):
        out = []
        for nid in self.order:
            t = self.nodes[nid]["tris"].reshape(-1, 3)
            size = t.max(axis=0) - t.min(axis=0)
            out.append({"id": nid, "name": self.nodes[nid]["name"], "size_mm": [round(float(v), 1) for v in size]})
        return out

    def mesh(self, nid):
        return self.nodes[nid]["tris"].copy()

    def model_info(self, nid):
        p = self.nodes[nid]["tris"].reshape(-1, 3)
        size = p.max(axis=0) - p.min(axis=0)
        vol = self.printer()["volume"]
        outside = bool(size[0] > vol[0] or size[1] > vol[1] or size[2] > vol[2] or p[:, 1].min() < -0.05)
        return {"size_mm": [round(float(v), 1) for v in size], "outside": outside}

    def lay_flat(self, nid):
        geo = geometry.analyze(self.nodes[nid]["tris"])
        best = max(geo["orientations"], key=lambda c: c["bed_mm2"])
        if best["rotate"]:
            self.rotate(nid, best["rotate"][0], best["rotate"][1])
        self.place(nid)

    def rotate(self, nid, axis, deg):
        n = self.nodes[nid]
        t = n["tris"]
        c = t.reshape(-1, 3).mean(axis=0)
        n["tris"] = (t - c).dot(_rot(axis, deg).T) + c
        self.sliced = False

    def rotate_vec(self, nid, axis, deg):
        n = self.nodes[nid]
        t = n["tris"]
        c = t.reshape(-1, 3).mean(axis=0)
        n["tris"] = (t - c).dot(geometry.rot_matrix(axis, deg).T) + c
        self.sliced = False

    def choose_folder(self, start=None):
        """Без STE Slicer: диалог tkinter, если он есть и есть экран; иначе None (папку впишут вручную)."""
        try:
            import tkinter
            from tkinter import filedialog
            root = tkinter.Tk()
            root.withdraw()
            root.attributes("-topmost", True)
            path = filedialog.askdirectory(initialdir=start or os.path.expanduser("~"), title="Папка для проекта и G-code")
            root.destroy()
            return path or None
        except Exception:
            raise RuntimeError("Выбор папки работает в STE Slicer — здесь впишите путь вручную")

    def scene_triangles(self):
        return int(sum(len(n["tris"]) for n in self.nodes.values()))

    def place(self, nid, center=False, offset=None):
        n = self.nodes[nid]
        p = n["tris"].reshape(-1, 3)
        lo, hi = p.min(axis=0), p.max(axis=0)
        shift = np.array([0.0, -lo[1], 0.0])
        if center:
            ox, oz = (offset[0], offset[2]) if offset else (0.0, 0.0)
            shift[0] = -((lo[0] + hi[0]) / 2 + ox)
            shift[2] = -((lo[2] + hi[2]) / 2 + oz)
        n["tris"] = n["tris"] + shift
        self.sliced = False

    def arrange(self):
        x = -100.0
        for nid in self.order:
            self.place(nid, center=True)
            p = self.nodes[nid]["tris"].reshape(-1, 3)
            w = p[:, 0].max() - p[:, 0].min()
            self.nodes[nid]["tris"] = self.nodes[nid]["tris"] + np.array([x + w / 2, 0, 0])
            x += w + 8

    def multiply(self, nid, count):
        for _ in range(max(0, count - 1)):
            k = "n%d" % self.next_id
            self.next_id += 1
            self.nodes[k] = {"name": self.nodes[nid]["name"], "tris": self.nodes[nid]["tris"].copy()}
            self.order.append(k)
        self.arrange()

    def remove_copies(self, nid):
        gone = [k for k in self.order if k != nid]
        for k in gone:
            self.nodes.pop(k, None)
        self.order = [nid] if nid in self.nodes else []
        if gone:
            self.sliced = False
        return len(gone)

    def clear(self):
        self.nodes, self.order = {}, []

    # ------------------------------------------------------------ настройки
    def get_settings(self, keys=None):
        keys = keys or list(self.settings.keys())
        return dict((k, self.settings.get(k)) for k in keys)

    def set_settings(self, values):
        changes = []
        for k, v in values.items():
            old = self.settings.get(k)
            self.user.add(k)
            if old != v:
                self.settings[k] = v
                changes.append((k, old, v))
        if changes:
            self.sliced = False
        return changes

    def reset_settings(self, keys):
        out = []
        for k in keys or []:
            if k in self.user:
                old = self.settings.get(k)
                self.user.discard(k)
                self.settings[k] = self._base(k)
                if self.settings[k] is None:
                    self.settings.pop(k, None)
                out.append((k, old, self.settings.get(k)))
        if out:
            self.sliced = False
        return out

    def setting_info(self, keys):
        out = {}
        for k in keys or []:
            if k not in SETTING_INFO:
                continue
            out[k] = self._describe(k)
        return out

    def _describe(self, k):
        label, desc, unit, typ, lo, hi = SETTING_INFO[k]
        return {"label": label, "description": desc, "unit": unit, "type": typ, "min": lo, "max": hi,
                "options": [list(o) for o in OPTIONS.get(k, [])], "enabled": True, "value": self.settings.get(k),
                "changed": k in self.user}

    def list_settings(self, query="", limit=80):
        q = (query or "").strip().lower()
        found, total = [], 0
        for cat, keys in CATEGORIES:
            for k in keys:
                if k not in SETTING_INFO:
                    continue
                if q:
                    if q not in k.lower() and q not in SETTING_INFO[k][0].lower() and q not in cat.lower():
                        continue
                elif k not in self.user:
                    continue
                total += 1
                if len(found) < limit:
                    item = self._describe(k)
                    item["key"], item["category"] = k, cat
                    found.append(item)
        return {"items": found, "total": total}

    def _profile(self):
        from . import materials
        prof = (materials.by_id(self.material_id) or {}).get("profile") or {}
        out = {}
        for name, keys in PROFILE_KEYS.items():
            if prof.get(name) is not None:
                for k in keys:
                    out[k] = float(prof[name])
        return out

    def _base(self, k):
        """Значение «из профиля» — без пользовательских изменений: материал, затем значения по умолчанию."""
        return self._profile().get(k, DEFAULTS.get(k))

    def validate(self, keys):
        out = {}
        for k in keys or []:
            info = SETTING_INFO.get(k)
            v = self.settings.get(k)
            state = "ok"
            if info and isinstance(v, (int, float)) and not isinstance(v, bool):
                if info[4] is not None and v < info[4]:
                    state = "MinimumError"
                elif info[5] is not None and v > info[5]:
                    state = "MaximumError"
            out[k] = state
        return out

    def model_warnings(self):
        from . import materials
        m = materials.by_id(self.material_id) or {}
        big = any(max(self.model_info(n)["size_mm"]) > 150 for n in self.order)
        if big and any(m.get("base", "").startswith(x) for x in SHRINKING):
            return ["Деталь крупная, а материал даёт усадку: возможны коробление и отрыв от стола."]
        return []

    def material_profiles(self):
        from . import materials
        out = []
        for m in materials.MATERIALS + materials.SUPPORT_MATERIALS + materials.SPECIAL_MATERIALS:
            if m["id"] in getattr(self, "missing_materials", ()):
                continue
            out.append({"id": m["id"], "name": m["name"], "brand": "Stereotech", "type": m.get("base"),
                        "density": m.get("density"), "diameter": 1.75, "description": m.get("note", ""),
                        "adhesion_info": ""})
        return out

    def material(self):
        return self.material_id

    def set_material(self, mid):
        old = self.material_id
        self.material_id = mid
        for k, v in self._profile().items():  # профиль материала: температуры, обдув, откат, скорость
            if k not in self.user:
                self.settings[k] = v
        self.sliced = False
        return old

    def set_quality(self, qt):
        self.quality = qt

    # ------------------------------------------------------------ нарезка
    def slice(self, timeout=None, progress=None, should_stop=None, stall=None):
        import time
        if getattr(self, "fail_with", None):
            return dict(self.fail_with, ok=False)
        if not self.order:
            return {"ok": False, "error": "Нечего резать: на столе нет моделей", "code": "NothingToSlice"}
        vol = 0.0
        area = 0.0
        for nid in self.order:
            t = self.nodes[nid]["tris"]
            vol += geometry.volume(t) / 1000.0
            a, _, _ = geometry.face_data(t)
            area += float(a.sum()) / 100.0
            p = t.reshape(-1, 3)
            if p[:, 1].min() < -0.05:
                return {"ok": False, "error": "Модель вне рабочей зоны: часть ниже стола",
                        "code": "NothingToSlice"}
        s = self.settings
        walls = float(s.get("wall_line_count", 2))
        shell = min(vol, area * 0.04 * walls)
        used = shell + max(vol - shell, 0) * float(s.get("infill_sparse_density", 20)) / 100.0
        if s.get("support_enable"):
            used *= 1.12
        layer = float(s.get("cylindrical_layer_height" if s.get("printing_mode") != "classic" else "layer_height", 0.1))
        dens = DENSITY.get(self.material_id, 1.2)
        grams = used * dens
        meters = used / (math.pi * 0.0875 ** 2) / 100.0
        minutes = used / (layer * 0.45 * 40 * 0.06) + 5
        minutes *= 0.4 + 0.6 * 50.0 / max(float(s.get("speed_print") or 50), 1.0)  # скорость — на время печати
        if s.get("printing_mode") != "classic":
            minutes *= 0.8
        if progress:
            stages = (0.05, 0.12, 0.2, 0.28, 0.6, 0.9) if s.get("printing_mode") != "classic" else (0.25, 0.6, 0.9)
            for k in stages:
                if self.slice_delay:
                    time.sleep(self.slice_delay / len(stages))
                if should_stop and should_stop():
                    return {"ok": False, "error": "Нарезку остановили", "code": "Stopped"}
                progress(k)
        sec = minutes * 60
        feats = {"inset_0": 0.30 * sec, "inset_x": 0.22 * sec, "infill": 0.31 * sec, "skin": 0.09 * sec,
                 "travel": 0.08 * sec}
        if s.get("support_enable"):
            feats["support"] = 0.1 * sec
        self.sliced = True
        self.last_slice = {"ok": True, "time_s": sec, "features": feats, "grams": grams, "meters": meters,
                           "warnings": []}
        return copy.deepcopy(self.last_slice)

    def save_project(self, path):
        with open(path, "w", encoding="utf-8") as f:
            f.write("Демо-проект STE Assistant: %s\n" % ", ".join(self.nodes[n]["name"] for n in self.order))
        return path

    def save_gcode(self, path):
        if not self.sliced:
            raise RuntimeError("Сначала нарежьте модель")
        with open(path, "w", encoding="utf-8") as f:
            f.write(";FLAVOR:Marlin\n;Демо G-code STE Assistant\n;PRINTING_MODE:%s\n" % self.settings["printing_mode"])
            for k, v in sorted(self.settings.items()):
                f.write(";SETTING %s = %s\n" % (k, v))
        return path
