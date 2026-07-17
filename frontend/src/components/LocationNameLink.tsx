type LocationNameLinkProps = {
  name: string;
  slug?: string | null;
};

/** Название локации со ссылкой на её страницу /locations/{slug} (если slug известен). */
export function LocationNameLink({ name, slug }: LocationNameLinkProps) {
  if (!slug) {
    return <>{name}</>;
  }
  return (
    <a
      className="location-page-link"
      href={`/locations/${encodeURIComponent(slug)}`}
      title="Открыть страницу локации"
    >
      {name}
    </a>
  );
}
