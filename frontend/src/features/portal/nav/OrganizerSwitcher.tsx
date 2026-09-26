/**
 * Переключатель локаций в кабинете организатора (решение Дмитрия 23.09.2026:
 * кабинет организатора — отдельный раздел; одна локация — сразу её кабинет,
 * несколько — выбор). Название открытой локации работает как кнопка со
 * списком остальных своих локаций.
 *
 * Список локаций организатора — один на документ: переключатель стоит и в
 * колонке, и в полосе телефона, его же ждут шапка («Оргкабинет» ведёт сразу в
 * единственную локацию), крошки и страница «Мои локации». Раньше каждый
 * экземпляр ходил на сервер сам — два запроса на каждую страницу, у админа
 * это 2 × 60 КБ (code-7). Теперь запрос общий (один промис), а короткая
 * копия списка лежит в localStorage (см. organizerMemory): переходы между
 * инструментами идут без перезагрузки, а в новой вкладке список есть сразу.
 */
import { useEffect, useRef, useState } from "react";
import { getOrganizerLocations, type OrganizerLocationItem, type User } from "../../../lib/api";
import { CHEVRON_DOWN_ICON } from "./navIcons";
import {
  organizerLocationsSavedAt,
  readOrganizerLocations,
  writeOrganizerLocations,
  type OrganizerPlaceItem,
} from "./organizerMemory";
import { ORGANIZER_INDEX_HREF, type NavPlace } from "./siteNav";

// У админа в списке весь каталог — выпадашка на сотни строк бессмысленна,
// ему остаётся ссылка на «Мои локации» с поиском.
const MAX_DROPDOWN = 15;

// Сколько живёт сохранённый список: доступ к новой локации появится в меню
// не позже чем через 10 минут, а ходить за списком на каждой странице незачем.
const LIST_TTL_MS = 10 * 60 * 1000;

let inflight: { userId: string; promise: Promise<OrganizerLocationItem[]> } | null = null;
// Полный ответ, полученный в этом документе: странице «Мои локации» нужны
// все поля, а не только короткая копия из localStorage.
let fetched: { userId: string; items: OrganizerLocationItem[]; at: number } | null = null;
const listeners = new Set<() => void>();

/**
 * Список локаций организатора с сервера — не больше одного запроса на
 * документ (пока ответ свежий): все, кто спросил одновременно, получают один
 * и тот же промис.
 */
export function loadOrganizerLocations(userId: string): Promise<OrganizerLocationItem[]> {
  if (fetched && fetched.userId === userId && Date.now() - fetched.at < LIST_TTL_MS) {
    return Promise.resolve(fetched.items);
  }
  if (inflight && inflight.userId === userId) {
    return inflight.promise;
  }
  const promise = getOrganizerLocations()
    .then((payload) => {
      fetched = { userId, items: payload.items, at: Date.now() };
      writeOrganizerLocations(
        userId,
        payload.items.map(({ slug, name, city }) => ({ slug, name, city })),
      );
      for (const listener of listeners) listener();
      return payload.items;
    })
    .finally(() => {
      if (inflight?.promise === promise) inflight = null;
    });
  inflight = { userId, promise };
  return promise;
}

/**
 * Короткий список локаций организатора (slug, имя, город) или null, пока
 * его нет. Сохранённая копия отдаётся сразу; за свежей хук сходит сам, если
 * копии нет или она старше LIST_TTL_MS. user=null — хук спит (так шапка
 * выключает его у тех, кому он не нужен).
 */
export function useOrganizerLocations(user: User | null | undefined): OrganizerPlaceItem[] | null {
  const userId = user?.id ?? null;
  const [items, setItems] = useState<OrganizerPlaceItem[] | null>(() => (userId ? readOrganizerLocations(userId) : null));
  useEffect(() => {
    if (!userId) {
      setItems(null);
      return;
    }
    const sync = () => setItems(readOrganizerLocations(userId));
    listeners.add(sync);
    sync();
    const savedAt = organizerLocationsSavedAt(userId);
    if (savedAt === null || Date.now() - savedAt > LIST_TTL_MS) {
      loadOrganizerLocations(userId).catch(() => {
        // нет доступа или сеть — меню просто ведёт на /organizer
      });
    }
    return () => {
      listeners.delete(sync);
    };
  }, [userId]);
  return items;
}

export function OrganizerSwitcher({
  user,
  place,
  variant,
}: {
  user: User | null | undefined;
  place: NavPlace;
  variant: "column" | "chip";
}) {
  const items = useOrganizerLocations(user);
  const [open, setOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);
  const buttonRef = useRef<HTMLButtonElement>(null);
  const listRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    // Список — раскрывашка из обычных ссылок: первым Tab попадаем в него,
    // Esc и уход фокуса наружу закрывают (a11y-2).
    listRef.current?.querySelector<HTMLElement>("a")?.focus({ preventScroll: true });
    const onPointer = (event: PointerEvent) => {
      if (!rootRef.current?.contains(event.target as Node)) setOpen(false);
    };
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        setOpen(false);
        buttonRef.current?.focus();
      }
    };
    const onFocus = (event: FocusEvent) => {
      if (!rootRef.current?.contains(event.target as Node)) setOpen(false);
    };
    document.addEventListener("pointerdown", onPointer);
    document.addEventListener("keydown", onKey);
    document.addEventListener("focusin", onFocus);
    return () => {
      document.removeEventListener("pointerdown", onPointer);
      document.removeEventListener("keydown", onKey);
      document.removeEventListener("focusin", onFocus);
    };
  }, [open]);

  const others = (items ?? []).filter((item) => item.slug !== place.slug);
  const name = items?.find((item) => item.slug === place.slug)?.name ?? place.name;
  const className = `site-org-switch site-org-switch-${variant}`;

  // Пока список не приехал, или своя локация одна — переключать не на что:
  // это просто заголовок. Раньше до ответа рисовалась кнопка со стрелкой, а в
  // её списке была только ссылка на «Мои локации», которая при одной локации
  // тут же возвращала обратно (a11y-10).
  if (items === null || others.length === 0) {
    return (
      <div className={className}>
        <span className="site-org-switch-name">{name}</span>
      </div>
    );
  }
  if (items.length > MAX_DROPDOWN) {
    // В полосе телефона места на «имя + ссылку» нет: само имя ведёт к списку.
    if (variant === "chip") {
      return (
        <div className={className}>
          <a className="site-org-switch-button" href={ORGANIZER_INDEX_HREF} title="Выбрать другую локацию">
            <span className="site-org-switch-name">{name}</span>
            <span className="site-org-switch-chevron">{CHEVRON_DOWN_ICON}</span>
          </a>
        </div>
      );
    }
    return (
      <div className={className}>
        <span className="site-org-switch-name">{name}</span>
        <a className="site-org-switch-all" href={ORGANIZER_INDEX_HREF}>
          Другая локация
        </a>
      </div>
    );
  }

  const listId = `site-org-switch-list-${variant}`;
  return (
    <div className={className} ref={rootRef}>
      <button
        ref={buttonRef}
        type="button"
        className="site-org-switch-button"
        aria-expanded={open}
        aria-controls={open ? listId : undefined}
        title="Другая локация"
        onClick={() => setOpen((value) => !value)}
      >
        <span className="site-org-switch-name">{name}</span>
        <span className="site-org-switch-chevron">{CHEVRON_DOWN_ICON}</span>
      </button>
      {open && (
        <div className="site-org-switch-menu" id={listId} ref={listRef}>
          {others.map((item) => (
            <a key={item.slug} className="site-org-switch-item" href={`/organizer/${item.slug}`}>
              <span>{item.name}</span>
              {item.city && <small>{item.city}</small>}
            </a>
          ))}
          <a className="site-org-switch-item site-org-switch-item-all" href={ORGANIZER_INDEX_HREF}>
            Все мои локации
          </a>
        </div>
      )}
    </div>
  );
}
