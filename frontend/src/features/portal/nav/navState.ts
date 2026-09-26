/**
 * Где человек сейчас: раздел, открытая локация или кабинет организатора.
 *
 * Страницы по-прежнему передают `active` и `location` (так было у сайдбара),
 * а всё, что страница не передала, достраивается по адресу — поэтому нижняя
 * панель, «Меню» и ссылки шапки работают и на главной, в блоге и в «О
 * проекте», где своего `active` нет.
 */
import type { User } from "../../../lib/api";
import { locationHintFor } from "../../../lib/locationHint";
import { isOwnHandle } from "../../../lib/portalRoutes";
import {
  buildSiteNav,
  CABINET_TAB_KEYS,
  sectionKeyFromPath,
  type CabinetTabKey,
  type NavPlace,
  type NavSection,
  type NavSectionKey,
} from "./siteNav";

/** Что подсвечивать: вкладка ЛК, раздел сайта или ничего. */
export type SiteSidebarActive =
  | CabinetTabKey
  | "locations"
  | "results"
  | "last-results"
  | "unified-protocol"
  | "organizer"
  | "ratings"
  | "backlog"
  | null;

function isCabinetTab(active: SiteSidebarActive | undefined): active is CabinetTabKey {
  return active != null && (CABINET_TAB_KEYS as readonly string[]).includes(active);
}

function sectionFromActive(active: SiteSidebarActive | undefined): NavSectionKey | null {
  if (active == null) return null;
  // Страница настроек рисуется каркасом кабинета, но относится к аккаунту.
  if (active === "settings") return "account";
  if (isCabinetTab(active)) return "me";
  if (active === "results" || active === "last-results" || active === "unified-protocol") return "results";
  if (active === "backlog") return "project";
  return active;
}

export function currentPathname(): string {
  return typeof window !== "undefined" ? window.location.pathname : "";
}

function placeFromPath(pathname: string, prefix: "/locations/" | "/organizer/"): NavPlace | null {
  if (!pathname.startsWith(prefix)) return null;
  const raw = pathname.slice(prefix.length).split("/")[0];
  if (!raw) return null;
  let slug = raw;
  try {
    slug = decodeURIComponent(raw);
  } catch {
    // битый адрес — оставляем как есть
  }
  // Имени до ответа API нет; подсказка из sessionStorage спасает от мигания,
  // а без неё лучше показать slug, чем пустой заголовок.
  return locationHintFor(slug) ?? { slug, name: slug };
}

export type NavStateInput = {
  active?: SiteSidebarActive;
  user: User | null | undefined;
  location?: NavPlace | null;
};

export type NavState = {
  pathname: string;
  sections: NavSection[];
  current: NavSection | null;
  /** Локация открытого кабинета организатора — для переключателя. */
  organizerPlace: NavPlace | null;
};

export function resolveNavState({ active, user, location }: NavStateInput): NavState {
  const pathname = currentPathname();
  let key = sectionFromActive(active) ?? sectionKeyFromPath(pathname);
  // Свой публичный адрес /users/{хендл} — это кабинет, чужой — ничей раздел.
  if (key === null && user) {
    const match = pathname.match(/^\/users\/([^/]+)/);
    if (match && isOwnHandle(user, decodeURIComponent(match[1]))) {
      key = "me";
    }
  }
  const inOrganizer = key === "organizer";
  const place = location ?? null;
  const organizerPlace = inOrganizer ? (place ?? placeFromPath(pathname, "/organizer/")) : null;
  const sections = buildSiteNav({
    user,
    location: key === "locations" ? (place ?? placeFromPath(pathname, "/locations/")) : null,
    organizerLocation: organizerPlace,
    inOrganizer,
  });
  return {
    pathname,
    sections,
    current: sections.find((section) => section.key === key) ?? null,
    organizerPlace,
  };
}
