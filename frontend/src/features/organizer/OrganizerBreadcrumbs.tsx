/**
 * Крошки кабинета: всегда дают вернуться и в каталог, и на главную страницу
 * самой локации, и в хаб её инструментов. Без ссылки на /locations/{slug}
 * из кабинета не было выхода на публичную страницу локации.
 *
 * «Мои локации» ведёт на /organizer — список локаций, где человек организатор
 * (сценарий Дмитрия 24.08.2026: организатор нескольких локаций должен уметь
 * перейти из кабинета одной к списку остальных; при одной локации /organizer
 * сам вернёт в её кабинет).
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
  const name = locationName ?? "Локация";
  return (
    <p className="muted loc-header-breadcrumb">
      <a href="/organizer">← Мои локации</a> / <a href={`/locations/${slug}`}>{name}</a> /{" "}
      {tool ? (
        <>
          <a href={`/organizer/${slug}`}>Кабинет организатора</a> / {tool}
        </>
      ) : (
        "Кабинет организатора"
      )}
    </p>
  );
}
