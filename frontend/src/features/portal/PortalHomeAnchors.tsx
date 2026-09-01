const ANCHORS: readonly { id: string; label: string }[] = [
  { id: "week", label: "Последняя суббота" },
  { id: "systems", label: "Системы" },
  { id: "geo", label: "География" },
  { id: "fastest", label: "Рекорды" },
  { id: "blog", label: "Блог" },
];

/**
 * Оглавление главной: страница длинная, и без него единственный способ
 * добраться до географии или блога — прокрутить полтора десятка секций.
 * Порядок блоков в A/B-вариантах разный, якорям это безразлично.
 */
export function PortalHomeAnchors({ hasBlog }: { hasBlog: boolean }) {
  const anchors = ANCHORS.filter((anchor) => anchor.id !== "blog" || hasBlog);

  const scrollTo = (event: React.MouseEvent<HTMLAnchorElement>, id: string) => {
    const target = document.getElementById(id);
    if (!target) {
      return;
    }
    event.preventDefault();
    target.scrollIntoView({ behavior: "smooth", block: "start" });
    // Адрес меняем без прыжка, чтобы ссылку на секцию можно было скопировать.
    window.history.replaceState(null, "", `#${id}`);
  };

  return (
    <nav className="portal-anchors" aria-label="Разделы страницы">
      {anchors.map((anchor) => (
        <a
          key={anchor.id}
          className="portal-anchor"
          href={`#${anchor.id}`}
          onClick={(event) => scrollTo(event, anchor.id)}
        >
          {anchor.label}
        </a>
      ))}
    </nav>
  );
}
