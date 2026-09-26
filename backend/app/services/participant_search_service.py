"""Поиск участников по ФИО во всех системах — для онбординга и привязки профилей.

Ищем по participants.display_name (регистронезависимо, каждое слово запроса —
подстрока имени, порядок слов не важен: «Попов Дмитрий» == «Дмитрий Попов»).
«Е» и «ё» не различаем с обеих сторон: имя сворачивается выражением
translate(lower(display_name), 'ё', 'е'), и под ним GIN-индекс pg_trgm
(миграция 103; до неё индекс был на lower(display_name), миграция 068).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from datetime import date
from typing import Any
from uuid import UUID

from sqlalchemy import and_, case, func, not_, or_, select, text, union_all
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.models import Event, Location, Participant, Platform, PlatformLink, RunResult, User, VolunteerResult
from app.parkrun.volunteer_credits import count_parkrun_volunteering
from app.runpark.mappings import runpark_profile_url
from app.services.co_runners_service import _is_unknown_participant_name
from app.services.location_catalog_service import PARKRUN_PLATFORM_CODE

MIN_QUERY_LENGTH = 3
MAX_QUERY_LENGTH = 100
# Сколько строк-кандидатов достаём из БД до ранжирования и обрезки.
CANDIDATE_LIMIT = 200
RESULT_LIMIT = 30
# Сколько слов запроса ищем по имени. Каждое слово — условие на имя и по две-три
# регулярки ранжирования на КАЖДУЮ строку-кандидата, поэтому число слов и есть
# множитель стоимости запроса: «ова», повторённое 25 раз, грузило базу на пять
# секунд (ревью 26.09.2026, SRCH-DOS-1). Имя с отчеством и городом — четыре слова.
MAX_QUERY_WORDS = 4
# Потолок на один SQL-запрос поиска людей. Поиск ограничен по стоимости и так
# (пул кандидатов, число слов), это предохранитель: запрос, который всё же
# ушёл в долгий план, умирает сам и не держит соединение из пула, пока сайт
# ждёт свободного (5+10 на процесс, pool_timeout 10 с).
SEARCH_STATEMENT_TIMEOUT_MS = 2000
# SQLSTATE query_canceled — так Postgres отвечает на истёкший statement_timeout.
_QUERY_CANCELED_SQLSTATE = "57014"


class ParticipantSearchError(Exception):
    def __init__(self, message: str, status_code: int = 400) -> None:
        self.message = message
        self.status_code = status_code
        super().__init__(message)


@dataclass(frozen=True)
class ParticipantSearchActivity:
    kind: str  # "run" | "volunteer"
    event_date: date
    location_name: str
    finish_time_display: str | None
    role: str | None


@dataclass(frozen=True)
class ParticipantSearchResult:
    participant_id: UUID
    platform_code: str
    platform_name: str
    display_name: str
    club_name: str | None
    age_category: str | None
    profile_url: str | None
    total_runs: int
    total_volunteering: int
    last_run_date: date | None
    home_location_name: str | None
    home_location_city: str | None
    already_linked: bool
    linked_to_me: bool
    recent_activities: list[ParticipantSearchActivity]


@dataclass(frozen=True)
class ParticipantSearchPage:
    query: str
    results: list[ParticipantSearchResult]
    truncated: bool
    # Привязанные системы, где по запросу есть совпадения, скрытые из выдачи.
    # Кто именно нашёлся — не раскрываем, только факт и код системы.
    hidden_linked_platform_codes: list[str]


# Код участника: «A7035519» (штрихкод из QR любой системы) или просто цифры
# (номер участника 5 вёрст / parkrun / С95). Минимум 3 цифры, чтобы не путать
# с короткими именами. Кириллическую «А» принимаем наравне с латинской: на
# русской раскладке её набирают, не глядя, а штрихкод от этого не меняется.
_IDENTIFIER_RE = re.compile(r"^[AaАа]?(\d{3,16})$")


def _escape_like(term: str) -> str:
    return term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _query_words(raw_query: str) -> list[str]:
    return [word for word in raw_query.split() if word]


def strip_control_chars(text: str) -> str:
    """Текст без управляющих и невидимых символов (пробельные остаются).

    Нулевой байт из адреса (?q=ab%00cd) доходил до LIKE-параметра, и psycopg
    отвечал DataError — публичный поиск отдавал 500 и трейсбек в лог на каждый
    такой запрос (ревью, NUL-6). Одиночный суррогат из кривого %-кода так же
    не кодируется в UTF-8. Живой человек ни того, ни другого не наберёт,
    поэтому просто выбрасываем всё непечатаемое: управляющие, форматные
    (мягкий перенос, нулевой ширины), суррогаты.
    """
    return "".join(char for char in text if char.isprintable() or char.isspace())


def normalize_query_text(raw_query: str) -> str:
    """Запрос без управляющих символов, крайних и сдвоенных пробелов — то, что реально ищем и показываем."""
    return " ".join(_query_words(strip_control_chars(raw_query or "")))


# Знаки по краям слова: не буквы и не цифры (подчёркивание — тоже знак).
_EDGE_PUNCTUATION_RE = re.compile(r"^[\W_]+|[\W_]+$")


def trim_word(word: str) -> str:
    """Слово без знаков по краям: «Попов,» → «Попов», «Д.» → «Д», «%%%» → «».

    Знаки внутри слова остаются («Римского-Корсакова», «О'Нил»).
    """
    return _EDGE_PUNCTUATION_RE.sub("", word)


def significant_words(words: list[str], limit: int = MAX_QUERY_WORDS) -> list[str]:
    """Слова запроса для поиска по имени: без знаков по краям, без повторов и не больше limit.

    Слово из одних знаков («%%%», «___», «...», «№№№») выбрасываем: в именах
    его нет, а триграмм у него нет — индекс ему не помогает, и каждый ярус
    пула кандидатов шёл полным проходом по всем участникам, 0,5–0,7 с на
    запрос (ревью, SKEP-4). Знак по краю («Попов,», «Д.») — опечатка, а не
    часть имени: «Попов, Дмитрий» раньше не находил Попова вовсе.

    Повтор слова ничего не добавляет к условию (подстрока в имени уже
    требуется), а стоимость умножает. Если слов больше предела, оставляем
    самые длинные — они избирательнее и по ним человека и ищут («а б в г
    Иванов» — это Иванов); порядок слов сохраняем, он важен поиску «имя +
    место».
    """
    unique: list[str] = []
    seen: set[str] = set()
    for raw_word in words:
        word = trim_word(raw_word)
        key = fold_name_text(word)
        if key and key not in seen:
            seen.add(key)
            unique.append(word)
    if len(unique) <= limit:
        return unique
    keep = sorted(range(len(unique)), key=lambda index: (-len(unique[index]), index))[:limit]
    return [unique[index] for index in sorted(keep)]


def prepare_search_transaction(db: Session) -> None:
    """Настройки транзакции поиска людей: потолок времени, без JIT и без хэш-соединений.

    - statement_timeout = SEARCH_STATEMENT_TIMEOUT_MS на каждый запрос.
    - JIT выключен: планировщик сильно завышает стоимость запросов поиска
      (LATERAL «бегал в этом месте», частые сочетания букв), и Postgres их
      компилировал — на стенде 0,57 с компиляции на запрос, который сам
      выполняется за 0,1 с.
    - Хэш-соединения выключены. Все запросы поиска — выборки по короткому
      списку id (кандидаты, показанные строки), но планировщик ждёт на
      каждого участника 45 пробежек вместо 7 (n_distinct у
      run_results.participant_id — 51 тысяча при 327 тысячах настоящих) и
      ради пары тысяч строк строил хэш по всем 178 тысячам событий: 80 мс на
      каждый подсчёт пробежек, дважды на запрос, против 15–30 мс вложенным
      циклом по индексам. Потолок сверху — тот же statement_timeout.

    SET LOCAL, а не SET: соединение вернётся в пул без этих настроек, как
    только сессия запроса откатится или закоммитится.
    """
    db.execute(text(f"SET LOCAL statement_timeout = {int(SEARCH_STATEMENT_TIMEOUT_MS)}"))
    db.execute(text("SET LOCAL jit = off"))
    db.execute(text("SET LOCAL enable_hashjoin = off"))


def is_statement_timeout(exc: BaseException) -> bool:
    """Запрос отменён по statement_timeout (а не упал по другой причине)."""
    return isinstance(exc, OperationalError) and getattr(exc.orig, "sqlstate", None) == _QUERY_CANCELED_SQLSTATE


def fold_name_text(text: str | None) -> str:
    """Текст для сравнения имён: нижний регистр и «ё» → «е».

    Ровно то же, что делает с именем SQL-выражение folded_display_name(): в
    протоколах «Фёдоров» и «Федоров» пишут как попало, и без свёртки с обеих
    сторон человек с «ё» в фамилии не находился ни через «е», ни через «ё».
    """
    return (text or "").lower().replace("ё", "е")


def folded_display_name():
    """translate(lower(display_name), 'ё', 'е') — под этим выражением индекс (миграция 103)."""
    return func.translate(func.lower(Participant.display_name), "ё", "е")


def apply_name_filters(query, words: list[str]):
    """Каждое слово запроса — подстрока имени (регистронезависимо, порядок не важен).

    Условие написано ровно над folded_display_name(), чтобы его подхватил
    GIN-индекс pg_trgm ix_participants_display_name_fold_trgm. Общее для
    онбординга и публичного поиска на сайте.
    """
    for word in words:
        query = query.filter(substring_filter(word))
    return query


def substring_filter(word: str):
    """Условие «word — подстрока имени» (без регистра и без различия «е»/«ё»)."""
    return folded_display_name().like(f"%{_escape_like(fold_name_text(word))}%", escape="\\")


def word_start_regex(word: str, *, whole: bool = False) -> str:
    """POSIX-регулярка «слово имени начинается с word» (whole — и им же кончается)."""
    return _word_start_pattern(word, whole=whole)


def _word_start_pattern(word: str, *, whole: bool) -> str:
    # POSIX-регулярка Postgres: слово имени начинается с начала строки, после
    # пробела или дефиса («Римского-Корсакова»). re.escape годится и для неё:
    # экранирует только не-буквы, а обратная косая перед не-буквой в ARE —
    # просто сам этот символ.
    escaped = re.escape(fold_name_text(word))
    tail = "($|[[:space:]-])" if whole else ""
    return f"(^|[[:space:]-]){escaped}{tail}"


def word_start_rank(words: list[str]):
    """SQL-балл «насколько слова запроса совпали с началом слов имени».

    За каждое слово: 2 — совпало слово целиком («Ким» у Юлии Ким), 1 — с начала
    слова («Кимов»), 0 — только из середины («Хакимжанов», «Екимова»). Сортируем
    по нему В SQL, до LIMIT: кандидатов по частому сочетанию букв («лев» — это
    все Яковлевы и Королевы) тысячи, и без порядка база отдавала первые
    попавшиеся — нужный человек мог не попасть в выдачу вовсе.
    """
    folded = folded_display_name()
    score = None
    for word in words:
        part = case(
            (folded.op("~")(_word_start_pattern(word, whole=True)), 2),
            (folded.op("~")(_word_start_pattern(word, whole=False)), 1),
            else_=0,
        )
        score = part if score is None else score + part
    return score


def word_start_all(words: list[str]):
    """SQL: 1, если КАЖДОЕ слово запроса совпало с началом какого-то слова имени, иначе 0.

    Сумма word_start_rank этого не различает: у двух слов «2 + 0» (одно
    совпало целиком, другое — из середины) и «1 + 1» (оба с начала) балл
    одинаковый. А для выдачи важно именно второе: совпадения из середины
    идут ниже всех совпадений с начала слова.
    """
    folded = folded_display_name()
    return case(
        (and_(*(folded.op("~")(_word_start_pattern(word, whole=False)) for word in words)), 1),
        else_=0,
    )


def whole_word_filter(word: str):
    """Условие «слово имени целиком равно word» — для фамилий из двух букв.

    «Ли», «Ан», «Юн» как подстрока есть почти в каждом имени, поэтому такие
    запросы ищут только целое слово. Записано через LIKE с пробелами, а не
    регуляркой: такие шаблоны GIN-индекс pg_trgm разбирает на триграммы
    (« ли», «ли »), и запрос идёт по индексу — единицы миллисекунд вместо
    полного прохода по участникам (регулярка с границами слова индексом не
    покрывается). Имя из одного слова («Ли» без имени) не ищем: таких нет.
    """
    folded = folded_display_name()
    term = _escape_like(fold_name_text(word))
    patterns = (
        f"% {term} %",
        f"{term} %",
        f"% {term}",
        f"{term}-%",
        f"%-{term}",
        f"% {term}-%",
        f"%-{term} %",
    )
    return or_(*(folded.like(pattern, escape="\\") for pattern in patterns))


def _tier_condition(word: str, *, whole: bool):
    """Ярус пула: слово имени начинается с word (whole — и совпадает целиком).

    Регулярка, а не семь LIKE-шаблонов whole_word_filter: GIN-индекс pg_trgm
    проходит её один раз, а у шаблонов — по разу на каждый, хотя набор
    триграмм у них один и тот же («Дмитрий»: 20 мс против 3). Граница слова —
    явный класс «[ -]», а не «[[:space:]-]», как в ранжировании
    (_word_start_pattern): только из явного класса pg_trgm берёт триграммы
    начала слова («  о», « ов») и отсекает по индексу всех «…ова» из середины
    слова — у «ова» это 191 строка вместо 48 806. Имя с экзотическим пробелом
    (табуляция, неразрывный) от этого не теряется: оно попадает в ярус «в
    середине слова», а место в выдаче ему всё равно считает ранжирование.
    """
    escaped = re.escape(fold_name_text(word))
    tail = "($|[ -])" if whole else ""
    return folded_display_name().op("~")(f"(^|[ -]){escaped}{tail}")


def name_tier_conditions(substring_words: list[str]) -> tuple[Any, Any, Any]:
    """Условия трёх ярусов имени — (целиком, с начала слова, в середине), без пересечений.

    1. все слова совпали со словами имени целиком («Ким»);
    2. все слова совпали с началом слов имени, но не все целиком («Кимов»);
    3. остальные: хоть одно слово нашлось только в середине («Хакимов»).

    Каждый ярус идёт по триграммному индексу; почему условия именно такие —
    см. name_candidate_pool и _tier_condition.
    """
    whole = and_(*(_tier_condition(word, whole=True) for word in substring_words))
    start = and_(*(_tier_condition(word, whole=False) for word in substring_words))
    anywhere = and_(*(substring_filter(word) for word in substring_words))
    return whole, and_(start, not_(whole)), and_(anywhere, not_(start))


def name_candidate_pool(
    base_query: Any, substring_words: list[str], *, tier_limit: int, stop_when_full: bool = False
) -> Any:
    """Ограниченный пул id кандидатов по имени — ранжирование считается только по нему.

    Раньше ORDER BY по баллу (регулярки word_start_rank/word_start_all)
    считался по ВСЕМ строкам, подошедшим по LIKE: у «ова» это 48 806
    участников, 0,4–0,8 с на запрос и 5 с, если слово повторить (ревью
    26.09.2026, SRCH-DOS-1, search-heavy-anon). Теперь кандидаты берутся тремя
    ярусами, каждый не больше tier_limit строк, и все три идут по
    триграммному индексу:

    1. все слова совпали со словами имени целиком («Ким»);
    2. все слова совпали с началом слов имени, но не все целиком («Кимов»);
    3. остальные: хоть одно слово нашлось только в середине («Хакимов»).

    Ярусы идут ровно в том порядке, в каком выдача их ставит (целиком выше
    начала слова, начало выше середины), поэтому при tier_limit не меньше
    нужного числа кандидатов первые места те же, что дал бы ORDER BY по
    всей выборке. Разница — только внутри яруса, который больше предела: там
    берётся произвольная часть, как и раньше при равном балле.

    stop_when_full — нижний ярус не читать вовсе, если верхние уже набрали
    tier_limit строк: у «Дмитрий» целым словом семь тысяч имён, и ярусы «с
    начала слова» и «в середине» перебирали их все ради пары строк, которые
    всё равно не попали бы в выдачу. Условие стоит в SQL как одноразовый
    фильтр (One-Time Filter): ложное — Postgres ярус и не начинает.

    Условие «подстрока» есть только в третьем ярусе, и это нарочно. Postgres
    сильно недооценивает, сколько имён содержат частое сочетание («%ова%»:
    оценка 109 строк при 48 806 настоящих), и, увидев его рядом с условием
    «слово целиком», шёл по индексу именно за ним, а целое слово проверял
    фильтром на всех 48 тысячах строк — те же 0,4 с. Условия ярусов — см.
    _tier_condition.

    base_query — db.query(Participant.id) с условиями, кроме подстрок
    (система, заглушки, короткие слова). substring_words — слова, которые
    ищутся подстрокой имени: по ним и делятся ярусы.
    """
    if not substring_words:
        return base_query.limit(tier_limit * 3).subquery("name_pool")
    whole, start, middle = name_tier_conditions(substring_words)

    def taken(tier: Any) -> Any:
        return select(func.count()).select_from(tier).scalar_subquery()

    whole_tier = base_query.filter(whole).limit(tier_limit).cte("name_pool_whole")
    start_query = base_query.filter(start)
    if stop_when_full:
        start_query = start_query.filter(taken(whole_tier) < tier_limit)
    start_tier = start_query.limit(tier_limit).cte("name_pool_start")
    middle_query = base_query.filter(middle)
    if stop_when_full:
        middle_query = middle_query.filter(taken(whole_tier) + taken(start_tier) < tier_limit)
    middle_tier = middle_query.limit(tier_limit).subquery("name_pool_middle")
    return union_all(
        select(whole_tier.c.id), select(start_tier.c.id), select(middle_tier.c.id)
    ).subquery("name_pool")


def _apply_query_filters(query, words: list[str]):
    """Общие условия поиска: код участника (точно) или все слова имени (подстроки)."""
    identifier = _IDENTIFIER_RE.match(words[0]) if len(words) == 1 else None
    if identifier:
        # Ввели код участника — точное совпадение во всех системах:
        # и как штрихкод (A…), и как номер участника (цифры). Штрихкод сверяем
        # в обеих формах: часть кодов С95 лежит в базе без префикса «A»
        # (приехали из легаси голыми цифрами), и без этого свой же QR —
        # A770012057 — в поиске ничего не находил.
        digits = identifier.group(1)
        # Оба условия — по индексам: штрихкод по ix_participants_barcode_id
        # (без upper(): регистр — только у буквы «A», оба варианта в списке), номер
        # участника — по уникальному (система, номер), для чего системы названы
        # списком. С upper() и голым номером Postgres проверял все 341 тысячу
        # участников, а при выключенных хэш-соединениях поиска
        # (prepare_search_transaction) — ещё и слиянием по всему первичному
        # ключу: 0,33 с на один штрихкод.
        platform_ids = [platform_id for (platform_id,) in query.session.query(Platform.id)]
        return query.filter(
            or_(
                Participant.barcode_id.in_((f"A{digits}", f"a{digits}", digits)),
                and_(Participant.platform_id.in_(platform_ids), Participant.external_user_id == digits),
            )
        )
    return apply_name_filters(query, words)


def _name_candidates(
    db: Session, words: list[str], linked_platform_ids: list[UUID]
) -> list[tuple[Participant, Platform]]:
    """До CANDIDATE_LIMIT + 1 кандидатов: код участника — точно, имя — из ограниченного пула."""
    id_query = (
        db.query(Participant.id)
        .join(Platform, Participant.platform_id == Platform.id)
        .filter(Platform.is_active.is_(True), Participant.display_name.isnot(None))
    )
    if linked_platform_ids:
        id_query = id_query.filter(Participant.platform_id.notin_(linked_platform_ids))
    candidates_query = db.query(Participant, Platform).join(Platform, Participant.platform_id == Platform.id)
    is_identifier = len(words) == 1 and _IDENTIFIER_RE.match(words[0]) is not None
    if is_identifier:
        # Код участника находит единицы строк по точному равенству — ни пул,
        # ни ранжирование по имени ему не нужны.
        id_query = _apply_query_filters(id_query, words)
        return candidates_query.filter(Participant.id.in_(id_query)).limit(CANDIDATE_LIMIT + 1).all()
    # Совпадения с начала слова — первыми ещё в SQL: кандидатов берём
    # ограниченно, и «Ким» не должен тонуть в Хакимжановых. Балл считаем
    # только по пулу (name_candidate_pool): по всей выборке частого сочетания
    # букв («ова» — 48 тысяч имён) он стоил полсекунды на запрос. Каждое
    # слово онбординга — подстрока имени, пул делит на ярусы по всем.
    pool = name_candidate_pool(id_query, words, tier_limit=CANDIDATE_LIMIT + 1, stop_when_full=True)
    return (
        candidates_query.join(pool, pool.c.id == Participant.id)
        .order_by(word_start_rank(words).desc())
        .limit(CANDIDATE_LIMIT + 1)
        .all()
    )


def _hidden_linked_platform_codes(db: Session, words: list[str], linked_platform_ids: list[UUID]) -> list[str]:
    """Коды привязанных систем, где по запросу есть совпадения (они скрыты из выдачи).

    Совпадения в привязанных системах не показываем, но честно говорим, что
    они есть: иначе «никого не нашли» выглядит враньём (кейс Дмитрия: ввёл
    штрихкод, а система этого бегуна у него уже привязана). Нужен только факт,
    поэтому по каждой системе — первая подходящая строка, а не DISTINCT по
    всем совпадениям: у «ова» их десятки тысяч.
    """
    if not linked_platform_ids:
        return []
    codes: list[str] = []
    for platform_id, code in db.query(Platform.id, Platform.code).filter(
        Platform.id.in_(linked_platform_ids), Platform.is_active.is_(True)
    ):
        probe = db.query(Participant.id).filter(
            Participant.platform_id == platform_id, Participant.display_name.isnot(None)
        )
        if _apply_query_filters(probe, words).limit(1).first() is not None:
            codes.append(code)
    return sorted(codes)


def search_participants(db: Session, user: User, raw_query: str) -> ParticipantSearchPage:
    if not user.consent_accepted:
        raise ParticipantSearchError(
            "Сначала примите условия обработки персональных данных — после этого можно искать и привязывать профили.",
            403,
        )
    query_text = normalize_query_text(raw_query)
    if len(query_text) < MIN_QUERY_LENGTH:
        raise ParticipantSearchError("Введите минимум 3 символа имени или фамилии", 422)
    if len(query_text) > MAX_QUERY_LENGTH:
        raise ParticipantSearchError("Слишком длинный запрос", 422)

    try:
        prepare_search_transaction(db)
        return _search_page(db, user, query_text)
    except OperationalError as exc:
        if not is_statement_timeout(exc):
            raise
        db.rollback()
        raise ParticipantSearchError(
            "Поиск не уложился по времени — уточните запрос: имя и фамилию целиком.", 503
        ) from exc


def _search_page(db: Session, user: User, query_text: str) -> ParticipantSearchPage:
    # Повторы и лишние слова — не условия, а только множитель стоимости запроса.
    words = significant_words(_query_words(query_text))
    if not words:
        # Одни знаки («%%%», «---»): искать нечего. Без этой проверки пустой
        # список слов снял бы с поиска все условия на имя, и выдача состояла бы
        # из первых попавшихся участников.
        return ParticipantSearchPage(query=query_text, results=[], truncated=False, hidden_linked_platform_codes=[])
    # Системы, где профиль уже привязан, из поиска исключаем: искать там больше
    # нечего, а оставлять работающий поиск людей по чужим именам — нечестно.
    linked_platform_ids = [
        platform_id
        for (platform_id,) in db.query(PlatformLink.platform_id).filter(PlatformLink.user_id == user.id)
    ]
    candidates = _name_candidates(db, words, linked_platform_ids)
    hidden_linked_codes = _hidden_linked_platform_codes(db, words, linked_platform_ids)

    truncated_candidates = len(candidates) > CANDIDATE_LIMIT
    candidates = [
        (participant, platform)
        for participant, platform in candidates[:CANDIDATE_LIMIT]
        if not _is_unknown_participant_name(participant.display_name)
    ]
    if not candidates:
        return ParticipantSearchPage(
            query=query_text,
            results=[],
            truncated=truncated_candidates,
            hidden_linked_platform_codes=hidden_linked_codes,
        )

    participant_ids = [participant.id for participant, _ in candidates]
    run_stats = load_run_stats(db, participant_ids)
    volunteering_counts = load_volunteering_counts(db, candidates)
    home_locations = _load_home_locations(db, participant_ids)
    linked_owners = load_linked_owners(db, candidates)

    results: list[ParticipantSearchResult] = []
    for participant, platform in candidates:
        runs_count, last_run_date = run_stats.get(participant.id, (0, None))
        home = home_locations.get(participant.id)
        owner_id = linked_owners.get(participant.id)
        age_category = participant.age_category
        if platform.code == "parkrun":
            from app.parkrun.age_category import normalize_parkrun_age_group

            age_category = normalize_parkrun_age_group(age_category)
        profile_url = participant.profile_url
        if platform.code == "runpark" and not profile_url:
            # Страница кармы открывается только по идентификатору аккаунта;
            # у личности «barcode:A…» аккаунта нет и ссылки быть не должно
            # (как в platformProfileUrl на фронте).
            profile_url = runpark_profile_url(participant.external_user_id)
        results.append(
            ParticipantSearchResult(
                participant_id=participant.id,
                platform_code=platform.code,
                platform_name=platform.name,
                display_name=(participant.display_name or "").strip(),
                club_name=participant.club_name,
                age_category=age_category,
                profile_url=profile_url,
                total_runs=runs_count,
                total_volunteering=volunteering_counts.get(participant.id, 0),
                last_run_date=last_run_date,
                home_location_name=home[0] if home else None,
                home_location_city=home[1] if home else None,
                already_linked=owner_id is not None,
                linked_to_me=owner_id == user.id,
                recent_activities=[],
            )
        )

    results.sort(key=_rank_key)
    truncated = truncated_candidates or len(results) > RESULT_LIMIT
    top = results[:RESULT_LIMIT]
    # Последние события тянем только для показываемой верхушки — не для всех кандидатов.
    recents = _load_recent_activities(db, [item.participant_id for item in top])
    top = [replace(item, recent_activities=recents.get(item.participant_id, [])) for item in top]
    return ParticipantSearchPage(
        query=query_text,
        results=top,
        truncated=truncated,
        hidden_linked_platform_codes=hidden_linked_codes,
    )


def _rank_key(item: ParticipantSearchResult) -> tuple[int, int, int, str]:
    # Свежепробежавшие — выше (решение Дмитрия 25.08.2026): человек скорее
    # всего активен и ищет себя, а «пустые» однофамильцы без пробежек — внизу.
    return (
        0 if item.last_run_date is not None else 1,
        -(item.last_run_date.toordinal() if item.last_run_date is not None else 0),
        -item.total_runs,
        item.display_name.casefold(),
    )


RECENT_ACTIVITIES_LIMIT = 3


def _load_recent_activities(
    db: Session,
    participant_ids: list[UUID],
) -> dict[UUID, list[ParticipantSearchActivity]]:
    """Последние события участника (пробежки и волонтёрства вместе), по 3 на карточку."""
    if not participant_ids:
        return {}

    run_rn = (
        func.row_number()
        .over(partition_by=RunResult.participant_id, order_by=Event.event_date.desc())
        .label("rn")
    )
    runs_sq = (
        db.query(
            RunResult.participant_id.label("participant_id"),
            Event.event_date.label("event_date"),
            Location.name.label("location_name"),
            RunResult.finish_time_display.label("finish_time_display"),
            run_rn,
        )
        .join(Event, RunResult.event_id == Event.id)
        .join(Location, Event.location_id == Location.id)
        .filter(
            RunResult.participant_id.in_(participant_ids),
            Event.is_test_event.is_(False),
        )
        .subquery()
    )
    run_rows = (
        db.query(runs_sq)
        .filter(runs_sq.c.rn <= RECENT_ACTIVITIES_LIMIT)
        .all()
    )

    vol_rn = (
        func.row_number()
        .over(partition_by=VolunteerResult.participant_id, order_by=Event.event_date.desc())
        .label("rn")
    )
    vol_sq = (
        db.query(
            VolunteerResult.participant_id.label("participant_id"),
            Event.event_date.label("event_date"),
            Location.name.label("location_name"),
            VolunteerResult.role.label("role"),
            vol_rn,
        )
        .join(Event, VolunteerResult.event_id == Event.id)
        .join(Location, Event.location_id == Location.id)
        .filter(
            VolunteerResult.participant_id.in_(participant_ids),
            Event.is_test_event.is_(False),
            Event.event_date > date(1970, 1, 1),
        )
        .subquery()
    )
    vol_rows = (
        db.query(vol_sq)
        .filter(vol_sq.c.rn <= RECENT_ACTIVITIES_LIMIT)
        .all()
    )

    merged: dict[UUID, list[ParticipantSearchActivity]] = {}
    for row in run_rows:
        merged.setdefault(row.participant_id, []).append(
            ParticipantSearchActivity(
                kind="run",
                event_date=row.event_date,
                location_name=row.location_name,
                finish_time_display=row.finish_time_display,
                role=None,
            )
        )
    for row in vol_rows:
        merged.setdefault(row.participant_id, []).append(
            ParticipantSearchActivity(
                kind="volunteer",
                event_date=row.event_date,
                location_name=row.location_name,
                finish_time_display=None,
                role=row.role,
            )
        )
    return {
        participant_id: sorted(items, key=lambda a: a.event_date, reverse=True)[:RECENT_ACTIVITIES_LIMIT]
        for participant_id, items in merged.items()
    }


def load_run_stats(db: Session, participant_ids: list[UUID]) -> dict[UUID, tuple[int, date | None]]:
    rows = (
        db.query(
            RunResult.participant_id,
            func.count(RunResult.id),
            func.max(Event.event_date),
        )
        .join(Event, RunResult.event_id == Event.id)
        .filter(
            RunResult.participant_id.in_(participant_ids),
            Event.is_test_event.is_(False),
        )
        .group_by(RunResult.participant_id)
        .all()
    )
    return {participant_id: (count, last_date) for participant_id, count, last_date in rows}


def load_volunteering_counts(
    db: Session, candidates: list[tuple[Participant, Platform]]
) -> dict[UUID, int]:
    """Волонтёрств у каждого кандидата поиска.

    У parkrun считать строки нельзя: волонтёрская история приезжает из профиля
    атлета не событиями, а сводкой («Run Director (22×)»), и все её строки лежат
    на дате-заглушке 1970-01-01. Фильтр по дате отсекал их целиком, и карточка
    parkrun показывала «0 волонтёрств» человеку со 112 сменами (Дмитрий
    02.09.2026). Для parkrun берём тот же счётчик, что и кабинет, —
    count_parkrun_volunteering (parkrun называет это Total Credits).
    """
    participant_ids = [participant.id for participant, _ in candidates]
    if not participant_ids:
        return {}

    rows = (
        db.query(VolunteerResult.participant_id, func.count(VolunteerResult.id))
        .join(Event, VolunteerResult.event_id == Event.id)
        .filter(
            VolunteerResult.participant_id.in_(participant_ids),
            Event.is_test_event.is_(False),
            Event.event_date > date(1970, 1, 1),
        )
        .group_by(VolunteerResult.participant_id)
        .all()
    )
    counts: dict[UUID, int] = dict(rows)

    for participant, platform in candidates:
        if platform.code == PARKRUN_PLATFORM_CODE:
            counts[participant.id] = count_parkrun_volunteering(db, participant, platform.id)
    return counts


@dataclass(frozen=True)
class TopLocation:
    name: str
    city: str | None
    count: int
    last_date: date | None


def load_top_locations(
    db: Session,
    participant_ids: list[UUID],
    *,
    source: str = "run",
) -> dict[UUID, TopLocation]:
    """Локация, где у участника больше всего пробежек (source="run") или
    волонтёрств (source="volunteer"); при равенстве — где был последним.

    Волонтёрства parkrun на дате-заглушке 1970-01-01 (сводка ролей из профиля,
    а не события) не считаем: у них нет настоящей площадки, это псевдолокация
    «parkrun (сводка ролей)».
    """
    if not participant_ids:
        return {}
    model = RunResult if source == "run" else VolunteerResult
    query = (
        db.query(
            model.participant_id,
            Location.name,
            Location.city,
            func.count(model.id).label("items_count"),
            func.max(Event.event_date).label("last_date"),
        )
        .join(Event, model.event_id == Event.id)
        .join(Location, Event.location_id == Location.id)
        .filter(
            model.participant_id.in_(participant_ids),
            Event.is_test_event.is_(False),
        )
    )
    if model is VolunteerResult:
        query = query.filter(Event.event_date > date(1970, 1, 1))
    rows = query.group_by(model.participant_id, Location.name, Location.city).all()
    best: dict[UUID, TopLocation] = {}
    for participant_id, name, city, items_count, last_date in rows:
        current = best.get(participant_id)
        candidate = TopLocation(name=name, city=city, count=items_count, last_date=last_date)
        if current is None or (candidate.count, candidate.last_date or date.min) > (
            current.count,
            current.last_date or date.min,
        ):
            best[participant_id] = candidate
    return best


def _load_home_locations(db: Session, participant_ids: list[UUID]) -> dict[UUID, tuple[str, str | None]]:
    """Домашняя локация = где у участника больше всего пробежек (при равенстве — свежее)."""
    return {
        participant_id: (top.name, top.city)
        for participant_id, top in load_top_locations(db, participant_ids).items()
    }


def load_linked_owners(
    db: Session,
    candidates: list[tuple[Participant, Platform]],
) -> dict[UUID, UUID]:
    """participant_id -> user_id владельца привязки (по participant_id и по внешнему ключу)."""
    participant_ids = [participant.id for participant, _ in candidates]
    owners: dict[UUID, UUID] = {}
    rows = (
        db.query(PlatformLink.participant_id, PlatformLink.user_id)
        .filter(PlatformLink.participant_id.in_(participant_ids))
        .all()
    )
    for participant_id, user_id in rows:
        if participant_id is not None:
            owners[participant_id] = user_id

    # Привязка могла быть создана до появления строки участника — тогда
    # participant_id в ней пуст, но (platform_id, external_user_id) совпадает.
    unresolved = [
        (participant.platform_id, participant.external_user_id, participant.id)
        for participant, _ in candidates
        if participant.id not in owners
    ]
    if unresolved:
        external_ids = list({external_id for _, external_id, _ in unresolved})
        link_rows = (
            db.query(PlatformLink.platform_id, PlatformLink.external_user_id, PlatformLink.user_id)
            .filter(PlatformLink.external_user_id.in_(external_ids))
            .all()
        )
        by_identity = {(platform_id, external_id): user_id for platform_id, external_id, user_id in link_rows}
        for platform_id, external_id, participant_id in unresolved:
            owner = by_identity.get((platform_id, external_id))
            if owner is not None:
                owners[participant_id] = owner
    return owners
