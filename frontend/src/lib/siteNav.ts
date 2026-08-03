import type { SiteNavItem } from "../components/SiteHeader";

export const APP_NAV_ITEMS: SiteNavItem[] = [
  // Домик — обратная навигация из кабинета на публичную главную портала.
  { href: "/", label: "На главную портала", icon: "home" },
  { href: "/dashboard", label: "Личный кабинет" },
  { href: "/runs", label: "Пробежки" },
  { href: "/volunteering", label: "Волонтёрство" },
  { href: "/achievements", label: "Достижения" },
  { href: "/co-runners", label: "Встречи" },
  { href: "/ratings", label: "Рейтинги" },
  { href: "/backlog", label: "Бэклог" },
  { href: "/maps", label: "Карта" },
  { href: "/history", label: "Моя история" },
  { href: "/locations", label: "Локации" },
  { href: "/share", label: "Поделиться" },
  { href: "/admin/users", label: "Админка", adminOnly: true, adminStyle: true },
  { href: "/settings", label: "Настройки" },
  { href: "/about", label: "О проекте" },
];

export const PUBLIC_NAV_ITEMS: SiteNavItem[] = [
  { href: "/about", label: "О проекте" },
];
