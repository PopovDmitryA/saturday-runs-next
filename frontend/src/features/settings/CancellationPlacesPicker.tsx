import { useEffect, useId, useRef, useState, type KeyboardEvent } from "react";
import { ApiError, suggestCancellationPlaces, type CancellationPlace } from "../../lib/api";

// Поле «Где следить за отменами»: набираешь название, выбираешь подсказку —
// она встаёт в строку чипом, как адресат в почте. Места — локации и целиком
// города, регионы, страны (бэкенд: cancellation_places_service). Пустая
// строка — вся страна, как было до выбора мест.
//
// Клавиатура как у адресатов в почте: фокус всё время в поле ввода, а чип
// «выбирается» подсветкой. Backspace в пустом поле сначала выделяет
// последний чип и только вторым нажатием убирает его — случайно стереть
// город одним лишним нажатием нельзя. ←/→ ходят по чипам, Delete убирает
// выделенный, Esc и любой набранный символ снимают выделение.
const KIND_ICONS: Record<CancellationPlace["kind"], string> = {
  location: "📍",
  city: "🏙",
  region: "🗺",
  country: "🌍",
};

const DEBOUNCE_MS = 250;
const MIN_QUERY_LENGTH = 2;

export function CancellationPlacesPicker({
  places,
  disabled,
  onChange,
}: {
  places: CancellationPlace[];
  disabled: boolean;
  // Новый список целиком: строка обновляется сразу, сохранение — следом.
  onChange: (next: CancellationPlace[]) => void;
}) {
  const [query, setQuery] = useState("");
  const [items, setItems] = useState<CancellationPlace[]>([]);
  const [active, setActive] = useState(0);
  const [open, setOpen] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // Индекс выделенного чипа; null — курсор в поле ввода.
  const [picked, setPicked] = useState<number | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const listId = useId();
  const chosen = new Set(places.map((place) => place.key));
  const visible = items.filter((item) => !chosen.has(item.key));
  const pickedPlace = picked !== null ? places[picked] ?? null : null;

  useEffect(() => {
    const text = query.trim();
    if (text.length < MIN_QUERY_LENGTH) {
      setItems([]);
      setLoading(false);
      return;
    }
    const controller = new AbortController();
    setLoading(true);
    const timer = window.setTimeout(() => {
      suggestCancellationPlaces(text, controller.signal)
        .then((response) => {
          setItems(response.items);
          setActive(0);
          setError(null);
        })
        .catch((err) => {
          if (controller.signal.aborted) return;
          setItems([]);
          setError(err instanceof ApiError ? err.message : "Подсказки не загрузились");
        })
        .finally(() => {
          if (!controller.signal.aborted) setLoading(false);
        });
    }, DEBOUNCE_MS);
    return () => {
      window.clearTimeout(timer);
      controller.abort();
    };
  }, [query]);

  const add = (item: CancellationPlace) => {
    onChange([...places, item]);
    setQuery("");
    setItems([]);
    inputRef.current?.focus();
  };

  const remove = (key: string) => {
    onChange(places.filter((place) => place.key !== key));
    setPicked(null);
    inputRef.current?.focus();
  };

  // Курсор в самом начале поля и ничего не выделено в тексте: отсюда
  // стрелка влево уходит на чипы.
  const caretAtStart = (input: HTMLInputElement) => input.selectionStart === 0 && input.selectionEnd === 0;

  const onChipKey = (event: KeyboardEvent<HTMLInputElement>): boolean => {
    const last = places.length - 1;
    if (pickedPlace && picked !== null) {
      if (event.key === "Backspace" || event.key === "Delete") {
        remove(pickedPlace.key);
      } else if (event.key === "ArrowLeft") {
        setPicked(Math.max(0, picked - 1));
      } else if (event.key === "ArrowRight") {
        setPicked(picked >= last ? null : picked + 1);
      } else if (event.key === "Home") {
        setPicked(0);
      } else if (event.key === "End") {
        setPicked(null);
      } else if (event.key === "Escape") {
        // Esc снимает выделение, модалку закроет уже следующий.
        event.stopPropagation();
        setPicked(null);
      } else {
        // Любая другая клавиша — снова печатаем в поле.
        setPicked(null);
        return false;
      }
      event.preventDefault();
      return true;
    }
    if (last < 0 || !caretAtStart(event.currentTarget)) return false;
    if ((event.key === "Backspace" && query === "") || event.key === "ArrowLeft") {
      event.preventDefault();
      setOpen(false);
      setPicked(last);
      return true;
    }
    return false;
  };

  const onKeyDown = (event: KeyboardEvent<HTMLInputElement>) => {
    if (onChipKey(event)) return;
    if (event.key === "ArrowDown" && visible.length > 0) {
      event.preventDefault();
      setOpen(true);
      setActive((index) => (index + 1) % visible.length);
    } else if (event.key === "ArrowUp" && visible.length > 0) {
      event.preventDefault();
      setActive((index) => (index - 1 + visible.length) % visible.length);
    } else if (event.key === "Enter") {
      // Enter без подсказки не должен нажимать «Готово» модалки.
      event.preventDefault();
      const item = visible[active];
      if (open && item) add(item);
    } else if (event.key === "Escape" && open && query) {
      // Первый Esc закрывает подсказки, второй — уже модалку.
      event.stopPropagation();
      setOpen(false);
    }
  };

  const showList = open && query.trim().length >= MIN_QUERY_LENGTH;

  return (
    <div className="notify-places">
      <div
        className={`notify-places-box ${disabled ? "is-disabled" : ""}`}
        onClick={() => inputRef.current?.focus()}
      >
        {places.map((place, index) => (
          <span
            className={`notify-place-chip ${index === picked ? "is-picked" : ""}`}
            key={place.key}
            title={place.label}
            onClick={(event) => {
              event.stopPropagation();
              setPicked(index);
              setOpen(false);
              inputRef.current?.focus();
            }}
          >
            <span aria-hidden="true">{KIND_ICONS[place.kind]}</span>
            <span className="notify-place-chip-label">{place.label}</span>
            <button
              type="button"
              className="notify-place-chip-remove"
              aria-label={`Убрать: ${place.label}`}
              disabled={disabled}
              onClick={(event) => {
                event.stopPropagation();
                remove(place.key);
              }}
            >
              ×
            </button>
          </span>
        ))}
        <input
          ref={inputRef}
          className="notify-places-input"
          type="text"
          role="combobox"
          aria-expanded={showList}
          aria-controls={listId}
          aria-autocomplete="list"
          aria-activedescendant={showList && visible[active] ? `${listId}-${active}` : undefined}
          aria-label="Добавить локацию, город или регион"
          placeholder={places.length === 0 ? "Вся страна — или добавьте город, регион, локацию" : "Добавить ещё…"}
          value={query}
          disabled={disabled}
          onChange={(event) => {
            setQuery(event.target.value);
            setPicked(null);
            setOpen(true);
          }}
          onFocus={() => setOpen(true)}
          // Клик по подсказке срабатывает раньше blur за счёт onMouseDown.
          onBlur={() => {
            setOpen(false);
            setPicked(null);
          }}
          onKeyDown={onKeyDown}
        />
      </div>
      <span className="visually-hidden" aria-live="polite">
        {pickedPlace ? `Выбрано: ${pickedPlace.label}. Backspace — убрать.` : ""}
      </span>
      {showList && (
        <ul className="notify-places-list" id={listId} role="listbox">
          {visible.map((item, index) => (
            <li
              key={item.key}
              id={`${listId}-${index}`}
              role="option"
              aria-selected={index === active}
              className={`notify-places-option ${index === active ? "is-active" : ""}`}
              onMouseDown={(event) => {
                event.preventDefault();
                add(item);
              }}
              onMouseEnter={() => setActive(index)}
            >
              <span aria-hidden="true">{KIND_ICONS[item.kind]}</span>
              <span className="notify-places-option-text">
                <span>{item.label}</span>
                {item.hint && <span className="muted notify-places-option-hint">{item.hint}</span>}
              </span>
            </li>
          ))}
          {visible.length === 0 && (
            <li className="muted notify-places-empty">{loading ? "Ищем…" : error ?? "Ничего не нашлось"}</li>
          )}
        </ul>
      )}
    </div>
  );
}
