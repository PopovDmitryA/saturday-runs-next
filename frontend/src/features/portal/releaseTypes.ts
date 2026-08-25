/** Публичные релизы (страница «Обновления»): типы и запросы без авторизации. */

export type SiteRelease = {
  id: string;
  version: string;
  title: string;
  body: string;
  released_at: string;
};

export type SiteReleaseList = {
  items: SiteRelease[];
  /** Всего опубликованных релизов — не только на этой странице. */
  total: number;
  page: number;
  page_size: number;
  pages: number;
  latest_version: string | null;
};

export type ReleasesQuery = {
  page?: number;
  /**
   * «Открой страницу с этой версией» — для ссылок-якорей вида /updates#v2.5.0:
   * с появлением страниц такая версия может лежать не на первой из них.
   */
  version?: string | null;
};

export async function fetchReleases(query: ReleasesQuery = {}): Promise<SiteReleaseList> {
  const params = new URLSearchParams();
  if (query.page && query.page > 1) {
    params.set("page", String(query.page));
  }
  if (query.version) {
    params.set("version", query.version);
  }
  const query_string = params.toString();
  const suffix = query_string ? `?${query_string}` : "";
  const response = await fetch(`/api/releases${suffix}`, { credentials: "same-origin" });
  if (!response.ok) {
    throw new Error(`Не удалось загрузить обновления (HTTP ${response.status})`);
  }
  return (await response.json()) as SiteReleaseList;
}

/** Номер последнего опубликованного релиза — для футера; null, пока релизов нет. */
export async function fetchLatestReleaseVersion(): Promise<string | null> {
  const response = await fetch("/api/releases/latest", { credentials: "same-origin" });
  if (!response.ok) {
    return null;
  }
  const payload = (await response.json()) as { version: string | null };
  return payload.version;
}
