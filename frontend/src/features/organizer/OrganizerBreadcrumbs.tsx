import { useOptionalUser } from "../../lib/useOptionalUser";
import { useOrganizerLocations } from "../portal/nav/OrganizerSwitcher";
import { ORGANIZER_INDEX_HREF, ORGANIZER_LABEL } from "../portal/nav/siteNav";

/**
 * Крошки кабинета: всегда дают вернуться на главную страницу самой локации и
 * в хаб её инструментов. Без ссылки на /locations/{slug} из кабинета не было
 * выхода на публичную страницу локации.
 *
 * «← Мои локации» ведёт на /organizer — список локаций, где человек
 * организатор (сценарий Дмитрия 24.08.2026: организатор нескольких локаций
 * переходит из кабинета одной к списку остальных). При одной своей локации
 * ссылки нет: список сразу вернул бы в этот же кабинет, и ссылка обещала бы
 * то, чего не показывает (a11y-10). Список локаций — общий на документ, лишнего
 * запроса крошки не делают.
 */
export function OrganizerBreadcrumbs({
  slug,
  locationName,
  tool,
}: {
  slug: string;
  locationName: string | null;
  /** Название открытого инструмента; не задано — мы в хабе кабинета. */
  tool?: string;
}) {
  const user = useOptionalUser();
  const items = useOrganizerLocations(user);
  const showIndex = Boolean(user?.is_admin) || (items !== null && items.length > 1);
  const name = locationName ?? "Локация";
  return (
    <p className="muted loc-header-breadcrumb">
      {showIndex && (
        <>
          <a href={ORGANIZER_INDEX_HREF}>← Мои локации</a> /{" "}
        </>
      )}
      <a href={`/locations/${slug}`}>{name}</a> /{" "}
      {tool ? (
        <>
          <a href={`/organizer/${slug}`}>{ORGANIZER_LABEL}</a> / {tool}
        </>
      ) : (
        ORGANIZER_LABEL
      )}
    </p>
  );
}
