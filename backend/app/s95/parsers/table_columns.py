from __future__ import annotations


def runs_table_column_indexes(headers: list[str]) -> dict[str, int | None]:
    low = [h.lower().strip() for h in headers]

    def idx(*needles: str) -> int | None:
        for i, header in enumerate(low):
            if any(needle in header for needle in needles):
                return i
        return None

    return {
        "event_num": idx("#", "№"),
        "date": idx("дата", "datum"),
        "time": idx("время", "time", "результат"),
        "pace": idx("темп", "tempo", "pace"),
        "place": idx("место", "place", "поз"),
        "event": idx("мероприят", "event", "локац"),
        "participant": idx("участник", "participant", "učesnik", "имя"),
    }
