/**
 * Крошки кабинета: всегда дают вернуться и в каталог, и на главную страницу
 * самой локации, и в хаб её инструментов. Без ссылки на /locations/{slug}
 * из кабинета не было выхода на публичную карточку площадки.
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
      <a href="/locations">← Все локации</a> / <a href={`/locations/${slug}`}>{name}</a> /{" "}
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
