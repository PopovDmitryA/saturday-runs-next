import { PORTAL_CABINET_MAP_HREF } from "../../lib/portalRoutes";
import { SectionSidebarNav, type SectionNavItem } from "../portal/PortalSectionShell";

/**
 * Сайдбар раздела «Локации». На странице конкретной локации добавляются её
 * подразделы (страница + журнал протоколов); «Моя карта» — только залогиненным.
 */
export function LocationsSidebar({ location }: { location?: { slug: string; name: string } }) {
  const items: SectionNavItem[] = [{ href: "/locations", label: "Каталог локаций", exact: true }];
  if (location) {
    items.push({ href: `/locations/${location.slug}`, label: location.name, exact: true });
    items.push({ href: `/locations/${location.slug}/events`, label: "Журнал протоколов" });
  }
  items.push({ href: PORTAL_CABINET_MAP_HREF, label: "Моя карта", authOnly: true });
  return <SectionSidebarNav title="Локации" items={items} />;
}
