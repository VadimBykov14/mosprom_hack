# -*- coding: utf-8 -*-
"""MCP-сервер плагина: JSON-RPC 2.0 поверх HTTP (POST /mcp).

Методы: initialize, notifications/initialized, ping, tools/list, tools/call.
Встроенный агент вызывает те же инструменты напрямую; внешние клиенты (Claude Desktop,
Cursor) — только если это разрешено в настройках, только с 127.0.0.1 и только с токеном.
"""
import json

from . import tools as T

PROTOCOL = "2025-03-26"


def _ok(rid, result):
    return {"jsonrpc": "2.0", "id": rid, "result": result}


def _err(rid, code, message):
    return {"jsonrpc": "2.0", "id": rid, "error": {"code": code, "message": message}}


def handle(app, payload, client="MCP-клиент"):
    """Обработать запрос или пакет запросов. Возвращает ответ (dict, list) или None для уведомлений."""
    if isinstance(payload, list):
        out = [r for r in (handle(app, p, client) for p in payload) if r is not None]
        return out or None
    if not isinstance(payload, dict) or payload.get("jsonrpc") != "2.0":
        return _err(None, -32600, "Ожидается JSON-RPC 2.0")
    rid = payload.get("id")
    method = payload.get("method")
    params = payload.get("params") or {}
    if not isinstance(params, dict):
        return _err(rid, -32602, "params должен быть объектом")
    if rid is None:
        return None  # уведомление: notifications/initialized и т. п.
    if method == "initialize":
        return _ok(rid, {"protocolVersion": params.get("protocolVersion") or PROTOCOL,
                         "capabilities": {"tools": {"listChanged": False}},
                         "serverInfo": {"name": "ste-assistant", "version": "1.0.0"},
                         "instructions": "Инструменты управляют STE Slicer: модель, материал, режим 3D/5D, настройки, нарезка, сохранение. "
                                         "Работа идёт с активной деталью чата."})
    if method == "ping":
        return _ok(rid, {})
    if method == "tools/list":
        return _ok(rid, {"tools": T.mcp_tools()})
    if method == "tools/call":
        name = params.get("name")
        args = params.get("arguments") or {}
        if not isinstance(args, dict):
            return _err(rid, -32602, "arguments должен быть объектом")
        if name not in [t["name"] for t in T.mcp_tools()]:
            return _err(rid, -32602, "Неизвестный инструмент %s" % name)
        part = app.store.get(app.active_part) if app.active_part else None
        if part is None:
            part = app.store.new_part("Деталь от %s" % client)
            app.active_part = part["id"]
        if app.agent.busy(part["id"]):
            return _ok(rid, {"content": [{"type": "text", "text": "Ассистент сейчас работает над деталью, повторите позже"}], "isError": True})
        try:
            result = app.agent.mcp_call(part, name, args)
            app.mcp_log_add(client, name, "готово")
            return _ok(rid, {"content": [{"type": "text", "text": json.dumps(result, ensure_ascii=False, default=str)}], "isError": False})
        except Exception as e:
            app.mcp_log_add(client, name, "ошибка: %s" % e)
            return _ok(rid, {"content": [{"type": "text", "text": str(e)}], "isError": True})
    return _err(rid, -32601, "Метод %s не поддерживается" % method)
