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
  total: number;
  latest_version: string | null;
};

export async function fetchReleases(): Promise<SiteReleaseList> {
  const response = await fetch("/api/releases", { credentials: "same-origin" });
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
