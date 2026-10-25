# -*- coding: utf-8 -*-
"""Анализ геометрии модели: сетка из сцены STE Slicer → trimesh из состава слайсера.

Что берём из trimesh (если он и его зависимости есть в STE Slicer):
  * symmetry / symmetry_axis — кандидат оси вращения;
  * ray.intersects_any — сквозное ли отверстие по оси (луч вдоль оси ничего не задевает);
  * poses.compute_stable_poses — насколько устойчиво деталь стоит на каждой грани габарита.
trimesh определяет симметрию по моментам инерции, поэтому квадратный брусок для него тоже
«тело вращения». Круглость сечений проверяем по одной оси-кандидату (numpy).
Если trimesh нет или он старый — тот же разбор целиком на numpy; источник пишется в res["source"].
Поддержки, проверку настроек и время печати считает сам слайсер.

Координаты — как в сцене STE Slicer (Uranium): Y вверх, стол — плоскость XZ.
STL при загрузке переводится в сцену как (x, z, -y), см. stl_to_scene.
Зависимость одна — numpy, он есть в составе STE Slicer. Совместимо с Python 3.5.
"""
import math
import re
import struct

import numpy as np

try:  # trimesh входит в состав STE Slicer (как и в Cura); в тестах без него — numpy
    import trimesh
except Exception:  # pragma: no cover - зависит от сборки слайсера
    trimesh = None

UP = 1  # ось Y сцены
AXIS_NAMES = "XYZ"


def load_stl(path):
    """Треугольники (n, 3, 3) в координатах файла. Бинарный и ASCII STL."""
    with open(path, "rb") as f:
        data = f.read()
    if len(data) >= 84:
        count = struct.unpack("<I", data[80:84])[0]
        need = 84 + 50 * count
        # Бинарный: длина совпадает, или файл длиннее (некоторые программы дописывают хвост) и не начинается с «solid».
        if count and (len(data) == need or (len(data) > need and not data[:5].lower() == b"solid")):
            dtype = np.dtype([("n", "<3f4"), ("v", "<9f4"), ("a", "<u2")])
            tris = np.frombuffer(data[84:], dtype=dtype, count=count)
            return tris["v"].reshape(-1, 3, 3).astype(float)
    verts = re.findall(rb"vertex\s+(\S+)\s+(\S+)\s+(\S+)", data)
    if not verts:
        raise ValueError("В файле не найдено треугольников STL")
    return np.array(verts, dtype=float).reshape(-1, 3, 3)


def stl_to_scene(tris):
    """Как Uranium STLReader: (x, y, z) файла -> (x, z, -y) сцены."""
    out = np.empty_like(tris)
    out[..., 0] = tris[..., 0]
    out[..., 1] = tris[..., 2]
    out[..., 2] = -tris[..., 1]
    return out


def face_data(tris):
    cross = np.cross(tris[:, 1] - tris[:, 0], tris[:, 2] - tris[:, 0])
    area = np.linalg.norm(cross, axis=1) / 2
    normals = cross / np.maximum(2 * area, 1e-12)[:, None]
    return area, normals, tris.mean(axis=1)


def volume(tris):
    return abs(float(np.sum(np.einsum("ij,ij->i", tris[:, 0], np.cross(tris[:, 1], tris[:, 2])))) / 6)


def to_trimesh(tris):
    """Сетка сцены → trimesh.Trimesh с объединёнными вершинами или None."""
    if trimesh is None:
        return None
    try:
        t = np.asarray(tris, dtype=float)
        return trimesh.Trimesh(vertices=t.reshape(-1, 3), faces=np.arange(len(t) * 3).reshape(-1, 3), process=True)
    except Exception:
        return None


PROXY_TRIANGLES = 120000


def proxy(tris, limit=PROXY_TRIANGLES):
    """Упрощённая копия сетки для тяжёлых шагов разбора (trimesh, поиск оси).

    Вершины сводятся к сетке с шагом cell (среднее по ячейке), вырожденные и повторные треугольники
    отбрасываются; шаг растёт, пока треугольников больше limit. Площади, нависания и канал считаются
    по исходной сетке — им упрощение не нужно. Фигурка на 565 тыс. треугольников разбиралась ~8 с.
    """
    if len(tris) <= limit:
        return tris
    p = tris.reshape(-1, 3)
    lo = p.min(axis=0)
    size = float((p.max(axis=0) - lo).max())
    # Шаг сетки — по площади: при limit треугольниках средний треугольник занимает площадь S / limit.
    a = np.linalg.norm(np.cross(tris[:, 1] - tris[:, 0], tris[:, 2] - tris[:, 0]), axis=1)
    cell = max(math.sqrt(2.0 * float(a.sum()) / 2.0 / limit), 0.05)
    best = tris
    for _ in range(8):
        q = np.floor((p - lo) / cell).astype(np.int64)
        dims = q.max(axis=0) + 1
        key = (q[:, 0] * dims[1] + q[:, 1]) * dims[2] + q[:, 2]  # одно число на ячейку — unique по 1-D быстро
        _, inv, cnt = np.unique(key, return_inverse=True, return_counts=True)
        inv = inv.reshape(-1)
        rep = np.zeros((len(cnt), 3))
        np.add.at(rep, inv, p)
        rep /= cnt[:, None]
        f = inv.reshape(-1, 3)
        f = f[(f[:, 0] != f[:, 1]) & (f[:, 1] != f[:, 2]) & (f[:, 0] != f[:, 2])]
        fs = np.sort(f, axis=1).astype(np.int64)
        n = np.int64(len(cnt))
        _, keep = np.unique((fs[:, 0] * n + fs[:, 1]) * n + fs[:, 2], return_index=True)
        out = rep[f[np.sort(keep)]]
        if limit // 4 <= len(out) <= limit:
            return out
        if len(out) <= limit and (best is tris or len(out) > len(best)):
            best = out  # самая подробная из подходящих по размеру
        cell = cell * 1.4 if len(out) > limit else cell / 1.5  # слишком грубо — форма теряется; мельче
    # Не нашли подходящий шаг — лучше исходная сетка, чем схлопнутая.
    return best if len(best) >= max(limit // 20, 200) else tris


def tm_axis(mesh):
    """Ось-кандидат из trimesh.symmetry_axis, если она вдоль оси сцены: индекс 0..2, None или False (нет функции)."""
    if mesh is None or not hasattr(mesh, "symmetry_axis"):
        return False
    try:
        if getattr(mesh, "symmetry", None) not in ("radial", "spherical"):
            return None
        v = np.asarray(mesh.symmetry_axis, dtype=float)
        j = int(np.argmax(np.abs(v)))
        return j if abs(v[j]) > 0.98 else None
    except Exception:
        return False


def tm_through(mesh, ax, center):
    """Сквозное ли отверстие по оси: луч вдоль оси через центр не задевает сетку. None — нет данных."""
    if mesh is None:
        return None
    try:
        d = np.zeros(3)
        d[ax] = 1.0
        start = np.array(center, dtype=float)
        start[ax] = float(mesh.bounds[0][ax]) - 1.0
        return not bool(mesh.ray.intersects_any([start], [d])[0])
    except Exception:  # нет rtree / embree в сборке
        return None


def tm_stable(mesh):
    """Вероятности устойчивых положений trimesh, сведённые к 6 граням габарита: {"-Y": p, ...} или None.

    trimesh кладёт деталь на плоскость XY, «вниз» — −Z; переводим в направление сцены.
    """
    if mesh is None or not hasattr(trimesh, "poses"):
        return None
    try:
        transforms, probs = trimesh.poses.compute_stable_poses(mesh)
    except Exception:  # нет scipy / networkx в сборке
        return None
    out = dict((name, 0.0) for name, _ in DIRS)
    for T, p in zip(transforms, probs):
        down = np.asarray(T)[:3, :3].T.dot([0.0, 0.0, -1.0])
        for name, d in DIRS:
            if float(np.dot(down, d)) > 0.95:
                out[name] += float(p)
    return out


def roundness(points, lo, hi, ax):
    """Асимметрия сечений вокруг оси ax: 0 — круг; 9 — не посчитать."""
    center = (lo + hi) / 2
    o = [j for j in range(3) if j != ax]
    rel = points[:, o] - center[o]
    r = np.hypot(rel[:, 0], rel[:, 1])
    sector = (((np.arctan2(rel[:, 1], rel[:, 0]) + np.pi) / (2 * np.pi)) * 12).astype(int) % 12
    t = points[:, ax]
    edges = np.linspace(lo[ax], hi[ax], 21)
    scores = []
    for k in range(20):
        m = (t >= edges[k]) & (t <= edges[k + 1])
        if m.sum() < 30:
            continue
        rm, sm = r[m], sector[m]
        maxima = [rm[sm == s].max() for s in range(12) if (sm == s).any()]
        if len(maxima) == 12:
            scores.append(np.std(maxima) / max(np.mean(maxima), 1e-6))
    return float(np.median(scores)) if scores else 9.0


def rotation_axis(points, lo, hi):
    """Запасной путь без trimesh: ось, вокруг которой сечения ближе всего к окружностям."""
    best = (None, 9.0)
    for ax in range(3):
        score = roundness(points, lo, hi, ax)
        if score < best[1]:
            best = (ax, score)
    return best


def offcenter_axis(points, lo, hi):
    """Ось, смещённая от центра габарита: вал с кулачком, ступица с рычагом.

    По каждой оси сцены режем деталь на 20 сечений; круглое сечение — то, у которого вершины по 12 секторам
    одинаково далеко от центра его собственного габарита. Ось есть, если круглых сечений не меньше половины
    и их центры совпадают (разброс до 0,5 мм). Возвращает (ось, асимметрия, центр) или (None, 9.0, None).
    """
    best = (None, 9.0, None, 0.0)
    for ax in range(3):
        o = [j for j in range(3) if j != ax]
        t = points[:, ax]
        edges = np.linspace(lo[ax], hi[ax], 21)
        valid, rnd = 0, []
        for k in range(20):
            m = (t >= edges[k]) & (t <= edges[k + 1])
            if m.sum() < 30:
                continue
            valid += 1
            q = points[m][:, o]
            c = (q.min(axis=0) + q.max(axis=0)) / 2
            rel = q - c
            r = np.hypot(rel[:, 0], rel[:, 1])
            sector = (((np.arctan2(rel[:, 1], rel[:, 0]) + np.pi) / (2 * np.pi)) * 12).astype(int) % 12
            maxima = [r[sector == sct].max() for sct in range(12) if (sector == sct).any()]
            if len(maxima) == 12:
                score = np.std(maxima) / max(np.mean(maxima), 1e-6)
                if score < 0.06:
                    rnd.append((score, c))
        if not valid or len(rnd) < 0.5 * valid:
            continue
        cs = np.array([c for _, c in rnd])
        med = np.median(cs, axis=0)
        if float(np.hypot(*(cs - med).T).max()) > 0.5:
            continue
        frac = len(rnd) / float(valid)
        asym = float(np.median([sc for sc, _ in rnd]))
        if frac > best[3] or (frac == best[3] and asym < best[1]):
            center = (lo + hi) / 2.0
            center[o[0]], center[o[1]] = med[0], med[1]
            best = (ax, asym, center, frac)
    return best[:3]


def _bore(points, ax, center, length):
    """Радиус сквозного отверстия по оси или None.

    Отверстие сквозное, если минимальный радиус точек заметно больше нуля и вершины
    на этом радиусе встречаются по всей длине детали вдоль оси.
    """
    o = [j for j in range(3) if j != ax]
    r = np.hypot(points[:, o[0]] - center[o[0]], points[:, o[1]] - center[o[1]])
    r_min = float(r.min())
    if r_min < 1.0:
        return None
    near = r < r_min * 1.08 + 0.2
    t = points[near, ax]
    if t.size == 0:
        return None
    covered = float(t.max() - t.min())
    return r_min if covered >= 0.8 * length else None


def free_core(tris, ax, center):
    """Радиус пустого канала вдоль оси ax через center: расстояние от оси до ближайшего треугольника.

    Каждый треугольник проецируется на плоскость поперёк оси; если проекция накрывает ось,
    канала нет (0). Иначе — минимум расстояния от оси до рёбер проекции. Считается по
    треугольникам, а не по вершинам: торцевая крышка без вершины в центре тоже закрывает канал.
    """
    o = [j for j in range(3) if j != ax]
    p = tris[:, :, o] - np.asarray(center, dtype=float)[o]  # (n, 3, 2), ось — в начале координат
    a, b, c = p[:, 0], p[:, 1], p[:, 2]

    def cross(u, v):
        return u[:, 0] * v[:, 1] - u[:, 1] * v[:, 0]
    # Ось внутри проекции треугольника: знаки трёх ориентированных площадей совпадают.
    d1, d2, d3 = cross(b - a, -a), cross(c - b, -b), cross(a - c, -c)
    inside = ((d1 >= 0) & (d2 >= 0) & (d3 >= 0)) | ((d1 <= 0) & (d2 <= 0) & (d3 <= 0))
    area2 = np.abs(cross(b - a, c - a))
    inside &= area2 > 1e-9  # вырожденная (ребром к оси) проекция ничего не накрывает
    if inside.any():
        return 0.0

    def seg_dist(u, v):
        e = v - u
        ll = np.maximum((e * e).sum(axis=1), 1e-18)
        t = np.clip(-(u * e).sum(axis=1) / ll, 0.0, 1.0)
        q = u + e * t[:, None]
        return np.hypot(q[:, 0], q[:, 1])
    return float(min(seg_dist(a, b).min(), seg_dist(b, c).min(), seg_dist(c, a).min()))


SUPPORT_ANGLE = 50.0  # как support_angle по умолчанию в STE Slicer; план ставит то же значение


def overhang_3d(area, normals, centers, points, up, limit_deg=SUPPORT_ANGLE):
    """Площадь нависаний и площадь опоры на столе при направлении «вверх» = up.

    limit_deg — как support_angle в STE Slicer: угол нависания от вертикали, начиная с которого
    нужны поддержки. Грань нависает круче, если её нормаль ближе к «вниз», чем на 90° − limit_deg:
    cos(угла между нормалью и «вниз») > sin(limit_deg).
    """
    height = centers.dot(up) - points.dot(up).min()
    facing_down = -normals.dot(up)
    lifted = height > 0.3
    over = lifted & (facing_down > math.sin(math.radians(limit_deg)))
    bed = (~lifted) & (facing_down > 0.99)
    return float(area[over].sum()), float(area[bed].sum())


def overhang_5d(area, normals, centers, ax, center, base_r, limit_deg=SUPPORT_ANGLE):
    """В 5D «вверх» — от оси наружу. Считаем грани, смотрящие к оси дальше базы."""
    o = [j for j in range(3) if j != ax]
    radial = np.zeros_like(centers)
    radial[:, o] = centers[:, o] - center[o]
    r = np.linalg.norm(radial, axis=1)
    radial /= np.maximum(r, 1e-9)[:, None]
    facing_in = -np.einsum("ij,ij->i", normals, radial)
    over = (r > base_r + 0.5) & (facing_in > math.sin(math.radians(limit_deg)))
    return float(area[over].sum())


DIRS = [
    ("+X", np.array([1.0, 0, 0])), ("-X", np.array([-1.0, 0, 0])),
    ("+Y", np.array([0, 1.0, 0])), ("-Y", np.array([0, -1.0, 0])),
    ("+Z", np.array([0, 0, 1.0])), ("-Z", np.array([0, 0, -1.0])),
]

# Как повернуть модель, чтобы направление d (в сцене) смотрело вниз, на стол.
# (ось поворота, угол в градусах) — поворот вокруг осей сцены.
TO_DOWN = {
    "-Y": None,
    "+Y": ("X", 180.0),
    "+X": ("Z", -90.0),
    "-X": ("Z", 90.0),
    "+Z": ("X", 90.0),
    "-Z": ("X", -90.0),
}

DOWN_WORDS = {
    "-Y": "как в файле", "+Y": "перевёрнута", "+X": "на правом боку", "-X": "на левом боку",
    "+Z": "передом вниз", "-Z": "задом вниз",
}


def analyze(tris):
    """Полный разбор треугольников сцены. Возвращает словарь для карточки «Модель» и правил."""
    tris = np.asarray(tris, dtype=float)
    if tris.size == 0:
        raise ValueError("В модели нет треугольников — проверьте файл")
    points = tris.reshape(-1, 3)
    lo, hi = points.min(axis=0), points.max(axis=0)
    size = hi - lo
    area, normals, centers = face_data(tris)
    vol = volume(tris)
    total_area = float(area.sum())
    res = {
        "triangles": int(len(tris)),
        "size_mm": [round(float(v), 1) for v in size],
        "volume_cm3": round(vol / 1000.0, 1),
        "area_cm2": round(total_area / 100.0, 1),
        "mean_wall_mm": round(vol / max(total_area, 1e-9) * 2, 2),
    }

    light = proxy(tris, PROXY_TRIANGLES)  # для trimesh и поиска оси; на обычных моделях — та же сетка
    lpoints = light.reshape(-1, 3)
    mesh = to_trimesh(light)
    src = ["proxy %d" % len(light)] if light is not tris else []

    # Кандидаты ориентации: какая грань габарита смотрит вниз; устойчивость — из trimesh.
    stable = tm_stable(mesh)
    if stable is not None:
        src.append("compute_stable_poses")
    cands = []
    for name, d in DIRS:
        up = -d
        over, bed = overhang_3d(area, normals, centers, points, up)
        c = {"down": name, "words": DOWN_WORDS[name], "rotate": TO_DOWN[name],
             "overhang_mm2": int(round(over)), "bed_mm2": int(round(bed)),
             "height_mm": round(float(abs(np.dot(size, d))), 1)}
        if stable is not None:
            c["stability"] = round(stable[name], 3)
        cands.append(c)
    if stable is not None:  # неустойчивое положение (вероятность < 2 %) — в конец списка
        cands.sort(key=lambda c: (c["stability"] < 0.02, c["bed_mm2"] < 50, c["overhang_mm2"], -c["bed_mm2"], c["down"] != "-Y"))
    else:  # при равенстве — как в файле: модель не переворачиваем без причины
        cands.sort(key=lambda c: (c["bed_mm2"] < 50, c["overhang_mm2"], -c["bed_mm2"], c["down"] != "-Y"))
    res["orientations"] = cands

    cand = tm_axis(mesh)
    if cand is False:  # trimesh нет или в нём нет symmetry_axis — перебираем три оси сами
        ax, asym = rotation_axis(lpoints, lo, hi)
    else:
        src.append("symmetry_axis")
        if cand is not None:
            ax, asym = cand, roundness(lpoints, lo, hi, cand)
        else:
            # trimesh признаёт только сплошную симметрию: шестерня с зубьями или колесо с лопастями для него
            # «не радиальные». Проверяем сечения сами — как без trimesh.
            ax, asym = rotation_axis(lpoints, lo, hi)
    res["rotation_axis"] = None
    res["bore_mm"] = None
    res["core_mm"] = 0.0
    res["shell"] = False
    center = (lo + hi) / 2
    if ax is None or asym >= 0.06:
        ax2, asym2, c2 = offcenter_axis(lpoints, lo, hi)
        if ax2 is not None:
            ax, asym, center = ax2, asym2, c2
            src.append("offcenter")
    if ax is not None and asym < 0.06:
        o = [j for j in range(3) if j != ax]
        r_c = np.hypot(points[:, o[0]] - center[o[0]], points[:, o[1]] - center[o[1]])
        outer_r = float(r_c.max())
        # Смещение оси от центра габарита (в 5D деталь ставится на стол по оси, а не по габариту).
        res["axis_offset_mm"] = [round(float(center[j] - (lo[j] + hi[j]) / 2), 2) for j in range(3)]
        bore_r = _bore(points, ax, center, float(size[ax]))
        through = tm_through(mesh, ax, center)
        if through is not None:
            src.append("ray.intersects_any")
            if not through:
                bore_r = None
        res["rotation_axis"] = AXIS_NAMES[ax]
        res["axis_vertical"] = ax == UP
        res["outer_diameter_mm"] = round(2 * outer_r, 1)
        res["asymmetry"] = round(asym, 3)
        if bore_r:
            res["bore_mm"] = round(2 * bore_r, 1)
        # Оболочка: тонкая стенка вокруг большой пустоты (пыльник, гофра).
        solid_vol = math.pi * outer_r ** 2 * float(size[ax])
        r_all = np.hypot(points[:, o[0]] - center[o[0]], points[:, o[1]] - center[o[1]])
        res["inner_min_mm"] = round(2 * float(r_all.min()), 1)
        res["fill_ratio"] = round(vol / max(solid_vol, 1e-9), 3)
        res["shell"] = res["fill_ratio"] < 0.25 and res["mean_wall_mm"] < 4
        # Пустой канал по оси: если он есть, база 5D = канал и внутри базы печатать нечего.
        core_r = free_core(tris, ax, center)
        res["core_mm"] = round(math.floor(2 * core_r * 10) / 10.0, 1)
        res["overhang_5d_mm2"] = int(round(overhang_5d(area, normals, centers, ax, center, core_r)))
    res["part_type"] = part_type(res)
    res["lying_on_side"] = bool(res["rotation_axis"] and not res.get("axis_vertical"))
    res["flat"] = bool(size[UP] < 0.25 * max(size[0], size[2]))
    res["bed_footprint_mm2"] = cands[0]["bed_mm2"] if cands else 0
    res["source"] = "trimesh: " + ", ".join(src) if src else "numpy"
    return res


def part_type(res):
    if res.get("rotation_axis"):
        if res.get("bore_mm"):
            return "rotation_bore"
        if res.get("shell"):
            return "rotation_shell"
        return "rotation_solid"
    return "general"


TYPE_WORDS = {
    "rotation_bore": "тело вращения со сквозным отверстием",
    "rotation_shell": "тонкостенная оболочка вращения",
    "rotation_solid": "сплошное тело вращения",
    "general": "деталь общей формы",
}


def features(res):
    """Метки для карточки «Модель»: [(вид, текст)]."""
    out = []
    t = res["part_type"]
    if t != "general":
        out.append(("ok", "Тело вращения"))
    if res.get("bore_mm"):
        out.append(("ok", "Отверстие Ø%s сквозное" % _n(res["bore_mm"])))
    if res.get("shell"):
        out.append(("ok", "Тонкая стенка ~%s мм" % _n(res["mean_wall_mm"])))
    if res.get("rotation_axis"):
        out.append(("", "Ось по %s сцены" % res["rotation_axis"]))
    if res.get("flat"):
        out.append(("", "Плоская деталь"))
    if t == "general" and not res.get("flat"):
        out.append(("", "Отверстий по оси нет"))
    return out


def _n(v):
    return ("%g" % v).replace(".", ",")


def silhouette(tris, axis=None, bore_mm=None, grid=128):
    """Силуэт модели для карточки «Модель»: проекция сетки сцены на плоскость.

    Тело вращения показываем сбоку, осью вверх; остальное — спереди (X вправо, Y вверх).
    Треугольники закрашиваются построчно (по строке сетки — отрезок пересечения
    с каждым треугольником), соседние клетки строки сливаются в один прямоугольник.
    Возвращает {"w", "h", "d": путь SVG, "axis": x оси, "bore": полуширина отверстия} в клетках сетки;
    отверстие внутри тела сбоку не видно, его рисуем тонкими линиями.
    """
    tris = np.asarray(tris, dtype=float)
    if tris.size == 0:
        return None
    ax = AXIS_NAMES.index(axis) if axis in tuple(AXIS_NAMES) else None
    if ax is None:
        hj, vj = 0, UP
    else:
        rest = [j for j in range(3) if j != ax]
        pts = tris.reshape(-1, 3)
        span = pts.max(axis=0) - pts.min(axis=0)
        hj, vj = max(rest, key=lambda j: span[j]), ax
    x, y = tris[:, :, hj], tris[:, :, vj]
    x0, y0 = x.min(), y.min()
    span = max(x.max() - x0, y.max() - y0, 1e-6)
    k = (grid - 1) / span
    x, y = (x - x0) * k, (y.max() - y) * k  # y вниз, как в SVG
    w, h = int(math.ceil(x.max())) + 1, int(math.ceil(y.max())) + 1
    ymin, ymax = y.min(axis=1), y.max(axis=1)
    rows = []
    for r in range(h):
        yc = r + 0.5
        m = (ymin <= yc) & (ymax >= yc)
        if not m.any():
            rows.append([])
            continue
        tx, ty = x[m], y[m]
        xs = []
        for a, b in ((0, 1), (1, 2), (2, 0)):
            ya, yb, xa, xb = ty[:, a], ty[:, b], tx[:, a], tx[:, b]
            d = yb - ya
            ok = ((ya - yc) * (yb - yc) <= 0) & (d != 0)
            t = np.where(ok, (yc - ya) / np.where(d == 0, 1, d), np.nan)
            xs.append(xa + t * (xb - xa))
        xs = np.vstack(xs)
        good = ~np.all(np.isnan(xs), axis=0)
        if not good.any():
            rows.append([])
            continue
        lo = np.floor(np.nanmin(xs[:, good], axis=0)).astype(int)
        hi = np.ceil(np.nanmax(xs[:, good], axis=0)).astype(int)
        diff = np.zeros(w + 2, dtype=int)
        np.add.at(diff, np.clip(lo, 0, w), 1)
        np.add.at(diff, np.clip(hi, 0, w) + 1, -1)
        filled = np.cumsum(diff)[:w] > 0
        runs, c = [], 0
        while c < w:
            if filled[c]:
                s = c
                while c < w and filled[c]:
                    c += 1
                runs.append((s, c - s))
            else:
                c += 1
        rows.append(runs)
    # Одинаковые соседние строки сливаем по высоте — путь короче.
    parts, r = [], 0
    while r < h:
        n = 1
        while r + n < h and rows[r + n] == rows[r]:
            n += 1
        for s, ln in rows[r]:
            parts.append("M%d %dh%dv%dh-%dz" % (s, r, ln, n, ln))
        r += n
    out = {"w": w, "h": h, "d": "".join(parts), "axis": None, "bore": None}
    if ax is not None:
        out["axis"] = round(w / 2.0, 1)
        if bore_mm:
            out["bore"] = round(bore_mm / 2.0 * k, 1)
    return out


# ---------------------------------------------------------------- повороты

def rot_matrix(axis, deg):
    """Матрица поворота на deg градусов вокруг вектора axis (правило правой руки, как Uranium Quaternion)."""
    a = np.asarray(axis, dtype=float)
    a = a / max(np.linalg.norm(a), 1e-12)
    t = math.radians(deg)
    c, s = math.cos(t), math.sin(t)
    x, y, z = a
    return np.array([[c + x * x * (1 - c), x * y * (1 - c) - z * s, x * z * (1 - c) + y * s],
                     [y * x * (1 - c) + z * s, c + y * y * (1 - c), y * z * (1 - c) - x * s],
                     [z * x * (1 - c) - y * s, z * y * (1 - c) + x * s, c + z * z * (1 - c)]])


def axis_angle(m):
    """Обратное к rot_matrix: (ось, градусы) или None, если поворота нет."""
    m = np.asarray(m, dtype=float)
    c = max(-1.0, min(1.0, (np.trace(m) - 1) / 2))
    ang = math.acos(c)
    if ang < 1e-6:
        return None
    if math.pi - ang < 1e-4:  # поворот на 180°: ось — из (m + I)
        b = m + np.eye(3)
        axis = b[:, int(np.argmax(np.linalg.norm(b, axis=0)))]
    else:
        axis = np.array([m[2, 1] - m[1, 2], m[0, 2] - m[2, 0], m[1, 0] - m[0, 1]]) / (2 * math.sin(ang))
    return axis / np.linalg.norm(axis), math.degrees(ang)


def rotation_to(v_from, v_to):
    """Поворот, переводящий направление v_from в v_to: (ось (x, y, z), угол в градусах).

    Противоположные направления — поворот на 180° вокруг любой перпендикулярной оси.
    """
    f = np.asarray(v_from, dtype=float)
    t = np.asarray(v_to, dtype=float)
    f = f / max(np.linalg.norm(f), 1e-12)
    t = t / max(np.linalg.norm(t), 1e-12)
    d = float(np.clip(np.dot(f, t), -1.0, 1.0))
    if d > 1 - 1e-9:
        return (1.0, 0.0, 0.0), 0.0
    if d < -1 + 1e-9:
        perp = np.cross(f, [1.0, 0.0, 0.0])
        if np.linalg.norm(perp) < 1e-6:
            perp = np.cross(f, [0.0, 0.0, 1.0])
        perp = perp / np.linalg.norm(perp)
        return tuple(float(x) for x in perp), 180.0
    ax = np.cross(f, t)
    ax = ax / np.linalg.norm(ax)
    return tuple(float(x) for x in ax), math.degrees(math.acos(d))
