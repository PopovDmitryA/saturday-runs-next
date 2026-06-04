import { createContext, useContext, type ReactNode } from "react";
import {
  demoGetBestResults,
  demoGetCatalogLocationsMap,
  demoGetCatalogLocationsTable,
  demoGetPersonalRecords,
  demoGetUniqueLocationsDetail,
  demoGetVisitedLocationsMap,
  demoGetVolunteerRoleStats,
  demoListRuns,
  demoListVolunteering,
  getBestResults,
  getCatalogLocationsMap,
  getCatalogLocationsTable,
  getPersonalRecords,
  getUniqueLocationsDetail,
  getVisitedLocationsMap,
  getVolunteerRoleStats,
  listRuns,
  listVolunteering,
  type BestResultItem,
  type CatalogLocationsTableResponse,
  type MapLocationsResponse,
  type PersonalRecordItem,
  type RunItem,
  type UniqueLocationsDetailResponse,
  type VolunteerRoleStatItem,
  type VolunteeringItem,
} from "./api";

export type AppDataSource = {
  mode: "auth" | "demo";
  listRuns: (includeTest?: boolean, limit?: number) => Promise<RunItem[]>;
  listVolunteering: (includeTest?: boolean, limit?: number) => Promise<VolunteeringItem[]>;
  getBestResults: (includeTest?: boolean) => Promise<BestResultItem[]>;
  getPersonalRecords: (includeTest?: boolean) => Promise<PersonalRecordItem[]>;
  getVolunteerRoleStats: (includeTest?: boolean) => Promise<VolunteerRoleStatItem[]>;
  getUniqueLocationsDetail: (includeTest?: boolean) => Promise<UniqueLocationsDetailResponse>;
  getVisitedLocationsMap: (includeTest?: boolean) => Promise<MapLocationsResponse>;
  getCatalogLocationsMap: () => Promise<MapLocationsResponse>;
  getCatalogLocationsTable: (includeTest?: boolean) => Promise<CatalogLocationsTableResponse>;
};

export const authDataSource: AppDataSource = {
  mode: "auth",
  listRuns,
  listVolunteering,
  getBestResults,
  getPersonalRecords,
  getVolunteerRoleStats,
  getUniqueLocationsDetail,
  getVisitedLocationsMap,
  getCatalogLocationsMap,
  getCatalogLocationsTable,
};

export const demoDataSource: AppDataSource = {
  mode: "demo",
  listRuns: () => demoListRuns(),
  listVolunteering: () => demoListVolunteering(),
  getBestResults: () => demoGetBestResults(),
  getPersonalRecords: () => demoGetPersonalRecords(),
  getVolunteerRoleStats: () => demoGetVolunteerRoleStats(),
  getUniqueLocationsDetail: () => demoGetUniqueLocationsDetail(),
  getVisitedLocationsMap: () => demoGetVisitedLocationsMap(),
  getCatalogLocationsMap: () => demoGetCatalogLocationsMap(),
  getCatalogLocationsTable: () => demoGetCatalogLocationsTable(),
};

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
