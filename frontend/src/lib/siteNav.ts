import type { SiteNavItem } from "../components/SiteHeader";

export const APP_NAV_ITEMS: SiteNavItem[] = [
  { href: "/dashboard", label: "Главная" },
  { href: "/runs", label: "Пробежки" },
  { href: "/volunteering", label: "Волонтёрство" },
  { href: "/achievements", label: "Достижения" },
  { href: "/co-runners", label: "Встречи" },
  { href: "/ratings", label: "Рейтинги" },
  { href: "/maps", label: "Карта" },
  { href: "/history", label: "Моя история" },
  { href: "/locations", label: "Локации" },
  { href: "/share", label: "Поделиться" },
  { href: "/admin/users", label: "Админка", adminOnly: true, adminStyle: true },
  { href: "/settings", label: "Настройки" },
  { href: "/about", label: "О проекте" },
];

export const DEMO_NAV_ITEMS: SiteNavItem[] = [
  { href: "/demo", label: "Главная" },
  { href: "/demo/runs", label: "Пробежки" },
  { href: "/demo/volunteering", label: "Волонтёрство" },
  { href: "/demo/co-runners", label: "Встречи" },
  { href: "/demo/maps", label: "Карта" },
  { href: "/demo/history", label: "Моя история" },
  { href: "/about", label: "О проекте", muted: true },
];

export const PUBLIC_NAV_ITEMS: SiteNavItem[] = [
  { href: "/about", label: "О проекте" },
];
