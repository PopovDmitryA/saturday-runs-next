# make parkrun — Mac-демон очереди parkrun/s95 + parkrun-monitoring

Единая справка по устройству и запуску. Актуально на 21.07.2026.

Весь код исполняется **только на Mac** из рабочей папки (не из Docker-образа),
поэтому правки в `backend/app/services/parkrun_queue_daemon.py`,
`backend/app/parkrun/…`, `scripts/parkrun_mac.sh` подхватываются со следующего
запуска **без деплоя**. Деплой на прод нужен лишь тем частям, что живут в
celery-воркерах (`app/workers/…`, `enqueue_profile_fetch_pending`).

---

## Что делает один прогон

`make parkrun` → `scripts/parkrun_mac.sh` → `backend/scripts/parkrun_queue_daemon.py`
→ `run_daemon()`. Работает против **прод-БД** (SSH-туннель на 127.0.0.1:5434) и
локального Docker-Redis. Порядок:

1. **parkrun-monitoring sync** — обновляет каталог локаций + недельную
   статистику стран в локальной БД соседнего проекта
   (`~/Projects/parkrun-monitoring`, путь в `PARKRUN_MONITORING_DIR`). Это
   отдельный публичный репозиторий, см. память `project-parkrun-monitoring`.
2. **Очередь s95** — разбирается своей пачкой (httpx, без браузера: прод-IP в
   белом списке s95.ru).
3. **Очередь parkrun** — основной цикл через **Playwright Chromium** (браузер
   виден, при капче ждёт прохождения в окне).
4. По ходу цикла — **чередование с локациями** parkrun-monitoring (см. ниже).
5. В конце — добор бюджета локаций и **push** собранной монитор-статистики/истории
   на сервер (каноническая БД parkrun-monitoring живёт на проде).

Профили людей идут через браузер (страница `/parkrunner/{id}/` под AWS WAF),
**локации — обычным httpx** (`/results/eventhistory/` капчей не защищена).

---

## Вывод и лог

- **Терминал** — только справочные строки «в моменте» (прогресс очереди, кто
  обработан, паузы, итог), каждая с таймстампом `HH:MM:SS`. Трейсбэков и
  библиотечного шума тут нет.
- **Файл** `data/parkrun_daemon.log` (рядом со скриптом, gitignored) — всё
  подряд: те же строки + библиотечный INFO + **полные трейсбэки** с
  таймстампами. Сюда смотреть, когда что-то упало.
- Трейсбэк в терминале — **только** если скрипт упал целиком (тогда одна
  строка `СКРИПТ УПАЛ — подробности в …`). Отдельные ошибки очереди (s95 403,
  not-found) в терминал не сыплются — они в файле.

## Как запускать

Переменные окружения можно ставить **и до `make`, и после `parkrun`** — работает
одинаково (проверено). Примеры:

```bash
make parkrun                       # обычный прогон, бюджет 50
make parkrun LIMIT=300             # на ночь: 300 профилей + 300 локаций
make parkrun QUIET=1               # без DB/HTTP/page-load шума
PENDING_ONLY=1 make parkrun        # только очередь, без плановых синков
```

| Переменная | Что делает |
|---|---|
| `LIMIT=N` | бюджет прогона: N профилей из очереди сайта + N eventhistory-локаций. По умолчанию 50. |
| `QUIET=1` | срезает из файла-лога **только библиотечный** INFO-шум (httpx, DB flush, page-load). Справочные строки демона и трейсбэки в файле остаются — иначе трейсбэки были бы без контекста. Терминал и без того чист. |
| `PENDING_ONLY=1` | пропустить плановые синки привязанных профилей, разбирать только pending-очередь. |
| `PARKRUN_MONITORING_DIR` | путь к соседнему проекту (по умолчанию `~/Projects/parkrun-monitoring`). Если папки/venv нет — чередование с локациями тихо отключается, демон работает как раньше. |

### Экспериментальный режим без браузера

```bash
NO_BROWSER=1 LIMIT=40 FAST_DELAY=8 make parkrun
```

| Переменная | Что делает |
|---|---|
| `NO_BROWSER=1` | ЭКСПЕРИМЕНТ: профили тянутся обычным httpx, **без Chromium и без окна капчи**. При первом же признаке защиты WAF вся оставшаяся пачка сразу останавливается (окна для прохождения капчи нет). Реальный риск бана — держать `LIMIT` маленьким. |
| `FAST_DELAY=N` | только с `NO_BROWSER`: пауза между запросами, N секунд с джиттером ±30%. Действует человек↔человек, человек↔локация **и между двумя страницами одного профиля** (summary→/all — иначе там осталась бы дефолтная пауза 10с). По умолчанию 3. |

Причина, почему это эксперимент: S95 отказался от браузера только после того,
как его IP занесли в белый список; для parkrun вместо этого построена лестница
банов/капчи. Голый httpx с Mac проходит (проверено), но Mac «прогрет» месяцами
Playwright-трафика — на другом IP/объёме поведение WAF может отличаться. При
бане Дмитрий меняет страну VPN и становится «новым» пользователем.

---

## Две очереди (важно не путать)

Ручной клик и новая привязка попадают в **разные** механизмы:

1. **`profile_fetch_pending`** (таблица) — превью/привязка нового профиля +
   служебный backlog (discovery от s95-декаплинга, сид из архива RunPark).
   Порядок в `list_pending_rows` — **три уровня приоритета**:
   1. строки с `user_id` (реальный пользователь ждёт ответа);
   2. сид RunPark (метка `seed from RunPark…` в `last_error`) — там
      гарантированно есть история;
   3. discovery-backlog (метка `queued by s95 sync…`) — лишь гипотеза.
2. **`platform_links.sync_status='error'`** — ручной рефреш **уже
   привязанного** профиля (кнопка «Обновить» на сайте). Такой строки в
   `profile_fetch_pending` нет; `build_parkrun_work_queue` смотрит на
   последний `SyncJob` линка и ставит `trigger='manual'` **в самое начало
   очереди, впереди даже pending**.

### Чередование с локациями

Один прогон идёт каруселью: `человек → пауза → 1 локация → пауза → человек → …`
Пауза (человеческий темп) стоит по обе стороны от локации. Бюджет локаций = `LIMIT`.

---

## Капча и баны

- **Эскалирующее охлаждение** в Redis: 1ч → 5ч → 24ч → 72ч → неделя. Успешный
  фетч сбрасывает лестницу, новая капча поднимает на ступень
  (`parkrun:fetch:ban_cooldown_until`, `parkrun:fetch:ban_level`).
- **`aws-waf-token`** в `data/parkrun_playwright_state.json` протухает ~раз в
  6 дней. Перед прогоном обновить сессию:
  `bash scripts/parkrun_save_session_mac.sh` (или `make parkrun` предложит сам).
- В браузерном режиме капча → демон ждёт, пока её пройдут в окне Chromium.
  В `--no-browser` окна нет → пачка останавливается.

### Что демон делает с «безнадёжными» строками

- **Профиль 404** (реально не существует) → сразу `failed` с меткой
  `[permanent]`, больше не воскрешается на старте демона.
- **Стабильный бан/403 (s95/parkrun)** → после 5 попыток тоже `failed +
  [permanent]`, не крутится в очереди вечно.
- **Дедуп** ищет существующую строку по любому статусу (не только pending),
  так что для одного атлета не копятся дубли.
- **Обрыв SSH-туннеля к прод-БД** (порт 5434 рвётся посреди прогона: в логе
  SSL EOF, дальше `Connection refused`) → после **3 обрывов подряд** прогон
  прерывается со строкой «перезапусти make parkrun». Это не parkrun-ошибка;
  профили остаются `pending` и переобработаются. Локации при этом идут дальше
  (пишутся в локальную SQLite, туннель им не нужен), но остаток после стопа
  не добирается.

---

## Диагностика очереди (прод-БД)

Открыть туннель и посчитать backlog:

```bash
bash -c 'source scripts/prod_db_env.sh && export_prod_database_url && \
  PYTHONPATH="$(pwd)/backend" .conda-parkrun/bin/python -c "
from app.db.session import get_session_factory
from app.services.profile_fetch_pending_service import count_pending_rows
db = get_session_factory()()
print(\"parkrun pending:\", count_pending_rows(db, \"parkrun\"))
print(\"s95 pending:\", count_pending_rows(db, \"s95\"))
"'
```

В самом выводе демона размер очереди печатается честно:
`В очереди: N задач(и) всего (pending P, sync S) — берём в этот прогон K`.

---

## Файлы

| Файл | Роль |
|---|---|
| `scripts/parkrun_mac.sh` | обёртка: туннель, Redis, conda-env, прокидывает env-флаги |
| `backend/scripts/parkrun_queue_daemon.py` | CLI, argparse, вызывает `run_daemon` |
| `backend/app/services/parkrun_queue_daemon.py` | `run_daemon`, `build_parkrun_work_queue`, цикл, чередование |
| `backend/app/services/s95_queue_daemon.py` | разбор s95-очереди (httpx) |
| `backend/app/services/profile_fetch_pending_service.py` | приоритеты очереди, `process_pending_row`, лимиты попыток |
| `backend/app/parkrun/fetch/daemon_session.py` | сессия браузера/httpx, паузы, режим `--no-browser` |
| `backend/app/services/parkrun_monitoring_bridge.py` | мост к parkrun-monitoring (stats/history/push) |

Связанная память: `project-parkrun-monitoring`, `project-parkrun-summary-pseudo-location`,
`project-s95-parkrun-fetch-findings`.
