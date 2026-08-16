import type { LastSaturday } from "../lib/api";
import { formatDate } from "../lib/format";
import { PlatformBadge } from "./PlatformBadge";

/**
 * Герой дашборда «Твоя последняя суббота»: свежайший результат — локация,
 * время, темп, место и дельта к прошлому визиту на ту же площадку.
 * Карточка события: тот же формат в будущем станет единицей ленты.
 */
/** Разница во времени: до минуты — в секундах, дальше — «м:сс», иначе
 *  крупная дельта («926 сек») читается как техническая величина. */
function formatDeltaValue(seconds: number): string {
  if (seconds < 60) {
    return `${seconds} сек`;
  }
  const minutes = Math.floor(seconds / 60);
  return `${minutes}:${String(seconds % 60).padStart(2, "0")}`;
}

export function LastSaturdayCard({ data, own = false }: { data: LastSaturday; own?: boolean }) {
  const deltaSec = data.delta_vs_prev_sec;
  const deltaChip =
    deltaSec != null && deltaSec !== 0 ? (
      <span
        className={`last-saturday-delta ${deltaSec < 0 ? "last-saturday-delta-faster" : "last-saturday-delta-slower"}`}
      >
        {deltaSec < 0 ? "↓" : "↑"} {formatDeltaValue(Math.abs(deltaSec))}{" "}
        {deltaSec < 0 ? "быстрее" : "медленнее"}, чем здесь в прошлый раз
      </span>
    ) : deltaSec === 0 ? (
      <span className="last-saturday-delta">секунда в секунду с прошлым визитом сюда</span>
    ) : null;

  return (
    <div className="card last-saturday-card">
      <p className="last-saturday-kicker">
        {own ? "Твоя последняя суббота" : "Последняя суббота"} · {formatDate(data.event_date)}
      </p>
      <div className="last-saturday-main">
        {data.location_slug ? (
          <a className="last-saturday-location" href={`/locations/${data.location_slug}`}>
            {data.location_name}
          </a>
        ) : (
          <span className="last-saturday-location">{data.location_name}</span>
        )}
        <PlatformBadge code={data.platform_code} />
        {data.is_pr && <span className="badge badge-pr">PR</span>}
        {data.is_first_run_at_location && (
          <span className="last-saturday-first">впервые здесь</span>
        )}
      </div>
      <div className="last-saturday-stats">
        {data.finish_time_display && (
          <span className="last-saturday-stat">
            <b>{data.finish_time_display}</b>
          </span>
        )}
        {data.pace_display && (
          <span className="last-saturday-stat">{data.pace_display} /км</span>
        )}
        {data.position != null && (
          <span className="last-saturday-stat">{data.position} место</span>
        )}
      </div>
      {deltaChip}
      {data.notables.length > 0 && (
        <ul className="last-saturday-notables">
          {data.notables.map((note) => (
            <li key={note}>{note}</li>
          ))}
        </ul>
      )}
    </div>
  );
}
