import { useEffect, useState } from "react";
import { DetailModal } from "./DetailModal";
import { getHomeDistanceDetail, type HomeDistanceDetail, type HomeDistanceLocation } from "../lib/api";
import { formatKm } from "../lib/format";

const HINT =
  "Расстояние по прямой от домашней локации до площадки. Каждая площадка даёт свои " +
  "километры один раз, сколько бы раз вы туда ни ездили. Домашняя локация меняется " +
  "в настройках.";

function placeSubtitle(row: HomeDistanceLocation): string {
  return [row.city, row.region].filter(Boolean).join(", ");
}

function LocationCell({ row }: { row: HomeDistanceLocation }) {
  const subtitle = placeSubtitle(row);
  return (
    <span className="home-distance-place">
      {row.location_slug ? (
        <a href={`/locations/${row.location_slug}`}>{row.name}</a>
      ) : (
        <span>{row.name}</span>
      )}
      {subtitle && <span className="muted home-distance-place-sub">{subtitle}</span>}
    </span>
  );
}

function DistanceCell({ row }: { row: HomeDistanceLocation }) {
  if (row.is_home) {
    return <span className="home-distance-home-badge">дом</span>;
  }
  if (row.distance_km == null) {
    // Закрытые зарубежные площадки, которых нет в мировом каталоге parkrun:
    // координат нет, поэтому и в зачёт километров они не идут.
    return <span className="muted">нет координат</span>;
  }
  return <span className="num">{formatKm(row.distance_km)}</span>;
}

export function HomeDistanceModal({ open, onClose }: { open: boolean; onClose: () => void }) {
  const [data, setData] = useState<HomeDistanceDetail | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!open) {
      return;
    }
    setData(null);
    setError(null);
    let cancelled = false;
    getHomeDistanceDetail()
      .then((payload) => {
        if (!cancelled) {
          setData(payload);
        }
      })
      .catch((err: unknown) => {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : "Не удалось загрузить дальность");
        }
      });
    return () => {
      cancelled = true;
    };
  }, [open]);

  return (
    <DetailModal open={open} title="Дальность от дома" onClose={onClose}>
      {error && <p className="error-text">{error}</p>}
      {!error && !data && <p className="muted">Загрузка…</p>}
      {!error && data && (
        <>
          <p className="muted personal-records-hint">{HINT}</p>
          {data.home && (
            <p className="home-distance-summary">
              Дом — <b>{data.home.name}</b>. В зачёте {formatKm(data.total_distance_km)} по{" "}
              {data.counted_count} площадкам
              {data.unknown_count > 0 && ` (ещё ${data.unknown_count} без координат)`}.
            </p>
          )}

          <h3 className="home-distance-table-title">Где вы были</h3>
          <div className="unique-locations-table-wrap">
            <table className="data-table unique-locations-table home-distance-table">
              <thead>
                <tr>
                  <th>Локация</th>
                  <th className="col-num">В зачёте</th>
                  <th className="col-num">Пробежек</th>
                </tr>
              </thead>
              <tbody>
                {data.visited.map((row) => (
                  <tr key={row.catalog_identity_key} className={row.is_home ? "home-distance-row-home" : undefined}>
                    <td>
                      <LocationCell row={row} />
                    </td>
                    <td className="col-num">
                      <DistanceCell row={row} />
                    </td>
                    <td className="col-num num">{row.run_count}</td>
                  </tr>
                ))}
                {data.visited.length === 0 && (
                  <tr>
                    <td colSpan={3} className="muted">
                      Пока нет пробежек.
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>

          <h3 className="home-distance-table-title">Где вы ещё не были</h3>
          <p className="muted home-distance-table-note">
            Действующие площадки, до которых вы пока не доехали, — от ближней к дальней.
          </p>
          <div className="unique-locations-table-wrap">
            <table className="data-table unique-locations-table home-distance-table home-distance-table-unvisited">
              <thead>
                <tr>
                  <th>Локация</th>
                  <th className="col-num">От дома</th>
                </tr>
              </thead>
              <tbody>
                {data.unvisited.map((row) => (
                  <tr key={row.catalog_identity_key}>
                    <td>
                      <LocationCell row={row} />
                    </td>
                    <td className="col-num">
                      <DistanceCell row={row} />
                    </td>
                  </tr>
                ))}
                {data.unvisited.length === 0 && (
                  <tr>
                    <td colSpan={2} className="muted">
                      Вы побывали на всех действующих площадках.
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
        </>
      )}
    </DetailModal>
  );
}
