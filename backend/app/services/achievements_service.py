"""Цели на год и челленджи с уровнями (бронза/серебро/золото).

Челленджи — перенос правил из Grafana-дашборда «Челленджи»
(data/grafana/dashboards/3e54a2d8….json) + новые. Три вида:
- coverage: закрыть коллекцию (секунды финиша :00–:59, буквы алфавита, дни года…),
  уровни — доли коллекции (25%/50%/100%);
- counter: накопить события-совпадения (палиндромы, дежавю…), уровни — пороги;
- value: вырастить показатель (p-индекс, уникальные локации…), уровни — пороги.

Каждый челлендж (кроме «Семь дней» — коллекция из 7 клеток, не режется на тиры)
даёт три уровня сложности — easy/medium/hard, в каждом свои бронза/серебро/золото
(CHALLENGE_TIERS). Пороги easy/medium откалиброваны по фактическому распределению
прогресса зарегистрированных пользователей (307–401 чел., август 2026): easy
закрывается за первые полгода-год активности, medium — цель регулярного бегуна,
hard в основном воспроизводит прежние, «ветеранские» пороги. Тиры внутри тира
монотонно растут (bronze medium > gold easy и т.д.) — это гарантирует, что
"лучший" тир/уровень при показе бейджа однозначно определяется как самый
сложный тир, где вообще есть хоть один уровень (см. _challenge()).

Цели — пресеты (без свободного текста), можно выбрать любое число из GOAL_PRESETS
на год, хранятся в user_goals. Прогресс и прогноз «успеешь/не успеешь» считаются
на лету.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import date, timedelta
from uuid import UUID

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.geo.country_names import normalize_country_name
from app.models import (
    Event,
    Location,
    LocationRating,
    LocationRatingPhoto,
    Participant,
    Platform,
    PlatformLink,
    RunResult,
    User,
    UserGoal,
    VolunteerResult,
)
from app.saturday_week import max_saturday_streak, saturday_weeks
from app.services.location_catalog_service import LocationCatalogIndex, russian_parkrun_location_ids
from app.services.start_weather_service import (
    DEEP_FROST_C,
    DOWNPOUR_MM,
    FROST_C,
    HEAT_C,
    RAIN_MM,
    SNOW_CODES,
    SNOW_DEPTH_CM,
    WINDY_GUST_MS,
    format_temperature,
    weather_rows_for_pairs,
)


def _f(value: object) -> float | None:
    return None if value is None else float(value)  # type: ignore[arg-type]


from app.services.location_map_service import MAP_HISTORIC_PLATFORM
from app.services.platform_titles import PLATFORM_TITLES
from app.services.user_location_stats import _canonical_region, _normalize_geo_value
from app.time_format import normalize_finish_time_display
from app.volunteer_role_taxonomy import (
    CANONICAL_ROLE_LABELS,
    canonical_volunteer_role,
    role_is_on_site,
    role_occasions,
)
from app.volunteering_occasions import count_volunteering_for_platform, is_inventory_day, volunteer_occasion_dates

LEVEL_ORDER = ("bronze", "silver", "gold")

# Один тир на «весь» челлендж — единственный ключ словаря должен называться
# "solo" (сигнал фронту не рисовать вкладки сложности). Иначе три тира —
# всегда именно "easy"/"medium"/"hard" в этом порядке (порядок словаря важен:
# на нём строится выбор дефолтной вкладки и "лучшего" достижения).
TIER_LABELS: dict[str, str | None] = {
    "easy": "Лёгкий",
    "medium": "Средний",
    "hard": "Сложный",
    "solo": None,
}

# Реестр порогов: code -> {tier_key: (bronze, silver, gold)}. Единственное
# место в кодовой базе, где меняются числа уровней — история (level_dates)
# считается на лету по сырым событиям, так что правка порогов не требует ни
# миграции, ни бэкфилла.
CHALLENGE_TIERS: dict[str, dict[str, tuple[int, int, int]]] = {
    "seconds": {"easy": (3, 7, 10), "medium": (15, 25, 35), "hard": (40, 50, 60)},
    "positions": {"easy": (3, 7, 10), "medium": (15, 25, 40), "hard": (50, 75, 100)},
    "alphabet": {"easy": (2, 4, 7), "medium": (9, 13, 17), "hard": (20, 24, 28)},
    "calendar_days": {"easy": (5, 10, 25), "medium": (50, 100, 150), "hard": (200, 280, 366)},
    "start_numbers": {"easy": (5, 15, 30), "medium": (50, 80, 120), "hard": (150, 175, 200)},
    "start_numbers_pro": {"easy": (5, 15, 30), "medium": (50, 80, 120), "hard": (150, 175, 200)},
    "weekdays": {"solo": (2, 4, 7)},
    "palindrome": {"easy": (1, 3, 5), "medium": (7, 10, 13), "hard": (15, 20, 25)},
    "deja_vu": {"easy": (1, 2, 4), "medium": (8, 15, 25), "hard": (40, 70, 110)},
    "number_match": {"easy": (1, 2, 3), "medium": (5, 8, 12), "hard": (20, 35, 50)},
    "jubilee": {"easy": (1, 2, 3), "medium": (5, 7, 10), "hard": (15, 25, 50)},
    "p_index": {"easy": (2, 3, 4), "medium": (5, 6, 8), "hard": (10, 12, 15)},
    "pilgrim": {"easy": (3, 5, 10), "medium": (15, 25, 40), "hard": (60, 100, 150)},
    "regions": {"easy": (2, 3, 5), "medium": (8, 12, 18), "hard": (25, 40, 60)},
    "streak": {"easy": (4, 8, 12), "medium": (20, 30, 45), "hard": (60, 85, 120)},
    "best_year": {"easy": (5, 10, 20), "medium": (26, 34, 42), "hard": (45, 50, 55)},
    "inspector": {"easy": (1, 5, 10), "medium": (20, 40, 70), "hard": (100, 150, 200)},
    "reviewer": {"easy": (1, 3, 7), "medium": (15, 25, 50), "hard": (75, 100, 150)},
    # Фото к отзыву — самая дорогая форма обратной связи: снять, выбрать,
    # загрузить. На 04.09.2026 фото приложили 17 человек, максимум — два
    # отзыва, поэтому лёгкий тир начинается с одного и не уходит дальше пяти.
    "photo_reporter": {"easy": (1, 3, 5), "medium": (10, 20, 35), "hard": (50, 75, 100)},
    # V-индекс — те же пороги, что у p-индекса (решение Дмитрия 07.09.2026).
    # Оба индекса Хирша, и шкала у них должна читаться одинаково: V=5 и p=5
    # это одно и то же число, странно давать за них разные медали. То, что
    # роли набираются легче локаций (V>=3 у 64% волонтёров против p>=3 у 38%
    # бегунов), делает V-индекс не «лёгким тиром», а просто более доступным
    # челленджем — снижать под это пороги значит обесценивать медаль.
    "v_index": {"easy": (2, 3, 4), "medium": (5, 6, 8), "hard": (10, 12, 15)},
    # «Мастер на все роли» — счётчик РАЗНЫХ освоенных ролей, любых: цель не в
    # том, чтобы закрыть конкретный список (у каждой площадки он свой), а в
    # широте. Потолок лестницы — 20 ролей (решение Дмитрия 07.09.2026), три
    # тира разложены по всему диапазону. Доли 588 волонтёров прод-базы со
    # сводкой parkrun: лёгкий — 95 / 83 / 72 %, средний — 60 / 50 / 35 %,
    # сложный — 23 / 14 / 5.6 %. В справочнике 37 ролей, рекорд базы — 25.
    "role_master": {"easy": (2, 4, 6), "medium": (8, 10, 13), "hard": (15, 17, 20)},
    # Ч48 «Хет-трик систем»: систем всего четыре, лестница упирается в потолок
    # по построению — тир один. Доли 519 бегунов прод-масштабной базы:
    # 2 системы — 62%, 3 — 26%, все 4 — 5%.
    "platform_slam": {"solo": (2, 3, 4)},
    # Ч51 «Международный турист»: 87% бегунов знают ровно одну страну, поэтому
    # уже бронза (вторая страна) — редкость, а золото берут единицы. Тир один:
    # растянуть это на три уровня сложности не на чем — рекорд базы 11 стран,
    # и «сложный» тир стоял бы пустым у всех.
    "countries": {"solo": (2, 3, 5)},
    # Ч23 «Коллекция минут» — ЗАКРЫТЫЕ минутные корзины, по одной за каждую
    # новую минуту на финишных часах. Считать размах (от самой быстрой минуты
    # до самой медленной) оказалось нельзя: две пробежки, 21:xx и 60:xx, давали
    # сразу сорок корзин и закрывали челлендж целиком (Дмитрий, 14.09.2026).
    # Перцентили по 519 бегунам: p20=9, p50=15, p80=20, p90=23, p95=27, p99=33,
    # рекорд базы — 48. Потолок лестницы оставлен на 35, как Дмитрий и просил.
    "minute_range": {"easy": (3, 6, 9), "medium": (12, 15, 18), "hard": (22, 28, 35)},
    # Ч26 «Индекс Уилсона» — классический (цепочка с №1). Перцентили: p50=1,
    # p80=4, p90=12, p95=17, p99=55.
    # Лестница задана Дмитрием 14.09.2026. Прежняя (1/2/3 · 5/8/12 · 18/25/40)
    # была откалибрована по распределению (p50=1, p90=12, p99=55) и оказалась
    # слишком доступной: золото лёгкого тира выдавалось за три номера подряд.
    # Новая смотрит за горизонт — по тому же правилу, что у p-индекса: сложный
    # тир и должен быть запасом на вырост, а не снимком сегодняшней базы.
    "wilson": {"easy": (3, 10, 15), "medium": (25, 40, 75), "hard": (75, 100, 150)},
    # Ч27 «Клуб Нельсона» — финиши на стартах с номером, кратным 111. Половина
    # базы имеет хотя бы один, рекорд — 5. Тир один: на трёх «нельсонах» уже
    # 7.7% бегунов, дальше растягивать нечего.
    # Считаем РАЗНЫЕ нельсоны, а не финиши на них (карточка стала коллекцией
    # 11.09.2026). Доли базы: №111 или №222 есть у половины, оба — у 14%,
    # три разных — у двух человек из 519. Золото здесь и должно быть за
    # горизонтом: до №333 действующим площадкам ещё года полтора.
    "nelson": {"solo": (1, 2, 3)},
    # Ч28 «Числа Фибоначчи» — коллекция из 15 клеток (1…987). Доли базы:
    # 4 клетки — 55%, 6 — 39%, 8 — 22%, 10 — 12%, 11 — 6.7%, 12 — 1.5%.
    # Золото сложного (13) требует №233 — до него дорастают 5 вёрст и S95;
    # №377 и дальше живут только в мировом parkrun.
    "fibonacci": {"easy": (2, 3, 4), "medium": (6, 8, 10), "hard": (11, 12, 13)},
    # Ч29 «Простые числа» — счётчик финишей на стартах с простым номером.
    # Перцентили среди тех, у кого есть хоть один: p50=18, p80=42, p95=77, p99=101.
    # РАЗНЫЕ простые номера до №400 (всего их 78). Перцентили 503 бегунов:
    # p20=6, p35=11, p50=17, p65=23, p80=31, p90=38, p95=45, p99=57, рекорд 71.
    "primes": {"easy": (2, 5, 10), "medium": (17, 23, 31), "hard": (38, 45, 57)},
    # Погодные челленджи (Дмитрий, 14.09.2026). «Морж» — старт при −20° и ниже:
    # в средней полосе такое случается раз в несколько зим, в Якутске — всю зиму,
    # поэтому средний и сложный тиры — сибирские. «Огнеупорный» — от +25° в час
    # старта, симметричная редкость (+25°). «Под дождём» — от 1 мм за час забега.
    "walrus": {"easy": (1, 2, 3), "medium": (5, 8, 12), "hard": (20, 35, 50)},
    "heatproof": {"easy": (1, 2, 3), "medium": (5, 8, 12), "hard": (20, 35, 50)},
    "rain_runner": {"easy": (3, 7, 12), "medium": (20, 30, 45), "hard": (60, 80, 100)},
    # Коллекции без тиров: всё меню погоды (8 клеток) и все 12 месяцев.
    "all_weather": {"solo": (3, 5, 8)},
    "seasons": {"solo": (6, 9, 12)},
}

# Ч48: порядок систем в клетках «Хет-трика» — по возрасту системы в России.
PLATFORM_SLAM_ORDER: tuple[str, ...] = ("parkrun", "five_verst", "s95", "runpark")

# Ч23: минутные корзины считаем в разумных границах. Нижняя — 14 минут
# (решение Дмитрия 14.09.2026): 13:xx на пятёрке это уровень мирового рекорда,
# в парковых протоколах такое время означает ошибку, а не бегуна. В прод-базе
# финишей быстрее 14 минут нет ни одного, так что на распределение и на пороги
# правка не влияет. Верхняя — два часа: дальше это уже сбой протокола, а не
# прогулка шагом.
MINUTE_BUCKET_MIN = 14
MINUTE_BUCKET_MAX = 120

# Ч28: числа Фибоначчи в пределах номеров, которые вообще бывают у стартов
# (мировой рекорд parkrun — чуть больше 1100).
FIBONACCI_NUMBERS: tuple[int, ...] = (1, 2, 3, 5, 8, 13, 21, 34, 55, 89, 144, 233, 377, 610, 987)

# Ч27: «нельсон» из крикета — 111 и его кратные. Коллекция кончается на 999:
# «три девятки» — последний нельсон, который вообще выговаривают в крикете.
NELSON_STEP = 111
NELSON_NUMBERS: tuple[int, ...] = tuple(range(NELSON_STEP, 10 * NELSON_STEP, NELSON_STEP))

# Ч26: сколько номеров показываем на ленте Уилсона. Классическая цепочка длиннее
# 55 есть у одного человека из ста, так что шестьдесят клеток закрывают почти
# всех; лента при необходимости растягивается до конца плавающей цепочки.
WILSON_STRIP_NUMBERS = 60

# Ч29: верхняя граница коллекции простых. Рекорд действующих площадок — №241,
# у закрытого российского parkrun — №357; за 400 во всей базе набирается
# одиннадцать финишей, и все — на зарубежных площадках, куда не спланируешь.
PRIME_STRIP_MAX = 400

# Диапазоны номеров для «Нумератора» и «Нумератора ПРО». Одни и те же границы
# нужны и карточке челленджа, и таблице планирования — держим в одном месте,
# чтобы сетка ячеек и таблица не разъехались при правке порогов.
START_NUMBER_RANGES: dict[str, tuple[int, int]] = {
    "start_numbers": (1, 200),
    "start_numbers_pro": (201, 400),
}
# Названия тех же челленджей: их спрашивает не только страница достижений, но и
# попап точки на карте («даст ли этот старт +1 в Нумераторе»), — держим рядом с
# диапазонами, чтобы подписи не разъехались.
START_NUMBER_TITLES: dict[str, str] = {
    "start_numbers": "Нумератор",
    "start_numbers_pro": "Нумератор ПРО",
}
# Сколько недельных окон показываем в планировании: ближайшая неделя, W+1, W+2.
START_NUMBER_PLAN_WEEKS = 3

# Горизонт планирования «числовых» челленджей (Фибоначчи, Нельсон, простые,
# Уилсон). Три недели «Нумератора» им не годятся: до №222 ближайшей площадке
# полгода, и в трёхнедельном окне подсказка была бы пустой у всех. Полгода —
# компромисс: №144, №222 и №233 в него попадают (замер 10.09.2026: 17, 43 и 90
# площадок соответственно), а дальше прогноз «плюс старт в неделю» врёт слишком
# сильно, чтобы называть даты.
PLANNING_WEEKS = 26
# Выше номеров стартов в наших системах не бывает: рекорд действующих площадок —
# 241, у закрытого российского parkrun — 357.
PLANNING_MAX_NUMBER = 400

# Русский алфавит для челленджа «Алфавит» (Ё объединяем с Е, твёрдый/мягкий знак
# и Ы не бывают первыми буквами названий).
_RU_ALPHABET = "АБВГДЕЖЗИКЛМНОПРСТУФХЦЧШЩЭЮЯ"

_EPOCH_GUARD = date(1970, 1, 1)

# Рецензия — комментарий не короче стольких символов (звёзд мало, нужен текст).
REVIEW_MIN_COMMENT_LEN = 50


@dataclass(frozen=True)
class RatingRow:
    """Оценка старта. rated_on — дата САМОЙ оценки, а не старта: счётчик растёт
    в момент, когда человек оценил, и уровни датируются по нему."""

    rated_on: date
    platform_code: str
    is_review: bool
    # Приложено ли к оценке хотя бы одно фото — счётчик «Фоторепортёра».
    has_photo: bool


@dataclass(frozen=True)
class VolunteerRoleRow:
    """Волонтёрство с приведённой к общему знаменателю ролью.

    role_key/role_label — результат canonical_volunteer_role: «Сканирование»
    RunPark, «Сканер» С95 и «Barcode Scanning» parkrun — одна и та же роль,
    иначе V-индекс и чек-лист ролей завышались бы за счёт того, что системы
    называют одну и ту же работу по-своему.

    event_date = None и occasions > 1 — это parkrun: он отдаёт не протоколы, а
    сводку профиля («Marshal (12×)» — двенадцать волонтёрств маршалом без дат). Такие
    волонтёрства в зачёт идут (иначе ветеран parkrun выглядел бы новичком), но
    датировать уровни ими нельзя.
    """

    event_date: date | None
    platform_code: str
    role_key: str
    role_label: str
    location_name: str
    location_key: str
    occasions: int = 1


@dataclass(frozen=True)
class RunRow:
    event_date: date
    finish_time_sec: int | None
    position: int | None
    event_number: int | None
    location_name: str
    location_key: str
    region: str | None
    # Страна площадки по-русски (см. _row_country): None — в БД её нет, такая
    # пробежка не идёт в зачёт «Международного туриста», но видна отдельной
    # строкой в деталях, чтобы дыра в данных не выглядела занижением счётчика.
    country: str | None
    platform_code: str
    is_pr: bool
    # Погода в час старта (start_weather); None — строки нет (зарубежный parkrun
    # или площадка без координат). Заполняется одним батчем в _collect_run_rows.
    temperature_c: float | None = None
    precipitation_run_mm: float | None = None
    snow_depth_cm: float | None = None
    snowfall_cm: float | None = None
    weather_code: int | None = None
    wind_gusts_ms: float | None = None


@dataclass
class WeatherDay:
    """День, когда человек был на площадке, с погодой того часа старта.

    Погодные челленджи считают не финиши, а присутствие: волонтёр мокнет под
    тем же ливнем и мёрзнет в тот же мороз, что и бегуны (просьба Киры
    Барановской, 18.09.2026). Поэтому к пробежкам добавляются волонтёрства —
    но только в ролях, требующих быть на площадке (role_is_on_site): за пост в
    канале или обработку результатов из дома «Морж» начисляться не должен.

    Один день на одной площадке — одна строка: пробежал и отработал в ту же
    субботу — это по-прежнему один выход, а не два.
    """

    event_date: date
    location_name: str
    location_key: str
    platform_code: str
    #: День закрыт волонтёрством, а не финишем — показываем это в деталях.
    volunteered: bool
    temperature_c: float | None = None
    precipitation_run_mm: float | None = None
    snow_depth_cm: float | None = None
    snowfall_cm: float | None = None
    weather_code: int | None = None
    wind_gusts_ms: float | None = None


# ---------------------------------------------------------------------------
# Сбор данных


def _collect_run_rows(db: Session, user_id: UUID) -> list[RunRow]:
    from app.services.personal_record_service import user_secondary_crosslinked_run_ids

    query = (
        db.query(RunResult, Event, Location, Platform.code)
        .join(Event, RunResult.event_id == Event.id)
        .join(Location, Event.location_id == Location.id)
        .join(Platform, Event.platform_id == Platform.id)
        .join(PlatformLink, PlatformLink.participant_id == RunResult.participant_id)
        .filter(
            PlatformLink.user_id == user_id,
            PlatformLink.platform_id == Platform.id,
            Event.is_test_event.is_(False),
            Event.event_date > _EPOCH_GUARD,
        )
    )
    secondary_ids = user_secondary_crosslinked_run_ids(db, user_id)
    if secondary_ids:
        query = query.filter(RunResult.id.notin_(secondary_ids))

    catalog_index = LocationCatalogIndex(db)
    russian_parkrun = russian_parkrun_location_ids(db, catalog_index)
    fetched = query.all()
    weather_rows = weather_rows_for_pairs(db, [(event.location_id, event.event_date) for _r, event, _l, _p in fetched])
    rows: list[RunRow] = []
    for run, event, location, platform_code in fetched:
        weather = weather_rows.get((event.location_id, event.event_date))
        rows.append(
            RunRow(
                event_date=event.event_date,
                finish_time_sec=run.finish_time_sec,
                position=run.position,
                event_number=event.event_number,
                location_name=catalog_index.display_name(location, platform_code),
                location_key=catalog_index.canonical_identity_key(location, platform_code),
                region=_normalize_geo_value(location.region),
                country=_row_country(location, platform_code, russian_parkrun),
                platform_code=platform_code,
                is_pr=bool(run.is_pr),
                temperature_c=_f(weather.temperature_c) if weather else None,
                precipitation_run_mm=_f(weather.precipitation_run_mm) if weather else None,
                snow_depth_cm=_f(weather.snow_depth_cm) if weather else None,
                snowfall_cm=_f(weather.snowfall_cm) if weather else None,
                weather_code=int(weather.weather_code) if weather and weather.weather_code is not None else None,
                wind_gusts_ms=_f(weather.wind_gusts_ms) if weather else None,
            )
        )
    rows.sort(key=lambda row: (row.event_date, row.location_key))
    return rows


def _row_country(location: Location, platform_code: str, russian_parkrun: frozenset[UUID]) -> str | None:
    """Страна площадки одним написанием.

    Русский parkrun определяется не полем country (у части строк оно пустое или
    осталось заглушкой «United Kingdom» из мирового каталога), а связкой с
    каталогом локаций — тем же правилом, что карта и рейтинги
    (russian_parkrun_location_ids). Остальным нормализуем написание:
    «United Kingdom» и «Великобритания» — одна страна, иначе турист по
    британским паркранам получил бы две.
    """
    if platform_code == MAP_HISTORIC_PLATFORM and location.id in russian_parkrun:
        return "Россия"
    return normalize_country_name(location.country)


def _collect_rating_rows(db: Session, user_id: UUID) -> list[RatingRow]:
    """Оценки пользователя: когда оценил, в какой системе, рецензия или звёзды,
    и есть ли фото. Фото считаем наличием, а не количеством: пять кадров на
    один старт — это по-прежнему один рассказ о нём."""
    photos = db.query(LocationRatingPhoto.rating_id).filter(LocationRatingPhoto.rating_id == LocationRating.id).exists()
    query = db.query(
        LocationRating.created_at,
        LocationRating.platform_code,
        LocationRating.comment,
        photos.label("has_photo"),
    ).filter(LocationRating.user_id == user_id)
    rows = [
        RatingRow(
            rated_on=created_at.date(),
            platform_code=platform_code,
            is_review=len((comment or "").strip()) >= REVIEW_MIN_COMMENT_LEN,
            has_photo=bool(has_photo),
        )
        for created_at, platform_code, comment, has_photo in query.all()
    ]
    rows.sort(key=lambda row: row.rated_on)
    return rows


def _collect_volunteer_role_rows(db: Session, user_id: UUID) -> list[VolunteerRoleRow]:
    """Волонтёрства с каноническими ролями — для V-индекса и чек-листа ролей.

    Отдельный сбор от _collect_volunteer_rows: тому нужны только дата и
    локация (он считает поводы по календарю), а здесь важна роль и не важно
    схлопывание нескольких ролей одного дня — две разные роли в одну субботу
    это две освоенные роли.
    """
    query = (
        db.query(Event.event_date, VolunteerResult.role, Platform.code, Location)
        .select_from(VolunteerResult)
        .join(Event, VolunteerResult.event_id == Event.id)
        .join(Location, Event.location_id == Location.id)
        .join(Platform, Event.platform_id == Platform.id)
        .join(PlatformLink, PlatformLink.participant_id == VolunteerResult.participant_id)
        .filter(
            PlatformLink.user_id == user_id,
            PlatformLink.platform_id == Platform.id,
            Event.is_test_event.is_(False),
        )
    )
    catalog_index = LocationCatalogIndex(db)
    rows: list[VolunteerRoleRow] = []
    for event_date, role, platform_code, location in query.all():
        canonical = canonical_volunteer_role(role)
        if canonical is None:
            continue
        # Сводка parkrun лежит на псевдо-событии 1970-01-01 — обычно такие
        # строки отсекает _EPOCH_GUARD, но здесь они нужны: без них ветеран
        # parkrun остался бы с пустым чек-листом ролей.
        dated = event_date is not None and event_date > _EPOCH_GUARD
        rows.append(
            VolunteerRoleRow(
                event_date=event_date if dated else None,
                platform_code=platform_code,
                role_key=canonical.key,
                role_label=canonical.label,
                location_name=catalog_index.display_name(location, platform_code),
                location_key=catalog_index.canonical_identity_key(location, platform_code),
                occasions=1 if dated else (role_occasions(role) or 1),
            )
        )
    # Недатированная сводка идёт первой: российские parkrun работали
    # 2014–2022, то есть в основном ДО появления 5 вёрст и С95.
    rows.sort(key=lambda row: (row.event_date is not None, row.event_date or date.min, row.role_key))
    return rows


def _collect_weather_days(db: Session, user_id: UUID, rows: list[RunRow]) -> list[WeatherDay]:
    """Дни на площадке с погодой: финиши плюс волонтёрства «с присутствием».

    Пробежки уже несут погоду (её подтянул _collect_run_rows), волонтёрским
    строкам она добирается отдельным батчем. Удалённые роли отсекаются
    таксономией, недатированная сводка parkrun — тоже: без даты неизвестно,
    какая тогда была погода.
    """

    by_day: dict[tuple[str, date], WeatherDay] = {}
    for row in rows:
        by_day[(row.location_key, row.event_date)] = WeatherDay(
            event_date=row.event_date,
            location_name=row.location_name,
            location_key=row.location_key,
            platform_code=row.platform_code,
            volunteered=False,
            temperature_c=row.temperature_c,
            precipitation_run_mm=row.precipitation_run_mm,
            snow_depth_cm=row.snow_depth_cm,
            snowfall_cm=row.snowfall_cm,
            weather_code=row.weather_code,
            wind_gusts_ms=row.wind_gusts_ms,
        )

    query = (
        db.query(Event.event_date, Event.location_id, VolunteerResult.role, Platform.code, Location)
        .select_from(VolunteerResult)
        .join(Event, VolunteerResult.event_id == Event.id)
        .join(Location, Event.location_id == Location.id)
        .join(Platform, Event.platform_id == Platform.id)
        .join(PlatformLink, PlatformLink.participant_id == VolunteerResult.participant_id)
        .filter(
            PlatformLink.user_id == user_id,
            PlatformLink.platform_id == Platform.id,
            Event.is_test_event.is_(False),
            Event.event_date > _EPOCH_GUARD,
        )
    )
    catalog_index = LocationCatalogIndex(db)
    pending: dict[tuple[str, date], tuple[UUID, date, str, str]] = {}
    for event_date, location_id, role, platform_code, location in query.all():
        canonical = canonical_volunteer_role(role)
        if canonical is None or not role_is_on_site(canonical.key):
            continue
        location_key = catalog_index.canonical_identity_key(location, platform_code)
        day = (location_key, event_date)
        if day in by_day or day in pending:
            # Несколько ролей в одну субботу — это один выход на площадку.
            continue
        pending[day] = (location_id, event_date, catalog_index.display_name(location, platform_code), platform_code)

    weather_rows = weather_rows_for_pairs(db, [(loc_id, when) for loc_id, when, _n, _p in pending.values()])
    for (location_key, event_date), (location_id, _when, location_name, platform_code) in pending.items():
        weather = weather_rows.get((location_id, event_date))
        by_day[(location_key, event_date)] = WeatherDay(
            event_date=event_date,
            location_name=location_name,
            location_key=location_key,
            platform_code=platform_code,
            volunteered=True,
            temperature_c=_f(weather.temperature_c) if weather else None,
            precipitation_run_mm=_f(weather.precipitation_run_mm) if weather else None,
            snow_depth_cm=_f(weather.snow_depth_cm) if weather else None,
            snowfall_cm=_f(weather.snowfall_cm) if weather else None,
            weather_code=int(weather.weather_code) if weather and weather.weather_code is not None else None,
            wind_gusts_ms=_f(weather.wind_gusts_ms) if weather else None,
        )

    days = list(by_day.values())
    days.sort(key=lambda day: (day.event_date, day.location_key))
    return days


def _collect_volunteer_rows(db: Session, user_id: UUID) -> dict[str, list[tuple[date, str]]]:
    """Волонтёрские строки по платформам: (дата, location_key)."""
    query = (
        db.query(Event.event_date, Location.external_key, Platform.code)
        .select_from(VolunteerResult)
        .join(Event, VolunteerResult.event_id == Event.id)
        .join(Location, Event.location_id == Location.id)
        .join(Platform, Event.platform_id == Platform.id)
        .join(PlatformLink, PlatformLink.participant_id == VolunteerResult.participant_id)
        .filter(
            PlatformLink.user_id == user_id,
            PlatformLink.platform_id == Platform.id,
            Event.is_test_event.is_(False),
            Event.event_date > _EPOCH_GUARD,
        )
    )
    by_platform: dict[str, list[tuple[date, str]]] = {}
    for event_date, location_key, platform_code in query.all():
        by_platform.setdefault(platform_code, []).append((event_date, location_key or "unknown"))
    return by_platform


def _count_volunteering(vol_rows: dict[str, list[tuple[date, str]]]) -> int:
    return sum(count_volunteering_for_platform(code, rows) for code, rows in vol_rows.items())


def _parkrun_volunteer_total(db: Session, user_id: UUID) -> int:
    """parkrun не публикует даты волонтёрств — только суммарный счётчик
    (profile_extra/роли), поэтому он не попадает в _collect_volunteer_rows
    (там события лежат на epoch-дате и отсекаются _EPOCH_GUARD). Здесь берём
    готовый общий счётчик, которым уже пользуется дашборд."""
    from app.parkrun.volunteer_credits import count_parkrun_volunteering

    link = (
        db.query(PlatformLink)
        .join(Platform, Platform.id == PlatformLink.platform_id)
        .filter(PlatformLink.user_id == user_id, Platform.code == "parkrun")
        .first()
    )
    if link is None or link.participant_id is None:
        return 0
    participant = db.query(Participant).filter(Participant.id == link.participant_id).one_or_none()
    if participant is None:
        return 0
    return count_parkrun_volunteering(db, participant, link.platform_id)


# ---------------------------------------------------------------------------
# Челленджи


def _plural_ru(count: int, forms: tuple[str, str, str]) -> str:
    tail = count % 100
    if 11 <= tail <= 14:
        return forms[2]
    last = count % 10
    if last == 1:
        return forms[0]
    if last in (2, 3, 4):
        return forms[1]
    return forms[2]


def _resolve_level(current: int, levels: dict[str, int]) -> tuple[str | None, str | None, int | None]:
    """Достигнутый уровень + следующий уровень и сколько до него осталось."""
    achieved: str | None = None
    for level in LEVEL_ORDER:
        if current >= levels[level]:
            achieved = level
    for level in LEVEL_ORDER:
        if current < levels[level]:
            return achieved, level, levels[level] - current
    return achieved, None, None


def _level_dates(sorted_dates: list[date], levels: dict[str, int]) -> dict[str, str | None]:
    """Дата достижения каждого уровня: sorted_dates — дата события, добавившего
    +1 к счётчику, по возрастанию. k-я по счёту дата — момент, когда счётчик
    впервые достиг k, что и есть дата уровня с порогом k."""
    result: dict[str, str | None] = {}
    for level in LEVEL_ORDER:
        threshold = levels[level]
        result[level] = sorted_dates[threshold - 1].isoformat() if 1 <= threshold <= len(sorted_dates) else None
    return result


def _level_dates_optional(ordered_dates: list[date | None], levels: dict[str, int]) -> dict[str, str | None]:
    """Как _level_dates, но часть шагов счётчика может быть без даты.

    Нужно там, где в зачёт идёт сводка parkrun: волонтёрства есть, дат у них нет.
    Недатированные шаги стоят в начале списка (parkrun-эпоха раньше 5 вёрст),
    и уровень, взятый на них, честно остаётся без даты — вместо того чтобы
    приписать ему дату первой российской субботы.
    """
    result: dict[str, str | None] = {}
    for level in LEVEL_ORDER:
        threshold = levels[level]
        if 1 <= threshold <= len(ordered_dates):
            reached = ordered_dates[threshold - 1]
            result[level] = reached.isoformat() if reached is not None else None
        else:
            result[level] = None
    return result


def _threshold_dates(sorted_dates: list[date], thresholds: tuple[int, ...]) -> dict[str, str | None]:
    """Как _level_dates, но для произвольного списка порогов (клубы)."""
    return {
        str(threshold): sorted_dates[threshold - 1].isoformat() if threshold <= len(sorted_dates) else None
        for threshold in thresholds
    }


def _tier_payload(
    tier_key: str,
    thresholds: tuple[int, int, int],
    current: int,
    *,
    level_dates_fn: Callable[[dict[str, int]], dict[str, str | None]],
    to_next_label_fn: Callable[[dict[str, int], str], str | None] | None,
) -> dict[str, object]:
    levels = {"bronze": thresholds[0], "silver": thresholds[1], "gold": thresholds[2]}
    level, next_level, to_next = _resolve_level(current, levels)
    gold = levels["gold"]
    to_next_label = to_next_label_fn(levels, next_level) if (to_next_label_fn and next_level) else None
    return {
        "tier": tier_key,
        "label": TIER_LABELS.get(tier_key),
        "levels": levels,
        "target": gold,
        "level": level,
        "next_level": next_level,
        "to_next_level": to_next,
        "to_next_label": to_next_label,
        "pct": round(min(current / gold, 1.0) * 100, 1) if gold else 0.0,
        "level_dates": level_dates_fn(levels),
    }


def _challenge(
    *,
    code: str,
    title: str,
    icon: str,
    description: str,
    category: str,
    current: int,
    level_dates_fn: Callable[[dict[str, int]], dict[str, str | None]],
    unit: str | None = None,
    detail: dict[str, object] | None = None,
    to_next_label_fn: Callable[[dict[str, int], str], str | None] | None = None,
    tier_thresholds: dict[str, tuple[int, int, int]] | None = None,
) -> dict[str, object]:
    # tier_thresholds — пороги, посчитанные на лету вместо реестра: сегодня так
    # делает только «Алфавит», у которого размер коллекции зависит от фильтра
    # систем (см. _alphabet_tiers).
    thresholds_by_tier = tier_thresholds or CHALLENGE_TIERS[code]
    tiers = [
        _tier_payload(tier_key, thresholds, current, level_dates_fn=level_dates_fn, to_next_label_fn=to_next_label_fn)
        for tier_key, thresholds in thresholds_by_tier.items()
    ]
    # "Лучшее" достижение — самый сложный тир, где взят хоть один уровень.
    # Пороги тиров заданы монотонно растущими (bronze medium > gold easy), так
    # что взятие любого уровня в более сложном тире гарантированно означает
    # золото во всех более лёгких — простой проход по порядку с перезаписью
    # даёт тот же результат, что явный поиск "с конца".
    best_tier: str | None = None
    best_level: str | None = None
    for tier in tiers:
        if tier["level"] is not None:
            best_tier, best_level = str(tier["tier"]), str(tier["level"])
    default_tier = next((str(t["tier"]) for t in tiers if t["level"] != "gold"), str(tiers[-1]["tier"]))
    return {
        "code": code,
        "title": title,
        "icon": icon,
        "description": description,
        "category": category,
        "current": current,
        "unit": unit,
        "detail": detail or {},
        "tiers": tiers,
        "best_tier": best_tier,
        "best_level": best_level,
        "default_tier": default_tier,
        # Насколько последняя пробежка продвинула счётчик — проставляется
        # снаружи (compute_challenges), по умолчанию 0.
        "recent_delta": 0,
        # Дата того самого последнего дня активности: по ней «Детали» подсвечивают
        # клетки, закрытые этой пробежкой (иначе «↑ +1» на карточке есть, а какая
        # именно клетка новая — приходится помнить самому).
        "recent_date": None,
    }


def _mmss_or_none(finish_time_sec: int | None) -> tuple[int, int] | None:
    """(минуты, секунды) для времён до часа — челленджи совпадений считаем по MM:SS."""
    if finish_time_sec is None or finish_time_sec <= 0 or finish_time_sec >= 3600:
        return None
    return finish_time_sec // 60, finish_time_sec % 60


def _time_display(finish_time_sec: int) -> str:
    """Короткий формат MM:SS для времён до часа (24:31, а не 00:24:31)."""
    if finish_time_sec >= 3600:
        return normalize_finish_time_display(finish_time_sec, None) or ""
    return f"{finish_time_sec // 60}:{finish_time_sec % 60:02d}"


def _first_letter(name: str) -> str | None:
    for char in name.strip().upper():
        if char.isalpha():
            return "Е" if char == "Ё" else char
        if char.isdigit():
            return None
    return None


def _cell(
    label: str,
    row: RunRow | WeatherDay | None,
    *,
    hint: str | None = None,
    count: int | None = None,
    accent: str | None = None,
    count_label: str | None = None,
) -> dict[str, object]:
    """Клетка коллекции: закрыта первой пробежкой row; hint — подсказка для
    незакрытых. accent помечает клетку как часть выделенной группы — сегодня
    так «Индекс Уилсона» показывает обе свои цепочки на одной ленте номеров."""
    return {
        "label": label,
        "done": row is not None,
        "date": row.event_date.isoformat() if row else None,
        "location": row.location_name if row else None,
        "hint": hint,
        "platform_code": row.platform_code if row else None,
        "count": count,
        "count_label": count_label,
        "accent": accent,
    }


def _seconds_challenge(rows: list[RunRow]) -> dict[str, object]:
    first_by_second: dict[int, RunRow] = {}
    count_by_second: dict[int, int] = {}
    for row in rows:
        mmss = _mmss_or_none(row.finish_time_sec)
        if mmss is None:
            continue
        second = mmss[1]
        first_by_second.setdefault(second, row)
        count_by_second[second] = count_by_second.get(second, 0) + 1
    cells = [
        _cell(f":{second:02d}", first_by_second.get(second), count=count_by_second.get(second)) for second in range(60)
    ]
    sorted_dates = sorted(row.event_date for row in first_by_second.values())
    return _challenge(
        code="seconds",
        title="60 секунд",
        icon="⏱️",
        description="Финишируй с каждой секундой на часах — от :00 до :59.",
        category="collection",
        current=len(first_by_second),
        unit="секунд",
        detail={"cells": cells},
        level_dates_fn=lambda levels: _level_dates(sorted_dates, levels),
    )


def _weekdays_challenge(rows: list[RunRow]) -> dict[str, object]:
    labels = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]
    first_by_weekday: dict[int, RunRow] = {}
    for row in rows:
        first_by_weekday.setdefault(row.event_date.weekday(), row)
    cells = [_cell(labels[index], first_by_weekday.get(index)) for index in range(7)]
    sorted_dates = sorted(row.event_date for row in first_by_weekday.values())
    return _challenge(
        code="weekdays",
        title="Семь дней",
        icon="📅",
        description="Пробеги парковый старт в каждый день недели — не субботой единой.",
        category="collection",
        current=len(first_by_weekday),
        unit="дней недели",
        detail={"cells": cells},
        level_dates_fn=lambda levels: _level_dates(sorted_dates, levels),
    )


def _positions_challenge(rows: list[RunRow]) -> dict[str, object]:
    first_by_position: dict[int, RunRow] = {}
    for row in rows:
        if row.position is not None and row.position > 0:
            first_by_position.setdefault(row.position % 100, row)
    cells = [_cell(f"{position:02d}", first_by_position.get(position)) for position in range(100)]
    sorted_dates = sorted(row.event_date for row in first_by_position.values())
    return _challenge(
        code="positions",
        title="Бинго позиций",
        icon="🎯",
        description="Финишируй на местах со всеми окончаниями от 00 до 99. Клетку определяют две последние цифры места: 18-е, 118-е и 218-е место закрывают одну и ту же клетку «18».",
        category="collection",
        current=len(first_by_position),
        unit="позиций",
        detail={"cells": cells},
        level_dates_fn=lambda levels: _level_dates(sorted_dates, levels),
    )


def _alphabet_available_names(db: Session, platform_code: str | None) -> dict[str, set[str]]:
    """Буква -> названия локаций, которыми её можно закрыть.

    Каталог букв зависит от выбранной системы: в 5 вёрстах нет ни одной
    локации на «Ц», в S95 нет «Д» — показывать в фильтре по системе буквы,
    которые в ней физически не закрыть, значит врать о цели.
    """
    query = (
        db.query(Location.name).join(Platform, Location.platform_id == Platform.id).filter(Location.name.isnot(None))
    )
    if platform_code is None:
        # Сквозной вид: parkrun не считается (см. _alphabet_challenge), значит
        # и буквы его локаций в каталог не попадают.
        query = query.filter(Platform.code != MAP_HISTORIC_PLATFORM)
    else:
        query = query.filter(Platform.code == platform_code)
    available_names: dict[str, set[str]] = {}
    for (name,) in query.all():
        letter = _first_letter(name or "")
        if letter is not None and letter in _RU_ALPHABET:
            available_names.setdefault(letter, set()).add((name or "").strip())
    return available_names


def _alphabet_challenge(
    rows: list[RunRow],
    available_names: dict[str, set[str]],
    *,
    platform_code: str | None = None,
) -> dict[str, object]:
    """Parkrun-локации не считаются: у parkrun имена латиницей/местные, они
    искажали бы букву старта — challenge остаётся про русский алфавит.
    Исключение — фильтр по самому parkrun: там весь скоуп и есть parkrun, и
    буквы берутся с его же русскоязычных локаций.
    """
    skip_parkrun = platform_code is None
    first_by_letter: dict[str, RunRow] = {}
    for row in rows:
        if skip_parkrun and row.platform_code == MAP_HISTORIC_PLATFORM:
            continue
        letter = _first_letter(row.location_name)
        if letter is not None and letter in available_names:
            first_by_letter.setdefault(letter, row)
    letters: list[dict[str, object]] = []
    for letter in _RU_ALPHABET:
        if letter not in available_names:
            continue
        first_row = first_by_letter.get(letter)
        names = sorted(available_names[letter])
        letters.append(
            {
                "letter": letter,
                "done": first_row is not None,
                "date": first_row.event_date.isoformat() if first_row else None,
                "location": first_row.location_name if first_row else None,
                "locations": names[:8],
                "locations_more": max(len(names) - 8, 0),
                "platform_code": first_row.platform_code if first_row else None,
            }
        )
    sorted_dates = sorted(row.event_date for row in first_by_letter.values())
    return _challenge(
        code="alphabet",
        title="Алфавит",
        icon="🔤",
        description=_alphabet_description(len(letters), platform_code),
        category="collection",
        current=len(first_by_letter),
        unit="букв",
        detail={"letters": letters, "available": len(letters)},
        level_dates_fn=lambda levels: _level_dates(sorted_dates, levels),
        tier_thresholds=_alphabet_tiers(len(letters)),
    )


def _alphabet_tiers(available: int) -> dict[str, tuple[int, int, int]]:
    """Пороги «Алфавита» под размер каталога букв.

    Пороги в CHALLENGE_TIERS откалиброваны на полный каталог (28 букв). Под
    фильтром систем букв меньше — у 5 вёрст их 26, у S95 17 — и прежняя шкала
    обещала недостижимое: золото за 28 букв там нельзя взять физически.
    Поэтому шкала линейно сжимается, а золото сложного тира приравнивается к
    числу доступных букв: собрал весь алфавит своей системы — взял золото.

    Порядок порогов остаётся строго возрастающим сквозь все тиры — на этом
    держится выбор «лучшего» тира в _challenge(). Если букв меньше, чем самих
    порогов, три тира в такой каталог не укладываются, и остаётся один тир
    ("solo", как у «Семи дней» — фронт тогда не рисует вкладки сложности).
    """
    reference = CHALLENGE_TIERS["alphabet"]
    full = reference["hard"][2]
    if available >= full:
        return reference

    flat = [value for thresholds in reference.values() for value in thresholds]
    if available >= len(flat):
        scaled: list[int] = []
        previous = 0
        for value in flat:
            scaled.append(max(1, round(value * available / full), previous + 1))
            previous = scaled[-1]
        # Золото сложного тира — ровно весь доступный алфавит, без округлений.
        scaled[-1] = available
        return {
            tier_key: (scaled[index * 3], scaled[index * 3 + 1], scaled[index * 3 + 2])
            for index, tier_key in enumerate(reference)
        }

    bronze = max(1, -(-available // 3))
    silver = max(bronze, -(-available * 2 // 3))
    return {"solo": (bronze, silver, max(silver, available))}


def _alphabet_description(available: int, platform_code: str | None) -> str:
    """Описание называет размер каталога и цену золота: под фильтром систем
    и букв меньше, и пороги другие (см. _alphabet_tiers)."""
    if platform_code is None:
        return (
            "Финишируй в локациях на каждую букву алфавита (считаются буквы, на которые есть "
            f"хотя бы одна локация — сейчас их {available}; parkrun в этом челлендже не учитывается). "
            f"Золото сложного уровня — все {available}."
        )
    title = PLATFORM_TITLES.get(platform_code, platform_code)
    if available == 0:
        return (
            f"Финишируй в локациях на каждую букву алфавита. У системы «{title}» нет локаций "
            "с русскими названиями — закрывать нечего, снимите фильтр систем."
        )
    letters = f"{available} {_plural_ru(available, ('букву', 'буквы', 'букв'))}"
    return (
        f"Финишируй в локациях на каждую букву алфавита. Выбрана система «{title}» — считаются "
        f"только её локации, а они дают {letters}. Пороги уровней подстроены под этот каталог: "
        f"золото даётся за все {available}."
    )


def _calendar_days_challenge(rows: list[RunRow]) -> dict[str, object]:
    first_by_day: dict[str, RunRow] = {}
    for row in rows:
        first_by_day.setdefault(f"{row.event_date.month:02d}-{row.event_date.day:02d}", row)
    days = [
        {
            "key": key,
            "date": row.event_date.isoformat(),
            "location": row.location_name,
            "platform_code": row.platform_code,
        }
        for key, row in sorted(first_by_day.items())
    ]
    # hard-золото = 366 (весь календарь, включая 29 февраля) — больше дат физически не бывает.
    sorted_dates = sorted(row.event_date for row in first_by_day.values())
    return _challenge(
        code="calendar_days",
        title="Круглый год",
        icon="🗓️",
        description="Закрой каждую дату календаря — все 366 дней в году (год не важен).",
        category="collection",
        current=len(first_by_day),
        unit="дат",
        detail={"days": days},
        level_dates_fn=lambda levels: _level_dates(sorted_dates, levels),
    )


@dataclass(frozen=True)
class PredictedStart:
    """Один предсказанный старт: локация, дата, порядковый номер в своей системе."""

    platform_code: str
    number: int
    event_date: date
    location_name: str
    location_slug: str
    # Индекс недельного окна от сегодня: 0 — ближайшая неделя, 1 — W+1 и т.д.
    week_index: int


def _predict_upcoming_starts(
    db: Session,
    *,
    today: date | None = None,
    weeks: int = 3,
    max_number: int = 400,
) -> list[PredictedStart]:
    """Прогноз ближайших стартов: последний известный старт каждой активной
    локации + 1..weeks недель вперёд. Локации, у которых последний старт давно
    (прогноз уже в прошлом), отпадают сами. Номер старта считается ВНУТРИ своей
    системы (five_verst/s95/parkrun/runpark) — каждая платформа нумерует события
    независимо.

    Отсекаем только зарубежный parkrun: из 2859 активных локаций 2437 — его
    мировой каталог, и подсказка «Скоро: Westpark ≈ 01.08» предлагала номер,
    который человек закрыть не может (у нас от такой площадки лежат лишь строки
    наших же участников из их профилей — см. russian_parkrun_location_ids).

    Действующие системы берём целиком, независимо от страны: у 5 вёрст, s95 и
    RunPark есть площадки в Беларуси, Сербии, Грузии и далее, и для тамошних
    участников это домашние старты. Раньше фильтр шёл по стране, и вместе с
    мировым parkrun выкидывал Гомель с Нови-Садом.

    Окно недели считаем от сегодня (`week_index`), а не по счётчику цикла: у
    локаций разные даты последнего старта, и «+1 неделя» для отставшей локации
    попадает в то же календарное окно, что «+2 недели» для идущей вровень.
    """
    today = today or date.today()
    latest = (
        db.query(
            Event.location_id.label("location_id"),
            func.max(Event.event_date).label("last_date"),
        )
        .filter(
            Event.is_test_event.is_(False),
            Event.event_number.isnot(None),
            Event.event_date > _EPOCH_GUARD,
        )
        .group_by(Event.location_id)
        .subquery()
    )
    query = (
        db.query(Event.event_number, Event.event_date, Location, Platform.code)
        .join(
            latest,
            (Event.location_id == latest.c.location_id) & (Event.event_date == latest.c.last_date),
        )
        .join(Location, Event.location_id == Location.id)
        .join(Platform, Event.platform_id == Platform.id)
        .filter(
            Event.event_number.isnot(None),
            Location.is_cancelled.is_(False),
            Location.is_paused.is_(False),
        )
    )
    catalog_index = LocationCatalogIndex(db)
    russian_parkrun = russian_parkrun_location_ids(db, catalog_index)
    predictions: list[PredictedStart] = []
    for event_number, event_date, location, platform_code in query.all():
        if platform_code == MAP_HISTORIC_PLATFORM and location.id not in russian_parkrun:
            continue
        display_name = catalog_index.display_name(location, platform_code)
        for week in range(1, weeks + 1):
            predicted_number = event_number + week
            predicted_date = event_date + timedelta(weeks=week)
            if predicted_date < today:
                continue
            week_index = (predicted_date - today).days // 7
            if week_index >= weeks:
                continue
            if predicted_number > max_number:
                break
            predictions.append(
                PredictedStart(
                    platform_code=platform_code,
                    number=predicted_number,
                    event_date=predicted_date,
                    location_name=display_name,
                    location_slug=location.external_key.strip().lower(),
                    week_index=week_index,
                )
            )
    return predictions


def _numbers_from_predictions(
    predictions: list[PredictedStart], *, weeks: int, max_number: int
) -> dict[tuple[str, int], list[tuple[date, str]]]:
    """Прогноз для подсказок в ячейках: (платформа, номер) → отсортированные
    пары (дата, локация), суженный до weeks недель и номеров до max_number."""
    upcoming: dict[tuple[str, int], list[tuple[date, str]]] = {}
    for item in predictions:
        if item.week_index >= weeks or item.number > max_number:
            continue
        upcoming.setdefault((item.platform_code, item.number), []).append((item.event_date, item.location_name))
    for entries in upcoming.values():
        entries.sort()
    return upcoming


def _upcoming_hint(entries: list[tuple[date, str]] | None) -> str | None:
    if not entries:
        return None
    parts = [f"{name} ≈ {when.day:02d}.{when.month:02d}" for when, name in entries[:3]]
    more = len(entries) - 3
    suffix = f" и ещё {more}" if more > 0 else ""
    return "Скоро: " + ", ".join(parts) + suffix


def _planned_hint(entries: list[tuple[date, str]] | None) -> str | None:
    """Подсказка на длинном горизонте — с годом: полгода вперёд «14.02» без года
    уже двусмысленно."""
    if not entries:
        return None
    parts = [f"{name} ≈ {when.strftime('%d.%m.%y')}" for when, name in entries[:3]]
    more = len(entries) - 3
    suffix = f" и ещё {more}" if more > 0 else ""
    return "Где взять: " + ", ".join(parts) + suffix


def _planned_by_number(
    planned: dict[tuple[str, int], list[tuple[date, str]]], numbers: Iterable[int]
) -> dict[int, list[tuple[date, str]]]:
    """Прогноз, схлопнутый по системам: номер закрывается стартом в ЛЮБОЙ
    системе, поэтому «№222 в Битце» и «№222 в Сормовском» стоят в одном списке."""
    wanted = set(numbers)
    by_number: dict[int, list[tuple[date, str]]] = {}
    for (_platform_code, number), entries in planned.items():
        if number in wanted:
            by_number.setdefault(number, []).extend(entries)
    for entries in by_number.values():
        entries.sort()
    return by_number


def _start_numbers_range_challenge(
    rows: list[RunRow],
    upcoming: dict[tuple[str, int], list[tuple[date, str]]],
    *,
    code: str,
    title: str,
    description: str,
    low: int,
    high: int,
) -> dict[str, object]:
    """Номер старта считается ВНУТРИ одной системы (каждая платформа нумерует
    события независимо — см. _predict_upcoming_starts), но само число в
    диапазоне засчитывается в общий счётчик, если получено В ЛЮБОЙ системе:
    старт №4 на s95 закрывает клетку "4" точно так же, как старт №4 на
    five_verst — платформы здесь не соревнуются друг с другом, просто у
    каждой цифры своя (первая по дате) система-источник, которая видна в
    подсказке ячейки."""
    first_by_number: dict[int, RunRow] = {}
    for row in rows:
        if row.event_number is not None and low <= row.event_number <= high:
            first_by_number.setdefault(row.event_number, row)
    upcoming_by_number: dict[int, list[tuple[date, str]]] = {}
    for (_platform_code, number), entries in upcoming.items():
        if low <= number <= high:
            upcoming_by_number.setdefault(number, []).extend(entries)
    for entries in upcoming_by_number.values():
        entries.sort()
    cells = [
        _cell(
            str(number),
            first_by_number.get(number),
            hint=_upcoming_hint(upcoming_by_number.get(number)),
        )
        for number in range(low, high + 1)
    ]
    sorted_dates = sorted(row.event_date for row in first_by_number.values())
    return _challenge(
        code=code,
        title=title,
        icon="🔢",
        description=description,
        category="collection",
        current=len(first_by_number),
        unit="номеров",
        detail={"cells": cells},
        level_dates_fn=lambda levels: _level_dates(sorted_dates, levels),
    )


def _palindrome_challenge(rows: list[RunRow]) -> dict[str, object]:
    items: list[dict[str, object]] = []
    seen: set[int] = set()
    for row in rows:
        mmss = _mmss_or_none(row.finish_time_sec)
        if mmss is None or row.finish_time_sec in seen:
            continue
        minutes, seconds = mmss
        if f"{minutes:02d}" == f"{seconds:02d}"[::-1]:
            seen.add(row.finish_time_sec)  # type: ignore[arg-type]
            items.append(
                {
                    "date": row.event_date.isoformat(),
                    "value": _time_display(row.finish_time_sec),  # type: ignore[arg-type]
                    "location": row.location_name,
                }
            )
    sorted_dates = sorted(date.fromisoformat(str(item["date"])) for item in items)
    return _challenge(
        code="palindrome",
        title="Палиндром",
        icon="🪞",
        description="Финишируй со временем-зеркалом: минуты читаются как секунды наоборот — 23:32, 21:12, 30:03.",
        category="coincidence",
        current=len(items),
        unit="палиндромов",
        detail={"items": items},
        level_dates_fn=lambda levels: _level_dates(sorted_dates, levels),
    )


def _deja_vu_challenge(rows: list[RunRow]) -> dict[str, object]:
    rows_by_time: dict[int, list[RunRow]] = {}
    for row in rows:
        if row.finish_time_sec is not None and row.finish_time_sec > 0:
            rows_by_time.setdefault(row.finish_time_sec, []).append(row)
    repeated = sorted(time_sec for time_sec, entries in rows_by_time.items() if len(entries) >= 2)
    items = [
        {
            "value": _time_display(time_sec),
            "count": len(rows_by_time[time_sec]),
            "occurrences": [
                {"date": entry.event_date.isoformat(), "location": entry.location_name}
                for entry in rows_by_time[time_sec]
            ],
        }
        for time_sec in repeated
    ]
    # Дежавю "случается" в момент ВТОРОГО финиша с этим временем — эта дата и
    # закрывает соответствующую клетку счётчика совпадений.
    sorted_dates = sorted(rows_by_time[time_sec][1].event_date for time_sec in repeated)
    return _challenge(
        code="deja_vu",
        title="Дежавю",
        icon="👯",
        description="Финишируй с одним и тем же временем — секунда в секунду — на разных пробежках.",
        category="coincidence",
        current=len(repeated),
        unit="совпадений",
        detail={"items": items},
        level_dates_fn=lambda levels: _level_dates(sorted_dates, levels),
    )


def _number_match_challenge(rows: list[RunRow]) -> dict[str, object]:
    items: list[dict[str, object]] = []
    for index, row in enumerate(rows, start=1):
        if row.event_number is not None and row.event_number == index:
            items.append(
                {
                    "date": row.event_date.isoformat(),
                    "value": f"№{row.event_number}",
                    "location": row.location_name,
                    # Система нужна в строке: номер старта у каждой системы
                    # свой, и на мультисистемной площадке «№30» без плашки не
                    # говорит, чей это номер (просьба Дмитрия 17.09.2026).
                    "platform_code": row.platform_code,
                }
            )
    detail: dict[str, object] = {"items": items}
    if not items:
        # Пример, чтобы было видно, как записывается совпадение
        next_index = len(rows) + 1
        top_location = Counter(row.location_name for row in rows).most_common(1)
        detail["example"] = {
            "value": f"№{next_index}",
            "location": top_location[0][0] if top_location else "Кузьминки",
            "note": f"так запишется совпадение — твоя {next_index}-я пробежка на старте №{next_index}",
        }
    sorted_dates = [date.fromisoformat(str(item["date"])) for item in items]
    return _challenge(
        code="number_match",
        title="Совпадение номеров",
        icon="🔗",
        description="Порядковый номер старта совпал с номером твоей пробежки: твоя 30-я — и старт №30.",
        category="coincidence",
        current=len(items),
        unit="совпадений",
        detail=detail,
        level_dates_fn=lambda levels: _level_dates(sorted_dates, levels),
    )


def _jubilee_challenge(rows: list[RunRow]) -> dict[str, object]:
    items: list[dict[str, object]] = []
    for row in rows:
        if row.event_number is not None and row.event_number > 0 and row.event_number % 50 == 0:
            items.append(
                {
                    "date": row.event_date.isoformat(),
                    "value": f"№{row.event_number}",
                    "location": row.location_name,
                    "platform_code": row.platform_code,
                }
            )
    sorted_dates = [date.fromisoformat(str(item["date"])) for item in items]
    return _challenge(
        code="jubilee",
        title="Юбилейщик",
        icon="🎂",
        description="Участвуй в юбилейных стартах локаций — событиях с круглыми номерами №50, №100, №150…",
        category="coincidence",
        current=len(items),
        unit="юбилеев",
        detail={"items": items},
        level_dates_fn=lambda levels: _level_dates(sorted_dates, levels),
    )


# ---------------------------------------------------------------------------
# Ч48/Ч51/Ч23/Ч26/Ч27/Ч28/Ч29 — челленджи из бэклога сообщества (10.09.2026)


def _finished_rows(rows: list[RunRow]) -> list[RunRow]:
    """Только финиши: у части строк время пустое (DNF, снятые протоколы,
    волонтёрский зачёт в чужой системе). «Хет-трик систем» и «Международный
    турист» — про финиш, а не про факт присутствия на старте."""
    return [row for row in rows if row.finish_time_sec is not None and row.finish_time_sec > 0]


def _platform_slam_challenge(rows: list[RunRow]) -> dict[str, object]:
    """Ч48. Финиш во всех четырёх системах — механика, которой нет ни у одного
    западного аналога: Running Challenges живёт внутри одного parkrun, а у нас
    четыре системы в одном профиле."""
    finished = _finished_rows(rows)
    first_by_platform: dict[str, RunRow] = {}
    counts: Counter[str] = Counter()
    for row in finished:
        first_by_platform.setdefault(row.platform_code, row)
        counts[row.platform_code] += 1
    cells = [
        _cell(
            PLATFORM_TITLES.get(code, code),
            first_by_platform.get(code),
            count=counts.get(code),
            hint="Финиша в этой системе ещё нет" if code not in first_by_platform else None,
        )
        for code in PLATFORM_SLAM_ORDER
    ]
    sorted_dates = sorted(row.event_date for row in first_by_platform.values())
    return _challenge(
        code="platform_slam",
        title="Хет-трик систем",
        icon="🎰",
        description="Финишируй в каждой из четырёх систем: parkrun, 5 вёрст, S95, RunPark.",
        category="collection",
        current=len(first_by_platform),
        unit="систем",
        detail={"cells": cells},
        level_dates_fn=lambda levels: _level_dates(sorted_dates, levels),
    )


def _countries_challenge(rows: list[RunRow], home_country: str | None) -> dict[str, object]:
    """Ч51. Страны, где человек финишировал.

    Домашней считается страна ДОМАШНЕЙ ЛОКАЦИИ (той же, от которой считается
    «дальность от дома»), а не Россия по умолчанию: у нас есть площадки в
    Сербии, Беларуси, Аргентине и Грузии, и для бегуна из Белграда
    международным стартом является как раз российский.
    """
    finished = _finished_rows(rows)
    first_by_country: dict[str, RunRow] = {}
    counts: Counter[str] = Counter()
    unknown = 0
    for row in finished:
        if row.country is None:
            unknown += 1
            continue
        first_by_country.setdefault(row.country, row)
        counts[row.country] += 1

    def _order(country: str) -> tuple[int, int, str]:
        # Дом первым, дальше по числу финишей — так виден масштаб выезда.
        return (0 if country == home_country else 1, -counts[country], country)

    items: list[dict[str, object]] = [
        {
            "value": f"🏠 {country}" if country == home_country else country,
            "count": counts[country],
            "location": first_by_country[country].location_name,
            "date": first_by_country[country].event_date.isoformat(),
        }
        for country in sorted(first_by_country, key=_order)
    ]
    if unknown:
        items.append(
            {
                "value": "Страна площадки неизвестна",
                "count": unknown,
                "location": "в каталоге parkrun у неё не заполнена страна",
            }
        )
    # Дата страны — первый финиш в ней; счётчик растёт именно в этот день.
    sorted_dates = sorted(row.event_date for row in first_by_country.values())
    abroad = sum(1 for country in first_by_country if country != home_country)
    if home_country is None:
        note = "Домашняя страна пока не определена — нужен хотя бы один финиш."
    elif abroad == 0:
        note = f"Дом — {home_country}. Заграничных стартов пока нет."
    else:
        note = f"Дом — {home_country}; {abroad} {_plural_ru(abroad, ('страна', 'страны', 'стран'))} за его пределами."
    return _challenge(
        code="countries",
        title="Международный турист",
        icon="🌍",
        description=(
            "Финишируй в разных странах. Домашней считается страна твоей домашней локации — "
            "от неё и отсчитывается заграница: для бегуна из Белграда международный старт "
            "это как раз российский."
        ),
        category="scale",
        current=len(first_by_country),
        unit="стран",
        detail={"items": items, "note": note},
        level_dates_fn=lambda levels: _level_dates(sorted_dates, levels),
    )


def _minute_bucket(finish_time_sec: int | None) -> int | None:
    """Минутная корзина финиша: 24:31 → 24. Вне разумных границ — None."""
    if finish_time_sec is None or finish_time_sec <= 0:
        return None
    minutes = finish_time_sec // 60
    if minutes < MINUTE_BUCKET_MIN or minutes > MINUTE_BUCKET_MAX:
        return None
    return minutes


def _minute_range_challenge(rows: list[RunRow]) -> dict[str, object]:
    """Ч23. Коллекция разных минут на финишных часах: 21:xx, 22:xx, 23:xx…

    Считаем ЗАКРЫТЫЕ корзины, а не размах между самой быстрой и самой
    медленной (правка Дмитрия 14.09.2026): при размахе две пробежки, 21:xx и
    60:xx, давали сразу сорок корзин и закрывали челлендж целиком, хотя между
    ними пусто. Теперь каждая новая минута — ровно +1.

    Лента рисуется от своей быстрой минуты до своей медленной: пустые клетки
    внутри — это и есть то, что осталось собрать, а недостижимых клеток из
    чужой сетки здесь по-прежнему не бывает.
    """
    first_by_bucket: dict[int, RunRow] = {}
    counts: Counter[int] = Counter()
    for row in rows:
        bucket = _minute_bucket(row.finish_time_sec)
        if bucket is None:
            continue
        first_by_bucket.setdefault(bucket, row)
        counts[bucket] += 1
    sorted_dates = sorted(row.event_date for row in first_by_bucket.values())
    if not first_by_bucket:
        return _challenge(
            code="minute_range",
            title="Коллекция минут",
            icon="🪗",
            description=_MINUTE_RANGE_DESCRIPTION,
            category="collection",
            current=0,
            unit="минутных корзин",
            detail={"cells": [], "note": "Первый же финиш откроет первую корзину."},
            level_dates_fn=lambda levels: _level_dates(sorted_dates, levels),
        )
    lowest = min(first_by_bucket)
    highest = max(first_by_bucket)
    cells = [
        _cell(
            f"{bucket}",
            first_by_bucket.get(bucket),
            count=counts.get(bucket),
            hint=None if bucket in first_by_bucket else f"Финиша в {bucket}:xx ещё не было",
        )
        for bucket in range(lowest, highest + 1)
    ]
    note = (
        f"Закрыто {len(first_by_bucket)} "
        f"{_plural_ru(len(first_by_bucket), ('корзина', 'корзины', 'корзин'))} "
        f"в диапазоне {lowest}:xx — {highest}:xx. "
        "Новая появится от финиша в минуту, которой ещё не было."
    )
    return _challenge(
        code="minute_range",
        title="Коллекция минут",
        icon="🪗",
        description=_MINUTE_RANGE_DESCRIPTION,
        category="collection",
        current=len(first_by_bucket),
        unit="минутных корзин",
        detail={"cells": cells, "note": note},
        level_dates_fn=lambda levels: _level_dates(sorted_dates, levels),
    )


# Две строки максимум (правка Дмитрия 11.09.2026): подробности человек и так
# видит на ленте клеток, объяснять их словами в шапке карточки незачем.
_MINUTE_RANGE_DESCRIPTION = (
    "Собери разные минуты на финишных часах: 21:xx, 22:xx, 23:xx… Каждая новая минута — плюс одна корзина."
)


def _wilson_chains(numbers: set[int]) -> tuple[int, int, int]:
    """(классический Wilson, плавающий Wilson, начало плавающей цепочки).

    Классический считает цепочку строго с №1 — отсюда охота за инаугурациями;
    плавающий берёт самую длинную цепочку подряд идущих номеров с любого числа.
    """
    classic = 0
    while classic + 1 in numbers:
        classic += 1
    best_len = 0
    best_start = 0
    for number in sorted(numbers):
        if number - 1 in numbers:
            continue
        length = 0
        while number + length in numbers:
            length += 1
        if length > best_len:
            best_len, best_start = length, number
    return classic, best_len, best_start


def _wilson_level_dates(rows: list[RunRow], levels: dict[str, int]) -> dict[str, str | None]:
    numbers: set[int] = set()
    classic = 0
    achieved: dict[str, date | None] = {level: None for level in LEVEL_ORDER}
    for row in rows:
        if row.event_number is None or row.event_number <= 0:
            continue
        numbers.add(row.event_number)
        while classic + 1 in numbers:
            classic += 1
        for level in LEVEL_ORDER:
            if achieved[level] is None and classic >= levels[level]:
                achieved[level] = row.event_date
    return {level: (value.isoformat() if value else None) for level, value in achieved.items()}


def _wilson_challenge(rows: list[RunRow], planned: dict[tuple[str, int], list[tuple[date, str]]]) -> dict[str, object]:
    """Ч26. Индекс Уилсона в обеих вариациях — считаем по номерам стартов в
    любой системе, как и «Нумератор»: номер №4 на S95 закрывает то же число,
    что №4 на 5 вёрстах.

    Обе цепочки показываем на ОДНОЙ ленте номеров (просьба Дмитрия 11.09.2026):
    классическая — это её начало от №1, плавающая — самый длинный сплошной
    кусок где угодно. Раздельные блоки заставляли бы сравнивать две сетки
    глазами, а тут видно сразу, где цепочка рвётся.
    """
    first_by_number: dict[int, RunRow] = {}
    for row in rows:
        if row.event_number is not None and row.event_number > 0:
            first_by_number.setdefault(row.event_number, row)
    numbers = set(first_by_number)
    classic, floating, floating_start = _wilson_chains(numbers)

    classic_range = range(1, classic + 1)
    floating_range = range(floating_start, floating_start + floating) if floating else range(0)
    # Лента охватывает начало (там живёт классическая цепочка) и обязательно
    # достаёт до конца плавающей — иначе вторая цифра подписи ссылалась бы на
    # номера, которых на ленте нет.
    high = min(
        max(WILSON_STRIP_NUMBERS, floating_start + floating + 4, classic + 5),
        PLANNING_MAX_NUMBER,
    )
    strip = list(range(1, high + 1))
    planned_by_number = _planned_by_number(planned, strip)
    cells = []
    for number in strip:
        if number in classic_range:
            accent = "classic"
        elif number in floating_range:
            accent = "floating"
        else:
            accent = None
        cells.append(
            _cell(
                str(number),
                first_by_number.get(number),
                hint=_planned_hint(planned_by_number.get(number)),
                accent=accent,
            )
        )

    note_parts = [
        f"Цепочка с начала — {classic}"
        + (f" (№1–№{classic}), следующий нужен №{classic + 1}." if classic else ": нужен старт №1."),
    ]
    if floating:
        note_parts.append(f"Самая длинная цепочка — {floating} (№{floating_start}–№{floating_start + floating - 1}).")
    note_parts.append("Уровни считаются по цепочке с начала; даты стартов приблизительные.")
    return _challenge(
        code="wilson",
        title="Индекс Уилсона",
        icon="⛓️",
        description=(
            "Собирай номера стартов подряд: №1, №2, №3… Индекс — длина непрерывной цепочки. "
            "Классический считается только от самого первого старта площадки, "
            "плавающий — от любого номера."
        ),
        category="scale",
        current=classic,
        unit="",
        detail={"cells": cells, "note": " ".join(note_parts)},
        level_dates_fn=lambda levels: _wilson_level_dates(rows, levels),
    )


def _nelson_challenge(rows: list[RunRow], planned: dict[tuple[str, int], list[tuple[date, str]]]) -> dict[str, object]:
    """Ч27. «Нельсоны» — номера, кратные 111.

    Решение Дмитрия 11.09.2026: планирование НУЖНО — карточка показывает, где и
    когда наступит следующий нельсон. Прежняя оговорка «считается по факту, без
    анонсов» (осторожность после просьбы parkrun HQ 2023 года) снята: у нас
    четыре системы и три сотни площадок, наплыв на одну конкретную субботу нам
    не грозит, а без подсказки челлендж выпадает случайным образом и им нельзя
    пользоваться.
    """
    first_by_number: dict[int, RunRow] = {}
    counts: Counter[int] = Counter()
    for row in rows:
        number = row.event_number
        if number is not None and number > 0 and number % NELSON_STEP == 0:
            first_by_number.setdefault(number, row)
            counts[number] += 1
    planned_by_number = _planned_by_number(planned, NELSON_NUMBERS)
    cells = [
        _cell(
            str(number),
            first_by_number.get(number),
            count=counts.get(number),
            hint=_planned_hint(planned_by_number.get(number)),
        )
        for number in NELSON_NUMBERS
    ]
    sorted_dates = sorted(row.event_date for row in first_by_number.values())
    return _challenge(
        code="nelson",
        title="Клуб Нельсона",
        icon="🏏",
        description=(
            "Старты с номерами, кратными 111: №111, №222, №333… "
            "«Нельсон» пришёл из крикета и стал одним из самых любимых суеверий parkrun."
        ),
        category="coincidence",
        current=len(first_by_number),
        unit="нельсонов",
        detail={"cells": cells},
        level_dates_fn=lambda levels: _level_dates(sorted_dates, levels),
    )


def _fibonacci_challenge(
    rows: list[RunRow], planned: dict[tuple[str, int], list[tuple[date, str]]]
) -> dict[str, object]:
    """Ч28. Коллекция из пятнадцати клеток ряда Фибоначчи."""
    first_by_number: dict[int, RunRow] = {}
    counts: Counter[int] = Counter()
    for row in rows:
        number = row.event_number
        if number is not None and number in FIBONACCI_NUMBERS:
            first_by_number.setdefault(number, row)
            counts[number] += 1
    planned_by_number = _planned_by_number(planned, FIBONACCI_NUMBERS)
    cells = [
        _cell(
            str(number),
            first_by_number.get(number),
            count=counts.get(number),
            hint=_planned_hint(planned_by_number.get(number)),
        )
        for number in FIBONACCI_NUMBERS
    ]
    sorted_dates = sorted(row.event_date for row in first_by_number.values())
    return _challenge(
        code="fibonacci",
        title="Числа Фибоначчи",
        icon="🐚",
        description=(
            "Финишируй на стартах с номерами из ряда Фибоначчи: 1, 2, 3, 5, 8, 13, 21, 34… "
            "Каждое следующее число — сумма двух предыдущих."
        ),
        category="collection",
        current=len(first_by_number),
        unit="чисел ряда",
        detail={"cells": cells},
        level_dates_fn=lambda levels: _level_dates(sorted_dates, levels),
    )


def _is_prime(number: int) -> bool:
    if number < 2:
        return False
    if number % 2 == 0:
        return number == 2
    divisor = 3
    while divisor * divisor <= number:
        if number % divisor == 0:
            return False
        divisor += 2
    return True


# Клетки коллекции простых: 78 чисел от №2 до №397.
PRIME_NUMBERS: tuple[int, ...] = tuple(n for n in range(2, PRIME_STRIP_MAX + 1) if _is_prime(n))


def _primes_challenge(rows: list[RunRow], planned: dict[tuple[str, int], list[tuple[date, str]]]) -> dict[str, object]:
    """Ч29. Коллекция простых номеров стартов.

    Была счётчиком финишей, стала коллекцией плитками (просьба Дмитрия
    11.09.2026): плитки показывают, каких простых номеров не хватает и где их
    взять, а счётчик финишей на это не отвечал. Сколько всего финишей пришлось
    на простые номера и какую долю они составляют — осталось в подписи.
    """
    first_by_number: dict[int, RunRow] = {}
    counts: Counter[int] = Counter()
    matched = 0
    numbered = 0
    for row in rows:
        number = row.event_number
        if number is None or number <= 0:
            continue
        numbered += 1
        if number > PRIME_STRIP_MAX or not _is_prime(number):
            continue
        matched += 1
        first_by_number.setdefault(number, row)
        counts[number] += 1
    planned_by_number = _planned_by_number(planned, PRIME_NUMBERS)
    cells = [
        _cell(
            str(number),
            first_by_number.get(number),
            count=counts.get(number),
            hint=_planned_hint(planned_by_number.get(number)),
        )
        for number in PRIME_NUMBERS
    ]
    sorted_dates = sorted(row.event_date for row in first_by_number.values())
    return _challenge(
        code="primes",
        title="Простые числа",
        icon="➗",
        description=(
            "Собирай старты с простыми номерами: №2, №3, №5, №7, №11… "
            "Простое число делится только на себя и на единицу. "
            f"В коллекции все простые до №{PRIME_STRIP_MAX}."
        ),
        category="collection",
        current=len(first_by_number),
        unit="простых номеров",
        detail={"cells": cells},
        level_dates_fn=lambda levels: _level_dates(sorted_dates, levels),
    )


def _runs_needed_for_p(counts: list[int], target_p: int) -> int:
    """Минимум добежек до p-индекса target_p: добиваем target_p самых
    «наполненных» локаций до target_p финишей (новые локации считаем с нуля)."""
    top = sorted(counts, reverse=True)[:target_p]
    top += [0] * (target_p - len(top))
    return sum(max(0, target_p - count) for count in top)


def _p_index_level_dates(rows: list[RunRow], levels: dict[str, int]) -> dict[str, str | None]:
    """Реплей истории в хронологическом порядке: после каждого финиша
    пересчитываем p-индекс и запоминаем первую дату, когда он достиг порога."""
    counts: Counter[str] = Counter()
    achieved: dict[str, date | None] = {level: None for level in LEVEL_ORDER}
    for row in rows:
        counts[row.location_key] += 1
        sorted_counts = sorted(counts.values(), reverse=True)
        p_index = 0
        for index, count in enumerate(sorted_counts, start=1):
            if count >= index:
                p_index = index
            else:
                break
        for level in LEVEL_ORDER:
            if achieved[level] is None and p_index >= levels[level]:
                achieved[level] = row.event_date
    return {level: (value.isoformat() if value else None) for level, value in achieved.items()}


def _p_index_challenge(rows: list[RunRow]) -> dict[str, object]:
    counts = Counter(row.location_key for row in rows)
    names: dict[str, str] = {}
    for row in rows:
        names.setdefault(row.location_key, row.location_name)
    sorted_counts = sorted(counts.values(), reverse=True)
    p_index = 0
    for index, count in enumerate(sorted_counts, start=1):
        if count >= index:
            p_index = index
        else:
            break
    counts_values = list(counts.values())

    def _to_next_label(levels: dict[str, int], next_level: str) -> str | None:
        needed = _runs_needed_for_p(counts_values, levels[next_level])
        return f"ещё {needed} {_plural_ru(needed, ('пробежка', 'пробежки', 'пробежек'))}"

    top = [{"location": names[key], "count": count} for key, count in counts.most_common(20)]
    return _challenge(
        code="p_index",
        title="p-индекс",
        icon="🧮",
        description="p локаций, в каждой из которых минимум p финишей.",
        category="scale",
        current=p_index,
        unit="",
        detail={"items": top},
        to_next_label_fn=_to_next_label,
        level_dates_fn=lambda levels: _p_index_level_dates(rows, levels),
    )


def _pilgrim_challenge(rows: list[RunRow]) -> dict[str, object]:
    first_visit: dict[str, date] = {}
    for row in rows:
        if row.location_key not in first_visit or row.event_date < first_visit[row.location_key]:
            first_visit[row.location_key] = row.event_date
    sorted_dates = sorted(first_visit.values())
    return _challenge(
        code="pilgrim",
        title="Коллекционер локаций",
        icon="🗺️",
        description="Финишируй в как можно большем числе разных локаций.",
        category="scale",
        current=len(first_visit),
        unit="локаций",
        level_dates_fn=lambda levels: _level_dates(sorted_dates, levels),
    )


def _inspector_challenge(rating_rows: list[RatingRow]) -> dict[str, object]:
    sorted_dates = sorted(row.rated_on for row in rating_rows)
    return _challenge(
        code="inspector",
        title="Ревизор",
        icon="🔍",
        description="Оценивай старты, где бегал или волонтёрил: звёзды за организацию, трассу и атмосферу помогают другим выбрать, куда ехать.",
        category="community",
        current=len(sorted_dates),
        unit="оценок",
        level_dates_fn=lambda levels: _level_dates(sorted_dates, levels),
    )


def _reviewer_challenge(rating_rows: list[RatingRow]) -> dict[str, object]:
    sorted_dates = sorted(row.rated_on for row in rating_rows if row.is_review)
    return _challenge(
        code="reviewer",
        title="Рецензент",
        icon="📝",
        description=(
            f"Звёзд мало — расскажи словами. Отзыв от {REVIEW_MIN_COMMENT_LEN} символов: "
            "как встретили новичков, понятен ли брифинг, легко ли найти старт, что по трассе."
        ),
        category="community",
        current=len(sorted_dates),
        unit="рецензий",
        level_dates_fn=lambda levels: _level_dates(sorted_dates, levels),
    )


def _photo_reporter_challenge(rating_rows: list[RatingRow]) -> dict[str, object]:
    """Третья ступень обратной связи: звёзды → текст → фотографии.

    Считаем ОЦЕНКИ С ФОТО, а не сами файлы: пять кадров с одного старта — это
    один рассказ о нём, и счётчик не должен поощрять заливку галереи вместо
    поездки на новую площадку.
    """
    sorted_dates = sorted(row.rated_on for row in rating_rows if row.has_photo)
    return _challenge(
        code="photo_reporter",
        title="Фоторепортёр",
        icon="📸",
        description=(
            "Приложи к отзыву фотографии: старт, финишный створ, трасса, указатели. "
            "Одна фотография объясняет про место больше, чем абзац текста, — по ней видно, "
            "во что одеваться, где парковаться и на что там вообще смотреть."
        ),
        category="community",
        current=len(sorted_dates),
        unit="отзывов с фото",
        level_dates_fn=lambda levels: _level_dates(sorted_dates, levels),
    )


def _v_index(counts: dict[str, int]) -> int:
    """V такое, что найдётся V локаций, на каждой минимум по V волонтёрств."""
    value = 0
    for index, count in enumerate(sorted(counts.values(), reverse=True), start=1):
        if count >= index:
            value = index
        else:
            break
    return value


def _v_index_challenge(rows: list[VolunteerRoleRow]) -> dict[str, object]:
    """V-индекс волонтёра — тот же индекс Хирша, что и p-индекс, но по волонтёрствам.

    V локаций, на каждой минимум по V волонтёрств: p-индекс меряет, где человек
    бегает не разово, V-индекс — где он не разово ПОМОГАЕТ. Пороги общие с
    p-индексом, чтобы две шкалы читались одинаково.

    Волонтёрство — это (дата, локация), а не строка роли: две роли в одну
    субботу на одной площадке — одно волонтёрство, иначе индекс рос бы у того, кому вписали
    вторую роль, а не у того, кто приехал ещё раз. Так же считает и общий
    счётчик волонтёрств (app/volunteering_occasions.py).

    **parkrun в зачёт не идёт (решение Дмитрия 07.09.2026).** Он отдаёт не
    протоколы, а сводку профиля: у волонтёрства нет ни локации, ни даты, только
    «Marshal (12×)» — то есть ровно те два поля, на которых этот индекс и
    держится. В «Мастере на все роли» сводка, наоборот, остаётся: там вопрос
    «выходил ли вообще», и на него она отвечает честно.

    Даты уровней считаются проигрыванием истории: индекс не может вырасти
    больше чем на 1 за одно волонтёрство, поэтому список «дат, когда индекс
    поднялся» ровно той же длины, что и сам индекс.
    """
    shifts: set[tuple[date, str]] = set()
    labels: dict[str, str] = {}
    for row in rows:
        if row.platform_code == MAP_HISTORIC_PLATFORM or row.event_date is None:
            continue
        shifts.add((row.event_date, row.location_key))
        labels.setdefault(row.location_key, row.location_name)

    counts: Counter[str] = Counter()
    growth_dates: list[date | None] = []
    current = 0
    for event_date, location_key in sorted(shifts):
        counts[location_key] += 1
        updated = _v_index(counts)
        while updated > current:
            growth_dates.append(event_date)
            current += 1

    def _to_next_label(levels: dict[str, int], next_level: str) -> str | None:
        needed = _volunteerings_needed_for_v(counts, levels[next_level])
        return f"ещё {needed} {_plural_ru(needed, ('волонтёрство', 'волонтёрства', 'волонтёрств'))}"

    top = [
        {"value": labels[key], "count": count}
        for key, count in sorted(counts.items(), key=lambda item: (-item[1], labels[item[0]]))[:20]
    ]
    return _challenge(
        code="v_index",
        title="V-индекс",
        icon="🧰",
        description=("V локаций, на каждой минимум по V волонтёрств."),
        category="community",
        current=current,
        unit="",
        detail={"items": top},
        level_dates_fn=lambda levels: _level_dates_optional(growth_dates, levels),
        to_next_label_fn=_to_next_label,
    )


def _volunteerings_needed_for_v(counts: Counter[str], target: int) -> int:
    """Сколько волонтёрств не хватает до V-индекса target — дешевейшим путём.

    Добираем target локаций, начиная с самых обжитых: на каждой нужно target
    волонтёрств. Площадки, где человек ещё не помогал, стоят полные target
    волонтёрств каждая, поэтому недостающие позиции считаем как нули.
    """
    have = sorted(counts.values(), reverse=True)[:target]
    have += [0] * (target - len(have))
    return sum(max(target - count, 0) for count in have)


def _role_master_challenge(rows: list[VolunteerRoleRow]) -> dict[str, object]:
    """Сколько РАЗНЫХ волонтёрских ролей человек освоил — любых.

    Решение Дмитрия 07.09.2026: не фиксированный список ролей, а счётчик
    широты. Список бы наказывал за площадку: в 5 вёрстах есть «Проверка
    трассы», в С95 — «Организация питания», и человек не виноват, что у его
    старта нет какой-то роли из чужой системы. Пятнадцать любых — цель, до
    которой можно дойти в любой из систем.

    Роли приведены к общему знаменателю (см. volunteer_role_taxonomy), так что
    «Секундомер» 5 вёрст, «Хронометраж» С95 и «Timekeeper» parkrun — одна
    освоенная роль, а не три. Сводка parkrun в зачёт идёт: на вопрос «выходил
    ли на роль вообще» она отвечает честно, даже без дат.
    """
    first_by_role: dict[str, VolunteerRoleRow] = {}
    counts: Counter[str] = Counter()
    for row in rows:
        counts[row.role_key] += row.occasions
        known = first_by_role.get(row.role_key)
        # Роль могла прийти и датированной строкой, и сводкой parkrun. В клетке
        # показываем датированную: «закрыто 12.10.24 в Кузьминках» полезнее,
        # чем «закрыто по сводке parkrun», даже если parkrun был раньше.
        if known is None or (known.event_date is None and row.event_date is not None):
            first_by_role[row.role_key] = row

    def _cell_for(key: str, label: str) -> dict[str, object]:
        row = first_by_role.get(key)
        count = counts.get(key) or None
        return {
            "label": label,
            "done": row is not None,
            "date": row.event_date.isoformat() if row is not None and row.event_date else None,
            "location": row.location_name if row is not None and row.event_date else None,
            "hint": (
                "Роль ещё не пробовали"
                if row is None
                else ("Закрыто по сводке волонтёрств parkrun — дат она не хранит" if row.event_date is None else None)
            ),
            "platform_code": row.platform_code if row is not None else None,
            "count": count,
            "count_label": (
                f"{count} {_plural_ru(count, ('волонтёрство', 'волонтёрства', 'волонтёрств'))}" if count else None
            ),
        }

    # Клетки — весь справочник ролей в порядке субботнего утра: это не
    # обязательный список, а меню, из которого набираются пятнадцать любых.
    cells = [_cell_for(key, label) for key, label in CANONICAL_ROLE_LABELS.items()]
    # Роль, которой в справочнике ещё нет (система завела новую), не теряется:
    # она идёт под своим названием в хвосте — и в счётчик, и в меню.
    cells += [
        _cell_for(key, first_by_role[key].role_label) for key in first_by_role if key not in CANONICAL_ROLE_LABELS
    ]
    # Роли, закрытые недатированной сводкой parkrun, идут первыми: см.
    # порядок строк в _collect_volunteer_role_rows.
    sorted_dates: list[date | None] = sorted(
        (row.event_date for row in first_by_role.values()),
        key=lambda value: (value is not None, value or date.min),
    )
    return _challenge(
        code="role_master",
        title="Мастер на все роли",
        icon="🧑\u200d🔧",
        description=(
            "Побывай в как можно большем числе разных волонтёрских ролей — годятся любые. "
            "Одна и та же роль в разных системах считается одной."
        ),
        category="community",
        current=len(first_by_role),
        unit="ролей",
        detail={"cells": cells},
        level_dates_fn=lambda levels: _level_dates_optional(sorted_dates, levels),
    )


def _regions_challenge(rows: list[RunRow]) -> dict[str, object]:
    first_visit: dict[str, date] = {}
    for row in rows:
        if not row.region:
            continue
        region = _canonical_region(row.region)
        if region not in first_visit or row.event_date < first_visit[region]:
            first_visit[region] = row.event_date
    sorted_dates = sorted(first_visit.values())
    return _challenge(
        code="regions",
        title="Путешественник",
        icon="🧭",
        description="Пробеги в разных регионах — от домашнего парка до другого конца страны.",
        category="scale",
        current=len(first_visit),
        unit="регионов",
        level_dates_fn=lambda levels: _level_dates(sorted_dates, levels),
    )


def _streak_level_dates(activity_dates: set[date], levels: dict[str, int]) -> dict[str, str | None]:
    """Идём по субботам-с-активностью по порядку; в момент, когда текущая
    (не обязательно ещё финальная) серия впервые достигает порога — это и есть
    дата уровня, даже если серия потом прервётся."""
    saturdays = sorted(saturday_weeks(activity_dates))
    achieved: dict[str, date | None] = {level: None for level in LEVEL_ORDER}
    current_run = 0
    previous: date | None = None
    for value in saturdays:
        current_run = current_run + 1 if previous is not None and value - previous == timedelta(days=7) else 1
        previous = value
        for level in LEVEL_ORDER:
            if achieved[level] is None and current_run >= levels[level]:
                achieved[level] = value
    return {level: (value.isoformat() if value else None) for level, value in achieved.items()}


def _streak_challenge(rows: list[RunRow], vol_rows: dict[str, list[tuple[date, str]]]) -> dict[str, object]:
    """«Серийный бегун» — по неделям, как календарь суббот и серии дашборда.

    До 16.09.2026 челлендж считал только буквальные субботы (weekday() == 5) и
    расходился с календарём: перенос старта на воскресенье (рабочая суббота
    01.11.2025) рвал серию здесь, хотя в календаре она шла дальше. Правило
    одно на весь сайт — app.saturday_week.
    """
    activity_dates = {row.event_date for row in rows}
    for platform_code, platform_rows in vol_rows.items():
        activity_dates |= volunteer_occasion_dates(platform_code, platform_rows)
    streak = max_saturday_streak(activity_dates)
    return _challenge(
        code="streak",
        title="Серийный бегун",
        icon="🔥",
        description="Лучшая серия суббот подряд — пробежкой или волонтёрством, без пропусков.",
        category="scale",
        current=streak,
        unit="суббот",
        level_dates_fn=lambda levels: _streak_level_dates(activity_dates, levels),
    )


def _best_year_level_dates(rows: list[RunRow], levels: dict[str, int]) -> dict[str, str | None]:
    """Для каждого года — дата, когда счётчик пробежек В ЭТОМ году впервые
    достиг порога; уровень "получен", как только хоть один год его достиг —
    берём самую раннюю такую дату среди всех лет."""
    dates_by_year: dict[int, list[date]] = {}
    for row in rows:
        dates_by_year.setdefault(row.event_date.year, []).append(row.event_date)
    best_date: dict[str, date | None] = {level: None for level in LEVEL_ORDER}
    for year_dates in dates_by_year.values():
        year_dates.sort()
        for level in LEVEL_ORDER:
            threshold = levels[level]
            if len(year_dates) >= threshold:
                candidate = year_dates[threshold - 1]
                current_best = best_date[level]
                if current_best is None or candidate < current_best:
                    best_date[level] = candidate
    return {level: (value.isoformat() if value else None) for level, value in best_date.items()}


def _best_year_challenge(rows: list[RunRow]) -> dict[str, object]:
    by_year = Counter(row.event_date.year for row in rows)
    best = max(by_year.values(), default=0)
    return _challenge(
        code="best_year",
        title="Ударный год",
        icon="📈",
        description="Твой личный рекорд активности: сколько пробежек уместилось в один календарный год.",
        category="scale",
        current=best,
        unit="пробежек за год",
        level_dates_fn=lambda levels: _best_year_level_dates(rows, levels),
    )


# ---------------------------------------------------------------------------
# Клубы (10/25/50/100/250/500/1000)

CLUB_THRESHOLDS = (10, 25, 50, 100, 250, 500, 1000)


def _volunteer_occasion_instances(platform_code: str, rows: list[tuple[date, str]]) -> list[date]:
    """Даты волонтёрских occasion'ов (с повторами — на пятивёрстовской
    инвентаризации 1 января несколько локаций в один день считаются разными
    occasion'ами). Длина результата совпадает с count_volunteering_for_platform."""
    if platform_code == "five_verst":
        seen_inventory: set[tuple[date, str]] = set()
        seen_regular: set[date] = set()
        instances: list[date] = []
        for event_date, location_key in sorted(rows):
            if is_inventory_day(event_date):
                key = (event_date, location_key or "unknown")
                if key not in seen_inventory:
                    seen_inventory.add(key)
                    instances.append(event_date)
            elif event_date not in seen_regular:
                seen_regular.add(event_date)
                instances.append(event_date)
        return sorted(instances)
    seen: set[date] = set()
    instances = []
    for event_date, _location_key in sorted(rows):
        if event_date not in seen:
            seen.add(event_date)
            instances.append(event_date)
    return instances


def _club_entry(code: str, title: str, icon: str, dates: list[date], *, extra_count: int = 0) -> dict[str, object]:
    """extra_count — волонтёрства без известной даты (parkrun: только общий
    счётчик), добавляются к current, но не могут дать level_dates для
    порогов за пределами len(dates)."""
    current = len(dates) + extra_count
    sorted_dates = sorted(dates)
    earned = [threshold for threshold in CLUB_THRESHOLDS if current >= threshold]
    next_threshold = next((threshold for threshold in CLUB_THRESHOLDS if current < threshold), None)
    previous = earned[-1] if earned else 0
    if next_threshold is None:
        pct = 100.0
    else:
        pct = round((current - previous) / (next_threshold - previous) * 100, 1)
    return {
        "code": code,
        "title": title,
        "icon": icon,
        "current": current,
        "thresholds": list(CLUB_THRESHOLDS),
        "earned": earned,
        "next_threshold": next_threshold,
        "to_next": next_threshold - current if next_threshold is not None else None,
        "pct_to_next": pct,
        "level_dates": _threshold_dates(sorted_dates, CLUB_THRESHOLDS),
    }


def _compute_clubs(
    rows: list[RunRow],
    vol_rows: dict[str, list[tuple[date, str]]],
    parkrun_volunteer_total: int = 0,
) -> dict[str, object]:
    all_vol_instances: list[date] = []
    for code, platform_rows in vol_rows.items():
        all_vol_instances.extend(_volunteer_occasion_instances(code, platform_rows))
    overall = [
        _club_entry("runs", "Пробежки", "🏃", [row.event_date for row in rows]),
        _club_entry("volunteering", "Волонтёрства", "💚", all_vol_instances, extra_count=parkrun_volunteer_total),
    ]
    runs_by_platform: dict[str, list[date]] = {}
    for row in rows:
        runs_by_platform.setdefault(row.platform_code, []).append(row.event_date)
    platform_codes = set(runs_by_platform) | set(vol_rows)
    if parkrun_volunteer_total > 0:
        platform_codes.add("parkrun")
    platforms = []
    for code in sorted(platform_codes, key=lambda code: -len(runs_by_platform.get(code, []))):
        run_dates = runs_by_platform.get(code, [])
        vol_dates = _volunteer_occasion_instances(code, vol_rows.get(code, []))
        vol_extra = parkrun_volunteer_total if code == "parkrun" else 0
        if not run_dates and not vol_dates and not vol_extra:
            continue
        platforms.append(
            {
                "platform_code": code,
                "entries": [
                    _club_entry("runs", "Пробежки", "🏃", run_dates),
                    _club_entry("volunteering", "Волонтёрства", "💚", vol_dates, extra_count=vol_extra),
                ],
            }
        )
    return {"overall": overall, "platforms": platforms}


def _weather_counter_challenge(
    days: list[WeatherDay],
    *,
    code: str,
    title: str,
    icon: str,
    description: str,
    unit: str,
    predicate: Callable[[WeatherDay], bool],
) -> dict[str, object]:
    """Счётчик выходов на площадку в такую погоду, с датами уровней."""
    matched = sorted((day for day in days if predicate(day)), key=lambda day: day.event_date)
    sorted_dates = [day.event_date for day in matched]
    items = [
        {
            "date": day.event_date.isoformat(),
            "location": day.location_name,
            "value": (
                format_temperature(day.temperature_c)
                if code in ("walrus", "heatproof")
                else f"{day.precipitation_run_mm:.1f} мм"
                if day.precipitation_run_mm is not None
                else ""
            ),
            # Волонтёрский выход помечаем: человек должен видеть, за что именно
            # ему засчитали тот мороз.
            "note": "волонтёрство" if day.volunteered else None,
        }
        for day in reversed(matched)
    ][:30]
    return _challenge(
        code=code,
        title=title,
        icon=icon,
        description=description,
        category="weather",
        current=len(matched),
        unit=unit,
        detail={"items": items},
        level_dates_fn=lambda levels: _level_dates(sorted_dates, levels),
    )


def _walrus_challenge(days: list[WeatherDay]) -> dict[str, object]:
    return _weather_counter_challenge(
        days,
        code="walrus",
        title="Морж",
        icon="🥶",
        description=(
            "Будь на старте при −20° и ниже — финишёром или волонтёром на площадке. Температура — "
            "по архиву погоды в точке старта; ощущаемая с ветром бывает ещё ниже, но считаем воздух."
        ),
        unit="морозных стартов",
        predicate=lambda day: day.temperature_c is not None and day.temperature_c <= DEEP_FROST_C,
    )


def _heatproof_challenge(days: list[WeatherDay]) -> dict[str, object]:
    return _weather_counter_challenge(
        days,
        code="heatproof",
        title="Огнеупорный",
        icon="🔥",
        description=(
            "Будь на старте при +25° и выше — финишёром или волонтёром на площадке. "
            "Жара в девять утра редкость даже на юге."
        ),
        unit="жарких стартов",
        predicate=lambda day: day.temperature_c is not None and day.temperature_c >= HEAT_C,
    )


def _rain_runner_challenge(days: list[WeatherDay]) -> dict[str, object]:
    return _weather_counter_challenge(
        days,
        code="rain_runner",
        title="Под дождём",
        icon="🌧️",
        description=(
            "Старты, на которых шёл дождь, — свои и отработанные волонтёром: от 1 мм за час забега. "
            "Морось в 0.3 мм архив видит, но дождём не считает, а снегопад считает снегом, а не дождём."
        ),
        unit="дождливых стартов",
        predicate=lambda day: day.precipitation_run_mm is not None and day.precipitation_run_mm >= RAIN_MM,
    )


# Меню погоды «Всепогодного»: ключ → (подпись, условие, подсказка).
_ALL_WEATHER_CELLS: tuple[tuple[str, str, Callable[[WeatherDay], bool], str], ...] = (
    (
        "rain",
        "Дождь",
        lambda r: r.precipitation_run_mm is not None and r.precipitation_run_mm >= RAIN_MM,
        "от 1 мм дождя за час забега",
    ),
    (
        "downpour",
        "Ливень",
        lambda r: r.precipitation_run_mm is not None and r.precipitation_run_mm >= DOWNPOUR_MM,
        "от 3 мм дождя за час забега",
    ),
    (
        "snowfall",
        "Снегопад",
        lambda r: (r.snowfall_cm or 0) > 0 or (r.weather_code in SNOW_CODES),
        "снег шёл в час старта",
    ),
    (
        "snow_cover",
        "По снегу",
        lambda r: r.snow_depth_cm is not None and r.snow_depth_cm >= SNOW_DEPTH_CM,
        "снежный покров от 1 см",
    ),
    ("frost", "Мороз", lambda r: r.temperature_c is not None and r.temperature_c <= FROST_C, "−10° и ниже"),
    (
        "deep_frost",
        "Лютый мороз",
        lambda r: r.temperature_c is not None and r.temperature_c <= DEEP_FROST_C,
        "−20° и ниже",
    ),
    ("heat", "Жара", lambda r: r.temperature_c is not None and r.temperature_c >= HEAT_C, "+25° и выше"),
    ("gale", "Шквал", lambda r: r.wind_gusts_ms is not None and r.wind_gusts_ms >= WINDY_GUST_MS, "порывы от 15 м/с"),
)


def _all_weather_challenge(days: list[WeatherDay]) -> dict[str, object]:
    """Коллекция погод: клетка закрывается первым выходом на площадку в такую погоду."""
    first_by_key: dict[str, WeatherDay] = {}
    counts: Counter[str] = Counter()
    for day in sorted(days, key=lambda d: d.event_date):
        for key, _label, predicate, _hint in _ALL_WEATHER_CELLS:
            if predicate(day):
                counts[key] += 1
                first_by_key.setdefault(key, day)
    cells = []
    for key, label, _predicate, hint in _ALL_WEATHER_CELLS:
        first = first_by_key.get(key)
        count = counts.get(key) or None
        cells.append(
            _cell(
                label,
                first,
                hint=None if first else hint,
                count=count,
                # «Финиш» здесь больше не подходит: клетку мог закрыть и
                # волонтёрский выход.
                count_label=(
                    None if count is None else f"{count} {_plural_ru(count, ('старт', 'старта', 'стартов'))}"
                ),
            )
        )
    sorted_dates = sorted(day.event_date for day in first_by_key.values())
    return _challenge(
        code="all_weather",
        title="Всепогодный",
        icon="🌦️",
        description=(
            "Собери все погоды: дождь и ливень, снегопад и трасса под снегом, мороз и лютый мороз, "
            "жара и шквалистый ветер. Клетку закрывает и пробежка, и волонтёрство на площадке. "
            "Погода берётся из архива по точке старта."
        ),
        category="weather",
        current=len(first_by_key),
        unit="погод",
        detail={"cells": cells},
        level_dates_fn=lambda levels: _level_dates(sorted_dates, levels),
    )


_SEASON_MONTHS = ("Янв", "Фев", "Мар", "Апр", "Май", "Июн", "Июл", "Авг", "Сен", "Окт", "Ноя", "Дек")


def _seasons_challenge(rows: list[RunRow]) -> dict[str, object]:
    """Финиши во все 12 месяцев года — год не важен."""
    first_by_month: dict[int, RunRow] = {}
    counts: Counter[int] = Counter()
    for row in sorted(rows, key=lambda r: r.event_date):
        counts[row.event_date.month] += 1
        first_by_month.setdefault(row.event_date.month, row)
    cells = [
        _cell(
            label,
            first_by_month.get(month),
            hint=None if month in first_by_month else "ещё не бегали в этом месяце",
            count=counts.get(month) or None,
        )
        for month, label in enumerate(_SEASON_MONTHS, start=1)
    ]
    sorted_dates = sorted(row.event_date for row in first_by_month.values())
    return _challenge(
        code="seasons",
        title="Коллекционер сезонов",
        icon="🍂",
        description="Финишируй во все двенадцать месяцев года — январь и июль считаются одинаково, год не важен.",
        category="weather",
        current=len(first_by_month),
        unit="месяцев",
        detail={"cells": cells},
        level_dates_fn=lambda levels: _level_dates(sorted_dates, levels),
    )


def _build_challenge_list(
    rows: list[RunRow],
    vol_rows: dict[str, list[tuple[date, str]]],
    upcoming: dict[tuple[str, int], list[tuple[date, str]]],
    rating_rows: list[RatingRow],
    vol_role_rows: list[VolunteerRoleRow],
    weather_days: list[WeatherDay],
    *,
    alphabet_names: dict[str, set[str]],
    platform_code: str | None,
    home_country: str | None,
    planned: dict[tuple[str, int], list[tuple[date, str]]] | None = None,
) -> list[dict[str, object]]:
    challenges = [
        _seconds_challenge(rows),
        _positions_challenge(rows),
        _alphabet_challenge(rows, alphabet_names, platform_code=platform_code),
        _calendar_days_challenge(rows),
        _start_numbers_range_challenge(
            rows,
            upcoming,
            code="start_numbers",
            title=START_NUMBER_TITLES["start_numbers"],
            description="Прими участие в стартах с порядковыми номерами от №1 до №200 — неважно, в какой системе получен каждый номер.",
            low=START_NUMBER_RANGES["start_numbers"][0],
            high=START_NUMBER_RANGES["start_numbers"][1],
        ),
        _start_numbers_range_challenge(
            rows,
            upcoming,
            code="start_numbers_pro",
            title=START_NUMBER_TITLES["start_numbers_pro"],
            description="Для тех, кому мало двух сотен: старты с порядковыми номерами от №201 до №400 — неважно, в какой системе получен каждый номер.",
            low=START_NUMBER_RANGES["start_numbers_pro"][0],
            high=START_NUMBER_RANGES["start_numbers_pro"][1],
        ),
        _weekdays_challenge(rows),
        _palindrome_challenge(rows),
        _deja_vu_challenge(rows),
        _number_match_challenge(rows),
        _jubilee_challenge(rows),
        _p_index_challenge(rows),
        _pilgrim_challenge(rows),
        _regions_challenge(rows),
        _streak_challenge(rows, vol_rows),
        _best_year_challenge(rows),
        _inspector_challenge(rating_rows),
        _reviewer_challenge(rating_rows),
        _photo_reporter_challenge(rating_rows),
        _v_index_challenge(vol_role_rows),
        _role_master_challenge(vol_role_rows),
        _minute_range_challenge(rows),
        _fibonacci_challenge(rows, planned or {}),
        _nelson_challenge(rows, planned or {}),
        _primes_challenge(rows, planned or {}),
        _wilson_challenge(rows, planned or {}),
        _countries_challenge(rows, home_country),
        _walrus_challenge(weather_days),
        _heatproof_challenge(weather_days),
        _rain_runner_challenge(weather_days),
        _all_weather_challenge(weather_days),
        _seasons_challenge(rows),
    ]
    if platform_code is None:
        # «Хет-трик систем» — челлендж ПРО переходы между системами: под
        # фильтром одной системы он по построению показывал бы вечную единицу
        # из четырёх. В сквозном виде он на месте, в разрезе платформы его нет.
        challenges.append(_platform_slam_challenge(rows))
    return challenges


def _rows_before_last_activity(rows: list[RunRow]) -> list[RunRow] | None:
    """Пробежки без самого последнего дня активности — чтобы вычислить, что
    именно продвинулось благодаря последней пробежке. None, если пробежек нет."""
    if not rows:
        return None
    last_date = rows[-1].event_date  # rows уже отсортированы по event_date
    return [row for row in rows if row.event_date < last_date]


def _scope_by_platform(
    rows: list[RunRow],
    vol_rows: dict[str, list[tuple[date, str]]],
    upcoming: dict[tuple[str, int], list[tuple[date, str]]],
    rating_rows: list[RatingRow],
    vol_role_rows: list[VolunteerRoleRow],
    weather_days: list[WeatherDay],
    platform_code: str | None,
) -> tuple[
    list[RunRow],
    dict[str, list[tuple[date, str]]],
    dict[tuple[str, int], list[tuple[date, str]]],
    list[RatingRow],
    list[VolunteerRoleRow],
    list[WeatherDay],
]:
    """Сужает пробежки/волонтёрства/прогноз номеров/оценки/роли/погодные дни до
    одной системы — для челленджей в разрезе платформы. None — без сужения."""
    if platform_code is None:
        return rows, vol_rows, upcoming, rating_rows, vol_role_rows, weather_days
    scoped_rows = [row for row in rows if row.platform_code == platform_code]
    scoped_vol_rows = {code: v for code, v in vol_rows.items() if code == platform_code}
    scoped_upcoming = {key: v for key, v in upcoming.items() if key[0] == platform_code}
    scoped_rating_rows = [row for row in rating_rows if row.platform_code == platform_code]
    scoped_role_rows = [row for row in vol_role_rows if row.platform_code == platform_code]
    scoped_weather_days = [day for day in weather_days if day.platform_code == platform_code]
    return (
        scoped_rows,
        scoped_vol_rows,
        scoped_upcoming,
        scoped_rating_rows,
        scoped_role_rows,
        scoped_weather_days,
    )


class StartNumberPlanError(ValueError):
    """Запрошен челлендж, у которого нет планирования по номерам стартов."""


@dataclass(frozen=True)
class PlanSpec:
    """Как устроена таблица планирования конкретного челленджа.

    columns × weeks_per_column = weeks: «Нумератор» режет три недели на три
    колонки по неделе (E, E+1, E+2 — забеги локации подряд), остальные кладут
    весь горизонт в одну колонку «Где и когда», потому что до №222 ближайшей
    площадке ехать месяцы и колонок понадобилось бы 26.

    numbers получает число пробежек человека: «Совпадению номеров» нужны не
    фиксированные числа, а НОМЕРА ЕГО БУДУЩИХ ПРОБЕЖЕК — 203-я, 204-я и так
    далее.

    tracks_done=False у челленджей-счётчиков: там один и тот же номер можно
    брать сколько угодно раз (каждый юбилей идёт в зачёт), и отметка «закрыто»
    в таблице только сбивала бы.
    """

    numbers: Callable[[int], tuple[int, ...]]
    weeks: int
    columns: int
    column_titles: tuple[str, ...]
    horizon_label: str
    entries_per_cell: int
    tracks_done: bool = True
    intro: str | None = None

    @property
    def weeks_per_column(self) -> int:
        return max(1, self.weeks // self.columns)


def _range_numbers(low: int, high: int) -> Callable[[int], tuple[int, ...]]:
    return lambda _runs: tuple(range(low, high + 1))


# Подписи колонок «Нумератора»: считаем в ЗАБЕГАХ локации, а не в календарных
# неделях — E это ближайший старт площадки, E+1 следующий за ним.
_START_NUMBER_COLUMNS = ("Ближайший забег (E)", "E+1", "E+2")
_WIDE_COLUMN = ("Где и когда",)

# Ч «Совпадение номеров»: на сколько будущих пробежек вперёд показываем шансы.
# Дальше десятка смысла нет — чтобы поймать 213-е совпадение, нужно сначала
# ровно десять раз никуда не попасть.
NUMBER_MATCH_PLAN_DEPTH = 10

# Ч «Юбилейщик»: круглые номера, до которых наши системы вообще доросли.
JUBILEE_STEP = 50
JUBILEE_NUMBERS: tuple[int, ...] = tuple(range(JUBILEE_STEP, PLANNING_MAX_NUMBER + 1, JUBILEE_STEP))


def _number_match_plan_numbers(runs: int) -> tuple[int, ...]:
    """Номера, которые дадут совпадение: следующая пробежка должна прийтись на
    старт со своим порядковым номером, через одну — на следующий, и так далее."""
    return tuple(range(runs + 1, runs + 1 + NUMBER_MATCH_PLAN_DEPTH))


PLAN_SPECS: dict[str, PlanSpec] = {
    "start_numbers": PlanSpec(
        numbers=_range_numbers(*START_NUMBER_RANGES["start_numbers"]),
        weeks=START_NUMBER_PLAN_WEEKS,
        columns=START_NUMBER_PLAN_WEEKS,
        column_titles=_START_NUMBER_COLUMNS,
        horizon_label="ближайшие 3 недели",
        entries_per_cell=12,
    ),
    "start_numbers_pro": PlanSpec(
        numbers=_range_numbers(*START_NUMBER_RANGES["start_numbers_pro"]),
        weeks=START_NUMBER_PLAN_WEEKS,
        columns=START_NUMBER_PLAN_WEEKS,
        column_titles=_START_NUMBER_COLUMNS,
        horizon_label="ближайшие 3 недели",
        entries_per_cell=12,
    ),
    "fibonacci": PlanSpec(
        numbers=lambda _runs: FIBONACCI_NUMBERS,
        weeks=PLANNING_WEEKS,
        columns=1,
        column_titles=_WIDE_COLUMN,
        horizon_label="ближайшие полгода",
        entries_per_cell=10,
    ),
    "nelson": PlanSpec(
        numbers=lambda _runs: NELSON_NUMBERS,
        weeks=PLANNING_WEEKS,
        columns=1,
        column_titles=_WIDE_COLUMN,
        horizon_label="ближайшие полгода",
        entries_per_cell=10,
    ),
    "primes": PlanSpec(
        numbers=lambda _runs: PRIME_NUMBERS,
        weeks=PLANNING_WEEKS,
        columns=1,
        column_titles=_WIDE_COLUMN,
        horizon_label="ближайшие полгода",
        entries_per_cell=10,
    ),
    "wilson": PlanSpec(
        numbers=_range_numbers(1, WILSON_STRIP_NUMBERS),
        weeks=PLANNING_WEEKS,
        columns=1,
        column_titles=_WIDE_COLUMN,
        horizon_label="ближайшие полгода",
        entries_per_cell=10,
    ),
    "jubilee": PlanSpec(
        numbers=lambda _runs: JUBILEE_NUMBERS,
        weeks=PLANNING_WEEKS,
        columns=1,
        column_titles=_WIDE_COLUMN,
        horizon_label="ближайшие полгода",
        entries_per_cell=12,
        tracks_done=False,
        intro=(
            "Юбилеи идут в зачёт сколько угодно раз, поэтому «закрытых» номеров здесь нет: "
            "каждый круглый старт — плюс один, даже если на таком номере вы уже бывали."
        ),
    ),
    "number_match": PlanSpec(
        numbers=_number_match_plan_numbers,
        weeks=PLANNING_WEEKS,
        columns=1,
        column_titles=_WIDE_COLUMN,
        horizon_label="ближайшие полгода",
        entries_per_cell=12,
        tracks_done=False,
        intro=(
            "Совпадение ловится по СЧЁТУ ваших пробежек: №N в таблице — это ваша N-я пробежка. "
            "Первая строка — следующая пробежка, вторая — та, что через одну, и так далее. "
            "Поедете на старт не из списка — счёт сдвинется, и целевым станет следующий номер."
        ),
    ),
}


def build_start_numbers_plan(
    db: Session,
    user_id: UUID,
    *,
    code: str,
    platform_code: str | None = None,
    today: date | None = None,
) -> dict[str, object]:
    """Таблица планирования: строка — номер старта, колонки — ближайшие забеги
    локаций, в ячейках сами локации с датами.

    У «Нумератора» строки идут сплошным диапазоном, а колонок три: E, E+1, E+2 —
    номер известен на три недели вперёд с приличной точностью. У числовых
    челленджей (Фибоначчи, Нельсон, простые, Уилсон) строки разрежены, а до
    нужного номера площадке бывает полгода, поэтому у них одна колонка «Где и
    когда» на весь горизонт (см. PLAN_SPECS).

    Номер закрывается пробежкой В ЛЮБОЙ системе (см. _start_numbers_range_challenge),
    поэтому `done` считаем по номерам без привязки к платформе, а систему
    предсказанного старта показываем в ячейке — она подсказывает, куда ехать.

    platform_code повторяет фильтр систем со страницы достижений: когда там
    выбраны только 5 вёрст, и сам челлендж, и это планирование считаются по
    одной системе, иначе таблица предлагала бы старты, которые в текущем
    скоупе всё равно не засчитаются.
    """
    spec = PLAN_SPECS.get(code)
    if spec is None:
        raise StartNumberPlanError(f"У челленджа «{code}» нет планирования по номерам стартов")

    today = today or date.today()
    my_rows = _collect_run_rows(db, user_id)
    if platform_code:
        my_rows = [row for row in my_rows if row.platform_code == platform_code]
    # Номера строк «Совпадения номеров» зависят от того, сколько пробежек уже
    # набрано, поэтому список считается ПОСЛЕ сужения по системе — под фильтром
    # «только 5 вёрст» челлендж считает свои пробежки тем же способом.
    numbers = spec.numbers(len(my_rows))
    done_numbers = {row.event_number for row in my_rows if row.event_number is not None} if spec.tracks_done else set()

    wanted = set(numbers)
    high = max(numbers)
    cells: dict[int, list[list[dict[str, object]]]] = {}
    for item in _predict_upcoming_starts(db, today=today, weeks=spec.weeks, max_number=high):
        if item.number not in wanted:
            continue
        if platform_code and item.platform_code != platform_code:
            continue
        column = min(item.week_index // spec.weeks_per_column, spec.columns - 1)
        row_cells = cells.setdefault(item.number, [[] for _ in range(spec.columns)])
        row_cells[column].append(
            {
                "location": item.location_name,
                "location_slug": item.location_slug,
                "platform_code": item.platform_code,
                "date": item.event_date.isoformat(),
            }
        )
    for row_cells in cells.values():
        for column_index, week_cell in enumerate(row_cells):
            week_cell.sort(key=lambda entry: (str(entry["date"]), str(entry["location"])))
            # Разрежённым челленджам до одного номера доходят десятки площадок в
            # одну и ту же субботу — показываем ближайшие, иначе таблица
            # превращается в простыню на несколько экранов.
            del week_cell[spec.entries_per_cell :]
            row_cells[column_index] = week_cell

    rows = [
        {
            "number": number,
            "done": number in done_numbers,
            "weeks": cells.get(number) or [[] for _ in range(spec.columns)],
        }
        for number in numbers
    ]
    return {
        "code": code,
        "platform_code": platform_code,
        "low": min(numbers),
        "high": high,
        "generated_for": today.isoformat(),
        "week_count": spec.columns,
        # Подписи колонок и горизонта задаёт бэк: у «Нумератора» это забеги
        # локации (E, E+1, E+2), у остальных — одна колонка на полгода.
        "column_titles": list(spec.column_titles),
        "horizon_label": spec.horizon_label,
        "tracks_done": spec.tracks_done,
        "intro": spec.intro,
        "rows": rows,
    }


def _resolve_home_country(db: Session, user_id: UUID, rows: list[RunRow]) -> str | None:
    """Страна домашней локации — точка отсчёта «Международного туриста» (Ч51).

    Домашняя локация та же, от которой считается «дальность от дома»
    (resolve_home_location): выбранная человеком вручную либо посчитанная
    автоматически. Это важно не ради красоты: у нас есть площадки в Сербии,
    Беларуси, Грузии и Аргентине, и для бегуна из Белграда международным
    стартом является российский, а не наоборот.

    Пока страна у человека одна, тяжёлый разбор домашней локации не нужен —
    дом заведомо в ней. Разбор включается только у тех, кто бегал больше чем в
    одной стране (на прод-масштабной базе это 13% зарегистрированных).
    """
    counts = Counter(row.country for row in _finished_rows(rows) if row.country)
    if len(counts) <= 1:
        return next(iter(counts), None)

    from app.services.home_location_service import resolve_home_location

    user = db.get(User, user_id)
    if user is not None:
        candidate, _is_auto = resolve_home_location(db, user)
        if candidate is not None:
            home_country = next(
                (row.country for row in rows if row.location_key == candidate.catalog_identity_key and row.country),
                None,
            )
            if home_country is not None:
                return home_country
    # Домашняя локация не опознана (например, у неё не заполнена страна) —
    # берём страну, где финишей больше всего: это тот же принцип «дом там, где
    # бегаешь чаще», только огрублённый до страны.
    return counts.most_common(1)[0][0]


def compute_challenges(db: Session, user_id: UUID, platform_code: str | None = None) -> dict[str, object]:
    """platform_code сужает челленджи/бейджи/summary до одной системы —
    клубы (сквозные по конструкции — overall + по каждой платформе сразу)
    этим фильтром не затрагиваются и всегда считаются по полным данным."""
    rows = _collect_run_rows(db, user_id)
    vol_rows = _collect_volunteer_rows(db, user_id)
    # Прогноз ближайших стартов строится ОДИН раз на полгода вперёд, а дальше
    # режется на два представления: трёхнедельное для «Нумератора» и полное для
    # числовых челленджей. Запрос по последним стартам всех площадок не из
    # дешёвых, второй раз его гонять незачем.
    predictions = _predict_upcoming_starts(db, weeks=PLANNING_WEEKS, max_number=PLANNING_MAX_NUMBER)
    upcoming = _numbers_from_predictions(
        predictions, weeks=START_NUMBER_PLAN_WEEKS, max_number=START_NUMBER_RANGES["start_numbers_pro"][1]
    )
    planned = _numbers_from_predictions(predictions, weeks=PLANNING_WEEKS, max_number=PLANNING_MAX_NUMBER)
    rating_rows = _collect_rating_rows(db, user_id)
    vol_role_rows = _collect_volunteer_role_rows(db, user_id)
    weather_days = _collect_weather_days(db, user_id, rows)

    (
        scoped_rows,
        scoped_vol_rows,
        scoped_upcoming,
        scoped_rating_rows,
        scoped_role_rows,
        scoped_weather_days,
    ) = _scope_by_platform(rows, vol_rows, upcoming, rating_rows, vol_role_rows, weather_days, platform_code)
    # Прогноз для числовых челленджей сужается тем же фильтром систем: под
    # «только 5 вёрст» подсказка не должна звать на старт S95.
    scoped_planned = (
        planned if platform_code is None else {key: value for key, value in planned.items() if key[0] == platform_code}
    )

    # Каталог букв «Алфавита» зависит от того же фильтра систем — читаем его
    # один раз на оба прогона списка челленджей (второй считает recent_delta).
    alphabet_names = _alphabet_available_names(db, platform_code)
    # Домашняя страна «Международного туриста» считается по ПОЛНЫМ пробежкам,
    # а не по суженным фильтром: дом от выбора системы не переезжает.
    home_country = _resolve_home_country(db, user_id, rows)

    challenges = _build_challenge_list(
        scoped_rows,
        scoped_vol_rows,
        scoped_upcoming,
        scoped_rating_rows,
        scoped_role_rows,
        scoped_weather_days,
        alphabet_names=alphabet_names,
        platform_code=platform_code,
        home_country=home_country,
        planned=scoped_planned,
    )

    rows_before = _rows_before_last_activity(scoped_rows)
    if rows_before is not None and len(rows_before) < len(scoped_rows):
        # Тот самый последний день активности, относительно которого считается
        # recent_delta: «Детали» подсветят по нему свежие клетки.
        recent_date = scoped_rows[-1].event_date.isoformat()
        # Тем же днём отсекаем и погодные выходы: иначе «+1 за последний старт»
        # считался бы против списка, в котором этот старт уже учтён.
        cutoff = scoped_rows[-1].event_date
        weather_days_before = [day for day in scoped_weather_days if day.event_date < cutoff]
        previous: dict[str, int] = {
            str(c["code"]): int(c["current"])  # type: ignore[call-overload]
            for c in _build_challenge_list(
                rows_before,
                scoped_vol_rows,
                scoped_upcoming,
                scoped_rating_rows,
                scoped_role_rows,
                weather_days_before,
                alphabet_names=alphabet_names,
                platform_code=platform_code,
                home_country=home_country,
                planned=scoped_planned,
            )
        }
        for challenge in challenges:
            code = str(challenge["code"])
            delta = int(challenge["current"]) - previous.get(code, int(challenge["current"]))  # type: ignore[call-overload]
            challenge["recent_delta"] = max(delta, 0)
            challenge["recent_date"] = recent_date

    def _tier(challenge: dict[str, object], tier_key: object) -> dict[str, object]:
        tiers = challenge["tiers"]
        assert isinstance(tiers, list)
        return next(t for t in tiers if t["tier"] == tier_key)

    summary = Counter(challenge["best_level"] for challenge in challenges if challenge["best_level"])
    badges = [
        {
            "code": challenge["code"],
            "title": challenge["title"],
            "icon": challenge["icon"],
            "level": challenge["best_level"],
            "tier": challenge["best_tier"],
            "tier_label": _tier(challenge, challenge["best_tier"])["label"],
            "achieved_at": _tier(challenge, challenge["best_tier"])["level_dates"].get(  # type: ignore[attr-defined]
                str(challenge["best_level"])
            ),
        }
        for challenge in sorted(
            (c for c in challenges if c["best_level"]),
            key=lambda c: LEVEL_ORDER.index(str(c["best_level"])),
            reverse=True,
        )
    ]
    return {
        "challenges": challenges,
        "badges": badges,
        "summary": {
            "gold": summary.get("gold", 0),
            "silver": summary.get("silver", 0),
            "bronze": summary.get("bronze", 0),
            "total": len(challenges),
        },
        "clubs": _compute_clubs(rows, vol_rows, _parkrun_volunteer_total(db, user_id)),
    }


# ---------------------------------------------------------------------------
# Цели на год


@dataclass(frozen=True)
class GoalPreset:
    title: str
    icon: str
    unit: str
    # count | time | streak
    kind: str
    default_target: int
    min: int
    max: int
    description: str


GOAL_PRESETS: dict[str, GoalPreset] = {
    "runs_year": GoalPreset(
        title="Пробежки за год",
        icon="🏃",
        unit="пробежек",
        kind="count",
        default_target=50,
        min=1,
        max=200,
        description="Сколько парковых пробежек пробежать в этом году.",
    ),
    "volunteering_year": GoalPreset(
        title="Волонтёрства за год",
        icon="💚",
        unit="волонтёрств",
        kind="count",
        default_target=12,
        min=1,
        max=200,
        description="Сколько раз помочь на стартах в этом году.",
    ),
    "new_locations_year": GoalPreset(
        title="Новые локации",
        icon="🗺️",
        unit="локаций",
        kind="count",
        default_target=10,
        min=1,
        max=100,
        description="Открыть новые парки — локации, где ты ещё не финишировал.",
    ),
    "new_regions_year": GoalPreset(
        title="Новые регионы",
        icon="🧭",
        unit="регионов",
        kind="count",
        default_target=3,
        min=1,
        max=50,
        description="Пробежать в регионах, где ты ещё не бегал.",
    ),
    "finish_under": GoalPreset(
        title="Выбежать из времени",
        icon="⏱️",
        unit="",
        kind="time",
        default_target=25 * 60,
        min=12 * 60,
        max=90 * 60,
        description="Финишировать быстрее целевого времени хотя бы раз за год.",
    ),
    "saturday_streak": GoalPreset(
        title="Серия суббот",
        icon="🔥",
        unit="суббот подряд",
        kind="streak",
        default_target=10,
        min=2,
        max=52,
        description="Собрать серию суббот подряд — пробежкой или волонтёрством.",
    ),
    "saturday_consistency_year": GoalPreset(
        title="Регулярность",
        icon="📆",
        unit="%",
        kind="percent",
        default_target=50,
        min=10,
        max=100,
        description="Будь активен минимум в такую долю суббот года — пробежкой или волонтёрством, не обязательно подряд.",
    ),
    "pr_count_year": GoalPreset(
        title="Личные рекорды",
        icon="🏆",
        unit="рекордов",
        kind="count",
        default_target=3,
        min=1,
        max=20,
        description="Сколько раз обновить личный рекорд на платформе в этом году.",
    ),
}


def _year_fraction_elapsed(today: date) -> float:
    year_start = date(today.year, 1, 1)
    year_end = date(today.year, 12, 31)
    total_days = (year_end - year_start).days + 1
    elapsed = (today - year_start).days + 1
    return max(min(elapsed / total_days, 1.0), 1 / total_days)


def _saturdays_left(today: date) -> int:
    year_end = date(today.year, 12, 31)
    next_saturday = today + timedelta(days=(5 - today.weekday()) % 7)
    if next_saturday > year_end:
        return 0
    return (year_end - next_saturday).days // 7 + 1


def _saturdays_of_year(year: int) -> list[date]:
    start = date(year, 1, 1)
    end = date(year, 12, 31)
    days: list[date] = []
    current = start
    while current <= end:
        if current.weekday() == 5:
            days.append(current)
        current += timedelta(days=1)
    return days


def _year_activity_dates(year: int, rows: list[RunRow], vol_rows: dict[str, list[tuple[date, str]]]) -> set[date]:
    year_dates = {row.event_date for row in rows if row.event_date.year == year}
    for code, platform_rows in vol_rows.items():
        year_dates |= {
            d for d in volunteer_occasion_dates(code, [(d, key) for d, key in platform_rows if d.year == year])
        }
    return year_dates


def _preset_current(
    goal_type: str,
    year: int,
    *,
    rows: list[RunRow],
    vol_rows: dict[str, list[tuple[date, str]]],
    today: date,
) -> tuple[int, str | None]:
    """Текущее значение метрики пресета за год — НЕ зависит от target_value,
    поэтому годится и для активных целей, и для превью ещё не выбранных
    пресетов в модалке настройки."""
    year_rows = [row for row in rows if row.event_date.year == year]

    if goal_type == "runs_year":
        return len(year_rows), None
    if goal_type == "volunteering_year":
        year_vol = {
            code: [(d, key) for d, key in platform_rows if d.year == year] for code, platform_rows in vol_rows.items()
        }
        return _count_volunteering(year_vol), None
    if goal_type == "new_locations_year":
        first_visits: dict[str, date] = {}
        for row in rows:
            if row.location_key not in first_visits or row.event_date < first_visits[row.location_key]:
                first_visits[row.location_key] = row.event_date
        return sum(1 for value in first_visits.values() if value.year == year), None
    if goal_type == "new_regions_year":
        first_regions: dict[str, date] = {}
        for row in rows:
            if not row.region:
                continue
            region = _canonical_region(row.region)
            if region not in first_regions or row.event_date < first_regions[region]:
                first_regions[region] = row.event_date
        return sum(1 for value in first_regions.values() if value.year == year), None
    if goal_type == "finish_under":
        times = [row.finish_time_sec for row in year_rows if row.finish_time_sec]
        best = min(times) if times else None
        return (best or 0), (_time_display(best) if best else None)
    if goal_type == "saturday_streak":
        year_dates = _year_activity_dates(year, rows, vol_rows)
        return max_saturday_streak(year_dates), None
    if goal_type == "pr_count_year":
        return sum(1 for row in year_rows if row.is_pr), None
    if goal_type == "saturday_consistency_year":
        year_dates = _year_activity_dates(year, rows, vol_rows)
        all_saturdays = _saturdays_of_year(year)
        active_weeks = saturday_weeks(year_dates)
        active_saturdays = sum(1 for day in all_saturdays if day in active_weeks)
        # Текущий темп: доля АКТИВНЫХ суббот среди уже ПРОШЕДШИХ (не всего года) —
        # иначе в январе даже идеальная регулярность показывала бы единицы процентов.
        elapsed_saturdays = max(sum(1 for day in all_saturdays if day <= today), 1)
        return active_saturdays, f"{round(active_saturdays / elapsed_saturdays * 100)}%"
    return 0, None  # pragma: no cover — неизвестный пресет отфильтрован на записи


def _goal_progress(
    goal: UserGoal,
    *,
    rows: list[RunRow],
    vol_rows: dict[str, list[tuple[date, str]]],
    today: date,
    rows_before: list[RunRow] | None = None,
) -> dict[str, object]:
    preset = GOAL_PRESETS[goal.goal_type]
    year = goal.year
    current, current_display = _preset_current(goal.goal_type, year, rows=rows, vol_rows=vol_rows, today=today)
    on_track: bool | None = None
    forecast_value: int | None = None
    target_display: str | None = None

    # Насколько последняя пробежка продвинула именно эту цель — считается
    # одинаково для всех типов, кроме finish_under (там меньше = лучше).
    recent_delta = 0
    if rows_before is not None:
        current_before, _ = _preset_current(goal.goal_type, year, rows=rows_before, vol_rows=vol_rows, today=today)
        if goal.goal_type == "finish_under":
            recent_delta = max(current_before - current, 0) if current_before > 0 and current > 0 else 0
        else:
            recent_delta = max(current - current_before, 0)

    if goal.goal_type == "finish_under":
        best = current or None
        target_display = _time_display(goal.target_value)
        if best is None:
            pct = 0.0
        elif best <= goal.target_value:
            pct = 100.0
        else:
            pct = round(goal.target_value / best * 100, 1)
        done = best is not None and best <= goal.target_value
        return _goal_payload(
            goal, preset, current, pct, done, on_track, forecast_value, current_display, target_display, recent_delta
        )

    if goal.goal_type == "saturday_streak":
        year_dates = _year_activity_dates(year, rows, vol_rows)
        done = current >= goal.target_value
        # Достижима ли ещё цель: живая серия (заканчивающаяся последней субботой)
        # плюс оставшиеся субботы года.
        live_streak = 0
        if not done:
            year_saturdays = saturday_weeks(year_dates)
            last_saturday = today - timedelta(days=(today.weekday() - 5) % 7)
            expected = last_saturday
            if expected not in year_saturdays:
                expected -= timedelta(days=7)
            while expected in year_saturdays and expected.year == year:
                live_streak += 1
                expected -= timedelta(days=7)
            on_track = live_streak + _saturdays_left(today) >= goal.target_value
        pct = round(min(current / goal.target_value, 1.0) * 100, 1)
        return _goal_payload(goal, preset, current, pct, done, on_track, forecast_value, None, None, recent_delta)

    if goal.goal_type == "saturday_consistency_year":
        all_saturdays = _saturdays_of_year(year)
        total_saturdays = max(len(all_saturdays), 1)
        target_count = max(1, round(goal.target_value / 100 * total_saturdays))
        pct = round(min(current / target_count, 1.0) * 100, 1)
        done = current >= target_count
        if not done:
            remaining = sum(1 for day in all_saturdays if day > today)
            on_track = current + remaining >= target_count
        target_display = f"{goal.target_value}%"
        return _goal_payload(
            goal, preset, current, pct, done, on_track, forecast_value, current_display, target_display, recent_delta
        )

    # count-цели: прогресс + линейный прогноз к концу года
    pct = round(min(current / goal.target_value, 1.0) * 100, 1)
    done = current >= goal.target_value
    if not done and today.year == year:
        forecast_value = round(current / _year_fraction_elapsed(today))
        on_track = forecast_value >= goal.target_value
    return _goal_payload(goal, preset, current, pct, done, on_track, forecast_value, None, None, recent_delta)


def _goal_payload(
    goal: UserGoal,
    preset: GoalPreset,
    current: int,
    pct: float,
    done: bool,
    on_track: bool | None,
    forecast_value: int | None,
    current_display: str | None,
    target_display: str | None,
    recent_delta: int = 0,
) -> dict[str, object]:
    return {
        "goal_type": goal.goal_type,
        "year": goal.year,
        "target_value": goal.target_value,
        "title": preset.title,
        "icon": preset.icon,
        "unit": preset.unit,
        "kind": preset.kind,
        "current_value": current,
        "pct": pct,
        "done": done,
        "on_track": on_track,
        "forecast_value": forecast_value,
        "current_display": current_display,
        "target_display": target_display,
        # Насколько последняя пробежка продвинула цель (0, если не продвинула
        # или дельта не считалась — см. rows_before в _goal_progress).
        "recent_delta": recent_delta,
    }


def _presets_payload(
    year: int, *, rows: list[RunRow], vol_rows: dict[str, list[tuple[date, str]]], today: date
) -> list[dict[str, object]]:
    result = []
    for code, preset in GOAL_PRESETS.items():
        current_value, current_display = _preset_current(code, year, rows=rows, vol_rows=vol_rows, today=today)
        result.append(
            {
                "goal_type": code,
                "title": preset.title,
                "icon": preset.icon,
                "unit": preset.unit,
                "kind": preset.kind,
                "default_target": preset.default_target,
                "min": preset.min,
                "max": preset.max,
                "description": preset.description,
                "current_value": current_value,
                "current_display": current_display,
            }
        )
    return result


def get_goals_payload(db: Session, user_id: UUID, *, today: date | None = None) -> dict[str, object]:
    today = today or date.today()
    year = today.year
    goals = (
        db.query(UserGoal)
        .filter(UserGoal.user_id == user_id, UserGoal.year == year)
        .order_by(UserGoal.created_at)
        .all()
    )
    rows = _collect_run_rows(db, user_id)
    vol_rows = _collect_volunteer_rows(db, user_id)
    rows_before = _rows_before_last_activity(rows)
    return {
        "year": year,
        "max_goals": len(GOAL_PRESETS),
        "goals": [
            _goal_progress(goal, rows=rows, vol_rows=vol_rows, today=today, rows_before=rows_before)
            for goal in goals
            if goal.goal_type in GOAL_PRESETS
        ],
        "presets": _presets_payload(year, rows=rows, vol_rows=vol_rows, today=today),
    }


class GoalValidationError(ValueError):
    pass


def save_goals(
    db: Session,
    user_id: UUID,
    goals: list[tuple[str, int]],
    *,
    today: date | None = None,
) -> dict[str, object]:
    """Заменяет набор целей пользователя на текущий год."""
    today = today or date.today()
    year = today.year
    if len(goals) > len(GOAL_PRESETS):
        raise GoalValidationError("Нельзя выбрать больше целей, чем есть пресетов")
    seen: set[str] = set()
    for goal_type, target_value in goals:
        preset = GOAL_PRESETS.get(goal_type)
        if preset is None:
            raise GoalValidationError(f"Неизвестная цель: {goal_type}")
        if goal_type in seen:
            raise GoalValidationError(f"Цель повторяется: {goal_type}")
        seen.add(goal_type)
        if not (preset.min <= target_value <= preset.max):
            raise GoalValidationError(f"Значение цели «{preset.title}» должно быть от {preset.min} до {preset.max}")

    existing = {
        goal.goal_type: goal
        for goal in db.query(UserGoal).filter(UserGoal.user_id == user_id, UserGoal.year == year).all()
    }
    requested = dict(goals)
    for goal_type, goal in existing.items():
        if goal_type not in requested:
            db.delete(goal)
        elif goal.target_value != requested[goal_type]:
            goal.target_value = requested[goal_type]
    for goal_type, target_value in requested.items():
        if goal_type not in existing:
            db.add(UserGoal(user_id=user_id, year=year, goal_type=goal_type, target_value=target_value))
    db.commit()
    return get_goals_payload(db, user_id, today=today)
