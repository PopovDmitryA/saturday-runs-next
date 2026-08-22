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
  { href: "/admin/location-openings", label: "Открытия локаций" },
  { href: "/admin/blog", label: "Блог" },
  { href: "/admin/releases", label: "Релизы" },
  { href: "/admin/backlog", label: "Бэклог" },
  { href: "/admin/abuse", label: "Блокировки" },
  { href: "/admin/profile-slugs", label: "Резерв ссылок" },
] as const;

// Внешние панели вебмастеров — не разделы сайта, поэтому отдельная группа со
// своим стилем (пунктирная рамка + иконка внешней ссылки) и открытием в новой
// вкладке: уходя туда, не хочется терять админку в этой же вкладке.
// Ведём сразу на дашборд нашего сайта, где это возможно. У Google остаётся
// общий вход: ресурс типа «Домен» адресуется префиксом sc-domain, и прямая
// ссылка ломается при смене типа ресурса.
const EXTERNAL_LINKS = [
  {
    // Формат адреса у Яндекса нестандартный — протокол и порт внутри пути.
    // Это не опечатка, короче он не открывается (просьба Дмитрия 13.08.2026).
    href: "https://webmaster.yandex.ru/site/https:run5k.run:443/dashboard/",
    label: "Яндекс.Вебмастер",
  },
  {
    // Метрика — исключение из правила «общий вход»: счётчик один, а его id
    // в URL стабилен, поэтому ведём сразу на дашборд «Сводка».
    href: "https://metrika.yandex.ru/overview?id=111350728&period=today&group=dekaminute&isMinSamplingEnabled=false&currency=RUB&attr=%7B%22attributionId%22%3A%22LastSign%22%2C%22isCrossDevice%22%3Atrue%7D&isUndefinedEnabled=false",
    label: "Яндекс.Метрика",
  },
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
