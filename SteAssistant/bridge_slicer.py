# -*- coding: utf-8 -*-
"""Мост к STE Slicer: сцена, настройки, материал, нарезка, сохранение — только через API слайсера.

Методы вызываются из рабочего потока агента. Всё, что трогает сцену, настройки и Qt,
выполняется в главном потоке через Application.callLater, а рабочий поток ждёт результат
(не дольше timeout). Интерфейс — как у FakeBridge (см. bridge_fake.py).
"""
import math
import os
import threading
import time
import uuid

import numpy as np

from UM.Logger import Logger
from UM.Math.Quaternion import Quaternion
from UM.Math.Vector import Vector
from UM.Operations.RotateOperation import RotateOperation
from UM.Operations.TranslateOperation import TranslateOperation
from UM.Scene.Iterator.DepthFirstIterator import DepthFirstIterator
from UM.Scene.SceneNode import SceneNode

try:
    from UM.Backend.Backend import BackendState
except ImportError:  # pragma: no cover
    BackendState = None

from PyQt5.QtCore import QUrl

SETTING_TYPES = ("float", "int", "bool", "enum")  # что можно менять из чата; строки и полигоны — нет

AXES = {"X": Vector(1, 0, 0), "Y": Vector(0, 1, 0), "Z": Vector(0, 0, 1)}

START_ERRORS = {
    "Error": "Ошибка подготовки нарезки",
    "SettingError": "Ошибка в настройках: проверьте значения, подсвеченные в слайсере",
    "NothingToSlice": "Нечего резать: модель не помещается в рабочую зону — поверните или уменьшите её",
    "MaterialIncompatible": "Материал несовместим с принтером или соплом",
    "BuildPlateError": "Неверная позиция стола или башни",
    "ObjectSettingError": "Ошибка в настройках отдельной модели",
    "ObjectsWithDisabledExtruder": "Модель привязана к выключенному экструдеру",
}


class MainThreadTimeout(Exception):
    pass


class _Done(object):
    """Флаг окончания Job. Uranium Signal не удерживает лямбды, поэтому — метод объекта."""

    def __init__(self):
        self.event = threading.Event()

    def set(self, *args):
        self.event.set()


class SlicerBridge(object):
    def __init__(self, app):
        self.app = app
        self._nodes = {}

    # ------------------------------------------------------------ главный поток
    def _main(self, fn, timeout=60.0):
        if threading.current_thread() is threading.main_thread():
            return fn()
        box = {}
        ev = threading.Event()

        def call():
            try:
                box["value"] = fn()
            except Exception as e:  # передаём ошибку в рабочий поток
                box["error"] = e
            finally:
                ev.set()
        self.app.callLater(call)
        if not ev.wait(timeout):
            raise MainThreadTimeout("STE Slicer не ответил за %d с" % timeout)
        if "error" in box:
            raise box["error"]
        return box.get("value")

    def _stack(self):
        st = self.app.getGlobalContainerStack()
        if st is None:
            raise RuntimeError("В STE Slicer не выбран принтер")
        return st

    def _extruder(self, st):
        ex = getattr(st, "extruders", None) or {}
        return ex.get("0") if hasattr(ex, "get") else None

    def _sliceable(self):
        root = self.app.getController().getScene().getRoot()
        out = []
        for node in DepthFirstIterator(root):
            if not isinstance(node, SceneNode) or not node.getMeshData():
                continue
            if not node.callDecoration("isSliceable"):
                continue
            out.append(node)
        return out

    def _tag(self, node, taken=()):
        """Постоянный номер модели. Адрес объекта (id) после очистки сцены может достаться
        новой модели, и деталь «нашла» бы чужую — поэтому метку храним на самом узле.
        Копия узла метку не наследует, а если вдруг унаследовала — получает свою."""
        nid = getattr(node, "_ste_assistant_id", None)
        if not nid or nid in taken or (nid in self._nodes and self._nodes[nid] is not node):
            nid = "n" + uuid.uuid4().hex[:12]
            try:
                node._ste_assistant_id = nid
            except AttributeError:
                pass
        self._nodes[nid] = node
        return nid

    def _node(self, nid):
        node = self._nodes.get(nid)
        if node is None:
            raise KeyError("Модель %s больше не в сцене" % nid)
        return node

    # ------------------------------------------------------------ принтер
    def printer(self):
        """Принтер и рабочая зона. Ответ кэшируется на 30 с: чат спрашивает при каждом обновлении,
        а главный поток слайсера бывает занят (загрузка модели) — тогда отдаём прежний ответ."""
        cached = getattr(self, "_printer_cache", None)
        if cached and time.time() - cached[0] < 30:
            return cached[1]

        def f():
            st = self._stack()
            return {"name": st.definition.getName(), "definition": st.definition.getId(),
                    "volume": [st.getProperty("machine_width", "value"), st.getProperty("machine_height", "value"),
                               st.getProperty("machine_depth", "value")],
                    "shape": st.getProperty("machine_shape", "value")}
        try:
            info = self._main(f, 5)
        except MainThreadTimeout:
            if cached:
                return cached[1]
            raise
        self._printer_cache = (time.time(), info)
        return info

    # ------------------------------------------------------------ сцена
    def load_model(self, path):
        if not os.path.isfile(path):
            raise ValueError("Файл не найден: %s" % path)

        def start():
            # Каждая деталь — отдельная сцена: убираем прежние модели.
            try:
                self.app.deleteAll()
            except Exception:
                pass
            self._nodes = {}
            self.app.readLocalFile(QUrl.fromLocalFile(path), True)
        self._main(start)
        end = time.time() + 90
        norm = os.path.normcase(os.path.abspath(path))
        while time.time() < end:
            time.sleep(0.3)

            def find():
                for n in self._sliceable():
                    md = n.getMeshData()
                    fn = md.getFileName() if md else None
                    if fn and os.path.normcase(os.path.abspath(fn)) == norm:
                        return n
                return None
            node = self._main(find, 10)
            if node is not None:
                return self._main(lambda: self._tag(node))
        raise RuntimeError("STE Slicer не загрузил модель за 90 с")

    def models(self):
        def f():
            out = []
            seen = set()
            for n in self._sliceable():
                bb = n.getBoundingBox()
                nid = self._tag(n, seen)
                seen.add(nid)
                out.append({"id": nid, "name": n.getName(), "size_mm": [round(bb.width, 1), round(bb.height, 1), round(bb.depth, 1)]})
            return out
        return self._main(f)

    def mesh(self, nid):
        def f():
            node = self._node(nid)
            md = node.getMeshData()
            verts = np.asarray(md.getVertices(), dtype=float)
            idx = md.getIndices() if md.hasIndices() else None
            m = np.asarray(node.getWorldTransformation().getData(), dtype=float)
            return verts, (np.asarray(idx) if idx is not None else None), m
        verts, idx, m = self._main(f)
        homo = np.hstack([verts, np.ones((len(verts), 1))])
        world = homo.dot(m.T)[:, :3]
        if idx is not None:
            return world[idx.reshape(-1, 3)]
        return world.reshape(-1, 3, 3)

    def model_info(self, nid):
        """Габарит узла (SceneNode.getBoundingBox) и признак «вне рабочей зоны» (BuildVolume помечает узел)."""
        def f():
            node = self._node(nid)
            bb = node.getBoundingBox()
            outside = None
            if hasattr(node, "isOutsideBuildArea"):
                try:
                    outside = bool(node.isOutsideBuildArea())
                except Exception:
                    outside = None
            return {"size_mm": [round(bb.width, 1), round(bb.height, 1), round(bb.depth, 1)], "outside": outside}
        return self._main(f)

    def lay_flat(self, nid):
        """«Положить плашмя» STE Slicer: LayFlatOperation сама находит грань и поворачивает узел."""
        from UM.Operations.LayFlatOperation import LayFlatOperation

        def f():
            op = LayFlatOperation(self._node(nid))
            op.process()
            op.push()
        self._main(f, 120)

    def rotate(self, nid, axis, deg):
        def f():
            node = self._node(nid)
            q = Quaternion.fromAngleAxis(math.radians(deg), AXES[axis])
            RotateOperation(node, q, rotate_around_point=node.getBoundingBox().center).push()
        self._main(f)

    def rotate_vec(self, nid, axis, deg):
        """Поворот вокруг произвольного вектора сцены (x, y, z) на deg градусов, вокруг центра габарита."""
        def f():
            node = self._node(nid)
            q = Quaternion.fromAngleAxis(math.radians(deg), Vector(float(axis[0]), float(axis[1]), float(axis[2])))
            RotateOperation(node, q, rotate_around_point=node.getBoundingBox().center).push()
        self._main(f)

    def place(self, nid, center=False, offset=None):
        """Поставить на стол; center — центр габарита в центр стола, offset — сдвиг оси детали от центра габарита."""
        def f():
            node = self._node(nid)
            bb = node.getBoundingBox()
            ox, oz = (offset[0], offset[2]) if offset else (0.0, 0.0)
            dx = -(bb.center.x + ox) if center else 0.0
            dz = -(bb.center.z + oz) if center else 0.0
            TranslateOperation(node, Vector(dx, -bb.bottom, dz)).push()
        self._main(f)

    def arrange(self):
        self._main(lambda: self.app.arrangeAll())
        time.sleep(1.0)

    def multiply(self, nid, count):
        from steslicer.MultiplyObjectsJob import MultiplyObjectsJob
        done = _Done()  # живёт до конца метода: Signal держит методы по слабой ссылке

        def f():
            job = MultiplyObjectsJob([self._node(nid)], max(0, int(count) - 1), 8)
            job.finished.connect(done.set)
            job.start()
        self._main(f)
        done.event.wait(120)
        time.sleep(0.5)

    def remove_copies(self, nid):
        """Убрать со стола все модели, кроме nid (копии прошлой партии). Возвращает, сколько убрано."""
        from UM.Operations.GroupedOperation import GroupedOperation
        from UM.Operations.RemoveSceneNodeOperation import RemoveSceneNodeOperation

        def f():
            keep = self._node(nid)
            op = GroupedOperation()
            n = 0
            for node in self._sliceable():
                if node is not keep:
                    op.addOperation(RemoveSceneNodeOperation(node))
                    n += 1
            if n:
                op.push()
            return n
        return self._main(f)

    def clear(self):
        self._main(lambda: self.app.deleteAll())
        self._nodes = {}

    # ------------------------------------------------------------ настройки
    def get_settings(self, keys=None):
        def f():
            st = self._stack()
            ex = self._extruder(st)
            out = {}
            for k in keys or []:
                per = st.getProperty(k, "settable_per_extruder")
                stack = ex if (per and ex is not None) else st
                out[k] = stack.getProperty(k, "value")
            return out
        return self._main(f)

    def set_settings(self, values):
        def f():
            st = self._stack()
            ex = self._extruder(st)
            changes = []
            for k, v in values.items():
                typ = st.getProperty(k, "type")
                if typ is None:
                    Logger.log("w", "STE Assistant: настройка %s не найдена в STE Slicer", k)
                    continue
                v = _coerce(typ, v)
                per = st.getProperty(k, "settable_per_extruder")
                stack = ex if (per and ex is not None) else st
                old = stack.getProperty(k, "value")
                if old == v:
                    continue
                stack.userChanges.setProperty(k, "value", v)
                changes.append((k, old, v))
            return changes
        return self._main(f)

    def reset_settings(self, keys):
        """Убрать значения плагина из пользовательских изменений (userChanges) глобального стека
        и экструдера — параметр снова берётся из профиля качества и материала.
        Возвращает [(key, было, стало)] для тех, что действительно были заданы."""
        def f():
            st = self._stack()
            ex = self._extruder(st)
            out = []
            for k in keys or []:
                for stack in (st, ex):
                    if stack is None:
                        continue
                    uc = stack.userChanges
                    if uc.getInstance(k) is None:
                        continue
                    old = stack.getProperty(k, "value")
                    uc.removeInstance(k)
                    out.append((k, old, stack.getProperty(k, "value")))
            return out
        return self._main(f)

    def _tr(self):
        """Перевод подписей определений STE Slicer (ru_RU) — как в панели настроек слайсера."""
        try:
            from UM.i18n import i18nCatalog
            cats = [i18nCatalog("fdmprinter.def.json"), i18nCatalog("fdmextruder.def.json")]
        except Exception:
            cats = []

        def tr(key, what, text):
            for c in cats:
                try:
                    t = c.i18nc("%s %s" % (key, what), text)
                    if t and t != text:
                        return t
                except Exception:
                    pass
            return text
        return tr

    def _describe(self, st, ex, k, tr):
        per = st.getProperty(k, "settable_per_extruder")
        stack = ex if (per and ex is not None) else st
        label = stack.getProperty(k, "label") or k
        desc = stack.getProperty(k, "description") or ""
        opts = stack.getProperty(k, "options") or {}
        try:
            options = [[str(o), tr(k, "option %s" % o, str(lab))] for o, lab in opts.items()]
        except AttributeError:
            options = []
        changed = any(s is not None and s.userChanges.getInstance(k) is not None for s in (st, ex))
        return {"label": tr(k, "label", label), "description": tr(k, "description", desc),
                "unit": stack.getProperty(k, "unit") or "", "type": stack.getProperty(k, "type"),
                "min": stack.getProperty(k, "minimum_value"), "max": stack.getProperty(k, "maximum_value"),
                "options": options, "enabled": bool(stack.getProperty(k, "enabled")),
                "value": stack.getProperty(k, "value"), "changed": changed}

    def setting_info(self, keys):
        """Описание параметров из определений STE Slicer: подпись и описание (с переводом ru_RU),
        единицы, тип, пределы, варианты, доступность при текущих настройках и задан ли он вручную."""
        tr = self._tr()

        def f():
            st = self._stack()
            ex = self._extruder(st)
            out = {}
            for k in keys or []:
                if st.getProperty(k, "type") is None:
                    continue
                out[k] = self._describe(st, ex, k, tr)
            return out
        return self._main(f)

    def list_settings(self, query="", limit=80):
        """Все параметры STE Slicer, которые можно менять: поиск по ключу, подписи и разделу.

        Без запроса — только заданные вручную (userChanges). Раздел «Принтер» (machine_settings)
        не отдаём: это описание самого принтера, не печати.
        Возвращает {"items": [{"key", "category", ...setting_info}], "total": n}.
        """
        tr = self._tr()
        q = (query or "").strip().lower()

        def f():
            st = self._stack()
            ex = self._extruder(st)
            defn = getattr(st, "definition", None) or st.getBottom()
            found, total = [], 0
            for d in defn.findDefinitions():
                if d.type not in SETTING_TYPES:
                    continue
                top = d
                while top.parent is not None:
                    top = top.parent
                if top.key in ("machine_settings",):
                    continue
                k = d.key
                if q:
                    label = tr(k, "label", d.label or k)
                    cat = tr(top.key, "label", top.label or top.key)
                    if q not in k.lower() and q not in label.lower() and q not in cat.lower():
                        continue
                elif not any(s is not None and s.userChanges.getInstance(k) is not None for s in (st, ex)):
                    continue
                total += 1
                if len(found) < limit:
                    item = self._describe(st, ex, k, tr)
                    item["key"] = k
                    item["category"] = tr(top.key, "label", top.label or top.key)
                    found.append(item)
            return {"items": found, "total": total}
        return self._main(f)

    def validate(self, keys):
        """Состояние проверки параметров (то же, что подсвечивает MachineErrorChecker):
        {key: "ok" | "MinimumError" | "MaximumError" | "MinimumWarning" | "MaximumWarning" | "Exception"}."""
        def f():
            st = self._stack()
            ex = self._extruder(st)
            out = {}
            for k in keys or []:
                per = st.getProperty(k, "settable_per_extruder")
                stack = ex if (per and ex is not None) else st
                state = stack.getProperty(k, "validationState")
                name = str(state).split(".")[-1] if state is not None else "Unknown"
                out[k] = "ok" if name in ("Valid", "Unknown", "None") else name
            return out
        return self._main(f)

    def model_warnings(self):
        """Предупреждения ModelChecker STE Slicer (коробление крупных деталей из материалов с усадкой)."""
        def f():
            try:
                from UM.PluginRegistry import PluginRegistry
                mc = PluginRegistry.getInstance().getPluginObject("ModelChecker")
            except Exception:
                return []
            if mc is None:
                return []
            try:
                if hasattr(mc, "runChecks"):
                    mc.runChecks()
                has = mc.hasWarnings() if callable(getattr(mc, "hasWarnings", None)) else getattr(mc, "hasWarnings", False)
                if not has:
                    return []
                msg = getattr(mc, "_caution_message", None)
                text = msg.getText() if msg is not None and hasattr(msg, "getText") else ""
                return [text or "STE Slicer предупреждает о риске коробления: деталь крупная, материал с усадкой."]
            except Exception:
                return []
        return self._main(f)

    def material_profiles(self):
        """Профили материалов, доступные текущему принтеру и соплу (MaterialManager / ContainerRegistry):
        [{"id": root_material_id, "name", "brand", "type", "density", "diameter", "description", "adhesion_info"}]."""
        def meta_of(m):
            props = m.get("properties") or {}

            def num(v):
                try:
                    return float(v)
                except (TypeError, ValueError):
                    return None
            return {"id": m.get("base_file") or m.get("id"), "name": m.get("name"), "brand": m.get("brand"),
                    "type": m.get("material"), "density": num(props.get("density")),
                    "diameter": num(props.get("diameter")), "description": m.get("description") or "",
                    "adhesion_info": m.get("adhesion_info") or ""}

        def f():
            st = self._stack()
            ex = self._extruder(st)
            metas = []
            try:  # что доступно этому принтеру и соплу
                mm = self.app.getMaterialManager()
                nodes = mm.getAvailableMaterialsForMachineExtruder(st, ex) or {}
                for rid, node in nodes.items():
                    md = node.getMetaData() if hasattr(node, "getMetaData") else getattr(node, "metadata", {})
                    metas.append(dict(md or {}, base_file=rid))
            except Exception:
                metas = []
            if not metas:  # запасной путь: все корневые профили материалов в реестре
                from UM.Settings.ContainerRegistry import ContainerRegistry
                for m in ContainerRegistry.getInstance().findInstanceContainersMetadata(type="material"):
                    if m.get("base_file") in (None, m.get("id")):
                        metas.append(m)
            seen, out = set(), []
            for m in metas:
                d = meta_of(m)
                if d["id"] and d["id"] not in seen:
                    seen.add(d["id"])
                    out.append(d)
            return out
        return self._main(f, 30)

    def material(self):
        def f():
            ex = self._extruder(self._stack())
            return ex.material.getMetaDataEntry("base_file") if ex is not None else None
        return self._main(f)

    def set_material(self, root_id):
        def f():
            mm = self.app.getMachineManager()
            mm.setMaterialById("0", root_id)
            ex = self._extruder(self._stack())
            got = ex.material.getMetaDataEntry("base_file") if ex is not None else None
            if got != root_id:
                raise RuntimeError("материала %s нет для этого принтера и сопла" % root_id)
            return got
        return self._main(f)

    def set_quality(self, quality_type):
        def f():
            try:
                self.app.getMachineManager().setQualityGroupByQualityType(quality_type)
            except KeyError:
                Logger.log("w", "STE Assistant: профиль качества %s недоступен", quality_type)
        self._main(f)

    # ------------------------------------------------------------ нарезка
    def _settle(self, quiet=1.0, limit=10.0, should_stop=None):
        """Ждём, пока сцена и настройки перестанут меняться и закончится проверка настроек.

        Поворот, укладка, смена режима и материала доходят до бэкенда с задержкой (таймеры
        PlatformPhysics и MachineErrorChecker). Если запустить нарезку раньше, поздний сигнал
        вызывает stopSlicing() — нарезка молча сбрасывается.
        """
        last = [time.time()]

        def touch(*a, **k):
            last[0] = time.time()

        def connect():
            scene = self.app.getController().getScene()
            scene.sceneChanged.connect(touch)
            stack = self.app.getGlobalContainerStack()
            if stack is not None:
                stack.propertyChanged.connect(touch)
            return scene, stack
        scene, stack = self._main(connect)

        def checker_busy():
            try:
                checker = self.app.getMachineErrorChecker()
                return bool(checker is not None and checker.needToWaitForResult)
            except Exception:
                return False
        end = time.time() + limit
        try:
            while time.time() < end:
                if should_stop and should_stop():
                    return
                time.sleep(0.2)
                if time.time() - last[0] >= quiet and not self._main(checker_busy):
                    return
        finally:
            def disconnect():
                for sig in (scene.sceneChanged, stack.propertyChanged if stack is not None else None):
                    if sig is None:
                        continue
                    try:
                        sig.disconnect(touch)
                    except Exception:
                        pass
            self._main(disconnect)

    def choose_folder(self, start=None):
        """Системный диалог выбора папки (проводник Windows, Finder) поверх окна STE Slicer.

        Возвращает путь или None, если пользователь закрыл диалог. Ждём ответа человека — до 10 минут."""
        def f():
            from PyQt5.QtCore import Qt
            from PyQt5.QtWidgets import QFileDialog
            dlg = QFileDialog(None, "Папка для проекта и G-code", start if start and os.path.isdir(start) else os.path.expanduser("~"))
            dlg.setFileMode(QFileDialog.Directory)
            dlg.setOption(QFileDialog.ShowDirsOnly, True)
            dlg.setWindowFlags(dlg.windowFlags() | Qt.WindowStaysOnTopHint)  # чат в браузере — диалог не прячем за окнами
            dlg.raise_()
            dlg.activateWindow()
            if dlg.exec_() and dlg.selectedFiles():
                return dlg.selectedFiles()[0]
            return None
        return self._main(f, timeout=600.0)

    def scene_triangles(self):
        """Сколько треугольников во всех деталях сцены (с копиями) — от этого зависит время нарезки."""
        def f():
            total = 0
            for n in self._sliceable():
                md = n.getMeshData()
                if md is None:
                    continue
                total += (len(md.getIndices()) if md.hasIndices() else md.getVertexCount() // 3)
            return int(total)
        try:
            return self._main(f)
        except Exception:
            return 0

    def _slice_timeout(self):
        """(предел на всю нарезку, предел тишины), с. Растут с числом треугольников: детальная фигурка
        в 2–4 копиях — это 1–2 млн треугольников, и одна передача сетки в движок идёт минуты без сигналов."""
        mode = (self.get_settings(["printing_mode"]) or {}).get("printing_mode") or "classic"
        t_all, t_stall = (300, 120) if mode == "classic" else (1200, 240)
        tris = self.scene_triangles()
        return t_all + tris // 4000, t_stall + tris // 15000

    def slice(self, timeout=None, progress=None, should_stop=None, stall=None):
        """Нарезка с перезапуском, если её сбросило поздним изменением сцены или настроек.

        timeout — предел на всю нарезку, stall — сколько ждать без единого сигнала от бэкенда.
        """
        t_all, t_stall = self._slice_timeout()
        timeout = timeout or t_all
        stall = stall or t_stall
        self._settle(should_stop=should_stop)
        state = {"done": False, "error": None, "code": None, "times": None, "seen": False,
                 "reset_at": None, "error_at": None, "last": time.time(), "progress": 0.0}
        ev = threading.Event()

        def on_state(s):
            if BackendState is None:
                return
            state["last"] = time.time()
            if s == BackendState.Done:
                ev.set()
            elif s == BackendState.Error:
                # Причина придёт следом в backendError; если не придёт — общая ошибка (см. цикл ожидания).
                state["error_at"] = time.time()
            elif s == BackendState.Processing:
                state["seen"] = True
                state["reset_at"] = None
            elif s in (BackendState.NotStarted, BackendState.Disabled) and state["seen"]:
                # Нарезку остановили (сцена или настройки поменялись). Если за 3 с она
                # не продолжится сама — перезапустим.
                state["reset_at"] = time.time()

        def on_error(job):
            try:
                cancelled = bool(getattr(job, "isCancelled", lambda: False)())
                exc = getattr(job, "getError", lambda: None)()
                res = job.getResult()
                name = getattr(res, "name", None) or (str(res).split(".")[-1] if res is not None else "")
            except Exception:
                cancelled, exc, name = False, None, ""
            if exc is not None:
                Logger.log("e", "STE Assistant: задание нарезки упало: %r", exc)
                state["error"] = "STE Slicer не смог подготовить нарезку: %s" % (str(exc) or type(exc).__name__)
                state["code"] = "JobError"
                ev.set()
            elif cancelled or name in ("", "Finished"):
                # Задание отменил stopSlicing() — нарезку сбросило поздним изменением сцены или настроек.
                Logger.log("w", "STE Assistant: задание нарезки отменено (%s), перезапущу", name or "без результата")
                state["error_at"] = None
                state["reset_at"] = time.time()
                state["seen"] = True
            else:
                state["error"] = START_ERRORS.get(name, "Нарезка не удалась (%s)" % name)
                state["code"] = name
                ev.set()

        def on_duration(build_plate, times, amounts=None):
            try:
                if times:
                    state["times"] = dict((str(k), float(v)) for k, v in dict(times).items() if v == v)
            except Exception:
                pass

        def on_progress(k):
            state["last"] = time.time()
            try:
                state["progress"] = max(state["progress"], float(k)) if state["seen"] else float(k)
            except Exception:
                pass
            if progress:
                try:
                    progress(state["progress"], quiet=0)
                except Exception:
                    pass

        conn = {}

        def connect_and_start():
            backend = self.app.getBackend()
            if conn.get("backend") is not backend:
                disconnect()
                backend.backendStateChange.connect(on_state)
                if hasattr(backend, "backendError"):
                    backend.backendError.connect(on_error)
                backend.processingProgress.connect(on_progress)
                if hasattr(backend, "printDurationMessage"):
                    backend.printDurationMessage.connect(on_duration)
                conn["backend"] = backend
            backend.forceSlice()

        def disconnect():
            backend = conn.pop("backend", None)
            if backend is None:
                return
            sigs = [(backend.backendStateChange, on_state), (backend.processingProgress, on_progress)]
            if hasattr(backend, "printDurationMessage"):
                sigs.append((backend.printDurationMessage, on_duration))
            if hasattr(backend, "backendError"):
                sigs.append((backend.backendError, on_error))
            for sig, fn in sigs:
                try:
                    sig.disconnect(fn)
                except Exception:
                    pass

        start = time.time()
        restarts = 0
        Logger.log("i", "STE Assistant: нарезка, бэкенд %s, предел %d с", type(self._main(self.app.getBackend)).__name__, timeout)
        self._main(connect_and_start)
        try:
            while not ev.wait(0.5):
                now = time.time()
                if should_stop and should_stop():
                    try:
                        self._main(lambda: self.app.getBackend().stopSlicing())
                    except Exception:
                        pass
                    return {"ok": False, "error": "Нарезку остановили", "code": "Stopped"}
                if state["error_at"] and now - state["error_at"] > 1.5:
                    state["error"] = "STE Slicer сообщил об ошибке нарезки без причины"
                    state["code"] = "Error"
                    break
                if state["reset_at"] and now - state["reset_at"] > 3:
                    if restarts >= 3:
                        return {"ok": False, "code": "Reset",
                                "error": "STE Slicer несколько раз сбросил нарезку: сцена или настройки продолжают меняться"}
                    restarts += 1
                    Logger.log("w", "STE Assistant: нарезку сбросило, перезапускаю (%d)", restarts)
                    state.update(seen=False, reset_at=None, error_at=None, last=now)
                    self._settle(should_stop=should_stop)
                    self._main(connect_and_start)
                    continue
                if not state["seen"] and now - state["last"] > 20:
                    if restarts < 3:
                        restarts += 1
                        Logger.log("w", "STE Assistant: нарезка не началась, запускаю ещё раз (%d)", restarts)
                        state["last"] = now
                        self._main(connect_and_start)
                        continue
                    return {"ok": False, "code": "NotStarted",
                            "error": "STE Slicer не начал нарезку: на команду «Нарезать» нет ответа"}
                # Пока бэкенд в состоянии Processing, не прерываем: внешний движок 5D (Glicer)
                # работает минутами без единого сообщения. Прерываем только по общему пределу
                # или если бэкенд молчит, так и не перейдя в Processing.
                if not state["seen"] and now - state["last"] > stall:
                    return {"ok": False, "code": "Stalled",
                            "error": "STE Slicer %d с ничего не сообщает о нарезке" % int(now - state["last"])}
                if progress and now - state.get("beat", 0) >= 2:
                    state["beat"] = now
                    try:
                        progress(state["progress"], quiet=int(now - state["last"]))
                    except Exception:
                        pass
                if now - start > timeout:
                    return {"ok": False, "code": "Timeout",
                            "error": "Нарезка не закончилась за %d мин (дошла до %d %%)" % (
                                timeout // 60, int(state["progress"] * 100))}
        finally:
            self._main(disconnect)
            Logger.log("i", "STE Assistant: нарезка закончена за %.1f с, перезапусков %d, прогресс %d %%, ошибка %s",
                       time.time() - start, restarts, int(state["progress"] * 100), state["code"])
        if state["error"]:
            return {"ok": False, "error": state["error"], "code": state["code"] or "Error"}
        time.sleep(0.3)  # PrintInformation обновляется сообщением бэкенда сразу после Done

        def info():
            pi = self.app.getPrintInformation()
            feats = dict(state["times"] or {})
            secs = sum(feats.values()) if feats else _seconds(pi.currentPrintTime)
            if not feats:
                try:  # запасной путь: ключи уже переведены, поэтому разбивка по элементам будет «Прочее»
                    raw = getattr(pi, "_print_times_per_feature", {})
                    bp = self.app.getMultiBuildPlateModel().activeBuildPlate
                    for k, d in raw.get(bp, {}).items():
                        feats[str(k)] = _seconds(d)
                except Exception:
                    pass
            gcode = None
            try:
                scene = self.app.getController().getScene()
                bp = self.app.getMultiBuildPlateModel().activeBuildPlate
                gd = getattr(scene, "gcode_dict", None)
                if isinstance(gd, dict):
                    gcode = bool(gd.get(bp))
            except Exception:
                gcode = None
            grams = sum(float(x) for x in (pi.materialWeights or []))
            meters = sum(float(x) for x in (pi.materialLengths or []))
            return {"ok": True, "time_s": secs, "features": feats, "grams": grams, "meters": meters, "warnings": [],
                    "gcode": gcode}
        # Оценка времени и расхода может прийти чуть позже Done — ждём до 3 с.
        res = self._main(info)
        for _ in range(10):
            if res["time_s"] > 0 or res["grams"] > 0:
                break
            time.sleep(0.3)
            res = self._main(info)
        return res

    # ------------------------------------------------------------ сохранение
    # Запись 3MF и превью в G-code в STE Slicer помечены @call_on_qt_thread: они сами
    # переходят в главный поток и ждут его. Поэтому писать надо из рабочего потока —
    # вызов из главного потока ждёт сам себя, и слайсер замирает.
    def _write_off_main(self, fn, what, timeout=300.0):
        if threading.current_thread() is threading.main_thread():
            raise RuntimeError("%s нельзя записать из главного потока STE Slicer" % what)
        box = {}

        def run():
            try:
                box["value"] = fn()
            except Exception as e:
                box["error"] = e
        t = threading.Thread(target=run, name="ste-assistant-save", daemon=True)
        t.start()
        t.join(timeout)
        if t.is_alive():
            raise MainThreadTimeout("STE Slicer не записал %s за %d с" % (what, timeout))
        if "error" in box:
            raise box["error"]
        return box.get("value")

    def save_project(self, path):
        def f():
            handler = self.app.getWorkspaceFileHandler()
            writer = handler.getWriter("3MFWriter")
            if writer is None:
                raise RuntimeError("В STE Slicer нет записи проектов 3MF")
            root = self.app.getController().getScene().getRoot()
            tmp = path + ".part"
            with open(tmp, "wb") as stream:
                ok = writer.write(stream, [root], writer.OutputMode.BinaryMode)
            if ok is False:
                _remove(tmp)
                raise RuntimeError(writer.getInformation() or "не удалось записать 3MF")
            os.replace(tmp, path)
            return path
        return self._write_off_main(f, "проект 3MF")

    def save_gcode(self, path):
        def f():
            handler = self.app.getMeshFileHandler()
            writer = handler.getWriterByMimeType("text/x-gcode") or handler.getWriter("GCodeWriter")
            if writer is None:
                raise RuntimeError("В STE Slicer нет записи G-code")
            tmp = path + ".part"
            with open(tmp, "w", encoding="utf-8") as stream:
                ok = writer.write(stream, None)
            if ok is False:
                _remove(tmp)
                raise RuntimeError(writer.getInformation() or "сначала нарежьте модель")
            os.replace(tmp, path)
            return path
        return self._write_off_main(f, "G-code")


def _remove(path):
    try:
        os.remove(path)
    except OSError:
        pass


def _coerce(typ, v):
    try:
        if typ == "bool":
            if isinstance(v, str):
                return v.strip().lower() in ("true", "1", "да", "yes")
            return bool(v)
        if typ == "int":
            return int(round(float(v)))
        if typ == "float":
            return float(v)
    except (TypeError, ValueError):
        pass
    return v


def _seconds(duration):
    if duration is None:
        return 0
    try:
        return int(duration)
    except (TypeError, ValueError):
        pass
    total = 0
    for attr, k in (("days", 86400), ("hours", 3600), ("minutes", 60), ("seconds", 1)):
        total += int(getattr(duration, attr, 0) or 0) * k
    return total
