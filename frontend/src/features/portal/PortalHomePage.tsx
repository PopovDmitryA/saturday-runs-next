import { useEffect, useMemo, useState } from "react";
import { PlatformBadge } from "../../components/PlatformBadge";
import { PORTAL_LOGIN_HREF } from "../../lib/portalRoutes";
import { PortalGeoMap } from "./PortalGeoMap";
import { PortalHeader } from "./PortalHeader";
import { PLATFORM_CHART_META, PortalTrendChart, type TrendPoint } from "./PortalTrendChart";
import {
  fetchPortalHome,
  type PortalAttendanceTopRow,
  type PortalFastestRow,
  type PortalHomeResponse,
} from "./portalTypes";
import "./portal.css";

const EARTH_EQUATOR_KM = 40_075;
const SECONDS_PER_YEAR = 365 * 24 * 3600;

function plural(count: number, one: string, few: string, many: string): string {
  const mod100 = Math.abs(count) % 100;
  const mod10 = mod100 % 10;
  if (mod100 >= 11 && mod100 <= 14) return many;
  if (mod10 === 1) return one;
  if (mod10 >= 2 && mod10 <= 4) return few;
  return many;
}

function formatInt(value: number): string {
  return value.toLocaleString("ru-RU");
}

function formatDateLong(iso: string): string {
  const parsed = new Date(`${iso}T00:00:00`);
  return parsed.toLocaleDateString("ru-RU", {
    weekday: "long",
    day: "numeric",
    month: "long",
  });
}

function formatDateShort(iso: string): string {
  const parsed = new Date(`${iso}T00:00:00`);
  return parsed.toLocaleDateString("ru-RU", { day: "numeric", month: "long", year: "numeric" });
}

function formatDateCompact(iso: string): string {
  const parsed = new Date(`${iso}T00:00:00`);
  return parsed.toLocaleDateString("ru-RU", { day: "2-digit", month: "2-digit", year: "numeric" });
}

function funDistanceNote(totalKm: number): string {
  const laps = totalKm / EARTH_EQUATOR_KM;
  if (laps >= 1) {
    return `${formatInt(Math.round(laps))} раз вокруг Земли`;
  }
  return "дистанция всех финишей";
}

function funTimeNote(totalSec: number): string {
  const years = totalSec / SECONDS_PER_YEAR;
  if (years >= 1) {
    return `${formatInt(Math.round(years))} лет непрерывного бега`;
  }
  const days = totalSec / 86_400;
  return `${formatInt(Math.round(days))} суток непрерывного бега`;
}

function FastestPlatformCard({
  title,
  code,
  rows,
}: {
  title: string;
  code: string;
  rows: PortalFastestRow[];
}) {
  if (rows.length === 0) {
    return null;
  }
  return (
    <div className="portal-system portal-fastest-card">
      <div className="portal-system-head">
        <span className={`portal-system-dot ${code}`} />
        {title}
      </div>
      {rows.map((row) => (
        <div className="portal-fastest-row" key={row.gender}>
          <span className={`portal-gender-chip ${row.gender === "male" ? "male" : "female"}`}>
            {row.gender === "male" ? "М" : "Ж"}
          </span>
          <span className="portal-fastest-main">
            <b className="num">
              {row.value_display}
              {row.delta_sec != null && row.delta_sec > 0 && (
                <span className="portal-delta down">
                  ↓ −{row.delta_sec} сек
                </span>
              )}
            </b>
            <span>
              {row.runner_name ?? "Участник"} · {row.location_name},{" "}
              {formatDateCompact(row.event_date)}
            </span>
          </span>
        </div>
      ))}
    </div>
  );
}

function AttendanceTopList({ rows }: { rows: PortalAttendanceTopRow[] }) {
  if (rows.length === 0) {
    return <p className="portal-empty-note">Пока нет данных.</p>;
  }
  return (
    <ol className="portal-top-list">
      {rows.map((row, index) => (
        <li key={`${row.location_name}-${row.event_date}`}>
          <span className="portal-top-rank num">{index + 1}</span>
          <span className="portal-top-name">{row.location_name}</span>
          <PlatformBadge code={row.platform_code} />
          <span className="portal-top-value">
            <b className="num">{formatInt(row.finishers)}</b>
            <span>{formatDateCompact(row.event_date)}</span>
          </span>
        </li>
      ))}
    </ol>
  );
}

function GenderSplitPanel({ data }: { data: import("./portalTypes").PortalGenderSplit }) {
  const { male, female } = data.total;
  const known = male + female;
  if (known === 0) {
    return null;
  }
  const femalePct = Math.round((female / known) * 100);
  const malePct = 100 - femalePct;

  // донат: длина окружности при r=80
  const circumference = 2 * Math.PI * 80;
  const femaleArc = (female / known) * circumference;

  return (
    <section className="portal-panel" aria-label="Разбивка по полу">
      <div className="portal-panel-head">
        <div>
          <h2>Разбивка по полу</h2>
          <p className="portal-panel-sub">Доля финишей мужчин и женщин · вся история</p>
        </div>
      </div>
      <div className="portal-gender">
        <div className="portal-gender-donut-wrap">
          <div className="portal-gender-donut">
            <svg width="190" height="190" viewBox="0 0 190 190" aria-hidden="true">
              <circle cx="95" cy="95" r="80" fill="none" stroke="var(--link)" strokeWidth="28" />
              <circle
                cx="95"
                cy="95"
                r="80"
                fill="none"
                stroke="var(--tint-rose-text)"
                strokeWidth="28"
                strokeDasharray={`${femaleArc.toFixed(1)} ${circumference.toFixed(1)}`}
                transform="rotate(-90 95 95)"
              />
            </svg>
            <div className="portal-gender-donut-center">
              <b className="num">{femalePct}%</b>
              <span>женщины</span>
            </div>
          </div>
          <div className="portal-gender-legend">
            <span>
              <i style={{ background: "var(--tint-rose-text)" }} />
              Женщины {femalePct}%
            </span>
            <span>
              <i style={{ background: "var(--link)" }} />
              Мужчины {malePct}%
            </span>
          </div>
        </div>
        <div className="portal-gender-bars">
          {data.by_platform.map((row) => {
            const total = row.male + row.female;
            if (total === 0) {
              return null;
            }
            const fPct = Math.round((row.female / total) * 100);
            const meta = PLATFORM_CHART_META[row.platform_code];
            return (
              <div className="portal-gender-bar-row" key={row.platform_code}>
                <div className="portal-gender-bar-top">
                  <span>
                    <span
                      className="portal-gender-sys-dot"
                      style={{ background: meta?.cssVar ?? "var(--text-faint)" }}
                    />
                    {meta?.title ?? row.platform_code}
                  </span>
                  <b>{fPct}% Ж</b>
                </div>
                <div className="portal-gender-bar-track">
                  <span className="portal-gender-bar-f" style={{ width: `${fPct}%` }} />
                  <span className="portal-gender-bar-m" style={{ width: `${100 - fPct}%` }} />
                </div>
              </div>
            );
          })}
        </div>
      </div>
    </section>
  );
}

export function PortalHomePage() {
  const [data, setData] = useState<PortalHomeResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [period, setPeriod] = useState<"all" | "year">("all");

  useEffect(() => {
    fetchPortalHome()
      .then(setData)
      .catch((err) => setError(err instanceof Error ? err.message : "Не удалось загрузить данные"));
  }, []);

  const weekLabel = (iso: string) =>
    new Date(`${iso}T00:00:00`).toLocaleDateString("ru-RU", { day: "numeric", month: "short" });

  const chartPoints = useMemo<TrendPoint[]>(() => {
    if (!data) {
      return [];
    }
    if (period === "all") {
      return data.chart_years.map((point) => ({
        label: String(point.year),
        platforms: point.platforms,
      }));
    }
    return data.chart_weeks.map((point) => ({
      label: weekLabel(point.week_start),
      platforms: point.platforms,
    }));
  }, [data, period]);

  const chartProjection = useMemo<TrendPoint | null>(() => {
    if (!data?.chart_year_projection || period !== "all") {
      return null;
    }
    const projection = data.chart_year_projection;
    return {
      label: `${projection.year} (оценка)`,
      platforms: projection.platforms,
    };
  }, [data, period]);

  const newcomersProjection = useMemo<TrendPoint | null>(() => {
    if (!data?.newcomers_year_projection || period !== "all") {
      return null;
    }
    const projection = data.newcomers_year_projection;
    return {
      label: `${projection.year} (оценка)`,
      platforms: projection.platforms,
    };
  }, [data, period]);

  const locationsPoints = useMemo<TrendPoint[]>(() => {
    if (!data) {
      return [];
    }
    if (period === "all") {
      return data.locations_by_year.map((point) => ({
        label: String(point.year),
        platforms: point.platforms,
      }));
    }
    return data.locations_by_week.map((point) => ({
      label: weekLabel(point.week_start),
      platforms: point.platforms,
    }));
  }, [data, period]);

  const newcomersPoints = useMemo<TrendPoint[]>(() => {
    if (!data) {
      return [];
    }
    if (period === "all") {
      return data.newcomers_by_year.map((point) => ({
        label: String(point.year),
        platforms: point.platforms,
      }));
    }
    return data.newcomers_by_week.map((point) => ({
      label: weekLabel(point.week_start),
      platforms: point.platforms,
    }));
  }, [data, period]);

  const hero = data ? (period === "year" && data.hero_last_year ? data.hero_last_year : data.hero) : null;

  return (
    <>
      <PortalHeader />
      <main className="portal-home">
        {!data && !error && <p className="portal-loading">Загрузка статистики…</p>}
        {error && <p className="portal-error">{error}</p>}

        {data && (
          <>
            <section className="portal-hero">
              <p className="portal-eyebrow">Суббота · утро · парк · 5 км</p>
              <h1>Все парковые беговые системы — в цифрах</h1>
              <p className="portal-hero-lead">
                5 вёрст, S95, parkrun и RunPark: вся история — от первого старта до прошлой
                субботы, в одном месте.
              </p>
            </section>

            <section className="portal-scope" aria-label="Статистика за период">
            <div className="portal-period-sticky">
            <div className="portal-period" role="tablist" aria-label="Период статистики">
              <button
                type="button"
                className={period === "all" ? "active" : ""}
                onClick={() => setPeriod("all")}
              >
                За всё время
              </button>
              <button
                type="button"
                className={period === "year" ? "active" : ""}
                onClick={() => setPeriod("year")}
              >
                Последний год
              </button>
            </div>
            </div>

            {hero && (
              <section className="portal-counts" aria-label="Итоги периода">
                <div className="portal-count">
                  <b className="num">{formatInt(hero.finishes_total)}</b>
                  <span>финишей</span>
                </div>
                <div className="portal-count">
                  <b className="num">{formatInt(hero.participants_total)}</b>
                  <span>участников</span>
                </div>
                <div className="portal-count">
                  <b className="num">{formatInt(hero.locations_total)}</b>
                  <span>локаций</span>
                </div>
                <div className="portal-count">
                  <b className="num">{formatInt(hero.starts_total)}</b>
                  <span>стартов</span>
                </div>
              </section>
            )}

            <section className="portal-panel">
              <div className="portal-panel-head">
                <div>
                  <h2>Финиши по системам</h2>
                  {period === "year" ? (
                    <p className="portal-panel-sub">
                      Последние 52 недели — наведите на график, чтобы увидеть детали
                    </p>
                  ) : (
                    chartProjection && (
                      <p className="portal-panel-sub">
                        {chartProjection.label.replace(" (оценка)", "")} год ещё не закончился —
                        пунктир прогнозирует итог при текущем темпе
                      </p>
                    )
                  )}
                </div>
              </div>
              <PortalTrendChart
                points={chartPoints}
                totalLabel="Всего"
                showGrowth={period === "all"}
                projection={chartProjection}
              />
            </section>

            <section className="portal-panel">
              <div className="portal-panel-head">
                <div>
                  <h2>Новички по системам</h2>
                  <p className="portal-panel-sub">
                    {period === "all"
                      ? "Сколько людей пробежали свой первый старт в каждом году"
                      : "Первые старты по неделям за последние 52 недели"}
                  </p>
                  {newcomersProjection && (
                    <p className="portal-panel-sub">
                      {newcomersProjection.label.replace(" (оценка)", "")} год ещё не закончился —
                      пунктир прогнозирует итог при текущем темпе
                    </p>
                  )}
                </div>
              </div>
              <PortalTrendChart
                points={newcomersPoints}
                totalLabel="Всего"
                showGrowth={period === "all"}
                projection={newcomersProjection}
              />
            </section>

            <section className="portal-panel">
              <div className="portal-panel-head">
                <div>
                  <h2>Локации по системам</h2>
                  <p className="portal-panel-sub">
                    {period === "all"
                      ? "Сколько локаций проводили старты в каждом году"
                      : "Сколько локаций стартовали в каждую из 52 недель"}
                  </p>
                </div>
              </div>
              <PortalTrendChart points={locationsPoints} showGrowth={period === "all"} />
            </section>
            </section>

            <section className="portal-week-scope" aria-label="Итоги последней недели">
            <div className="portal-week-heading">Последняя беговая неделя</div>

            {data.pulse && (
              <div className="portal-week-sticky">
                <section className="portal-pulse" aria-label="Последняя суббота">
                  <span className="portal-pulse-title">
                    <i />
                    {formatDateLong(data.pulse.event_date)}
                  </span>
                  <span className="portal-pulse-metric">
                    <b className="num">{formatInt(data.pulse.starts)}</b> стартов
                  </span>
                  <span className="portal-pulse-metric">
                    <b className="num">{formatInt(data.pulse.finishes)}</b> финишей
                  </span>
                  <span className="portal-pulse-metric">
                    <b className="num">{formatInt(data.pulse.newcomers)}</b> новичков
                  </span>
                  <span className="portal-pulse-metric">
                    <b className="num">{formatInt(data.pulse.volunteers)}</b> волонтёров
                  </span>
                  <span className="portal-pulse-metric">
                    <b className="num">{formatInt(data.pulse.personal_records)}</b> личных рекордов
                  </span>
                </section>
              </div>
            )}



            <section id="records" className="portal-half-grid" aria-label="Итоги недели">
              <div className="portal-panel">
                <div className="portal-panel-head">
                  <div>
                    <h2>Рекорды посещаемости за неделю</h2>
                    <p className="portal-panel-sub">
                      Локации, собравшие больше финишёров, чем когда-либо
                    </p>
                  </div>
                </div>
                {!data.week_records || data.week_records.attendance.length === 0 ? (
                  <p className="portal-empty-note">На этой неделе рекордов посещаемости нет.</p>
                ) : (
                  <ul className="portal-record-list">
                    {data.week_records.attendance.map((record) => (
                      <li
                        className="portal-record-row"
                        key={`${record.location_name}-${record.event_date}`}
                      >
                        <span className="portal-record-icon">🏆</span>
                        <span className="portal-record-main">
                          <b>{record.location_name}</b>
                          <span>{formatDateShort(record.event_date)}</span>
                        </span>
                        <PlatformBadge code={record.platform_code} />
                        <span className="portal-record-value">
                          <b className="num">
                            {formatInt(record.finishers)}
                            <span className="portal-delta up">
                              ↑ +{formatInt(record.finishers - record.previous_record)}
                            </span>
                          </b>
                          <span>
                            было {formatInt(record.previous_record)}
                            {record.previous_record_date &&
                              ` · ${formatDateCompact(record.previous_record_date)}`}
                          </span>
                        </span>
                      </li>
                    ))}
                  </ul>
                )}
              </div>
              <div className="portal-panel">
                <div className="portal-panel-head">
                  <div>
                    <h2>Самые массовые</h2>
                    <p className="portal-panel-sub">
                      {data.pulse
                        ? `Топ-10 стартов по финишёрам · ${formatDateShort(data.pulse.event_date)}`
                        : "Топ-10 стартов по финишёрам"}
                    </p>
                  </div>
                </div>
                {data.top_saturday.length === 0 ? (
                  <p className="portal-empty-note">Нет данных за последнюю субботу.</p>
                ) : (
                  <div className="portal-bars">
                    {data.top_saturday.map((row) => {
                      const max = data.top_saturday[0]?.finishers ?? 1;
                      const widthPct = Math.max(14, Math.round((row.finishers / max) * 100));
                      return (
                        <div className="portal-bar-row" key={row.location_name}>
                          <span className="portal-bar-name">{row.location_name}</span>
                          <span className="portal-bar-track">
                            <span
                              className={`portal-bar-fill portal-bar-fill-${row.platform_code}`}
                              style={{ width: `${widthPct}%` }}
                            >
                              <span className="portal-bar-sys">
                                {row.platform_code === "five_verst"
                                  ? "5 вёрст"
                                  : row.platform_code === "s95"
                                    ? "S95"
                                    : row.platform_code === "runpark"
                                      ? "RunPark"
                                      : "parkrun"}
                              </span>
                            </span>
                          </span>
                          <span className="portal-bar-value num">{formatInt(row.finishers)}</span>
                        </div>
                      );
                    })}
                  </div>
                )}
              </div>
            </section>

            {data.week_records && data.week_records.course.length > 0 && (
              <section className="portal-panel" aria-label="Рекорды трасс">
                <div className="portal-panel-head">
                  <div>
                    <h2>Рекорды трасс за неделю</h2>
                    <p className="portal-panel-sub">
                      Рекорд трассы внутри своей системы — обновлён на этой неделе
                    </p>
                  </div>
                </div>
                <ul className="portal-record-list">
                  {data.week_records.course.map((record) => (
                    <li
                      className="portal-record-row"
                      key={`${record.location_name}-${record.gender}`}
                    >
                      <span
                        className={`portal-gender-chip ${record.gender === "male" ? "male" : "female"}`}
                      >
                        {record.gender === "male" ? "М" : "Ж"}
                      </span>
                      <span className="portal-record-main">
                        <b>
                          {record.runner_name ?? "Участник"} · {record.location_name}
                        </b>
                        <span>{formatDateShort(record.event_date)}</span>
                      </span>
                      <PlatformBadge code={record.platform_code} />
                      <span className="portal-record-value">
                        <b className="num">
                          {record.time_display}
                          {record.delta_sec != null && record.delta_sec > 0 && (
                            <span className="portal-delta down">↓ −{record.delta_sec} сек</span>
                          )}
                        </b>
                        {record.previous_display && (
                          <span>
                            было {record.previous_display}
                            {record.previous_record_date &&
                              ` · ${formatDateCompact(record.previous_record_date)}`}
                          </span>
                        )}
                      </span>
                    </li>
                  ))}
                </ul>
              </section>
            )}
            </section>

            <section className="portal-panel" aria-label="Рекорды посещаемости">
              <div className="portal-panel-head">
                <div>
                  <h2>Рекорды посещаемости</h2>
                  <p className="portal-panel-sub">
                    Топ-10 локаций по числу финишёров одного старта
                  </p>
                </div>
              </div>
              <div className="portal-att-grid">
                <div>
                  <h3 className="portal-att-subhead">За всю историю</h3>
                  <AttendanceTopList rows={data.attendance_top_all} />
                </div>
                <div>
                  <h3 className="portal-att-subhead">
                    {data.attendance_year ? `В ${data.attendance_year} году` : "В этом году"}
                  </h3>
                  <AttendanceTopList rows={data.attendance_top_year} />
                </div>
              </div>
            </section>

            <section id="systems" className="portal-systems" aria-label="Беговые системы">
              {data.systems.map((system) => (
                <div className="portal-system" key={system.code}>
                  <div className="portal-system-head">
                    <span className={`portal-system-dot ${system.code}`} />
                    {system.title}
                    <span className={`portal-system-state ${system.is_active ? "live" : "archive"}`}>
                      {system.is_active ? "активна" : "архив"}
                    </span>
                  </div>
                  <div className="portal-system-rows">
                    <b>{formatInt(system.locations)}</b>{" "}
                    {system.is_active
                      ? plural(system.locations, "действующая локация", "действующие локации", "действующих локаций")
                      : plural(system.locations, "локация", "локации", "локаций")}{" "}
                    · <b>{formatInt(system.finishes)}</b>{" "}
                    {plural(system.finishes, "финиш", "финиша", "финишей")}
                    {system.avg_finish_display && (
                      <>
                        <br />
                        среднее время <b>{system.avg_finish_display}</b>
                      </>
                    )}
                  </div>
                </div>
              ))}
            </section>

            {data.gender_split && <GenderSplitPanel data={data.gender_split} />}

            <section id="geo" className="portal-panel" aria-label="География">
              <div className="portal-panel-head">
                <div>
                  <h2>География</h2>
                  <p className="portal-panel-sub">
                    {formatInt(data.geo.locations_total)}{" "}
                    {plural(data.geo.locations_total, "локация", "локации", "локаций")}
                    {data.geo.regions_total > 0 &&
                      ` в ${formatInt(data.geo.regions_total)} ${plural(data.geo.regions_total, "регионе", "регионах", "регионах")}`}
                    {" — размер точки: число стартов"}
                  </p>
                </div>
              </div>
              <PortalGeoMap points={data.geo.points} />
            </section>

            <section className="portal-panel" aria-label="Самый массовый день">
              <div className="portal-panel-head portal-panel-head-split">
                <div>
                  <h2>Самый массовый день</h2>
                  <p className="portal-panel-sub">
                    Больше всего финишей за один день — по всем системам и в разрезе каждой
                  </p>
                </div>
                {data.busiest_day_overall && (
                  <div className="portal-hero-plaque">
                    <p className="portal-fact-label">Все системы вместе</p>
                    <p className="portal-hero-plaque-line">
                      <b className="num">{formatInt(data.busiest_day_overall.finishers)}</b>
                      <span>
                        финишей · {formatDateShort(data.busiest_day_overall.event_date)}
                      </span>
                    </p>
                  </div>
                )}
              </div>
              <div className="portal-fastest-grid">
                {data.systems.map((system) => {
                  const row = data.busiest_day_by_platform.find(
                    (item) => item.platform_code === system.code,
                  );
                  if (!row) {
                    return null;
                  }
                  return (
                    <div className="portal-system portal-fastest-card" key={system.code}>
                      <div className="portal-system-head">
                        <span className={`portal-system-dot ${system.code}`} />
                        {system.title}
                      </div>
                      <div className="portal-fastest-row">
                        <span className="portal-fastest-main">
                          <b className="num">{formatInt(row.finishers)} финишей</b>
                          <span>{formatDateCompact(row.event_date)}</span>
                        </span>
                      </div>
                    </div>
                  );
                })}
              </div>
            </section>

            <section className="portal-panel" aria-label="Самые быстрые 5 км">
              <div className="portal-panel-head">
                <div>
                  <h2>Самые быстрые 5 км</h2>
                  <p className="portal-panel-sub">Рекорды каждой системы за всю историю</p>
                </div>
              </div>
              <div className="portal-fastest-grid">
                {data.systems.map((system) => (
                  <FastestPlatformCard
                    key={system.code}
                    title={system.title}
                    code={system.code}
                    rows={data.fastest.filter((row) => row.platform_code === system.code)}
                  />
                ))}
              </div>
            </section>

            <section className="portal-facts" aria-label="Интересные факты">
              <div className="portal-fact">
                <p className="portal-fact-label">Суммарная дистанция</p>
                <b className="num">{formatInt(data.fun_facts.total_distance_km)} км</b>
                <span>{funDistanceNote(data.fun_facts.total_distance_km)}</span>
                {!!data.fun_facts.distance_delta_km && (
                  <span className="portal-delta up">
                    ↑ +{formatInt(data.fun_facts.distance_delta_km)} км за неделю
                  </span>
                )}
              </div>
              {data.fun_facts.avg_finish_display && (
                <div className="portal-fact">
                  <p className="portal-fact-label">Среднее время финиша</p>
                  <b className="num">{data.fun_facts.avg_finish_display}</b>
                  <span>все системы, вся история</span>
                  {!!data.fun_facts.avg_delta_sec && (
                    <span className={`portal-delta ${data.fun_facts.avg_delta_sec > 0 ? "up" : "down"}`}>
                      {data.fun_facts.avg_delta_sec > 0 ? "↑ +" : "↓ −"}
                      {Math.abs(data.fun_facts.avg_delta_sec)} сек за неделю
                    </span>
                  )}
                </div>
              )}
              {data.fun_facts.total_time_sec > 0 && (
                <div className="portal-fact">
                  <p className="portal-fact-label">Время на трассе</p>
                  <b className="num">
                    {formatInt(Math.round(data.fun_facts.total_time_sec / 3600))} часов
                  </b>
                  <span>{funTimeNote(data.fun_facts.total_time_sec)}</span>
                  {!!data.fun_facts.time_delta_sec && (
                    <span className="portal-delta up">
                      ↑ +{formatInt(Math.round(data.fun_facts.time_delta_sec / 3600))} часов за неделю
                    </span>
                  )}
                </div>
              )}
            </section>

            <section className="portal-cta portal-cta-split">
              <div className="portal-cta-copy">
                <h2>А теперь найдите здесь себя</h2>
                <p>
                  Привяжите профили беговых систем — и получите личную статистику по всем
                  платформам: рекорды, серии суббот, карту визитов, встречи и вехи вашей беговой
                  истории.
                </p>
                <div className="portal-cta-actions">
                  <a className="btn primary" href={PORTAL_LOGIN_HREF}>
                    Создать кабинет
                  </a>
                </div>
              </div>

              <div className="portal-poster" aria-hidden="true">
                <div className="portal-poster-badge">пример вашей карточки</div>
                <div className="portal-poster-head">
                  <span className="portal-poster-avatar" />
                  <div>
                    <b>Ваше имя</b>
                    <span>run5k.run · с апреля 2022</span>
                  </div>
                </div>

                <div className="portal-poster-grid6">
                  <div className="portal-poster-tile">
                    <b className="num">312</b>
                    <span>пробежек</span>
                  </div>
                  <div className="portal-poster-tile">
                    <b className="num">21:47</b>
                    <span>рекорд</span>
                  </div>
                  <div className="portal-poster-tile">
                    <b className="num">18</b>
                    <span>суббот подряд</span>
                  </div>
                  <div className="portal-poster-tile">
                    <b className="num">46</b>
                    <span>локаций</span>
                  </div>
                  <div className="portal-poster-tile">
                    <b className="num">5:22</b>
                    <span>темп/км</span>
                  </div>
                  <div className="portal-poster-tile">
                    <b className="num">34</b>
                    <span>волонтёрств</span>
                  </div>
                </div>

                <div className="portal-poster-systems">
                  <div className="portal-poster-sys-row">
                    <b style={{ color: "var(--accent-green-text)" }}>5 вёрст</b>
                    <span className="portal-poster-sys-track">
                      <span
                        className="portal-poster-sys-fill"
                        style={{ width: "82%", background: "var(--accent-green-text)" }}
                      />
                    </span>
                    <span className="num">228</span>
                  </div>
                  <div className="portal-poster-sys-row">
                    <b style={{ color: "var(--link)" }}>S95</b>
                    <span className="portal-poster-sys-track">
                      <span
                        className="portal-poster-sys-fill"
                        style={{ width: "34%", background: "var(--link)" }}
                      />
                    </span>
                    <span className="num">61</span>
                  </div>
                  <div className="portal-poster-sys-row">
                    <b style={{ color: "var(--tint-violet-text)" }}>parkrun</b>
                    <span className="portal-poster-sys-track">
                      <span
                        className="portal-poster-sys-fill"
                        style={{ width: "14%", background: "var(--tint-violet-text)" }}
                      />
                    </span>
                    <span className="num">23</span>
                  </div>
                </div>

                <div className="portal-poster-row2">
                  <div className="portal-poster-panel">
                    <p className="portal-poster-panel-label">Финиши по годам</p>
                    <svg
                      className="portal-poster-spark"
                      viewBox="0 0 220 44"
                      preserveAspectRatio="none"
                      aria-hidden="true"
                    >
                      <polyline
                        points="0,38 25,32 50,28 75,16 100,20 125,10 150,4 175,8 200,2 220,6"
                        fill="none"
                        stroke="var(--accent-indigo)"
                        strokeWidth="2.5"
                        strokeLinecap="round"
                        strokeLinejoin="round"
                      />
                    </svg>
                  </div>
                  <div className="portal-poster-panel">
                    <p className="portal-poster-panel-label">Активность</p>
                    <div className="portal-poster-heat">
                      {Array.from({ length: 36 }).map((_, index) => (
                        <i
                          key={index}
                          className={
                            [1, 2, 5, 6, 8, 11, 12, 15, 18, 19, 22, 25, 28, 31, 33].includes(index)
                              ? "on"
                              : [3, 9, 16, 23, 30].includes(index)
                                ? "vol"
                                : ""
                          }
                        />
                      ))}
                    </div>
                  </div>
                </div>

                <div className="portal-poster-panel">
                  <p className="portal-poster-panel-label">Карта визитов · 12 регионов</p>
                  <div className="portal-poster-map">
                    <svg viewBox="0 0 460 96" aria-hidden="true">
                      <circle cx="40" cy="62" r="5" fill="var(--accent-green-text)" />
                      <circle cx="85" cy="34" r="4" fill="var(--accent-green-text)" />
                      <circle cx="135" cy="54" r="6" fill="var(--accent-indigo)" />
                      <circle cx="175" cy="26" r="4" fill="var(--link)" />
                      <circle cx="215" cy="70" r="4" fill="var(--accent-green-text)" />
                      <circle cx="260" cy="40" r="5" fill="var(--tint-violet-text)" />
                      <circle cx="300" cy="58" r="4" fill="var(--accent-green-text)" />
                      <circle cx="335" cy="30" r="4" fill="var(--accent-green-text)" />
                      <circle cx="375" cy="54" r="3.5" fill="var(--link)" />
                      <circle cx="415" cy="34" r="4" fill="var(--accent-green-text)" />
                    </svg>
                  </div>
                </div>

                <div className="portal-poster-foot">
                  <span className="portal-poster-chip">🏅 Клуб 250</span>
                  <span className="portal-poster-chip">🗺️ 12 регионов</span>
                  <span className="portal-poster-chip">⚡ 8 PR</span>
                </div>
              </div>
            </section>
          </>
        )}
      </main>
    </>
  );
}
