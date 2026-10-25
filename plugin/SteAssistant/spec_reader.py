# -*- coding: utf-8 -*-
"""Текст ТЗ из DOCX и TXT без сторонних библиотек: DOCX — это zip с word/document.xml."""
import re
import zipfile
from xml.etree import ElementTree as ET

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


def _para_text(p):
    out = []
    for node in p.iter():
        if node.tag == W + "t" and node.text:
            out.append(node.text)
        elif node.tag == W + "tab":
            out.append("\t")
        elif node.tag in (W + "br", W + "cr"):
            out.append("\n")
    return "".join(out)


MAX_XML = 40 * 1024 * 1024


def read_docx(path):
    try:
        with zipfile.ZipFile(path) as z:
            info = z.getinfo("word/document.xml")
            if info.file_size > MAX_XML:
                raise ValueError("ТЗ слишком большое: текст DOCX больше 40 МБ")
            xml = z.read(info)
    except (zipfile.BadZipFile, KeyError):
        raise ValueError("Файл ТЗ не похож на DOCX. Если это старый .doc — сохраните его в Word как DOCX или TXT")
    # Сущности в документе Word не нужны, а в ElementTree Python 3.5 они раздуваются в памяти.
    if b"<!ENTITY" in xml[:4096] or b"<!DOCTYPE" in xml[:4096]:
        raise ValueError("ТЗ содержит необычную разметку DOCX — сохраните его заново или пришлите TXT")
    try:
        root = ET.fromstring(xml)
    except ET.ParseError:
        raise ValueError("Не удалось прочитать DOCX — файл повреждён")
    body = root.find(W + "body")
    if body is None:
        return ""
    lines = []
    for el in body:
        if el.tag == W + "p":
            t = _para_text(el).strip()
            if t:
                lines.append(t)
        elif el.tag == W + "tbl":
            for row in el.iter(W + "tr"):
                cells = []
                for cell in row.iter(W + "tc"):
                    ct = " ".join(_para_text(p).strip() for p in cell.iter(W + "p"))
                    cells.append(ct.strip())
                if any(cells):
                    lines.append(" | ".join(cells))
    return "\n".join(lines)


def read_txt(path):
    with open(path, "rb") as f:
        data = f.read()
    for enc in ("utf-8-sig", "cp1251", "koi8-r"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", "replace")


def read(path):
    low = path.lower()
    if low.endswith(".docx"):
        return read_docx(path)
    if low.endswith(".txt") or low.endswith(".md"):
        return read_txt(path)
    raise ValueError("Формат ТЗ не поддерживается: пришлите DOCX или TXT")


def part_name(text):
    m = re.search(r"Наименование детали:\s*(.+)", text)
    if m:
        name = m.group(1).strip().rstrip(".")
        name = re.sub(r"\s*\(.*?\)\s*$", "", name)
        return name[:1].upper() + name[1:]
    return None


def quantity(text):
    m = re.search(r"парти\w+ из (\d+)|(\d+)\s*(?:шт|штук|фигур|копи)", text, re.I)
    if m:
        return int(m.group(1) or m.group(2))
    return 1
