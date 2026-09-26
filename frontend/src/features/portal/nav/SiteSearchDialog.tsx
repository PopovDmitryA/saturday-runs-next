/**
 * Поиск по сайту (решение Дмитрия 23.09.2026: «делаем сразу»).
 *
 * Ищет три вещи: страницы сайта (по дереву навигации и синонимам — на
 * клиенте, см. pageSearch), локации и людей (на сервере, /api/search). Люди,
 * у которых запрос нашёлся только в середине слова имени («лев» в
 * «Михалевском»), — отдельной группой ниже всех остальных. Людей
 * два вида:
 * - зарегистрированные на сайте с открытым профилем — строка со ссылкой;
 * - все остальные из протоколов (и с закрытым профилем) — только цифры, без
 *   перехода. Ссылок на профили в беговых системах нет намеренно: согласия
 *   этих людей на это у нас нет. Гостю над ними — одна строка «Нашли себя?
 *   Войдите…»: это приглашение, а не ссылка на чужой профиль.
 *
 * Открывается кнопками в шапке и «Меню», ⌘K / Ctrl+K и «/» (по физической
 * клавише — работает и в русской раскладке). На телефоне — на весь экран.
 *
 * Окно ведёт себя как отдельный экран: «Назад» закрывает его, не уводя со
 * страницы (nav/useOverlayHistory), Tab ходит внутри окна, после закрытия
 * фокус возвращается туда, где был (nav/useOverlayFocus). Переход по
 * результату — как по обычной ссылке сайта: без перезагрузки, а Ctrl/⌘-клик
 * и средняя кнопка открывают новую вкладку.
 *
 * Каждый поиск пишется в журнал (анонимно) — и когда человек закрыл окно,
 * ушёл «Назад» или закрыл вкладку, ничего не выбрав: именно такие поиски —
 * список недостающих синонимов (/admin/search).
 */
import { useCallback, useEffect, useMemo, useRef, useState, type MouseEvent as ReactMouseEvent } from "react";
import {
  ApiError,
  logSiteSearch,
  searchSite,
  type SiteSearchLocation,
  type SiteSearchPerson,
  type SiteSearchResponse,
  type User,
} from "../../../lib/api";
import { lockBodyScroll } from "../../../lib/bodyScrollLock";
import { formatDate } from "../../../lib/format";
import { PORTAL_LOGIN_HREF } from "../../../lib/portalRoutes";
import { getRecentLocations } from "../../../lib/recentLocations";
import { useOptionalUser } from "../../../lib/useOptionalUser";
import { PlatformBadge } from "../../../components/PlatformBadge";
import type { SiteSearchOpenDetailWithMode, SiteSearchOpenMode } from "./findSelfSearch";
import { CATALOG_ICON, CLOSE_ICON, LOCATIONS_ICON, SEARCH_ICON } from "./navIcons";
import { resolveNavState } from "./navState";
import { useOrganizerLocations } from "./OrganizerSwitcher";
import {
  combineLocationPages,
  mergePageHits,
  normalizeQuery,
  placelessLocationPages,
  planSearch,
  type KnownPlace,
  type PageHit,
  type SearchContext,
} from "./pageSearch";
import { buildSiteNav, type NavPlace, type NavSection } from "./siteNav";
import { SITE_SEARCH_OPEN_EVENT } from "./siteSearchBus";
import { useOverlayFocus } from "./useOverlayFocus";
import { useOverlayHistory } from "./useOverlayHistory";
import "./siteSearch.css";

/**
 * Пауза в наборе, после которой уходит запрос к серверу. У поиска лимит
 * запросов в минуту на адрес, и при 220 мс почти каждая набранная на телефоне
 * буква становилась запросом: 15 букв — 12 запросов, 4–5 имён подряд — и
 * поиск упирался в 429 (ревью перед пушем 26.09.2026, S2). На телефоне
 * печатают медленнее, чем на клавиатуре, — там и ждём дольше.
 */
const DEBOUNCE_MS = 300;
const DEBOUNCE_MOBILE_MS = 350;
/**
 * Сервер ответил 429: ждём, сколько он сказал в Retry-After (без заголовка —
 * THROTTLE_FALLBACK_S), но не меньше пары секунд — и шлём один запрос по
 * последнему набранному, а не запрос на каждую букву: пока пауза не вышла,
 * каждый из них получил бы тот же отказ. Пауза длиннее минуты (окно лимита
 * поиска) — это уже блокировка адреса целиком: повторять бессмысленно, окно
 * честно говорит, что поиск недоступен.
 */
const THROTTLE_FALLBACK_S = 4;
const THROTTLE_MIN_S = 3;
const THROTTLE_MAX_S = 60;
const MIN_SERVER_QUERY = 2;
// Сколько локаций организатора разворачивать в инструменты для поиска: у
// обычного организатора их одна-две, больше десятка — уже не организатор.
const ORGANIZER_PLACES_LIMIT = 12;

type ClickKind = "page" | "location" | "person";

type Selectable = { kind: ClickKind; href: string };

type RegisteredPerson = Extract<SiteSearchPerson, { kind: "registered" }>;

const LISTBOX_ID = "site-search-listbox";
const LISTBOX_MORE_ID = "site-search-listbox-more";

// Пример для подсказки «допишите название локации» — известные парки, кроме
// тех, что уже предложены строками выдачи (и открытой сейчас).
const SAMPLE_PLACES: readonly { slug: string; name: string }[] = [
  { slug: "sokolniki", name: "сокольники" },
  { slug: "kuzminki", name: "кузьминки" },
  { slug: "kolomenskoe", name: "коломенское" },
  { slug: "izmailovo", name: "измайлово" },
];

function samplePlace(places: KnownPlace[]): string {
  const taken = places.flatMap((place) => [place.slug.toLowerCase(), normalizeQuery(place.name)]);
  return (SAMPLE_PLACES.find((sample) => !taken.includes(sample.slug) && !taken.includes(sample.name)) ?? SAMPLE_PLACES[0])
    .name;
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
  // Однофамильцы упорядочены по последнему старту — без даты на экране
  // порядок выглядел случайным.
  const last = person.last_run_date ? ` · последний старт ${formatDate(person.last_run_date)}` : "";
  return `${runs} · ${vol}${last}`;
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

// Пока страница локации не загрузилась, дерево знает только адрес — и
// подписывало бы «Погода · sokolniki». Служебную латиницу не показываем.
const SLUG_LIKE = /^[a-z0-9-]+$/;
const UNNAMED_PLACE = "Эта локация";

function withReadablePlaces(sections: NavSection[]): NavSection[] {
  return sections.map((section) => ({
    ...section,
    groups: section.groups.map((group) =>
      group.context && group.title && SLUG_LIKE.test(group.title) ? { ...group, title: UNNAMED_PLACE } : group,
    ),
  }));
}

/** Открытая сейчас локация — из дерева навигации (группа страниц локации). */
function currentPlace(sections: NavSection[]): NavPlace | null {
  const group = sections.find((section) => section.key === "locations")?.groups.find((item) => item.key === "place");
  const href = group?.items[0]?.href;
  const match = href?.match(/^\/locations\/([^/]+)/);
  if (!group || !match) return null;
  let slug = match[1];
  try {
    slug = decodeURIComponent(slug);
  } catch {
    // битый адрес — оставляем как есть
  }
  // Имени ещё нет — подпись заглушки в название строки не тащим
  // («Погода · Эта локация»): строка сама подписана «Эта локация».
  const title = group.title ?? "";
  return { slug, name: title && title !== UNNAMED_PLACE && !SLUG_LIKE.test(title) ? title : "" };
}

/**
 * Где искать «погоду» без названия локации: открытая сейчас, своя (из
 * профиля, User.home_location), последняя открытая в этом браузере.
 */
function knownPlaces(sections: NavSection[], user: User | null | undefined): KnownPlace[] {
  const out: KnownPlace[] = [];
  const push = (place: NavPlace | null | undefined, why: KnownPlace["why"]) => {
    if (!place?.slug || out.some((item) => item.slug === place.slug)) return;
    out.push({ slug: place.slug, name: place.name, why });
  };
  push(currentPlace(sections), "current");
  push(user?.home_location ?? null, "home");
  push(
    getRecentLocations().find((place) => !out.some((item) => item.slug === place.slug)),
    "recent",
  );
  return out;
}

type Pending = {
  query: string;
  corrected: string | null;
  pages: number;
  locations: number;
  people: number;
  peopleSkipped: boolean;
};

export function SiteSearchDialog() {
  const user = useOptionalUser();
  const [open, setOpen] = useState(false);
  const [openSeq, setOpenSeq] = useState(0);
  const [mode, setMode] = useState<SiteSearchOpenMode | null>(null);
  const [query, setQuery] = useState("");
  const [response, setResponse] = useState<SiteSearchResponse | null>(null);
  const [responseFor, setResponseFor] = useState("");
  const [loading, setLoading] = useState(false);
  const [failed, setFailed] = useState(false);
  // Сервер попросил подождать (429): на сколько секунд — для текста подсказки.
  const [throttle, setThrottle] = useState<number | null>(null);
  // До какого момента (Date.now()) запросы к серверу не шлём. В ref, а не в
  // состоянии: окно смонтировано всегда, и пауза переживает закрытие и
  // повторное открытие поиска.
  const throttledUntilRef = useRef(0);
  const blockedUntilRef = useRef(0);
  // Повторить запрос после паузы: эффект запроса перезапускается по этому счётчику.
  const [retryTick, setRetryTick] = useState(0);
  const [selected, setSelected] = useState(0);
  const dialogRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const listRef = useRef<HTMLDivElement>(null);
  const openRef = useRef(open);
  useEffect(() => {
    openRef.current = open;
  }, [open]);
  // Последний «устоявшийся» поиск, ещё не записанный в журнал. Пишем, когда
  // человек начал другой поиск, выбрал результат, закрыл окно или ушёл со
  // страницы — а не на каждую букву, иначе журнал забьётся обрывками «Ива»,
  // «Иван», «Иванов».
  const pendingRef = useRef<Pending | null>(null);

  const flushLog = useCallback((click: { kind: ClickKind; target: string } | null) => {
    const pending = pendingRef.current;
    pendingRef.current = null;
    if (!pending || pending.query.length < 2) return;
    const entry = {
      query: pending.query,
      corrected_query: pending.corrected,
      pages_found: pending.pages,
      locations_found: pending.locations,
      people_found: pending.people,
      clicked_kind: click?.kind ?? null,
      clicked_target: click?.target ?? null,
      is_mobile: isMobileViewport(),
      people_skipped: pending.peopleSkipped,
    };
    // Запись журнала уходит сразу и один раз: у неё своё ведро лимита на
    // сервере, пауза поиска после 429 её не касается.
    logSiteSearch(entry);
  }, []);

  // Окно закрылось (любым путём: «Назад», Esc, крестик, переход) — пишем
  // несостоявшийся поиск в журнал.
  const handleClosed = useCallback(() => {
    flushLog(null);
    setOpen(false);
    setMode(null);
  }, [flushLog]);

  const overlay = useOverlayHistory(open, handleClosed);
  const overlayRef = useRef(overlay);
  useEffect(() => {
    overlayRef.current = overlay;
  });

  useOverlayFocus({
    open,
    containers: [dialogRef],
    initial: () => {
      inputRef.current?.select();
      return inputRef.current;
    },
    onEscape: overlay.dismiss,
  });

  const openWith = useCallback((detail?: SiteSearchOpenDetailWithMode) => {
    setMode(detail?.mode ?? null);
    if (typeof detail?.query === "string") {
      setQuery(detail.query);
    } else if (detail?.mode === "find-self") {
      setQuery("");
    }
    setOpenSeq((value) => value + 1);
    setOpen(true);
  }, []);

  // Открытие: событие от кнопок и горячие клавиши. Клавиши сравниваем по
  // физической кнопке (event.code): в русской раскладке event.key у Ctrl+K —
  // «л», а у «/» — «.», и по ним окно не открывалось.
  useEffect(() => {
    const onOpen = (event: Event) => openWith((event as CustomEvent<SiteSearchOpenDetailWithMode>).detail);
    const onKey = (event: KeyboardEvent) => {
      if ((event.metaKey || event.ctrlKey) && !event.altKey && (event.code === "KeyK" || event.key.toLowerCase() === "k")) {
        event.preventDefault();
        if (openRef.current) {
          overlayRef.current.dismiss();
        } else {
          openWith();
        }
        return;
      }
      if (
        (event.code === "Slash" || event.key === "/") &&
        !event.metaKey &&
        !event.ctrlKey &&
        !event.altKey &&
        !event.shiftKey &&
        !openRef.current &&
        !isTypingTarget(event.target)
      ) {
        event.preventDefault();
        openWith();
      }
    };
    window.addEventListener(SITE_SEARCH_OPEN_EVENT, onOpen);
    window.addEventListener("keydown", onKey);
    return () => {
      window.removeEventListener(SITE_SEARCH_OPEN_EVENT, onOpen);
      window.removeEventListener("keydown", onKey);
    };
  }, [openWith]);

  // Пока окно открыто, страница под ним не прокручивается. Закрылось (в том
  // числе переходом по результату) — прокрутка возвращается. Блокировка общая
  // со счётчиком (lib/bodyScrollLock): поиск открывают поверх «Меню» и списка
  // полосы, и те закрываются уже после того, как поиск открылся.
  useEffect(() => {
    if (!open) return;
    return lockBodyScroll();
  }, [open]);

  // Ушли со страницы или закрыли вкладку с открытым поиском — журнал всё
  // равно должен узнать, что искали (sendBeacon в logSiteSearch долетит).
  useEffect(() => {
    if (!open) return;
    const onHide = () => flushLog(null);
    const onVisibility = () => {
      if (document.visibilityState === "hidden") flushLog(null);
    };
    window.addEventListener("pagehide", onHide);
    document.addEventListener("visibilitychange", onVisibility);
    return () => {
      window.removeEventListener("pagehide", onHide);
      document.removeEventListener("visibilitychange", onVisibility);
    };
  }, [open, flushLog]);

  // Дерево — заново при каждом открытии: окно живёт поверх всех страниц, и
  // запомненное с первой страницы дерево вело «погоду» с Кузьминок в
  // Сокольники (code-3).
  const sections = useMemo(
    () => (open ? withReadablePlaces(resolveNavState({ user }).sections) : []),
    // openSeq — сигнал «окно открыли заново»: пересчитать по текущему адресу.
    [user, open, openSeq],
  );
  const places = useMemo(
    () => (open ? knownPlaces(sections, user) : []),
    [sections, user, open, openSeq],
  );

  // Инструменты организатора — всех его локаций, из любого места сайта
  // («юбилеи», «пост»). Админу не нужно: у него в списке весь каталог.
  const organizerUser = open && user && user.is_organizer && !user.is_admin ? user : null;
  const organizerPlaces = useOrganizerLocations(organizerUser);
  const extraLinks = useMemo<SearchContext["extraLinks"]>(() => {
    if (!organizerUser || !organizerPlaces || organizerPlaces.length === 0) return [];
    const many = organizerPlaces.length > 1;
    const out: NonNullable<SearchContext["extraLinks"]>[number][] = [];
    for (const place of organizerPlaces.slice(0, ORGANIZER_PLACES_LIMIT)) {
      const section = buildSiteNav({
        user: organizerUser,
        organizerLocation: { slug: place.slug, name: place.name },
        inOrganizer: true,
      }).find((item) => item.key === "organizer");
      for (const group of section?.groups ?? []) {
        for (const link of group.items) {
          out.push({
            link: many ? { ...link, label: `${link.label} · ${place.name}` } : link,
            context: [section?.label, group.title].filter(Boolean).join(" · "),
            weightKey: `organizer:${link.key}`,
            placeName: place.name,
          });
        }
      }
    }
    return out;
  }, [organizerUser, organizerPlaces]);

  const plan = useMemo(
    () => planSearch(query, sections, { guest: user === null, extraLinks }),
    [query, sections, user, extraLinks],
  );

  // Запрос к серверу — с задержкой и отменой предыдущего. Сервер попросил
  // подождать (429) — ждём конца паузы и шлём один запрос, по последнему
  // набранному, а не по запросу на каждую букву.
  const serverQuery = plan.serverQuery;
  useEffect(() => {
    if (!open) return;
    if (serverQuery.length < MIN_SERVER_QUERY) {
      setResponse(null);
      setResponseFor("");
      setLoading(false);
      setFailed(false);
      setThrottle(null);
      return;
    }
    if (blockedUntilRef.current > Date.now()) {
      // Адрес заблокирован целиком — запросов не шлём, окно говорит, что
      // поиск недоступен (см. THROTTLE_MAX_S).
      setLoading(false);
      setThrottle(null);
      setFailed(true);
      return;
    }
    const controller = new AbortController();
    const pause = throttledUntilRef.current - Date.now();
    const debounce = isMobileViewport() ? DEBOUNCE_MOBILE_MS : DEBOUNCE_MS;
    if (pause > 0) {
      // Вместо крутилки — спокойная подсказка «подождите».
      setLoading(false);
      setThrottle((value) => value ?? Math.ceil(pause / 1000));
    } else {
      setLoading(true);
      setThrottle(null);
    }
    const timer = window.setTimeout(
      () => {
        setLoading(true);
        searchSite(serverQuery, controller.signal)
          .then((payload) => {
            setResponse(payload);
            setResponseFor(serverQuery);
            setFailed(false);
            setThrottle(null);
          })
          .catch((error: unknown) => {
            if (error instanceof DOMException && error.name === "AbortError") return;
            if (error instanceof ApiError && error.status === 429) {
              const asked = error.retryAfterSeconds ?? THROTTLE_FALLBACK_S;
              if (asked > THROTTLE_MAX_S) {
                blockedUntilRef.current = Date.now() + asked * 1000;
                setThrottle(null);
                setFailed(true);
                return;
              }
              const seconds = Math.max(THROTTLE_MIN_S, asked);
              throttledUntilRef.current = Date.now() + seconds * 1000;
              setThrottle(seconds);
              setFailed(false);
              // Перезапуск эффекта: он дождётся конца паузы и повторит запрос.
              setRetryTick((value) => value + 1);
              return;
            }
            // Иная ошибка после паузы: подсказка «подождите, продолжится сам»
            // больше не правда — оставляем только сообщение о недоступности.
            setThrottle(null);
            setFailed(true);
          })
          .finally(() => {
            if (!controller.signal.aborted) setLoading(false);
          });
      },
      Math.max(debounce, pause),
    );
    return () => {
      controller.abort();
      window.clearTimeout(timer);
    };
  }, [open, serverQuery, retryTick]);

  const fresh = response && responseFor === serverQuery ? response : null;
  const locations: SiteSearchLocation[] = fresh?.locations ?? [];
  const allLocations = fresh?.locations_all ?? null;
  // Гость спрашивает про своё («с кем я бегал», «мои пробежки») — ответ на
  // это вход, а не чужие люди, у которых в имени нашлось «бегал».
  const guest = user === null;
  const people = guest && plan.personal ? [] : (fresh?.people ?? []);
  const combos = plan.placeless ? placelessLocationPages(plan, places) : combineLocationPages(plan, locations);
  // «Моя: Мещерский» и та же локация в выдаче — одна и та же страница.
  const locationHrefs = new Set(locations.map((location) => location.href));
  const pages: PageHit[] = mergePageHits(
    combos.filter((hit) => !locationHrefs.has(hit.href)),
    plan.pages.filter((hit) => !locationHrefs.has(hit.href)),
  );
  // Люди — два яруса (порядок задаёт сервер): у кого слова запроса совпали с
  // начала слова имени, и ниже, отдельной группой, — у кого только из
  // середины («лев» в «Михалевском»). Нет первых — вторые и есть выдача.
  const strongPeople = people.filter((person) => !person.partial);
  const mainPeople = strongPeople.length > 0 ? strongPeople : people;
  const partialPeople = strongPeople.length > 0 ? people.filter((person) => person.partial) : [];
  const isRegistered = (person: SiteSearchPerson): person is RegisteredPerson => person.kind === "registered";
  const registered = mainPeople.filter(isRegistered);
  const others = mainPeople.filter((person) => person.kind === "participant");
  const partialRegistered = partialPeople.filter(isRegistered);
  const partialOthers = partialPeople.filter((person) => person.kind === "participant");

  const allHref = allLocations ? `/locations?q=${encodeURIComponent(allLocations.query)}` : null;
  const selectables: Selectable[] = [
    ...pages.map((hit) => ({ kind: "page" as const, href: hit.href })),
    ...locations.map((location) => ({ kind: "location" as const, href: location.href })),
    ...(allHref ? [{ kind: "location" as const, href: allHref }] : []),
    ...registered.map((person) => ({ kind: "person" as const, href: person.href })),
    ...partialRegistered.map((person) => ({ kind: "person" as const, href: person.href })),
  ];
  const mainListCount = selectables.length - partialRegistered.length;

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
      peopleSkipped: fresh?.people_skipped === true,
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

  // Клик по результату: запись в журнал, дальше — как у обычной ссылки сайта.
  // Простой клик закрывает окно (снимая его запись из истории) и переходит
  // без перезагрузки; Ctrl/⌘/Shift-клик браузер открывает в новой вкладке сам.
  const onResultClick = (event: ReactMouseEvent<HTMLAnchorElement>, kind: ClickKind, target: string) => {
    flushLog({ kind, target });
    overlay.interceptLinks(event);
  };

  const onKeyDown = (event: React.KeyboardEvent<HTMLInputElement>) => {
    if (event.key === "ArrowDown") {
      event.preventDefault();
      setSelected((index) => Math.min(index + 1, Math.max(selectables.length - 1, 0)));
    } else if (event.key === "ArrowUp") {
      event.preventDefault();
      setSelected((index) => Math.max(index - 1, 0));
    } else if (event.key === "Enter") {
      const row = listRef.current?.querySelector<HTMLAnchorElement>(`#site-search-item-${selected}`);
      if (row) {
        event.preventDefault();
        // Тот же путь, что и у клика мышью: журнал, закрытие, переход.
        row.click();
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
  const waiting = serverQuery.length >= MIN_SERVER_QUERY && fresh === null && !failed;
  // «Погода» без локации, которую мы не знаем, — не «ничего не нашлось», а
  // подсказка дописать название (placelessHint).
  const nothing =
    hasQuery &&
    !loading &&
    !failed &&
    !plan.placeless &&
    pages.length === 0 &&
    locations.length === 0 &&
    people.length === 0 &&
    !waiting;
  const findSelf = mode === "find-self";
  // Не ошибка, а пауза: поиск продолжится сам, как только сервер разрешит.
  const throttleText =
    throttle === null
      ? ""
      : `Слишком много запросов, подождите ${throttle <= 10 ? "пару секунд" : "до минуты"} — поиск продолжится сам.`;

  // Подсказка «допишите название локации»: страница локации названа, а
  // локация — нет. Не нужна, если первой строкой уже стоит страница сайта,
  // названная в запросе целиком («самые быстрые» — это рейтинг).
  const firstPage = pages[0];
  const exactSitePage =
    firstPage !== undefined && firstPage.kind === "page" && firstPage.score >= 100 && !firstPage.href.startsWith("/locations/");
  const hint = plan.placeless && plan.placelessPhrase && !exactSitePage ? plan.placelessPhrase : "";
  const hintSample = hint ? `${hint} ${samplePlace(places)}` : "";
  const hintPage = plan.locationPages[0];
  const hintLead =
    places.length > 0
      ? "Другая локация? Допишите название:"
      : hintPage && hintPage.suffix !== ""
        ? `«${hintPage.label}» есть у каждой локации — допишите название:`
        : // «Обзор» — слово общее: «как добраться» живёт на главной странице локации.
          "Это есть на странице каждой локации — допишите название:";

  const found = [
    pages.length > 0 && `${pages.length} ${plural(pages.length, "страница", "страницы", "страниц")}`,
    locations.length > 0 && `${locations.length} ${plural(locations.length, "локация", "локации", "локаций")}`,
    people.length > 0 && `${people.length} ${plural(people.length, "участник", "участника", "участников")}`,
  ].filter(Boolean);
  const statusText = !hasQuery
    ? ""
    : throttleText
      ? throttleText
      : waiting
        ? "Ищем…"
        : nothing
          ? "Ничего не нашлось"
          : found.length > 0
            ? `Найдено: ${found.join(", ")}`
            : hint
              ? "Страница есть у каждой локации — допишите название локации"
              : "";
  // aria-controls — только на то, что нарисовано: ссылка на несуществующий
  // список сбивает диктор.
  const listboxIds = [mainListCount > 0 && LISTBOX_ID, partialRegistered.length > 0 && LISTBOX_MORE_ID]
    .filter(Boolean)
    .join(" ");

  const option = (i: number, href: string, kind: ClickKind, content: React.ReactNode, key: string) => (
    <a
      key={key}
      id={`site-search-item-${i}`}
      data-index={i}
      role="option"
      aria-selected={selected === i}
      href={href}
      className={`site-search-row${selected === i ? " selected" : ""}`}
      onMouseEnter={() => setSelected(i)}
      onClick={(event) => onResultClick(event, kind, href)}
    >
      {content}
    </a>
  );

  const example = (text: string) => (
    <button type="button" onClick={() => setQuery(text)}>
      {text}
    </button>
  );

  const placelessHint = hint ? (
    <p className="site-search-note">
      {hintLead} {example(hintSample)}
    </p>
  ) : null;

  // Совет, как сузить список однофамильцев. Место уже в запросе (нашли по
  // «Иванов Мытищи») — второй город не поможет, помогает имя.
  const peopleHint = fresh?.people_truncated
    ? plan.serverWords <= 1 || fresh.people_place
      ? "Показаны первые совпадения — добавьте имя или фамилию."
      : `Показаны первые совпадения — добавьте город: «${plan.serverQuery} Москва».`
    : null;

  const registeredOption = (person: RegisteredPerson) => {
    const i = nextIndex();
    return option(
      i,
      person.href,
      "person",
      <>
        <span className="site-search-avatar" aria-hidden="true">
          {person.avatar_url ? <img src={person.avatar_url} alt="" /> : initials(person.display_name)}
        </span>
        <span className="site-search-row-main">
          <b>{person.display_name}</b>
          <small>
            {personStats(person)}
            {person.top_location_name && ` · чаще всего: ${person.top_location_name}`}
          </small>
        </span>
      </>,
      person.href,
    );
  };

  // Одна строка над людьми из протоколов — над первым таким блоком выдачи.
  const protocolsLead = guest ? (
    // Приглашение, а не ссылка на чужой профиль: нашёл себя — войди, и всё
    // посчитанное откроется. Правило о согласии соблюдено.
    <a
      className="site-search-invite"
      href={PORTAL_LOGIN_HREF}
      onClick={(event) => onResultClick(event, "page", PORTAL_LOGIN_HREF)}
    >
      Нашли себя? <u>Войдите</u> — пробежки, рекорды и карта уже посчитаны
    </a>
  ) : (
    <p className="site-search-caption">Профиля на нашем сайте нет — только цифры из протоколов.</p>
  );

  const protocolRows = (list: SiteSearchPerson[]) => (
    <ul className="site-search-protocol-list">
      {list.map((person, n) => (
        <li key={`${person.display_name}-${n}`} className="site-search-row site-search-row-static">
          <span className="site-search-avatar site-search-avatar-ghost" aria-hidden="true">
            {initials(person.display_name)}
          </span>
          <span className="site-search-row-main">
            <b>{person.display_name}</b>
            <small>
              {personStats(person)}
              {person.kind === "participant" &&
                person.top_location_name &&
                ` · чаще всего: ${placeWithCity(person.top_location_name, person.top_location_city)}`}
            </small>
          </span>
          <span className="site-search-badges">
            <span className="visually-hidden">Системы: </span>
            {person.platform_codes.map((code) => (
              <PlatformBadge key={code} code={code} />
            ))}
          </span>
        </li>
      ))}
    </ul>
  );

  return (
    <div className="site-search-backdrop" onClick={overlay.dismiss} role="presentation">
      <div
        ref={dialogRef}
        className="site-search"
        role="dialog"
        aria-modal="true"
        aria-label="Поиск по сайту"
        onClick={(event) => event.stopPropagation()}
      >
        <div className="site-search-bar">
          <span className="site-search-bar-icon" aria-hidden="true">
            {SEARCH_ICON}
          </span>
          <input
            ref={inputRef}
            id="site-search-input"
            className="site-search-input"
            type="search"
            inputMode="search"
            autoComplete="off"
            spellCheck={false}
            placeholder={findSelf ? "Фамилия и имя" : "Страница, локация или участник"}
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            onKeyDown={onKeyDown}
            role="combobox"
            aria-expanded={selectables.length > 0}
            aria-controls={listboxIds || undefined}
            aria-autocomplete="list"
            aria-activedescendant={selectables.length > 0 ? `site-search-item-${selected}` : undefined}
          />
          {loading && <span className="site-search-spinner" aria-hidden="true" />}
          <button type="button" className="site-search-close" onClick={overlay.dismiss} aria-label="Закрыть поиск">
            <span className="site-search-close-icon" aria-hidden="true">
              {CLOSE_ICON}
            </span>
            <span className="site-search-close-text" aria-hidden="true">
              Отмена
            </span>
          </button>
        </div>

        {/* Итог поиска для экранного диктора: список он не перечитывает сам. */}
        <p className="visually-hidden" role="status" aria-live="polite">
          {statusText}
        </p>

        <div className="site-search-results" ref={listRef}>
          {!hasQuery && (
            <div className="site-search-empty">
              {findSelf || guest ? (
                <>
                  <p className="site-search-lead">
                    Найдите себя: введите фамилию и имя — покажем пробежки из протоколов 5 вёрст, S95, parkrun и
                    RunPark.
                  </p>
                  {!findSelf && (
                    <p>
                      Или: {example("сокольники")}, {example("самые быстрые")}, {example("как войти")}.
                    </p>
                  )}
                </>
              ) : (
                <p>
                  Например: {places.length > 0 && <>{example("погода")}, </>}
                  {example("мои пробежки")}, {example("рекорды локаций")}
                  {organizerUser && <>, {example("юбилеи")}</>} или фамилия участника.
                </p>
              )}
            </div>
          )}

          {fresh?.corrected_query && (
            <p className="site-search-note">
              Показаны результаты для «{fresh.corrected_query}» — похоже, была включена другая раскладка.
            </p>
          )}

          {throttleText && <p className="site-search-note">{throttleText}</p>}

          {mainListCount > 0 && (
            <div role="listbox" id={LISTBOX_ID} aria-label="Результаты поиска">
              {pages.length > 0 && (
                <div className="site-search-group" role="group" aria-label="Разделы сайта">
                  <h3 aria-hidden="true">Разделы сайта</h3>
                  {pages.map((hit) => {
                    const i = nextIndex();
                    return option(
                      i,
                      hit.href,
                      "page",
                      <>
                        <span className="site-search-row-mark site-search-row-mark-icon" aria-hidden="true">
                          {hit.icon ?? SEARCH_ICON}
                        </span>
                        <span className="site-search-row-main">
                          <b>{hit.label}</b>
                          <small>{hit.context}</small>
                        </span>
                      </>,
                      hit.key,
                    );
                  })}
                </div>
              )}

              {(locations.length > 0 || allHref) && (
                <div
                  className="site-search-group"
                  role="group"
                  aria-label={fresh?.locations_similar ? "Похожие локации" : "Локации"}
                >
                  <h3 aria-hidden="true">{fresh?.locations_similar ? "Похожие локации" : "Локации"}</h3>
                  {locations.map((location) => {
                    const i = nextIndex();
                    return option(
                      i,
                      location.href,
                      "location",
                      <>
                        <span className="site-search-row-mark site-search-row-mark-icon" aria-hidden="true">
                          {LOCATIONS_ICON}
                        </span>
                        <span className="site-search-row-main">
                          <b>{location.name}</b>
                          <small>
                            {location.city && <span>{location.city}</span>}
                            {location.platform_codes.map((code) => (
                              <PlatformBadge key={code} code={code} />
                            ))}
                          </small>
                        </span>
                      </>,
                      location.slug,
                    );
                  })}
                  {allLocations && allHref &&
                    option(
                      nextIndex(),
                      allHref,
                      "location",
                      <>
                        <span className="site-search-row-mark site-search-row-mark-icon" aria-hidden="true">
                          {CATALOG_ICON}
                        </span>
                        <span className="site-search-row-main">
                          <b className="site-search-all">
                            Все локации: {allLocations.label} ({allLocations.count}) →
                          </b>
                          <small>Каталог с фильтром</small>
                        </span>
                      </>,
                      "all-locations",
                    )}
                </div>
              )}

              {registered.length > 0 && (
                <div className="site-search-group" role="group" aria-label="Участники сайта">
                  <h3 aria-hidden="true">Участники сайта</h3>
                  {registered.map(registeredOption)}
                </div>
              )}
            </div>
          )}

          {placelessHint}

          {fresh?.locations_similar && (
            <p className="site-search-note">Точно такой локации не нашлось — может быть, одна из похожих выше.</p>
          )}

          {fresh?.people_place && people.length > 0 && (
            <p className="site-search-note">Люди с этим именем, которые бегали или помогали: {fresh.people_place}.</p>
          )}

          {others.length > 0 && (
            <section className="site-search-group site-search-protocols" aria-label="Из протоколов">
              <h3>Из протоколов</h3>
              {protocolsLead}
              {protocolRows(others)}
            </section>
          )}

          {partialPeople.length > 0 && (
            // Совпадения из середины слова — после всех совпадений с начала:
            // на «Лев» сначала Львы и Левины, а Михалевские — здесь.
            <section className="site-search-group site-search-partial" aria-label="Совпадение внутри имени">
              <h3>Совпадение внутри имени</h3>
              {partialRegistered.length > 0 && (
                <div role="listbox" id={LISTBOX_MORE_ID} aria-label="Участники сайта: совпадение внутри имени">
                  {partialRegistered.map(registeredOption)}
                </div>
              )}
              {others.length === 0 && partialOthers.length > 0 && protocolsLead}
              {partialOthers.length > 0 && protocolRows(partialOthers)}
            </section>
          )}

          {peopleHint && <p className="site-search-note">{peopleHint}</p>}

          {nothing && (
            <div className="site-search-empty">
              <p>
                По запросу «{normalized}» ничего не нашлось. Людей ищите по фамилии и имени, локации — по названию
                или городу.
              </p>
              {guest && (
                <p>
                  Ищете себя? <a href={PORTAL_LOGIN_HREF} onClick={(event) => onResultClick(event, "page", PORTAL_LOGIN_HREF)}>Войдите</a>{" "}
                  — после входа сайт сам найдёт ваши профили во всех системах.
                </p>
              )}
              {/* Поиски админа сервер не пишет — не обещаем ему того, чего нет. */}
              {!user?.is_admin && (
                <p className="site-search-empty-sub">Запрос сохранён — по таким запросам мы дописываем синонимы.</p>
              )}
            </div>
          )}

          {failed && <p className="site-search-note">Поиск по локациям и людям сейчас недоступен. Попробуйте через минуту.</p>}
          {!failed && fresh?.people_skipped && (
            <p className="site-search-note">
              Сейчас много запросов — людей в этот раз не искали. Повторите поиск через пару секунд.
            </p>
          )}
        </div>

        <div className="site-search-foot" aria-hidden="true">
          <span>
            <kbd>↑</kbd> <kbd>↓</kbd> выбрать · <kbd>Enter</kbd> открыть · <kbd>Esc</kbd> закрыть
          </span>
        </div>
      </div>
    </div>
  );
}
