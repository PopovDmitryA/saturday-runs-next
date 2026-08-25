import { createContext, useContext, type ReactNode } from "react";
import {
  getPublicProfileBestResults,
  getPublicProfileCatalogTable,
  getPublicProfileHomeDistanceDetail,
  getPublicProfilePersonalRecords,
  getPublicProfileVisitedDetail,
  getPublicProfileVisitedMap,
  getPublicProfileVolunteerRoleStats,
  getPublicProfileWins,
  getAllPublicProfileRuns,
  getAllPublicProfileVolunteering,
  getBestResults,
  getCatalogLocationsMap,
  getCatalogLocationsTable,
  getHomeDistanceDetail,
  getPersonalRecords,
  getUniqueLocationsDetail,
  getVisitedLocationsMap,
  getVolunteerRoleStats,
  getWins,
  getAllUserRuns,
  getAllUserVolunteering,
  type BestResultItem,
  type CatalogLocationsTableResponse,
  type HomeDistanceDetail,
  type MapLocationsResponse,
  type PersonalRecordItem,
  type RunItem,
  type UniqueLocationsDetailResponse,
  type VolunteerRoleStatItem,
  type WinItem,
  type VolunteeringItem,
} from "./api";

export type AppDataSourceMode = "auth" | "public-profile";

export type AppDataSource = {
  mode: AppDataSourceMode;
  listRuns: (includeTest?: boolean, limit?: number) => Promise<RunItem[]>;
  listVolunteering: (includeTest?: boolean, limit?: number) => Promise<VolunteeringItem[]>;
  getBestResults: (includeTest?: boolean) => Promise<BestResultItem[]>;
  getPersonalRecords: (includeTest?: boolean) => Promise<PersonalRecordItem[]>;
  getWins: (includeTest?: boolean) => Promise<WinItem[]>;
  getVolunteerRoleStats: (includeTest?: boolean) => Promise<VolunteerRoleStatItem[]>;
  getUniqueLocationsDetail: (includeTest?: boolean) => Promise<UniqueLocationsDetailResponse>;
  getVisitedLocationsMap: (includeTest?: boolean) => Promise<MapLocationsResponse>;
  getCatalogLocationsMap: () => Promise<MapLocationsResponse>;
  getCatalogLocationsTable: (includeTest?: boolean) => Promise<CatalogLocationsTableResponse>;
  // Модалка «Дальность от дома» ходит за деталями сама, поэтому в чужом профиле
  // ей нужен свой источник: личный эндпоинт вернул бы данные смотрящего.
  getHomeDistanceDetail: (includeTest?: boolean) => Promise<HomeDistanceDetail>;
};

export const authDataSource: AppDataSource = {
  mode: "auth",
  listRuns: (includeTest) => getAllUserRuns(includeTest),
  listVolunteering: (includeTest) => getAllUserVolunteering(includeTest),
  getBestResults,
  getPersonalRecords,
  getWins,
  getVolunteerRoleStats,
  getUniqueLocationsDetail,
  getVisitedLocationsMap,
  getCatalogLocationsMap,
  getCatalogLocationsTable,
  getHomeDistanceDetail,
};

export function createPublicProfileDataSource(serialId: number): AppDataSource {
  return {
    mode: "public-profile",
    listRuns: (includeTest) => getAllPublicProfileRuns(serialId, includeTest),
    listVolunteering: (includeTest) => getAllPublicProfileVolunteering(serialId, includeTest),
    getBestResults: (includeTest) => getPublicProfileBestResults(serialId, includeTest),
    getPersonalRecords: (includeTest) => getPublicProfilePersonalRecords(serialId, includeTest),
    getWins: (includeTest) => getPublicProfileWins(serialId, includeTest),
    getVolunteerRoleStats: (includeTest) => getPublicProfileVolunteerRoleStats(serialId, includeTest),
    getUniqueLocationsDetail: (includeTest) => getPublicProfileVisitedDetail(serialId, includeTest),
    getVisitedLocationsMap: (includeTest) => getPublicProfileVisitedMap(serialId, includeTest),
    getCatalogLocationsMap,
    getCatalogLocationsTable: (includeTest) => getPublicProfileCatalogTable(serialId, includeTest),
    getHomeDistanceDetail: (includeTest) =>
      getPublicProfileHomeDistanceDetail(serialId, includeTest),
  };
}

const AppDataSourceContext = createContext<AppDataSource>(authDataSource);

export function AppDataSourceProvider({
  source,
  children,
}: {
  source: AppDataSource;
  children: ReactNode;
}) {
  return <AppDataSourceContext.Provider value={source}>{children}</AppDataSourceContext.Provider>;
}

export function useAppDataSource(): AppDataSource {
  return useContext(AppDataSourceContext);
}
