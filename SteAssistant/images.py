# -*- coding: utf-8 -*-
"""Фото для LLM: уменьшение, data-URL и проверочная картинка.

Фото с телефона весят 3–10 МБ — API такие часто не принимают. В слайсере есть PyQt5:
уменьшаем до 1280 px по длинной стороне и пересохраняем в JPEG. Без Qt отправляем
файл как есть, если он не больше 4 МБ.
"""
import base64
import struct
import zlib

MAX_SIDE = 1280
RAW_LIMIT = 4 * 1024 * 1024


def _scaled_jpeg(path):
    try:
        from PyQt5.QtCore import QBuffer, QByteArray, QIODevice, Qt
        from PyQt5.QtGui import QImage
    except Exception:
        return None
    img = QImage(path)
    if img.isNull():
        return None
    if max(img.width(), img.height()) > MAX_SIDE:
        img = img.scaled(MAX_SIDE, MAX_SIDE, Qt.KeepAspectRatio, Qt.SmoothTransformation)
    if img.hasAlphaChannel():
        img = img.convertToFormat(QImage.Format_RGB32)
    ba = QByteArray()
    buf = QBuffer(ba)
    buf.open(QIODevice.WriteOnly)
    ok = img.save(buf, "JPG", 85)
    buf.close()
    return bytes(ba.data()) if ok and ba.size() else None


def data_url(path, name=""):
    """data-URL для image_url или None, если фото не удалось подготовить."""
    data = _scaled_jpeg(path)
    if data:
        return "data:image/jpeg;base64," + base64.b64encode(data).decode("ascii")
    try:
        with open(path, "rb") as fh:
            raw = fh.read(RAW_LIMIT + 1)
    except OSError:
        return None
    if len(raw) > RAW_LIMIT:
        return None
    mime = "image/png" if (name or path).lower().endswith(".png") else "image/jpeg"
    return "data:%s;base64,%s" % (mime, base64.b64encode(raw).decode("ascii"))


def test_png(size=64, rgb=(220, 30, 30)):
    """Квадрат одного цвета в PNG — для проверки, что модель видит изображения."""
    row = b"\x00" + bytes(rgb) * size
    raw = row * size

    def chunk(tag, body):
        return struct.pack(">I", len(body)) + tag + body + struct.pack(">I", zlib.crc32(tag + body) & 0xffffffff)
    png = (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0)) +
           chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))
    return base64.b64encode(png).decode("ascii")
