/* Связь чата с плагином: fetch для запросов, длинный опрос /api/events для потока событий
   (ответ ассистента, ход работы, прогресс нарезки). Токен сессии — из адреса ?t=. */
(function () {
  'use strict';
  var params = new URLSearchParams(location.search);
  var token = params.get('t');
  try {
    if (token) sessionStorage.setItem('ste-token', token);
    else token = sessionStorage.getItem('ste-token');
  } catch (e) { /* хранилище недоступно — работаем с тем, что в адресе */ }

  var listeners = [];
  var seq = 0;
  var alive = false;

  function emit(ev) { listeners.forEach(function (fn) { try { fn(ev); } catch (e) { console.error(e); } }); }

  function req(method, path, body, raw) {
    var opt = { method: method, headers: { 'X-Session-Token': token || '' } };
    if (raw) { opt.body = raw; opt.headers['Content-Type'] = 'application/octet-stream'; }
    else if (body !== undefined) { opt.body = JSON.stringify(body); opt.headers['Content-Type'] = 'application/json'; }
    return fetch(path, opt).then(function (r) {
      return r.text().then(function (t) {
        var data = null;
        try { data = t ? JSON.parse(t) : null; } catch (e) { data = null; }
        if (!r.ok) {
          var err = new Error((data && data.error) || ('Плагин ответил ' + r.status));
          err.status = r.status;
          throw err;
        }
        return data;
      });
    });
  }

  function poll() {
    req('GET', '/api/events?since=' + seq).then(function (evs) {
      if (!alive) { alive = true; emit({ type: 'conn' }); }
      (evs || []).forEach(function (e) {
        if (e.type === 'reset') { seq = e.seq || 0; emit({ type: 'conn' }); return; }
        seq = Math.max(seq, e.seq || 0); emit(e);
      });
      setTimeout(poll, 30);
    }, function (e) {
      // 403 — плагин перезапущен с новым ключом сессии: старая вкладка больше не подойдёт.
      if (e.status === 403) { emit({ type: 'auth', error: e.message }); setTimeout(poll, 10000); return; }
      if (alive) { alive = false; emit({ type: 'offline', error: e.message }); }
      setTimeout(poll, 2000);
    });
  }

  function partUrl(id, action) { return '/api/parts/' + encodeURIComponent(id) + (action ? '/' + action : ''); }

  window.Api = {
    hasToken: function () { return !!token; },
    on: function (fn) { listeners.push(fn); },
    init: function () {
      return req('GET', '/api/state').then(function (s) { seq = s.seq || 0; alive = true; poll(); return s; });
    },
    state: function () { return req('GET', '/api/state'); },
    getPart: function (id) { return req('GET', partUrl(id)); },
    newPart: function (folder) { return req('POST', '/api/parts', { folder: folder || '' }); },
    movePart: function (id, folder) { return req('PATCH', partUrl(id), { folder: folder || '' }); },
    addFolder: function (name) { return req('POST', '/api/folders', { name: name }); },
    renameFolder: function (id, name) { return req('PATCH', '/api/folders/' + encodeURIComponent(id), { name: name }); },
    deleteFolder: function (id) { return req('DELETE', '/api/folders/' + encodeURIComponent(id)); },
    renamePart: function (id, name) { return req('PATCH', partUrl(id), { name: name }); },
    deletePart: function (id) { return req('DELETE', partUrl(id)); },
    upload: function (file) {
      return req('POST', '/api/upload?name=' + encodeURIComponent(file.name), undefined, file);
    },
    send: function (id, text, fileIds, comment, mode) { return req('POST', partUrl(id, 'messages'), { text: text, files: fileIds, comment: comment || '', mode: mode || '' }); },
    stop: function (id) { return req('POST', partUrl(id, 'stop'), {}); },
    answer: function (id, qid, value) { return req('POST', partUrl(id, 'answer'), { question: qid, value: value }); },
    planEdit: function (id, key, value) { return req('POST', partUrl(id, 'plan'), { key: key, value: value }); },
    confirm: function (id) { return req('POST', partUrl(id, 'confirm'), {}); },
    assumption: function (id, key, value) { return req('POST', partUrl(id, 'assumption'), { key: key, value: value }); },
    variant: function (id, v) { return req('POST', partUrl(id, 'variant'), { variant: v }); },
    variants: function (id) { return req('POST', partUrl(id, 'variants'), {}); },
    slice: function (id) { return req('POST', partUrl(id, 'slice'), {}); },
    mode: function (id, m) { return req('POST', partUrl(id, 'mode'), { mode: m }); },
    fix: function (id, warning, index, label) { return req('POST', partUrl(id, 'fix'), { warning: warning, index: index, label: label }); },
    setting: function (id, key, value) { return req('POST', partUrl(id, 'setting'), { key: key, value: value }); },
    reset: function (id, key) { return req('POST', partUrl(id, 'reset'), key ? { key: key } : {}); },
    slicerSettings: function (q) { return req('GET', '/api/slicer-settings?q=' + encodeURIComponent(q || '')); },
    review: function (id, block, defects) { return req('POST', partUrl(id, 'review'), { block: block, defects: defects }); },
    reviewApply: function (id, block) { return req('POST', partUrl(id, 'review-apply'), { block: block }); },
    undo: function (id, block) { return req('POST', partUrl(id, 'undo'), { block: block }); },
    orientation: function (id, index) { return req('POST', partUrl(id, 'orientation'), { index: index }); },
    requirement: function (id, data) { return req('POST', partUrl(id, 'requirement'), data); },
    save: function (id, opts) { return req('POST', partUrl(id, 'save'), opts); },
    chooseFolder: function (start) { return req('POST', '/api/choose-folder', { start: start || '' }); },
    saveName: function (id) { return req('GET', '/api/save-name?part=' + encodeURIComponent(id)); },
    catalog: function () { return req('GET', '/api/catalog'); },
    updateRecord: function (rid, patch) { return req('PATCH', '/api/catalog/' + encodeURIComponent(rid), patch); },
    asTemplate: function (rid) { return req('POST', '/api/catalog/' + encodeURIComponent(rid) + '/template', {}); },
    openRecord: function (rid) { return req('POST', '/api/catalog/' + encodeURIComponent(rid) + '/open', {}); },
    importFolder: function (folder) { return req('POST', '/api/catalog/import', { folder: folder }); },
    settings: function () { return req('GET', '/api/settings'); },
    saveSettings: function (s) { return req('PUT', '/api/settings', s); },
    checkLlm: function (s) { return req('POST', '/api/settings/check', s); },
    mcp: function () { return req('GET', '/api/mcp'); },
    setMcp: function (on) { return req('PUT', '/api/mcp', { external: on }); },
    newMcpToken: function () { return req('POST', '/api/mcp/token', {}); },
    openFolder: function (path) { return req('POST', '/api/open-folder', { path: path }); }
  };
})();
