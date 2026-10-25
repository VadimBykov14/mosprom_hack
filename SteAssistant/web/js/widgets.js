/* Виджеты выбора: у каждого вопроса — свой способ ответа, а не одинаковый список.
 *
 *   triangle  — «быстро · дёшево · качественно»: точка внутри треугольника = смесь вариантов
 *   slider    — плотность заполнения: ползунок и живой срез заполнения
 *   stepper   — количество: − N + и стол, на котором видно, сколько помещается за запуск
 *   mode      — 3D или 5D: плоские слои или кольца вокруг оси
 *   levels    — прочность: уровни, как индикатор сигнала
 *   segmented — короткий выбор с иконками (поверхность, поддержки)
 *   tiles     — положение на столе: силуэт детали в каждом положении
 *
 * Разметка строится строками (как в render.js). Перетаскивание треугольника и ползунок меняют DOM
 * напрямую, без перерисовки ленты; итог передаётся в Wd.commit(вид, элемент, значение), который
 * задаёт app.js.
 */
(function () {
  'use strict';

  function esc(s) {
    return String(s == null ? '' : s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
  }
  function n2(v) { return String(Math.round(v * 100) / 100).replace('.', ','); }
  function attrs(o) {
    var out = '';
    for (var k in o) if (o[k] != null) out += ' data-' + k + '="' + esc(o[k]) + '"';
    return out;
  }

  var SVG = {
    eye: '<path d="M2.5 12S6 5.5 12 5.5 21.5 12 21.5 12 18 18.5 12 18.5 2.5 12 2.5 12z"/><circle cx="12" cy="12" r="3"/>',
    ruler: '<rect x="3" y="8" width="18" height="8" rx="1.5"/><path d="M7 8v3M11 8v4M15 8v3M19 8v2"/>',
    dash: '<path d="M6 12h12"/>',
    'sup-all': '<path d="M4 20h16"/><path d="M6 9h12v4H6z"/><path d="M8 13v7M12 13v7M16 13v7"/><path d="M9 5h6v4"/>',
    'sup-plate': '<path d="M4 20h16"/><path d="M6 9h12v4H6z"/><path d="M8 13v7M16 13v7"/>',
    'sup-none': '<path d="M4 20h16"/><path d="M6 9h12v4H6z"/><path d="M12 13v3" stroke-dasharray="1 2"/>'
  };
  function icon(name) {
    return '<svg class="wd-ic" viewBox="0 0 24 24" aria-hidden="true">' + (SVG[name] || SVG.dash) + '</svg>';
  }

  // ------------------------------------------------------------ треугольник приоритетов
  // Вершины: [быстро, дёшево, качественно] — как rules.VARIANT_ORDER. «Качественно» — сверху.
  var TRI = { A: [24, 196], B: [236, 196], C: [130, 40] };
  var LAYER = [0.2, 0.15, 0.1], KINF = [0.8, 0.66, 1.0], NAMES = ['быстро', 'дёшево', 'качественно'];

  function norm(w) {
    var t = (w[0] || 0) + (w[1] || 0) + (w[2] || 0) || 1;
    return [Math.max(0, w[0] / t), Math.max(0, w[1] / t), Math.max(0, w[2] / t)];
  }
  function point(w) {
    w = norm(w);
    return [w[0] * TRI.A[0] + w[1] * TRI.B[0] + w[2] * TRI.C[0], w[0] * TRI.A[1] + w[1] * TRI.B[1] + w[2] * TRI.C[1]];
  }
  function bary(x, y) {
    var a = TRI.A, b = TRI.B, c = TRI.C;
    var d = (b[1] - c[1]) * (a[0] - c[0]) + (c[0] - b[0]) * (a[1] - c[1]);
    var l1 = ((b[1] - c[1]) * (x - c[0]) + (c[0] - b[0]) * (y - c[1])) / d;
    var l2 = ((c[1] - a[1]) * (x - c[0]) + (a[0] - c[0]) * (y - c[1])) / d;
    var w = [l1, l2, 1 - l1 - l2];
    // за краем — к ближайшей точке треугольника: отрицательные веса в ноль
    w = w.map(function (v) { return Math.max(0, v); });
    w = norm(w);
    // у вершины — «прилипаем», чтобы легко выбрать чистый вариант
    for (var i = 0; i < 3; i++) if (w[i] > 0.92) { w = [0, 0, 0]; w[i] = 1; }
    return w;
  }
  function triText(w) {
    w = norm(w);
    var layer = w[0] * LAYER[0] + w[1] * LAYER[1] + w[2] * LAYER[2];
    var k = w[0] * KINF[0] + w[1] * KINF[1] + w[2] * KINF[2];
    var order = [0, 1, 2].sort(function (a, b) { return w[b] - w[a]; });
    var title = w[order[0]] > 0.95 ? NAMES[order[0]].charAt(0).toUpperCase() + NAMES[order[0]].slice(1) :
      order.filter(function (i) { return w[i] >= 0.05; }).map(function (i) { return Math.round(w[i] * 100) + ' % ' + NAMES[i]; }).join(', ');
    return { title: title, sub: 'слой ' + n2(layer) + ' мм · заполнение × ' + n2(k) };
  }
  function wStr(w) { w = norm(w); return 'w:' + w.map(function (v) { return Math.round(v * 1000) / 1000; }).join(','); }

  // Контур треугольника со скруглёнными углами: у каждой вершины отступаем на r по обеим сторонам
  // и соединяем точки квадратичной кривой через вершину.
  function roundTri(r) {
    var v = [TRI.A, TRI.B, TRI.C], d = '';
    function toward(p, q, k) {
      var dx = q[0] - p[0], dy = q[1] - p[1], len = Math.sqrt(dx * dx + dy * dy);
      return [(p[0] + dx / len * k).toFixed(1), (p[1] + dy / len * k).toFixed(1)];
    }
    for (var i = 0; i < 3; i++) {
      var p = v[i], prev = v[(i + 2) % 3], next = v[(i + 1) % 3];
      var s1 = toward(p, prev, r), s2 = toward(p, next, r);
      d += (i ? 'L' : 'M') + s1[0] + ' ' + s1[1] + 'Q' + p[0] + ' ' + p[1] + ' ' + s2[0] + ' ' + s2[1];
    }
    return d + 'Z';
  }

  function triangle(o) {
    var w = norm(o.w || [0, 0, 1]);
    var p = point(w), t = triText(w);
    var a = TRI.A, b = TRI.B, c = TRI.C;
    var grid = '';
    for (var i = 1; i < 4; i++) {  // сетка через четверть — видно, где «поровну»
      var f = i / 4;
      grid += '<path d="M' + (a[0] + (b[0] - a[0]) * f) + ' ' + (a[1] + (b[1] - a[1]) * f) + 'L' + (a[0] + (c[0] - a[0]) * f) + ' ' + (a[1] + (c[1] - a[1]) * f) + '"/>';
      grid += '<path d="M' + (b[0] + (a[0] - b[0]) * f) + ' ' + (b[1] + (a[1] - b[1]) * f) + 'L' + (b[0] + (c[0] - b[0]) * f) + ' ' + (b[1] + (c[1] - b[1]) * f) + '"/>';
      grid += '<path d="M' + (c[0] + (a[0] - c[0]) * f) + ' ' + (c[1] + (a[1] - c[1]) * f) + 'L' + (c[0] + (b[0] - c[0]) * f) + ' ' + (c[1] + (b[1] - c[1]) * f) + '"/>';
    }
    var rec = o.recW ? point(o.recW) : null;
    var vlab = function (v, i, dx, dy, anchor) {
      return '<text x="' + (v[0] + dx) + '" y="' + (v[1] + dy) + '" text-anchor="' + anchor + '" class="tri-lab' + (w[i] > 0.95 ? ' on' : '') + '">' + NAMES[i].charAt(0).toUpperCase() + NAMES[i].slice(1) + '</text>';
    };
    var recW = norm(o.recW || [0, 0, 1]);  // рекомендация правил; без неё — «качественно»
    return '<div class="wd-tri"' + attrs({ tri: '1', kind: o.kind, q: o.q, i: o.i, k: o.key, w: wStr(w), rec: wStr(recW) }) + '>' +
      '<svg viewBox="0 0 260 226" role="slider" tabindex="0" aria-label="Приоритет: быстро, дёшево, качественно" aria-valuetext="' + esc(t.title) + '">' +
      '<path class="tri-face" d="' + roundTri(14) + '"/>' +
      '<g class="tri-grid">' + grid + '</g>' +
      '<path class="tri-edge" d="' + roundTri(14) + '"/>' +
      vlab(c, 2, 0, -22, 'middle') + vlab(a, 0, -4, 22, 'start') + vlab(b, 1, 4, 22, 'end') +
      (rec ? '<circle class="tri-rec" cx="' + rec[0] + '" cy="' + rec[1] + '" r="5"/>' : '') +
      '<g class="tri-knob" transform="translate(' + p[0] + ' ' + p[1] + ')"><circle class="halo" r="16"/><circle r="8"/></g>' +
      '</svg>' +
      '<div class="tri-read" aria-live="polite"><b>' + esc(t.title) + '</b><span>' + esc(t.sub) + '</span></div>' +
      '<button class="tri-reset" type="button" data-act="w-tri-reset" title="Вернуть точку к рекомендованной">' +
      '<svg class="ico sm" viewBox="0 0 24 24"><use href="#i-undo"/></svg>К рекомендованному</button></div>';
  }

  // ------------------------------------------------------------ ползунок заполнения
  function infillPreview(n) {
    // Срез заполнения: мягкий квадрат, линии сетки чаще с ростом плотности; 100 % — сплошной
    var bg = '<rect x="0" y="0" width="40" height="40" rx="7" class="fill-bg"/>';
    if (n >= 99) return bg + '<rect x="4" y="4" width="32" height="32" rx="4" class="fill-solid"/>';
    if (n <= 0) return bg;
    var gap = Math.max(2.2, 2.4 * 100 / Math.max(n, 1) / 2.2);
    var out = '';
    for (var x = 4 + gap / 2; x < 36; x += gap) out += '<path d="M' + x.toFixed(1) + ' 4V36"/><path d="M4 ' + x.toFixed(1) + 'H36"/>';
    return bg + '<g class="fill-lines">' + out + '</g>';
  }
  function recLabel(n, rec) {
    if (rec == null) return '';
    return n === rec ? '<span class="sl-rec on">рекомендовано</span>' :
      '<button type="button" class="sl-rec" data-act="w-slide-to"' + attrs({ v: rec }) + '>рекомендую ' + rec + ' %</button>';
  }
  function slider(o) {
    var n = Math.max(o.min || 0, Math.min(o.max || 100, o.n || 0));
    var pct = (n - (o.min || 0)) / ((o.max || 100) - (o.min || 0)) * 100;
    var lastTick = -100;  // подписи ближе 8 % друг к другу налезают — вторую не показываем
    var ticks = (o.stops || []).filter(function (s) { if (s - lastTick < 8) return false; lastTick = s; return true; }).map(function (s) {
      return '<button type="button" class="sl-tick" style="left:' + s + '%" data-act="w-slide-to"' + attrs({ k: o.key, v: s }) + '>' + s + '</button>';
    }).join('');
    var rec = o.rec == null ? null : Number(o.rec);
    var recMark = rec == null ? '' : '<i class="sl-recmark" style="left:' + ((rec - (o.min || 0)) / ((o.max || 100) - (o.min || 0)) * 100) + '%" title="рекомендую ' + rec + ' %"></i>';
    return '<div class="wd-slider"' + attrs({ slider: '1', k: o.key, rec: rec == null ? null : rec }) + ' style="--p:' + pct + '%">' +
      '<svg class="sl-prev" viewBox="0 0 40 40" aria-hidden="true">' + infillPreview(n) + '</svg>' +
      '<div class="sl-main"><div class="sl-top"><b class="sl-val num">' + n + ' %</b><span class="sl-recw">' + recLabel(n, rec) + '</span>' +
      (o.note ? '<span class="muted">' + esc(o.note) + '</span>' : '') + '</div>' +
      '<input type="range" id="sl-' + esc(o.key) + '" min="' + (o.min || 0) + '" max="' + (o.max || 100) + '" step="' + (o.step || 5) + '" value="' + n + '" aria-label="Плотность заполнения, %">' +
      '<div class="sl-ticks">' + recMark + ticks + '</div></div></div>';
  }

  // ------------------------------------------------------------ счётчик и стол
  function plate(n, per) {
    var on = per ? Math.min(n, per) : n;
    var slots = per ? Math.min(per, 12) : Math.min(n, 12);
    var out = '<circle class="plate" cx="30" cy="30" r="27"/>';
    var cols = Math.ceil(Math.sqrt(slots)), rows = Math.ceil(slots / cols), sz = 34 / Math.max(cols, rows);
    for (var i = 0; i < slots; i++) {
      var cx = 30 - (cols - 1) * sz / 2 + (i % cols) * sz, cy = 30 - (rows - 1) * sz / 2 + Math.floor(i / cols) * sz;
      out += '<rect class="slot' + (i < on ? ' on' : '') + '" style="--d:' + (i * 30) + 'ms" x="' + (cx - sz * 0.35).toFixed(1) + '" y="' + (cy - sz * 0.35).toFixed(1) + '" width="' + (sz * 0.7).toFixed(1) + '" height="' + (sz * 0.7).toFixed(1) + '" rx="1.5"/>';
    }
    return out;
  }
  function stepperText(n, per) {
    if (!per) return '';
    if (n <= per) return 'все на одном столе';
    var runs = Math.ceil(n / per);
    return 'на стол ≈ ' + per + ' шт. · ' + runs + ' ' + (runs % 10 === 1 && runs % 100 !== 11 ? 'запуск' : (runs % 10 >= 2 && runs % 10 <= 4 && (runs % 100 < 10 || runs % 100 >= 20)) ? 'запуска' : 'запусков');
  }
  function stepper(o) {
    var n = o.n || 1;
    return '<div class="wd-step"' + attrs({ step: '1', kind: o.kind, q: o.q, i: o.i, k: o.key, n: n, per: o.perPlate || 0, min: o.min || 1, max: o.max || 200 }) + '>' +
      '<div class="st-ctl"><button type="button" class="st-b" data-act="w-step" data-d="-1" aria-label="Меньше">−</button>' +
      '<input class="st-n num" id="st-' + esc(o.q || o.key) + '-' + esc(o.i || '') + '" inputmode="numeric" value="' + n + '" aria-label="Количество, шт.">' +
      '<button type="button" class="st-b" data-act="w-step" data-d="1" aria-label="Больше">+</button></div>' +
      '<svg class="st-plate" viewBox="0 0 60 60" aria-hidden="true">' + plate(n, o.perPlate) + '</svg>' +
      '<span class="st-txt muted">' + esc(stepperText(n, o.perPlate)) + '</span></div>';
  }

  // ------------------------------------------------------------ 3D или 5D
  function modeArt(m) {
    if (m === '3D') {
      var l = '';
      for (var i = 0; i < 6; i++) l += '<rect class="lay" style="--d:' + (i * 70) + 'ms" x="10" y="' + (42 - i * 6) + '" width="44" height="4" rx="1"/>';
      return '<svg viewBox="0 0 64 52" aria-hidden="true"><path class="bed" d="M4 48h56"/>' + l + '</svg>';
    }
    var r = '';
    for (var j = 0; j < 4; j++) r += '<circle class="ring" style="--d:' + (j * 90) + 'ms" cx="32" cy="26" r="' + (8 + j * 5) + '"/>';
    return '<svg viewBox="0 0 64 52" aria-hidden="true">' + r + '<circle class="axis" cx="32" cy="26" r="3"/><path class="spin-arc" d="M32 3a23 23 0 0 1 23 23"/></svg>';
  }
  function mode(o) {
    return '<div class="wd-mode" role="radiogroup" aria-label="Режим печати">' + ['3D', '5D'].map(function (m) {
      var on = o.cur === m;
      var note = (o.notes || {})[m] || '';
      var a = o.kind === 'plan' ? { act: 'plan-set', k: 'mode', v: m } : { act: 'q-toggle', q: o.q, i: o.i, v: m };
      return '<button type="button" role="radio" aria-checked="' + on + '" class="md' + (on ? ' sel' : '') + '"' + attrs(a) + '>' + modeArt(m) +
        '<span class="md-t"><b>' + m + '</b>' + (o.rec === m ? '<span class="rec">рекомендую</span>' : '') + '</span>' +
        (note ? '<span class="md-n">' + esc(note) + '</span>' : '') + '</button>';
    }).join('') + '</div>';
  }

  // ------------------------------------------------------------ уровни, сегменты, плитки
  function levels(o) {
    return '<div class="wd-lvl" role="radiogroup">' + o.options.map(function (opt, i) {
      var on = o.sel === opt;
      var bars = '';
      for (var b = 0; b < 3; b++) bars += '<rect class="bar' + (b <= i ? ' on' : '') + '" style="--d:' + (b * 60) + 'ms" x="' + (2 + b * 9) + '" y="' + (18 - b * 6) + '" width="6" height="' + (6 + b * 6) + '" rx="1"/>';
      return '<button type="button" role="radio" aria-checked="' + on + '" class="lv' + (on ? ' sel' : '') + '"' + attrs({ act: 'q-toggle', q: o.q, i: o.i, v: opt }) + '>' +
        '<svg viewBox="0 0 30 26" aria-hidden="true">' + bars + '</svg><b>' + esc(opt) + '</b>' +
        (o.rec === opt ? '<span class="rec">рекомендую</span>' : '') +
        ((o.notes || {})[opt] ? '<span class="lv-n">' + esc(o.notes[opt]) + '</span>' : '') + '</button>';
    }).join('') + '</div>';
  }
  function segmented(o) {
    var note = o.sel && (o.notes || {})[o.sel] ? o.notes[o.sel] : (o.rec && (o.notes || {})[o.rec] ? o.notes[o.rec] : '');
    return '<div class="wd-seg" role="radiogroup">' + o.options.map(function (opt) {
      var on = o.sel === opt;
      return '<button type="button" role="radio" aria-checked="' + on + '" class="sg' + (on ? ' sel' : '') + '"' + attrs({ act: 'q-toggle', q: o.q, i: o.i, v: opt }) + '>' +
        icon((o.icons || {})[opt]) + '<span>' + esc(opt) + '</span>' + (o.rec === opt ? '<i class="rec-dot" title="рекомендую"></i>' : '') + '</button>';
    }).join('') + '</div>' + (note ? '<span class="wd-segnote">' + esc(note) + '</span>' : '');
  }
  function sil(s) {
    if (!s) return '<svg viewBox="0 0 40 40" aria-hidden="true"><path class="bed" d="M2 38h36"/></svg>';
    var w = s.w + 4, h = s.h + 8;
    return '<svg viewBox="-2 -2 ' + w + ' ' + h + '" preserveAspectRatio="xMidYMax meet" aria-hidden="true"><path class="body" d="' + esc(s.d) + '"/>' +
      '<path class="bed" d="M-2 ' + (s.h + 2) + 'H' + (s.w + 2) + '"/></svg>';
  }
  function tiles(o) {
    return '<div class="wd-tiles" role="radiogroup">' + o.options.map(function (opt) {
      var on = o.sel === opt;
      return '<button type="button" role="radio" aria-checked="' + on + '" class="tl' + (on ? ' sel' : '') + '"' + attrs({ act: 'q-toggle', q: o.q, i: o.i, v: opt }) + '>' +
        '<span class="tl-pic">' + sil((o.sils || {})[opt]) + '</span><b>' + esc(opt) + '</b>' +
        (o.rec === opt ? '<span class="rec">рекомендую</span>' : '') +
        ((o.notes || {})[opt] ? '<span class="tl-n">' + esc(o.notes[opt]) + '</span>' : '') + '</button>';
    }).join('') + '</div>';
  }

  // ------------------------------------------------------------ взаимодействие
  var drag = null;

  function triFrom(el, e) {
    var svg = el.querySelector('svg');
    var r = svg.getBoundingClientRect();
    var vb = svg.viewBox.baseVal;
    return bary((e.clientX - r.left) / r.width * vb.width, (e.clientY - r.top) / r.height * vb.height);
  }
  function triSet(el, w) {
    var p = point(w), t = triText(w);
    el.querySelector('.tri-knob').setAttribute('transform', 'translate(' + p[0].toFixed(1) + ' ' + p[1].toFixed(1) + ')');
    el.querySelector('.tri-read b').textContent = t.title;
    el.querySelector('.tri-read span').textContent = t.sub;
    el.querySelector('svg').setAttribute('aria-valuetext', t.title);
    var labs = el.querySelectorAll('.tri-lab');
    for (var i = 0; i < labs.length; i++) labs[i].classList.toggle('on', w[i] > 0.95);
    el.setAttribute('data-w', wStr(w));
  }

  document.addEventListener('pointerdown', function (e) {
    var el = e.target.closest && e.target.closest('[data-tri]');
    if (!el || e.button > 0) return;
    e.preventDefault();
    drag = el;
    el.classList.add('drag');
    triSet(el, triFrom(el, e));
  });
  document.addEventListener('pointermove', function (e) {
    if (drag) triSet(drag, triFrom(drag, e));
  });
  function endDrag() {
    if (!drag) return;
    var el = drag;
    drag = null;
    el.classList.remove('drag');
    if (Wd.commit) Wd.commit('triangle', el, el.getAttribute('data-w'));
  }
  document.addEventListener('pointerup', endDrag);
  document.addEventListener('pointercancel', endDrag);
  document.addEventListener('keydown', function (e) {  // стрелки двигают точку на 5 %
    var el = e.target.closest && e.target.closest('[data-tri]');
    if (!el) return;
    var dirs = { ArrowUp: [0, -8], ArrowDown: [0, 8], ArrowLeft: [-8, 0], ArrowRight: [8, 0] };
    if (!dirs[e.key]) return;
    e.preventDefault();
    var w = (el.getAttribute('data-w') || 'w:0,0,1').slice(2).split(',').map(Number);
    var p = point(w);
    var nw = bary(p[0] + dirs[e.key][0], p[1] + dirs[e.key][1]);
    triSet(el, nw);
    clearTimeout(el._t);
    el._t = setTimeout(function () { if (Wd.commit) Wd.commit('triangle', el, el.getAttribute('data-w')); }, 500);
  });

  function sliderSet(el, n) {
    var inp = el.querySelector('input');
    var pct = (n - Number(inp.min)) / (Number(inp.max) - Number(inp.min)) * 100;
    el.style.setProperty('--p', pct + '%');
    el.querySelector('.sl-val').textContent = n + ' %';
    el.querySelector('.sl-prev').innerHTML = infillPreview(n);
    var rw = el.querySelector('.sl-recw'), rec = el.getAttribute('data-rec');
    if (rw && rec !== null && rec !== '') rw.innerHTML = recLabel(n, Number(rec));
  }
  document.addEventListener('input', function (e) {
    var el = e.target.closest && e.target.closest('[data-slider]');
    if (el && e.target.type === 'range') sliderSet(el, Number(e.target.value));
  });
  document.addEventListener('change', function (e) {
    var el = e.target.closest && e.target.closest('[data-slider]');
    if (el && e.target.type === 'range' && Wd.commit) Wd.commit('slider', el, Number(e.target.value));
    var st = e.target.closest && e.target.closest('[data-step]');
    if (st && e.target.classList.contains('st-n')) stepTo(st, parseInt(e.target.value, 10) || 1, true);
  });

  function stepTo(el, n, now) {
    var min = Number(el.getAttribute('data-min')) || 1, max = Number(el.getAttribute('data-max')) || 200;
    n = Math.max(min, Math.min(max, n));
    var per = Number(el.getAttribute('data-per')) || 0;
    el.setAttribute('data-n', n);
    el.querySelector('.st-n').value = n;
    el.querySelector('.st-plate').innerHTML = plate(n, per);
    el.querySelector('.st-txt').textContent = stepperText(n, per);
    el.classList.remove('tick'); void el.offsetWidth; el.classList.add('tick');
    clearTimeout(el._t);
    el._t = setTimeout(function () { if (Wd.commit) Wd.commit('stepper', el, n); }, now ? 0 : 450);
  }
  document.addEventListener('click', function (e) {
    var b = e.target.closest && e.target.closest('[data-act="w-step"]');
    if (b) { var el = b.closest('[data-step]'); stepTo(el, Number(el.getAttribute('data-n')) + Number(b.getAttribute('data-d'))); return; }
    var rb = e.target.closest && e.target.closest('[data-act="w-tri-reset"]');
    if (rb) {
      var tri = rb.closest('[data-tri]'), rw = (tri.getAttribute('data-rec') || 'w:0,0,1').slice(2).split(',').map(Number);
      triSet(tri, norm(rw));
      if (Wd.commit) Wd.commit('triangle', tri, tri.getAttribute('data-w'));
      return;
    }
    var t = e.target.closest && e.target.closest('[data-act="w-slide-to"]');
    if (t) {
      var sl = t.closest('[data-slider]'), v = Number(t.getAttribute('data-v'));
      sl.querySelector('input').value = v; sliderSet(sl, v);
      if (Wd.commit) Wd.commit('slider', sl, v);
    }
  });

  var Wd = window.Wd = { triangle: triangle, slider: slider, stepper: stepper, mode: mode, levels: levels,
    segmented: segmented, tiles: tiles, triText: triText, wStr: wStr, commit: null };
})();
