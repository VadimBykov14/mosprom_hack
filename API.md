# API чата STE Assistant

Сервер плагина: `http://localhost:<порт>`, слушает только `127.0.0.1`. Все запросы `/api/*` — с заголовком `X-Session-Token: <токен из адреса ?t=>`. Ответы — JSON; ошибка — `{"error": "что случилось и что сделать"}`.

## События

`GET /api/events?since=N` — длинный опрос (до 20 с). Возвращает список событий с номером `seq`:

| type | Когда | Что делает чат |
|---|---|---|
| `part` (`id`) | изменилась деталь: ход работы, карточки, паспорт | перечитывает деталь |
| `parts` | создана или удалена деталь | перечитывает список |
| `running` (`id`, `on`) | ассистент начал или закончил работу | обновляет статус |
| `conn` | изменилось подключение к LLM или настройки | перечитывает `/api/state` |
| `open` (`what: "save"`, `id`) | ассистент просит открыть окно сохранения | открывает окно |

## Детали

| Запрос | Что делает |
|---|---|
| `GET /api/state` | подключение, список деталей, настройки, `seq` |
| `POST /api/parts` | новая деталь |
| `GET/PATCH/DELETE /api/parts/{id}` | деталь (переписка, паспорт), переименовать, удалить |
| `POST /api/upload?name=файл` | тело — байты файла; ответ `{id, name, size, kind}` |
| `POST /api/parts/{id}/messages` | `{text, files: [id], mode}` — сообщение ассистенту; `mode: "review"` — разбор напечатанной детали по фото (без `mode` разбор включается сам: фото к нарезанной детали со словами «напечатал», «дефект» или без текста) |
| `POST /api/parts/{id}/stop` | остановить после текущего шага |
| `POST /api/parts/{id}/answer` | `{question, value}` — ответ на вопрос; для карточки вопросов `value` — `{id вопроса: ответ}`: первый раунд `env, strength, surface, qty, priority`, второй `material, mode, orientation, supports`; `priority` — вершина («Быстро») или точка треугольника `w:быстро,дёшево,качественно` (например `w:0.5,0.2,0.3`), `qty` — число (пустой объект — «Пропустить» / «Оставить рекомендованное») |
| `POST /api/parts/{id}/plan` | `{key, value}` — изменить строку плана: `material`, `mode`, `orientation`, `variant` (в том числе `w:…` — точка треугольника), `quantity` или ключ настройки STE Slicer со значением из `options` строки (закреплённые ТЗ не меняются) |
| `POST /api/parts/{id}/confirm` | «Подтвердить и нарезать» |
| `POST /api/parts/{id}/assumption` | `{key, value}` — выбрать значение допущения |
| `POST /api/parts/{id}/variant` · `/variants` | применить вариант (в плане — строка плана) · посчитать три варианта (только после подтверждения) |
| `POST /api/parts/{id}/slice` · `/mode` · `/orientation` | нарезать (в плане — то же, что подтвердить) · `{mode: "3D"|"5D"}` · `{index}` |
| `POST /api/parts/{id}/undo` | `{block}` — отменить «было → стало» |
| `POST /api/parts/{id}/fix` | `{warning, index, label}` — кнопка в предупреждении о сочетании: `warning` — `id` предупреждения, `index` — номер исправления; сочетания проверяются заново, нет предупреждения — ошибка в чате |
| `POST /api/parts/{id}/setting` | `{key, value}` или `{values: {ключ: значение}}` — правка параметра STE Slicer из паспорта или «Все настройки»: тип по определению слайсера, проверка `validationState`, перенарезка, «было → стало»; до подтверждения плана — правка плана |
| `POST /api/parts/{id}/reset` | `{key}` — вернуть один параметр · `{}` — «Вернуть исходные»: убрать все правки пользователя и снова применить план |
| `GET /api/slicer-settings?q=` | все параметры STE Slicer по запросу (ключ, подпись ru_RU, раздел): `{items [{key, category, label, description, unit, type, min, max, options [[значение, подпись]], enabled, value, text, changed}], total}`; пустой запрос — изменённые вручную |
| `POST /api/parts/{id}/review` · `/review-apply` | `{block, defects: [id]}` — выбрать дефекты в карточке разбора · `{block}` — применить исправления и перенарезать |
| `POST /api/parts/{id}/requirement` | `{tag, text, kind}` · `{tag, delete: true}` · `{add: true, text}` |
| `GET /api/save-name?part=id` · `POST /api/parts/{id}/save` | имя по шаблону · `{project, gcode, folder, name}` |

## Картотека, настройки, MCP

| Запрос | Что делает |
|---|---|
| `GET /api/catalog` · `PATCH /api/catalog/{id}` | записи (у записей с разбором по фото — `lessons [{key, label, from, to, defect}]`) · `{state, note, fav}` |
| `POST /api/catalog/{id}/open` · `/template` | открыть деталь · взять настройки как шаблон |
| `POST /api/catalog/import` | `{folder}` — импорт папки, отчёт `{added, fromName, skipped}` |
| `GET/PUT /api/settings` | настройки; ключ передаётся только на запись (`key`), обратно — `keySaved` |
| `POST /api/settings/check` | проверка LLM: ответ, инструменты, изображения |
| `GET/PUT /api/mcp` · `POST /api/mcp/token` | адрес, токен, инструменты, журнал · вкл/выкл внешних клиентов · новый токен |
| `POST /mcp` | MCP JSON-RPC 2.0 (`Authorization: Bearer <токен MCP>`) |

## Блоки сообщения ассистента

`text`, `trace` (ход работы: шаги `tool, title, sum, status, args, result, changes`), `model`, `reqs`, `questions` (`title` «Условия работы» или «Как сделать», `skip` — подпись кнопки пропуска, `items [{id, q, options, multi, exclusive, def, why, notes {вариант: пояснение}, rec, widget}]`, `widget` — `triangle` (`recW`), `stepper` (`min, max, perPlate`), `mode` (`o3d, o5d`), `levels`, `segmented` (`icons`), `tiles` (`sils` — силуэт в каждом положении), `answers`; id: `env, strength, surface, qty, priority` в первом раунде, `material, mode, orientation, supports` во втором), `plan` (`head, rows [{key, k, v, why, src, options, changed, widget}]` — `slider` у заполнения (`n, min, max, step, stops`), `stepper` у количества (`n, perPlate`), `mode` у режима (`cur`), `triangle` у варианта (`w`);, warnings — строки или предупреждения о сочетаниях с `id` и `fixes`, status: wait|confirmed, material, modes`; `src` ещё и `profile` — профиль материала, `experience` — опыт прошлых печатей), `material` (в том числе `plain [[материал, описание простыми словами]]`), `modes`, `orient`, `ask`, `slice`, `variants`, `diff`, `notice` (после правки, создавшей сочетание, — `combos`: те же предупреждения с `fixes`), `saved`, `review` (разбор по фото: `photos, seen_by, catalog [{id, title, signs}], defects [{id, confidence, where}], question, notes, selected, fixes [{key, label, from, to, fromText, toText, why, defect}], state: pick|fixes|nofix|applied`). Статус детали: `draft`, `ask` (ждёт ответов), `plan` (ждёт подтверждения), `work`, `sliced`, `saved`, `failed`. Паспорт — поле `passport` детали: `mode, modeShort, material, printer, quality, variant, rows [{k, v, src, why, key, lock, changed, edit}], params [{key, k, v, src, why, changed, edit}] — скорость, температуры, обдув, откат, overridden [ключи правок пользователя], warnings [{id, kind: bad|warn, text, fixes [{label, set {ключ: значение} | mode | quantity}]}] — недопустимые и рискованные сочетания с готовыми исправлениями, assumptions, totals, variants, prep, rejected, locks`; `edit` — `{type: float|int|bool|enum|mode, unit, min, max, options, value}`.
