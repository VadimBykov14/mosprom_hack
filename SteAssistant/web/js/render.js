/* Отрисовка ленты, карточек ассистента и паспорта печати. Только функции «данные → HTML».
   Действия описаны атрибутами data-act / data-*; их обрабатывает app.js. */
(function () {
  'use strict';

  function esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
    });
  }
  function ico(id, cls) { return '<svg class="ico' + (cls ? ' ' + cls : '') + '"><use href="#' + id + '"/></svg>'; }
  function md(text) {
    var paras = String(text || '').split(/\n{2,}/);
    return paras.map(function (p) {
      var t = esc(p.trim());
      t = t.replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>').replace(/`([^`]+)`/g, '<code>$1</code>');
      if (/^(- |\* )/m.test(p)) {
        var items = t.split(/\n/).filter(Boolean).map(function (l) { return '<li>' + l.replace(/^(- |\* )/, '') + '</li>'; });
        return '<ul>' + items.join('') + '</ul>';
      }
      return '<p>' + t.replace(/\n/g, '<br>') + '</p>';
    }).join('');
  }
  var KIND_ICON = { wheel: 'k-wheel', shaft: 'k-shaft', bracket: 'k-bracket', gear: 'k-gear', fan: 'k-fan', impeller: 'k-impeller',
    boot: 'k-boot', figure: 'k-figure' };
  function kindIcon(kind) { return KIND_ICON[kind] || 'k-part'; }
  var UNITS = { mm: 'мм', 'mm/s': 'мм/с', s: 'с', '°C': '°C', '%': '%', '°': '°' };
  var SRC = { tz: 'по ТЗ', words: 'ваш выбор', geom: 'геометрия', rules: 'правила', def: 'по умолчанию', assume: 'допущение', catalog: 'картотека', profile: 'профиль', experience: 'опыт' };
  var STATUS = { draft: ['Черновик', ''], work: ['', 'work'], sliced: ['Нарезано', 'ok'], saved: ['Сохранено', 'ok'],
    ask: ['Ждёт ответа', 'warn'], plan: ['Ждёт подтверждения', 'warn'], failed: ['Нарезка не удалась', 'bad'] };
  // Точка у детали в списке — только когда нужно внимание или идёт работа.
  var DOT = { ask: 'ask', plan: 'ask', failed: 'bad', work: 'work' };
  function extOf(f) { var e = (f.name.split('.').pop() || '').toUpperCase(); return { DOCX: 'DOC', JPEG: 'JPG' }[e] || e.slice(0, 3); }
  function fileKind(name) {
    var n = name.toLowerCase();
    if (/\.(stl|obj|3mf)$/.test(n)) return 'model';
    if (/\.(docx|txt)$/.test(n)) return 'spec';
    if (/\.(jpe?g|png)$/.test(n)) return 'image';
    return 'other';
  }

  // ------------------------------------------------------------ шапка и список
  function status(conn) {
    var slicer = '<div class="conn"><span class="dot ' + (conn.slicer ? 'ok' : 'bad') + '"></span><b>STE Slicer</b><span>' +
      (conn.slicer ? 'подключён' : 'не отвечает') + '</span></div>';
    var llm = conn.llm ? '<div class="conn" title="Модель LLM"><span class="dot ok"></span><b>LLM</b><span class="mono">' + esc((conn.model || '').split('/').pop()) + '</span></div>' :
      '<button class="conn" type="button" data-act="view" data-v="settings" title="' + esc(conn.llmError || '') + '"><span class="dot bad"></span><b>LLM</b><span>' +
      (conn.keySaved ? 'недоступна' : 'нет ключа') + '</span></button>';
    return slicer + llm;
  }

  // Строка чата в списке. В папке — с отступом; перетаскивается мышью в папку или в «Детали».
  function partRow(p, activeId, inFolder) {
    var st = STATUS[p.status] || STATUS.draft;
    var meta = p.status === 'ask' ? 'Ждёт ответа' : p.status === 'failed' ? 'Нарезка не удалась' :
      [p.mode, p.material, st[0].toLowerCase()].filter(Boolean).join(' · ');  // идёт работа — точкой, без подписи
    var dot = DOT[p.status] ? '<span class="dot ' + DOT[p.status] + '"></span>' : '';
    return '<div class="partrow' + (inFolder ? ' infolder' : '') + '" draggable="true" data-drag="' + esc(p.id) + '"><button class="part" type="button" data-act="part" data-id="' + esc(p.id) + '" aria-current="' + (p.id === activeId) + '">' +
      ico(kindIcon(p.kind), 'k') + '<span class="tx"><span class="nm">' + esc(p.name) + '</span><span class="mt">' + dot + '<span>' + esc(meta || 'пока пусто') + '</span></span></span></button>' +
      '<span class="pacts"><button class="pren" type="button" data-act="move-menu" data-id="' + esc(p.id) + '" aria-label="Переложить «' + esc(p.name) + '» в папку" title="В папку">' + ico('i-folder', 'sm') + '</button>' +
      '<button class="pren" type="button" data-act="rename" data-id="' + esc(p.id) + '" aria-label="Переименовать «' + esc(p.name) + '»" title="Переименовать">' + ico('i-edit', 'sm') + '</button>' +
      '<button class="pdel" type="button" data-act="part-del-ask" data-id="' + esc(p.id) + '" aria-label="Удалить чат «' + esc(p.name) + '»" title="Удалить чат">' + ico('i-trash', 'sm') + '</button></span></div>';
  }

  function side(parts, activeId, query, folders, fold) {
    var q = (query || '').toLowerCase();
    folders = folders || []; fold = fold || {};
    var list = parts.filter(function (p) {
      if (p.empty && p.id !== activeId) return false;  // пустой чат виден, только пока он открыт
      return !q || (p.name + ' ' + p.material + ' ' + p.mode).toLowerCase().indexOf(q) >= 0;
    });
    var known = {};
    folders.forEach(function (f) { known[f.id] = true; });
    var groups = folders.map(function (f) {
      var inside = list.filter(function (p) { return p.folder === f.id; });
      if (q && !inside.length && f.name.toLowerCase().indexOf(q) < 0) return '';  // при поиске пустые папки прячем
      var open = q ? true : !fold[f.id];
      return '<div class="folder" data-drop="' + esc(f.id) + '">' +
        '<div class="fhead"><button class="fbtn" type="button" data-act="fold" data-id="' + esc(f.id) + '" aria-expanded="' + open + '">' +
        ico('i-chev-d', 'sm fchev') + ico('i-folder', 'k') + '<span class="nm">' + esc(f.name) + '</span><span class="cnt">' + inside.length + '</span></button>' +
        '<span class="pacts"><button class="pren" type="button" data-act="new" data-folder="' + esc(f.id) + '" aria-label="Новый чат в папке «' + esc(f.name) + '»" title="Новый чат в папке">' + ico('i-plus', 'sm') + '</button>' +
        '<button class="pren" type="button" data-act="folder-rename" data-id="' + esc(f.id) + '" aria-label="Переименовать папку «' + esc(f.name) + '»" title="Переименовать папку">' + ico('i-edit', 'sm') + '</button>' +
        '<button class="pdel" type="button" data-act="folder-del-ask" data-id="' + esc(f.id) + '" aria-label="Удалить папку «' + esc(f.name) + '»" title="Удалить папку">' + ico('i-trash', 'sm') + '</button></span></div>' +
        (open ? '<div class="fitems">' + (inside.map(function (p) { return partRow(p, activeId, true); }).join('') ||
          '<span class="fempty">Перетащите сюда чат или нажмите «+»</span>') + '</div>' : '') + '</div>';
    }).join('');
    var loose = list.filter(function (p) { return !p.folder || !known[p.folder]; }).map(function (p) { return partRow(p, activeId, false); }).join('');
    var empty = !groups && !loose ? '<span class="muted" style="padding:8px 10px;font-size:13px">' + (q ? 'Ничего не найдено' : 'Деталей пока нет') + '</span>' : '';
    return '<button class="newpart" type="button" data-act="new"><span class="pl">' + ico('i-plus') + '</span>Новая деталь</button>' +
      '<div class="grouphead" data-drop=""><span>Детали</span><button class="iconbtn plain sm" type="button" data-act="folder-new" aria-label="Новая папка" title="Новая папка">' + ico('i-folder-plus', 'sm') + '</button></div>' +
      '<label class="search"><span class="sr">Поиск по деталям</span>' + ico('i-search') +
      '<input type="search" id="partSearch" placeholder="Поиск по деталям" value="' + esc(query || '') + '"></label>' +
      '<div class="parts" data-drop="">' + groups + loose + empty + '</div>';
  }
  function plural(n, a, b, c) { n = Math.abs(n) % 100; var n1 = n % 10; if (n > 10 && n < 20) return c; if (n1 > 1 && n1 < 5) return b; if (n1 === 1) return a; return c; }

  function chatHead(p) {
    if (!p) return '';
    var st = STATUS[p.status] || STATUS.draft;
    var empty = !p.messages.length;
    var pp = p.passport, t = pp && pp.totals;
    var sub = empty ? '' :
      p.status === 'failed' ? 'Параметры не показываю, пока нарезка не пройдёт.' :
      pp ? [pp.modeShort, pp.material, t && t.time, t && t.mass].filter(Boolean).join(' · ') : '';
    return '<div class="tt"><div class="tr"><h1><button class="h1btn" type="button" data-act="rename" data-id="' + esc(p.id) + '" title="Переименовать">' + esc(p.name) +
      ico('i-edit', 'sm') + '</button></h1>' + (empty ? '' : st[0] ? '<span class="tag ' + st[1] + '">' + st[0] + '</span>' : '<span class="dot work" role="status" aria-label="Обновляется"></span>') + '</div>' +
      (sub ? '<span class="sub">' + esc(sub) + '</span>' : '') + '</div><div class="acts">' +
      '<button class="iconbtn only-pass" type="button" data-act="drawer" data-v="pass" aria-label="Паспорт печати" title="Паспорт печати">' + ico('i-panel') + '</button>' +
      (empty ? '' : '<button class="iconbtn" type="button" data-act="part-del-ask" data-id="' + esc(p.id) + '" aria-label="Удалить чат" title="Удалить чат">' + ico('i-trash') + '</button>') + '</div>';
  }

  // ------------------------------------------------------------ лента
  function welcome() {
    var starters = [
      ['Подбери материал по ТЗ', 'Подберу оптимальный материал Stereotech по всем критериям ТЗ: нагрузка, температура, среда, запреты',
        'Подбери материал по ТЗ из вложения. Учти все обязательные требования и запреты, условия работы детали — нагрузку, температуру, контакт с маслом, топливом или химией, улицу и солнце. Покажи таблицу «требование × материал», объясни, почему выбран именно этот материал и чем хуже ближайшие альтернативы.'],
      ['3D или 5D?', 'Сравню режимы по нависаниям и поддержкам, выберу положение и диаметр базы 5D',
        'Выбери режим печати для этой детали: 3D, 5D Spiral или 5D. Сравни нависания и нужные поддержки в каждом режиме, учти ось вращения и отверстия, предложи положение на столе и диаметр базы для 5D. Объясни выбор и чем рискуем в другом режиме.'],
      ['Партия на один стол', 'Сделаю копии, расставлю их на столе и посчитаю время и стоимость партии',
        'Нужна партия из 5 штук на один стол. Расставь копии с зазором в рабочей зоне, нарежь и покажи общее время печати, расход материала и стоимость всей партии и одной детали. Если 5 не помещаются, скажи, сколько влезает за один раз.'],
      ['Как в прошлый раз', 'Найду похожую деталь в картотеке и возьму её настройки как шаблон',
        'Найди в картотеке похожую деталь, которую уже печатали, и возьми её настройки как шаблон: материал, режим, слой, заполнение, стенки, поддержки. Покажи, что взял, и что пришлось поменять под эту модель и почему.']
    ];
    var SICO = ['i-gem', 'i-rotate', 'i-grid', 'i-archive'];
    return '<div class="welcome">' +
      '<div class="hero"><i class="orb" aria-hidden="true"></i><h2><span>Здравствуйте!</span>Какую деталь печатаем?</h2></div>' +
      '<button class="drop" type="button" data-act="attach"><b>' + ico('i-upload') + 'Перетащите модель, ТЗ и фото узла</b>' +
      '<span>или <span class="pick">выберите файлы</span></span><small>Модель STL, OBJ, 3MF · ТЗ DOCX, TXT · фото JPG, PNG · до 10 файлов</small></button>' +
      '<div class="starters"><h2>Или начните с запроса</h2><ul>' + starters.map(function (s, i) {
        return '<li><button class="starter" type="button" data-act="' + (s[2] ? 'starter' : 'view') + '" data-v="' + (s[2] ? '' : 'catalog') + '" data-text="' + esc(s[2]) + '">' +
          '<span class="st-head"><b>' + esc(s[0]) + '</b><i class="sico">' + ico(SICO[i] || 'i-star') + '</i></span><span class="sd">' + esc(s[1]) + '</span><span class="go">' + (s[2] ? 'в поле ввода' : 'картотека') + ' →</span></button></li>';
      }).join('') + '</ul></div></div>';
  }

  var KIND_LABEL = { model: 'Модель', spec: 'ТЗ', image: 'Фото' };
  // Лента по сообщениям: каждая строка — один корневой элемент. Так app.js заменяет в DOM только
  // изменившиеся сообщения, а не всю ленту на каждое обновление.
  function feedItems(p, ui) {
    if (!p) return [];
    if (!p.messages.length) return [welcome()];
    var running = p.status === 'work';
    var out = p.messages.map(function (m, i) {
      if (m.role === 'user') return userMsg(m);
      var last = i === p.messages.length - 1;
      return aiMsg(m, p, ui, last && running);
    });
    if (running && p.messages[p.messages.length - 1].role === 'user') out.push(aiMsg({ blocks: [] }, p, ui, true));
    return out;
  }
  function feed(p, ui) { return feedItems(p, ui).join(''); }

  function userMsg(m) {
    var files = (m.files || []).map(function (f) {
      var k = fileKind(f.name);
      return '<div class="file"><span class="ext">' + esc(extOf(f)) + '</span><span class="fn"><b>' + esc(f.name) + '</b><small>' + esc(f.size || '') + '</small></span></div>';
    }).join('');
    return '<div class="u">' + (files ? '<div class="files">' + files + '</div>' : '') +
      (m.comment ? '<div class="ucomment"><b>Комментарий к запросу</b>' + esc(m.comment) + '</div>' : '') +
      (m.text ? '<div class="bubble">' + esc(m.text) + '</div>' : '') + (m.time ? '<span class="time">' + esc(m.time) + '</span>' : '') + '</div>';
  }

  function aiMsg(m, p, ui, working) {
    var blocks = (m.blocks || []).map(function (b) { return block(b, p, ui, working); }).join('');
    var meta = esc(m.time || '') + (m.duration ? ' · ' + m.duration + ' с' : '');
    if (working && !m.duration) meta = esc(m.time || '');  // работу показывают точки ниже, без подписи
    return '<div class="ai"><div class="who"><b>Ассистент</b><span>' + meta + '</span></div><div class="body">' + blocks +
      (working ? '<div class="typing" role="status" aria-label="Ассистент работает"><i></i><i></i><i></i></div>' : '') + '</div></div>';
  }

  function block(b, p, ui, working) {
    switch (b.type) {
      case 'text': return '<div class="prose">' + md(b.text) + '</div>';
      case 'trace': return trace(b, ui, working);
      case 'model': return modelCard(b);
      case 'reqs': return reqsCard(b, ui);
      case 'material': return materialCard(b);
      case 'modes': return modesCard(b, p, working);
      case 'orient': return orientCard(b);
      case 'ask': return askCard(b, ui);
      case 'questions': return questionsCard(b, ui);
      case 'plan': return planCard(b, ui, p);
      case 'slice': return sliceCard(b);
      case 'variants': return variantsCard(b);
      case 'diff': return diffCard(b);
      case 'notice': return noticeCard(b, p);
      case 'saved': return savedCard(b);
      case 'review': return reviewCard(b, p, ui, working);
      default: return '';
    }
  }

  function trace(b, ui, working) {
    var steps = b.steps || [];
    var done = steps.filter(function (s) { return s.status === 'done'; }).length;
    var errs = steps.filter(function (s) { return s.status === 'error'; }).length;
    var open = ui.open[b.steps[0] && b.steps[0].id] !== undefined ? ui.open[b.steps[0].id] : (working || b.open);
    var key = steps[0] ? steps[0].id : '';
    if (!open) {
      var tools = steps.map(function (s) { return s.status === 'error' ? '<span class="err">' + esc(s.tool) + '</span>' : esc(s.tool); }).join(' · ');
      return '<div class="card"><button class="trace-head" type="button" data-act="trace" data-id="' + esc(key) + '" data-open="1">' +
        (errs ? '<span class="glyph warnc">' + ico('i-alert') + '</span>' : '<span class="glyph done">' + ico('i-check') + '</span>') + '<b>Выполнил ' + steps.length + ' ' + plural(steps.length, 'действие', 'действия', 'действий') +
        ' в STE Slicer</b>' + (errs ? '<span class="tag warn">' + errs + ' с ошибкой</span>' : '') +
        '<span class="chev">Подробнее' + ico('i-chev-d', 'sm') + '</span><span class="trace-tools">' + tools + '</span></button></div>';
    }
    var rows = steps.map(function (s) {
      var ic = s.status === 'done' ? ico('i-check') : s.status === 'run' ? ico('i-spin', 'spin') : ico('i-x');
      var opened = ui.step[s.id];
      var det = '';
      if (opened) {
        det = '<div class="det">';
        var args = Object.keys(s.args || {});
        if (args.length) det += '<div>' + args.map(function (k) { return '<span class="muted">' + esc(k) + '</span> = ' + esc(JSON.stringify(s.args[k])); }).join('<br>') + '</div>';
        if (s.result) det += '<div><span class="muted">результат</span> ' + esc(s.result) + '</div>';
        if (s.changes && s.changes.length) {
          det += '<table class="chg"><thead><tr><th>настройка</th><th>было</th><th>стало</th></tr></thead><tbody>' + s.changes.map(function (c) {
            return '<tr><td>' + esc(c[0]) + '</td><td class="was">' + esc(c[1]) + '</td><td class="now">' + esc(c[2]) + '</td></tr>';
          }).join('') + '</tbody></table>';
        }
        det += '</div>';
      }
      return '<div class="step ' + esc(s.status) + '"><button type="button" data-act="step" data-id="' + esc(s.id) + '">' +
        '<span class="glyph ' + esc(s.status) + '">' + ic + '</span><span><span class="t">' + esc(s.title) + (s.external ? '<span class="extc">внешний клиент</span>' : '') +
        '</span><br><span class="s">' + esc(s.sum) + '</span></span><span class="tool">' + esc(s.tool) + (s.ms != null ? ' · ' + (s.ms / 1000).toFixed(1).replace('.', ',') + ' с' : '') + '</span></button>' + det + '</div>';
    }).join('');
    var pct = steps.length ? Math.round(done / steps.length * 100) : 0;
    return '<div class="card"><div class="trace-head"><b style="font-weight:600">Ход работы</b><span class="muted num" style="font-size:13px">' +
      (working ? done + ' из ' + steps.length : steps.length + ' ' + plural(steps.length, 'действие', 'действия', 'действий')) + '</span>' +
      (working ? '<span class="bar" style="margin-left:auto"><i style="width:' + pct + '%"></i></span>' :
        '<button class="btn text sm" type="button" data-act="trace" data-id="' + esc(key) + '" data-open="0" style="margin-left:auto">Свернуть</button>') +
      '</div><div class="steps">' + rows + '</div></div>';
  }

  // Силуэт из сетки сцены (geometry.silhouette): заливка, ось пунктиром, отверстие тонкими линиями.
  function silSvg(s) {
    var p = 6, w = s.w + 2 * p, h = s.h + 2 * p, out = '';
    if (s.bore) [s.axis - s.bore, s.axis + s.bore].forEach(function (x) {
      out += '<line class="bore" x1="' + x + '" x2="' + x + '" y1="0" y2="' + s.h + '"/>';
    });
    if (s.axis != null) out += '<line class="ax" x1="' + s.axis + '" x2="' + s.axis + '" y1="' + (-p) + '" y2="' + (s.h + p) + '"/>';
    return '<svg viewBox="' + (-p) + ' ' + (-p) + ' ' + w + ' ' + h + '" preserveAspectRatio="xMidYMid meet"><path class="body" d="' + esc(s.d) + '"/>' + out + '</svg>';
  }
  function modelCard(b) {
    var feats = (b.features || []).map(function (f) { return '<span class="tag' + (f[0] === 'ok' ? ' ok' : '') + '">' + esc(f[1]) + '</span>'; }).join('');
    var note = b.note ? '<div class="note ' + esc(b.note.kind === 'warn' ? 'warn' : 'info') + '">' + ico(b.note.kind === 'warn' ? 'i-alert' : 'i-rotate') + '<span>' + esc(b.note.text) + '</span></div>' : '';
    return '<div class="card"><div class="model"><div class="pic' + (b.sil ? ' sil' : '') + '" aria-hidden="true">' + (b.sil ? silSvg(b.sil) : ico(kindIcon(b.kind))) + '</div>' +
      '<div class="info"><div class="hrow"><b class="h16">' + esc(b.name) + '</b><span class="muted" style="font-size:13px">модель в сцене STE Slicer</span></div>' +
      '<div class="strip"><div><span>Габариты, мм</span><strong class="num">' + esc(b.dims) + '</strong></div><div><span>Объём</span><strong class="num">' + esc(b.volume) +
      '</strong></div><div><span>' + esc(b.massLabel || 'Масса') + '</span><strong class="num">' + esc(b.mass) + '</strong></div></div>' +
      (feats ? '<div class="chips">' + feats + '</div>' : '') + '</div></div>' + note + '</div>';
  }

  function reqsCard(b, ui) {
    var c = b.counts || {};
    var groups = (b.groups || []).map(function (g) {
      return '<div class="rgroup"><span class="label">' + esc(g.name) + '</span>' + g.items.map(function (r) {
        if (ui.editReq === r.tag) {
          return '<div class="redit"><div class="seg3">' + ['must', 'ban', 'want'].map(function (k) {
            return '<button type="button" data-act="req-kind" data-k="' + k + '" aria-pressed="' + (ui.editKind === k) + '">' + { must: 'обязательно', ban: 'запрещено', want: 'желательно' }[k] + '</button>';
          }).join('') + '</div><label class="sr" for="reqText">Текст требования</label><input class="input" id="reqText" value="' + esc(r.text) + '">' +
            '<div class="row"><button class="btn sm" type="button" data-act="req-cancel">Отмена</button><button class="btn primary sm" type="button" data-act="req-save" data-tag="' + esc(r.tag) + '">Сохранить и пересчитать</button></div></div>';
        }
        return '<div class="req"><span><span class="kind ' + esc(r.kind) + '">' + { must: 'обязательно', ban: 'запрещено', want: 'желательно' }[r.kind] + '</span></span>' +
          '<span><span>' + esc(r.text) + '</span>' + (r.quote ? '<span class="q">«' + esc(r.quote.replace(/^«|»$/g, '')) + '»' + (r.clause ? ' · ' + esc(r.clause) : '') + '</span>' : '') + '</span>' +
          '<span class="acts"><button class="iconbtn sm" type="button" data-act="req-edit" data-tag="' + esc(r.tag) + '" data-k="' + esc(r.kind) + '" aria-label="Изменить требование" title="Изменить">' + ico('i-edit', 'sm') + '</button>' +
          '<button class="iconbtn sm" type="button" data-act="req-del" data-tag="' + esc(r.tag) + '" aria-label="Удалить требование" title="Удалить">' + ico('i-trash', 'sm') + '</button></span></div>';
      }).join('') + '</div>';
    }).join('');
    var add = ui.addReq ? '<div class="redit"><label class="sr" for="reqNew">Новое требование</label><input class="input" id="reqNew" placeholder="Например: стойкость к бензину, плотность не более 1,2 г/см³">' +
      '<div class="row"><button class="btn sm" type="button" data-act="req-cancel">Отмена</button><button class="btn primary sm" type="button" data-act="req-add-save">Добавить и пересчитать</button></div></div>' :
      '<button class="btn text sm" type="button" data-act="req-add" style="align-self:flex-start;margin-left:-6px">' + ico('i-plus', 'sm') + 'Добавить требование</button>';
    return '<div class="card"><div class="card-head"><span style="min-width:0"><b class="ttl">Требования</b><br><span class="mono muted" style="font-size:12px;overflow-wrap:anywhere">' + esc(b.file) + '</span></span>' +
      '<span class="right muted num" style="font-size:13px">' + (c.must || 0) + ' обязательно · ' + (c.ban || 0) + ' запрещено · ' + (c.want || 0) + ' желательно</span></div>' +
      '<div class="reqs">' + (groups || '<span class="muted" style="padding:0 8px">Требований не нашёл.</span>') + add + '</div></div>';
  }

  function materialCard(b) {
    var head = '<th>Требование</th>' + b.cols.map(function (c, i) { return '<th class="' + (i === 0 ? 'sel' : '') + '">' + esc(c) + (i === 0 ? '<br><span class="chosen">выбран</span>' : '') + '</th>'; }).join('');
    var rows = b.rows.map(function (r) {
      var lab = (r.kind === 'ban' ? '<span class="kind ban">запрет · </span>' : r.kind === 'want' ? '<span class="kind">желательно · </span>' : '') + esc(r.text) +
        (r.clause ? ' <span class="muted">· ' + esc(r.clause) + '</span>' : '');
      return '<tr' + (r.kind === 'want' ? ' class="want"' : '') + '><td>' + lab + '</td>' + r.cells.map(function (c, i) {
        return '<td class="' + (i === 0 ? 'sel' : '') + '"><span class="cell ' + c + '">' + { yes: 'да', no: 'нет', nd: 'нет данных' }[c] + '</span></td>';
      }).join('') + '</tr>';
    }).join('');
    var fail = b.allFail ? '<div class="note bad">' + ico('i-alert') + '<span>Ни один материал не проходит все обязательные требования — взял ближайший. Проверьте требования.</span></div>' : '';
    var plain = (b.plain || []).filter(function (x) { return x[1]; }).map(function (x) {
      return '<p><b>' + esc(x[0]) + '</b> — ' + esc(x[1]) + '</p>';
    }).join('');
    if (plain) fail += '<div class="mat-plain">' + plain + '</div>';
    return '<div class="card"><div class="mat-top"><div class="hrow"><b>' + esc(b.pick.name) + '</b><span class="src">' + esc(b.pick.src) + '</span></div>' +
      (b.rows.length || !b.pick.why ? '' : '<span class="muted">' + esc(b.pick.why) + '</span>') + '</div>' +  // матрица сама показывает, почему подходит
      '<div class="scroll-x" style="border-top:1px solid var(--rule)"><table class="matrix"><thead><tr>' + head + '</tr></thead><tbody>' + rows + '</tbody></table></div>' + fail + '</div>';
  }

  function modeRow(r) {
    var icon = r[0] === 'ok' ? ['i-check', 'done'] : r[0] === 'bad' ? ['i-x', 'error'] : r[0] === 'info' ? ['i-info', 'muted'] : ['i-alert', 'warnc'];
    return '<div class="r"><span class="glyph ' + icon[1] + '">' + ico(icon[0], 'sm') + '</span><span><span class="muted">' + esc(r[1]) + ':</span> ' + esc(r[2]) + '</span></div>';
  }
  function modesCard(b, p, working, inPlan) {
    // Режим меняется нажатием на карточку способа — без отдельной кнопки.
    var dis = working || (p && p.status === 'work');
    var cols = b.cols.map(function (c) {
      var on = c.id === b.chosen;
      return '<button type="button" class="opt' + (on ? ' on' : '') + '" data-act="' + (inPlan ? 'plan-set" data-k="mode' : 'mode') + '" data-v="' + esc(c.id) + '" aria-pressed="' + on + '"' +
        (on || dis ? ' disabled' : '') + ' title="' + (on ? 'Сейчас печатаю так' : 'Печатать в ' + esc(c.id)) + '"><div class="hd"><b>' + esc(c.title) + '</b>' +
        (on ? '<span class="chosen">' + ico('i-check', 'sm') + '</span>' : '<span class="pick">Выбрать</span>') + '</div>' + c.rows.map(modeRow).join('') + '</button>';
    }).join('');
    return '<div class="card card-pad"><div class="hrow"><b class="h16">3D или 5D</b><span class="muted" style="font-size:13px">нажмите на способ, чтобы переключить</span>' +
      '</div><div class="cols2">' + cols + '</div>' +
      (b.reason ? '<span class="muted" style="font-size:13px">' + esc(b.reason) + '</span>' : '') + '</div>';
  }

  function orientCard(b) {
    return '<div class="card card-pad"><b class="h16">Положение на столе</b><div class="cols3">' + b.items.map(function (it, i) {
      return '<div class="opt' + (it.chosen ? ' on' : '') + '"><div class="hd"><b>' + esc(it.title) + '</b>' + (it.chosen ? '<span class="chosen">Выбрано</span>' :
        '<button class="btn text sm" type="button" data-act="orient" data-i="' + i + '">Выбрать</button>') + '</div>' +
        '<div class="rows">' + it.rows.map(function (r) { return '<span><i>' + esc(r[0]) + '</i><b>' + esc(r[1]) + '</b></span>'; }).join('') + '</div>' +
        '<span class="muted" style="font-size:12px;line-height:16px">' + esc(it.why) + '</span></div>';
    }).join('') + '</div></div>';
  }

  var PRESELECT = { surface: true, strength: true };
  // Все вопросы одной карточкой (S-8). Выбор хранится в ui.qSel[блок][вопрос] до «Ответить».
  function questionsCard(b, ui) {
    var items = b.items || [];
    if (b.answers) {
      var got = items.map(function (it) {
        var v = b.answers[it.id];
        return v ? '<span><i>' + esc(it.q.replace(/\?.*$/, '')) + '</i>' + esc(v.replace(/; /g, ', ')) + '</span>' : '';
      }).join('');
      if (b.answers.text) got = '<span>' + esc(b.answers.text) + '</span>';
      return '<div class="qcard done"><div class="qdone">' + ico('i-check', 'sm') + '<b>Ответы приняты</b>' +
        (got ? '<div class="qgot">' + got + '</div>' : '<span class="muted">по умолчанию</span>') + '</div></div>';
    }
    var sel = (ui && ui.qSel && ui.qSel[b.id]) || {};
    var any = false;
    // Режим, выбранный в этой же карточке (или рекомендованный): от него зависит, спрашивать ли количество.
    var modeIt = items.filter(function (x) { return x.id === 'mode'; })[0];
    var modeNow = (sel.mode && sel.mode[0]) || (modeIt && modeIt.rec) || '';
    var qs = items.map(function (it) {
      var picked = sel[it.id] || [];
      if (picked.length) any = true;
      var notes = it.notes || null;
      var opts, one = picked[0];
      // Прочность и поверхность: рекомендованный вариант выбран сразу — его можно просто подтвердить.
      if (!one && PRESELECT[it.id] && it.rec) { one = it.rec; any = true; }
      if (it.modeDep && modeNow === '5D') {
        return '<div class="qitem"><b>' + esc(it.q) + '</b><div class="note info inline-note">' + ico('i-info', 'sm') +
          '<span>В 5D печатается одна деталь за запуск. Чтобы напечатать партию на одном столе, выберите 3D.</span></div></div>';
      }
      var common = { kind: 'q', q: b.id, i: it.id, rec: it.rec, notes: notes, options: it.options, sel: one };
      if (it.widget === 'triangle' && window.Wd) {
        var w = one && one.indexOf('w:') === 0 ? one.slice(2).split(',').map(Number) :
          one ? [one === 'Быстро' ? 1 : 0, one === 'Дёшево' ? 1 : 0, one === 'Качественно' ? 1 : 0] : (it.recW || [0, 0, 1]);
        opts = Wd.triangle({ kind: 'q', q: b.id, i: it.id, w: w, recW: it.recW });
      } else if (it.widget === 'stepper' && window.Wd) {
        opts = Wd.stepper({ kind: 'q', q: b.id, i: it.id, n: parseInt(one || it.rec || '1', 10), perPlate: it.perPlate, min: it.min, max: it.max });
      } else if (it.widget === 'mode' && window.Wd) {
        opts = Wd.mode({ kind: 'q', q: b.id, i: it.id, cur: one || '', rec: it.rec, notes: notes });
      } else if ((it.widget === 'levels' || it.widget === 'segmented' || it.widget === 'tiles') && window.Wd) {
        opts = Wd[it.widget]({ kind: 'q', q: b.id, i: it.id, rec: it.rec, notes: notes, options: it.options, sel: one, icons: it.icons, sils: it.sils });
      } else if (notes && !it.multi) {
        // Варианты с пояснением: строка на вариант, рекомендованный отмечен.
        opts = '<div class="qchoices">' + it.options.map(function (o) {
          var on = picked.indexOf(o) >= 0;
          return '<button class="qchoice' + (on ? ' sel' : '') + '" type="button" aria-pressed="' + on + '" data-act="q-toggle" data-q="' + esc(b.id) + '" data-i="' + esc(it.id) + '" data-v="' + esc(o) + '">' +
            '<span class="qc-top"><b>' + esc(o) + '</b>' + (o === it.rec ? '<span class="rec">рекомендую</span>' : '') + '</span>' +
            (notes[o] ? '<span class="qc-note">' + esc(notes[o]) + '</span>' : '') + '</button>';
        }).join('') + '</div>' +
          '<div class="opts"><button class="chip self' + (picked.indexOf('Реши сам') >= 0 ? ' sel' : '') + '" type="button" data-act="q-toggle" data-q="' + esc(b.id) + '" data-i="' + esc(it.id) + '" data-v="Реши сам">Реши сам</button></div>';
      } else {
        opts = '<div class="opts">' + it.options.concat(['Реши сам']).map(function (o) {
          var on = picked.indexOf(o) >= 0;
          var self = o === 'Реши сам';
          if (it.multi && !self) {
            return '<button class="chip check' + (on ? ' sel' : '') + '" type="button" role="checkbox" aria-checked="' + on + '" data-act="q-toggle" data-q="' + esc(b.id) + '" data-i="' + esc(it.id) + '" data-v="' + esc(o) + '">' +
              '<span class="box">' + (on ? ico('i-check', 'sm') : '') + '</span>' + esc(o) + '</button>';
          }
          return '<button class="chip' + (self ? ' self' : '') + (on ? ' sel' : '') + '" type="button" aria-pressed="' + on + '" data-act="q-toggle" data-q="' + esc(b.id) + '" data-i="' + esc(it.id) + '" data-v="' + esc(o) + '">' + esc(o) + '</button>';
        }).join('') + '</div>';
      }
      return '<div class="qitem"><b>' + esc(it.q) + '</b>' + opts +
        (it.def ? '<span class="def">' + esc(it.def) + '</span>' : '') + '</div>';
    }).join('');
    var n = items.length;
    var count = n === 1 ? '1 вопрос' : n + ' ' + plural(n, 'вопрос', 'вопроса', 'вопросов');
    return '<div class="qcard"><div class="qtop"><b class="h16">' + esc(b.title || 'Вопросы') + '</b><span class="muted">' + count + '</span></div>' +
      '<div class="qlist">' + qs + '</div>' +
      '<div class="qfoot"><span class="muted">Можно ответить своими словами в поле ниже</span>' +
      '<button class="btn text" type="button" data-act="q-skip" data-q="' + esc(b.id) + '">' + esc(b.skip || 'Пропустить') + '</button>' +
      '<button class="btn primary" type="button" data-act="q-submit" data-q="' + esc(b.id) + '"' + (any ? '' : ' disabled') + '>Ответить</button></div></div>';
  }

  // План печати (S-23): «параметр — значение — почему», правка в строке, «Подтвердить и нарезать».
  function planCard(b, ui, p) {
    if (b.status === 'confirmed') {
      return '<div class="plan done"><div class="qdone">' + ico('i-check', 'sm') + '<b>План подтверждён</b><span class="muted">' + esc(b.head || '') + '</span></div></div>';
    }
    var open = (ui && ui.planOpen) || {};
    var why = (ui && ui.planWhy) || {};
    var rows = (b.rows || []).map(function (r) {
      var can = r.options && r.options.length > 1;
      var val = can ? '<button class="pval" type="button" data-act="plan-open" data-k="' + esc(r.key) + '" aria-expanded="' + !!open[r.key] + '">' +
        esc(r.v) + ico('i-chev-d', 'sm') + '</button>' : '<span class="pval fixed">' + esc(r.v) + '</span>';
      var inline = '';
      if (window.Wd && r.widget === 'slider') { inline = Wd.slider({ key: r.key, n: r.n, min: r.min, max: r.max, step: r.step, stops: r.stops, rec: r.rec }); can = false; val = ''; }
      else if (window.Wd && r.widget === 'stepper') { inline = Wd.stepper({ kind: 'plan', key: r.key, n: r.n, perPlate: r.perPlate, min: r.min, max: r.max }); can = false; val = ''; }
      else if (window.Wd && r.widget === 'mode') { inline = Wd.mode({ kind: 'plan', cur: r.cur, notes: {} }); can = false; val = r.cur === '5D' ? '<span class="pval fixed">' + esc(r.v) + '</span>' : ''; }
      var reason = r.src === 'assume' ? '<span class="as">Допущение:</span> ' + esc(r.why) : esc(r.why);
      if (r.changed) reason = 'Изменено' + (r.why && r.src !== 'words' ? ' · ' + esc(r.why) : '');
      var link = '';
      if (r.key === 'material' && b.material) link = ' <button class="linkbtn more" type="button" data-act="plan-why" data-k="material">' + (why.material ? 'скрыть таблицу' : 'почему этот') + '</button>';
      if (r.key === 'mode' && b.modes) link = ' <button class="linkbtn more" type="button" data-act="plan-why" data-k="mode">' + (why.mode ? 'скрыть сравнение' : 'почему ' + esc(b.modes.chosen)) + '</button>';
      var opts = can && open[r.key] && r.widget === 'triangle' && window.Wd ? '<div class="popts tri-pop">' + Wd.triangle({ kind: 'plan', key: r.key, w: r.w }) + '</div>' :
        can && open[r.key] ? '<div class="popts">' + r.options.map(function (o) {
        return '<button class="chip' + (o === r.v ? ' sel' : '') + '" type="button" data-act="plan-set" data-k="' + esc(r.key) + '" data-v="' + esc(o) + '">' + esc(o) + '</button>';
      }).join('') + '</div>' : '';
      var extra = '';
      if (r.key === 'material' && why.material && b.material) extra = '<div class="pwhy">' + materialCard(b.material) + '</div>';
      if (r.key === 'mode' && why.mode && b.modes) extra = '<div class="pwhy">' + modesCard(b.modes, null, false, true) + '</div>';
      return '<div class="prow2' + (r.changed ? ' ch' : '') + '"><span class="k">' + esc(r.k) + '</span><span class="v">' + val + inline +
        (reason || link ? '<span class="why">' + reason + link + '</span>' : '') + opts + '</span>' + extra + '</div>';
    }).join('');
    var live = b.status === 'wait' ? liveCombos(p) : {}, pbusy = p && p.status === 'work';
    var warns = (b.warnings || []).map(function (w) {
      return typeof w === 'string' ? '<div class="note warn inline-note">' + ico('i-alert') + '<span>' + esc(w) + '</span></div>' : comboNote(w, live, pbusy);
    }).join('');
    return '<div class="plan"><div class="ptop"><b class="h16">План печати</b><span class="muted">' + esc(b.head || '') + '</span></div>' +
      '<div class="prows">' + rows + '</div>' + (warns ? '<div class="pwarn">' + warns + '</div>' : '') +
      '<div class="qfoot"><span class="muted">До подтверждения в слайсере меняется только положение модели</span>' +
      '<button class="btn text" type="button" data-act="plan-words">Изменить словами</button>' +
      '<button class="btn primary" type="button" data-act="plan-confirm">Подтвердить и нарезать</button></div></div>';
  }

  function askCard(b, ui) {
    var done = !!b.answer;
    var multi = !!b.multi && !done;
    var answered = done ? String(b.answer).split(/\s*;\s*/) : [];
    var picked = multi ? ((ui && ui.askSel && ui.askSel[b.id]) || []) : [];
    var opts = b.options.map(function (o) {
      var on = done ? answered.indexOf(o) >= 0 : picked.indexOf(o) >= 0;
      if (multi) {
        return '<button class="chip check' + (on ? ' sel' : '') + '" type="button" role="checkbox" aria-checked="' + on + '" data-act="ask-toggle" data-q="' + esc(b.id) + '" data-v="' + esc(o) + '">' +
          '<span class="box">' + (on ? ico('i-check', 'sm') : '') + '</span>' + esc(o) + '</button>';
      }
      return '<button class="chip' + (on ? ' sel' : '') + '" type="button" data-act="answer" data-q="' + esc(b.id) + '" data-v="' + esc(o) + '"' + (done ? ' disabled' : '') + '>' + esc(o) + '</button>';
    }).join('') + '<button class="chip self' + (b.answer === 'Реши сам' ? ' sel' : '') + '" type="button" data-act="answer" data-q="' + esc(b.id) + '" data-v="Реши сам"' + (done ? ' disabled' : '') + '>' + ico('i-bolt', 'sm') + 'Реши сам</button>';
    var submit = multi ? '<div class="ask-go"><span class="muted">' + (picked.length ? 'Выбрано: ' + picked.length : 'Отметьте всё, что подходит') + '</span>' +
      '<button class="btn primary" type="button" data-act="ask-submit" data-q="' + esc(b.id) + '"' + (picked.length ? '' : ' disabled') + '>Ответить</button></div>' : '';
    return '<div class="ask' + (done ? ' done' : '') + '">' +
      '<div class="qq">' + esc(b.q) + '</div><div class="opts">' + opts + '</div>' + submit +
      (done ? '' : '<div class="def">' + esc(b.def) + ' Можно ответить своими словами в поле ниже.</div>') + '</div>';
  }

  function dur(sec) { sec = sec || 0; var m = Math.floor(sec / 60), s = sec % 60; return m ? m + ' мин' + (s ? ' ' + s + ' с' : '') : s + ' с'; }
  function sliceNote(b) {
    var parts = [];
    if (b.elapsed) parts.push(dur(b.elapsed));
    if (b.eta != null && b.eta > 0) parts.push('осталось ≈ ' + dur(Math.max(5, Math.round(b.eta / 5) * 5)));
    else if (b.eta === 0) parts.push('дольше обычного');
    return parts.join(' · ');
  }
  function sliceCard(b) {
    if (b.error) return failCard(b.fix || { error: b.error, steps: [], actions: [] }, true);
    if (b.progress < 100 || !b.time) {
      var pct = b.progress || 0;
      return '<div class="card card-pad"><div class="hrow"><b class="h16">Нарезка</b><span class="muted" style="font-size:13px">' + esc(b.stage || 'STE Slicer') + '</span>' +
        '<span class="pct">' + (b.estimated ? '≈ ' : '') + pct + ' %</span></div>' +
        '<div class="progress live" role="progressbar" aria-valuemin="0" aria-valuemax="100" aria-valuenow="' + pct + '" aria-label="Прогресс нарезки"><i style="width:' + pct + '%"></i></div>' +
        (b.steps ? '<ol class="sstages">' + b.steps.map(function (x) { return '<li class="' + x[1] + '">' + (x[1] === 'done' ? ico('i-check', 'sm') : x[1] === 'run' ? ico('i-spin', 'sm spin') : '<span class="dot"></span>') + esc(x[0]) + '</li>'; }).join('') + '</ol>' : '') +
        '<span class="muted" style="font-size:13px">' + sliceNote(b) + '</span></div>';
    }
    var split = (b.split || []).filter(function (s) { return s[1] > 0; });
    var checks = (b.checks || []).map(function (c) {
      var ic = c[0] === 'ok' ? ['i-check', 'done'] : c[0] === 'warn' ? ['i-alert', 'warnc'] : ['i-x', 'error'];
      return '<div><span class="glyph ' + ic[1] + '">' + ico(ic[0], 'sm') + '</span><span>' + esc(c[1]) + '</span><span>' + esc(c[2]) + '</span></div>';
    }).join('');
    var nOk = (b.checks || []).filter(function (c) { return c[0] === 'ok'; }).length;
    return '<div class="card card-pad"><div class="hrow"><b class="h16">Нарезано</b><span class="tag ok">' + nOk + ' из ' + (b.checks || []).length + ' проверок</span></div>' +
      '<div class="strip" style="--n:4"><div><span>Время печати</span><strong>' + esc(b.time) + '</strong></div><div><span>Материал</span><strong>' + esc(b.mass) +
      '</strong></div><div><span>Длина нити</span><strong>' + esc(b.length || '—') + '</strong></div><div><span>Стоимость</span><strong>' + esc(b.cost) + '</strong></div></div>' +
      (split.length ? '<div class="split">' + split.map(function (s) { return '<i class="' + s[2] + '" style="width:' + s[1] + '%"></i>'; }).join('') + '</div><div class="legend">' +
        split.map(function (s) { return '<span><i class="' + s[2] + '"></i>' + esc(s[0]) + ' ' + s[1] + ' %</span>'; }).join('') + '</div>' : '') +
      '<div class="checks">' + checks + '</div></div>';
  }

  var VAR = { fast: ['Быстро', 'time', 'быстрее всех'], cheap: ['Дёшево', 'cost', 'меньше материала'], quality: ['Качественно', 'layer', 'тоньше слой'] };
  function variantsCard(b) {
    if (!b.variants) {
      return '<div class="card card-pad"><div class="hrow"><b class="h16">Варианты печати</b><span class="muted num" style="font-size:13px">считаю ' +
        (b.progress || 1) + ' из ' + (b.of || 3) + '</span></div><div class="progress"><i style="width:' + Math.round(((b.progress || 1) - 0.5) / (b.of || 3) * 100) + '%"></i></div></div>';
    }
    var cards = ['fast', 'cheap', 'quality'].filter(function (k) { return b.variants[k]; }).map(function (k) {
      var v = b.variants[k]; var d = VAR[k]; var on = b.current === k;
      var rows = [['Слой', v.layer], ['Время', v.time], ['Материал', v.mass], ['Стоимость', v.cost]].filter(function (r) {
        return !(d[1] === 'time' && r[0] === 'Время') && !(d[1] === 'cost' && r[0] === 'Стоимость') && !(d[1] === 'layer' && r[0] === 'Слой');
      });
      return '<button class="opt' + (on ? ' on' : '') + '" type="button" data-act="variant" data-v="' + k + '" aria-pressed="' + on + '"><span class="hd"><b>' + d[0] + '</b>' +
        (on ? '<span class="chosen">Выбрано</span>' : '<span class="muted" style="font-size:12px">' + d[2] + '</span>') + '</span>' +
        '<span class="vbig">' + esc(v[d[1]]) + '</span><span class="rows">' + rows.map(function (r) { return '<span><i>' + r[0] + '</i><b style="font-weight:400">' + esc(r[1]) + '</b></span>'; }).join('') + '</span></button>';
    }).join('');
    var locks = (b.locks || []).map(function (l) { return '<span>' + ico('i-lock', 'sm') + esc(l) + '</span>'; }).join('');
    return '<div class="card card-pad"><b class="h16">Варианты печати</b><div class="cols3">' + cards + '</div>' +
      (locks ? '<div class="locks"><span class="label">Не меняется ни в одном варианте — так требует ТЗ</span>' + locks + '</div>' : '') + '</div>';
  }

  function delta(a, b, unit) {
    if (a == null || b == null) return '';
    var d = b - a; if (Math.abs(d) < 0.5) return '';
    var v = Math.round(Math.abs(d));
    // Минуты больше часа — в часах, как и само время печати.
    var txt = unit === 'мин' && v >= 60 ? Math.floor(v / 60) + ' ч' + (v % 60 ? ' ' + v % 60 + ' мин' : '') : v + ' ' + unit;
    return '<small class="' + (d < 0 ? 'good' : 'badc') + '">' + (d < 0 ? '−' : '+') + txt + ' к прежнему</small>';
  }
  function diffCard(b) {
    var rows = (b.rows || []).map(function (r) {
      return '<tr><td>' + esc(r[0]) + '</td><td class="was">' + esc(r[1]) + '</td><td class="now">' + esc(r[2]) + '</td><td class="why">' + esc(r[3]) + '</td></tr>';
    }).join('');
    var A = b.after, B = b.before;
    var tiles = A ? '<div style="padding:4px 16px 16px"><div class="strip"><div><span>Время печати</span><strong>' + esc(A.time) + '</strong>' + delta(B && B.time_s / 60, A.time_s / 60, 'мин') +
      '</div><div><span>Материал</span><strong>' + esc(A.mass) + '</strong>' + delta(B && B.grams, A.grams, 'г') + '</div><div><span>Стоимость</span><strong>' + esc(A.cost) + '</strong></div></div></div>' : '';
    return '<div class="card' + (b.undone ? ' undone' : '') + '"><div class="card-head"><b class="ttl">' + esc(b.title || 'Было → стало') + '</b>' +
      '<span class="right">' + (b.undone ? '<span class="tag">Отменено</span>' : b.expired ? '<span class="tag" title="Отменить можно последние 15 изменений">Давнее</span>' : '<button class="btn sm" type="button" data-act="undo" data-id="' + esc(b.id) + '">' + ico('i-undo', 'sm') + 'Отменить</button>') + '</span></div>' +
      (rows ? '<div class="scroll-x" style="padding:0 16px"><table class="diff"><thead><tr><th>Параметр</th><th>Было</th><th>Стало</th><th>Почему</th></tr></thead><tbody>' + rows + '</tbody></table></div>' : '') + tiles + '</div>';
  }

  function failCard(f, inChat) {
    var steps = (f.steps || []).map(function (x) { return '<li>' + md(x).replace(/^<p>|<\/p>$/g, '') + '</li>'; }).join('');
    var acts = (f.actions || []).map(function (a, i) {
      return '<button class="btn' + (i === f.actions.length - 1 && inChat ? ' primary' : '') + '" type="button" data-act="suggest" data-text="' + esc(a[1]) + '">' + esc(a[0]) + '</button>';
    }).join('');
    return '<div class="failcard' + (inChat ? '' : ' compact') + '"><div class="fhead"><span class="ic">' + ico('i-alert', 'lg') + '</span><span><b>Нарезка не удалась</b>' +
      '<span>' + esc(f.error) + '</span></span></div>' + (f.why ? '<p class="fwhy">' + esc(f.why) + '</p>' : '') +
      (steps ? '<span class="label">Как исправить</span><ol class="fsteps">' + steps + '</ol>' : '') +
      (acts ? '<div class="facts">' + acts + '</div>' : '') + '</div>';
  }

  // Предупреждение о сочетании с кнопками исправления. Кнопки — только пока такое сочетание есть в
  // паспорте (live) и ассистент не занят; иначе — просто текст.
  function liveCombos(p) {
    var out = {};
    ((p && p.passport && p.passport.warnings) || []).forEach(function (w) { if (w.id) out[w.id] = true; });
    return out;
  }
  function comboNote(w, live, busy, cls) {
    var fixes = live && w.id && live[w.id] ? (w.fixes || []) : [];
    var btns = fixes.map(function (f, i) {
      return '<button class="btn sm" type="button" data-act="fix" data-w="' + esc(w.id) + '" data-i="' + i + '" data-label="' + esc(f.label) + '"' + (busy ? ' disabled' : '') + '>' + esc(f.label) + '</button>';
    }).join('');
    return '<div class="note ' + (w.kind === 'bad' ? 'bad' : 'warn') + ' ' + (cls || 'inline-note') + (btns ? ' has-fix' : '') + '">' + ico('i-alert', 'sm') +
      '<span class="nt">' + esc(w.text) + (btns ? '<span class="fixes">' + btns + '</span>' : '') + '</span></div>';
  }

  function noticeCard(b, p) {
    if (b.combos && b.combos.length) {
      var live = liveCombos(p), busy = p && p.status === 'work';
      return '<div class="combo-notice"><b class="h14">' + esc(b.title) + '</b>' + b.combos.map(function (w) { return comboNote(w, live, busy); }).join('') + '</div>';
    }
    if (b.kind === 'bad') {
      return '<div class="err-big" role="alert"><span class="ic">' + ico('i-alert') + '</span><span><b>' + esc(b.title) + '</b><span>' + esc(b.text) + '</span></span></div>';
    }
    return '<div class="note inline-note ' + esc(b.kind === 'warn' ? 'warn' : b.kind) + '">' + ico(b.kind === 'warn' ? 'i-alert' : 'i-info') +
      '<span><b>' + esc(b.title) + '</b> — ' + esc(b.text) + '</span></div>';
  }

  // Разбор напечатанной детали по фото: что видно, что не так, какие параметры поменять.
  function reviewCard(b, p, ui, working) {
    var sel = ui.rvSel[b.id] || b.selected || [];
    var done = b.state === 'applied', busy = working || (p && p.status === 'work');
    var found = (b.defects || []).map(function (d) {
      var c = (b.catalog || []).filter(function (x) { return x.id === d.id; })[0] || { title: d.id };
      return '<li><b>' + esc(c.title) + '</b>' + (d.where ? ' — ' + esc(d.where) : '') + '<span class="conf">' + Math.round(d.confidence * 100) + ' %</span></li>';
    }).join('');
    var q = b.question ? '<div class="note warn">' + ico('i-q', 'sm') + '<span><b>Не понял по фото.</b> ' + esc(b.question) +
      '<br><button class="btn text sm" type="button" data-act="review-photo">' + ico('i-camera', 'sm') + 'Приложить другое фото</button></span></div>' : '';
    var chips = done ? '' : '<div class="rv-pick"><span class="label">Что не так с деталью</span><div class="rv-chips" role="group">' + (b.catalog || []).map(function (c) {
      var on = sel.indexOf(c.id) >= 0;
      return '<button type="button" data-act="rv-toggle" data-id="' + esc(b.id) + '" data-v="' + esc(c.id) + '" aria-pressed="' + on + '" title="' + esc(c.signs) + '"' + (busy ? ' disabled' : '') + '>' + esc(c.title) + '</button>';
    }).join('') + '</div>' + (ui.rvSel[b.id] ? '<button class="btn sm" type="button" data-act="rv-choose" data-id="' + esc(b.id) + '"' + (busy || !sel.length ? ' disabled' : '') + '>Подобрать исправления</button>' : '') + '</div>';
    var fx = (b.fixes || []).length && !ui.rvSel[b.id] ? '<div class="scroll-x"><table class="diff"><thead><tr><th>Параметр</th><th>Сейчас</th><th>Станет</th><th>Почему</th></tr></thead><tbody>' +
      b.fixes.map(function (f) { return '<tr><td>' + esc(f.label) + '</td><td class="num">' + esc(f.fromText) + '</td><td class="num"><b>' + esc(f.toText) + '</b></td><td>' + esc(f.defect) + ': ' + esc(f.why) + '</td></tr>'; }).join('') +
      '</tbody></table></div>' : '';
    var none = b.state === 'nofix' && !ui.rvSel[b.id] ? '<p class="muted" style="font-size:13px;margin:0">Для этого дефекта параметры печати менять не нужно — проверьте модель и стол.</p>' : '';
    var act = done ? '<span class="tag ok">' + ico('i-check', 'sm') + 'Исправления применены</span>' :
      (b.fixes || []).length && !ui.rvSel[b.id] ? '<button class="btn primary" type="button" data-act="rv-apply" data-id="' + esc(b.id) + '"' + (busy ? ' disabled' : '') + '>Применить и перенарезать</button>' : '';
    return '<div class="card card-pad review"><div class="hrow"><span class="glyph">' + ico('i-camera') + '</span><b class="h16">Разбор печати по фото</b>' +
      '<span class="muted" style="font-size:13px">' + esc((b.photos || []).join(', ') || 'без фото') + (b.seen_by ? ' · посмотрела ' + esc(b.seen_by.split('/').pop()) : '') + '</span></div>' +
      q + (found ? '<ul class="rv-found">' + found + '</ul>' : '') + (b.notes ? '<p class="muted" style="font-size:13px;margin:0">' + esc(b.notes) + '</p>' : '') +
      chips + fx + none + (act ? '<div class="facts">' + act + '</div>' : '') + '</div>';
  }

  function savedCard(b) {
    return '<div class="card card-pad"><div class="hrow"><span class="glyph done">' + ico('i-check') + '</span><b class="h16">Сохранено</b><span class="muted" style="font-size:13px">запись добавлена в картотеку</span></div>' +
      '<div class="paths">' + (b.paths || []).map(function (p) {
        return '<div><span class="ext">' + (/gcode$/i.test(p) ? 'GCODE' : '3MF') + '</span><span class="mono">' + esc(p) + '</span></div>';
      }).join('') + '</div><button class="btn sm" type="button" data-act="open-folder" data-path="' + esc((b.paths || [''])[0]) + '" style="align-self:flex-start">' + ico('i-folder', 'sm') + 'Открыть папку</button></div>';
  }

  // ------------------------------------------------------------ паспорт
  function passport(p, ui) {
    var pp = p && p.passport;
    var head = '<div class="pp-head"><h2>Паспорт печати</h2>';
    if (!pp) {
      return '<div class="pp">' + head + '</div><div class="answer"><span class="fig none">Ещё не нарезано</span></div>' +
        '<div class="pp-empty">Здесь будут время печати, расход и стоимость, режим и материал — и откуда взялось каждое решение.</div></div>';
    }
    // Статус детали — только у её названия в шапке чата; здесь — «вернуть исходные», если что-то меняли.
    head += (pp.overridden && pp.overridden.length && p.status !== 'plan' ? '<button class="btn text sm" type="button" data-act="preset-all"' + (p.status === 'work' ? ' disabled' : '') +
      ' title="Убрать ваши правки: ' + esc(pp.overridden.length) + '">' + ico('i-undo', 'sm') + 'Вернуть исходные</button>' : '') + '</div>';
    if (pp.failed && p.status !== 'work') {
      return '<div class="pp">' + head + failCard(pp.failed, false) +
        '<p class="muted" style="font-size:13px;line-height:18px">Параметры печати появятся после успешной нарезки — без неё они не проверены.</p></div>';
    }
    var t = pp.totals;
    // Ответ экрана: сколько печатать и сколько это стоит — самое крупное в паспорте.
    var answer = '<div class="answer"><span class="label">Время печати</span>' + (t ? '<span class="fig">' + esc(t.time) + '</span><span class="sub">' + esc(t.mass) + ' · ' + esc(t.cost) + '</span>' :
      '<span class="fig none">после нарезки</span>') + '</div>';
    var what = '<div class="pp-what"><span class="mode"><strong>' + esc(pp.modeShort) + '</strong><span class="mono">' + esc(pp.mode) + '</span></span><b>' + esc(pp.material) + '</b>' +
      '<small>' + esc(pp.printer) + ' · ' + esc(pp.quality) + '</small></div>';
    // Недопустимые и рискованные сочетания — сразу под тем, что печатаем.
    var plive = liveCombos(p);
    what += (pp.warnings || []).map(function (w) { return comboNote(w, plive, p.status === 'work', 'pp-warn'); }).join('');
    var busy0 = p.status === 'work';
    var rows = pp.rows.map(function (r) { return prow(r, ui, busy0); }).join('');
    var params = pp.params && pp.params.length ? '<div class="pp-sec"><span class="label">Параметры печати</span><div>' +
      pp.params.map(function (r) { return prow(r, ui, busy0); }).join('') + '</div>' +
      '<button class="btn text sm pp-all" type="button" data-act="all-settings">' + ico('i-sliders', 'sm') + 'Все настройки STE Slicer</button></div>' : '';
    var assume = pp.assumptions && pp.assumptions.length ? '<div class="assume-box"><span class="label">' + ico('i-q', 'sm') + 'Я предположил — поменяйте, если не так</span>' + pp.assumptions.map(function (a) {
      return '<div style="display:flex;flex-direction:column;gap:8px"><span style="font-size:13px;line-height:18px">' + esc(a.text) + '</span><div class="aopts">' + a.options.map(function (o) {
        return '<button type="button" class="' + (o === a.value ? 'on' : '') + '" data-act="assume" data-k="' + esc(a.key) + '" data-v="' + esc(o) + '" aria-pressed="' + (o === a.value) + '">' + esc(o) + '</button>';
      }).join('') + '</div></div>';
    }).join('') + '</div>' : '';
    var prep = pp.prep && pp.prep.length ? '<div class="pp-sec"><span class="label">Подготовка материала</span><div class="prep">' +
      pp.prep.map(function (x) { return '<span>' + ico('i-check', 'sm') + esc(x) + '</span>'; }).join('') + '</div></div>' : '';
    var sliced = !!t;
    var busy = p.status === 'work';
    var dis = busy ? ' disabled' : '';
    if (p.status === 'plan') {  // до подтверждения плана нарезать можно только через подтверждение
      return '<div class="pp">' + head + answer + what + '<div class="pp-sec"><span class="label">Почему так</span><div>' + rows + '</div></div>' +
        params + assume + prep + '<div class="pp-actions"><button class="btn primary block" type="button" data-act="plan-confirm">Подтвердить и нарезать</button></div></div>';
    }
    var review = sliced || p.status === 'saved' ? '<button class="btn block" type="button" data-act="review-photo"' + dis + '>' + ico('i-camera', 'sm') + 'Разобрать печать по фото</button>' : '';
    var actions = '<div class="pp-actions">' + review + (sliced ? '<button class="btn primary block" type="button" data-act="save-open"' + dis + '>Сохранить проект 3MF</button>' +
      '<div class="two"><button class="btn" type="button" data-act="save-open" data-gcode="1"' + dis + '>Экспорт G-code</button><button class="btn" type="button" data-act="slice"' + dis + '>Нарезать заново</button></div>' :
      '<button class="btn primary block" type="button" data-act="slice"' + dis + '>Нарезать</button>' +
      '<div class="two"><button class="btn" type="button" data-act="variants"' + dis + '>Варианты</button><button class="btn" type="button" data-act="save-open"' + dis + '>Сохранить</button></div>') + '</div>';
    return '<div class="pp">' + head + answer + what + '<div class="pp-sec"><span class="label">Почему так</span><div>' + rows + '</div></div>' +
      params + assume + prep + actions + '</div>';
  }

  // Строка паспорта: подпись · значение · источник; раскрывается — причина, правка, «вернуть исходное».
  function prow(r, ui, busy) {
    var id = r.key || r.k, open = !!ui.prow[id];
    var src = r.changed ? '<span class="src mine">вы</span>' : '<span class="src ' + (r.src === 'assume' ? 'assume' : '') + '">' + esc(SRC[r.src] || r.src || '') + '</span>';
    var body = '';
    if (open) {
      body = '<div class="why">' + (r.why ? '<span>' + esc(r.why) + '</span>' : '') +
        (r.lock ? '<small>Задано ТЗ — меняется в требованиях.</small>' : editor(r.key, r.edit, busy)) +
        (r.changed ? '<button class="btn text sm" type="button" data-act="preset" data-key="' + esc(r.key) + '"' + (busy ? ' disabled' : '') + '>' + ico('i-undo', 'sm') + 'Вернуть исходное</button>' : '') + '</div>';
    }
    return '<div class="prow' + (r.changed ? ' changed' : '') + '"><button type="button" data-act="prow" data-k="' + esc(id) + '" aria-expanded="' + open + '"><span class="k">' + esc(r.k) + '</span><span class="v">' + esc(r.v) +
      '</span>' + src + '</button>' + body + '</div>';
  }

  // Поле правки по типу параметра STE Slicer: да/нет, варианты, число с единицами; режим — 3D/5D.
  function editor(key, e, busy) {
    if (!e || !key) return '';
    var dis = busy ? ' disabled' : '';
    var chips = function (opts, act) {
      return '<div class="ped-chips" role="group">' + opts.map(function (o) {
        var on = String(o[0]) === String(e.value);
        return '<button type="button" data-act="' + act + '" data-key="' + esc(key) + '" data-v="' + esc(o[0]) + '" aria-pressed="' + on + '"' + dis + '>' + esc(o[1]) + '</button>';
      }).join('') + '</div>';
    };
    if (e.type === 'mode') return chips([['3D', '3D'], ['5D', '5D']], 'mode');
    if (e.type === 'bool') return chips([[true, 'Да'], [false, 'Нет']], 'pset');
    var fid = 'ped-' + key.replace(/[^a-z0-9_]/gi, '');
    if (e.type === 'enum') {
      if ((e.options || []).length <= 4) return chips(e.options, 'pset');
      return '<div class="ped"><select class="input" id="' + fid + '"' + dis + '>' + e.options.map(function (o) {
        return '<option value="' + esc(o[0]) + '"' + (String(o[0]) === String(e.value) ? ' selected' : '') + '>' + esc(o[1]) + '</option>';
      }).join('') + '</select><button class="btn sm" type="button" data-act="pset-field" data-key="' + esc(key) + '" data-for="' + fid + '"' + dis + '>Применить</button></div>';
    }
    var lim = [e.min != null ? 'от ' + String(e.min).replace('.', ',') : '', e.max != null ? 'до ' + String(e.max).replace('.', ',') : ''].filter(Boolean).join(' ');
    return '<div class="ped"><span class="ped-in"><input class="input" id="' + fid + '" inputmode="decimal" data-key="' + esc(key) + '" value="' + esc(e.value == null ? '' : String(e.value).replace('.', ',')) + '"' +
      ' aria-label="Новое значение"' + dis + '>' + (e.unit ? '<span class="unit">' + esc(e.unit) + '</span>' : '') + '</span>' +
      '<button class="btn sm" type="button" data-act="pset-field" data-key="' + esc(key) + '" data-for="' + fid + '"' + dis + '>Применить</button>' +
      (lim ? '<small>' + esc(lim) + '</small>' : '') + '</div>';
  }

  window.R = { esc: esc, ico: ico, md: md, plural: plural, kindIcon: kindIcon, fileKind: fileKind, extOf: extOf, status: status, side: side, chatHead: chatHead,
    feed: feed, feedItems: feedItems, passport: passport, editor: editor, UNITS: UNITS, SRC: SRC };
})();
