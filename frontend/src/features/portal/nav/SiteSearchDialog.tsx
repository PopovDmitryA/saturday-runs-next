/**
 * Поиск по сайту (решение Дмитрия 23.09.2026: «делаем сразу»).
 *
 * Ищет три вещи: страницы сайта (по дереву навигации и синонимам — на
 * клиенте), локации и людей (на сервере, /api/search). Людей два вида:
 * - зарегистрированные на сайте с открытым профилем — строка со ссылкой;
 * - все остальные из протоколов (и с закрытым профилем) — карточка без
 *   перехода: пробежки, волонтёрства, самая частая локация и пометка
 *   «не зарегистрирован на сайте». Ссылок на профили в беговых системах нет
 *   намеренно: согласия этих людей на это у нас нет.
 *
 * Открывается кнопками в шапке и рельсе, ⌘K / Ctrl+K и «/». На телефоне —
 * на весь экран. Каждый поиск пишется в журнал (анонимно) — Дмитрию важно
 * видеть, что ищут и что не находится.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  logSiteSearch,
  searchSite,
  type SiteSearchLocation,
  type SiteSearchPerson,
  type SiteSearchResponse,
} from "../../../lib/api";
import { useOptionalUser } from "../../../lib/useOptionalUser";
import { PlatformBadge } from "../../../components/PlatformBadge";
import { CLOSE_ICON, LOCATIONS_ICON, SEARCH_ICON } from "./navIcons";
import { resolveNavState } from "./navState";
import { combineLocationPages, planSearch, type PageHit } from "./pageSearch";
import { SITE_SEARCH_OPEN_EVENT } from "./siteSearchBus";

const DEBOUNCE_MS = 220;
const MIN_SERVER_QUERY = 2;

type ClickKind = "page" | "location" | "person";

type Selectable =
  | { kind: "page"; hit: PageHit }
  | { kind: "location"; location: SiteSearchLocation }
  | { kind: "person"; person: Extract<SiteSearchPerson, { kind: "registered" }> };

function selectableHref(item: Selectable): string {
  if (item.kind === "page") return item.hit.href;
  if (item.kind === "location") return item.location.href;
  return item.person.href;
}

function isMobileViewport(): boolean {
  return typeof window !== "undefined" && window.matchMedia("(max-width: 900px)").matches;
}

function isTypingTarget(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) return false;
  return target.isContentEditable || ["INPUT", "TEXTAREA", "SELECT"].includes(target.tagName);
}

function plural(n: number, one: string, few: string, many: string): string {
  const mod10 = n % 10;
  const mod100 = n % 100;
  if (mod10 === 1 && mod100 !== 11) return one;
  if (mod10 >= 2 && mod10 <= 4 && (mod100 < 12 || mod100 > 14)) return few;
  return many;
}

function personStats(person: SiteSearchPerson): string {
  const runs = `${person.total_runs} ${plural(person.total_runs, "пробежка", "пробежки", "пробежек")}`;
  const vol = `${person.total_volunteering} ${plural(person.total_volunteering, "волонтёрство", "волонтёрства", "волонтёрств")}`;
  return `${runs} · ${vol}`;
}

// «Барнаул (Барнаул)» и «Королёв (Королёв)» читаются как опечатка: город
// дописываем, только если его нет в названии локации.
function placeWithCity(name: string, city: string | null): string {
  if (!city || name.toLowerCase().includes(city.toLowerCase())) return name;
  return `${name} (${city})`;
}

function initials(name: string): string {
  const parts = name.trim().split(/\s+/).filter(Boolean);
  return ((parts[0]?.[0] ?? "") + (parts[1]?.[0] ?? "")).toUpperCase() || "?";
}

type Pending = {
  query: string;
  corrected: string | null;
  pages: number;
  locations: number;
  people: number;
};

export function SiteSearchDialog() {
  const user = useOptionalUser();
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [response, setResponse] = useState<SiteSearchResponse | null>(null);
  const [responseFor, setResponseFor] = useState("");
  const [loading, setLoading] = useState(false);
  const [failed, setFailed] = useState(false);
  const [selected, setSelected] = useState(0);
  const inputRef = useRef<HTMLInputElement>(null);
  const listRef = useRef<HTMLDivElement>(null);
  // Последний «устоявшийся» поиск, ещё не записанный в журнал. Пишем, когда
  // человек начал другой поиск, выбрал результат или закрыл окно — а не на
  // каждую букву, иначе журнал забьётся обрывками «Ива», «Иван», «Иванов».
  const pendingRef = useRef<Pending | null>(null);

  const sections = useMemo(() => resolveNavState({ user }).sections, [user]);
  const plan = useMemo(() => planSearch(query, sections), [query, sections]);

  const flushLog = useCallback((click: { kind: ClickKind; target: string } | null) => {
    const pending = pendingRef.current;
    pendingRef.current = null;
    if (!pending || pending.query.length < 2) return;
    logSiteSearch({
      query: pending.query,
      corrected_query: pending.corrected,
      pages_found: pending.pages,
      locations_found: pending.locations,
      people_found: pending.people,
      clicked_kind: click?.kind ?? null,
      clicked_target: click?.target ?? null,
      is_mobile: isMobileViewport(),
    });
  }, []);

  const close = useCallback(() => {
    flushLog(null);
    setOpen(false);
  }, [flushLog]);

  // Открытие: событие от кнопок и горячие клавиши.
  useEffect(() => {
    const onOpen = () => setOpen(true);
    const onKey = (event: KeyboardEvent) => {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") {
        event.preventDefault();
        setOpen((value) => !value);
        return;
      }
      if (event.key === "/" && !event.metaKey && !event.ctrlKey && !isTypingTarget(event.target)) {
        event.preventDefault();
        setOpen(true);
      }
    };
    window.addEventListener(SITE_SEARCH_OPEN_EVENT, onOpen);
    window.addEventListener("keydown", onKey);
    return () => {
      window.removeEventListener(SITE_SEARCH_OPEN_EVENT, onOpen);
      window.removeEventListener("keydown", onKey);
    };
  }, []);

  // Пока окно открыто — фокус в поле, страница под ним не скроллится.
  useEffect(() => {
    if (!open) return;
    const previous = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    inputRef.current?.focus();
    inputRef.current?.select();
    return () => {
      document.body.style.overflow = previous;
    };
  }, [open]);

  // Запрос к серверу — с задержкой и отменой предыдущего.
  const serverQuery = plan.serverQuery;
  useEffect(() => {
    if (!open) return;
    if (serverQuery.length < MIN_SERVER_QUERY) {
      setResponse(null);
      setResponseFor("");
      setLoading(false);
      setFailed(false);
      return;
    }
    const controller = new AbortController();
    setLoading(true);
    const timer = window.setTimeout(() => {
      searchSite(serverQuery, controller.signal)
        .then((payload) => {
          setResponse(payload);
          setResponseFor(serverQuery);
          setFailed(false);
        })
        .catch((error: unknown) => {
          if (error instanceof DOMException && error.name === "AbortError") return;
          setFailed(true);
        })
        .finally(() => {
          if (!controller.signal.aborted) setLoading(false);
        });
    }, DEBOUNCE_MS);
    return () => {
      controller.abort();
      window.clearTimeout(timer);
    };
  }, [open, serverQuery]);

  const fresh = response && responseFor === serverQuery ? response : null;
  const locations = fresh?.locations ?? [];
  const people = fresh?.people ?? [];
  const combos = combineLocationPages(plan, locations);
  const pages = [...combos, ...plan.pages].slice(0, 7);
  const registered = people.filter(
    (person): person is Extract<SiteSearchPerson, { kind: "registered" }> => person.kind === "registered",
  );
  const others = people.filter((person) => person.kind === "participant");

  const selectables: Selectable[] = [
    ...pages.map((hit) => ({ kind: "page" as const, hit })),
    ...locations.map((location) => ({ kind: "location" as const, location })),
    ...registered.map((person) => ({ kind: "person" as const, person })),
  ];

  // Запоминаем устоявшийся результат для журнала. Новый поиск, который не
  // продолжает прежний (не дописывание и не стирание), сначала сбрасывает
  // прежний в журнал.
  const normalized = query.trim().replace(/\s+/g, " ");
  const settled = normalized.length >= 2 && (serverQuery.length < MIN_SERVER_QUERY || fresh !== null);
  useEffect(() => {
    if (!open || !settled) return;
    const previous = pendingRef.current;
    if (
      previous &&
      !normalized.toLowerCase().startsWith(previous.query.toLowerCase()) &&
      !previous.query.toLowerCase().startsWith(normalized.toLowerCase())
    ) {
      flushLog(null);
    }
    pendingRef.current = {
      query: normalized,
      corrected: fresh?.corrected_query ?? null,
      pages: pages.length,
      locations: locations.length,
      people: people.length,
    };
    // pages/locations/people производны от normalized и fresh — их в
    // зависимости не кладём, иначе эффект крутился бы на каждом рендере.
  }, [open, settled, normalized, fresh]);

  useEffect(() => {
    setSelected(0);
  }, [query, fresh]);

  useEffect(() => {
    listRef.current
      ?.querySelector<HTMLElement>(`[data-index="${selected}"]`)
      ?.scrollIntoView({ block: "nearest" });
  }, [selected]);

  const choose = (item: Selectable) => {
    const target =
      item.kind === "page" ? item.hit.href : item.kind === "location" ? item.location.href : item.person.href;
    flushLog({ kind: item.kind, target });
    setOpen(false);
    window.location.href = selectableHref(item);
  };

  const onKeyDown = (event: React.KeyboardEvent<HTMLInputElement>) => {
    if (event.key === "Escape") {
      event.preventDefault();
      close();
    } else if (event.key === "ArrowDown") {
      event.preventDefault();
      setSelected((index) => Math.min(index + 1, Math.max(selectables.length - 1, 0)));
    } else if (event.key === "ArrowUp") {
      event.preventDefault();
      setSelected((index) => Math.max(index - 1, 0));
    } else if (event.key === "Enter") {
      const item = selectables[selected];
      if (item) {
        event.preventDefault();
        choose(item);
      }
    }
  };

  if (!open) return null;

  let index = -1;
  const nextIndex = () => {
    index += 1;
    return index;
  };
  const hasQuery = normalized.length >= 2;
  const nothing =
    hasQuery && !loading && !failed && pages.length === 0 && locations.length === 0 && people.length === 0 &&
    (serverQuery.length < MIN_SERVER_QUERY || fresh !== null);

  return (
    <div className="site-search-backdrop" onClick={close} role="presentation">
      <div
        className="site-search"
        role="dialog"
        aria-modal="true"
        aria-label="Поиск по сайту"
        onClick={(event) => event.stopPropagation()}
      >
        <div className="site-search-bar">
          <span className="site-search-bar-icon">{SEARCH_ICON}</span>
          <input
            ref={inputRef}
            id="site-search-input"
            className="site-search-input"
            type="search"
            inputMode="search"
            autoComplete="off"
            spellCheck={false}
            placeholder="Страница, локация или участник"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            onKeyDown={onKeyDown}
            aria-controls="site-search-results"
            aria-activedescendant={selectables.length > 0 ? `site-search-item-${selected}` : undefined}
          />
          {loading && <span className="site-search-spinner" aria-label="Ищем" />}
          <button type="button" className="site-search-close" onClick={close} aria-label="Закрыть поиск">
            <span className="site-search-close-icon">{CLOSE_ICON}</span>
            <span className="site-search-close-text">Отмена</span>
          </button>
        </div>

        <div className="site-search-results" id="site-search-results" ref={listRef} role="listbox">
          {!hasQuery && (
            <div className="site-search-empty">
              <p>Например: <button type="button" onClick={() => setQuery("погода сокольники")}>погода сокольники</button>,{" "}
                <button type="button" onClick={() => setQuery("рекорды локаций")}>рекорды локаций</button>,{" "}
                <button type="button" onClick={() => setQuery("привязать профиль")}>привязать профиль</button> или фамилия участника.
              </p>
            </div>
          )}

          {fresh?.corrected_query && (
            <p className="site-search-note">
              Показаны результаты для «{fresh.corrected_query}» — похоже, была включена другая раскладка.
            </p>
          )}

          {pages.length > 0 && (
            <section className="site-search-group">
              <h3>Разделы сайта</h3>
              {pages.map((hit) => {
                const i = nextIndex();
                return (
                  <a
                    key={hit.key}
                    id={`site-search-item-${i}`}
                    data-index={i}
                    role="option"
                    aria-selected={selected === i}
                    href={hit.href}
                    className={`site-search-row${selected === i ? " selected" : ""}`}
                    onMouseEnter={() => setSelected(i)}
                    onClick={(event) => {
                      event.preventDefault();
                      choose({ kind: "page", hit });
                    }}
                  >
                    <span className="site-search-row-mark site-search-row-mark-icon">{hit.icon ?? SEARCH_ICON}</span>
                    <span className="site-search-row-main">
                      <b>{hit.label}</b>
                      <small>{hit.context}</small>
                    </span>
                  </a>
                );
              })}
            </section>
          )}

          {locations.length > 0 && (
            <section className="site-search-group">
              <h3>Локации</h3>
              {locations.map((location) => {
                const i = nextIndex();
                return (
                  <a
                    key={location.slug}
                    id={`site-search-item-${i}`}
                    data-index={i}
                    role="option"
                    aria-selected={selected === i}
                    href={location.href}
                    className={`site-search-row${selected === i ? " selected" : ""}`}
                    onMouseEnter={() => setSelected(i)}
                    onClick={(event) => {
                      event.preventDefault();
                      choose({ kind: "location", location });
                    }}
                  >
                    <span className="site-search-row-mark site-search-row-mark-icon">{LOCATIONS_ICON}</span>
                    <span className="site-search-row-main">
                      <b>{location.name}</b>
                      <small>
                        {location.city && <span>{location.city}</span>}
                        {location.platform_codes.map((code) => (
                          <PlatformBadge key={code} code={code} />
                        ))}
                      </small>
                    </span>
                  </a>
                );
              })}
            </section>
          )}

          {registered.length > 0 && (
            <section className="site-search-group">
              <h3>Участники сайта</h3>
              {registered.map((person) => {
                const i = nextIndex();
                return (
                  <a
                    key={person.href}
                    id={`site-search-item-${i}`}
                    data-index={i}
                    role="option"
                    aria-selected={selected === i}
                    href={person.href}
                    className={`site-search-row${selected === i ? " selected" : ""}`}
                    onMouseEnter={() => setSelected(i)}
                    onClick={(event) => {
                      event.preventDefault();
                      choose({ kind: "person", person });
                    }}
                  >
                    <span className="site-search-avatar">
                      {person.avatar_url ? <img src={person.avatar_url} alt="" /> : initials(person.display_name)}
                    </span>
                    <span className="site-search-row-main">
                      <b>{person.display_name}</b>
                      <small>
                        {personStats(person)}
                        {person.top_location_name && ` · чаще всего: ${person.top_location_name}`}
                      </small>
                    </span>
                  </a>
                );
              })}
            </section>
          )}

          {others.length > 0 && (
            <section className="site-search-group">
              <h3>В протоколах</h3>
              {others.map((person, n) => (
                <div key={`${person.display_name}-${n}`} className="site-search-row site-search-row-static">
                  <span className="site-search-avatar site-search-avatar-ghost">{initials(person.display_name)}</span>
                  <span className="site-search-row-main">
                    <b>{person.display_name}</b>
                    <small>
                      {personStats(person)}
                      {person.kind === "participant" && person.top_location_name &&
                        ` · чаще всего: ${placeWithCity(person.top_location_name, person.top_location_city)}`}
                    </small>
                    <em className="site-search-unregistered">не зарегистрирован на сайте</em>
                  </span>
                  <span className="site-search-badges">
                    {person.platform_codes.map((code) => (
                      <PlatformBadge key={code} code={code} />
                    ))}
                  </span>
                </div>
              ))}
              {fresh?.people_truncated && (
                <p className="site-search-note">Показаны первые совпадения — уточните запрос: добавьте имя или фамилию.</p>
              )}
            </section>
          )}

          {nothing && (
            <div className="site-search-empty">
              <p>
                По запросу «{normalized}» ничего не нашлось. Людей ищите по фамилии и имени, локации — по названию
                или городу.
              </p>
              <p className="site-search-empty-sub">Запрос сохранён — по таким запросам мы дописываем синонимы.</p>
            </div>
          )}

          {failed && <p className="site-search-note">Поиск по локациям и людям сейчас недоступен. Попробуйте через минуту.</p>}
        </div>

        <div className="site-search-foot">
          <span>
            <kbd>↑</kbd> <kbd>↓</kbd> выбрать · <kbd>Enter</kbd> открыть · <kbd>Esc</kbd> закрыть
          </span>
        </div>
      </div>
    </div>
  );
}
