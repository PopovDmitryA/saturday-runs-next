import { useEffect, useMemo, useState } from "react";
import { RequireAuth } from "../../components/RequireAuth";
import {
  ApiError,
  getOrganizerAttendance,
  type OrganizerAttendanceResponse,
} from "../../lib/api";
import { formatInt, platformCodeLabel, pluralizeRu } from "../../lib/format";
import { PORTAL_LOGIN_HREF } from "../../lib/portalRoutes";
import { locationHintFor } from "../../lib/locationHint";
import { TableWrap } from "../../components/tableUx/TableWrap";
import { useFloatingTableHead } from "../../lib/useFloatingTableHead";
import { HeaderHint } from "../../components/tableUx/HeaderHint";
import { ChartColumnTooltip } from "../../components/ChartColumnTooltip";
import { PortalSectionShell } from "../portal/PortalSectionShell";
import { OrganizerBreadcrumbs } from "./OrganizerBreadcrumbs";
import { OrganizerDenied } from "./OrganizerDenied";
import "./organizer.css";

const MONTH_LABELS = ["янв", "фев", "мар", "апр", "май", "июн", "июл", "авг", "сен", "окт", "ноя", "дек"];

function monthLabel(key: string): string {
  const [year, month] = key.split("-");
  return `${MONTH_LABELS[Number(month) - 1]} ${year}`;
}

// Класс окраски столбика по системе — та же палитра, что у коллекционных
// челленджей (cell-plat-*): 5 вёрст зелёный, parkrun фиолетовый и т.д.
function platformClass(code: string): string {
  return `org-hist-plat-${code.replace(/_/g, "-")}`;
}

function OrganizerAttendanceContent({ slug }: { slug: string }) {
  const attachFloatingHead = useFloatingTableHead();
  const [data, setData] = useState<OrganizerAttendanceResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [forbidden, setForbidden] = useState(false);
  const [notFound, setNotFound] = useState(false);

  useEffect(() => {
    let cancelled = false;
    getOrganizerAttendance(slug)
      .then((payload) => {
        if (!cancelled) {
          setData(payload);
        }
      })
      .catch((err) => {
        if (cancelled) {
          return;
        }
        if (err instanceof ApiError && err.status === 403) {
          setForbidden(true);
        } else if (err instanceof ApiError && err.status === 404) {
          setNotFound(true);
        } else {
          setError(err instanceof Error ? err.message : "Не удалось загрузить посещаемость");
        }
      });
    return () => {
      cancelled = true;
    };
  }, [slug]);

  // Вся история локации разом: у мультисистемных точек видна эра parkrun
  // до ребрендинга — в этом и фишка графика (просьба Дмитрия 19.08.2026).
  // Календарные дыры (ковид, перерывы) заполняем пустыми колонками, иначе
  // ось времени схлопывает паузы и график врёт.
  type ChartMonth =
    | { month: string; empty: true }
    | {
        month: string;
        empty: false;
        events: number;
        avg_finishers: number;
        max_finishers: number;
        platform_code: string;
      };
  const chartMonths = useMemo<ChartMonth[]>(() => {
    const source = data?.months ?? [];
    if (source.length === 0) {
      return [];
    }
    const byKey = new Map(source.map((item) => [item.month, item]));
    const result: ChartMonth[] = [];
    let [year, month] = source[0].month.split("-").map(Number);
    const last = source[source.length - 1].month;
    for (;;) {
      const key = `${year}-${String(month).padStart(2, "0")}`;
      const item = byKey.get(key);
      result.push(item ? { ...item, empty: false } : { month: key, empty: true });
      if (key === last) {
        break;
      }
      month += 1;
      if (month > 12) {
        month = 1;
        year += 1;
      }
    }
    return result;
  }, [data]);
  const chartMax = useMemo(
    () =>
      Math.max(1, ...chartMonths.map((item) => (item.empty ? 0 : item.avg_finishers))),
    [chartMonths],
  );
  const platformsInChart = useMemo(() => {
    const seen: string[] = [];
    for (const item of chartMonths) {
      if (!item.empty && !seen.includes(item.platform_code)) {
        seen.push(item.platform_code);
      }
    }
    return seen;
  }, [chartMonths]);
  const recentEvents = useMemo(() => {
    const ordered = [...(data?.events ?? [])];
    // Дельта — к предыдущему СОСТОЯВШЕМУСЯ старту (нулевые = отмены).
    let prevFinishers: number | null = null;
    const withDelta = ordered.map((event) => {
      const delta =
        event.finishers > 0 && prevFinishers != null ? event.finishers - prevFinishers : null;
      if (event.finishers > 0) {
        prevFinishers = event.finishers;
      }
      return { ...event, delta };
    });
    return withDelta.reverse().slice(0, 20);
  }, [data]);
  const recordFinishers = data?.record_finishers ?? null;

  const name = data?.location.name ?? locationHintFor(slug)?.name ?? null;
  const sidebar = {
    active: "organizer" as const,
    location: name ? { slug, name } : locationHintFor(slug),
  };

  if (forbidden || notFound) {
    return (
      <PortalSectionShell sidebar={sidebar}>
        <OrganizerDenied slug={slug} notFound={notFound} />
      </PortalSectionShell>
    );
  }

  return (
    <PortalSectionShell sidebar={sidebar}>
      <header className="loc-header">
        <OrganizerBreadcrumbs slug={slug} locationName={name} tool="Посещаемость" />
        <div className="loc-header-title">
          <h1>{name ?? "Локация"} — посещаемость</h1>
        </div>
        <p className="muted">
          Растём или падаем: среднее число финишёров по месяцам за всю историю локации, сравнение
          год к году и рекорд.
        </p>
      </header>

      {error && (
        <div className="card error">
          <p>{error}</p>
        </div>
      )}

      {!error && data === null && <p className="muted">Загрузка…</p>}

      {!error && data !== null && data.events_total === 0 && (
        <div className="card">
          <p className="muted">У локации пока нет состоявшихся стартов с протоколами.</p>
        </div>
      )}

      {!error && data !== null && data.events_total > 0 && (
        <>
          <section className="card org-toolbar-card">
            <div className="org-toolbar-row">
              <span className="muted">
                Средняя посещаемость за 12 месяцев: <strong>{data.last_12m_avg ?? "—"}</strong>
                {data.yoy_delta_pct != null && data.prev_12m_avg != null && (
                  <>
                    {" "}
                    ·{" "}
                    <span className={data.yoy_delta_pct >= 0 ? "org-delta-good" : "org-delta-bad"}>
                      {data.yoy_delta_pct > 0 ? "+" : ""}
                      {data.yoy_delta_pct}% к прошлому году
                    </span>{" "}
                    (было {data.prev_12m_avg})
                  </>
                )}
                {data.record_finishers != null && (
                  <>
                    {" "}
                    · рекорд: <strong>{formatInt(data.record_finishers)}</strong> ({data.record_date})
                  </>
                )}
                {" "}· состоявшихся стартов: {formatInt(data.events_total)} (все системы локации)
              </span>
            </div>
          </section>

          <section className="card org-table-card">
            <header className="org-table-head">
              <h2 className="section-title">
                <span className="org-table-emoji" aria-hidden="true">
                  📈
                </span>
                Среднее число финишёров по месяцам
              </h2>
              <span className="muted org-table-count">
                вся история{chartMonths.length > 0 && <> · с {monthLabel(chartMonths[0].month)}</>}
              </span>
            </header>
            {platformsInChart.length > 1 && (
              <div className="org-legend">
                {platformsInChart.map((code) => (
                  <span key={code} className="org-legend-item">
                    <span className={`org-legend-dot ${platformClass(code)}`} aria-hidden="true" />
                    {platformCodeLabel(code)}
                  </span>
                ))}
              </div>
            )}
            <div className="org-hist-plot">
              <div className="org-hist-axis" aria-hidden="true">
                <span style={{ top: 0 }}>{formatInt(chartMax)}</span>
                <span style={{ top: "50%" }}>{formatInt(Math.round(chartMax / 2))}</span>
              </div>
              {/* У старых площадок в «истории» под сотню месяцев, и зазор в три
                  пикселя съел бы полполотна: сужаем его по числу колонок. */}
              <div
                className="org-hist"
                role="img"
                aria-label="Посещаемость по месяцам за всю историю"
                style={{ gap: chartMonths.length > 40 ? 1 : chartMonths.length > 20 ? 2 : 3 }}
              >
                {chartMonths.map((item) => (
                  <div key={item.month} className="org-hist-col">
                    {/* Нативный title у браузера всплывает с секундной задержкой и
                        «не работает» на глаз — поэтому канонический ChartColumnTooltip,
                        как у гистограммы финишей: мгновенный ховер + тап на телефоне. */}
                    <ChartColumnTooltip
                      title={monthLabel(item.month)}
                      lines={
                        item.empty
                          ? ["Стартов с протоколами не было"]
                          : [
                              pluralizeRu(item.events, ["старт", "старта", "стартов"]),
                              `Финишёров в среднем: ${item.avg_finishers}`,
                              `Максимум: ${formatInt(item.max_finishers)}`,
                              platformCodeLabel(item.platform_code),
                            ]
                      }
                    >
                      {!item.empty && (
                        <div
                          className={`org-hist-bar ${platformClass(item.platform_code)}`}
                          style={{ height: `${Math.max(3, (item.avg_finishers / chartMax) * 100)}%` }}
                        />
                      )}
                    </ChartColumnTooltip>
                    {item.month.endsWith("-01") && (
                      <span className="org-hist-year">{item.month.slice(0, 4)}</span>
                    )}
                  </div>
                ))}
              </div>
            </div>
            <p className="muted org-hist-note">
              Наведите на столбик (на телефоне — тапните), чтобы увидеть месяц и цифры. Цвет
              столбика — система, в которой локация проводила старты в этот месяц.
            </p>
          </section>

          <section className="card org-table-card">
            <header className="org-table-head">
              <h2 className="section-title">
                <span className="org-table-emoji" aria-hidden="true">
                  🗓
                </span>
                Последние старты
              </h2>
              <span className="muted org-table-count">20 последних</span>
            </header>
            <TableWrap innerRef={attachFloatingHead}>
              <table className="data-table org-svod-table">
                <thead>
                  <tr>
                    <th>Дата</th>
                    <th>№</th>
                    <th>Система</th>
                    <th>
                      Финишёров
                      <HeaderHint text="Полоса — сколько это от рекорда локации. Прочерк — протокола нет: старт отменён или ещё не загружен" />
                    </th>
                    <th>
                      ± к прошлому
                      <HeaderHint text="Насколько больше или меньше финишёров, чем на предыдущем состоявшемся старте" />
                    </th>
                    <th>Волонтёров</th>
                  </tr>
                </thead>
                <tbody>
                  {recentEvents.map((event) => (
                    <tr key={event.date}>
                      <td>{event.date_display}</td>
                      <td>{event.event_number ?? "—"}</td>
                      <td>
                        <span className={`org-badge org-badge-plat ${platformClass(event.platform_code)}`}>
                          {platformCodeLabel(event.platform_code)}
                        </span>
                      </td>
                      <td>
                        {event.finishers > 0 ? (
                          <div className="org-bar-row">
                            <div
                              className={`org-bar ${platformClass(event.platform_code)}`}
                              style={{
                                width: `${Math.min(
                                  100,
                                  (event.finishers / Math.max(1, recordFinishers ?? event.finishers)) * 100,
                                )}%`,
                              }}
                            />
                            <span>
                              {formatInt(event.finishers)}
                              {recordFinishers != null && event.finishers >= recordFinishers && (
                                <span title="Рекорд локации"> 🏆</span>
                              )}
                            </span>
                          </div>
                        ) : (
                          <span
                            className="muted"
                            title="Протокола нет — старт не состоялся или не загружен"
                          >
                            —
                          </span>
                        )}
                      </td>
                      {/* Минус к прошлому старту — обычные колебания явки, а не
                          авария: янтарный вместо красного, красный тут читался
                          как поломка (Дмитрий 02.09.2026). */}
                      <td>
                        {event.delta != null ? (
                          <span className={event.delta >= 0 ? "org-delta-good" : "org-delta-mid"}>
                            {event.delta > 0 ? "+" : ""}
                            {formatInt(event.delta)}
                          </span>
                        ) : (
                          "—"
                        )}
                      </td>
                      <td>{event.volunteers > 0 ? formatInt(event.volunteers) : "—"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </TableWrap>
          </section>
        </>
      )}
    </PortalSectionShell>
  );
}

export function OrganizerAttendancePage({ slug }: { slug: string }) {
  return (
    <RequireAuth loginHref={PORTAL_LOGIN_HREF}>
      {() => <OrganizerAttendanceContent slug={slug} />}
    </RequireAuth>
  );
}
