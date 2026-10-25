# -*- coding: utf-8 -*-
"""Приложение ассистента: хранилище, картотека, мост к слайсеру, агент, настройки, сервер.

Не зависит от Qt: в STE Slicer его создаёт расширение (SteAssistant.py) и передаёт
SlicerBridge и хранилище настроек на Preferences; в разработке и тестах — FakeBridge
и настройки в JSON-файле (tools/dev_server.py).
"""
import json
import os
import base64
import binascii
import threading
import time

from . import catalog as C
from . import materials as M
from .agent import Agent
from .store import Store

DEFAULTS = {
    "url": "https://api.timeweb.ai/v1",
    "model": "dashscope/qwen3.8-flash",
    "price": 3000,
    "ask_questions": True,
    "autostart": False,
    "template": C.DEFAULT_TEMPLATE,
    "folder": "",
    "images": True,
    "vision_model": "",
    "timeout": 120,
    "mcp_external": False,
}
PUBLIC = ("url", "model", "price", "ask_questions", "autostart", "template", "folder", "images", "vision_model", "mcp_external")


class JsonPrefs(object):
    """Настройки в JSON-файле (вне слайсера). Ключ LLM хранится там же, но наружу не отдаётся."""

    def __init__(self, path):
        self.path = path
        self.data = {}
        if os.path.isfile(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    self.data = json.load(f)
            except (OSError, ValueError):
                self.data = {}

    def get(self, key, default=None):
        return self.data.get(key, default)

    def set(self, key, value):
        self.data[key] = value
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self.data, f, ensure_ascii=False, indent=1)
        os.replace(tmp, self.path)


class Settings(object):
    def __init__(self, prefs):
        self.prefs = prefs

    def get(self, key, default=None):
        v = self.prefs.get("ste_assistant/" + key, None)
        if v is None or v == "":
            v = DEFAULTS.get(key, default)
        return v

    def set(self, key, value):
        self.prefs.set("ste_assistant/" + key, value)

    def public(self, key_saved):
        out = dict((k, self.get(k)) for k in PUBLIC)
        out["keySaved"] = key_saved
        if not out.get("folder"):
            out["folder"] = default_folder()
        out["fields"] = C.FIELDS
        return out


def new_mcp_token():
    return "ste_" + binascii.hexlify(os.urandom(8)).decode("ascii")


def default_folder():
    return os.path.join(os.path.expanduser("~"), "Documents", "STE Assistant")


class App(object):
    def __init__(self, data_dir, bridge, prefs, logger=None, opener=None):
        if not os.path.isdir(data_dir):
            os.makedirs(data_dir)
        self.data_dir = data_dir
        self.bridge = bridge
        self.settings = Settings(prefs)
        self.prefs = prefs
        self.store = Store(data_dir)
        self.catalog = C.Catalog(data_dir)
        self.agent = Agent(self)
        self._log = logger
        self.opener = opener
        self.token = base64.urlsafe_b64encode(os.urandom(18)).decode("ascii").rstrip("=")
        if not self.settings.get("mcp_token"):
            self.settings.set("mcp_token", new_mcp_token())
        self.llm_ok = None
        self.llm_error = ""
        self.mcp_log = []
        self.active_part = None
        self.port = None
        self.lock = threading.Lock()

    # ------------------------------------------------------------- служебное
    def log(self, level, msg):
        if self._log:
            self._log(level, msg)

    def llm_key(self):
        return self.settings.get("llm_key") or os.environ.get("STE_LLM_KEY") or ""

    def set_llm_status(self, ok, error=""):
        if self.llm_ok != ok or self.llm_error != error:
            self.llm_ok, self.llm_error = ok, error
            self.store.events.emit({"type": "conn"})

    def conn(self):
        printer = {}
        try:
            printer = self.bridge.printer()
        except Exception:
            pass
        key = bool(self.llm_key())
        return {"slicer": bool(printer), "printer": printer.get("name", ""), "llm": key and self.llm_ok is not False,
                "keySaved": key, "model": self.settings.get("model"), "llmError": self.llm_error if key else "Нет ключа LLM"}

    def printer_name(self):
        try:
            return self.bridge.printer().get("name", "").replace("Stereotech ", "")
        except Exception:
            return ""

    def build_volume(self):
        try:
            return self.bridge.printer().get("volume")
        except Exception:
            return None

    # ------------------------------------------------------------- настройки
    def public_settings(self):
        out = self.settings.public(bool(self.llm_key()))
        return out

    def save_settings(self, data):
        err = None
        if "template" in data:
            err = C.check_template(data["template"])
            if err:
                raise ValueError(err)
        for k in PUBLIC:
            if k in data:
                v = data[k]
                if k == "price":
                    v = float(str(v).replace(" ", "").replace(",", ".") or 0)
                self.settings.set(k, v)
        if data.get("key"):
            self.settings.set("llm_key", data["key"].strip())
            self.llm_ok = None
        if data.get("clearKey"):
            self.settings.set("llm_key", "")
        self.store.events.emit({"type": "conn"})
        return self.public_settings()

    def check_llm(self, data):
        from .llm import LLMClient, LLMError
        key = (data.get("key") or "").strip() or self.llm_key()
        client = LLMClient(data.get("url") or self.settings.get("url"), data.get("model") or self.settings.get("model"), key, timeout=40)
        try:
            res = client.check()
            self.set_llm_status(True)
        except LLMError as e:
            self.set_llm_status(False, str(e))
            return {"reply": False, "error": str(e)}
        vision = (data.get("vision_model") if "vision_model" in data else self.settings.get("vision_model")) or ""
        vision = vision.strip()
        if vision and vision != client.model:
            vc = LLMClient(client.url, vision, key, timeout=40)
            res["vision"], res["vision_note"] = vc.sees_images()
        return res

    # ------------------------------------------------------------- MCP
    def mcp_info(self):
        from . import tools as T
        return {"url": "http://localhost:%s/mcp" % self.port, "token": self.settings.get("mcp_token"),
                "external": bool(self.settings.get("mcp_external")),
                "tools": [[t["name"], t["description"].split(".")[0]] for t in T.mcp_tools()],
                "log": self.mcp_log[:30]}

    def mcp_log_add(self, client, tool, result):
        self.mcp_log.insert(0, [time.strftime("%H:%M:%S"), client, tool, result])
        del self.mcp_log[60:]

    # ------------------------------------------------------------- материалы
    def materials_by_code(self):
        return dict((C.material_code(m["name"]).upper(), m["name"]) for m in M.MATERIALS)
