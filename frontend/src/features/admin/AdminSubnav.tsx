type AdminSubnavProps = {
  activePath: string;
};

const LINKS = [
  { href: "/admin/users", label: "Пользователи" },
  { href: "/admin/queue", label: "Очередь" },
  { href: "/admin/sync-runs", label: "Автообновление" },
  { href: "/admin/stats", label: "Статистика" },
  { href: "/admin/page-analytics", label: "Популярность" },
  { href: "/admin/ratings", label: "Рейтинг" },
  { href: "/admin/event-report", label: "Отчёт по событию" },
  { href: "/admin/records-digest", label: "Рекорды локаций" },
  { href: "/admin/location-contacts", label: "Контакты локаций" },
  { href: "/admin/blog", label: "Блог" },
  { href: "/admin/abuse", label: "Блокировки" },
  { href: "/admin/profile-slugs", label: "Резерв ссылок" },
] as const;

export function AdminSubnav({ activePath }: AdminSubnavProps) {
  return (
    <nav className="admin-subnav" aria-label="Разделы админки">
      {LINKS.map((link) => (
        <a
          key={link.href}
          href={link.href}
          className={activePath === link.href ? "admin-subnav-link active" : "admin-subnav-link"}
        >
          {link.label}
        </a>
      ))}
    </nav>
  );
}
