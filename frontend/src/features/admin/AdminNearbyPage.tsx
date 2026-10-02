/**
 * «Где ищут старт» — геопозиции, присланные боту (02.10.2026).
 *
 * Человек присылает @weekend_runs_bot свою точку, бот отвечает ближайшими
 * локациями. Главный список здесь — белые пятна: места, где субботний старт
 * ищут, а ближайшая локация дальше 30 км. Это спрос на новые локации, и
 * больше его нигде не видно.
 *
 * Журнал анонимный: ни Telegram, ни пользователя; точка огрублена до клетки
 * ~5 км ещё на сервере. Подпись места («Минск, Беларусь») ставит воркер
 * обратным геокодингом — у свежей строки её может ещё не быть.
 */
import { useEffect, useState } from "react";
import { RequireAdmin } from "../../components/RequireAdmin";
import { getAdminNearbyLog, type AdminNearbyLogResponse, type NearbyWhiteSpot } from "../../lib/api";
import { formatDate, formatDateTime } from "../../lib/format";
import { TableWrap } from "../../components/tableUx/TableWrap";
import { AdminShell } from "./AdminShell";
import { AdminSubnav } from "./AdminSubnav";

const PERIODS = [
  { label: "7 дней", days: 7 },
  { label: "30 дней", days: 30 },
  { label: "90 дней", days: 90 },
  { label: "Год", days: 365 },
] as const;

function percent(part: number, total: number): string {
  if (total === 0) return "—";
  return `${Math.round((part / total) * 100)}%`;
}

function formatKm(value: number | null): string {
  if (value === null) return "—";
  return value < 10 ? `${value.toFixed(1).replace(".", ",")} км` : `${Math.round(value)} км`;
}

function mapUrl(spot: NearbyWhiteSpot): string {
  return `https://yandex.ru/maps/?pt=${spot.cell_longitude},${spot.cell_latitude}&z=9&l=map`;
}

function LocationLink({ name, slug }: { name: string | null; slug: string | null }) {
  if (!name) return <span className="muted">—</span>;
  return slug ? <a href={`/locations/${encodeURIComponent(slug)}`}>{name}</a> : <>{name}</>;
}

function AdminNearbyContent() {
  const [days, setDays] = useState<number>(30);
  const [data, setData] = useState<AdminNearbyLogResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    getAdminNearbyLog(days)
      .then((payload) => {
        if (!cancelled) setData(payload);
      })
      .catch((err: unknown) => {
        if (!cancelled) setError(err instanceof Error ? err.message : "Не удалось загрузить журнал");
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [days]);

  return (
    <AdminShell title="Где ищут старт">
      <AdminSubnav activePath="/admin/nearby" />

      <div className="admin-stats-toolbar">
        <div className="admin-stats-periods" role="tablist" aria-label="Период">
          {PERIODS.map((item) => (
            <button
              key={item.days}
              type="button"
              className={days === item.days ? "btn primary" : "btn secondary"}
              onClick={() => setDays(item.days)}
            >
              {item.label}
            </button>
          ))}
        </div>
      </div>

      {loading && <p className="muted">Загрузка…</p>}
      {error && (
        <div className="card error">
          <p>{error}</p>
        </div>
      )}

      {data && !loading && (
        <>
          <div className="stats-grid">
            <div className="stat-card">
              <span className="stat-value">{data.total}</span>
              <span className="stat-label">геопозиций прислали боту</span>
            </div>
            <div className="stat-card">
              <span className="stat-value">{data.nothing_near_total}</span>
              <span className="stat-label">
                рядом стартов нет (ближе {data.radius_km} км) · {percent(data.nothing_near_total, data.total)}
              </span>
            </div>
            <div className="stat-card">
              <span className="stat-value">{data.linked_total}</span>
              <span className="stat-label">Telegram привязан к профилю · {percent(data.linked_total, data.total)}</span>
            </div>
            <div className="stat-card">
              <span className="stat-value">{data.inline_total}</span>
              <span className="stat-label">из чужих чатов (inline) · {percent(data.inline_total, data.total)}</span>
            </div>
          </div>

          <section className="card">
            <h2 className="section-title">Белые пятна</h2>
            <p className="muted">
              Места, где ищут субботний старт, а ближайшая локация дальше {data.white_spot_km} км. Точка огрублена до
              клетки ~5 км; несколько запросов из одной клетки — скорее всего, один человек или одна компания. Подпись
              места появляется через минуту после первого запроса.
            </p>
            {data.white_spots.length === 0 ? (
              <p className="muted">За период таких запросов нет.</p>
            ) : (
              <TableWrap>
                <table className="data-table">
                  <thead>
                    <tr>
                      <th>Место</th>
                      <th>Запросов</th>
                      <th>Ближайшая локация</th>
                      <th>Последний</th>
                    </tr>
                  </thead>
                  <tbody>
                    {data.white_spots.map((spot) => (
                      <tr key={`${spot.cell_latitude}:${spot.cell_longitude}`}>
                        <td>
                          <a href={mapUrl(spot)} target="_blank" rel="noreferrer">
                            {spot.place_label ?? `${spot.cell_latitude}, ${spot.cell_longitude}`}
                          </a>
                        </td>
                        <td>{spot.count}</td>
                        <td>
                          <LocationLink name={spot.nearest_name} slug={spot.nearest_slug} />
                          <span className="muted"> · {formatKm(spot.nearest_distance_km)}</span>
                        </td>
                        <td>{formatDateTime(spot.last_at)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </TableWrap>
            )}
          </section>

          <section className="card">
            <h2 className="section-title">Куда ведёт геопозиция</h2>
            <p className="muted">
              Ближайшая локация в ответах, где рядом что-то нашлось. Высокое место у локации, где мало бегунов, —
              повод для поста: к ней присматриваются.
            </p>
            {data.top_nearest.length === 0 ? (
              <p className="muted">За период запросов не было.</p>
            ) : (
              <TableWrap>
                <table className="data-table">
                  <thead>
                    <tr>
                      <th>Локация</th>
                      <th>Запросов</th>
                    </tr>
                  </thead>
                  <tbody>
                    {data.top_nearest.map((row) => (
                      <tr key={row.nearest_slug ?? row.nearest_name ?? "—"}>
                        <td>
                          <LocationLink name={row.nearest_name} slug={row.nearest_slug} />
                        </td>
                        <td>{row.count}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </TableWrap>
            )}
          </section>

          {data.daily.length > 0 && (
            <section className="card">
              <h2 className="section-title">По дням</h2>
              <TableWrap>
                <table className="data-table">
                  <thead>
                    <tr>
                      <th>День</th>
                      <th>Запросов</th>
                      <th>Рядом нет</th>
                    </tr>
                  </thead>
                  <tbody>
                    {data.daily.map((row) => (
                      <tr key={row.day}>
                        <td>{formatDate(row.day)}</td>
                        <td>{row.count}</td>
                        <td>{row.nothing_near || "—"}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </TableWrap>
            </section>
          )}
        </>
      )}
    </AdminShell>
  );
}

export function AdminNearbyPage() {
  return <RequireAdmin>{() => <AdminNearbyContent />}</RequireAdmin>;
}
