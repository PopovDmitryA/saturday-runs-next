"""Backfill users.display_name после миграции 066: имя берём из профилей систем.

До сих пор имя заполнялось от провайдера входа, и на проде это в основном логины:
121 из 489 привязанных пользователей показывались как `m4rtynovadian`, `a.kor90`,
`leo1973@spartak.ru`. Здесь имя пересчитывается по правилам
app.services.user_display_name_service и разбираются 79 ручных правок.

Что делается с прежним ручным именем (display_name_customized = true):
- совпадает с именем из профиля (55 человек на проде) — ничего, стиль «auto»;
- одно слово без фамилии («Андрей», «Наталья», 8 человек) — стиль «initial»,
  то есть «Андрей З.»: осознанный выбор приватности не теряется;
- всё остальное, включая настоящие ники («Йомиф Кеджелча», «Victor_42195») —
  стиль «auto», прежнее имя уходит в display_name_notice, и человек увидит в
  кабинете плашку «было … стало …» со ссылкой на настройку.

display_name_notice проставляется ВСЕМ, у кого имя фактически изменилось, а не
только бывшим никам: замена логина на своё же ФИО тоже требует объяснения.

Скрипт идемпотентен: повторный запуск ничего не меняет, notice не перетирается.

Запуск:
    docker compose exec api python scripts/backfill_display_names.py --dry-run
    docker compose exec api python scripts/backfill_display_names.py
"""

from __future__ import annotations

import argparse
import re
import sys
from collections import Counter

sys.path.insert(0, "/app")

from app.db.session import get_session_factory  # noqa: E402
from app.models import User  # noqa: E402
from app.services.user_display_name_service import (  # noqa: E402
    STYLE_AUTO,
    STYLE_INITIAL,
    compute_display_name,
    is_valid_person_name,
    rebind_display_name_source,
    selected_source_code,
)


def _norm(value: str | None) -> str:
    """Ключ сравнения имён: без регистра, пунктуации, ё и порядка слов."""
    cleaned = re.sub(r"[^\w\s]", " ", (value or "")).lower().replace("ё", "е")
    return " ".join(sorted(cleaned.split()))


def _looks_like_person_name(value: str | None) -> bool:
    return is_valid_person_name(value)


def _style_for_legacy_name(legacy: str | None, profile_name: str) -> str:
    """Прежнее ручное имя из одного слова — это выбор «без фамилии».

    Ближайший доступный вариант в новом селекторе — «Имя Ф.»: полную фамилию
    человек прятал намеренно, и возвращать её молча неправильно.
    """
    cleaned = (legacy or "").strip()
    if not cleaned:
        return STYLE_AUTO
    words = cleaned.split()
    if len(words) == 1 and words[0].lower() == profile_name.split()[0].lower():
        return STYLE_INITIAL
    return STYLE_AUTO


def _change_kind(old: str | None, new: str) -> str:
    if not _looks_like_person_name(old):
        return "логин/ник/почта → ФИО"
    if _norm(old) == _norm(new):
        return "косметика (регистр, ё)"
    if set(_norm(old).split()) & set(_norm(new).split()):
        return "сокращение или другое написание"
    return "совсем другое имя"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="только показать, ничего не писать")
    parser.add_argument("--limit", type=int, default=None, help="ограничить число пользователей (для отладки)")
    args = parser.parse_args()

    db = get_session_factory()()
    try:
        query = db.query(User).order_by(User.serial_id)
        if args.limit:
            query = query.limit(args.limit)
        users = query.all()

        kinds: Counter[str] = Counter()
        sources: Counter[str] = Counter()
        changed_rows: list[tuple[str | None, str, str, str]] = []
        styled = 0

        for user in users:
            legacy_name = user.display_name
            _preview_name, chosen = compute_display_name(db, user)

            # Стиль назначаем только тем, кто когда-то правил имя руками, и только
            # один раз: у остальных он уже "auto" из server_default.
            if user.display_name_customized and user.display_name_style == STYLE_AUTO and chosen is not None:
                style = _style_for_legacy_name(legacy_name, chosen.value)
                if style != STYLE_AUTO:
                    user.display_name_style = style
                    styled += 1

            # Здесь же фиксируется система-источник: дальше имя тянется только из
            # неё, и фоновый пересчёт источник больше не пересматривает.
            rebind_display_name_source(db, user)
            new_name = user.display_name or ""
            source = selected_source_code(db, user) or "провайдер входа"
            sources[source] += 1

            if new_name == legacy_name:
                continue

            kinds[_change_kind(legacy_name, new_name)] += 1
            changed_rows.append((legacy_name, new_name, source, user.display_name))
            # Плашка ставится один раз: повторный прогон её не перетирает и не
            # затирает уже закрытую (там NULL, но и имя уже совпадает).
            if user.display_name_notice is None and legacy_name:
                user.display_name_notice = legacy_name[:128]

        print(f"Пользователей просмотрено: {len(users)}")
        print(f"Имя изменится у: {len(changed_rows)}")
        print(f"Стиль «Имя Ф.» проставлен: {styled}")
        print("\nИсточник имени:")
        for source, count in sources.most_common():
            print(f"  {source:22s} {count}")
        print("\nИз чего состоят изменения:")
        for kind, count in kinds.most_common():
            print(f"  {kind:32s} {count}")

        print("\nБыло → стало (источник):")
        for old, new, source, _ in changed_rows:
            print(f"  {old!r:42s} → {new!r} ({source})")

        if args.dry_run:
            db.rollback()
            print("\n--dry-run: ничего не записано")
            return

        db.commit()
        print("\nЗаписано.")
    finally:
        db.close()


if __name__ == "__main__":
    main()
