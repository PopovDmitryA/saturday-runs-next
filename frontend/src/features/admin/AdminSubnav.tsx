type AdminSubnavProps = {
  activePath: string;
};

const LINKS = [
  { href: "/admin/users", label: "Пользователи" },
  { href: "/admin/queue", label: "Очередь" },
  { href: "/admin/stats", label: "Статистика" },
  { href: "/admin/ratings", label: "Рейтинг" },
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
