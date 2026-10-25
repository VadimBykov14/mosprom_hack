# -*- coding: utf-8 -*-
"""Детали, загруженные файлы и поток событий для чата.

Деталь — переписка, модель, требования и настройки одной задачи. Хранится в JSON
в папке данных плагина, файлы — рядом. События нумеруются; браузер забирает их
длинным опросом GET /api/events?since=N.
"""
import copy
import json
import os
import re
import shutil
import threading
import time
import uuid

MODEL_EXT = (".stl", ".obj", ".3mf")
SPEC_EXT = (".docx", ".txt")
IMAGE_EXT = (".jpg", ".jpeg", ".png")
MAX_MODEL = 100 * 1024 * 1024
MAX_OTHER = 20 * 1024 * 1024
MAX_FILES = 10


def now_hm():
    return time.strftime("%H:%M")


def file_kind(name):
    low = name.lower()
    if low.endswith(MODEL_EXT):
        return "model"
    if low.endswith(SPEC_EXT):
        return "spec"
    if low.endswith(IMAGE_EXT):
        return "image"
    return "other"


def human_size(n):
    if n >= 1024 * 1024:
        return ("%.1f МБ" % (n / 1048576.0)).replace(".", ",")
    return "%d КБ" % max(1, int(round(n / 1024.0)))


def safe_name(name):
    name = os.path.basename(name.replace("\\", "/"))
    name = re.sub(r"[^\w.\- ()]+", "_", name, flags=re.U).strip() or "file"
    return name[:120]


_TAIL = set("для из с со на в во и к ко по от под над без о об обо у а или при до за через".split())
# Окончания прилагательных и причастий: название не должно обрываться на «…конвейерной» без существительного.
_ADJ = re.compile(r"(ый|ий|ой|ая|яя|ое|ее|ые|ие|ого|его|ому|ему|ым|им|ых|их|ую|юю|ой|ей|ою|ею)$", re.U)
# Мусор в именах файлов: версии, «final», «copy», голые числа.
_JUNK = re.compile(r"^(v\d+|ver\d*|rev\d*|final|copy|new|old|test|draft|\d+)$", re.I)
WHOLE_LIMIT = 34  # фраза целиком, если помещается
CUT_LIMIT = 30  # иначе — обрезка по целым словам


def _is_adj(word):
    w = word.lower()
    return len(w) > 3 and bool(re.search(r"[а-яё]", w)) and bool(_ADJ.search(w))


def short_name(name, words=5, limit=WHOLE_LIMIT):
    """Короткое осмысленное название детали для списка и шапки чата.

    Помещается целиком (до 5 слов и 34 символов) — без обрезки: «Ведущее колесо конвейерной ленты».
    Длиннее — по целым словам, но не на прилагательном без существительного и не на предлоге:
    «Колесо приводное конвейера линии упаковки (поз. 4)» → «Колесо приводное конвейера».
    """
    name = re.sub(r"\s*\([^)]*\)", " ", str(name or ""))
    name = re.sub(r"[«»\"“”]", "", name)
    parts = [w.strip(",.;:") for w in name.split()]
    parts = [w for w in parts if w and w not in ("-", "—", "–") and not _JUNK.match(w)]
    if not parts:
        return ""

    def cap(t):
        return t[:1].upper() + t[1:]

    whole = list(parts)
    while len(whole) > 1 and whole[-1].lower() in _TAIL:
        whole = whole[:-1]
    if len(whole) <= words and len(" ".join(whole)) <= limit:
        return cap(" ".join(whole))
    best = None
    for n in range(min(words, len(parts)), 0, -1):
        cut = parts[:n]
        # Прилагательное после главного слова («Колесо приводное») — законченное название; дальше — обрыв.
        while len(cut) > 1 and (cut[-1].lower() in _TAIL or (_is_adj(cut[-1]) and not (len(cut) == 2 and not _is_adj(cut[0])))):
            cut = cut[:-1]
        text = " ".join(cut)
        if len(text) <= CUT_LIMIT:
            best = text
            break
    return cap((best or parts[0])[:limit].rstrip())


# Слова-названия деталей: по ним из сообщения пользователя берётся название чата.
PART_NOUNS = re.compile(
    r"(колес|ролик|вал\b|вала\b|ось\b|оси\b|кронштейн|шестер|зубчат|втулк|корпус|крышк|пыльник|пылник|чехол|фланец|фланц|"
    r"держател|крыльчат|импеллер|кольц|муфт|шкив|звёздочк|звездочк|заглушк|уплотн|прокладк|рычаг|ручк|кожух|"
    r"панел|адаптер|переходник|насадк|стойк|опор|ножк|клипс|зажим|защёлк|защелк|петл|гайк|винт|болт|шайб|"
    r"катушк|бобин|кнопк|рамк|рама|решётк|решетк|шарнир|кулачок|кулачк|толкател|направляющ|ограничител|"
    r"фиксатор|хомут|короб|бокс|лоток|поднос|подставк|фигур|статуэт|модел|макет|прототип)", re.I | re.U)
_FILLER = re.compile(
    r"^(нужн[оаы]?|надо|нужен|нужна|прошу|пожалуйста|хочу|можешь|можно|напечата\w*|распечата\w*|печата\w*|"
    r"сдела\w*|подготов\w*|нареж\w*|настро\w*|деталь|детали|это|вот|мне|для|меня|нам|штук\w*|шт)$", re.I | re.U)


_TR = [("shch", "щ"), ("sch", "щ"), ("sh", "ш"), ("ch", "ч"), ("zh", "ж"), ("kh", "х"), ("ts", "ц"), ("yo", "ё"),
       ("yu", "ю"), ("ya", "я"), ("ey", "ей"), ("ye", "е"), ("ei", "ей"), ("iy", "ий"), ("yy", "ый"), ("oy", "ой"), ("ay", "ай"),
       ("a", "а"), ("b", "б"), ("v", "в"), ("g", "г"), ("d", "д"), ("e", "е"), ("z", "з"), ("i", "и"), ("y", "ы"),
       ("j", "й"), ("k", "к"), ("l", "л"), ("m", "м"), ("n", "н"), ("o", "о"), ("p", "п"), ("r", "р"), ("s", "с"),
       ("t", "т"), ("u", "у"), ("f", "ф"), ("h", "х"), ("c", "к"), ("w", "в"), ("x", "кс"), ("q", "к")]


def from_translit(text):
    """«koleso dlya konveyera» → «колесо для конвейера», если так узнаётся деталь; иначе текст как был."""
    if not re.search(r"[a-z]", text or "", re.I) or re.search(r"[а-яё]", text or "", re.I):
        return text
    text = " ".join(w for w in text.split() if not _JUNK.match(w))
    out, low, i = "", text.lower(), 0
    while i < len(low):
        for a, b in _TR:
            if low.startswith(a, i):
                out += b
                i += len(a)
                break
        else:
            out += low[i]
            i += 1
    # Мягкий знак латиницей не пишут: частые слова поправляем.
    for a, b in (("пылник", "пыльник"), ("шрус", "ШРУС"), ("стелка", "стелька"), ("кулачек", "кулачок")):
        out = out.replace(a, b)
    return out if PART_NOUNS.search(out) else text


def name_from_text(text):
    """Название из слов пользователя: «Нужно напечатать ведущее колесо конвейерной ленты, 4 шт.» →
    «Ведущее колесо конвейерной ленты». Только если в тексте есть слово-деталь, иначе ""."""
    text = str(text or "").strip()
    if not text or not PART_NOUNS.search(text):
        return ""
    clause = ""
    for c in re.split(r"[,.;:!?\n]|\s\d+\s*(?:шт|штук)|\s[—–-]\s", text):
        if PART_NOUNS.search(c):
            clause = c
            break
    ws = [w for w in clause.split() if w]
    verb = False
    while ws and _FILLER.match(ws[0].strip("«»\"")):
        verb = verb or bool(re.match(r"^(напечата|распечата|печата|сдела|подготов|нареж|настро|нужн|надо|хочу|прошу)", ws[0], re.I))
        ws = ws[1:]
    if verb:  # «напечатай ведущую шестерню» → «ведущая шестерня»: до главного слова — в именительный падеж
        for i, w in enumerate(ws):
            low = w.lower()
            if low.endswith("ую"):
                ws[i] = w[:-2] + "ая"
            elif low.endswith("юю"):
                ws[i] = w[:-2] + "яя"
            elif PART_NOUNS.match(w):
                if low.endswith("у"):
                    ws[i] = w[:-1] + "а"
                elif low.endswith("ю"):
                    ws[i] = w[:-1] + "я"
                break
    # Хвост «в 5D», «из PETG», «2 штуки» — не часть названия.
    cut = []
    for w in ws:
        if re.match(r"^(\d|в$|во$|из$|на$|по$)", w, re.I) and cut:
            break
        cut.append(w)
    return short_name(" ".join(cut)) if cut else ""


class Events(object):
    def __init__(self):
        self._cond = threading.Condition()
        self._items = []
        self._seq = 0

    def emit(self, ev):
        with self._cond:
            self._seq += 1
            ev = dict(ev)
            ev["seq"] = self._seq
            self._items.append(ev)
            if len(self._items) > 500:
                self._items = self._items[-300:]
            self._cond.notify_all()

    def since(self, seq, timeout=25.0):
        end = time.time() + timeout
        with self._cond:
            if seq > self._seq:
                # Браузер помнит номер от прошлого запуска плагина — начинаем счёт заново.
                return [{"type": "reset", "seq": self._seq}]
            while True:
                out = [e for e in self._items if e["seq"] > seq]
                if out:
                    return out
                left = end - time.time()
                if left <= 0:
                    return []
                self._cond.wait(left)

    @property
    def seq(self):
        return self._seq


class Store(object):
    def __init__(self, data_dir):
        self.dir = data_dir
        self.parts_dir = os.path.join(data_dir, "parts")
        self.files_dir = os.path.join(data_dir, "files")
        for d in (self.parts_dir, self.files_dir):
            if not os.path.isdir(d):
                os.makedirs(d)
        self.lock = threading.RLock()
        self.events = Events()
        self.parts = {}
        self.order = []
        self.files = {}
        self.folders = []  # папки для чатов: [{"id", "name"}] — порядок как создавали
        self._load()
        self._load_folders()

    # ----------------------------------------------------------- детали
    def _load(self):
        items = []
        for fn in os.listdir(self.parts_dir):
            if not fn.endswith(".json"):
                continue
            try:
                with open(os.path.join(self.parts_dir, fn), "r", encoding="utf-8") as f:
                    p = json.load(f)
                if is_empty(p):  # пустая «Новая деталь» с прошлого запуска — не храним
                    os.remove(os.path.join(self.parts_dir, fn))
                    continue
                items.append(p)
            except (OSError, ValueError):
                continue
        items.sort(key=lambda p: p.get("updated", 0), reverse=True)
        for p in items:
            # Незавершённая работа после перезапуска — уже не работа.
            if p.get("status") == "work":
                p["status"] = "draft"
            if not p.get("name_user"):  # длинные названия из прежних версий — сокращаем
                p["name"] = short_name(p.get("name")) or "Новая деталь"
            self.parts[p["id"]] = p
            self.order.append(p["id"])
            for fi in p.get("files", []):
                # В детали файл хранится без пути — путь восстанавливаем по папке файлов.
                path = os.path.join(self.files_dir, fi["id"], safe_name(fi.get("name", "")))
                if os.path.isfile(path):
                    self.files[fi["id"]] = dict(fi, path=path, kind=fi.get("kind") or file_kind(fi.get("name", "")))
        self._sweep()

    def _sweep(self, age=24 * 3600):
        """Удаляет загрузки старше суток, которые так и не попали ни в одну деталь."""
        used = set(self.files)
        now = time.time()
        try:
            names = os.listdir(self.files_dir)
        except OSError:
            return
        for fid in names:
            d = os.path.join(self.files_dir, fid)
            try:
                if fid not in used and now - os.path.getmtime(d) > age:
                    shutil.rmtree(d, ignore_errors=True)
            except OSError:
                pass

    def save(self, part):
        part["updated"] = time.time()
        path = os.path.join(self.parts_dir, part["id"] + ".json")
        if is_empty(part):
            # Пока в чате ничего нет, деталь живёт только в памяти.
            try:
                os.remove(path)
            except OSError:
                pass
            return
        tmp = path + ".tmp"
        with self.lock:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(part, f, ensure_ascii=False)
            os.replace(tmp, path)

    def new_part(self, name="Новая деталь"):
        with self.lock:
            # Уже есть пустая деталь — открываем её, а не плодим новые.
            for old in self.order:
                if is_empty(self.parts[old]):
                    self.order.remove(old)
                    self.order.insert(0, old)
                    part = self.parts[old]
                    part["name"] = name
                    part.pop("name_user", None)
                    break
            else:
                part = None
        if part is not None:
            self.events.emit({"type": "parts"})
            return part
        with self.lock:
            pid = "p" + uuid.uuid4().hex[:8]
            part = {"id": pid, "name": name, "kind": "new", "mode": "", "material": "", "status": "draft",
                    "created": time.time(), "updated": time.time(), "files": [], "messages": [],
                    "suggest": [], "passport": None, "state": {}, "llm": []}
            self.parts[pid] = part
            self.order.insert(0, pid)
            self.save(part)
        self.events.emit({"type": "parts"})
        return part

    def get(self, pid):
        return self.parts.get(pid)

    def delete(self, pid):
        with self.lock:
            self.parts.pop(pid, None)
            if pid in self.order:
                self.order.remove(pid)
            try:
                os.remove(os.path.join(self.parts_dir, pid + ".json"))
            except OSError:
                pass
        self.events.emit({"type": "parts"})

    # ----------------------------------------------------------- папки чатов
    def _folders_path(self):
        return os.path.join(self.dir, "folders.json")

    def _load_folders(self):
        try:
            with open(self._folders_path(), "r", encoding="utf-8") as f:
                data = json.load(f)
            self.folders = [{"id": str(x["id"]), "name": str(x["name"])[:60]} for x in data if x.get("id") and x.get("name")]
        except (OSError, ValueError, TypeError, KeyError):
            self.folders = []
        ids = set(x["id"] for x in self.folders)
        for p in self.parts.values():  # папку удалили, а деталь о ней помнит — деталь без папки
            if p.get("folder") and p["folder"] not in ids:
                p.pop("folder", None)

    def _save_folders(self):
        tmp = self._folders_path() + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self.folders, f, ensure_ascii=False)
        os.replace(tmp, self._folders_path())

    def add_folder(self, name):
        name = (name or "").strip()[:60]
        if not name:
            raise ValueError("Введите название папки")
        with self.lock:
            fo = {"id": "f" + uuid.uuid4().hex[:8], "name": name}
            self.folders.append(fo)
            self._save_folders()
        self.events.emit({"type": "parts"})
        return fo

    def rename_folder(self, fid, name):
        name = (name or "").strip()[:60]
        if not name:
            raise ValueError("Введите название папки")
        with self.lock:
            fo = [x for x in self.folders if x["id"] == fid]
            if not fo:
                raise KeyError(fid)
            fo[0]["name"] = name
            self._save_folders()
        self.events.emit({"type": "parts"})
        return fo[0]

    def delete_folder(self, fid):
        """Удаляет папку; чаты из неё остаются — просто без папки."""
        with self.lock:
            self.folders = [x for x in self.folders if x["id"] != fid]
            self._save_folders()
            moved = [p for p in self.parts.values() if p.get("folder") == fid]
            for p in moved:
                p.pop("folder", None)
        for p in moved:
            self.save(p)
        self.events.emit({"type": "parts"})

    def set_folder(self, part, fid):
        if fid and fid not in set(x["id"] for x in self.folders):
            raise KeyError(fid)
        if fid:
            part["folder"] = fid
        else:
            part.pop("folder", None)
        self.save(part)
        self.events.emit({"type": "parts"})

    def summaries(self):
        out = []
        with self.lock:
            parts = [self.parts.get(pid) for pid in list(self.order)]
        for p in parts:
            if p is None:
                continue
            out.append({"id": p["id"], "name": p["name"], "kind": p.get("kind", "new"), "mode": p.get("mode", ""),
                        "material": p.get("material", ""), "status": p.get("status", "draft"), "empty": is_empty(p),
                        "folder": p.get("folder") or ""})
        return out

    def public(self, part):
        """Деталь для браузера: без служебного состояния и истории LLM.

        Ход ассистента меняет деталь в своём потоке, а браузер может запросить её в тот же момент:
        копирование тогда падает с «dictionary changed size during iteration». Повторяем — изменения
        короткие, следующая попытка проходит.
        """
        for attempt in range(5):
            try:
                out = copy.deepcopy(dict((k, v) for k, v in list(part.items()) if k not in ("state", "llm")))
                break
            except RuntimeError:
                if attempt == 4:
                    raise
                time.sleep(0.01)
        for m in out.get("messages", []):
            for b in m.get("blocks", []) or []:
                b.pop("snapshot", None)
        return out

    def changed(self, part, save=True):
        if save:
            self.save(part)
        self.events.emit({"type": "part", "id": part["id"]})

    # ----------------------------------------------------------- файлы
    def add_file(self, name, data):
        name = safe_name(name)
        kind = file_kind(name)
        if kind == "other":
            raise ValueError("Формат %s не поддерживается: пришлите STL, OBJ, 3MF, DOCX, TXT, JPG или PNG"
                             % (os.path.splitext(name)[1] or name))
        limit = MAX_MODEL if kind == "model" else MAX_OTHER
        if len(data) > limit:
            raise ValueError("Файл %s больше %d МБ" % (name, limit // 1048576))
        fid = "f" + uuid.uuid4().hex[:10]
        d = os.path.join(self.files_dir, fid)
        os.makedirs(d)
        path = os.path.join(d, name)
        with open(path, "wb") as f:
            f.write(data)
        info = {"id": fid, "name": name, "size": human_size(len(data)), "bytes": len(data), "kind": kind, "path": path}
        with self.lock:
            self.files[fid] = info
        return info

    def add_file_from_path(self, src):
        with open(src, "rb") as f:
            return self.add_file(os.path.basename(src), f.read())

    def file(self, fid):
        return self.files.get(fid)

    def cleanup_files(self):
        used = set()
        for p in self.parts.values():
            for fi in p.get("files", []):
                used.add(fi["id"])
        for fid in os.listdir(self.files_dir):
            if fid not in used:
                shutil.rmtree(os.path.join(self.files_dir, fid), ignore_errors=True)
                self.files.pop(fid, None)


def is_empty(part):
    """В чат ещё ничего не добавлено: ни сообщений, ни файлов."""
    return not part.get("messages") and not part.get("files")
