import { useEffect, useId, useRef, useState, type KeyboardEvent } from "react";
import { platformCodeLabel, pluralFormRu } from "../../lib/format";
import { getPlannerLocations, type PlannerLocationOption, type PlannerLocations } from "./plannerApi";
import { planDayLabel } from "./plannerDates";
import { errorText, SERIES_ICON } from "./seriesText";
import "./planner.css";

/**
 * «Где бежите?» в окне плана «от даты» (кабинет: «Мои планы», «Обзор»).
 * Решение Дмитрия 26.10.2026: «я турист и хочу расписать ближайшие два
 * месяца» — локацию выбирают прямо в окне, не уходя со страницы.
 *
 * Без запроса — подсказки сервера: постоянная, домашняя, локации недавних
 * планов, самые частые. С двух букв — поиск по названию, городу и региону
 * (те же сокращения, что у поиска по сайту). В обоих списках только локации,
 * куда в эту дату можно запланировать.
 *
 * Список — прямо под полем, а не всплывающим окном: на телефоне его листают
 * пальцем, как обычный список. С клавиатуры — стрелки и Enter (combobox):
 * фокус остаётся в поле. Esc в непустом поле стирает запрос, а не закрывает окно.
 *
 * Лимит дня известен заранее (то же правило, что у окна от локации и у
 * сервера — run_plan_service.day_plan_limits): на субботу с планом в другой
 * локации (на особую дату — с тремя) другую выбрать можно, и новый план
 * заменит прежний — об этом говорят до выбора, а окно после выбора называет,
 * какой план будет отменён (правка Дмитрия 27.10.2026). Локации, что уже в
 * планах этого дня (и суббота постоянного), помечены «уже в планах» и не
 * выбираются: окно добавляет локацию, а правят план «Изменить» в деталях дня
 * (решение 27.10.2026 — одно правило с окном от локации).
 */

// Поиск — с двух букв, как у сервера; между нажатиями ждём, а не шлём запрос
// на каждую букву.
const MIN_QUERY = 2;
const DEBOUNCE_MS = 250;
const PLAN_FORMS = ["план", "плана", "планов"] as const;

function optionMeta(option: PlannerLocationOption): string {
  const parts: string[] = [];
  if (option.city && option.city !== option.name) {
    parts.push(option.city);
  }
  if (option.platforms.length > 0) {
    parts.push(option.platforms.map((code) => platformCodeLabel(code)).join(", "));
  }
  if (option.number_forecast != null) {
    parts.push(`≈№${option.number_forecast}`);
  }
  return parts.join(" · ");
}

/** Уже в планах дня или отменённый старт — не выбрать. Заполненный день — можно: план заменит прежний. */
function optionDisabled(option: PlannerLocationOption): boolean {
  return option.mine || option.cancelled;
}

/** Набралось ли на день столько планов, сколько можно. */
function isDayFull(payload: PlannerLocations | null): boolean {
  return Boolean(payload && (payload.planned?.length ?? 0) >= payload.limit);
}

/** Планов больше лимита (админ убрал особую дату, объединение профилей): заменой не уложиться. */
function isDayOver(payload: PlannerLocations | null): boolean {
  return Boolean(payload && (payload.planned?.length ?? 0) > payload.limit);
}

/** Запрос, который уйдёт на сервер при таком тексте поля: короче двух букв — подсказки. */
function queryToSend(query: string): string {
  const trimmed = query.trim();
  return trimmed.length >= MIN_QUERY ? trimmed : "";
}

export function PlanPlacePicker({
  date,
  autoFocus = false,
  onPick,
}: {
  date: string;
  autoFocus?: boolean;
  /** keyboard — выбрали Enter'ом: окно переведёт фокус на «изменить». */
  onPick: (option: PlannerLocationOption, keyboard: boolean) => void;
}) {
  const baseId = useId();
  const inputId = `${baseId}-input`;
  const listId = `${baseId}-list`;
  const optionId = (index: number) => `${baseId}-opt-${index}`;
  const [query, setQuery] = useState("");
  // Запрос, ушедший на сервер: короче двух букв — подсказки, а не поиск.
  const [sent, setSent] = useState("");
  const [data, setData] = useState<{ key: string; payload: PlannerLocations } | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [retry, setRetry] = useState(0);
  const [active, setActive] = useState(-1);
  const inputRef = useRef<HTMLInputElement>(null);
  const listRef = useRef<HTMLUListElement>(null);
  // Enter нажали раньше, чем пришёл ответ на набранный текст: выбрать первую
  // строку ЭТОГО ответа, когда он придёт (ключ запроса), — а не прежнего
  // списка, который ещё на экране: «битц» + Enter ставил план в «Мега Парк
  // Кудрово» из подсказок (ревью 26.10.2026, UI-2).
  const pendingEnterRef = useRef<string | null>(null);
  const onPickRef = useRef(onPick);
  onPickRef.current = onPick;

  useEffect(() => {
    if (autoFocus) {
      inputRef.current?.focus({ preventScroll: true });
    }
  }, [autoFocus]);

  useEffect(() => {
    const next = queryToSend(query);
    if (next === sent) {
      return;
    }
    // Стёрли запрос — подсказки сразу, без ожидания.
    const timer = window.setTimeout(() => setSent(next), next ? DEBOUNCE_MS : 0);
    return () => window.clearTimeout(timer);
  }, [query, sent]);

  const requestKey = `${date}|${sent}`;
  useEffect(() => {
    const controller = new AbortController();
    setError(null);
    getPlannerLocations(date, sent, { signal: controller.signal })
      .then((payload) => {
        if (controller.signal.aborted) {
          return;
        }
        setData({ key: requestKey, payload });
        setActive(-1);
        if (pendingEnterRef.current === requestKey) {
          pendingEnterRef.current = null;
          const first = (sent ? payload.results : payload.suggested).find((option) => !optionDisabled(option));
          if (first) {
            onPickRef.current(first, true);
          }
        }
      })
      .catch((err) => {
        if (!controller.signal.aborted) {
          setError(errorText(err, "Не удалось загрузить локации"));
        }
      });
    return () => controller.abort();
    // requestKey собран из date и sent.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [date, sent, retry]);

  // Пока едет ответ на новый запрос, виден прежний список — без мигания
  // «Загрузки» на каждой букве; на другую дату прежний список не годится.
  const shown = data && data.key.startsWith(`${date}|`) ? data : null;
  const fresh = shown?.payload ?? null;
  // Подсказки на экране или поиск — по запросу, на который пришёл ответ, а
  // не по ушедшему: у подсказок results пуст, у поиска — suggested, и на
  // смене режима список пропадал до ответа, а на его месте стояло «ничего не
  // нашли».
  const searching = shown !== null && shown.key !== `${date}|`;
  const options = fresh ? (searching ? fresh.results : fresh.suggested) : [];
  const dayFull = isDayFull(fresh);
  const dayOver = isDayOver(fresh);
  const enabledIndexes = options
    .map((option, index) => (optionDisabled(option) ? -1 : index))
    .filter((i) => i >= 0);
  // Список на экране — ответ ровно на то, что набрано в поле.
  const wantedKey = `${date}|${queryToSend(query)}`;
  const current = data?.key === wantedKey;

  useEffect(() => {
    if (active < 0) {
      return;
    }
    listRef.current
      ?.querySelector<HTMLElement>(`#${CSS.escape(optionId(active))}`)
      ?.scrollIntoView({ block: "nearest" });
    // optionId — от постоянного baseId.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [active]);

  const move = (step: 1 | -1) => {
    // Выбирают стрелками — отложенный Enter больше не нужен.
    pendingEnterRef.current = null;
    if (enabledIndexes.length === 0) {
      return;
    }
    const position = enabledIndexes.indexOf(active);
    const next =
      position < 0
        ? step === 1
          ? 0
          : enabledIndexes.length - 1
        : (position + step + enabledIndexes.length) % enabledIndexes.length;
    setActive(enabledIndexes[next]);
  };

  const onKeyDown = (event: KeyboardEvent<HTMLInputElement>) => {
    if (event.key === "ArrowDown") {
      event.preventDefault();
      move(1);
    } else if (event.key === "ArrowUp") {
      event.preventDefault();
      move(-1);
    } else if (event.key === "Enter") {
      // Выбрано стрелками — его. Иначе первая подходящая строка ответа на
      // набранное: набрал «Кузьм» и нажал Enter. Ответ ещё не пришёл (ждём
      // паузы в наборе или сервер) — запрос сразу, выбор — по ответу.
      if (active < 0 && !current) {
        event.preventDefault();
        pendingEnterRef.current = wantedKey;
        setSent(queryToSend(query));
        return;
      }
      const index = active >= 0 ? active : enabledIndexes[0];
      if (index !== undefined && options[index]) {
        event.preventDefault();
        onPick(options[index], true);
      }
    } else if (event.key === "Escape" && query) {
      // Окно закрывается по Esc на документе — непустое поле сначала стирается.
      event.preventDefault();
      event.stopPropagation();
      setQuery("");
    }
  };

  const typedShort = query.trim().length > 0 && query.trim().length < MIN_QUERY;
  let status: string | null = null;
  if (error) {
    status = null;
  } else if (!fresh) {
    status = "Загружаем локации…";
  } else if (options.length === 0) {
    status = searching
      ? `На ${planDayLabel(date)} ничего не нашли. Проверьте название — или в этот день там нет старта.`
      : "Начните вводить название локации или город.";
  }

  return (
    <div className="plan-place">
      <label className="plan-modal-label" htmlFor={inputId}>
        Где бежите?
      </label>
      <input
        ref={inputRef}
        id={inputId}
        className="input plan-place-input"
        type="search"
        role="combobox"
        aria-expanded={options.length > 0}
        aria-controls={listId}
        aria-autocomplete="list"
        aria-activedescendant={active >= 0 ? optionId(active) : undefined}
        autoComplete="off"
        enterKeyHint="search"
        placeholder="Название локации, город или регион"
        value={query}
        onChange={(event) => {
          // Набор продолжился: выбор стрелками и отложенный Enter — о прежнем тексте.
          pendingEnterRef.current = null;
          setActive(-1);
          setQuery(event.target.value);
        }}
        onKeyDown={onKeyDown}
      />
      {options.length > 0 && (
        <p className="plan-place-head">
          {searching ? "Найдено" : typedShort ? "Ещё букву — и поищем. Пока ваши локации:" : "Ваши локации"}
        </p>
      )}
      {error && (
        <div className="plan-modal-load-error">
          <p className="error-text">{error}</p>
          <button type="button" className="btn secondary btn-sm" onClick={() => setRetry((value) => value + 1)}>
            Повторить
          </button>
        </div>
      )}
      {status && <p className="plan-modal-hint">{status}</p>}
      {fresh && dayOver && (
        <p className="plan-modal-hint plan-modal-hint-warn">
          На этот день у вас планов больше, чем можно: {(fresh.planned ?? []).map((item) => item.name).join(", ")}.
          Уберите лишние в «Моих планах».
        </p>
      )}
      {fresh && dayFull && !dayOver && (
        <p className="plan-modal-hint plan-modal-hint-warn">
          {fresh.limit > 1
            ? `На этот день у вас уже ${fresh.limit} ${pluralFormRu(fresh.limit, PLAN_FORMS)}`
            : "На этот день у вас уже есть план"}
          : {(fresh.planned ?? []).map((item) => item.name).join(", ")}. Выберите другую локацию —{" "}
          {fresh.limit > 1 ? "последний из них будет отменён" : "этот план будет отменён"}.
        </p>
      )}
      {fresh?.rest && <p className="plan-modal-hint">На этот день у вас «Не бегаю» — план снимет отметку.</p>}
      <ul ref={listRef} id={listId} className="plan-place-list" role="listbox" aria-label="Локации на эту дату">
        {options.map((option, index) => {
          const disabled = optionDisabled(option);
          const meta = optionMeta(option);
          return (
            <li
              key={option.key}
              id={optionId(index)}
              role="option"
              aria-selected={index === active}
              aria-disabled={disabled || undefined}
              className={`plan-place-option${index === active ? " plan-place-option-active" : ""}`}
              onMouseDown={(event) => event.preventDefault()}
              onMouseEnter={() => !disabled && setActive(index)}
              onClick={() => !disabled && onPick(option, false)}
            >
              <span className="plan-place-name">
                {option.regular && (
                  <span className="plan-place-regular" title="Ваш постоянный план">
                    {SERIES_ICON}{" "}
                  </span>
                )}
                {option.name}
              </span>
              {meta && <span className="plan-place-meta">{meta}</span>}
              {option.mine ? (
                <span className="plan-place-tag plan-place-tag-mine">уже в планах</span>
              ) : option.cancelled ? (
                <span className="plan-place-tag plan-place-tag-cancel">старт отменён</span>
              ) : null}
            </li>
          );
        })}
      </ul>
      {searching && fresh?.truncated && options.length > 0 && (
        <p className="plan-modal-hint">Показаны первые {options.length} — уточните название, чтобы найти остальные.</p>
      )}
    </div>
  );
}
