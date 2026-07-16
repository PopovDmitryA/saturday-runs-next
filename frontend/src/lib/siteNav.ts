import type { SiteNavItem } from "../components/SiteHeader";

export const APP_NAV_ITEMS: SiteNavItem[] = [
  { href: "/dashboard", label: "Главная" },
  { href: "/runs", label: "Пробежки" },
  { href: "/volunteering", label: "Волонтёрство" },
  { href: "/co-runners", label: "Встречи" },
  { href: "/maps", label: "Карта" },
  // Раздел пока не шарим на всех — только админ (решение Дмитрия 13.07.2026).
  { href: "/locations", label: "Локации", adminOnly: true },
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
  { href: "/about", label: "О проекте", muted: true },
];

export const PUBLIC_NAV_ITEMS: SiteNavItem[] = [
  { href: "/about", label: "О проекте" },
];
