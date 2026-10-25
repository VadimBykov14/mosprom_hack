# -*- coding: utf-8 -*-
"""Нарезка, итоги, оценка времени, разбор ошибок слайсера и варианты печати."""

import json
import math
import os
import time

from . import materials as M
from . import rules


class _Workflow(object):
    """Остальной сценарий — через workflow при вызове: модули ссылаются друг на друга."""

    def __getattr__(self, name):
        from . import workflow
        return getattr(workflow, name)


W = _Workflow()


# ============================================================ нарезка

FEATURE_NAMES = [("inset_0", "Стенки", "c1"), ("inset_x", "Стенки", "c1"), ("infill", "Заполнение", "c2"),
                 ("skin", "Верх и низ", "c3"), ("support", "Поддержки", "c5"), ("support_infill", "Поддержки", "c5"),
                 ("support_interface", "Поддержки", "c5"), ("skirt", "Юбка", "c4"), ("prime_tower", "Башня", "c4"),
                 ("travel", "Перемещения", "c4"), ("retract", "Перемещения", "c4")]


def cost_of(run, grams):
    try:
        price = float(str(run.app.settings.get("price", 3000) or 0).replace(",", ".").replace(" ", ""))
    except ValueError:
        price = 3000.0
    return grams / 1000.0 * price


SLICE_STAGES = {
    # (порог прогресса слайсера, название этапа)
    "classic": [(0.0, "Подготовка модели"), (0.02, "Нарезка слоёв"), (0.97, "Сборка G-code")],
    "cylindrical": [(0.0, "Подготовка модели"), (0.1, "Движок 5D считает слои"), (0.3, "Сборка G-code")],
    "cylindrical_full": [(0.0, "Подготовка модели"), (0.1, "Основание и нарезка"), (0.5, "Движок 5D считает слои"),
                         (0.6, "Сборка G-code")],
}
DEFAULT_SLICE_SECONDS = {"classic": 40, "cylindrical": 240, "cylindrical_full": 300}


def slice_stage(mode, k):
    stages = SLICE_STAGES.get(mode) or SLICE_STAGES["cylindrical"]
    cur = 0
    for i, (at, _) in enumerate(stages):
        if k >= at:
            cur = i
    steps = [[name, "done" if i < cur else "run" if i == cur else "wait"] for i, (_, name) in enumerate(stages)]
    return stages[cur][1], steps


def _times_path(app):
    return os.path.join(app.store.dir, "slice_times.json")


def _slice_volume(st):
    """Объём, который режет слайсер: деталь × число копий, см³ (0 — неизвестно)."""
    try:
        q = max(1, int(st.get("quantity", 1) or 1))
        on_plate = st.get("copies_made") or min(q, st.get("per_plate") or q)  # режет только то, что на столе
        return float((st.get("geo") or {}).get("volume_cm3") or 0) * max(1, int(on_plate))
    except (TypeError, ValueError):
        return 0.0


def _read_times(app):
    try:
        with open(_times_path(app), "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError, AttributeError):
        return {}


def slice_expectation(app, mode, volume=0.0):
    """(ожидаемая длительность, с; знаем ли её по прошлым нарезкам).

    Время нарезки растёт с объёмом детали, поэтому запоминаем секунды на см³ и
    пересчитываем на текущую деталь. Нет объёма — берём среднее время для режима.
    """
    known = _read_times(app).get(mode)
    if isinstance(known, (int, float)):
        known = {"s": float(known)}
    if isinstance(known, dict):
        rate, secs = known.get("rate"), known.get("s")
        if rate and volume > 0:
            return max(15.0, float(rate) * volume), True
        if secs:
            return float(secs), True
    return float(DEFAULT_SLICE_SECONDS.get(mode, 240)), False


def remember_slice_time(app, mode, seconds, volume=0.0):
    try:
        data = _read_times(app)
        old = data.get(mode)
        if isinstance(old, (int, float)):
            old = {"s": float(old)}
        if not isinstance(old, dict):
            old = {}

        def blend(prev, new):
            return round(new if not prev else prev * 0.6 + new * 0.4, 3)
        cur = {"s": blend(old.get("s"), seconds)}
        if volume > 0:
            cur["rate"] = blend(old.get("rate"), seconds / volume)
        elif old.get("rate"):
            cur["rate"] = old["rate"]
        data[mode] = cur
        with open(_times_path(app), "w", encoding="utf-8") as f:
            json.dump(data, f)
    except (OSError, ValueError, AttributeError, TypeError):
        pass


def time_progress(elapsed, expected):
    """Доля полосы по прошедшему времени. До ¾ ожидаемого времени — равномерно,
    дальше всё медленнее и никогда не доходит до 95 %: если нарезка идёт дольше
    обычного, полоса не упирается в конец задолго до финиша."""
    if not expected or expected <= 0:
        return 0.0
    r = elapsed / float(expected)
    if r <= 0.75:
        return 0.9 * r
    return 0.675 + 0.27 * (1.0 - math.exp(-(r - 0.75) / 0.6))


def check_result(res):
    """Нарезка засчитывается, только если слайсер вернул результат: время, расход и G-code."""
    if not res.get("ok"):
        return res
    if res.get("gcode") is False or (float(res.get("time_s") or 0) <= 0 and float(res.get("grams") or 0) <= 0):
        return {"ok": False, "code": "Empty",
                "error": "STE Slicer сообщил о конце нарезки, но не вернул результат: нет времени печати, расхода или G-code"}
    return res


def totals_from(run, res):
    return {"time": rules.fmt_time(res["time_s"] / 60.0), "mass": "%d г" % int(round(res["grams"])),
            "cost": rules.fmt_money(cost_of(run, res["grams"])), "time_s": res["time_s"], "grams": res["grams"]}


def slice_part(run, card=True):
    st = run.st
    st.pop("scene_stale", None)
    s = run.step("slice", "Нарезал в STE Slicer", {"mode": st.get("mode")})
    b = run.block({"type": "slice", "progress": 5}) if card else None

    started = time.time()
    mode = st.get("mode", "classic")
    volume = _slice_volume(st)
    expected = slice_expectation(run.app, mode, volume)
    shown = [0.0]

    def progress(k, quiet=0):
        if b is None:
            return
        elapsed = time.time() - started
        # Слайсер шлёт прогресс неравномерно (в 5D движок Glicer молчит большую часть времени),
        # поэтому полоса идёт по оценке времени, а настоящий прогресс её обгоняет, когда приходит.
        est = time_progress(elapsed, expected[0])
        shown[0] = max(shown[0], float(k or 0), est)
        b["progress"] = int(round(2 + shown[0] * 96))
        b["estimated"] = est > float(k or 0) + 0.02
        b["elapsed"] = int(elapsed)
        b["eta"] = max(0, int(expected[0] - elapsed)) if expected[1] else None
        b["quiet"] = int(quiet)
        b["mode5d"] = mode != "classic"
        b["stage"], b["steps"] = slice_stage(mode, float(k or 0))
        run.changed()
    progress(0)
    st.pop("totals", None)  # прежние итоги больше не верны, пока не придёт новый результат
    res = check_result(run.bridge.slice(progress=progress, should_stop=lambda: run.stop_flag))
    if not res.get("ok") and res.get("code") == "Stopped":
        run.check_stop()
    if not res.get("ok"):
        error = res.get("error") or "Нарезка не удалась"
        fix = slice_fix(error, res.get("code"), st)
        run.fail(s, error)
        st.pop("totals", None)
        st["slice_error"] = fix
        if b is not None:
            b.update({"progress": 100, "error": error, "fix": fix})
        else:
            run.block({"type": "slice", "progress": 100, "error": error, "fix": fix})
        run.part["status"] = "failed"
        return None
    st.pop("slice_error", None)
    remember_slice_time(run.app, mode, time.time() - started, volume)
    t = totals_from(run, res)
    st["totals"] = t
    run.done(s, "%s · %s · %s" % (t["time"], t["mass"], t["cost"]), "time_s=%d" % res["time_s"])
    if b is not None:
        split = {}
        order = []
        total = sum(res.get("features", {}).values()) or 1.0
        for key, label, cls in FEATURE_NAMES:
            v = res.get("features", {}).get(key)
            if v:
                if label not in split:
                    split[label] = [0.0, cls]
                    order.append(label)
                split[label][0] += v
        b.update({"progress": 100, "time": t["time"], "mass": t["mass"], "cost": t["cost"],
                  "length": "%s м" % W._n(round(res.get("meters", 0), 1)),
                  "split": [[lab, int(round(split[lab][0] / total * 100)), split[lab][1]] for lab in order],
                  "checks": checks(run, res)})
        run.changed()
    run.part["status"] = "sliced"
    return t


def slice_fix(error, code, st):
    """Что случилось и что сделать — для карточки нарезки, паспорта и ответа в чате."""
    low = (error or "").lower()
    five_d = st.get("mode", "classic") != "classic"
    redo = ["Нарезать заново", "Нарежь"]
    if code == "NothingToSlice" or "рабоч" in low or "нечего" in low or "ниже стола" in low:
        why = "Модель не помещается в рабочую зону принтера или лежит не на столе."
        steps = ["Проверьте габарит модели в карточке «Модель»: он должен помещаться в рабочую зону Hybrid 530.",
                 "Если модель в миллиметрах выглядит в 10 или 25 раз больше или меньше — пересохраните её в мм и приложите снова."]
        if five_d:
            steps.append("В 5D ось детали ставится в центр стола: проверьте, что деталь целиком помещается вокруг оси, иначе напечатайте в 3D.")
        if st.get("quantity", 1) > 1:
            steps.append("Копий %d — уменьшите количество, чтобы все поместились на стол." % st["quantity"])
        steps.append("Нажмите «Нарезать заново».")
        actions = ([["Печатать в 3D", "Печатай в 3D"]] if five_d else []) + [redo]
    elif code == "MaterialIncompatible" or "несовместим" in low:
        why = "Выбранный материал не подходит к соплу, которое стоит в принтере в STE Slicer."
        steps = ["В STE Slicer на панели принтера выберите сопло, подходящее для материала (для композитов — закалённое).",
                 "Или попросите другой материал, например «Материал PETG».",
                 "Нажмите «Нарезать заново»."]
        actions = [redo]
    elif code in ("SettingError", "ObjectSettingError") or "настройк" in low:
        why = "В настройках печати есть недопустимое значение."
        steps = ["В STE Slicer откройте настройки печати — ошибочные значения подсвечены красным.",
                 "Если ошибка появилась после изменения в чате — отмените его: «Верни как было».",
                 "Нажмите «Нарезать заново»."]
        actions = [["Верни как было", "Верни как было"], redo]
    elif code == "ObjectsWithDisabledExtruder" or "экструдер" in low:
        why = "Модель привязана к экструдеру, который выключен."
        steps = ["В STE Slicer включите экструдер 1 или назначьте модели включённый экструдер (правая кнопка мыши по модели).",
                 "Нажмите «Нарезать заново»."]
        actions = [redo]
    elif code in ("Timeout", "Stalled", "NotStarted", "Reset") or "не закончилась" in low or "завис" in low:
        engine = "движок 5D (Glicer)" if five_d else "движок нарезки"
        why = {
            "NotStarted": "STE Slicer не ответил на команду «Нарезать» — похоже, слайсер занят или нарезка в нём отключена.",
            "Reset": "STE Slicer сбрасывал нарезку, потому что сцена или настройки продолжали меняться.",
            "Stalled": "STE Slicer принял команду, но так и не начал нарезку.",
        }.get(code, "Нарезка идёт дольше 20 минут: модель очень детальная или %s завис." % engine)
        steps = ["Проверьте STE Slicer: не открыт ли там диалог, нет ли красных ошибок в настройках.",
                 "Нажмите в самом STE Slicer кнопку «Нарезать». Если и там нарезка не идёт — дело не в ассистенте, перезапустите STE Slicer."]
        if five_d:
            steps.append("Если в 5D нарезка не идёт и после перезапуска — напечатайте в 3D: «Печатай в 3D».")
        steps.append("Для детальной модели попробуйте слой 0,2 мм — «Сделай быстрее».")
        steps.append("Нажмите «Нарезать заново».")
        actions = ([["Печатать в 3D", "Печатай в 3D"]] if five_d else []) + [["Сделай быстрее", "Сделай быстрее"], redo]
    elif code in ("Empty", "NoSlicer"):
        why = ("STE Slicer не отвечает — команды плагина до него не доходят." if code == "NoSlicer" else
               "Слайсер не подтвердил, что нарезка выполнена, поэтому итоги не показываю.")
        steps = ["Переключитесь в окно STE Slicer: не открыт ли там диалог, не завис ли он.",
                 "Нажмите в STE Slicer кнопку «Нарезать» и дождитесь времени печати внизу справа.",
                 "Вернитесь в чат и нажмите «Нарезать заново»."]
        if code == "NoSlicer":
            steps.append("Если слайсер завис — перезапустите его: чат сохранится.")
        actions = [redo]
    elif code == "JobError":
        why = "Внутри STE Slicer упало задание подготовки нарезки — текст ошибки выше."
        steps = ["Нажмите «Нарезать заново»: часто это разовый сбой."]
        if five_d:
            steps.append("Если повторяется — нарежьте в самом STE Slicer кнопкой «Нарезать». Если и там ошибка, дело в движке 5D: напечатайте в 3D — «Печатай в 3D».")
        else:
            steps.append("Если повторяется — нарежьте в самом STE Slicer кнопкой «Нарезать» и посмотрите его сообщение.")
        steps.append("Пришлите разработчикам steslicer.log (Help → Show Configuration Folder) — там полный текст ошибки.")
        actions = ([["Печатать в 3D", "Печатай в 3D"]] if five_d else []) + [redo]
    else:
        why = "STE Slicer сообщил об ошибке нарезки."
        steps = ["Посмотрите сообщение в окне STE Slicer — там точная причина.",
                 "Если ошибка появилась после изменения в чате — отмените его: «Верни как было».",
                 "Нажмите «Нарезать заново». Если ошибка повторяется — перезапустите STE Slicer, чат сохранится."]
        actions = [["Верни как было", "Верни как было"], redo]
    return {"error": error, "code": code or "", "why": why, "steps": steps, "actions": actions}


def checks(run, res):
    st = run.st
    m = M.by_id(st.get("material_id") or "")
    s = run.bridge.get_settings(["printing_mode", "support_enable", "material_print_temperature"])
    out = [["ok" if s.get("printing_mode") == st.get("mode") else "bad", "Режим", s.get("printing_mode")],
           ["ok", "Материал", m["name"] if m else "—"],
           ["ok", "Поддержки", "включены" if s.get("support_enable") else "выключены"],
           ["ok", "Модель в рабочей зоне", "да"]]
    for w in res.get("warnings", []):
        out.append(["warn", "Предупреждение слайсера", w])
    return out


# ============================================================ варианты печати

def variants(run):
    st = run.st
    if not st.get("rows"):
        raise ValueError("Сначала подготовьте деталь")
    b = run.block({"type": "variants", "progress": 0, "of": 3})
    saved_variant = st.get("variant", "quality")
    out = {}
    order = [v for v in ("fast", "cheap", "quality") if v != saved_variant] + [saved_variant]
    errors = {}
    for i, v in enumerate(order):
        run.check_stop()
        b["progress"] = i + 1
        run.changed()
        W.apply_settings(run, v, title="Пробный вариант «%s»" % rules.VARIANTS[v]["title"])
        s = run.step("trial_slice", "Пробная нарезка «%s»" % rules.VARIANTS[v]["title"])
        st.pop("totals", None)
        res = check_result(run.bridge.slice(should_stop=lambda: run.stop_flag))
        if not res.get("ok") and res.get("code") == "Stopped":
            run.check_stop()
        if not res.get("ok"):
            run.fail(s, res.get("error", "Нарезка не удалась"))
            errors[v] = res
            continue
        t = totals_from(run, res)
        layer = [r for r in st["rows"] if r["key"] == "layer_height"][0]["v"]
        out[v] = {"layer": layer, "time": t["time"], "mass": t["mass"], "cost": t["cost"], "time_s": t["time_s"],
                  "grams": t["grams"]}
        run.done(s, "%s · %s" % (t["time"], t["cost"]))
    # Текущий вариант резали последним — слайсер сейчас в его настройках, итоги настоящие.
    st["variants"] = out
    if saved_variant in out:
        st["totals"] = dict((k, out[saved_variant][k]) for k in ("time", "mass", "cost", "time_s", "grams"))
        st.pop("slice_error", None)
    else:
        err = errors.get(saved_variant, {})
        st["slice_error"] = slice_fix(err.get("error") or "Нарезка не удалась", err.get("code"), st)
    m = M.by_id(st["material_id"])
    b.update({"progress": None, "variants": out, "current": saved_variant, "locks": rules.locks(st["rows"], m)})
    run.changed()
    return out
