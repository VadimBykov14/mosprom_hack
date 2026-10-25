# -*- coding: utf-8 -*-
"""Клиент OpenAI-совместимого Chat Completions API на urllib (без зависимостей).

Ключ передаётся только в заголовке Authorization и никуда не пишется.
"""
import json
import os
import socket
import ssl
import time
import urllib.error
import urllib.request


class LLMError(Exception):
    def __init__(self, message, status=None, retry=False, wait=None):
        Exception.__init__(self, message)
        self.status = status
        self.retry = retry  # сбой временный: перегрузка, ошибка сервера, обрыв связи
        self.wait = wait  # сколько просил подождать сервер (Retry-After), с


RETRY_STATUS = (429, 500, 502, 503, 504)
RETRY_PAUSES = (1.5, 4.0)  # два повтора; таймаут не повторяем — ждать ещё 2 минуты дольше, чем уйти на правила
RETRY_MAX_WAIT = 10.0


CA_BUNDLE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "certs", "cacert.pem")


def _ssl_context():
    """Системные сертификаты + свой набор корневых (Mozilla, из certifi).

    Во встроенном Python слайсера системного набора может не быть.
    STE_LLM_CA — путь к сертификату прокси или антивируса, если он проверяет HTTPS.
    """
    ctx = ssl.create_default_context()
    paths = [os.environ.get("STE_LLM_CA"), CA_BUNDLE]
    try:
        import certifi
        paths.append(certifi.where())
    except Exception:
        pass
    for path in paths:
        if path and os.path.isfile(path):
            try:
                ctx.load_verify_locations(path)
            except Exception:
                pass
    flag = getattr(ssl, "VERIFY_X509_TRUSTED_FIRST", 0)
    if flag:
        ctx.verify_flags |= flag
    return ctx


def _reason(e):
    reason = getattr(e, "reason", e)
    text = str(reason)
    if "CERTIFICATE_VERIFY_FAILED" in text:
        text += (". Проверьте дату и время на компьютере. Если HTTPS проверяет антивирус или прокси, "
                 "укажите путь к его корневому сертификату в переменной STE_LLM_CA и перезапустите слайсер")
    return text


class LLMClient(object):
    def __init__(self, url, model, key, timeout=120, pauses=RETRY_PAUSES):
        self.url = (url or "").rstrip("/")
        self.model = model
        self.key = key
        self.timeout = timeout
        self.pauses = pauses
        self.sleep = time.sleep

    def chat(self, messages, tools=None, temperature=0.2, max_tokens=1800):
        """Запрос к модели. Временный сбой (429, 5xx, обрыв связи) — повтор с паузой."""
        if not self.key:
            raise LLMError("Нет ключа LLM: введите его в настройках", 401)
        for i in range(len(self.pauses) + 1):
            try:
                return self._chat(messages, tools, temperature, max_tokens)
            except LLMError as e:
                if not e.retry or i >= len(self.pauses):
                    raise
                pause = self.pauses[i]
                if e.wait is not None:
                    pause = max(pause, min(e.wait, RETRY_MAX_WAIT))
                self.sleep(pause)

    def _chat(self, messages, tools, temperature, max_tokens):
        body = {"model": self.model, "messages": messages, "temperature": temperature, "max_tokens": max_tokens}
        if tools:
            body["tools"] = tools
            body["tool_choice"] = "auto"
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(self.url + "/chat/completions", data=data, method="POST", headers={
            "Content-Type": "application/json", "Authorization": "Bearer " + self.key})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout, context=_ssl_context()) as resp:
                raw = resp.read()
            try:
                payload = json.loads(raw.decode("utf-8"))
            except ValueError:
                raise LLMError("LLM ответила не JSON — проверьте адрес API (нужен …/v1): %s" % raw[:80].decode("utf-8", "replace"))
        except urllib.error.HTTPError as e:
            detail = ""
            try:
                detail = json.loads(e.read().decode("utf-8")).get("error", {}).get("message", "")
            except Exception:
                pass
            host = self.url.split("//")[-1].split("/")[0]
            hint = {401: "ключ не подходит", 403: "нет доступа к модели", 404: "адрес или модель не найдены",
                    429: "слишком много запросов, подождите", 500: "ошибка на стороне сервера"}.get(e.code, "")
            wait = None
            try:
                wait = float(e.headers.get("Retry-After")) if e.headers is not None else None
            except (TypeError, ValueError):
                pass
            raise LLMError("%s ответил %d%s%s" % (host, e.code, ": " + hint if hint else "",
                                                  (" (" + detail[:120] + ")") if detail else ""), e.code,
                           retry=e.code in RETRY_STATUS, wait=wait)
        except socket.timeout as e:
            raise LLMError("Нет связи с LLM: %s" % _reason(e))
        except ssl.SSLError as e:  # сертификат не исправится от повтора
            raise LLMError("Нет связи с LLM: %s" % _reason(e))
        except (urllib.error.URLError, ConnectionError) as e:
            bad_cert = isinstance(getattr(e, "reason", None), ssl.SSLError)
            timed_out = isinstance(getattr(e, "reason", None), socket.timeout)
            raise LLMError("Нет связи с LLM: %s" % _reason(e), retry=not (bad_cert or timed_out))
        try:
            msg = payload["choices"][0]["message"]
        except (KeyError, IndexError, TypeError):
            raise LLMError("LLM вернула ответ без сообщения")
        return msg

    def check(self):
        """Три проверки для экрана настроек: ответ, вызов инструментов, изображения."""
        out = {"reply": False, "tools": False, "images": False}
        import time
        t0 = time.time()
        self.chat([{"role": "user", "content": "Ответь одним словом: готов"}], max_tokens=10)
        out["reply"] = True
        out["ms"] = int((time.time() - t0) * 1000)
        tool = [{"type": "function", "function": {"name": "ping", "description": "Проверка связи",
                                                   "parameters": {"type": "object", "properties": {"ok": {"type": "boolean"}}, "required": ["ok"]}}}]
        try:
            m = self.chat([{"role": "user", "content": "Вызови инструмент ping с ok=true"}], tools=tool, max_tokens=50)
            out["tools"] = bool(m.get("tool_calls"))
        except LLMError:
            out["tools"] = False
        out["images"], out["images_note"] = self.sees_images()
        return out

    def sees_images(self):
        """Проверка на деле: модель должна назвать цвет красного квадрата.

        Текстовые модели часто молча выбрасывают картинку и отвечают наугад,
        поэтому отсутствие ошибки ещё ничего не значит.
        """
        from .images import test_png
        try:
            m = self.chat([{"role": "user", "content": [
                {"type": "text", "text": "Какого цвета квадрат на картинке? Ответь одним словом."},
                {"type": "image_url", "image_url": {"url": "data:image/png;base64," + test_png()}}]}], max_tokens=100)
        except LLMError as e:
            return False, "модель %s не принимает изображения: %s" % (self.model, e)
        answer = text_of(m).lower()
        if "крас" in answer or "red" in answer:
            return True, ""
        return False, "модель %s не видит изображения (ответ: «%s»). Укажите модель для фото с поддержкой изображений (VL)" % (
            self.model, answer[:40])

    def describe_images(self, urls, context=""):
        """Текстовое описание фото — для основной модели, если она сама картинки не видит."""
        parts = [{"type": "text", "text": DESCRIBE_PROMPT + (("\n\nКонтекст от пользователя: " + context) if context else "")}]
        parts += [{"type": "image_url", "image_url": {"url": u}} for u in urls]
        return text_of(self.chat([{"role": "user", "content": parts}], max_tokens=600)).strip()


DESCRIBE_PROMPT = """Ты помогаешь подготовить деталь к 3D-печати. Опиши по-русски, что на фото, 3–6 пунктами:
- что за деталь или узел и где она стоит;
- условия работы, которые видно: нагрузка, трение, вращение, температура, масло, вода, топливо, улица/солнце;
- сопрягаемые детали и посадки (вал, подшипник, шестерня), износ или поломка, если видны;
- надписи, размеры, маркировка материала, если читаются.
Только то, что видно; если не уверен — так и напиши."""


def text_of(msg):
    """content ответа: строка или список частей."""
    c = (msg or {}).get("content") or ""
    if isinstance(c, list):
        c = " ".join(p.get("text", "") for p in c if isinstance(p, dict))
    return c
