/**
 * Переключатель локаций в кабинете организатора (решение Дмитрия 23.09.2026:
 * кабинет организатора — отдельный раздел; одна локация — сразу её кабинет,
 * несколько — выбор). Название открытой локации работает как кнопка со
 * списком остальных своих локаций.
 *
 * Список берём из того же /organizer/locations, что и страница «Мои локации»,
 * и держим в sessionStorage: сайт — MPA, и без кэша на каждом переходе между
 * инструментами переключатель бы мигал, пока едет ответ.
 */
import { useEffect, useRef, useState } from "react";
import { getOrganizerLocations, type OrganizerLocationItem, type User } from "../../../lib/api";
import { CHEVRON_DOWN_ICON } from "./navIcons";
import { ORGANIZER_INDEX_HREF, type NavPlace } from "./siteNav";

const CACHE_KEY = "organizerLocations:v1";
// У админа в списке весь каталог — выпадашка на сотни строк бессмысленна,
// ему остаётся ссылка на «Мои локации» с поиском.
const MAX_DROPDOWN = 15;

type Cached = { userId: string; items: Pick<OrganizerLocationItem, "slug" | "name" | "city">[] };

function readCache(userId: string): Cached["items"] | null {
  try {
    const raw = sessionStorage.getItem(CACHE_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw) as Cached;
    return parsed.userId === userId && Array.isArray(parsed.items) ? parsed.items : null;
  } catch {
    return null;
  }
}

function writeCache(userId: string, items: Cached["items"]): void {
  try {
    sessionStorage.setItem(CACHE_KEY, JSON.stringify({ userId, items }));
  } catch {
    // приватный режим — просто без кэша
  }
}

export function useOrganizerLocations(user: User | null | undefined): Cached["items"] | null {
  const userId = user?.id ?? null;
  const [items, setItems] = useState<Cached["items"] | null>(() => (userId ? readCache(userId) : null));
  useEffect(() => {
    if (!userId) return;
    let cancelled = false;
    getOrganizerLocations()
      .then((payload) => {
        if (cancelled) return;
        const slim = payload.items.map(({ slug, name, city }) => ({ slug, name, city }));
        writeCache(userId, slim);
        setItems(slim);
      })
      .catch(() => {
        // нет доступа или сеть — переключатель просто покажет название
      });
    return () => {
      cancelled = true;
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

  useEffect(() => {
    if (!open) return;
    const onPointer = (event: PointerEvent) => {
      if (!rootRef.current?.contains(event.target as Node)) setOpen(false);
    };
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") setOpen(false);
    };
    document.addEventListener("pointerdown", onPointer);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("pointerdown", onPointer);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  const others = (items ?? []).filter((item) => item.slug !== place.slug);
  const name = items?.find((item) => item.slug === place.slug)?.name ?? place.name;
  const className = `site-org-switch site-org-switch-${variant}`;

  // Одна своя локация — переключать не на что, это просто заголовок.
  if (items !== null && others.length === 0) {
    return (
      <div className={className}>
        <span className="site-org-switch-name">{name}</span>
      </div>
    );
  }
  if (items !== null && items.length > MAX_DROPDOWN) {
    return (
      <div className={className}>
        <span className="site-org-switch-name">{name}</span>
        <a className="site-org-switch-all" href={ORGANIZER_INDEX_HREF}>
          Другая локация
        </a>
      </div>
    );
  }

  return (
    <div className={className} ref={rootRef}>
      <button
        type="button"
        className="site-org-switch-button"
        aria-haspopup="true"
        aria-expanded={open}
        onClick={() => setOpen((value) => !value)}
      >
        <span className="site-org-switch-name">{name}</span>
        <span className="site-org-switch-chevron">{CHEVRON_DOWN_ICON}</span>
      </button>
      {open && (
        <div className="site-org-switch-menu" role="menu">
          {others.map((item) => (
            <a key={item.slug} role="menuitem" className="site-org-switch-item" href={`/organizer/${item.slug}`}>
              <span>{item.name}</span>
              {item.city && <small>{item.city}</small>}
            </a>
          ))}
          <a role="menuitem" className="site-org-switch-item site-org-switch-item-all" href={ORGANIZER_INDEX_HREF}>
            Все мои локации
          </a>
        </div>
      )}
    </div>
  );
}
