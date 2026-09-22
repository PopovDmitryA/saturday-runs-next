# Отработавшие скрипты

Сюда переехали разовые бэкфилы и починки, которые уже прогнаны на проде.
Держать их рядом с рабочими инструментами было неудобно: чтобы найти нужный
скрипт, приходилось знать его имя заранее, а при рефакторинге сервисов они
молча ломались — никто их не запускает и не покрывает тестами.

Скрипты оставлены, а не удалены: в них видно, КАК чинили данные, и иногда это
единственное описание разовой операции. Если понадобится повторить — проверьте
сначала, что функции, которые он импортирует, ещё существуют.

Перенесено 21.09.2026. История файлов сохранена:
`git log --follow backend/scripts/archive/<файл>`.

| Скрипт | Что делал |
|---|---|
| `backfill_display_names.py` | Заполнил `users.display_name` после миграции 066 |
| `backfill_event_source_urls.py` | Проставил `events.source_url` по ссылкам на протоколы |
| `backfill_first_run_flags.py` | Первый старт участника и первый старт на локации для s95, parkrun, runpark |
| `backfill_five_verst_age_category.py` | Убрал место в группе из `age_category` у 5 вёрст |
| `backfill_foreign_parkrun_gender_positions.py` | Снял место в поле у зарубежных parkrun |
| `backfill_gender_positions.py` | Первый расчёт мест по полу |
| `backfill_parkrun_gender_positions.py` | То же для parkrun |
| `backfill_legacy_location_fields.py` | Город, регион и страна локаций из легаси-базы |
| `backfill_legacy_run_fields.py` | Позиция и возрастная группа из легаси-протоколов |
| `backfill_location_region.py` | Регион локаций по координатам |
| `backfill_participant_gender.py` | Пол участников после миграции 053 |
| `backfill_protocol_sync_migrated.py` | Отметил перенесённые из легаси протоколы как выгруженные |
| `backfill_runpark_event_source_urls.py` | Ссылки на протоколы RunPark |
| `backfill_s95_event_numbers.py` | Номера забегов S95 |
| `backfill_s95_location_country.py` | Страна локаций S95 по домену реестра |
| `backfill_schedule_parsed.py` | Разбор расписания из описаний локаций |
| `backfill_truncated_age_categories.py` | Обрезанные группы 5 вёрст («М11» ← «М110-114») |
| `dedupe_five_verst_runs.py` | Дубли результатов 5 вёрст |
| `dedupe_volunteer_results.py` | Дубли волонтёрских строк |
| `delete_removed_volunteer_result.py` | Удаление строки, снятой у источника |
| `enable_runpark_transitional_locations.py` | Подключил историю RunPark у перешедших локаций |
| `fix_runpark_profile_urls.py` | Починил ссылки на профили RunPark |
| `import_legacy_protocol_facts.py` | Импорт моментов выгрузки протоколов из легаси |
| `recount_page_types.py` | Пересчёт типа страницы у записанных просмотров |
| `seed_release_history.py` | Ретро-история релизов для «Обновлений» |

Рабочие инструменты остались уровнем выше: синки и перечитки (`five_verst_sync_*`,
`s95_*`, `parkrun_*`, `runpark_*`), пересчёты рекордов (`recalculate_*`), импорты
каталогов (`import_*`), релизы (`add_release.py`, `update_release.py`), а также
доборщики географии, которые полезны при каждой новой локации
(`backfill_location_country.py`, `backfill_location_geo.py`,
`backfill_location_tz_offsets.py`, `backfill_parkrun_coordinates.py`,
`backfill_parkrun_country.py`, `backfill_parkrun_from_archive.py`).
