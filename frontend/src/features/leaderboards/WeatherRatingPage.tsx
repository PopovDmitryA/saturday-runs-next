import { useEffect, useState } from "react";
import { PlatformBadge } from "../../components/PlatformBadge";
import { StatHintTooltip } from "../../components/StatHintTooltip";
import { TableWrap } from "../../components/tableUx/TableWrap";
import { formatDate, formatInt, pluralFormRu, pluralizeRu } from "../../lib/format";
import { surnameFirst } from "../../lib/personName";
import { formatTemp, temperatureTone } from "../../lib/weather";
import { PortalSectionShell } from "../portal/PortalSectionShell";
import { formatFinishTime } from "./formatFinishTime";
import { RatingsLoginBanner } from "./RatingsLoginBanner";
import {
  getWeatherRating,
  type SeasonLocationRow,
  type TemperatureBucket,
  type WalrusRow,
  type WeatherExtreme,
  type WeatherRatingResponse,
} from "./weatherRatingApi";
import "./leaderboards.css";

// Рейтинг «Погода»: три витрины на одной странице — моржи (люди), суровые
// локации (площадки) и температура против результата (вся страна). Всё
// считается из архива погоды в точке старта, см. weather_rating_service.

const EXTREME_TITLES: Array<{ key: string; label: string; value: (item: WeatherExtreme) => string }> = [
  { key: "coldest", label: "Самый холодный старт в истории", value: (i) => formatTemp(i.weather.temperature_c) },
  { key: "hottest", label: "Самый жаркий старт", value: (i) => formatTemp(i.weather.temperature_c) },
  {
    key: "wettest",
    label: "Самый мокрый старт",
    value: (i) => `${(i.weather.precipitation_run_mm ?? 0).toFixed(1)} мм`,
  },
  { key: "windiest", label: "Самый ветреный старт", value: (i) => `порывы ${Math.round(i.weather.wind_gusts_ms ?? 0)} м/с` },
];

function PersonName({ row }: { row: WalrusRow }) {
  const name = surnameFirst(row.name);
  if (row.handle) {
    return <a href={`/u/${row.handle}`}>{name}</a>;
  }
  return <span>{name}</span>;
}

function WalrusTable({ rows, valueLabel, byCold }: { rows: WalrusRow[]; valueLabel: string; byCold: boolean }) {
  if (rows.length === 0) {
    return <p className="muted">Пока никого — ни одного старта при −20° и ниже.</p>;
  }
  return (
    <TableWrap className="lb-table-wrap lb-table-wrap-flat">
      <table className="data-table lb-table">
        <thead>
          <tr>
            <th>#</th>
            <th>Участник</th>
            <th>{valueLabel}</th>
            <th>{byCold ? "Стартов от −20°" : "Самый холодный"}</th>
            <th>Где и когда</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={`${row.name}-${row.place}`}>
              <td className="td-compact">{row.place}</td>
              <td>
                <PersonName row={row} /> <PlatformBadge code={row.platform_code} />
              </td>
              <td className="td-compact">
                {byCold ? (
                  <b className={`temp-${temperatureTone(row.coldest_c)}`}>{row.coldest_label}</b>
                ) : (
                  <b>{formatInt(row.count)}</b>
                )}
              </td>
              <td className="td-compact">
                {byCold ? formatInt(row.count) : <span className={`temp-${temperatureTone(row.coldest_c)}`}>{row.coldest_label}</span>}
              </td>
              <td className="muted">
                {row.coldest_location}
                {row.coldest_date ? ` · ${formatDate(row.coldest_date)}` : ""}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </TableWrap>
  );
}

function SeasonTable({ rows, title, hint }: { rows: SeasonLocationRow[]; title: string; hint: string }) {
  return (
    <div>
      <h3 className="lb-weather-lead">
        {title}
        <StatHintTooltip text={hint}>
          <span className="loc-section-title-info" aria-label="Как считается">
            ⓘ
          </span>
        </StatHintTooltip>
      </h3>
      {rows.length === 0 ? (
        <p className="muted">Недостаточно стартов с погодой.</p>
      ) : (
        <TableWrap className="lb-table-wrap lb-table-wrap-flat">
          <table className="data-table lb-table">
            <thead>
              <tr>
                <th>#</th>
                <th>Локация</th>
                <th>Медиана</th>
                <th>Разброс</th>
                <th>Стартов</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((row) => (
                <tr key={`${row.slug}-${row.place}`}>
                  <td className="td-compact">{row.place}</td>
                  <td>
                    {row.slug ? <a href={`/locations/${row.slug}`}>{row.name}</a> : row.name}{" "}
                    <PlatformBadge code={row.platform_code} />
                    {row.region && <div className="muted">{row.region}</div>}
                  </td>
                  <td className="td-compact">
                    <b className={`temp-${temperatureTone(row.median_c)}`}>{formatTemp(row.median_c)}</b>
                  </td>
                  <td className="td-compact muted">
                    {formatTemp(row.min_c)} … {formatTemp(row.max_c)}
                  </td>
                  <td className="td-compact">{formatInt(row.starts)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </TableWrap>
      )}
    </div>
  );
}

function TemperatureTable({ rows }: { rows: TemperatureBucket[] }) {
  if (rows.length === 0) {
    return <p className="muted">Недостаточно финишей с погодой.</p>;
  }
  const fastest = rows.reduce<TemperatureBucket | null>(
    (best, row) => (row.avg_finish_sec != null && (best == null || row.avg_finish_sec < (best.avg_finish_sec ?? 0)) ? row : best),
    null,
  );
  const maxFinishers = Math.max(...rows.map((row) => row.avg_finishers ?? 0), 1);
  return (
    <>
      {fastest && (
        <p className="lb-weather-lead">
          Быстрее всего страна бежит при{" "}
          <b className={`temp-${temperatureTone(fastest.from_c)}`}>{fastest.label}</b>: среднее время{" "}
          <b>{formatFinishTime(fastest.avg_finish_sec)}</b> по {formatInt(fastest.finishes)}{" "}
          {pluralFormRu(fastest.finishes, ["финишу", "финишам", "финишам"])}.
        </p>
      )}
      <TableWrap className="lb-table-wrap lb-table-wrap-flat">
        <table className="data-table lb-table">
          <thead>
            <tr>
              <th>На старте</th>
              <th title="Среднее время финиша по всем протоколам с такой погодой">Среднее время</th>
              <th title="Среднее число финишёров на одном старте">Явка</th>
              <th>Финишей</th>
              <th>Стартов</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => (
              <tr key={row.from_c} className={fastest?.from_c === row.from_c ? "lb-row-me" : undefined}>
                <td className="td-compact">
                  <b className={`temp-${temperatureTone(row.from_c)}`}>{row.label}</b>
                </td>
                <td className="td-compact">{formatFinishTime(row.avg_finish_sec)}</td>
                <td>
                  <span
                    className="lb-weather-bucket-bar"
                    style={{ width: `${Math.max(6, Math.round(((row.avg_finishers ?? 0) / maxFinishers) * 120))}px` }}
                  />{" "}
                  {row.avg_finishers != null ? formatInt(Math.round(row.avg_finishers)) : "—"}
                </td>
                <td className="td-compact">{formatInt(row.finishes)}</td>
                <td className="td-compact">{formatInt(row.starts)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </TableWrap>
    </>
  );
}

export function WeatherRatingPage() {
  const [data, setData] = useState<WeatherRatingResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [walrusView, setWalrusView] = useState<"count" | "cold">("count");

  useEffect(() => {
    let cancelled = false;
    getWeatherRating()
      .then((payload) => {
        if (!cancelled) {
          setData(payload);
        }
      })
      .catch((err: Error) => {
        if (!cancelled) {
          setError(err.message);
        }
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const extremes = data
    ? EXTREME_TITLES.flatMap(({ key, label, value }) => {
        const item = data.extremes[key];
        return item ? [{ key, label, value: value(item), item }] : [];
      })
    : [];

  return (
    <PortalSectionShell sidebar={{ active: "ratings" }}>
      <header className="loc-header loc-wide-page">
        <p className="muted loc-header-breadcrumb">
          <a href="/ratings">← Рейтинги</a> / Погода
        </p>
        <h1>Погода на стартах</h1>
        <p className="protocol-subtitle">
          Моржи, суровые локации и температура против результата — по архиву погоды в точке старта, в час
          старта. Дождём считаем от 1 мм за старт, «моржовым» — старт при −20° и ниже.
        </p>
      </header>
      <RatingsLoginBanner />
      {error && (
        <div className="card error">
          <p>{error}</p>
        </div>
      )}
      {!data && !error && <p className="muted">Считаем…</p>}
      {data && (
        <>
          {extremes.length > 0 && (
            <div className="lb-weather-extremes">
              {extremes.map(({ key, label, value, item }) => (
                <div className="lb-weather-extreme" key={key}>
                  <div className="lb-weather-extreme-label">{label}</div>
                  <div
                    className={`lb-weather-extreme-value temp-${
                      key === "coldest" || key === "hottest" ? temperatureTone(item.weather.temperature_c) : "none"
                    }`}
                  >
                    <span aria-hidden>{item.weather.icon}</span> {value}
                  </div>
                  <div className="lb-weather-extreme-sub">
                    {item.location_slug ? (
                      <a href={`/locations/${item.location_slug}`}>{item.location_name}</a>
                    ) : (
                      item.location_name
                    )}{" "}
                    <PlatformBadge code={item.platform_code} /> · {formatDate(item.weather.date)}
                    {item.finishers != null && ` · ${pluralizeRu(item.finishers, ["финишёр", "финишёра", "финишёров"])}`}
                  </div>
                  <div className="lb-weather-extreme-sub muted">{item.weather.summary}</div>
                </div>
              ))}
            </div>
          )}

          <section className="card lb-weather-section">
            <h2>🥶 Моржи</h2>
            <p className="lb-weather-lead muted">
              {formatInt(data.walruses.participants)}{" "}
              {pluralFormRu(data.walruses.participants, ["человек выходил", "человека выходили", "человек выходили"])}{" "}
              на старт при {formatTemp(data.walruses.threshold_c)} и ниже.
            </p>
            <div className="aj-tabs" role="tablist">
              <button
                type="button"
                role="tab"
                className={`aj-tab${walrusView === "count" ? " aj-tab-active" : ""}`}
                aria-selected={walrusView === "count"}
                onClick={() => setWalrusView("count")}
              >
                По числу стартов
              </button>
              <button
                type="button"
                role="tab"
                className={`aj-tab${walrusView === "cold" ? " aj-tab-active" : ""}`}
                aria-selected={walrusView === "cold"}
                onClick={() => setWalrusView("cold")}
              >
                Самый холодный финиш
              </button>
            </div>
            {walrusView === "count" ? (
              <WalrusTable rows={data.walruses.by_count} valueLabel="Стартов от −20°" byCold={false} />
            ) : (
              <WalrusTable rows={data.walruses.coldest} valueLabel="Температура" byCold />
            )}
          </section>

          <section className="card lb-weather-section">
            <h2>🏔️ Суровые локации</h2>
            <div className="lb-weather-tables">
              <SeasonTable
                rows={data.locations.coldest_winter}
                title="Самые морозные зимой"
                hint="Медиана температуры в час старта по стартам декабря–февраля; нужно минимум 3 зимних старта."
              />
              <SeasonTable
                rows={data.locations.hottest_summer}
                title="Самые жаркие летом"
                hint="Медиана температуры в час старта по стартам июня–августа; нужно минимум 3 летних старта."
              />
            </div>
          </section>

          <section className="card lb-weather-section">
            <h2>🌡️ Температура и результат</h2>
            <TemperatureTable rows={data.temperature} />
          </section>
        </>
      )}
    </PortalSectionShell>
  );
}
