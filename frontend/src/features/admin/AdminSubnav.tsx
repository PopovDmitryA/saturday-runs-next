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
  { href: "/admin/releases", label: "Релизы" },
  { href: "/admin/backlog", label: "Бэклог" },
  { href: "/admin/abuse", label: "Блокировки" },
  { href: "/admin/profile-slugs", label: "Резерв ссылок" },
] as const;

// Внешние панели вебмастеров — не разделы сайта, поэтому отдельная группа со
// своим стилем (пунктирная рамка + иконка внешней ссылки) и открытием в новой
// вкладке: уходя туда, не хочется терять админку в этой же вкладке.
// Ссылки на общий вход панели, а не на deep-link конкретного сайта — формат
// URL сайта в обеих системах негарантированно стабилен (порт/протокол в
// пути у Яндекса, sc-domain-префикс у Google), а с одним подключённым сайтом
// обе панели сами приводят на его дашборд после входа.
const EXTERNAL_LINKS = [
  { href: "https://webmaster.yandex.ru/", label: "Яндекс.Вебмастер" },
  { href: "https://search.google.com/search-console", label: "Google Search Console" },
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
      {EXTERNAL_LINKS.map((link) => (
        <a
          key={link.href}
          href={link.href}
          target="_blank"
          rel="noreferrer"
          className="admin-subnav-link admin-subnav-link-external"
        >
          {link.label} ↗
        </a>
      ))}
    </nav>
  );
}
