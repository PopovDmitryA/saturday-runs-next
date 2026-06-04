import { AppShell } from "../../components/AppShell";
import { RequireAuth } from "../../components/RequireAuth";
import { AppDataSourceProvider, demoDataSource, useAppDataSource } from "../../lib/appDataSource";
import { DemoShell } from "../demo/DemoShell";
import { UserMapPanel } from "./UserMapPanel";

function MapsContent() {
  const { getVisitedLocationsMap, getCatalogLocationsMap, getCatalogLocationsTable, mode } = useAppDataSource();
  const isDemo = mode === "demo";

  const mapPanel = (
    <UserMapPanel
      loadVisitedMap={() => getVisitedLocationsMap(false)}
      loadCatalogMap={getCatalogLocationsMap}
      loadCatalogTable={() => getCatalogLocationsTable(false)}
    />
  );

  if (isDemo) {
    return <DemoShell title="Карта">{mapPanel}</DemoShell>;
  }

  return <AppShell title="Карта">{mapPanel}</AppShell>;
}

export function MapsPage() {
  return <RequireAuth>{() => <MapsContent />}</RequireAuth>;
}

export function DemoMapsPage() {
  return (
    <AppDataSourceProvider source={demoDataSource}>
      <MapsContent />
    </AppDataSourceProvider>
  );
}
