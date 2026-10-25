# -*- coding: utf-8 -*-
"""Агент: ведёт диалог по детали, вызывает инструменты и объясняет решения.

С LLM: OpenAI-совместимый Chat Completions с tool calling, не больше 15 вызовов на запрос.
Без LLM (нет ключа или сеть недоступна) работает автопилот на тех же правилах:
подготовка детали и простые команды («дешевле», «варианты», «нарежь», «сохрани»).
"""
import json
import re
import threading
import time
import traceback

from . import materials as M
from . import plan as P
from . import requirements as R
from . import rules
from . import tools as T
from . import workflow as W
from . import images as IMG
from . import review as RV
from .llm import LLMClient, LLMError

MAX_TOOL_CALLS = 15
HISTORY_LIMIT = 24

SYSTEM = """Ты — ассистент печати STE Assistant внутри STE Slicer (Stereotech). Помогаешь инженеру напечатать деталь на 3D/5D-принтере Stereotech Hybrid 530: подбираешь материал по ТЗ, режим 3D или 5D, положение, настройки, нарезаешь и объясняешь каждое решение.

Как работать:
- Порядок работы: файлы → вопросы об условиях работы → вопросы «как сделать» (материал, 3D/5D, положение, поддержки — с рекомендацией) → план печати → пользователь подтверждает → нарезка. Никогда не режь до подтверждения плана. Не решай за пользователя молча: если есть выбор, спроси, как сделать, и предложи рекомендацию.
- Для новой детали с приложенными файлами вызови prepare_part один раз. Он загрузит модель, прочитает ТЗ, сам задаст вопросы карточками и покажет план печати. Если пользователь назвал материал, режим, вариант (быстро/дёшево/качественно), количество, прочность, поверхность или поддержки — передай это в prepare_part, этого не спросят.
- Про материал говори простыми словами: что это за пластик, на что похож, чего боится (есть в material_plain).
- Если ТЗ нет, но в словах или на фото есть условия работы (нагрузка, среда, температура, химия), передай их в prepare_part как requirements — тогда лишних вопросов не будет.
- Пока план не подтверждён, правки (материал, режим, вариант, количество, настройки, требования) меняют план — вызывай те же инструменты, они пересоберут карточку. Когда пользователь подтвердил («да», «подтверждаю», «нарезай») — вызови confirm_plan.
- Свои вопросы через ask_user — после плана, если пользователь просит что-то, где есть несколько разумных способов. Первым вариантом — рекомендованный. Если ответа нет — делай рекомендованное и называй это допущением.
- Фото узла приходит в сообщении пользователя как изображение или как текст «Что видно на фото». Опирайся на него: назови, что увидел, и передай условия работы в requirements.
- Порядок источников без ТЗ: слова пользователя → фото → имя файла → геометрия → картотека → значения по умолчанию.
- Материал, режим и значения настроек выбирают правила и слайсер — не придумывай их сам и не называй цифры, которых нет в результатах инструментов.
- Для просьб «дешевле», «быстрее», «качественнее» используй show_variants и apply_variant; для конкретных настроек — set_settings с причиной.
- Если нарезка не удалась (в результате есть slice_error), не перечисляй материал, режим и настройки: назови причину и шаги из slice_error.steps своими словами, коротко.
- Если пользователь назвал материал, который нарушает ТЗ, предупреди, но сделай как он сказал.

Как отвечать (после вызовов инструментов):
- По-русски, на «вы», коротко: 2–4 абзаца. Первым — что сделано и главное решение.
- Решения выделяй **жирным** и объясняй причиной из ТЗ или геометрии с пунктом ТЗ, если он есть.
- Назови допущения отдельно: что предположено и где это поменять (паспорт справа).
- Не пересказывай таблицы — они уже показаны карточками. Не используй эмодзи и заголовки.
- После apply_variant не пиши «Применил вариант …»: карточка «Было → стало» уже это показывает.
- Числа с единицами и запятой: 0,15 мм, 220 °C, 56 г, Ø16."""


class Agent(object):
    def __init__(self, app):
        self.app = app
        self.runs = {}
        self.lock = threading.Lock()

    # ------------------------------------------------------------- внешние входы
    def busy(self, pid):
        return pid in self.runs

    def stop(self, pid):
        run = self.runs.get(pid)
        if run:
            run.stop_flag = True

    def _check_free(self, part):
        # Проверяем до того, как что-то добавить в переписку: иначе сообщение останется без ответа.
        if self.busy(part["id"]):
            raise RuntimeError("Ассистент ещё работает над этой деталью")

    def send(self, part, text, file_ids, comment="", mode=""):
        self._check_free(part)
        files = []
        for fid in file_ids[:10]:
            fi = self.app.store.file(fid)
            if fi:
                pub = dict((k, fi[k]) for k in ("id", "name", "size", "kind"))
                files.append(pub)
                part["files"].append(pub)
        msg = {"role": "user", "text": text, "time": time.strftime("%H:%M"),
               "files": [dict(id=f["id"], name=f["name"], size=f["size"]) for f in files]}
        if comment:
            msg["comment"] = comment
        part["messages"].append(msg)
        self.app.store.changed(part)
        # Комментарий к запросу — часть задачи: его разбирают и правила (автопилот), и LLM.
        full = with_comment(text, comment)
        if mode == "review" or RV.is_review(part, text, files):
            self._start(part, lambda run: RV.start(run, self._vision_factory, full, files))
            return
        self._start(part, lambda run: self._turn(run, full, files), any(f["kind"] == "model" for f in files))

    def answer(self, part, qid, value):
        block = None
        for m in part["messages"]:
            for b in m.get("blocks", []) or []:
                if b.get("type") in ("ask", "questions") and b.get("id") == qid:
                    block = b
        if not block:
            return
        if block["type"] == "questions":
            if block.get("answers") is not None:
                return
            self._check_free(part)
            answers = value if isinstance(value, dict) else {}
            answers = dict((str(k), str(v)) for k, v in answers.items() if v)
            P.close_questions_ui(block, answers)
            part["messages"].append({"role": "user", "text": P.answers_text(block, answers), "files": [],
                                     "time": time.strftime("%H:%M")})
            self.app.store.changed(part)
            self._start(part, lambda run: self._after_questions(run, block, answers))
            return
        if block.get("answer"):
            return
        self._check_free(part)
        block["answer"] = value
        part["messages"].append({"role": "user", "text": value, "files": [], "time": time.strftime("%H:%M")})
        part["state"].pop("pending_question", None)
        self.app.store.changed(part)
        self._start(part, lambda run: self._after_answer(run, value))

    def confirm(self, part):
        """«Подтвердить и нарезать» из карточки плана."""
        self.action(part, self._confirm, "Подтвердить и нарезать")

    def _confirm(self, run):
        P.confirm(run)
        self._explain(run)  # при ошибке нарезки — причина и шаги, без параметров печати

    def _after_questions(self, run, block, answers):
        P.close_questions(run, block, answers)
        W.prepare(run, {})  # следующий раунд вопросов или план

    def action(self, part, fn, label=None):
        """Действие из интерфейса (кнопка паспорта, «Отменить», вариант) — тоже ход ассистента."""
        self._check_free(part)
        if label:
            part["messages"].append({"role": "user", "text": label, "files": [], "time": time.strftime("%H:%M")})
        self._start(part, fn)

    def mcp_call(self, part, name, args):
        """Вызов инструмента внешним MCP-клиентом: синхронно, с пометкой «внешний клиент»."""
        with self.lock:
            if part["id"] in self.runs:
                raise RuntimeError("Ассистент сейчас работает над этой деталью — повторите вызов позже")
            run = W.Run(self.app, part, external=True)
            self.runs[part["id"]] = run
        try:
            W.ensure_scene(run)
            return T.execute(run, name, args)
        except T.AskPending:
            return {"asked": True}
        finally:
            run.finish()
            self._after(part)
            with self.lock:
                self.runs.pop(part["id"], None)

    # ------------------------------------------------------------- поток
    def _start(self, part, fn, new_model=False):
        with self.lock:
            if part["id"] in self.runs:
                raise RuntimeError("Ассистент ещё работает над этой деталью")
            run = W.Run(self.app, part)
            run.new_model = new_model
            self.runs[part["id"]] = run
        part["status"] = "work"
        self.app.store.events.emit({"type": "running", "id": part["id"], "on": True})
        self.app.store.changed(part)
        th = threading.Thread(target=self._worker, args=(part, run, fn), name="ste-agent")
        th.daemon = True
        th.start()

    def _worker(self, part, run, fn):
        try:
            if not getattr(run, "new_model", False):
                W.ensure_scene(run)  # модель этой детали могла смениться моделью другой
            fn(run)
        except W.Stopped:
            run.text("Остановился. Сделанные изменения остались — их можно отменить в карточках «Было → стало».")
        except T.AskPending:
            pass
        except Exception as e:
            self.app.log("e", "Ошибка агента: %s\n%s" % (e, traceback.format_exc()))
            st = part.setdefault("state", {})
            if type(e).__name__ == "MainThreadTimeout" and st.get("node"):
                # Слайсер не ответил посреди работы: что в нём сейчас — неизвестно, прежние итоги не верны.
                st.pop("totals", None)
                st["slice_error"] = W.slice_fix(str(e), "NoSlicer", st)
                run.block({"type": "slice", "progress": 100, "error": str(e), "fix": st["slice_error"]})
            else:
                run.notice("bad", "Не получилось", str(e))
        finally:
            run.finish()
            self._after(part)
            with self.lock:
                self.runs.pop(part["id"], None)
            self.app.store.events.emit({"type": "running", "id": part["id"], "on": False})

    def _after(self, part):
        st = part.get("state") or {}
        if part.get("status") == "work":
            if st.get("pending_question"):
                part["status"] = "ask"
            elif any(m.get("blocks") and any(b.get("type") == "saved" for b in m["blocks"]) for m in part["messages"][-1:]):
                part["status"] = "saved"
            else:
                part["status"] = "failed" if st.get("slice_error") else "sliced" if st.get("totals") else "draft"
        if st.get("pending_question"):
            part["status"] = "ask"
        W.build_passport(part, self.app)
        part["suggest"] = W.suggestions(part)
        self.app.store.changed(part)

    # ------------------------------------------------------------- ход
    def _vision_factory(self):
        """Клиент, который смотрит фото: модель для фото, иначе основная. (None, "") — LLM нет."""
        llm = self._llm()
        if llm is None or not self.app.settings.get("images", True):
            return None, ""
        vision = (self.app.settings.get("vision_model") or "").strip()
        if vision and vision != llm.model:
            return LLMClient(llm.url, vision, llm.key, timeout=llm.timeout), vision
        return llm, llm.model

    def _llm(self):
        s = self.app.settings
        key = self.app.llm_key()
        if not key:
            return None
        return LLMClient(s.get("url"), s.get("model"), key, timeout=int(s.get("timeout", 120)))

    def _turn(self, run, text, files):
        st = run.st
        new_model = any(f["kind"] == "model" for f in files)
        q = P.pending_questions(run.part)
        if q is not None and not new_model and text:
            # Ответ своими словами вместо кнопок — закрывает вопросы, дальше план.
            P.close_questions(run, q, {"text": text})
            W.prepare(run, {})
            return
        if P.in_plan(run.part) and not files and P.is_confirm(text):
            return self._confirm(run)
        if st.get("phase") == "questions" and not new_model and not q:
            st["phase"] = None
        llm = self._llm()
        if llm is None:
            return self._autopilot(run, text, files)
        try:
            self._llm_turn(run, llm, text, files)
        except LLMError as e:
            self.app.set_llm_status(False, str(e))
            run.notice("bad", "Нет подключения к LLM", "%s. Работаю по правилам без LLM." % e)
            self._autopilot(run, text, files)

    def _after_answer(self, run, value):
        st = run.st
        if value == "Реши сам":
            st["asked"] = 2
            st["material_src_hint"] = "assume"
        else:
            reqs, known = W.answer_to_requirements(value)
            st["requirements"] = R.merge(st.get("requirements", []), reqs)
            if not reqs:
                st["asked"] = 2
        if not st.get("requirements"):
            st["asked"] = 2
        W.prepare(run, {})

    def _explain(self, run, user_text=None):
        """Итоговый текст: от LLM, если она есть, иначе по шаблону."""
        llm = self._llm()
        text = None
        if llm is not None:
            try:
                msgs = [{"role": "system", "content": SYSTEM},
                        {"role": "user", "content": "Коротко объясни пользователю, что сделано и почему, по состоянию детали:\n"
                                                    + T.compact(T.state_summary(run.part), 5000)
                                                    + ("\nВопрос пользователя: " + user_text if user_text else "")}]
                text = (llm.chat(msgs, max_tokens=700).get("content") or "").strip()
            except LLMError as e:
                self.app.set_llm_status(False, str(e))
        if not text:
            text = W.summary_text(run.part)
        if text:
            run.text(text)

    # ------------------------------------------------------------- без LLM
    def _autopilot(self, run, text, files):
        st = run.st
        words = W.parse_words(text)
        low = (text or "").lower()
        new_model = any(f["kind"] == "model" for f in files) and (not st.get("node") or st.get("model_file") not in [f["id"] for f in files])
        if new_model and st.get("node"):
            run.part["state"] = {}
            st = run.st
        if not st.get("node") or new_model or (any(f["kind"] == "spec" for f in files) and not st.get("spec_file")):
            if any(f["kind"] == "spec" for f in files) and st.get("node"):
                # Новое ТЗ к уже загруженной модели — план пересобирается и снова ждёт подтверждения.
                W.read_spec(run, [f for f in files if f["kind"] == "spec"][-1]["id"])
                st["phase"] = None
                st.pop("plan_id", None)
                P.build(run)
                return
            res = W.prepare(run, words)
            if res in ("asked", "plan"):
                return
            run.text(W.summary_text(run.part))
            return
        if re.search(r"почему|зачем|объясн", low):
            return self._why(run, low)
        if P.in_plan(run.part):
            return self._plan_words(run, text, words, low)
        if re.search(r"верни|отмен", low):
            for m in reversed(run.part["messages"]):
                for b in m.get("blocks", []) or []:
                    if b.get("type") == "diff" and not b.get("undone"):
                        W.undo(run, b["id"])
                        run.text("Вернул как было.")
                        return
            run.text("Отменять нечего.")
            return
        if re.search(r"вариант", low):
            W.variants(run)
            run.text("Посчитал три варианта пробной нарезкой. То, что задаёт ТЗ, зафиксировано во всех трёх. Выберите карточку — применю и покажу, что изменилось.")
            return
        if words.get("variant"):
            W.set_variant(run, words["variant"])
            return
        if re.search(r"нареж|нарез|слайс", low):
            t = W.slice_part(run)
            if t:
                run.text("Нарезано: **%s**, %s, %s." % (t["time"], t["mass"], t["cost"]))
            return
        if re.search(r"сохран|экспорт|g-?code|3mf", low):
            self.app.store.events.emit({"type": "open", "what": "save", "id": run.part["id"]})
            run.text("Открыл окно сохранения. Имя файла собрал по шаблону из настроек.")
            return
        if words.get("material"):
            st["fixed_material"] = words["material"]
            before = W.snapshot(run)
            old = (M.by_id(st.get("material_id") or "") or {"name": "—"})["name"]
            W.select_material(run, fixed=words["material"])
            W.apply_settings(run)
            if before["state"].get("totals"):
                W.slice_part(run, card=False)
            W.diff_block(run, before, [["Материал", old, M.by_id(words["material"])["name"], "Вы попросили"]], "Сменил материал")
            return
        if words.get("mode"):
            T.execute(run, "set_printing_mode", {"mode": words["mode"]})
            run.text(st.get("mode_reason", ""))
            return
        if words.get("quantity") and words["quantity"] > 1:
            W.multiply(run, words["quantity"])
            run.text("Сделал %d копий и расставил их на столе." % words["quantity"])
            return
        if words.get("requirements"):
            st["requirements"] = R.merge(st.get("requirements", []), words["requirements"])
            W.show_requirements(run)
            W.select_material(run)
            W.apply_settings(run)
            run.text(W.summary_text(run.part))
            return
        run.text("Без LLM я понимаю простые команды: «сделай дешевле», «покажи варианты печати», «нарежь», «сохрани проект», "
                 "«печатай в 5D», «материал PETG», «15 штук». Подключите LLM в настройках — тогда можно писать свободно.")

    def _why(self, run, low):
        """Ответ на вопрос «почему …» без LLM — по решениям правил, ничего не меняя."""
        st = run.st
        m = M.by_id(st.get("material_id") or "")
        if "5d" in low or "3d" in low or "режим" in low:
            run.text("**%s.** %s" % (rules.MODE_TITLE.get(st.get("mode", "classic")), st.get("mode_reason", "")))
        elif "положен" in low or "ориент" in low or "лежит" in low:
            run.text("Положение «%s» выбрал по нависаниям и опоре на столе: меньше поддержек, рабочие поверхности — "
                     "вертикальными стенками." % (st.get("orientation") or "—"))
        elif m and (W.parse_words(low).get("material") or "материал" in low or "пластик" in low):
            asked = M.by_id(W.parse_words(low).get("material") or "")
            lines = ["**%s** — %s %s" % (m["name"], m.get("plain", ""), st.get("material_reason", ""))]
            if asked and asked["id"] != m["id"]:
                sc = rules.score(asked, st.get("requirements", []))
                lines.append("**%s** — %s %s" % (asked["name"], asked.get("plain", ""),
                             ("Не проходит: " + "; ".join(r["text"].lower() for r in sc["violations"]) + ".") if sc["violations"]
                             else "Тоже проходит требования; поставить его — напишите «материал %s»." % asked["abbr"]))
            run.text("\n\n".join(lines))
        else:
            run.text(W.summary_text(run.part))

    def _plan_words(self, run, text, words, low):
        """Без LLM, пока план не подтверждён: слова меняют план, «нарежь» — подтверждает."""
        st = run.st
        if re.search(r"нареж|нарез|слайс|подтвер", low) and not re.search(r"\bне\s+(нареж|нарез|подтвер)|пока не|подожди", low):
            return self._confirm(run)
        changed = False
        if words.get("material"):
            st["fixed_material"] = words["material"]
            changed = True
        if words.get("mode"):
            st["forced_mode"] = words["mode"]
            st["orientation"] = None
            changed = True
        if words.get("variant"):
            st["variant"], st["variant_src"] = words["variant"], "words"
            changed = True
        if words.get("quantity"):
            st["quantity"] = words["quantity"]
            changed = True
        if words.get("no_support"):
            st.setdefault("overrides", {})["support_enable"] = False
            changed = True
        if words.get("requirements"):
            st["requirements"] = R.merge(st.get("requirements", []), words["requirements"])
            changed = True
        if changed:
            P.build(run)
            return
        run.text("План ещё не подтверждён. Поменяйте строку в плане или напишите, что изменить: «материал PETG», "
                 "«печатай в 3D», «дешевле», «5 штук». Когда всё верно — «Подтвердить и нарезать».")

    # ------------------------------------------------------------- с LLM
    def _user_content(self, text, files):
        lines = [text or "(без текста)"]
        if files:
            lines.append("Вложения: " + ", ".join("%s (%s, id %s)" % (f["name"], {"model": "модель", "spec": "ТЗ", "image": "фото"}.get(f["kind"], f["kind"]), f["id"]) for f in files))
        return "\n".join(lines)

    def _photos(self, run, llm, text, files):
        """Фото этого сообщения: data-URL для основной модели или текстовое описание от модели для фото.

        Возвращает (urls, описание). В историю диалога фото не кладём — только описание.
        """
        images = [f for f in files if f["kind"] == "image"][:3]
        if not images:
            return [], ""
        if not self.app.settings.get("images", True):
            run.notice("info", "Фото не отправлено в LLM", "отправка фото выключена в настройках «Печать».")
            return [], ""
        urls, skipped = [], []
        for f in images:
            fi = self.app.store.file(f["id"])
            url = IMG.data_url(fi["path"], fi["name"]) if fi else None
            if url:
                urls.append(url)
            else:
                skipped.append(f["name"])
        if skipped:
            run.notice("warn", "Фото не отправлено", "%s: не удалось прочитать или файл больше 4 МБ. Уменьшите фото и приложите снова." % ", ".join(skipped))
        vision = (self.app.settings.get("vision_model") or "").strip()
        if not urls or not vision or vision == llm.model:
            return urls, ""
        try:
            vc = LLMClient(llm.url, vision, llm.key, timeout=llm.timeout)
            desc = vc.describe_images(urls, text)
        except LLMError as e:
            run.notice("warn", "Модель для фото не ответила", "%s. Отправляю фото основной модели." % e)
            return urls, ""
        if not desc:
            return urls, ""
        run.notice("info", "Фото посмотрела %s" % vision, desc[:300] + ("…" if len(desc) > 300 else ""))
        return [], desc

    def _llm_turn(self, run, llm, text, files):
        part = run.part
        hist = part.setdefault("llm", [])
        state = "Состояние детали сейчас:\n" + T.compact(T.state_summary(part), 4000)
        urls, desc = self._photos(run, llm, text, files)
        content = self._user_content(text, files)
        if desc:
            content += "\n\nЧто видно на фото (описание модели для фото):\n" + desc
        hist.append({"role": "user", "content": content})
        turn_start = len(hist) - 1
        tools = T.llm_tools()
        calls = 0
        prepared_before = bool(run.st.get("node"))
        while True:
            run.check_stop()
            msgs = [{"role": "system", "content": SYSTEM + "\n\n" + state}] + _with_photos(hist, turn_start, urls)
            try:
                msg = llm.chat(msgs, tools=tools)
            except LLMError as e:
                if not urls or e.status not in (400, 413, 415, 422):
                    raise
                run.notice("warn", "Модель не приняла фото", "%s. Продолжаю без фото; укажите модель для фото в настройках." % e)
                urls = []
                msgs = [{"role": "system", "content": SYSTEM + "\n\n" + state}] + _trim(hist)
                msg = llm.chat(msgs, tools=tools)
            tcalls = msg.get("tool_calls") or []
            entry = {"role": "assistant", "content": msg.get("content") or ""}
            if tcalls:
                entry["tool_calls"] = tcalls
            hist.append(entry)
            if not tcalls:
                content = (msg.get("content") or "").strip()
                needs_prepare = any(f["kind"] == "model" for f in files) and not run.st.get("node")
                if needs_prepare and not prepared_before:
                    # Модель не загрузила подготовку — делаем её сами, чтобы деталь не осталась ненастроенной.
                    res = W.prepare(run, W.parse_words(text))
                    if res in ("asked", "plan"):
                        return
                    content = W.summary_text(part)
                if content:
                    run.text(_clean(content))
                break
            for tc in tcalls:
                calls += 1
                fn = tc.get("function", {})
                name = fn.get("name")
                try:
                    args = json.loads(fn.get("arguments") or "{}")
                except ValueError:
                    args = {}
                if calls > MAX_TOOL_CALLS:
                    result = {"error": "Лимит 15 вызовов на запрос исчерпан. Ответь пользователю, что успел."}
                else:
                    try:
                        result = T.execute(run, name, args)
                    except T.AskPending:
                        hist.append({"role": "tool", "tool_call_id": tc.get("id"), "content": '{"asked": true}'})
                        part["llm"] = hist[-HISTORY_LIMIT:]
                        return
                    except W.Stopped:
                        raise
                    except Exception as e:
                        result = {"error": str(e)}
                hist.append({"role": "tool", "tool_call_id": tc.get("id"), "content": T.compact(result)})
            state = "Состояние детали сейчас:\n" + T.compact(T.state_summary(part), 4000)
            if calls > MAX_TOOL_CALLS + 1:
                run.text("Остановился после 15 шагов. Сделанное видно в ходе работы; напишите, что продолжить.")
                break
        part["llm"] = hist[-HISTORY_LIMIT:]


def with_comment(text, comment):
    if not comment:
        return text
    return ((text + "\n\n") if text else "") + "Комментарий к запросу и файлам: " + comment


def _with_photos(hist, turn_start, urls):
    """История для запроса; к сообщению пользователя этого хода добавляем фото."""
    h = _trim(hist)
    if not urls:
        return h
    target = hist[turn_start] if 0 <= turn_start < len(hist) else None
    out = []
    for m in h:
        if m is target:
            m = dict(m)
            m["content"] = [{"type": "text", "text": m["content"]}] + [
                {"type": "image_url", "image_url": {"url": u}} for u in urls]
        out.append(m)
    return out


def _trim(hist):
    """Последние сообщения для LLM; историю не начинаем с ответа инструмента."""
    h = hist[-HISTORY_LIMIT:]
    while h and h[0]["role"] == "tool":
        h = h[1:]
    return h


def _clean(text):
    text = re.sub(r"^#+\s*", "", text, flags=re.M)
    return text.strip()
