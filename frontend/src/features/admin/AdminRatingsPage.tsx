import { useCallback, useEffect, useState } from "react";
import { AppShell } from "../../components/AppShell";
import { RequireAdmin } from "../../components/RequireAdmin";
import { PlatformBadge } from "../../components/PlatformBadge";
import {
  getAdminLocationRatings,
  getAdminRatings,
  type AdminLocationRatings,
  type AdminRatings,
} from "../../lib/api";
import { formatDate } from "../../lib/format";
import { AdminSubnav } from "./AdminSubnav";

function num(value: number | null): string {
  return value == null ? "—" : value.toFixed(2);
}

function AdminRatingsContent() {
  const [raw, setRaw] = useState<AdminRatings | null>(null);
  const [locations, setLocations] = useState<AdminLocationRatings | null>(null);
  const [excludeLocals, setExcludeLocals] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const loadRaw = useCallback(() => {
    getAdminRatings()
      .then(setRaw)
      .catch((err) => setError(err instanceof Error ? err.message : "Не удалось загрузить оценки"));
  }, []);

  const loadLocations = useCallback((exclude: boolean) => {
    getAdminLocationRatings(exclude)
      .then(setLocations)
      .catch((err) => setError(err instanceof Error ? err.message : "Не удалось загрузить рейтинг"));
  }, []);

  useEffect(() => {
    loadRaw();
  }, [loadRaw]);

  useEffect(() => {
    loadLocations(excludeLocals);
  }, [loadLocations, excludeLocals]);

  return (
    <AppShell title="Рейтинг" activePath="/admin">
      <AdminSubnav activePath="/admin/ratings" />

      {error && <div className="card error"><p>{error}</p></div>}

      <section className="card admin-ratings-section">
        <div className="admin-ratings-head">
          <h2 className="section-title">Рейтинг локаций</h2>
          <label className="checkbox-row">
            <input
              type="checkbox"
              checked={excludeLocals}
              onChange={(e) => setExcludeLocals(e.target.checked)}
            />
            Без местных (исключить домашние локации оценивших)
          </label>
        </div>
        <p className="muted admin-ratings-lead">
          Среднее по критериям (1–5). «Оценивших» — число разных пользователей; на прод рейтинг
          планируется показывать с {locations?.min_voters ?? 10} разными оценившими.
        </p>
        <div className="table-wrap">
          <table className="data-table">
            <thead>
              <tr>
                <th>Локация</th>
                <th>Оценок</th>
                <th>Оценивших</th>
                <th>Общая</th>
                <th>Организация</th>
                <th>Трасса</th>
                <th>Сообщество</th>
              </tr>
            </thead>
            <tbody>
              {locations && locations.locations.length === 0 && (
                <tr>
                  <td colSpan={7} className="muted">Пока нет оценок</td>
                </tr>
              )}
              {locations?.locations.map((loc) => (
                <tr key={loc.location_key}>
                  <td>
                    {loc.location_name}
                    {loc.meets_threshold && (
                      <span className="badge admin-ratings-threshold" title="Достаточно оценивших для показа">
                        порог
                      </span>
                    )}
                  </td>
                  <td>{loc.ratings}</td>
                  <td>{loc.voters}</td>
                  <td className="admin-ratings-avg">{num(loc.avg_overall)}</td>
                  <td>{num(loc.avg_organization)}</td>
                  <td>{num(loc.avg_route)}</td>
                  <td>{num(loc.avg_community)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>

      <section className="card admin-ratings-section">
        <h2 className="section-title">Все оценки (сырьё)</h2>
        {raw && (
          <div className="admin-ratings-stats">
            <div className="admin-ratings-stat">
              <span className="admin-ratings-stat-value">{raw.stats.last_1d}</span>
              <span className="admin-ratings-stat-label">за сутки</span>
            </div>
            <div className="admin-ratings-stat">
              <span className="admin-ratings-stat-value">{raw.stats.last_7d}</span>
              <span className="admin-ratings-stat-label">за 7 дней</span>
            </div>
            <div className="admin-ratings-stat">
              <span className="admin-ratings-stat-value">{raw.stats.last_30d}</span>
              <span className="admin-ratings-stat-label">за 30 дней</span>
            </div>
            <div className="admin-ratings-stat">
              <span className="admin-ratings-stat-value">{raw.stats.total}</span>
              <span className="admin-ratings-stat-label">всего в базе</span>
            </div>
          </div>
        )}
        <p className="muted admin-ratings-lead">
          От новых к старым, {raw?.ratings.length ?? 0} шт. Замороженные — старту больше 3 месяцев.
        </p>
        <div className="table-wrap">
          <table className="data-table">
            <thead>
              <tr>
                <th>Дата</th>
                <th>Пользователь</th>
                <th>Локация</th>
                <th>Система</th>
                <th>Общая</th>
                <th>Орг.</th>
                <th>Трасса</th>
                <th>Сообщ.</th>
                <th>Публично</th>
                <th>Статус</th>
                <th>Комментарий</th>
              </tr>
            </thead>
            <tbody>
              {raw && raw.ratings.length === 0 && (
                <tr>
                  <td colSpan={11} className="muted">Пока нет оценок</td>
                </tr>
              )}
              {raw?.ratings.map((r) => (
                <tr key={r.id}>
                  <td>{formatDate(r.event_date)}</td>
                  <td>
                    {r.user_display}
                    {r.user_serial != null && <span className="muted"> #{r.user_serial}</span>}
                  </td>
                  <td>{r.location_name}</td>
                  <td><PlatformBadge code={r.platform_code} /></td>
                  <td className="admin-ratings-avg">{r.score_overall}</td>
                  <td>{r.score_organization ?? "—"}</td>
                  <td>{r.score_route ?? "—"}</td>
                  <td>{r.score_community ?? "—"}</td>
                  <td>{r.is_public ? "да" : "аноним"}</td>
                  <td>{r.editable ? "можно менять" : "заморожена"}</td>
                  <td className="admin-ratings-comment">
                    {r.comment ? (
                      <span className="admin-ratings-comment-text" title={r.comment}>
                        {r.comment}
                      </span>
                    ) : (
                      "—"
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>
    </AppShell>
  );
}

export function AdminRatingsPage() {
  return <RequireAdmin>{() => <AdminRatingsContent />}</RequireAdmin>;
}
