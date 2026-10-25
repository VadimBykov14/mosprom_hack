# -*- coding: utf-8 -*-
"""Локальный HTTP-сервер плагина: страница чата, /api/* для чата и /mcp для MCP.

Слушает только 127.0.0.1. Запросы /api принимаются только с токеном сессии
(заголовок X-Session-Token), /mcp — с токеном MCP. Заголовок Host проверяется,
чтобы чужая страница не достучалась до сервера через подмену DNS.
Потоковые события — длинный опрос GET /api/events?since=N.
"""
import hmac
import json
import mimetypes
import os
import socket
import threading
import time
import traceback
from http.server import BaseHTTPRequestHandler, HTTPServer
from socketserver import ThreadingMixIn
from urllib.parse import parse_qs, unquote, urlparse

from . import mcp
from . import workflow as W

WEB_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "web")
MAX_BODY = 110 * 1024 * 1024
STATIC_TYPES = {".js": "application/javascript; charset=utf-8", ".css": "text/css; charset=utf-8",
                ".html": "text/html; charset=utf-8", ".svg": "image/svg+xml", ".woff2": "font/woff2",
                ".png": "image/png", ".ico": "image/x-icon", ".json": "application/json"}


class HttpError(Exception):
    def __init__(self, code, message):
        Exception.__init__(self, message)
        self.code = code


class _Server(ThreadingMixIn, HTTPServer):
    daemon_threads = True
    allow_reuse_address = False


def find_port(preferred=51734):
    for port in [preferred] + list(range(preferred + 1, preferred + 40)):
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            s.bind(("127.0.0.1", port))
            s.close()
            return port
        except OSError:
            s.close()
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


class ChatServer(object):
    def __init__(self, app, port=None):
        self.app = app
        self.port = port or find_port()
        app.port = self.port
        handler = _make_handler(app)
        self.httpd = _Server(("127.0.0.1", self.port), handler)
        self.thread = None

    @property
    def url(self):
        return "http://localhost:%d/?t=%s" % (self.port, self.app.token)

    def start(self):
        self.thread = threading.Thread(target=self.httpd.serve_forever, name="ste-assistant-http")
        self.thread.daemon = True
        self.thread.start()
        return self.url

    def stop(self):
        try:
            self.httpd.shutdown()
            self.httpd.server_close()
        except Exception:
            pass


def _make_handler(app):
    class Handler(BaseHTTPRequestHandler):
        server_version = "STEAssistant/1.0"
        protocol_version = "HTTP/1.1"

        def log_message(self, fmt, *args):  # не засоряем журнал слайсера каждым запросом
            pass

        # ------------------------------------------------------ ответы
        def _send(self, code, body, ctype="application/json; charset=utf-8", extra=None):
            if not isinstance(body, bytes):
                body = json.dumps(body, ensure_ascii=False, default=str).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            for k, v in (extra or {}).items():
                self.send_header(k, v)
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)

        def _length(self, limit):
            try:
                n = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                raise HttpError(400, "Неверная длина запроса")
            if n < 0:
                raise HttpError(400, "Неверная длина запроса")
            if n > limit:
                raise HttpError(413, "Слишком большой запрос")
            return n

        def _json(self):
            n = self._length(2 * 1024 * 1024)
            raw = self.rfile.read(n) if n else b""
            if not raw:
                return {}
            try:
                data = json.loads(raw.decode("utf-8"))
            except ValueError:
                raise HttpError(400, "Тело запроса — не JSON")
            if not isinstance(data, dict):
                raise HttpError(400, "Тело запроса должно быть объектом JSON")
            return data

        def _host_ok(self):
            host = (self.headers.get("Host") or "").lower()
            return host in ("localhost:%d" % app.port, "127.0.0.1:%d" % app.port)

        def _auth_chat(self):
            tok = self.headers.get("X-Session-Token") or ""
            if not _same(tok, app.token):
                raise HttpError(403, "Нет доступа: откройте чат из меню «AI Assistant» в STE Slicer")

        # ------------------------------------------------------ маршруты
        def do_GET(self):
            self._route("GET")

        def do_HEAD(self):
            self._route("GET")

        def do_POST(self):
            self._route("POST")

        def do_PUT(self):
            self._route("PUT")

        def do_PATCH(self):
            self._route("PATCH")

        def do_DELETE(self):
            self._route("DELETE")

        def _route(self, method):
            try:
                if not self._host_ok():
                    raise HttpError(403, "Неверный адрес сервера")
                u = urlparse(self.path)
                path = u.path
                q = parse_qs(u.query)
                if path == "/mcp":
                    return self._mcp(method)
                if path.startswith("/api/"):
                    self._auth_chat()
                    return self._api(method, path[5:], q)
                if method != "GET":
                    raise HttpError(405, "Метод не поддерживается")
                return self._static(path)
            except HttpError as e:
                self._send(e.code, {"error": str(e)})
            except (ValueError, KeyError) as e:
                self._send(400, {"error": str(e)})
            except RuntimeError as e:
                self._send(409, {"error": str(e)})
            except (BrokenPipeError, ConnectionResetError):
                pass
            except Exception as e:
                app.log("e", "Ошибка сервера: %s\n%s" % (e, traceback.format_exc()))
                try:
                    self._send(500, {"error": "Внутренняя ошибка плагина: %s" % e})
                except Exception:
                    pass

        def _static(self, path):
            if path in ("/", "/index.html"):
                with open(os.path.join(WEB_DIR, "index.html"), "rb") as f:
                    body = b"<!doctype html>\n" + f.read()
                return self._send(200, body, "text/html; charset=utf-8",
                                  {"Content-Security-Policy": "default-src 'self'; img-src 'self' data: blob:; "
                                   "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; font-src 'self' https://fonts.gstatic.com; "
                                   "connect-src 'self'; frame-ancestors 'none'"})
            # Только файлы внутри web/: realpath + commonpath отсекают «..», абсолютные пути и диски Windows (C:файл).
            full = os.path.realpath(os.path.join(WEB_DIR, unquote(path).lstrip("/\\")))
            root = os.path.realpath(WEB_DIR)
            try:
                inside = os.path.commonpath([root, full]) == root
            except ValueError:  # разные диски в Windows
                inside = False
            if not inside or not os.path.isfile(full):
                raise HttpError(404, "Нет такого файла")
            ctype = STATIC_TYPES.get(os.path.splitext(full)[1].lower()) or mimetypes.guess_type(full)[0] or "application/octet-stream"
            with open(full, "rb") as f:
                self._send(200, f.read(), ctype)

        # ------------------------------------------------------ MCP
        def _mcp(self, method):
            if method != "POST":
                raise HttpError(405, "MCP: только POST")
            auth = self.headers.get("Authorization") or ""
            tok = auth[7:] if auth.lower().startswith("bearer ") else (self.headers.get("X-Session-Token") or "")
            client = self.headers.get("User-Agent", "MCP-клиент")[:40]
            if _same(tok, app.token):
                client = "Чат"
            elif not app.settings.get("mcp_external"):
                app.mcp_log_add("127.0.0.1", "—", "отказ: внешние клиенты выключены")
                raise HttpError(403, "Внешние MCP-клиенты выключены в настройках STE Assistant")
            elif not _same(tok, app.settings.get("mcp_token") or ""):
                app.mcp_log_add("127.0.0.1 без токена", "—", "отказ: нет токена")
                raise HttpError(401, "Нужен токен MCP-сервера")
            res = mcp.handle(app, self._json(), client=client)
            if res is None:
                return self._send(202, b"", "text/plain")
            self._send(200, res)

        # ------------------------------------------------------ API
        def _api(self, method, path, q):
            parts = [p for p in path.split("/") if p]
            st = app.store
            if method == "GET" and parts == ["state"]:
                return self._send(200, {"conn": app.conn(), "parts": st.summaries(), "folders": list(st.folders),
                                        "settings": app.public_settings(),
                                        "seq": st.events.seq, "active": app.active_part})
            if method == "GET" and parts == ["events"]:
                since = int((q.get("since") or ["0"])[0])
                return self._send(200, st.events.since(since, timeout=20.0))
            if method == "POST" and parts == ["upload"]:
                name = (q.get("name") or [""])[0]
                n = self._length(MAX_BODY)
                data = self.rfile.read(n)
                if len(data) < n:
                    raise HttpError(400, "Файл загрузился не полностью — приложите снова")
                fi = st.add_file(name, data)
                return self._send(200, dict((k, fi[k]) for k in ("id", "name", "size", "kind")))
            if parts == ["folders"] and method == "POST":
                try:
                    return self._send(200, st.add_folder(self._json().get("name")))
                except ValueError as e:
                    raise HttpError(400, str(e))
            if parts[:1] == ["folders"] and len(parts) == 2:
                if method == "PATCH":
                    try:
                        return self._send(200, st.rename_folder(parts[1], self._json().get("name")))
                    except ValueError as e:
                        raise HttpError(400, str(e))
                    except KeyError:
                        raise HttpError(404, "Папка не найдена")
                if method == "DELETE":
                    st.delete_folder(parts[1])
                    return self._send(200, {"ok": True})
            if parts == ["parts"] and method == "POST":
                body = self._json()
                p = st.new_part()
                try:  # новый чат — в папку, из которой его создали; пустой чат прошлого раза — из папки вынимаем
                    st.set_folder(p, str(body.get("folder") or ""))
                except KeyError:
                    st.set_folder(p, "")
                app.active_part = p["id"]
                return self._send(200, st.public(p))
            if parts[:1] == ["parts"] and len(parts) >= 2:
                part = st.get(parts[1])
                if not part:
                    raise HttpError(404, "Деталь не найдена")
                return self._part(method, part, parts[2:])
            if parts == ["slicer-settings"] and method == "GET":
                return self._send(200, W.list_settings(app.bridge, (q.get("q") or [""])[0][:80]))
            if parts == ["save-name"] and method == "GET":
                part = st.get((q.get("part") or [""])[0])
                return self._send(200, _save_name(app, part))
            if parts == ["catalog"] and method == "GET":
                return self._send(200, app.catalog.all())
            if parts[:1] == ["catalog"] and len(parts) >= 2:
                return self._catalog(method, parts[1:])
            if parts == ["settings"]:
                if method == "GET":
                    return self._send(200, app.public_settings())
                if method == "PUT":
                    return self._send(200, app.save_settings(self._json()))
            if parts == ["settings", "check"] and method == "POST":
                return self._send(200, app.check_llm(self._json()))
            if parts == ["mcp"]:
                if method == "GET":
                    return self._send(200, app.mcp_info())
                if method == "PUT":
                    data = self._json()
                    app.settings.set("mcp_external", bool(data.get("external")))
                    return self._send(200, app.mcp_info())
            if parts == ["mcp", "token"] and method == "POST":
                from .core import new_mcp_token
                app.settings.set("mcp_token", new_mcp_token())
                return self._send(200, app.mcp_info())
            if parts == ["choose-folder"] and method == "POST":
                data = self._json()
                try:
                    path = app.bridge.choose_folder(data.get("start") or app.settings.get("folder"))
                except Exception as e:
                    raise HttpError(409, str(e) or "Не открыть выбор папки — впишите путь вручную")
                return self._send(200, {"path": path})
            if parts == ["open-folder"] and method == "POST":
                data = self._json()
                if app.opener:
                    app.opener(data.get("path") or app.settings.get("folder"))
                return self._send(200, {"ok": True})
            raise HttpError(404, "Нет такого адреса API")

        def _part(self, method, part, rest):
            ag = app.agent
            st = app.store
            if not rest:
                if method == "GET":
                    app.active_part = part["id"]
                    return self._send(200, st.public(part))
                if method == "DELETE":
                    if ag.busy(part["id"]):
                        ag.stop(part["id"])
                        end = time.time() + 10
                        while ag.busy(part["id"]) and time.time() < end:
                            time.sleep(0.1)
                        if ag.busy(part["id"]):
                            raise HttpError(409, "Ассистент не остановился — попробуйте удалить через несколько секунд")
                    st.delete(part["id"])
                    return self._send(200, {"ok": True})
                if method == "PATCH":
                    data = self._json()
                    if "folder" in data:  # переложить чат в папку («» — без папки)
                        try:
                            st.set_folder(part, str(data.get("folder") or ""))
                        except KeyError:
                            raise HttpError(404, "Папка не найдена")
                    name = str(data.get("name") or "").strip()
                    if name:
                        part["name"] = name[:80]
                        part["name_user"] = True  # своё название ассистент больше не меняет
                        st.changed(part)
                        st.events.emit({"type": "parts"})
                    return self._send(200, st.public(part))
                raise HttpError(405, "Метод не поддерживается")
            action = rest[0]
            data = self._json() if method == "POST" else {}
            app.active_part = part["id"]
            if action == "messages":
                text = (data.get("text") or "").strip()
                files = data.get("files") or []
                if not text and not files:
                    raise HttpError(400, "Пустое сообщение")
                if len(files) > 10:
                    raise HttpError(400, "Не больше 10 файлов в сообщении")
                comment = str(data.get("comment") or "").strip()[:1000]
                ag.send(part, text[:8000], files, comment, str(data.get("mode") or ""))
            elif action == "stop":
                ag.stop(part["id"])
            elif action == "answer":
                ag.answer(part, data.get("question"), data.get("value"))
            elif action == "plan":
                from . import plan as P
                key, value = data.get("key"), data.get("value")
                ag.action(part, lambda run: P.edit(run, key, value))
            elif action == "confirm":
                ag.confirm(part)
            elif action == "assumption":
                key, value = data.get("key"), data.get("value")
                from . import plan as P
                if P.in_plan(part):
                    ag.action(part, lambda run: P.edit(run, key, value))
                else:
                    ag.action(part, lambda run: W.set_value(run, key, value), "%s: %s" % (key, value))
            elif action == "variant":
                v = data.get("variant")
                from . import plan as P
                if P.in_plan(part):
                    ag.action(part, lambda run: P.edit(run, "variant", v))
                else:
                    ag.action(part, lambda run: W.set_variant(run, v))
            elif action == "variants":
                from . import plan as P
                if P.in_plan(part):
                    raise HttpError(409, "Сначала подтвердите план печати — варианты считаются пробной нарезкой")
                ag.action(part, lambda run: W.variants(run), "Покажи варианты печати")
            elif action == "slice":
                from . import plan as P
                if P.in_plan(part):  # нарезка до подтверждения = подтверждение плана
                    ag.confirm(part)
                else:
                    ag.action(part, lambda run: W.slice_part(run), "Нарежь")
            elif action == "mode":
                from . import tools as T
                ag.action(part, lambda run: T.execute(run, "set_printing_mode", {"mode": data.get("mode")}),
                          "Печатать в %s" % data.get("mode"))
            elif action == "fix":
                wid, idx = str(data.get("warning") or ""), data.get("index", 0)
                ag.action(part, lambda run: W.apply_fix(run, wid, idx), str(data.get("label") or "")[:80] or None)
            elif action == "setting":
                values = data.get("values") or {}
                if data.get("key"):
                    values = {str(data["key"]): data.get("value")}
                if not values or len(values) > 20:
                    raise HttpError(400, "Нужен параметр и значение")
                ag.action(part, lambda run: W.set_user_settings(run, values))
            elif action == "reset":
                keys = [str(data["key"])] if data.get("key") else None
                ag.action(part, lambda run: W.reset_user_settings(run, keys),
                          None if keys else "Вернуть исходные параметры")
            elif action == "review":
                from . import review as RV
                bid, ids = data.get("block"), [str(x) for x in data.get("defects") or []][:12]
                ag.action(part, lambda run: RV.choose(run, bid, ids))
            elif action == "review-apply":
                from . import review as RV
                bid = data.get("block")
                ag.action(part, lambda run: RV.apply(run, bid), "Применить исправления по фото")
            elif action == "undo":
                bid = data.get("block")
                ag.action(part, lambda run: W.undo(run, bid))
            elif action == "save":
                opts = {"project": bool(data.get("project", True)), "gcode": bool(data.get("gcode", True)),
                        "folder": data.get("folder") or None, "name": data.get("name") or None}
                if opts["folder"]:
                    app.settings.set("folder", opts["folder"])
                ag.action(part, lambda run: W.save(run, **opts))
            elif action == "requirement":
                _edit_requirement(app, part, data)
            elif action == "orientation":
                idx = int(data.get("index", 0))
                ag.action(part, lambda run: _pick_orientation(run, idx))
            else:
                raise HttpError(404, "Нет такого действия")
            return self._send(202, {"ok": True})

        def _catalog(self, method, rest):
            cat = app.catalog
            if rest == ["import"] and method == "POST":
                data = self._json()
                folder = data.get("folder") or ""
                if not os.path.isdir(folder):
                    raise HttpError(400, "Папка не найдена: %s" % folder)
                return self._send(200, cat.import_folder(folder, app.materials_by_code()))
            rec = cat.get(rest[0])
            if not rec:
                raise HttpError(404, "Запись не найдена")
            if len(rest) == 1 and method == "PATCH":
                return self._send(200, cat.update(rec["id"], self._json()))
            if rest[1:] == ["template"] and method == "POST":
                part = _from_record(app, rec, template=True)
                return self._send(200, app.store.public(part))
            if rest[1:] == ["open"] and method == "POST":
                part = _from_record(app, rec, template=False)
                return self._send(200, app.store.public(part))
            raise HttpError(404, "Нет такого действия")

    return Handler


def _same(a, b):
    """Сравнение токенов за постоянное время; пустой токен не подходит никогда."""
    a = (a or "").encode("utf-8")
    b = (b or "").encode("utf-8")
    return bool(a) and bool(b) and hmac.compare_digest(a, b)


def _save_name(app, part):
    from . import catalog as C
    from . import materials as M
    st = (part or {}).get("state") or {}
    m = M.by_id(st.get("material_id") or "") or {"name": ""}
    rows = dict((r["key"], r["value"]) for r in st.get("rows", []) if r["key"])
    vals = C.values_for(part["name"] if part else "detal", (part or {}).get("mode") or "3D", m["name"],
                        rows.get("layer_height", 0.1), rows.get("infill_sparse_density", 20), rows.get("wall_line_count"),
                        printer=app.printer_name(), copies=st.get("quantity", 1))
    tpl = app.settings.get("template")
    from .core import default_folder
    return {"name": C.make_name(tpl, vals), "folder": app.settings.get("folder") or default_folder(), "template": tpl,
            "sliced": bool(st.get("totals")), "time": (st.get("totals") or {}).get("time")}


def _edit_requirement(app, part, data):
    if app.agent.busy(part["id"]):
        raise RuntimeError("Ассистент ещё работает над этой деталью — дождитесь конца хода")
    st = part.setdefault("state", {})
    reqs = st.get("requirements", [])
    tag = data.get("tag")
    text = data.get("text")
    kind = data.get("kind")
    from . import requirements as R
    if data.get("delete"):
        st["requirements"] = [r for r in reqs if r["tag"] != tag]
    elif tag in [r["tag"] for r in reqs]:
        for r in reqs:
            if r["tag"] == tag:
                if text:
                    r["text"] = text
                if kind in ("must", "ban", "want"):
                    r["kind"] = kind
                r["source"] = "words"
    elif data.get("add") and text:
        found = R.extract(text)
        if found:
            for r in found:
                r["source"] = "words"
                r["quote"] = "Добавлено вами"
            st["requirements"] = R.merge(reqs, found)
        else:
            raise ValueError("Не понял требование. Напишите, например: «стойкость к бензину» или «плотность не более 1,2 г/см³».")

    def redo(run):
        W.show_requirements(run)
        if st.get("geo"):
            before = W.snapshot(run)
            old = st.get("material_id")
            W.select_material(run)
            W.apply_settings(run)
            if before["state"].get("totals"):
                W.slice_part(run, card=False)
            if old and old != st.get("material_id"):
                from . import materials as M
                W.diff_block(run, before, [["Материал", M.by_id(old)["name"], M.by_id(st["material_id"])["name"], "Изменились требования"]],
                             "Пересчитал по требованиям")
    app.agent.action(part, redo, "Изменил требования")


def _pick_orientation(run, idx):
    """Кнопка «Выбрать» в карточке положения: idx — номер в списке кандидатов (в позе файла)."""
    st = run.st
    if st.get("mode", "classic") != "classic":
        raise ValueError("В 5D положение одно: ось вертикально, центр стола")
    before = W.snapshot(run)
    st["orient_choice"] = int(idx)
    W.orient(run, emit=False, choice=int(idx))
    W.apply_settings(run)
    if before["state"].get("totals"):
        W.slice_part(run, card=False)
    W.diff_block(run, before, [["Положение", before["state"].get("orientation", "—"), st["orientation"], "Вы выбрали"]], "Сменил положение")


def _from_record(app, rec, template):
    """Открыть запись картотеки: деталь с её настройками; модель — если файл на месте."""
    st = app.store
    for pid in st.order:
        p = st.get(pid)
        if p and p["id"] == rec.get("part_id") and not template:
            app.active_part = p["id"]
            return p
    part = st.new_part(rec["name"] + (" (по шаблону)" if template else ""))
    part["kind"] = rec.get("kind", "part")
    state = part["state"]
    if rec.get("requirements"):
        state["requirements"] = rec["requirements"]
    if rec.get("material_id"):
        state["fixed_material"] = rec["material_id"]
    files = []
    mp = rec.get("model_path")
    if mp and os.path.isfile(mp) and not template:
        fi = st.add_file_from_path(mp)
        files.append(fi["id"])
    text = ("Взять настройки из картотеки: «%s», %s, %s, заполнение %s." % (rec["name"], rec.get("mode"), rec.get("material"), rec.get("infill"))
            if template else "Открыть деталь из картотеки: «%s»." % rec["name"])
    if rec.get("note"):
        text += " Заметка: %s" % rec["note"]
    if files:
        app.agent.send(part, text, files)
    else:
        part["messages"].append({"role": "user", "text": text, "files": [], "time": ""})
        part["messages"].append({"role": "ai", "blocks": [{"type": "text", "text": (
            "Запомнил: **%s**, режим %s, заполнение %s%s. Приложите модель — применю эти настройки к ней."
            % (rec.get("material") or "материал из записи", rec.get("mode"), rec.get("infill"),
               (". Учту заметку: «%s»" % rec["note"]) if rec.get("note") else ""))}]})
        st.changed(part)
    app.active_part = part["id"]
    return part
