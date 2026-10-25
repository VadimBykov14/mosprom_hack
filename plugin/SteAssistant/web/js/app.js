/* STE Assistant — логика страницы: детали, лента, поле ввода, паспорт, картотека, настройки. */
(function () {
  'use strict';
  var $ = function (id) { return document.getElementById(id); };
  var esc = R.esc, ico = R.ico;

  var S = {
    view: 'chat', conn: {}, parts: [], active: null, part: null, query: '', settings: {}, catalog: [],
    pending: [], uploading: 0,
    seenQ: {},
    ui: { open: {}, step: {}, prow: {}, rvSel: {}, askSel: {}, qSel: {}, planOpen: {}, planWhy: {}, editReq: null, editKind: null, addReq: false },
    cat: { q: '', filter: 'all', sel: [] }, stab: 'llm', check: null, importRep: null, mcp: null, tplErr: null,
    folders: [], fold: {}, moving: null
  };
  try { S.fold = JSON.parse(localStorage.getItem('ste-fold') || '{}') || {}; } catch (e) { S.fold = {}; }

  // ------------------------------------------------------------ общие
  function toast(text) {
    var t = $('toast'); t.textContent = text; t.hidden = false;
    clearTimeout(toast._t); toast._t = setTimeout(function () { t.hidden = true; }, 3500);
  }
  function fail(e) { toast(e && e.message ? e.message : String(e)); }
  function theme(set) {
    var root = document.documentElement;
    var cur = root.getAttribute('data-theme') || (matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light');
    if (set) { cur = cur === 'dark' ? 'light' : 'dark'; root.setAttribute('data-theme', cur); try { localStorage.setItem('ste-theme', cur); } catch (e) {} }
    $('themeBtn').innerHTML = ico(cur === 'dark' ? 'i-sun' : 'i-moon');
  }
  try { var th = localStorage.getItem('ste-theme'); if (th) document.documentElement.setAttribute('data-theme', th); } catch (e) {}

  // ------------------------------------------------------------ отрисовка
  function renderTop() {
    $('status').innerHTML = R.status(S.conn);
    $('scope').textContent = S.conn.printer || 'ассистент печати 3D/5D';
    document.querySelectorAll('.nav button').forEach(function (b) { b.setAttribute('aria-current', b.dataset.v === S.view ? 'page' : 'false'); });
    ['chat', 'catalog', 'settings'].forEach(function (v) { $('view-' + v).hidden = v !== S.view; });
    $('app').classList.toggle('on-chat', S.view === 'chat');
    $('mbarTitle').textContent = 'STE Assistant';
  }
  function renderSide() {
    var hadFocus = document.activeElement && document.activeElement.id === 'partSearch';
    $('side').innerHTML = R.side(S.parts, S.view === 'chat' ? S.active : null, S.query, S.folders, S.fold);
    if (hadFocus) { var el = $('partSearch'); el.focus(); el.setSelectionRange(el.value.length, el.value.length); }
  }
  function nearBottom() { var f = $('feed'); return f.scrollHeight - f.scrollTop - f.clientHeight < 160; }
  function renderChat(forceScroll) {
    var p = S.part;
    var stick = forceScroll || nearBottom();
    $('chatHead').innerHTML = R.chatHead(p);
    $('chatHead').hidden = !p;
    $('view-chat').classList.toggle('welcome-mode', !!p && !p.messages.length);
    var bars = Array.prototype.map.call($('feedIn').querySelectorAll('.progress.live i'), function (i) { return i.style.width; });
    var before = feedShape();
    patchFeed(p);
    markNew(before);
    // Поле правки в паспорте, в которое сейчас пишут, переживает перерисовку.
    var ae = document.activeElement, keep = ae && ae.id && $('pass').contains(ae) && ae.tagName === 'INPUT' ? [ae.id, ae.value, ae.selectionStart] : null;
    var passHtml = R.passport(p, S.ui);
    if (passHtml !== S.passHtml || !$('pass').firstChild) {
      var was = passValues();
      $('pass').innerHTML = passHtml; S.passHtml = passHtml;
      softenPass(was);
    }
    if (keep && $(keep[0])) { var ne = $(keep[0]); ne.value = keep[1]; ne.focus(); try { ne.setSelectionRange(keep[2], keep[2]); } catch (e) {} }
    // Выдвижная «Все настройки» показывает свежие значения, когда ассистент закончил правку.
    var stNow = p && p.status;
    if ($('allList') && S.lastStatus === 'work' && stNow !== 'work') loadAll();
    S.lastStatus = stNow;
    keepMotion($('feedIn'), bars);
    renderComposer();
    var f = $('feed');
    if (p && !p.messages.length) { f.scrollTop = 0; return; }  // начальный экран — с начала
    // Карточка вопросов: показать её начало, а не прокручивать мимо вопросов к кнопкам внизу.
    var qBtn = $('feedIn').querySelector('.qcard:not(.done) [data-act="q-submit"]');
    var qid = qBtn && qBtn.getAttribute('data-q');
    if (qid && (forceScroll || !S.seenQ[qid])) {
      S.seenQ[qid] = true;
      var card = qBtn.closest('.qcard');
      f.scrollTop = Math.max(0, f.scrollTop + card.getBoundingClientRect().top - f.getBoundingClientRect().top - 12);
      return;
    }
    if (stick) f.scrollTop = f.scrollHeight;
  }
  // Значения паспорта до перерисовки: строка параметра (по ключу), время, масса и цена, что печатаем.
  var PASS_VALS = ['.answer .fig', '.answer .sub', '.pp-what b', '.pp-what .mode strong'];
  function passValues() {
    var out = {}, root = $('pass');
    Array.prototype.forEach.call(root.querySelectorAll('[data-act="prow"]'), function (b) {
      var v = b.querySelector('.v'); if (v) out['k:' + b.getAttribute('data-k')] = v.textContent;
    });
    PASS_VALS.forEach(function (sel) { var el = root.querySelector(sel); if (el) out[sel] = el.textContent; });
    return out;
  }
  // Изменившиеся значения мягко проявляются; при первом показе паспорта — без анимации.
  function softenPass(was) {
    if (!was || !Object.keys(was).length) return;
    var root = $('pass');
    Array.prototype.forEach.call(root.querySelectorAll('[data-act="prow"]'), function (b) {
      var k = 'k:' + b.getAttribute('data-k'), v = b.querySelector('.v');
      if (v && k in was && was[k] !== v.textContent) v.classList.add('val-upd');
    });
    PASS_VALS.forEach(function (sel) {
      var el = root.querySelector(sel);
      if (el && sel in was && was[sel] !== el.textContent) el.classList.add('val-upd');
    });
  }
  // Заменить в ленте только изменившиеся сообщения. Нетронутые узлы остаются как есть: не мигают,
  // не теряют раскрытые карточки и не начинают анимацию заново. Возвращает новые узлы.
  function patchFeed(p) {
    var root = $('feedIn'), items = R.feedItems(p, S.ui);
    var cache = S.feedCache && S.feedCache.part === (p && p.id) && S.feedCache.items.length === root.children.length ? S.feedCache.items : [];
    var fresh = [];
    var tpl = document.createElement('template');
    items.forEach(function (html, i) {
      var cur = root.children[i];
      if (cur && cache[i] === html && !cur.hasAttribute('data-touched')) return;
      tpl.innerHTML = html;
      var node = tpl.content.firstElementChild;
      if (!node) return;
      if (cur) root.replaceChild(node, cur); else root.appendChild(node);
      fresh.push(node);
    });
    while (root.children.length > items.length) root.removeChild(root.lastElementChild);
    S.feedCache = { part: p && p.id, items: items };
    return fresh;
  }
  // Виджеты (ползунок, треугольник, счётчик) и поля меняют DOM сами. Такое сообщение при следующем
  // обновлении перерисовываем, даже если сервер прислал тот же HTML, — на экране останется правда.
  function touch(e) {
    var t = e.target, root = $('feedIn');
    if (!t || !t.closest || !root.contains(t) || !t.closest('[data-tri],[data-slider],[data-step],input,select,textarea')) return;
    var top = t; while (top && top.parentNode !== root) top = top.parentNode;
    if (top) top.setAttribute('data-touched', '');
  }
  ['pointerdown', 'input', 'change', 'keydown'].forEach(function (ev) { document.addEventListener(ev, touch, true); });
  // Что было в ленте до перерисовки: деталь, число сообщений, число карточек в последнем, текст плана.
  function feedShape() {
    var root = $('feedIn'), kids = root.children, last = kids[kids.length - 1];
    var body = last && last.querySelector('.body');
    var plan = root.querySelector('.plan');
    return { part: S.animPart, n: kids.length, blocks: body ? body.children.length : 0, plan: plan ? plan.textContent : null };
  }
  // Анимация только нового: сообщение, карточка в последнем сообщении, план, обновлённый на месте.
  function markNew(b) {
    var root = $('feedIn'), kids = root.children;
    var same = b.part === (S.part && S.part.id);
    S.animPart = S.part && S.part.id;
    if (!same || !kids.length) return;
    for (var i = b.n; i < kids.length; i++) kids[i].classList.add('anim-in');
    if (kids.length === b.n) {
      var body = kids[kids.length - 1].querySelector('.body');
      if (body) for (var j = b.blocks; j < body.children.length; j++) body.children[j].classList.add('anim-in');
    }
    var plan = root.querySelector('.plan');
    if (plan && b.plan !== null && plan.textContent !== b.plan && !plan.closest('.anim-in')) plan.classList.add('anim-upd');
  }
  // Изменившиеся сообщения перерисовываются при каждом обновлении (во время нарезки — раз в 2 с). Чтобы анимации
  // не начинались заново, сдвигаем их фазу по общим часам, а полосу ведём от прежней ширины.
  function keepMotion(root, oldWidths) {
    var now = Date.now();
    Array.prototype.forEach.call(root.querySelectorAll('.typing i'), function (el, i) {
      el.style.animationDelay = (-(now % 1400) / 1000 + (i % 3) * 0.18) + 's';
    });
    Array.prototype.forEach.call(root.querySelectorAll('.progress.live i'), function (el, i) {
      el.style.setProperty('--shimmer-delay', -(now % 1600) / 1000 + 's');
      var target = el.style.width, from = oldWidths[i];
      if (from && from !== target) {
        el.style.transition = 'none'; el.style.width = from;
        void el.offsetWidth;  // применить прежнюю ширину, затем плавно доехать до новой
        el.style.transition = ''; el.style.width = target;
      }
    });
  }
  function renderComposer() {
    var p = S.part;
    var running = p && p.status === 'work';
    var off = S.conn && S.conn.slicer === false;
    $('composer').classList.toggle('off', !!off);
    var pend = $('pending');
    pend.hidden = !S.pending.length;
    var ready = S.pending.filter(function (f) { return f.id; }).length;
    pend.innerHTML = S.pending.map(function (f, i) {
      var k = R.fileKind(f.name);
      var state = f.id ? '<span class="pst ok">' + ico('i-check', 'sm') + 'загружен' + (f.size ? ' · ' + esc(f.size) : '') + '</span>' :
        '<span class="pst">' + ico('i-spin', 'sm spin') + 'загружаю…</span>';
      return '<span class="pfile' + (f.id ? ' done' : ' up') + '"><span class="ext ' + k + '">' + esc(R.extOf(f)) + '</span><span class="pn"><span class="pname">' + esc(f.name) + '</span>' + state + '</span>' +
        '<button type="button" data-act="unpend" data-i="' + i + '" aria-label="Убрать файл ' + esc(f.name) + '">' + ico('i-x', 'sm') + '</button></span>';
    }).join('') + (S.pending.length ? '<span class="pend-sum" role="status">' + (S.uploading ? 'Загружаю ' + S.uploading + ' из ' + S.pending.length + '…' :
      ready + ' ' + R.plural(ready, 'файл загружен', 'файла загружены', 'файлов загружено') + ' — отправьте сразу или допишите задачу') + '</span>' : '');
    var text = $('msg').value.trim();
    var can = (text || S.pending.length) && p;
    var showStop = running && !$('msg').value.trim() && !S.pending.length;
    $('sendSlot').innerHTML = showStop ? '<button class="stop" type="button" data-act="stop"><svg class="ico sm" viewBox="0 0 24 24"><rect x="5" y="5" width="14" height="14" rx="3" fill="currentColor" stroke="none"/></svg>Остановить</button>' :
      '<button class="send" type="submit" aria-label="Отправить"' + (can ? '' : ' disabled') + '>' + ico('i-send') + '</button>';
    var narrow = window.innerWidth < 560;
    $('msg').placeholder = running ? (narrow ? 'Отправлю после ответа…' : 'Можно написать — сообщение отправится, когда ассистент закончит') :
      (narrow ? 'Опишите деталь…' : 'Опишите деталь, приложите модель и ТЗ…');
    // Режим «разбор печати по фото»: следующее сообщение с фото — разбор дефектов.
    var rvBar = $('rvMode');
    if (rvBar) { rvBar.hidden = !S.review; }
    if (S.review) $('msg').placeholder = 'Приложите фото детали и напишите, что не так (можно без текста)';
    var sug = (p && !running && p.suggest) || [];
    $('suggest').innerHTML = sug.map(function (s) {
      var act = s === 'Разобрать печать по фото' ? 'review-photo' : 'suggest';
      return '<button type="button" data-act="' + act + '" data-text="' + esc(s) + '">' + (act === 'review-photo' ? ico('i-camera', 'sm') : '') + esc(s) + '</button>';
    }).join('');
  }
  function render() {
    renderTop();
    renderSide();
    if (S.view === 'chat') renderChat();
    if (S.view === 'catalog') renderCatalog();
    if (S.view === 'settings') renderSettings();
  }

  // ------------------------------------------------------------ данные
  function loadState() {
    return Api.state().then(function (s) {
      S.conn = s.conn; S.parts = s.parts; S.folders = s.folders || []; S.settings = s.settings;
      renderTop(); renderSide();
      return s;
    });
  }
  function openPart(id, scroll) {
    S.active = id;
    try { localStorage.setItem('ste-part', id); } catch (e) {}
    return Api.getPart(id).then(function (p) {
      if (S.active !== id) return;
      S.part = p; renderSide(); renderChat(scroll !== false);
    }, fail);
  }
  var partTimer = null;
  function refreshPart() {
    if (partTimer) return;
    partTimer = setTimeout(function () {
      partTimer = null;
      if (!S.active) return;
      Api.getPart(S.active).then(function (p) {
        if (p.id !== S.active) return;
        S.part = p; renderChat(); syncSummary(p);
        if (S.afterRun && S.afterRun.id === p.id && p.status !== 'work') { var q = S.afterRun; S.afterRun = null; send(q.text); }
      }, function () {});
    }, 80);
  }
  function syncSummary(p) {
    S.parts.forEach(function (x) { if (x.id === p.id) { x.empty = !p.messages.length && !(p.files || []).length; x.status = p.status; x.name = p.name; x.mode = p.mode; x.material = p.material; x.kind = p.kind; } });
    renderSide();
  }
  function newPart(folder) {
    folder = folder || '';
    var cur = S.part && (S.parts || []).filter(function (x) { return x.id === S.part.id; })[0];
    if (S.part && !S.part.messages.length && !(S.part.files || []).length && (!cur || (cur.folder || '') === folder)) {
      setView('chat'); $('msg').focus(); return Promise.resolve(S.part);  // уже открыт пустой чат в той же папке
    }
    if (folder) { S.fold[folder] = false; saveFold(); }
    return Api.newPart(folder).then(function (p) {
      S.parts = S.parts.filter(function (x) { return x.id !== p.id; });
      S.parts.unshift({ id: p.id, name: p.name, kind: p.kind, mode: '', material: '', status: 'draft', empty: true, folder: folder });
      S.pending = []; setView('chat');
      return openPart(p.id);
    }, fail);
  }

  Api.on(function (ev) {
    if (ev.type === 'part' && ev.id === S.active) refreshPart();
    else if (ev.type === 'part' || ev.type === 'parts' || ev.type === 'running') {
      Api.state().then(function (s) { S.parts = s.parts; S.folders = s.folders || []; S.conn = s.conn; renderTop(); renderSide(); });
      if (ev.id === S.active) refreshPart();
    } else if (ev.type === 'conn' || ev.type === 'offline') {
      if (ev.type === 'offline') { S.conn = Object.assign({}, S.conn, { slicer: false }); renderTop(); }
      else { loadState(); if (S.active) refreshPart(); }
    } else if (ev.type === 'auth') {
      if (!S.authLost) {
        S.authLost = true;
        S.conn = Object.assign({}, S.conn, { slicer: false }); renderTop();
        overlay('<section class="modal" role="alertdialog" aria-label="Чат устарел"><div class="mhead"><h2>Откройте чат заново</h2></div>' +
          '<div class="mbody"><p>STE Slicer перезапускался, и у этой вкладки старый ключ сессии. Откройте чат из меню «Расширения» → «AI Assistant» → «Открыть чат» — переписка сохранилась.</p></div></section>');
      }
    } else if (ev.type === 'open' && ev.what === 'save' && ev.id === S.active) openSave(false);
  });

  // ------------------------------------------------------------ файлы и отправка
  function addFiles(list) {
    var files = Array.prototype.slice.call(list || []);
    if (S.pending.length + files.length > 10) { toast('Не больше 10 файлов в сообщении'); files = files.slice(0, 10 - S.pending.length); }
    files.forEach(function (file) {
      var k = R.fileKind(file.name);
      if (k === 'other') { toast('Формат ' + file.name + ' не поддерживается: пришлите STL, OBJ, 3MF, DOCX, TXT, JPG или PNG'); return; }
      if (file.size > (k === 'model' ? 100 : 20) * 1048576) { toast('Файл ' + file.name + ' больше ' + (k === 'model' ? 100 : 20) + ' МБ'); return; }
      var entry = { name: file.name, id: null };
      S.pending.push(entry); S.uploading++;
      renderComposer();
      Api.upload(file).then(function (info) { entry.id = info.id; entry.size = info.size; }, function (e) {
        fail(e); S.pending.splice(S.pending.indexOf(entry), 1);
      }).then(function () {
        S.uploading--; renderComposer();
        if (!S.uploading && S.queued != null) { var q = S.queued; S.queued = null; send(q); }
      });
    });
  }
  function send(text) {
    var p = S.part;
    if (!p) return;
    if (p.status === 'work') {
      S.afterRun = { id: p.id, text: text };
      return;
    }
    if (S.uploading) { S.queued = text; return; }
    var ready = S.pending.filter(function (f) { return f.id; });
    var ids = ready.map(function (f) { return f.id; });
    if (!text && !ids.length) return;
    var mode = S.review ? 'review' : '';
    if (mode && !text) text = 'Разбери печать по фото';
    Api.send(p.id, text, ids, '', mode).then(function () { S.pending = []; S.review = false; $('msg').value = ''; autosize(); renderComposer(); }, fail);
  }
  function prefill(text) {
    var m = $('msg');
    var cur = m.value.trim();
    m.value = cur && cur !== text ? cur + ' ' + text : text;
    if (!/[\s—:]$/.test(m.value)) m.value += ' ';
    autosize(); renderComposer();
    m.focus(); m.setSelectionRange(m.value.length, m.value.length);
    $('composer').classList.add('flash'); setTimeout(function () { $('composer').classList.remove('flash'); }, 900);
  }
  function autosize() { var t = $('msg'); t.style.height = '32px'; t.style.height = Math.min(Math.max(t.scrollHeight, 32), 180) + 'px'; }

  $('composer').addEventListener('submit', function (e) { e.preventDefault(); send($('msg').value.trim()); });
  $('msg').addEventListener('keydown', function (e) {
    if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) { e.preventDefault(); send($('msg').value.trim()); }
  });
  $('msg').addEventListener('input', function () { autosize(); renderComposer(); });
  $('fileIn').addEventListener('change', function (e) { addFiles(e.target.files); e.target.value = ''; });
  var drag = 0;
  document.addEventListener('dragenter', function (e) { if (S.view !== 'chat') return; e.preventDefault(); drag++; $('composer').classList.add('drag'); });
  document.addEventListener('dragleave', function () { drag = Math.max(0, drag - 1); if (!drag) $('composer').classList.remove('drag'); });
  document.addEventListener('dragover', function (e) { if (S.view === 'chat') e.preventDefault(); });
  document.addEventListener('drop', function (e) {
    if (S.view !== 'chat') return;
    e.preventDefault(); drag = 0; $('composer').classList.remove('drag');
    if (!S.part) { newPart().then(function () { addFiles(e.dataTransfer.files); }); } else addFiles(e.dataTransfer.files);
  });
  document.addEventListener('input', function (e) {
    if (e.target.id === 'partSearch') { S.query = e.target.value; renderSide(); }
    if (e.target.id === 'catSearch') { S.cat.q = e.target.value; renderCatalogGrid(); }
    if (e.target.id === 'tplInput') { S.settings.template = e.target.value; renderTemplatePreview(); }
  });

  // ------------------------------------------------------------ окна
  function overlay(html, cls) {
    $('overlay').innerHTML = html; $('scrim').hidden = !html;
    var first = $('overlay').querySelector('input, button'); if (first) first.focus();
  }
  function closeOverlay() { overlay(''); var app = $('app'); app.dataset.drawer = ''; $('scrim').hidden = true; }
  document.addEventListener('keydown', function (e) { if (e.key === 'Escape') closeOverlay(); });
  document.addEventListener('keydown', function (e) {
    if (e.target && e.target.id === 'rnInput' && e.key === 'Enter') { e.preventDefault(); var b = document.querySelector('[data-act="rename-do"]'); if (b) renameDo(b.dataset.id); }
    if (e.target && e.target.id === 'fdInput' && e.key === 'Enter') { e.preventDefault(); var fb = document.querySelector('[data-act="folder-save"]'); if (fb) folderSave(fb.dataset.id); }
  });

  function findReview(p, id) {
    var out = null;
    (p && p.messages || []).forEach(function (m) { (m.blocks || []).forEach(function (b) { if (b.type === 'review' && b.id === id) out = b; }); });
    return out;
  }

  // Enter в поле правки параметра — то же, что «Применить».
  document.addEventListener('keydown', function (e) {
    var t = e.target;
    if (e.key !== 'Enter' || !t || !t.closest || !t.closest('.ped')) return;
    var b = t.closest('.ped').querySelector('[data-act="pset-field"]');
    if (b) { e.preventDefault(); b.click(); }
  });

  // ------------------------------------------------------------ все настройки STE Slicer
  var allTimer = null;
  function allSettings() {
    var p = S.part;
    overlay('<aside class="drawer" role="dialog" aria-label="Все настройки STE Slicer"><div class="mhead"><h2>Все настройки STE Slicer</h2>' +
      '<button class="iconbtn" type="button" data-act="close" aria-label="Закрыть">' + ico('i-x') + '</button></div>' +
      '<div class="dbody"><label class="sr" for="allQ">Поиск параметра</label><div class="search"><span>' + ico('i-search', 'sm') + '</span>' +
      '<input class="input" id="allQ" placeholder="Скорость, температура, откат, обдув…" value="' + esc(S.allq || '') + '"></div>' +
      '<p class="help">Значение проверяет STE Slicer: что он не примет, вернётся с причиной. Без запроса — то, что уже изменено вручную.</p>' +
      '<div id="allList" class="all-list"><p class="muted">Ищу…</p></div></div></aside>');
    var q = $('allQ'); q.focus(); q.setSelectionRange(q.value.length, q.value.length);
    loadAll();
  }
  function loadAll() {
    var p = S.part, q = S.allq || '';
    Api.slicerSettings(q).then(function (res) {
      var box = $('allList'); if (!box || q !== (S.allq || '')) return;
      if (!res.items.length) { box.innerHTML = '<p class="muted">' + (q ? 'Ничего не нашлось по «' + esc(q) + '».' : 'Вручную ничего не менялось. Начните вводить название параметра.') + '</p>'; return; }
      var busy = !p || p.status === 'work' || p.status === 'ask', cat = '';
      box.innerHTML = res.items.map(function (it) {
        var h = it.category !== cat ? '<h3>' + esc(it.category) + '</h3>' : ''; cat = it.category;
        var e = { type: it.type, unit: R.UNITS[it.unit] || it.unit, min: it.min, max: it.max, options: it.options, value: it.value };
        return h + '<div class="all-row' + (it.changed ? ' changed' : '') + '"><div class="all-h"><b>' + esc(it.label) + '</b><span class="v">' + esc(it.text) + '</span></div>' +
          (it.description ? '<small>' + esc(it.description) + '</small>' : '') + '<span class="mono key">' + esc(it.key) + '</span>' +
          (it.enabled === false ? '<small class="off">Сейчас не действует при текущих настройках.</small>' : '') + R.editor(it.key, e, busy) +
          (it.changed && p ? '<button class="btn text sm" type="button" data-act="preset" data-key="' + esc(it.key) + '">' + ico('i-undo', 'sm') + 'Вернуть исходное</button>' : '') + '</div>';
      }).join('') + (res.total > res.items.length ? '<p class="muted">Показаны ' + res.items.length + ' из ' + res.total + ' — уточните запрос.</p>' : '');
    }, function (e) { var box = $('allList'); if (box) box.innerHTML = '<p class="muted">' + esc(e.message) + '</p>'; });
  }
  document.addEventListener('input', function (e) {
    if (e.target.id !== 'allQ') return;
    S.allq = e.target.value; clearTimeout(allTimer); allTimer = setTimeout(loadAll, 200);
  });

  // ------------------------------------------------------------ ширина левой панели
  var SIDE_MIN = 180, SIDE_MAX = 440, SIDE_DEF = 228;
  function setSideWidth(w, save) {
    w = Math.round(Math.max(SIDE_MIN, Math.min(SIDE_MAX, w)));
    $('app').style.setProperty('--side-w', w + 'px');
    $('sideResize').setAttribute('aria-valuenow', w);
    if (save) { try { localStorage.setItem('ste-side-w', String(w)); } catch (e) {} }
  }
  (function () {
    var h = $('sideResize'); if (!h) return;
    var w0 = SIDE_DEF;
    try { w0 = +localStorage.getItem('ste-side-w') || SIDE_DEF; } catch (e) {}
    setSideWidth(w0, false);
    h.addEventListener('pointerdown', function (e) {
      e.preventDefault(); h.setPointerCapture(e.pointerId);
      var startX = e.clientX, startW = $('sideFrame').getBoundingClientRect().width;
      document.body.classList.add('resizing');
      function move(ev) { setSideWidth(startW + ev.clientX - startX, false); }
      function up(ev) {
        h.removeEventListener('pointermove', move); h.removeEventListener('pointerup', up); h.removeEventListener('pointercancel', up);
        document.body.classList.remove('resizing');
        setSideWidth(startW + ev.clientX - startX, true);
      }
      h.addEventListener('pointermove', move); h.addEventListener('pointerup', up); h.addEventListener('pointercancel', up);
    });
    h.addEventListener('dblclick', function () { setSideWidth(SIDE_DEF, true); });
    h.addEventListener('keydown', function (e) {
      var cur = $('sideFrame').getBoundingClientRect().width;
      if (e.key === 'ArrowLeft') { e.preventDefault(); setSideWidth(cur - 16, true); }
      if (e.key === 'ArrowRight') { e.preventDefault(); setSideWidth(cur + 16, true); }
      if (e.key === 'Home') { e.preventDefault(); setSideWidth(SIDE_DEF, true); }
    });
  })();

  function openSave(gcodeOnly) {
    var p = S.part; if (!p) return;
    Api.saveName(p.id).then(function (s) {
      var proj = !gcodeOnly;
      overlay('<section class="modal" role="dialog" aria-label="Сохранить результат"><div class="mhead"><h2>Сохранить результат</h2>' +
        '<button class="iconbtn plain" type="button" data-act="close" aria-label="Закрыть">' + ico('i-x') + '</button></div><div class="mbody">' +
        '<div class="fields2"><label class="chk' + (proj ? ' on' : '') + '"><input type="checkbox" id="svProject"' + (proj ? ' checked' : '') + '><span><b>Проект 3MF</b><span>модель, положение и все настройки</span></span></label>' +
        '<label class="chk on"><input type="checkbox" id="svGcode" checked><span><b>G-code</b><span>' + (s.sliced ? 'для ' + esc(S.conn.printer || 'принтера') + ' · ' + esc(s.time) : 'нарежу перед сохранением') + '</span></span></label></div>' +
        '<div class="field"><label for="svFolder">Папка</label><div class="pick-row"><input class="input" id="svFolder" value="' + esc(s.folder) + '">' +
        '<button class="btn" type="button" data-act="choose-folder">' + ico('i-folder', 'sm') + 'Выбрать…</button></div></div>' +
        '<div class="field"><label for="svName">Имя файла</label><input class="input mono" id="svName" value="' + esc(s.name) + '"><span class="help" style="flex-wrap:wrap">По шаблону <span class="mono" style="font-size:12px">' + esc(s.template) + '</span>' +
        '<a href="#" data-act="view" data-v="settings" data-tab="names">изменить шаблон</a></span></div>' +
        '<div class="note info inline-note">' + ico('i-archive') + '<span>Запись появится в картотеке — потом можно взять её как шаблон.</span></div></div>' +
        '<div class="mfoot"><button class="btn" type="button" data-act="close">Отмена</button><button class="btn primary" type="button" data-act="save-do">Сохранить</button></div></section>');
    }, fail);
  }
  // Системный проводник открывает STE Slicer — окно может появиться поверх слайсера, а не браузера.
  function chooseFolder(btn) {
    var inp = $('svFolder');
    btn.disabled = true;
    toast('Открываю выбор папки — окно появится поверх STE Slicer');
    Api.chooseFolder(inp.value.trim()).then(function (r) {
      btn.disabled = false;
      if (r && r.path) inp.value = r.path;
    }, function (e) { btn.disabled = false; fail(e); });
  }
  function doSave() {
    var opts = { project: $('svProject').checked, gcode: $('svGcode').checked, folder: $('svFolder').value.trim(), name: $('svName').value.trim() };
    if (!opts.project && !opts.gcode) { toast('Выберите, что сохранить'); return; }
    Api.save(S.part.id, opts).then(function () { closeOverlay(); toast('Сохраняю…'); }, fail);
  }

  // Итог виджета (точка треугольника, ползунок, счётчик): в карточке вопросов — выбор, в плане — правка.
  if (window.Wd) Wd.commit = function (kind, el, value) {
    var p = S.part;
    if (!p) return;
    var q = el.getAttribute('data-q'), i = el.getAttribute('data-i'), k = el.getAttribute('data-k');
    if (q && i) {
      var qs = S.ui.qSel[q] || (S.ui.qSel[q] = {});
      qs[i] = [String(value)];
      var card = el.closest('.qcard');
      var btn = card && card.querySelector('[data-act="q-submit"]');
      if (btn) btn.disabled = false;
      return;
    }
    if (k) Api.planEdit(p.id, k, kind === 'slider' ? value + ' %' : String(value)).then(null, fail);
  };

  function findBlock(p, id) {
    var found = null;
    ((p && p.messages) || []).forEach(function (m) { (m.blocks || []).forEach(function (b) { if (b.id === id) found = b; }); });
    return found;
  }
    function deleteDialog(id) {
    var p = (S.parts || []).filter(function (x) { return x.id === id; })[0] || (S.part && S.part.id === id ? S.part : null);
    if (!p) return;
    var busy = p.status === 'work';
    overlay('<section class="modal" role="dialog" aria-label="Удалить чат"><div class="mhead"><h2>Удалить чат?</h2><button class="iconbtn plain" type="button" data-act="close" aria-label="Закрыть">' + ico('i-x') + '</button></div>' +
      '<div class="mbody"><p style="margin:0">«<b>' + esc(p.name) + '</b>» — переписка и файлы этой детали удалятся без возможности восстановления.</p>' +
      '<p class="muted" style="margin:8px 0 0;font-size:13px">Сохранённые 3MF и G-code и запись в картотеке останутся.' + (busy ? ' Ассистент сейчас работает над деталью — сначала остановлю его.' : '') + '</p></div>' +
      '<div class="mfoot"><button class="btn" type="button" data-act="close">Отмена</button><button class="btn danger solid" type="button" data-act="part-delete" data-id="' + esc(p.id) + '">Удалить чат</button></div></section>');
  }
  function deletePart(id) {
    if (!id) return;
    var wasActive = S.part && S.part.id === id;
    Api.deletePart(id).then(function () {
      closeOverlay(); toast('Чат удалён');
      if (wasActive) { S.part = null; S.active = null; }
      return loadState().then(function (s) {
        if (!wasActive) return;
        if (s.parts[0]) openPart(s.parts[0].id); else newPart();
      });
    }).then(null, fail);
  }
  // ------------------------------------------------------------ папки чатов
  function saveFold() { try { localStorage.setItem('ste-fold', JSON.stringify(S.fold)); } catch (e) {} }
  function folderById(id) { return (S.folders || []).filter(function (f) { return f.id === id; })[0]; }
  function folderDialog(id) {
    var f = id ? folderById(id) : null;
    overlay('<section class="modal" role="dialog" aria-label="' + (f ? 'Переименовать папку' : 'Новая папка') + '"><div class="mhead"><h2>' + (f ? 'Название папки' : 'Новая папка') + '</h2><button class="iconbtn plain" type="button" data-act="close" aria-label="Закрыть">' + ico('i-x') + '</button></div>' +
      '<div class="mbody"><label class="sr" for="fdInput">Название папки</label><input class="input" id="fdInput" maxlength="60" placeholder="Например, «Заказ Krones» или «Оснастка цеха 2»" value="' + esc(f ? f.name : '') + '">' +
      (S.moving ? '<p class="muted" style="margin:8px 0 0;font-size:13px">Чат сразу переложу в эту папку.</p>' : '') + '</div>' +
      '<div class="mfoot"><button class="btn" type="button" data-act="close">Отмена</button><button class="btn primary" type="button" data-act="folder-save" data-id="' + esc(id || '') + '">' + (f ? 'Сохранить' : 'Создать') + '</button></div></section>');
    var inp = $('fdInput'); if (inp) { inp.focus(); inp.select(); }
  }
  function folderSave(id) {
    var name = $('fdInput').value.trim();
    if (!name) { toast('Введите название папки'); return; }
    var pr = id ? Api.renameFolder(id, name) : Api.addFolder(name);
    pr.then(function (f) {
      closeOverlay();
      if (id) S.folders.forEach(function (x) { if (x.id === id) x.name = f.name; });
      else S.folders.push(f);
      var mv = S.moving; S.moving = null;
      if (!id && mv) return movePart(mv, f.id);
      renderSide();
    }, fail);
  }
  function folderDeleteDialog(id) {
    var f = folderById(id); if (!f) return;
    var n = (S.parts || []).filter(function (p) { return p.folder === id && !p.empty; }).length;
    overlay('<section class="modal" role="dialog" aria-label="Удалить папку"><div class="mhead"><h2>Удалить папку?</h2><button class="iconbtn plain" type="button" data-act="close" aria-label="Закрыть">' + ico('i-x') + '</button></div>' +
      '<div class="mbody"><p style="margin:0">Папка «<b>' + esc(f.name) + '</b>» удалится. ' + (n ? 'Чаты из неё (' + n + ') останутся в списке деталей — без папки.' : 'Она пустая.') + '</p></div>' +
      '<div class="mfoot"><button class="btn" type="button" data-act="close">Отмена</button><button class="btn danger solid" type="button" data-act="folder-delete" data-id="' + esc(id) + '">Удалить папку</button></div></section>');
  }
  function folderDelete(id) {
    Api.deleteFolder(id).then(function () {
      closeOverlay();
      S.folders = S.folders.filter(function (f) { return f.id !== id; });
      S.parts.forEach(function (p) { if (p.folder === id) p.folder = ''; });
      renderSide();
    }, fail);
  }
  function movePart(pid, folder) {
    closeMenu();
    return Api.movePart(pid, folder).then(function () {
      S.parts.forEach(function (p) { if (p.id === pid) p.folder = folder || ''; });
      if (folder) { S.fold[folder] = false; saveFold(); }
      renderSide();
      var f = folderById(folder);
      toast(f ? 'Чат в папке «' + f.name + '»' : 'Чат без папки');
    }, fail);
  }
  // Меню «В папку» у строки чата: папки, «Без папки», «Новая папка…».
  function closeMenu() { var m = $('moveMenu'); if (m) m.remove(); }
  function moveMenu(btn, pid) {
    closeMenu();
    var p = (S.parts || []).filter(function (x) { return x.id === pid; })[0]; if (!p) return;
    var cur = p.folder || '';
    var items = (S.folders || []).map(function (f) {
      return '<button type="button" role="menuitemradio" aria-checked="' + (cur === f.id) + '" data-act="move-to" data-id="' + esc(pid) + '" data-folder="' + esc(f.id) + '">' + ico('i-folder', 'sm') + '<span>' + esc(f.name) + '</span>' + (cur === f.id ? ico('i-check', 'sm on') : '') + '</button>';
    }).join('');
    var m = document.createElement('div');
    m.id = 'moveMenu'; m.className = 'menu'; m.setAttribute('role', 'menu'); m.setAttribute('aria-label', 'Переложить чат в папку');
    m.innerHTML = '<span class="mlabel">Переложить в папку</span>' + items +
      (cur ? '<button type="button" role="menuitem" data-act="move-to" data-id="' + esc(pid) + '" data-folder="">' + ico('i-x', 'sm') + '<span>Без папки</span></button>' : '') +
      '<button type="button" role="menuitem" data-act="folder-new" data-move="' + esc(pid) + '">' + ico('i-folder-plus', 'sm') + '<span>Новая папка…</span></button>';
    document.body.appendChild(m);
    var r = btn.getBoundingClientRect(), mw = m.offsetWidth, mh = m.offsetHeight;
    m.style.left = Math.max(8, Math.min(window.innerWidth - mw - 8, r.left)) + 'px';
    m.style.top = (r.bottom + mh + 8 > window.innerHeight ? Math.max(8, r.top - mh - 4) : r.bottom + 4) + 'px';
    var first = m.querySelector('button'); if (first) first.focus();
  }
  document.addEventListener('pointerdown', function (e) { var m = $('moveMenu'); if (m && !m.contains(e.target) && !e.target.closest('[data-act="move-menu"]')) closeMenu(); });
  document.addEventListener('keydown', function (e) { if (e.key === 'Escape') closeMenu(); });
  // Перетаскивание чата мышью: на папку — в папку, на «Детали» или в общий список — без папки.
  (function () {
    var side = $('side'), dragId = null;
    side.addEventListener('dragstart', function (e) {
      var row = e.target.closest && e.target.closest('[data-drag]'); if (!row) return;
      dragId = row.getAttribute('data-drag');
      try { e.dataTransfer.setData('text/plain', dragId); e.dataTransfer.effectAllowed = 'move'; } catch (x) {}
      row.classList.add('dragging');
    });
    side.addEventListener('dragend', function () {
      dragId = null;
      side.querySelectorAll('.dragging, .drop-on').forEach(function (el) { el.classList.remove('dragging', 'drop-on'); });
    });
    function target(e) { var t = e.target.closest && e.target.closest('.folder[data-drop], .grouphead[data-drop], .parts[data-drop]'); return t; }
    side.addEventListener('dragover', function (e) {
      if (!dragId) return;
      var t = target(e); if (!t) return;
      e.preventDefault();
      side.querySelectorAll('.drop-on').forEach(function (el) { if (el !== t) el.classList.remove('drop-on'); });
      if (t.classList.contains('folder') || t.classList.contains('grouphead')) t.classList.add('drop-on');
    });
    side.addEventListener('drop', function (e) {
      var t = target(e); if (!t || !dragId) return;
      e.preventDefault();
      var folder = t.getAttribute('data-drop') || '';
      var p = (S.parts || []).filter(function (x) { return x.id === dragId; })[0];
      if (p && (p.folder || '') !== folder) movePart(dragId, folder);
    });
  })();

  function renameDialog(id) {
    var p = (S.part && (!id || S.part.id === id)) ? S.part : (S.parts || []).filter(function (x) { return x.id === id; })[0];
    if (!p) return;
    overlay('<section class="modal" role="dialog" aria-label="Переименовать деталь"><div class="mhead"><h2>Название детали</h2><button class="iconbtn plain" type="button" data-act="close" aria-label="Закрыть">' + ico('i-x') + '</button></div>' +
      '<div class="mbody"><label class="sr" for="rnInput">Название</label><input class="input" id="rnInput" maxlength="80" value="' + esc(p.name) + '">' +
      '</div>' +
      '<div class="mfoot"><button class="btn" type="button" data-act="close">Отмена</button><button class="btn primary" type="button" data-act="rename-do" data-id="' + esc(p.id) + '">Сохранить</button></div></section>');
    var inp = $('rnInput'); if (inp) { inp.focus(); inp.select(); }
  }
  function renameDo(id) {
    var name = $('rnInput').value.trim();
    if (!name) { toast('Введите название'); return; }
    Api.renamePart(id, name).then(function (x) {
      closeOverlay();
      S.parts.forEach(function (q) { if (q.id === id) q.name = x.name; });
      if (S.part && S.part.id === id) { S.part.name = x.name; renderChat(); }
      renderSide();
    }, fail);
  }

  // ------------------------------------------------------------ картотека
  var FILTERS = [['all', 'Все'], ['3D', '3D'], ['5D', '5D'], ['flex', 'Гибкие'], ['comp', 'Композиты'], ['fav', 'Избранное'], ['issue', 'Были проблемы']];
  var STATE_TXT = { printed: ['Напечатано', 'ok'], issue: ['Были проблемы', 'warn'], 'new': ['Не печаталось', 'plain'] };
  function catFiltered() {
    var q = S.cat.q.toLowerCase(), f = S.cat.filter;
    return S.catalog.filter(function (r) {
      var hay = (r.name + ' ' + r.material + ' ' + r.mode + ' ' + (r.file || '')).toLowerCase();
      if (q && hay.indexOf(q) < 0) return false;
      if (f === '3D' || f === '5D') return r.mode === f;
      if (f === 'flex') return /tpu|tpv|tpe|sebs/i.test(r.material);
      if (f === 'comp') return /fiberpart|contifiber/i.test(r.material);
      if (f === 'fav') return r.fav;
      if (f === 'issue') return r.state === 'issue';
      return true;
    });
  }
  function renderCatalog() {
    $('view-catalog').innerHTML = '<div class="page-in"><div class="page-title"><div><h1>Картотека</h1><span class="muted">Детали, которые вы сохраняли. Параметры печати записаны в имени файла — по нему картотека узнаёт старые проекты.</span></div>' +
      '<button class="btn" type="button" data-act="view" data-v="settings" data-tab="names">' + ico('i-folder') + 'Импортировать папку</button></div>' +
      '<div class="tabs" role="group" aria-label="Фильтр">' + FILTERS.map(function (f) { return '<button type="button" data-act="cat-filter" data-v="' + f[0] + '" aria-pressed="' + (S.cat.filter === f[0]) + '">' + f[1] + '</button>'; }).join('') + '</div>' +
      '<div class="toolbar"><label class="search"><span class="sr">Поиск по картотеке</span>' + ico('i-search') + '<input type="search" id="catSearch" placeholder="Имя, материал, режим, файл…" value="' + esc(S.cat.q) + '"></label>' +
      '<span class="count num" id="catCount"></span></div>' +
      '<div id="catGrid" style="display:flex;flex-direction:column;gap:16px"></div></div>';
    renderCatalogGrid();
  }
  function renderCatalogGrid() {
    var list = catFiltered();
    var rows = list.map(function (r) {
      var sel = S.cat.sel.indexOf(r.id) >= 0;
      var st = STATE_TXT[r.state] || STATE_TXT['new'];
      var params = [r.profile, r.infill && ('заполнение ' + r.infill), r.walls && (r.walls + ' стенки')].filter(Boolean).join(' · ');
      return '<tr class="' + (sel ? 'sel' : '') + '"><td class="c-chk"><input type="checkbox" data-act="cat-sel" data-id="' + esc(r.id) + '"' + (sel ? ' checked' : '') + ' aria-label="Выбрать для сравнения: ' + esc(r.name) + '"></td>' +
        '<td><div class="name"><span><button class="linkbtn" type="button" data-act="cat-record" data-id="' + esc(r.id) + '">' + esc(r.name) + '</button> ' +
        '<button class="fav' + (r.fav ? ' on' : '') + '" type="button" data-act="cat-fav" data-id="' + esc(r.id) + '" aria-label="' + (r.fav ? 'Убрать из избранного' : 'В избранное') + '" aria-pressed="' + !!r.fav + '">' + ico('i-star', 'sm') + '</button></span>' +
        '<span class="fname">' + esc(r.file || '') + '</span>' + (params ? '<span class="params">' + esc(params) + '</span>' : '') + '</div></td>' +
        '<td class="c-mode">' + esc(r.mode || '—') + '</td><td class="c-mat">' + esc(r.material || '—') + '</td>' +
        '<td class="num c-time">' + esc(r.time || '—') + '</td><td class="num c-cost">' + esc(r.cost || '—') + '</td>' +
        '<td class="c-state"><span class="tag ' + st[1] + '">' + st[0] + '</span></td><td class="num c-date">' + esc(r.date || '—') + '</td>' +
        '<td class="acts"><button class="btn sm" type="button" data-act="cat-open" data-id="' + esc(r.id) + '">Открыть</button> ' +
        '<button class="btn text sm" type="button" data-act="cat-template" data-id="' + esc(r.id) + '">Как шаблон</button></td></tr>';
    }).join('');
    var table = rows ? '<div class="scroll-x"><table class="ctbl"><thead><tr><th class="c-chk"><span class="sr">Сравнить</span></th><th>Деталь и файл</th><th>Режим</th><th>Материал</th>' +
      '<th class="num">Время</th><th class="num">Стоимость</th><th>Как напечаталось</th><th class="num">Дата</th><th><span class="sr">Действия</span></th></tr></thead><tbody>' + rows + '</tbody></table></div>' : '';
    var empty = !S.catalog.length ? '<div class="empty boxed"><b>В картотеке пока пусто</b><span>Сохраните деталь из чата или импортируйте папку со старыми проектами.</span></div>' :
      !list.length ? '<div class="empty boxed"><b>Ничего не нашлось</b><span>Измените запрос или фильтр.</span></div>' : '';
    var bar = S.cat.sel.length === 2 ? '<div class="cmpbar">Выбрано 2 записи<button class="btn sm" type="button" data-act="cat-compare">Сравнить</button></div>' :
      S.cat.sel.length === 1 ? '<span class="muted" style="font-size:13px">Отметьте ещё одну запись, чтобы сравнить.</span>' : '';
    $('catGrid').innerHTML = (table || empty) + bar;
    var c = $('catCount'); if (c) c.textContent = list.length + ' ' + R.plural(list.length, 'запись', 'записи', 'записей') + (list.length !== S.catalog.length ? ' из ' + S.catalog.length : '');
  }
  function loadCatalog() { return Api.catalog().then(function (c) { S.catalog = c; if (S.view === 'catalog') renderCatalogGrid(); if (S.view === 'chat') renderSide(); }, fail); }
  function recById(id) { return S.catalog.filter(function (r) { return r.id === id; })[0]; }
  function recordDrawer(id) {
    var r = recById(id); if (!r) return;
    var states = [['printed', 'Напечатано'], ['issue', 'Были проблемы'], ['new', 'Не печаталось']];
    overlay('<aside class="drawer" role="dialog" aria-label="Запись картотеки"><div class="mhead"><h2>' + esc(r.name) + '</h2><button class="iconbtn" type="button" data-act="close" aria-label="Закрыть">' + ico('i-x') + '</button></div>' +
      '<div class="dbody"><span class="muted" style="font-size:14px">' + esc([r.mode, r.material].filter(Boolean).join(' · ')) + '</span>' +
      '<div class="strip"><div><span>Время печати</span><strong>' + esc(r.time || '—') + '</strong></div><div><span>Материал</span><strong>' + esc(r.mass || '—') + '</strong></div><div><span>Стоимость</span><strong>' + esc(r.cost || '—') + '</strong></div></div>' +
      '<dl class="kv"><dt>Профиль</dt><dd>' + esc(r.profile || '—') + '</dd><dt>Заполнение</dt><dd>' + esc(r.infill || '—') + '</dd><dt>Стенки</dt><dd>' + esc(r.walls || '—') + '</dd><dt>Файлы</dt><dd class="mono" style="font-size:12px;white-space:pre-wrap">' +
      esc((r.paths || [r.file]).join('\n')) + '</dd></dl>' +
      '<div class="field"><span class="lab">Как напечаталось</span><div class="seg3">' +
      states.map(function (s) { return '<button type="button" data-act="rec-state" data-id="' + esc(r.id) + '" data-v="' + s[0] + '" aria-pressed="' + (r.state === s[0]) + '">' + s[1] + '</button>'; }).join('') + '</div></div>' +
      '<div class="field"><label for="recNote">Заметка</label><textarea class="input" id="recNote" rows="3" placeholder="Что пошло не так, что поменять в следующий раз">' + esc(r.note || '') + '</textarea>' +
      '<span class="help">С отметкой «Были проблемы» ассистент учтёт заметку в следующих деталях из ' + esc(r.material || 'этого материала') + ' в режиме ' + esc(r.mode || '3D') + '.</span></div>' +
      (r.state === 'issue' && (r.lessons || []).length ? '<div class="field"><span class="lab">Запомнил для следующих деталей</span><dl class="kv">' + r.lessons.map(function (x) {
        var f = function (v) { return typeof v === 'boolean' ? (v ? 'да' : 'нет') : String(v).replace('.', ','); };
        return '<dt>' + esc(x.label || x.key) + '</dt><dd>' + esc(f(x.from)) + ' → ' + esc(f(x.to)) + ' <span class="muted">· ' + esc((x.defect || '').toLowerCase()) + '</span></dd>';
      }).join('') + '</dl></div>' : '') + '</div>' +
      '<div class="mfoot"><button class="btn text" type="button" data-act="rec-note" data-id="' + esc(r.id) + '" style="margin-right:auto">Сохранить заметку</button><button class="btn" type="button" data-act="cat-template" data-id="' + esc(r.id) + '">Как шаблон</button>' +
      '<button class="btn primary" type="button" data-act="cat-open" data-id="' + esc(r.id) + '">Открыть в чате</button></div></aside>');
  }
  function compareDialog() {
    var a = recById(S.cat.sel[0]), b = recById(S.cat.sel[1]); if (!a || !b) return;
    var keys = [['mode', 'Режим'], ['material', 'Материал'], ['profile', 'Профиль'], ['walls', 'Стенки'], ['infill', 'Заполнение'], ['time', 'Время'], ['mass', 'Масса'], ['cost', 'Стоимость'], ['state', 'Как напечаталось']];
    var showSame = !!S.cat.same;
    var rows = keys.map(function (k) {
      var va = k[0] === 'state' ? (STATE_TXT[a.state] || STATE_TXT['new'])[0] : a[k[0]], vb = k[0] === 'state' ? (STATE_TXT[b.state] || STATE_TXT['new'])[0] : b[k[0]];
      var same = String(va) === String(vb);
      if (same && !showSame) return '';
      return '<tr class="' + (same ? 'same' : 'd') + '"><td>' + k[1] + '</td><td>' + esc(va || '—') + '</td><td>' + esc(vb || '—') + '</td></tr>';
    }).join('');
    overlay('<section class="modal wide" role="dialog" aria-label="Сравнение записей"><div class="mhead"><h2>Сравнение</h2><label style="display:flex;gap:8px;align-items:center;font-size:13px;margin-left:auto">' +
      '<input type="checkbox" data-act="cmp-same"' + (showSame ? ' checked' : '') + '>Показать одинаковые</label><button class="iconbtn" type="button" data-act="close" aria-label="Закрыть" style="margin-left:8px">' + ico('i-x') + '</button></div>' +
      '<div class="mbody scroll-x"><table class="cmp-tbl"><thead><tr><th>Параметр</th><th>' + esc(a.name) + '</th><th>' + esc(b.name) + '</th></tr></thead><tbody>' + (rows || '<tr><td colspan="3">Записи одинаковые</td></tr>') + '</tbody></table></div>' +
      '<div class="mfoot"><button class="btn" type="button" data-act="close">Закрыть</button><button class="btn primary" type="button" data-act="cat-template" data-id="' + esc(a.id) + '">Взять «' + esc(a.name) + '» как шаблон</button></div></section>');
  }

  // ------------------------------------------------------------ настройки
  var TABS = [['llm', 'Подключение к LLM'], ['print', 'Печать и файлы'], ['names', 'Имена файлов'], ['mcp', 'MCP-сервер']];
  function sw(id, on, title, text) {
    return '<label class="switch"><input type="checkbox" id="' + id + '"' + (on ? ' checked' : '') + '><span><b>' + title + '</b><span>' + text + '</span></span></label>';
  }
  function srow(title, desc, panel) {
    return '<section class="srow"><div class="sl"><h2>' + title + '</h2>' + (desc ? '<p>' + desc + '</p>' : '') + '</div><div class="spanel">' + panel + '</div></section>';
  }
  function renderSettings() {
    var s = S.settings, panel = '';
    if (S.stab === 'llm') {
      var ch = S.check;
      var hasVision = ch && ch.vision !== undefined;
      var checks = ch && !ch.error && ch.reply !== undefined ? '<div class="checks3">' + [['reply', 'Модель отвечает'], ['tools', 'Вызывает инструменты'], hasVision ? ['vision', 'Модель для фото видит фото'] : ['images', 'Понимает фото']].map(function (c) {
        return '<div><span class="glyph ' + (ch[c[0]] ? 'done' : 'error') + '">' + ico(ch[c[0]] ? 'i-check' : 'i-x') + '</span>' + c[1] + '</div>';
      }).join('') + '</div>' : '';
      var photoNote = ch && !ch.error ? (hasVision ? (!ch.vision && ch.vision_note) : (!ch.images && ch.images_note)) : '';
      if (photoNote) checks += '<div class="note warn inline-note">' + ico('i-alert') + '<span>' + esc(photoNote) + '</span></div>';
      panel = srow('Подключение', 'Любой OpenAI-совместимый API с вызовом инструментов. По умолчанию — timeweb.ai.',
          '<div class="fields2"><div class="field"><label for="stUrl">Адрес API</label><input class="input mono" id="stUrl" value="' + esc(s.url) + '"></div>' +
          '<div class="field"><label for="stModel">Модель</label><input class="input mono" id="stModel" value="' + esc(s.model) + '"></div></div>' +
          '<div class="field"><label for="stKey">Ключ API</label><div class="inrow"><input class="input mono" id="stKey" type="password" autocomplete="off" placeholder="' + (s.keySaved ? '•••••••••••• — введите, чтобы заменить' : 'Ключ из личного кабинета') + '">' +
          (s.keySaved ? '<span class="saved-badge">' + ico('i-check', 'sm') + 'Сохранён</span>' : '') + '</div>' +
          '<span class="help">' + ico('i-lock', 'sm') + 'Хранится в настройках STE Slicer. Не попадает в код плагина, журнал и браузер.</span></div>' +
          '<div class="pfoot"><button class="btn primary" type="button" data-act="settings-save" data-tab="llm">Сохранить</button></div>') +
        srow('Фото', 'Если основная модель не видит изображения, укажите модель с их поддержкой — обычно в названии VL. Она опишет фото текстом.',
          '<div class="field"><label for="stVision">Модель для фото</label><input class="input mono" id="stVision" placeholder="пусто — фото получает основная модель" value="' + esc(s.vision_model || '') + '"></div>' +
          '<div class="pfoot"><button class="btn" type="button" data-act="settings-save" data-tab="llm">Сохранить</button></div>') +
        srow('Проверка', 'Несколько коротких запросов: ответ, вызов инструментов, распознавание красного квадрата.',
          '<div class="inrow"><button class="btn" type="button" data-act="llm-check">' + ico('i-plug') + (S.checking ? 'Проверяю…' : 'Проверить подключение') + '</button>' +
          '<span class="muted num" style="font-size:13px">' + (ch && !ch.error && ch.ms ? 'ответ за ' + ch.ms + ' мс' : '') + '</span></div>' + checks +
          (ch && ch.error ? '<div class="note bad inline-note">' + ico('i-alert') + '<span>' + esc(ch.error) + '</span></div>' : ''));
    } else if (S.stab === 'print') {
      panel = srow('Принтер и цена', 'Принтер выбирается в самом STE Slicer. Цена нужна для стоимости в паспорте.',
          '<div class="fields2"><div class="field"><span class="lab">Принтер</span><span class="static">' + esc(S.conn.printer || 'принтер STE Slicer') + '</span></div>' +
          '<div class="field"><label for="stPrice">Цена материала, ₽ за кг</label><input class="input num" id="stPrice" inputmode="decimal" value="' + esc(s.price) + '"></div></div>' +
          '<div class="field"><label for="stFolder">Папка для сохранения</label><input class="input mono" id="stFolder" value="' + esc(s.folder) + '"></div>') +
        srow('Поведение ассистента', 'Что ассистент делает сам, а что спрашивает.',
          sw('stAsk', s.ask_questions, 'Задавать уточняющие вопросы без ТЗ', 'Не больше двух. Выключите — ассистент сразу решит сам и отметит допущения.') +
          sw('stImages', s.images, 'Отправлять фото в LLM', 'Фото узла помогают понять условия работы, если ТЗ нет.') +
          sw('stAuto', s.autostart, 'Открывать чат при запуске STE Slicer', 'Иначе — меню «Расширения» → «AI Assistant».') +
          '<div class="pfoot"><button class="btn primary" type="button" data-act="settings-save" data-tab="print">Сохранить</button></div>');
    } else if (S.stab === 'names') {
      var rep = S.importRep;
      panel = srow('Шаблон имени файла', 'Параметры печати записываются в имя. По имени картотека узнаёт файл и импортирует старые папки.',
          '<div class="field"><label for="tplInput">Шаблон</label><input class="input mono" id="tplInput" value="' + esc(s.template) + '"></div>' +
          '<div class="field"><span class="lab">Добавить поле</span><div class="tpl">' + (s.fields || []).map(function (f) { return '<button type="button" data-act="tpl-add" data-v="' + esc(f) + '"><span>+</span>' + esc(f) + '</button>'; }).join('') + '</div></div>' +
          '<div id="tplPreview"></div><div class="pfoot"><button class="btn primary" type="button" data-act="settings-save" data-tab="names">Сохранить шаблон</button></div>') +
        srow('Импорт папки', 'Добавлю в картотеку старые проекты: параметры возьму из имени файла.',
          '<div class="inrow"><label class="sr" for="impFolder">Папка</label><input class="input mono" id="impFolder" placeholder="D:\\Проекты\\Печать"><button class="btn" type="button" data-act="import">' + ico('i-folder') + 'Импортировать</button></div>' +
          (rep ? '<div class="report"><div><b>' + rep.added + '</b>добавлено</div><div><b>' + rep.fromName + '</b>параметры из имени</div><div><b>' + rep.skipped + '</b>пропущено</div></div>' +
            (rep.skippedFiles || []).map(function (x) { return '<div style="font-size:13px"><span class="mono">' + esc(x[0]) + '</span> <span class="muted">— ' + esc(x[1]) + '</span></div>'; }).join('') : ''));
    } else if (S.stab === 'mcp') {
      var m = S.mcp || { tools: [], log: [] };
      panel = srow('Доступ', 'Через MCP ассистент управляет слайсером. Те же инструменты можно дать Claude Desktop, Cursor или другому клиенту на этом компьютере.',
          sw('mcpExt', m.external, 'Разрешить внешние MCP-клиенты', 'Только с этого компьютера и только с токеном. Их вызовы видны в чате с пометкой «внешний клиент».') +
          '<div class="field"><label for="mcpUrl">Адрес</label><div class="inrow"><input class="input mono" id="mcpUrl" readonly value="' + esc(m.url || '') + '">' +
          '<button class="iconbtn boxed" type="button" data-act="copy" data-for="mcpUrl" aria-label="Копировать адрес" title="Копировать">' + ico('i-copy') + '</button></div></div>' +
          '<div class="field"><label for="mcpToken">Токен — заголовок Authorization: Bearer</label><div class="inrow"><input class="input mono" id="mcpToken" type="password" readonly value="' + esc(m.token || '') + '">' +
          '<button class="iconbtn boxed" type="button" data-act="copy" data-for="mcpToken" aria-label="Копировать токен" title="Копировать">' + ico('i-copy') + '</button><button class="btn" type="button" data-act="mcp-token">Сменить</button></div></div>') +
        srow('Инструменты', m.tools.length + ' ' + R.plural(m.tools.length, 'инструмент', 'инструмента', 'инструментов') + ' — те же, что у ассистента в чате.',
          '<div class="toolgrid">' + m.tools.map(function (t) { return '<div><span class="mono">' + esc(t[0]) + '</span><span>' + esc(t[1]) + '</span></div>'; }).join('') + '</div>') +
        srow('Журнал', 'Последние вызовы внешних клиентов.', m.log.length ? '<div class="scroll-x"><table class="tbl"><thead><tr><th>Время</th><th>Клиент</th><th>Инструмент</th><th>Результат</th></tr></thead><tbody>' +
          m.log.map(function (l) { return '<tr><td class="num muted">' + esc(l[0]) + '</td><td>' + esc(l[1]) + '</td><td class="mono">' + esc(l[2]) + '</td><td' + (/отказ|ошибка/.test(l[3]) ? ' class="badc"' : '') + '>' + esc(l[3]) + '</td></tr>'; }).join('') + '</tbody></table></div>' :
          '<span class="muted">Вызовов пока не было.</span>');
    }
    $('view-settings').innerHTML = '<div class="page-in"><div class="page-title"><div><h1>Настройки</h1><span class="muted">' + esc((TABS.filter(function (t) { return t[0] === S.stab; })[0] || TABS[0])[1]) + '</span></div></div>' +
      '<div class="tabs" role="tablist" aria-label="Разделы настроек">' + TABS.map(function (t) { return '<button type="button" role="tab" data-act="stab" data-v="' + t[0] + '" aria-current="' + (S.stab === t[0]) + '" aria-selected="' + (S.stab === t[0]) + '">' + t[1] + '</button>'; }).join('') + '</div>' +
      '<div>' + panel + '</div></div>';
    if (S.stab === 'names') renderTemplatePreview();
  }
  function renderTemplatePreview() {
    var tpl = S.settings.template || '';
    var fields = S.settings.fields || [];
    var bad = (tpl.match(/\{([^}]*)\}/g) || []).map(function (x) { return x.slice(1, -1); }).filter(function (x) { return fields.indexOf(x) < 0; });
    var sample = { 'деталь': 'koleso', 'режим': '5D', 'материал': 'TPU-D70', 'слой': '0.10', 'заполнение': '90', 'стенки': '4', 'профиль': 'normal', 'принтер': 'hybrid-530', 'копии': '', 'дата': '20261023' };
    var name = tpl.replace(/\{([^}]*)\}/g, function (m, k) { return sample[k] !== undefined ? sample[k] : m; });
    var el = $('tplPreview'); if (!el) return;
    el.innerHTML = bad.length ? '<div class="note inline-note bad">' + ico('i-alert') + '<span>Поле {' + esc(bad[0]) + '} не найдено. Доступны поля из списка выше.</span></div>' :
      '<div class="example"><span class="label">Пример</span><span class="mono">' + esc(name) + '.3mf</span></div>';
  }
  function saveSettings(tab) {
    var data = {};
    if (tab === 'llm') { data.url = $('stUrl').value.trim(); data.model = $('stModel').value.trim(); data.vision_model = $('stVision').value.trim(); if ($('stKey').value.trim()) data.key = $('stKey').value.trim(); }
    if (tab === 'print') { data.price = $('stPrice').value; data.folder = $('stFolder').value.trim(); data.ask_questions = $('stAsk').checked; data.images = $('stImages').checked; data.autostart = $('stAuto').checked; }
    if (tab === 'names') data.template = $('tplInput').value.trim();
    return Api.saveSettings(data).then(function (s) { S.settings = s; toast('Сохранено'); renderSettings(); return loadState(); }, fail);
  }

  // ------------------------------------------------------------ вид
  function setView(v, tab) {
    S.view = v; if (tab) S.stab = tab;
    $('app').dataset.drawer = '';
    if (v === 'catalog') loadCatalog();
    if (v === 'settings') {
      Api.settings().then(function (s) { S.settings = s; renderSettings(); }, fail);
      if (S.stab === 'mcp') Api.mcp().then(function (m) { S.mcp = m; renderSettings(); });
    }
    render();
  }

  // ------------------------------------------------------------ действия
  document.addEventListener('click', function (e) {
    var el = e.target.closest('[data-act]');
    if (!el) return;
    var act = el.dataset.act, p = S.part, d = el.dataset;
    if (el.tagName === 'A') e.preventDefault();
    var call = function (pr) { if (pr && pr.then) pr.then(null, fail); };
    switch (act) {
      case 'view': if (S.overlayOpen) closeOverlay(); closeOverlay(); setView(d.v, d.tab); break;
      case 'theme': theme(true); break;
      case 'drawer': $('app').dataset.drawer = $('app').dataset.drawer === d.v ? '' : d.v; $('scrim').hidden = $('app').dataset.drawer !== 'pass'; break;
      case 'close': closeOverlay(); break;
      case 'new': $('app').dataset.drawer = ''; newPart(d.folder || ''); break;
      case 'fold': S.fold[d.id] = !S.fold[d.id]; saveFold(); renderSide(); break;
      case 'folder-new': closeMenu(); S.moving = d.move || null; folderDialog(null); break;
      case 'folder-rename': folderDialog(d.id); break;
      case 'folder-save': folderSave(d.id || ''); break;
      case 'folder-del-ask': folderDeleteDialog(d.id); break;
      case 'folder-delete': folderDelete(d.id); break;
      case 'move-menu': if ($('moveMenu')) closeMenu(); else moveMenu(el, d.id); break;
      case 'move-to': movePart(d.id, d.folder || ''); break;
      case 'part': S.pending = []; $('app').dataset.drawer = ''; $('scrim').hidden = true; openPart(d.id); break;
      case 'attach': $('fileIn').click(); break;
      case 'unpend': S.pending.splice(+d.i, 1); renderComposer(); break;
      case 'stop': call(Api.stop(p.id)); break;
      case 'suggest': if (d.text) send(d.text); break;
      case 'starter': if (d.text) prefill(d.text); break;
      case 'rename': renameDialog(d.id); break;
      case 'rename-do': renameDo(d.id || (p && p.id)); break;
      case 'part-del-ask': deleteDialog(d.id || (p && p.id)); break;
      case 'part-delete': deletePart(d.id || (p && p.id)); break;
      case 'trace': S.ui.open[d.id] = d.open === '1'; renderChat(); break;
      case 'step': S.ui.step[d.id] = !S.ui.step[d.id]; renderChat(); break;
      case 'prow': S.ui.prow[d.k] = !S.ui.prow[d.k]; S.passHtml = R.passport(p, S.ui); $('pass').innerHTML = S.passHtml; break;
      case 'answer': call(Api.answer(p.id, d.q, d.v)); break;
      case 'ask-toggle':
        var blk = findBlock(p, d.q);
        var sel = (S.ui.askSel[d.q] || []).slice();
        var ex = (blk && blk.exclusive) || [];
        var at = sel.indexOf(d.v);
        if (at >= 0) sel.splice(at, 1);
        else if (ex.indexOf(d.v) >= 0) sel = [d.v];
        else { sel = sel.filter(function (x) { return ex.indexOf(x) < 0; }); sel.push(d.v); }
        S.ui.askSel[d.q] = sel; renderChat(); break;
      case 'ask-submit':
        var chosen = S.ui.askSel[d.q] || [];
        if (!chosen.length) break;
        var blk2 = findBlock(p, d.q);
        if (blk2) chosen = blk2.options.filter(function (o) { return chosen.indexOf(o) >= 0; });
        call(Api.answer(p.id, d.q, chosen.join('; ')).then(function () { delete S.ui.askSel[d.q]; })); break;
      case 'q-toggle':
        var qb = findBlock(p, d.q), it = null;
        (qb && qb.items || []).forEach(function (x) { if (x.id === d.i) it = x; });
        if (!it) break;
        var qs = S.ui.qSel[d.q] || (S.ui.qSel[d.q] = {});
        var cur = (qs[d.i] || []).slice(), at2 = cur.indexOf(d.v);
        var ex2 = (it.exclusive || []).concat(['Реши сам']);
        if (!it.multi || ex2.indexOf(d.v) >= 0) cur = at2 >= 0 ? [] : [d.v];
        else if (at2 >= 0) cur.splice(at2, 1);
        else { cur = cur.filter(function (x) { return ex2.indexOf(x) < 0; }); cur.push(d.v); }
        qs[d.i] = cur; renderChat(); break;
      case 'q-submit': case 'q-skip':
        var qsel = act === 'q-skip' ? {} : (S.ui.qSel[d.q] || {});
        var qb2 = findBlock(p, d.q), out = {};
        var mode5 = (qsel.mode || [])[0] === '5D' || (!(qsel.mode || []).length && (qb2 && qb2.items || []).some(function (x) { return x.id === 'mode' && x.rec === '5D'; }));
        (qb2 && qb2.items || []).forEach(function (x) {
          if (x.modeDep && mode5) return;  // в 5D количество не спрашиваем — счётчик был скрыт
          var v = (qsel[x.id] || []);
          if (x.widget === 'triangle' || x.widget === 'stepper') v = v.slice(0, 1);  // точка треугольника, число
          else v = x.options.concat(['Реши сам']).filter(function (o) { return v.indexOf(o) >= 0; });
          if (v.length) out[x.id] = v.join('; ');
        });
        call(Api.answer(p.id, d.q, out).then(function () { delete S.ui.qSel[d.q]; })); break;
      case 'plan-open': S.ui.planOpen[d.k] = !S.ui.planOpen[d.k]; renderChat(); break;
      case 'plan-why': S.ui.planWhy[d.k] = !S.ui.planWhy[d.k]; renderChat(); break;
      case 'plan-set': S.ui.planOpen[d.k] = false; call(Api.planEdit(p.id, d.k, d.v)); break;
      case 'plan-confirm': call(Api.confirm(p.id)); break;
      case 'plan-words': var mi = $('msg'); if (mi) { mi.focus(); if (!mi.value) mi.placeholder = 'Например: «материал PETG», «печатай в 3D», «5 штук»'; } break;
      case 'assume': call(Api.assumption(p.id, d.k, d.v)); break;
      case 'variant': call(Api.variant(p.id, d.v)); break;
      case 'pvariant':
        if (p.status === 'plan') call(Api.planEdit(p.id, 'variant', d.v));
        else if (p.passport && p.passport.variants) call(Api.variant(p.id, d.v));
        else if (p.passport && !p.passport.totals) toast('Сначала нарежьте деталь — варианты считаются пробной нарезкой');
        else call(Api.variants(p.id));
        break;
      case 'variants': call(Api.variants(p.id)); break;
      case 'slice': call(Api.slice(p.id)); break;
      case 'mode': call(Api.mode(p.id, d.v)); break;
      case 'fix': call(Api.fix(p.id, d.w, Number(d.i), d.label)); break;
      case 'orient': call(Api.orientation(p.id, +d.i)); break;
      case 'undo': call(Api.undo(p.id, d.id)); break;
      case 'review-photo': S.review = true; renderComposer(); $('fileIn').click(); break;
      case 'review-off': S.review = false; renderComposer(); break;
      case 'rv-toggle':
        var blkR = findReview(p, d.id), cur3 = (S.ui.rvSel[d.id] || (blkR && blkR.selected) || []).slice(), at3 = cur3.indexOf(d.v);
        if (at3 >= 0) cur3.splice(at3, 1); else cur3.push(d.v);
        S.ui.rvSel[d.id] = cur3; renderChat(); break;
      case 'rv-choose': call(Api.review(p.id, d.id, S.ui.rvSel[d.id] || []).then(function () { delete S.ui.rvSel[d.id]; })); break;
      case 'rv-apply': call(Api.reviewApply(p.id, d.id)); break;
      case 'pset': call(Api.setting(p.id, d.key, d.v)); break;
      case 'pset-field': var fi = $(d.for); if (fi && fi.value.trim()) call(Api.setting(p.id, d.key, fi.value.trim())); break;
      case 'preset': call(Api.reset(p.id, d.key)); break;
      case 'preset-all': call(Api.reset(p.id)); break;
      case 'all-settings': S.allq = S.allq || ''; allSettings(); break;
      case 'save-open': openSave(d.gcode === '1'); break;
      case 'save-do': doSave(); break;
      case 'choose-folder': chooseFolder(el); break;
      case 'open-folder': call(Api.openFolder(d.path ? d.path.replace(/[\\\/][^\\\/]*$/, '') : '')); break;
      case 'req-edit': S.ui.editReq = d.tag; S.ui.editKind = d.k; S.ui.addReq = false; renderChat(); break;
      case 'req-kind': S.ui.editKind = d.k; renderChat(); break;
      case 'req-cancel': S.ui.editReq = null; S.ui.addReq = false; renderChat(); break;
      case 'req-save': call(Api.requirement(p.id, { tag: d.tag, text: $('reqText').value.trim(), kind: S.ui.editKind }).then(function () { S.ui.editReq = null; })); break;
      case 'req-del': call(Api.requirement(p.id, { tag: d.tag, 'delete': true })); break;
      case 'req-add': S.ui.addReq = true; S.ui.editReq = null; renderChat(); break;
      case 'req-add-save': call(Api.requirement(p.id, { add: true, text: $('reqNew').value.trim() }).then(function () { S.ui.addReq = false; })); break;
      case 'cat-filter': S.cat.filter = d.v; renderCatalog(); break;
      case 'cat-sel':
        var i = S.cat.sel.indexOf(d.id);
        if (i >= 0) S.cat.sel.splice(i, 1); else { S.cat.sel.push(d.id); if (S.cat.sel.length > 2) S.cat.sel.shift(); }
        renderCatalogGrid(); break;
      case 'cat-fav': var r = recById(d.id); call(Api.updateRecord(d.id, { fav: !r.fav }).then(loadCatalog)); break;
      case 'cat-compare': S.cat.same = false; compareDialog(); break;
      case 'cmp-same': S.cat.same = el.checked; compareDialog(); break;
      case 'cat-record': recordDrawer(d.id); break;
      case 'rec-state': call(Api.updateRecord(d.id, { state: d.v }).then(function () { return loadCatalog(); }).then(function () { recordDrawer(d.id); })); break;
      case 'rec-note': call(Api.updateRecord(d.id, { note: $('recNote').value }).then(function () { toast('Заметка сохранена'); return loadCatalog(); })); break;
      case 'cat-open': case 'cat-template':
        call((act === 'cat-open' ? Api.openRecord(d.id) : Api.asTemplate(d.id)).then(function (np) {
          closeOverlay(); return loadState().then(function () { setView('chat'); return openPart(np.id); });
        })); break;
      case 'stab': S.stab = d.v; if (d.v === 'mcp') Api.mcp().then(function (m) { S.mcp = m; renderSettings(); }); renderSettings(); break;
      case 'settings-save': saveSettings(d.tab); break;
      case 'llm-check':
        S.checking = true; renderSettings();
        call(Api.checkLlm({ url: $('stUrl').value.trim(), model: $('stModel').value.trim(), vision_model: $('stVision').value.trim(), key: $('stKey').value.trim() }).then(function (r) {
          S.check = r; S.checking = false; renderSettings(); loadState();
        }, function (e) { S.checking = false; S.check = { error: e.message }; renderSettings(); })); break;
      case 'tpl-add': var t = $('tplInput'); t.value += (t.value && !/[_\-]$/.test(t.value) ? '_' : '') + '{' + d.v + '}'; S.settings.template = t.value; renderTemplatePreview(); break;
      case 'import':
        call(Api.importFolder($('impFolder').value.trim()).then(function (rep) { S.importRep = rep; renderSettings(); loadCatalog(); })); break;
      case 'mcp-token': call(Api.newMcpToken().then(function (m) { S.mcp = m; renderSettings(); toast('Токен сменён'); })); break;
      case 'copy':
        var inp = $(d.for);
        var done = function () { toast('Скопировано'); };
        if (navigator.clipboard) navigator.clipboard.writeText(inp.value).then(done, function () { inp.type = 'text'; inp.select(); });
        else { inp.type = 'text'; inp.select(); }
        break;
    }
  });
  document.addEventListener('change', function (e) {
    if (e.target.id === 'mcpExt') Api.setMcp(e.target.checked).then(function (m) { S.mcp = m; renderSettings(); }, fail);
  });

  // ------------------------------------------------------------ старт
  function start() {
    theme(false);
    if (!Api.hasToken()) {
      S.feedCache = null; $('feedIn').innerHTML = '<div class="err-big"><span class="ic">' + ico('i-lock') + '</span><span><b>Откройте чат из STE Slicer</b>' +
        '<span>Меню «Расширения» → «AI Assistant» → «Открыть чат». Адрес содержит ключ сессии, без него плагин не отвечает.</span></span></div>';
      return;
    }
    var hash = (location.hash || '').replace('#', '');
    Api.init().then(function (s) {
      S.conn = s.conn; S.parts = s.parts; S.folders = s.folders || []; S.settings = s.settings;
      loadCatalog();
      if (hash === 'settings') setView('settings'); else if (hash === 'catalog') setView('catalog'); else setView('chat');
      var last = null; try { last = localStorage.getItem('ste-part'); } catch (e) {}
      var id = s.active || last;
      if (!S.parts.some(function (p) { return p.id === id; })) id = S.parts[0] && S.parts[0].id;
      if (id) openPart(id); else newPart();
    }, function (e) {
      S.feedCache = null; $('feedIn').innerHTML = '<div class="err-big"><span class="ic">' + ico('i-alert') + '</span><span><b>Нет связи с плагином</b><span>' + esc(e.message) + '. Откройте чат заново из меню STE Slicer.</span></span></div>';
    });
  }
  window.addEventListener('hashchange', function () { var h = location.hash.replace('#', ''); if (h === 'settings' || h === 'catalog' || h === 'chat') { closeOverlay(); setView(h); } });
  start();
})();
