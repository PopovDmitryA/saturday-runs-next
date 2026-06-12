import { useCallback, useEffect, useMemo, useState } from "react";
import { getFiveVerstRunCountRating, type RunCountRatingResponse } from "../../lib/api";
import { InsightsShell } from "./InsightsShell";

function formatDate(value: string | null): string {
  if (!value) {
    return "—";
  }
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return value;
  }
  return date.toLocaleDateString("ru-RU");
}

function formatDateTime(value: string | null): string {
  if (!value) {
    return "—";
  }
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return value;
  }
  return date.toLocaleString("ru-RU");
}

export function RunCountRatingPage() {
  const [data, setData] = useState<RunCountRatingResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [query, setQuery] = useState("");

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const response = await getFiveVerstRunCountRating();
      setData(response);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Не удалось загрузить рейтинг");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const filteredRows = useMemo(() => {
    if (!data) {
      return [];
    }
    const normalized = query.trim().toLowerCase();
    if (!normalized) {
      return data.rows;
    }
    return data.rows.filter((row) => {
      const haystack = [row.runner_name, row.latest_location ?? "", String(row.run_count), String(row.rank)]
        .join(" ")
        .toLowerCase();
      return haystack.includes(normalized);
    });
  }, [data, query]);

  return (
    <InsightsShell activePath="/insights" title="Рейтинг количества пробежек">
      <div className="insights-page insights-detail rating-page">
        <nav className="insights-breadcrumb">
          <a href="/insights">Справочник</a>
          <span aria-hidden> / </span>
          <span>Рейтинг количества пробежек</span>
        </nav>

        <header className="insights-header">
          <p className="about-eyebrow">5 вёрст · рейтинг</p>
          <h1 className="about-hero-title">Рейтинг количества пробежек</h1>
          <p className="about-lead">
            ТОП-1000 участников по числу пробежек. Столбец «+1 на последней неделе» показывает, бегал ли
            участник в дату последней пробежки; «Кол-во пропустивших» — сколько выше стоящих в рейтинге
            пропустили тот же старт.
          </p>
        </header>

        <div className="rating-meta-grid">
          <div className="rating-meta-card">
            <span className="rating-meta-label">Дата последней пробежки</span>
            <strong>{formatDate(data?.latest_event_date ?? null)}</strong>
          </div>
          <div className="rating-meta-card">
            <span className="rating-meta-label">Дата обновления данных</span>
            <strong>{formatDateTime(data?.data_updated_at ?? null)}</strong>
          </div>
          <div className="rating-meta-card">
            <span className="rating-meta-label">Источник</span>
            <strong>{data?.data_source === "legacy" ? "Legacy БД" : "Новая БД"}</strong>
          </div>
        </div>

        {loading && !data && (
          <p className="muted">
            Загрузка рейтинга… Первый расчёт по всей базе может занять до минуты, повторные — быстрее.
          </p>
        )}

        {error && (
          <div className="card error">
            <p>{error}</p>
            <button type="button" className="btn secondary" onClick={() => void load()}>
              Повторить
            </button>
          </div>
        )}

        {data && !error && (
          <>
            {data.rows.length === 0 ? (
              <div className="card">
                <p>
                  В базе пока нет данных 5 вёрст для рейтинга. Локально можно указать{" "}
                  <code>LEGACY_DATABASE_URL</code> на read-only копию <code>five_verst_stats</code> или
                  прогнать ETL в новую БД.
                </p>
              </div>
            ) : (
              <>
                <label className="insights-search rating-search">
                  <span className="visually-hidden">Поиск по участнику</span>
                  <input
                    type="search"
                    value={query}
                    onChange={(event) => setQuery(event.target.value)}
                    placeholder="Поиск по ФИО, локации, месту…"
                  />
                </label>

                <div className="table-wrap rating-table-wrap">
                  <table className="data-table data-table-filterable rating-table">
                    <thead>
                      <tr>
                        <th>Место</th>
                        <th>ФИО участника</th>
                        <th>Кол-во пробежек</th>
                        <th>+1 на последней неделе?</th>
                        <th>Где бегал?</th>
                        <th>Кол-во пропустивших</th>
                      </tr>
                    </thead>
                    <tbody>
                      {filteredRows.map((row) => (
                        <tr key={`${row.rank}-${row.runner_name}`}>
                          <td className="rating-cell-rank">{row.rank}</td>
                          <td className="rating-cell-name">{row.runner_name}</td>
                          <td className="rating-cell-count">{row.run_count}</td>
                          <td className={row.ran_on_latest_date ? "rating-cell-yes" : "rating-cell-no"}>
                            {row.ran_on_latest_date ? "Да" : "Нет"}
                          </td>
                          <td>{row.latest_location ?? "—"}</td>
                          <td>{row.skipped_above_count}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>

                <p className="muted rating-footnote">
                  Показано {filteredRows.length} из {data.rows.length} участников рейтинга.
                </p>
              </>
            )}
          </>
        )}
      </div>
    </InsightsShell>
  );
}
