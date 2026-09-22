/**
 * Чипы подразделов под шапкой — телефонная замена колонки подразделов.
 *
 * До 23.09.2026 подразделы на телефоне прятались в шторку «Ещё» и были видны
 * только изнутри раздела: «Постоянный состав», «Единый протокол» и «Погоду»
 * почти не открывали (погоду — 9 раз за месяц). Теперь всё, что есть у
 * раздела, лежит рядом с заголовком страницы, а активный чип подтянут в
 * видимую часть ряда.
 *
 * На компьютере чипов нет: там эту роль играет колонка SiteSidebar.
 */
import { useLayoutEffect, useRef } from "react";
import type { User } from "../../../lib/api";
import { resolveNavState, type NavStateInput } from "./navState";
import { OrganizerSwitcher } from "./OrganizerSwitcher";
import { findCurrent, isLinkCurrent, type NavLink } from "./siteNav";

function ChipRow({ links, pathname, activeKey }: { links: NavLink[]; pathname: string; activeKey?: string }) {
  const rowRef = useRef<HTMLDivElement>(null);
  // Прокручиваем сам ряд, а не страницу: scrollIntoView дёргал бы и
  // вертикальную прокрутку, если страницу открыли не с самого верха.
  useLayoutEffect(() => {
    const row = rowRef.current;
    const active = row?.querySelector<HTMLElement>(".site-chip.active");
    if (row && active) {
      row.scrollLeft = active.offsetLeft - (row.clientWidth - active.offsetWidth) / 2;
    }
  }, [pathname, activeKey]);
  return (
    <div className="site-chips-row" ref={rowRef}>
      {links.map((link) => {
        const isCurrent = activeKey ? link.key === activeKey : isLinkCurrent(link, pathname);
        return (
          <a
            key={link.key}
            href={link.href}
            className={`site-chip${isCurrent ? " active" : ""}`}
            aria-current={isCurrent ? "page" : undefined}
          >
            {link.chipLabel ?? link.label}
          </a>
        );
      })}
    </div>
  );
}

export function SectionChips(props: Omit<NavStateInput, "user"> & { user: User | null | undefined }) {
  const { pathname, current, organizerPlace } = resolveNavState(props);
  if (!current) return null;

  // Рейтинги — два ряда: группа и таблица внутри группы. Иначе 14 чипов в
  // одном ряду снова прятали бы нужный рейтинг за краем экрана.
  if (current.key === "ratings") {
    const [hubGroup, ...groups] = current.groups;
    const found = findCurrent(current, pathname);
    const activeGroup = found && found.group !== hubGroup ? found.group : null;
    const groupLinks: NavLink[] = [
      ...hubGroup.items,
      ...groups.map((group) => ({ key: group.key, label: group.title ?? group.key, href: group.items[0].href })),
    ];
    return (
      <nav className="site-chips" aria-label="Подразделы: рейтинги">
        <ChipRow links={groupLinks} pathname={pathname} activeKey={activeGroup?.key ?? (found ? "hub" : undefined)} />
        {activeGroup && <ChipRow links={activeGroup.items} pathname={pathname} />}
      </nav>
    );
  }

  // В кабинете организатора первым идёт переключатель локации, дальше все
  // инструменты подряд; на странице «Мои локации» чипов нет — там и так список.
  if (current.key === "organizer") {
    if (!organizerPlace) return null;
    const tools = current.groups.flatMap((group) => group.items);
    return (
      <nav className="site-chips" aria-label="Инструменты организатора">
        <div className="site-chips-lead">
          <OrganizerSwitcher user={props.user} place={organizerPlace} variant="chip" />
        </div>
        <ChipRow links={tools} pathname={pathname} />
      </nav>
    );
  }

  // Остальные разделы — один ряд из подразделов открытой сущности (локации)
  // или раздела целиком. Каталог локаций своих подразделов не имеет.
  const contextGroups = current.groups.filter((group) => group.context);
  const links = (contextGroups.length > 0 ? contextGroups : current.groups).flatMap((group) => group.items);
  if (links.length <= 1) return null;
  return (
    <nav className="site-chips" aria-label={`Подразделы: ${current.label}`}>
      <ChipRow links={links} pathname={pathname} />
    </nav>
  );
}
