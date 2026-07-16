import { useCurrentUserIsAdmin } from "../lib/currentUserAdmin";

type LocationNameLinkProps = {
  name: string;
  slug?: string | null;
};

/**
 * Название локации со ссылкой на её страницу /locations/{slug} (если slug
 * известен). Раздел «Локации» пока admin-only, поэтому всем остальным —
 * обычный текст, без битой ссылки.
 */
export function LocationNameLink({ name, slug }: LocationNameLinkProps) {
  const isAdmin = useCurrentUserIsAdmin();
  if (!slug || !isAdmin) {
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
