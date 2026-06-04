# План деплоя и миграции данных

Документ зафиксирован после аудита legacy-базы `five_verst_stats` (PostgreSQL 14, ~860 MB, хост `195.58.34.112`).  
Дата аудита: **май 2026**.

> **Безопасность:** пароль read-only пользователя не хранится в репозитории. После завершения миграции рекомендуется сменить пароль или отозвать доступ.

---

## 1. Краткое резюме

Новый личный кабинет («Статистика парковых пробежек») разворачивается **параллельно** legacy-стеку (Grafana на run5k.run + старый Telegram-бот). Legacy БД **не модифицируется** — только read-only ETL в новую БД `saturday_runs_lk`.

**Объём данных для переноса:**

| Область | Строк (ориентир) | Комментарий |
|---------|------------------|-------------|
| 5 вёрст: локации | 207 | slug из `link_point` |
| 5 вёрст: события | 29 297 (из них 332 тестовых) | `list_all_events` |
| 5 вёрст: пробежки | 1 187 621 | `details_protocol` |
| 5 вёрст: волонтёрства | 453 052 | `details_vol` |
| S95: локации | 34 | есть s95.by |
| S95: события | 3 738 | |
| S95: пробежки | 130 591 | |
| S95: волонтёрства | 33 197 | |
| parkrun: пробежки | 729 020 | 545k RU + 184k зарубеж |
| parkrun: профили | 54 445 | `parkrun_users` |
| parkrun: сводка волонтёрств | 49 581 | агрегат по ролям, без дат |
| Пользователи Telegram | 265 | `tg_user_profile` |
| Привязки 5v / S95 / parkrun | 157 / 48 / 49 | 56 пользователей с 2+ системами |

**Итого ~2,5 млн строк результатов** (пробежки + волонтёрства). ETL батчами — ориентир **30–90 минут** на том же сервере.

**Не переносим** (~800k строк мёртвых/дублирующих данных):

- `parkrun_old_details` (588 378) — нулевое пересечение с `parkrun_details_protocol` по user+date, **0 пользователей** из `tg_user_profile` с пробежками в этой таблице
- `archive_*` — снимки для change log
- `all_system_loc`, `contacts_location`, `change_log`, `club_*`, `january2025/2026`, `update_table`
- `s95_*_copy`, `s95_*_backup` — дубликаты

**RunPark** в legacy БД **нет** — наполняется скриптами из репозитория (`import_runpark_location_mappings.py`, каталог локаций).

---

## 2. Топология на сервере

```
run5k.run              → Grafana + legacy Telegram-бот (без изменений)
app.run5k.run          → Nginx → React (dist) + FastAPI
195.58.34.112:5432
  ├── five_verst_stats     (legacy, read-only для ETL)
  └── saturday_runs_lk     (новая, read-write)
Redis + Celery (worker, worker-s95, worker-parkrun, beat) + новый Telegram-бот
```

Legacy и новая БД могут жить **на одном PostgreSQL-инстансе** (разные database name) — это упрощает ETL (`INSERT … SELECT` через dblink или скрипт с двумя connection string).

---

## 3. Инвентаризация legacy (фактическая схема)

### 3.1. Пользователи — `tg_user_profile` (265 строк)

| Legacy поле | Новое поле | Примечание |
|-------------|------------|------------|
| `tg_user_id` | `users.telegram_id` | PK в legacy |
| `tg_username` | `users.telegram_username` | |
| `tg_chat_id` | `users.telegram_chat_id` | для рассылок |
| `consent_accepted`, `consent_ts` | `users.consent_*` | 175 с согласием |
| `news_subscribed` | `users.news_subscribed` | 73 подписчика |
| `user_id_5v` | `platform_links` (five_verst) | 157 привязок, UNIQUE |
| `s95_user_id` | `platform_links` (s95) | 48 |
| `parkrun_user_id` | `platform_links` (parkrun) | 49 |
| `profile_url`, `bound_at` | `platform_links.external_url`, `linked_at` | для 5v |
| `last_*_change_at` | опционально `last_user_sync_at` | |

**Профили без привязок:** 104 пользователя (заходили в бота, но не привязали систему) — переносим как `users` без `platform_links`.

**Мультиплатформенные:** 56 пользователей с 2+ привязками (37 — все три системы).

### 3.2. 5 вёрст

**Локации — `general_location` (207)**

- `name_point` — **русское отображаемое имя** (не slug)
- `link_point` — URL вида `https://5verst.ru/{slug}/` → **external_key = slug**
- `latitude`/`longitude` — text → float
- `is_pause` → `locations.is_paused` (3 на паузе)
- `city`, `region` — переносим как есть

**События — `list_all_events` (29 297)**

- Уникальность: `(name_point, date_event)`
- `is_test` → `events.is_test_event` (332 тестовых)
- `count_runners`, `count_vol` → `events.runner_count`, `volunteer_count`
- 58 prod-событий без протокола — создаём event без results

**Пробежки — `details_protocol` (1 187 621)**

- Уникальность: `(name_point, date_event, position)`
- `user_id` → participant + run_result
- `finish_time` (time) → `finish_time_sec` / display
- **external_result_key** (как в live sync): `{slug}:{YYYY-MM-DD}:{user_id}`

**Волонтёрства — `details_vol` (453 052)**

- Уникальность: `(user_id|name_runner, name_point, date_event, vol_role)`
- **external_result_key**: `{slug}:{YYYY-MM-DD}:vol:{user_id}:{role_slug}`

**Участники:** ~124 912 distinct `user_id` в протоколах.

### 3.3. S95

**Локации — `s95_location` (34)**

- Аналогично 5v: slug из `link_point` (`s95.ru/events/{slug}` или `s95.by/events/{slug}`)
- 2 локации на паузе

**События — `s95_list_all_events` (3 738)**

- 3778 distinct `(name_point, date)` в протоколах — 40 «лишних» пар только в runs (создать event on-the-fly)

**Пробежки — `s95_details_protocol` (130 591)**

- Есть `pace`, `club_name` — переносим в `run_results` / `participants`
- **external_result_key**: `{slug}:{YYYY-MM-DD}:{user_id}:{position}`

**Волонтёрства — `s95_details_vol` (33 197)**

- **external_result_key**: `{slug}:{YYYY-MM-DD}:vol:{user_id}:{role}`

**Справочник — `s95_runners` (16 369):** barcode и display name — дополняет `participants` при ETL.

### 3.4. parkrun

**Локации — `parkrun_russia_location` (105 строк, только `name_point`)**

- Имена в формате **English Title Case** (`Tambov`, `Angarskie Prudy`)
- Slug → через `data/location_catalog.json` (`parkrun_location` → `legacy_parkrun_slug` / `links[].external_key`)
- Координаты — из CSV/каталога нового проекта, не из legacy

**Пробежки — `parkrun_details_protocol` (729 020)**

- Диапазон дат: 2005-01-01 … 2025-11-22
- 105 RU-локаций из списка → 544 925 пробежек
- 2 405 прочих локаций (зарубежные/исторические) → 184 095 пробежек
- **external_result_key** (как в парсере): `parkrun:{user_id}:{slug}:{YYYY-MM-DD}:{run_number}`  
  Slug для RU — из каталога; для abroad — slugify(`name_point`)

**Профили — `parkrun_users` (54 445)**

- `user_id`, `name_runner`, `actual_name_runner`, `actual_age_category`

**Волонтёрства — `parkrun_vol_summary` (49 581)**

- **Не по событиям!** Агрегат: `(user_id, vol_role, count_vol)`
- В новом ЛК parkrun-волонтёрство уже моделируется как сводка (дата `1970-01-01`, локация `parkrun-summary`) — см. `volunteer_summary_to_canonical`
- **external_result_key**: `parkrun:{user_id}:vol-summary:{role_slug}`
- Роль в UI: `"{role} ({count}×)"`

**Не переносим:** `parkrun_old_details` — см. резюме.

### 3.5. Вспомогательные таблицы legacy

| Таблица | Строк | Использование при миграции |
|---------|-------|----------------------------|
| `general_link_all_location` | 199 | Сопоставление display name → URL 5v (fallback slug) |
| `all_system_loc` | 38 | Другие системы (run5, VK) — **не переносим** |
| `contacts_location` | 190 | Telegram-ссылки для старого бота — **не переносим** |

---

## 4. Что уже есть в новом проекте

| Компонент | Статус |
|-----------|--------|
| Alembic миграции 001–018 | ✅ |
| Global + user sync (5v, S95, parkrun) | ✅ |
| Celery workers + beat | ✅ docker-compose.yml |
| Каталог локаций + RunPark import | ✅ Makefile-цели |
| `migrate_from_legacy.py` | ❌ **нужно написать** |
| Production compose + TLS + deploy CI | ❌ заглушки |
| Frontend (полный ЛК) | ✅ |

---

## 5. План работ

### Фаза A. Подготовка сервера (1 день)

1. DNS: `app.run5k.run` → сервер.
2. Создать БД `saturday_runs_lk` + пользователя с write-доступом.
3. Read-only доступ к `five_verst_stats` для ETL (отдельный пользователь).
4. Зарегистрировать **нового** Telegram-бота (не legacy).
5. Production `.env`:
   - `APP_ENV=production`, `APP_DEBUG=false`
   - `APP_BASE_URL=https://app.run5k.run`
   - сильные секреты (`APP_SECRET_KEY`, `TELEGRAM_BOT_INTERNAL_SECRET`)
   - `ADMIN_TELEGRAM_ID`, `DEMO_TELEGRAM_ID`

### Фаза B. Production-стек (2–3 дня)

1. Дописать `docker-compose.prod.yml`:
   - `api`, `nginx`, `bot`, `worker`, `worker-s95`, `worker-parkrun`, `beat`
   - без bind-mount исходников; образы через `build`
   - `uvicorn --workers 2`, без `--reload`
2. Nginx + Let's Encrypt для `app.run5k.run`.
3. GitHub Actions `deploy.yml`: build frontend → rsync/SSH → `docker compose up -d`.
4. Smoke: `/health`, вход через Telegram, пустой dashboard.

### Фаза C. Пустая новая БД + справочники (полдня)

```bash
alembic upgrade head
make location-catalog-import-docker
make location-csv-import-docker
make runpark-mapping-import-docker
```

Справочники нужны **до** ETL parkrun (маппинг `name_point` → slug) и для карты.

### Фаза D. ETL `migrate_from_legacy.py` (3–5 дней разработки)

Скрипт с флагами `--platform five_verst|s95|parkrun|users`, `--dry-run`, `--validate`.

**Порядок загрузки (FK):**

```
platforms (seed из alembic)
  → locations (5v, s95, parkrun abroad on-the-fly)
  → events
  → participants
  → run_results
  → volunteer_results (5v, s95; parkrun — vol_summary)
  → users
  → platform_links (+ participant_id)
  → dashboard_cache (recompute)
```

**Техника:**

- Два подключения: `LEGACY_DATABASE_URL` (read-only) + `DATABASE_URL` (new)
- Батчи по 5 000–10 000 строк, `COPY` / bulk insert
- In-memory lookup: `name_point` → slug, `(platform, slug)` → location_id
- Idempotent upsert по тем же ключам, что live sync (см. §3)
- `is_test_event=true` для 332 тестовых событий 5v
- Логировать расхождения counts

**Рекомендуемые подфазы ETL:**

| Подфаза | Источник | Строк | Время (оценка) |
|---------|----------|-------|----------------|
| D1 | 5v global core | ~1,64M | 15–25 мин |
| D2 | S95 global core | ~168k | 3–5 мин |
| D3 | parkrun runs + users | ~783k | 10–20 мин |
| D4 | parkrun vol_summary | ~50k | 1–2 мин |
| D5 | tg_user_profile | 265 | секунды |

### Фаза E. Валидация (0,5–1 день)

**Автоматические проверки:**

```sql
-- Пример: сравнение counts по платформе
SELECT p.code, COUNT(*) FROM run_results rr
JOIN events e ON e.id = rr.event_id
JOIN platforms p ON p.id = e.platform_id
GROUP BY p.code;
```

| Проверка | Legacy | Ожидание в new |
|----------|--------|----------------|
| 5v runs | 1 187 621 | = |
| 5v vols | 453 052 | = |
| 5v prod events | 28 965 | = |
| s95 runs | 130 591 | = |
| parkrun runs | 729 020 | = |
| users | 265 | = |
| platform_links 5v | 157 | = |

**Ручной spot-check (5 пользователей):**

- 1 пользователь только 5v
- 1 только parkrun (с зарубежными пробежками)
- 1 с тремя системами
- Сверить totals на dashboard с Grafana/legacy

### Фаза F. Пост-миграция (0,5 дня)

1. Включить Celery Beat (global sync догоняет после 2025-11-22 / 2026-05-23).
2. Low-priority `user_sync` для всех `platform_links`.
3. Parkrun: ручной refresh у 2–3 пользователей — проверить merge без дублей.
4. Пересчитать `dashboard_cache` для всех пользователей с привязками.

### Фаза G. Бета и go-live (1–2 дня)

1. Invite-only: активные пользователи из `tg_user_profile` (~157 с 5v).
2. Чеклист: вход, dashboard, таблицы, карта, настройки, sync, admin-очередь.
3. Legacy параллельно; баннер на Grafana про новый ЛК.
4. Открытая регистрация после стабилизации.

---

## 6. Стратегия: ETL vs повторный sync

| Данные | ETL | Global/User sync после |
|--------|-----|------------------------|
| 5v / S95 история (~1,8M runs) | **Обязательно** | Delta с последней даты |
| parkrun runs (729k) | **Обязательно** | User refresh для свежего |
| parkrun vol_summary | **ETL** (формат сводки) | User refresh обновит роли |
| users + bindings | **Обязательно** | — |
| RunPark | Import scripts | Global sync RunPark |
| Локации (coords, catalog) | Import scripts | Geocode tasks |

Полагаться только на sync **не вариант**: парсинг 1,8M+ строк занял бы недели и ударил по rate limits; parkrun bulk в legacy уже собран.

---

## 7. Rollback

Legacy не трогаем. Откат нового ЛК:

1. Остановить compose на `app.run5k.run`
2. При необходимости `DROP DATABASE saturday_runs_lk` и начать заново
3. run5k.run / legacy bot продолжают работать

---

## 8. Риски и митигация

| Риск | Митигация |
|------|-----------|
| `name_point` 5v ≠ slug | Lookup через `general_location.link_point`; fallback `general_link_all_location` |
| parkrun RU name → slug | `location_catalog.json` + `parkrun_russia_location` |
| parkrun abroad (2405 locs) | slugify name; `country` = NULL или «За рубежом» |
| parkrun vol без дат | Тот же формат, что `volunteer_summary_to_canonical` |
| Дубли при re-sync | Совпадение `external_result_key` с live parsers |
| Нагрузка на PG при ETL | Батчи, индексы создавать до bulk load или временно отключать |
| Playwright на prod | `worker-parkrun`, concurrency=1, достаточно RAM |

---

## 9. Критический путь и сроки

```
A. Сервер + DNS + .env
B. Production compose + TLS          } параллельно с D
D. migrate_from_legacy.py (dev/test на копии legacy)
C. alembic + справочники
D*. Прогон ETL на prod
E. Валидация
F. Beat + user_sync
G. Бета → go-live
```

**Оценка:** 1,5–2,5 недели календарно (ETL-скрипт — главный блокер).

---

## 10. Следующие шаги (action items)

- [ ] Написать `backend/scripts/migrate_from_legacy.py` по маппингу из §3
- [ ] Прогнать ETL на локальной копии `five_verst_stats` (pg_dump)
- [ ] Дописать `docker-compose.prod.yml` + deploy workflow
- [ ] Создать `saturday_runs_lk` на сервере
- [ ] Провести ETL + валидацию
- [ ] Бета с 5–10 пользователями
- [ ] Сменить пароль read-only после миграции

---

## Приложение A. Полный список таблиц legacy

```
all_system_loc, archive_*, change_log, city_population, club_change_log,
contacts_location, details_protocol, details_vol, general_date_load_protocol,
general_link_all_location, general_location, january2025, january2026,
list_all_events, list_clubs, parkrun_details_protocol, parkrun_old_details,
parkrun_russia_location, parkrun_users, parkrun_vol_summary,
s95_details_protocol, s95_details_vol, s95_list_all_events, s95_*_backup/copy,
s95_location, s95_runners, tg_user_profile, update_table
```

**В ETL:** блок «Пользователи и результаты» в §3 + `s95_runners`.  
**Вне ETL:** всё остальное из списка выше, если не указано иное.

## Приложение B. Пользователи по привязкам

| 5v | S95 | parkrun | Кол-во |
|----|-----|---------|--------|
| — | — | — | 104 |
| ✓ | — | — | 103 |
| ✓ | ✓ | ✓ | 37 |
| ✓ | ✓ | — | 9 |
| ✓ | — | ✓ | 8 |
| — | ✓ | ✓ | 2 |
| — | — | ✓ | 2 |
