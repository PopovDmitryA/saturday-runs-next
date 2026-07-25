import type { SectionNavItem } from "../portal/PortalSectionShell";

/** Пункты сайдбара раздела «Рейтинги» — все лидерборды в один клик. */
export const RATINGS_NAV: SectionNavItem[] = [
  { href: "/ratings", label: "Все рейтинги", exact: true },
  { href: "/ratings/runs", label: "Количество пробежек" },
  { href: "/ratings/wins", label: "Первые места" },
  { href: "/ratings/volunteering", label: "Волонтёрства" },
  { href: "/ratings/locations", label: "Уникальные локации" },
  { href: "/ratings/win-locations", label: "Локации с победой" },
];
