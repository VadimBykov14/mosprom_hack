# -*- coding: utf-8 -*-
"""Расширение STE Slicer: меню «AI Assistant», сервер чата, настройки в Preferences.

Чат — веб-страница в системном браузере: плагин поднимает локальный HTTP-сервер
и открывает http://localhost:<порт>/?t=<токен>. Ключ LLM хранится в настройках
приложения (Preferences) и в браузер не передаётся.
"""
import os
import threading

from PyQt5.QtCore import QObject, QUrl
from PyQt5.QtGui import QDesktopServices, QGuiApplication

from UM.Extension import Extension
from UM.Logger import Logger
from UM.Message import Message
from UM.Resources import Resources

from steslicer.SteSlicerApplication import SteSlicerApplication

from .bridge_slicer import SlicerBridge
from .core import DEFAULTS, App
from .server import ChatServer

PREFIX = "ste_assistant/"
PREF_KEYS = list(DEFAULTS.keys()) + ["llm_key", "mcp_token"]


class QtPrefs(object):
    """Хранилище настроек App поверх Preferences приложения."""

    def __init__(self, prefs):
        self.prefs = prefs
        for k in PREF_KEYS:
            try:
                prefs.addPreference(PREFIX + k, "")
            except Exception:  # уже зарегистрирована
                pass

    def get(self, key, default=None):
        v = self.prefs.getValue(key)
        if v in (None, ""):
            return default
        if v in ("True", "False"):
            return v == "True"
        return v

    def set(self, key, value):
        self.prefs.setValue(key, value)


class SteAssistantExtension(QObject, Extension):
    def __init__(self, parent=None):
        QObject.__init__(self, parent)
        Extension.__init__(self)
        if hasattr(self, "setMenuName"):
            self.setMenuName("AI Assistant")
        self.addMenuItem("Открыть чат", self.openChat)
        self.addMenuItem("Настройки", self.openSettings)
        self._app = None
        self._server = None
        self._lock = threading.Lock()
        self._ready_msg = None
        self._addr_msg = None
        self._chat_url = ""
        SteSlicerApplication.getInstance().engineCreatedSignal.connect(self._onEngineCreated)

    # ------------------------------------------------------------ запуск
    def _ensure(self):
        with self._lock:
            if self._server is not None:
                return self._server.url
            sa = SteSlicerApplication.getInstance()
            data_dir = os.path.join(Resources.getDataStoragePath(), "ste_assistant")
            prefs = QtPrefs(sa.getPreferences())
            self._app = App(data_dir, SlicerBridge(sa), prefs, logger=self._log, opener=self._openFolder)
            self._server = ChatServer(self._app)
            url = self._server.start()
            if hasattr(sa, "applicationShuttingDown"):
                sa.applicationShuttingDown.connect(self._shutdown)
            Logger.log("i", "STE Assistant: чат на http://localhost:%d", self._server.port)
            return url

    def _onEngineCreated(self):
        sa = SteSlicerApplication.getInstance()
        prefs = QtPrefs(sa.getPreferences())
        autostart = prefs.get(PREFIX + "autostart", False)
        if autostart:
            self.openChat()
            return
        msg = Message("Опишите задачу в чате — ассистент подберёт материал и режим и настроит печать.",
                      title="STE Assistant готов", lifetime=20)
        msg.addAction("open", "Открыть чат", "", "")
        # Signal в Uranium держит лямбды и вложенные функции по слабой ссылке — их сразу
        # собирает сборщик мусора, и кнопка молчит. Подключаем только методы объекта.
        msg.actionTriggered.connect(self._onReadyAction)
        self._ready_msg = msg
        msg.show()

    def _onReadyAction(self, message, action):
        message.hide()
        self.openChat()

    def _shutdown(self):
        if self._server:
            self._server.stop()

    def _log(self, level, msg):
        Logger.log(level, "STE Assistant: %s", msg)

    # ------------------------------------------------------------ меню
    def openChat(self, suffix=""):
        try:
            url = self._ensure() + suffix
        except Exception as e:
            Logger.logException("e", "STE Assistant не запустился")
            Message("Не удалось запустить чат: %s" % e, title="STE Assistant").show()
            return
        if not QDesktopServices.openUrl(QUrl(url)):
            self._showAddress(url)

    def openSettings(self):
        self.openChat("#settings")

    def _showAddress(self, url):
        msg = Message("Браузер не открылся. Откройте адрес вручную:\n%s" % url, title="Откройте чат в браузере", lifetime=0)
        msg.addAction("copy", "Копировать", "", "")
        self._chat_url = url
        self._addr_msg = msg
        msg.actionTriggered.connect(self._onCopyAction)
        msg.show()

    def _onCopyAction(self, message, action):
        QGuiApplication.clipboard().setText(self._chat_url)
        message.hide()
        Message("Адрес скопирован", title="STE Assistant", lifetime=4).show()

    def _openFolder(self, path):
        if path and os.path.isdir(path):
            SteSlicerApplication.getInstance().callLater(lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(path)))
