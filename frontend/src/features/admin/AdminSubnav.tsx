type AdminSubnavProps = {
  activePath: string;
};

const LINKS = [
  { href: "/admin", label: "Обзор" },
  { href: "/admin/stats", label: "Статистика" },
  { href: "/admin/queue", label: "Очередь" },
  { href: "/admin/parkrun", label: "Parkrun" },
  { href: "/admin/users", label: "Пользователи" },
  { href: "/admin/s95-participants", label: "S95 участники" },
  { href: "/admin/abuse", label: "Блокировки" },
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
