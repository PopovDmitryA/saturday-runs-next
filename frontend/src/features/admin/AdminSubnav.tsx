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
  {
    // Постмастер mail.ru: доля спама и жалоб по нашему домену. Домен
    // подтверждён 02.09.2026 файлом в корне (frontend/public), поэтому
    // ведём сразу на его страницу, а не на общий вход.
    href: "https://postmaster.mail.ru/run5k.run/",
    label: "Постмастер mail.ru",
  },
  {
    // Постмастер Google: доля спама, репутация домена и соответствие правилам
    // для отправителей. Домен подтверждён TXT-записью 07.09.2026, поэтому
    // ведём сразу на его дашборд. Панели наполняются только при заметном
    // объёме (у Google это сотни писем в сутки на gmail), у нас столько не
    // набирается — но «Соответствие требованиям» и история копятся.
    // У Яндекса аналога нет: свой постмастер он закрыл в 2020 году и замены не
    // дал, поэтому по яндексовым ящикам единственный сигнал — наша собственная
    // воронка по доменам на странице «Статистика».
    href: "https://postmaster.google.com/dashboards?domain=run5k.run",
    label: "Постмастер Google",
  },
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
