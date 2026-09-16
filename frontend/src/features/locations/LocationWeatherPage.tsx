import { useEffect } from "react";
import { useCachedResource } from "../../hooks/useCachedResource";
import { PlatformBadge } from "../../components/PlatformBadge";
import { StatHintTooltip } from "../../components/StatHintTooltip";
import { TableWrap } from "../../components/tableUx/TableWrap";
import { useNarrowViewport } from "../../components/tableUx/useNarrowViewport";
import { WeatherChip } from "../../components/WeatherChip";
import { getLocationWeather, type LocationWeather, type LocationWeatherMonth } from "../../lib/api";
import { formatDate, formatInt, pluralizeRu } from "../../lib/format";
import { locationHintFor, rememberLocationHint } from "../../lib/locationHint";
import { applyPageMeta } from "../../lib/pageMeta";
import { formatTemp, temperatureTone, type WeatherRecord } from "../../lib/weather";
import { PortalSectionShell } from "../portal/PortalSectionShell";

// Отдельная страница «Погода на стартах» локации: график по месяцам
// (медиана и разброс температуры в час старта), доли дождя и снега, рекорды,
// «год назад» и явка по погоде. Данные — /locations/page/{slug}/weather.

const RECORD_TITLES: Array<{ key: string; label: string; value: (record: WeatherRecord) => string }> = [
  { key: "coldest", label: "Самый холодный", value: (r) => formatTemp(r.weather.temperature_c) },
  { key: "hottest", label: "Самый жаркий", value: (r) => formatTemp(r.weather.temperature_c) },
  { key: "wettest", label: "Самый мокрый", value: (r) => `${(r.weather.precipitation_run_mm ?? 0).toFixed(1)} мм` },
  { key: "windiest", label: "Самый ветреный", value: (r) => `порывы ${Math.round(r.weather.wind_gusts_ms ?? 0)} м/с` },
  { key: "snowiest", label: "Самый снежный", value: (r) => `${Math.round(r.weather.snow_depth_cm ?? 0)} см снега` },
];

const TONE_FILL: Record<string, string> = {
  frost: "var(--weather-frost)",
  cold: "var(--weather-cold)",
  mild: "var(--weather-mild)",
  warm: "var(--weather-warm)",
  hot: "var(--weather-hot)",
  none: "var(--border)",
};

function percent(share: number | null): string {
  return share == null ? "—" : `${Math.round(share * 100)}%`;
}

function startLine(record: WeatherRecord): string {
  const parts: string[] = [formatDate(record.weather.date)];
  if (record.start?.event_number) {
    parts.push(`№${record.start.event_number}`);
  }
  if (record.start?.finishers != null) {
    parts.push(pluralizeRu(record.start.finishers, ["финишёр", "финишёра", "финишёров"]));
  }
  return parts.join(" · ");
}

/** График по месяцам: столбик — разброс от самого холодного до самого тёплого старта, точка — медиана. */
function MonthChart({ months }: { months: LocationWeatherMonth[] }) {
  const filled = months.filter((m) => m.basis !== "none" && m.temperature_min_c != null && m.temperature_max_c != null);
  if (filled.length === 0) {
    return null;
  }
  const lo = Math.min(0, ...filled.map((m) => m.temperature_min_c ?? 0)) - 3;
  const hi = Math.max(10, ...filled.map((m) => m.temperature_max_c ?? 0)) + 3;
  const width = 720;
  const height = 260;
  const padL = 40;
  const padB = 28;
  const padT = 10;
  const innerH = height - padB - padT;
  const y = (t: number) => padT + ((hi - t) / (hi - lo)) * innerH;
  const colW = (width - padL) / 12;
  const ticks: number[] = [];
  for (let t = Math.ceil(lo / 10) * 10; t <= hi; t += 10) {
    ticks.push(t);
  }
  return (
    <svg className="loc-weather-chart" viewBox={`0 0 ${width} ${height}`} role="img" aria-label="Температура на старте по месяцам">
      {ticks.map((t) => (
        <g key={t}>
          <line x1={padL} x2={width} y1={y(t)} y2={y(t)} className={`loc-weather-chart-grid${t === 0 ? " loc-weather-chart-zero" : ""}`} />
          <text x={padL - 6} y={y(t) + 4} textAnchor="end" className="loc-weather-chart-tick">
            {formatTemp(t)}
          </text>
        </g>
      ))}
      {months.map((month, index) => {
        const cx = padL + colW * index + colW / 2;
        if (month.basis === "none" || month.temperature_min_c == null || month.temperature_max_c == null) {
          return (
            <text key={month.month} x={cx} y={height - 8} textAnchor="middle" className="loc-weather-chart-label muted">
              {month.label.slice(0, 3)}
            </text>
          );
        }
        const tone = temperatureTone(month.temperature_median_c);
        const top = y(month.temperature_max_c);
        const bottom = y(month.temperature_min_c);
        return (
          <g key={month.month} className={month.basis === "saturdays" ? "loc-weather-chart-saturdays" : undefined}>
            <title>
              {`${month.label}: медиана ${formatTemp(month.temperature_median_c)}, от ${formatTemp(
                month.temperature_min_c,
              )} до ${formatTemp(month.temperature_max_c)}`}
            </title>
            <rect x={cx - colW * 0.28} width={colW * 0.56} y={top} height={Math.max(3, bottom - top)} rx={5} fill={TONE_FILL[tone]} opacity={0.35} />
            {month.temperature_median_c != null && (
              <>
                <line x1={cx - colW * 0.28} x2={cx + colW * 0.28} y1={y(month.temperature_median_c)} y2={y(month.temperature_median_c)} stroke={TONE_FILL[tone]} strokeWidth={3} />
                <text x={cx} y={y(month.temperature_median_c) - 7} textAnchor="middle" className="loc-weather-chart-value" fill={TONE_FILL[tone]}>
                  {formatTemp(month.temperature_median_c)}
                </text>
              </>
            )}
            <text x={cx} y={height - 8} textAnchor="middle" className="loc-weather-chart-label">
              {month.label.slice(0, 3)}
            </text>
          </g>
        );
      })}
    </svg>
  );
}

/** Полосы «дождь / снег» по месяцам — доля стартов. */
function ShareBars({ months, field, label }: { months: LocationWeatherMonth[]; field: "rain_share" | "snow_share"; label: string }) {
  return (
    <div className="loc-weather-shares">
      <div className="loc-weather-shares-label">{label}</div>
      <div className="loc-weather-shares-row">
        {months.map((month) => {
          const share = month[field];
          const pct = share == null ? 0 : Math.round(share * 100);
          return (
            <div className="loc-weather-share" key={month.month} title={`${month.label}: ${share == null ? "нет данных" : `${pct}%`}`}>
              <div className="loc-weather-share-pct">{share == null ? "·" : `${pct}`}</div>
              <div className="loc-weather-share-bar">
                <div className={`loc-weather-share-fill loc-weather-share-${field === "rain_share" ? "rain" : "snow"}`} style={{ height: `${pct}%` }} />
              </div>
              <div className="loc-weather-share-month muted">{month.label.slice(0, 3)}</div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

function AttendanceChart({ data }: { data: LocationWeather }) {
  const items = data.attendance.filter((item) => item.avg_finishers != null);
  if (items.length === 0) {
    return null;
  }
  const max = Math.max(...items.map((item) => item.avg_finishers ?? 0), 1);
  return (
    <div className="loc-weather-attendance-chart">
      {items.map((item) => (
        <div className="loc-weather-attendance-bar-row" key={item.key}>
          <div className="loc-weather-attendance-bar-label">{item.label}</div>
          <div className="loc-weather-attendance-bar-track">
            <div
              className={`loc-weather-attendance-bar-fill loc-weather-attendance-${item.key}`}
              style={{ width: `${Math.max(4, Math.round(((item.avg_finishers ?? 0) / max) * 100))}%` }}
            />
          </div>
          <div className="loc-weather-attendance-bar-value">
            <b>{formatInt(Math.round(item.avg_finishers ?? 0))}</b>{" "}
            <span className="muted">
              финишёров в среднем · {pluralizeRu(item.starts, ["старт", "старта", "стартов"])}
            </span>
          </div>
        </div>
      ))}
    </div>
  );
}

export function LocationWeatherPage({ slug }: { slug: string }) {
  const { data, error } = useCachedResource(`locations:weather:${slug}`, () => getLocationWeather(slug), [slug], {
    errorText: "Не удалось загрузить погоду",
    onLoaded: (payload) => {
      rememberLocationHint({ slug: payload.slug, name: payload.name });
    },
  });
  const narrowViewport = useNarrowViewport();

  useEffect(() => {
    if (data) {
      applyPageMeta({
        title: `${data.name} — погода на стартах — run5k.run`,
        description: `Какая погода бывает на старте «${data.name}» по месяцам: температура в час старта, дождь и снег, рекорды и явка.`,
        indexable: true,
      });
    }
  }, [data]);

  const sidebarLocation = data ? { slug: data.slug, name: data.name } : locationHintFor(slug);

  if (error) {
    return (
      <PortalSectionShell sidebar={{ active: "locations", location: sidebarLocation }}>
        <div className="card error">
          <p>{error}</p>
        </div>
      </PortalSectionShell>
    );
  }
  if (!data) {
    return (
      <PortalSectionShell sidebar={{ active: "locations", location: sidebarLocation }}>
        <p className="muted">Загрузка…</p>
      </PortalSectionShell>
    );
  }

  const records = RECORD_TITLES.flatMap(({ key, label, value }) => {
    const record = data.records[key];
    return record ? [{ key, label, value: value(record), record }] : [];
  });

  return (
    <PortalSectionShell sidebar={{ active: "locations", location: sidebarLocation }}>
      <header className="loc-header loc-wide-page">
        <p className="muted loc-header-breadcrumb">
          <a href="/locations">← Все локации</a> / <a href={`/locations/${data.slug}`}>{data.name}</a> / Погода
        </p>
        <div className="loc-header-title">
          <h1>{data.name} — погода на стартах</h1>
        </div>
        <p className="protocol-subtitle">
          Погода из архива Open-Meteo: координаты локации, время старта. Здесь{" "}
          {pluralizeRu(data.starts_with_weather, ["старт", "старта", "стартов"])} с погодой. Температура и ветер
          надёжны, а осадки модель размазывает по времени — дождём считаем от 1 мм в окне «час до старта — два
          часа после».
        </p>
      </header>

      {!data.has_data ? (
        <section className="card loc-section">
          <p className="muted">Погоды по этой локации пока нет: площадка без координат или вне периметра сбора.</p>
        </section>
      ) : (
        <>
          {(data.latest || data.years_ago.length > 0) && (
            <section className="card loc-section loc-weather">
              <h2 className="section-title">Сейчас и в прошлые годы</h2>
              <div className="loc-weather-now">
                {data.latest && (
                  <div className="loc-weather-now-item loc-weather-now-latest">
                    <div className="loc-weather-now-label">
                      {data.latest.start ? "Последний старт" : "Последняя суббота"} · {formatDate(data.latest.weather.date)}
                    </div>
                    <div className="loc-weather-now-value">
                      <WeatherChip weather={data.latest.weather} full />
                    </div>
                  </div>
                )}
                {data.years_ago.map((item) => (
                  <div className="loc-weather-now-item" key={item.years}>
                    <div className="loc-weather-now-label">
                      {item.years === 1 ? "Год назад" : `${item.years} года назад`} · {formatDate(item.weather.date)}
                      {!item.start && <span className="muted"> · старта не было</span>}
                    </div>
                    <div className="loc-weather-now-value">
                      <WeatherChip weather={item.weather} full />
                    </div>
                  </div>
                ))}
              </div>
            </section>
          )}

          <section className="card loc-section loc-weather">
            <h2 className="section-title">
              Температура на старте по месяцам
              <StatHintTooltip text="Столбик — от самого холодного до самого тёплого старта в этом месяце за все годы, отметка — медиана.">
                <span className="loc-section-title-info" aria-label="Как считается">
                  ⓘ
                </span>
              </StatHintTooltip>
            </h2>
            <MonthChart months={data.months} />
            <div className="loc-weather-shares-grid">
              <ShareBars months={data.months} field="rain_share" label="Доля стартов с дождём, %" />
              <ShareBars months={data.months} field="snow_share" label="Доля стартов со снегом, %" />
            </div>
            {!narrowViewport && (
              <TableWrap className="loc-weather-wrap">
                <table className="data-table loc-weather-table">
                  <thead>
                    <tr>
                      <th>Месяц</th>
                      <th title="Медиана температуры в час старта">На старте</th>
                      <th title="Медиана «ощущается как»">Ощущается</th>
                      <th title="Самый холодный и самый тёплый старт месяца">Разброс</th>
                      <th>Дождь</th>
                      <th>Снег</th>
                      <th title="Стартов локации в этом месяце с погодой">Стартов</th>
                    </tr>
                  </thead>
                  <tbody>
                    {data.months.map((month) => (
                      <tr key={month.month} className={month.basis === "saturdays" ? "loc-weather-row-saturdays" : undefined}>
                        <td className="loc-weather-month">
                          {month.label}
                          {month.basis === "saturdays" && <span className="loc-weather-basis-note"> · без стартов</span>}
                        </td>
                        <td className={`loc-weather-temp temp-${temperatureTone(month.temperature_median_c)}`}>{formatTemp(month.temperature_median_c)}</td>
                        <td className="muted">{formatTemp(month.apparent_median_c)}</td>
                        <td className="muted">
                          {month.basis === "none" ? "—" : `${formatTemp(month.temperature_min_c)} … ${formatTemp(month.temperature_max_c)}`}
                        </td>
                        <td>{percent(month.rain_share)}</td>
                        <td>{percent(month.snow_share)}</td>
                        <td className="muted">{month.starts > 0 ? formatInt(month.starts) : "—"}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </TableWrap>
            )}
          </section>

          {records.length > 0 && (
            <section className="card loc-section loc-weather">
              <h2 className="section-title">Рекорды локации</h2>
              <div className="loc-weather-records">
                {records.map(({ key, label, value, record }) => (
                  <div className="loc-weather-record" key={key}>
                    <div className="loc-weather-record-label">{label}</div>
                    <div className={`loc-weather-record-value temp-${key === "coldest" || key === "hottest" ? temperatureTone(record.weather.temperature_c) : "none"}`}>
                      <span aria-hidden>{record.weather.icon}</span> {value}
                    </div>
                    <div className="loc-weather-record-sub muted">
                      {startLine(record)}
                      {record.start && (
                        <>
                          {" "}
                          <PlatformBadge code={record.start.platform_code} />
                        </>
                      )}
                    </div>
                    <div className="loc-weather-record-summary">
                      <WeatherChip weather={record.weather} full />
                    </div>
                  </div>
                ))}
              </div>
            </section>
          )}

          {data.attendance.length > 0 && (
            <section className="card loc-section loc-weather">
              <h2 className="section-title">
                Явка и погода
                <StatHintTooltip text="Среднее число финишёров на стартах локации в такую погоду: корзины по температуре в час старта и отдельно сухие старты против дождливых.">
                  <span className="loc-section-title-info" aria-label="Как считается">
                    ⓘ
                  </span>
                </StatHintTooltip>
              </h2>
              <AttendanceChart data={data} />
            </section>
          )}
        </>
      )}
    </PortalSectionShell>
  );
}
