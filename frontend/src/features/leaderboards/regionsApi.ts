// API рейтинга регионов по числу локаций. Свой модуль, как и у рекордов
// локаций: строка здесь — регион или страна, а не участник, и с типами
// лидербордов людей у неё ничего общего нет.

// parkrun в этом рейтинге не участвует (закрыт с 2022, регион у его строк почти
// везде пуст) — фильтр знает только действующие системы.
export type RegionsPlatform = "all" | "five_verst" | "s95" | "runpark";

export const REGIONS_PLATFORM_LABELS: Record<string, string> = {
  all: "Все",
  five_verst: "5 вёрст",
  s95: "С95",
  runpark: "RunPark",
};

export type RegionRatingRow = {
  place: number;
  name: string;
  /** region — регион России, country — зарубежная страна (как на карте). */
  scope: "region" | "country";
  locations: number;
  paused: number;
  cities: number;
  by_platform: Record<string, number>;
};

export type RegionsRatingTotals = {
  regions: number;
  region_locations: number;
  countries: number;
  country_locations: number;
  paused: number;
  unknown_region: number;
};

export type RegionsRatingResponse = {
  platform: RegionsPlatform;
  platforms: string[];
  regions: RegionRatingRow[];
  countries: RegionRatingRow[];
  totals: RegionsRatingTotals;
};

export async function getRegionsRating(
  platform: RegionsPlatform = "all",
): Promise<RegionsRatingResponse> {
  const query = new URLSearchParams();
  if (platform !== "all") {
    query.set("platform", platform);
  }
  const suffix = query.toString();
  const response = await fetch(`/api/location-regions${suffix ? `?${suffix}` : ""}`, {
    credentials: "include",
    headers: { Accept: "application/json" },
  });
  if (!response.ok) {
    throw new Error(`Не удалось загрузить рейтинг (${response.status})`);
  }
  return (await response.json()) as RegionsRatingResponse;
}
