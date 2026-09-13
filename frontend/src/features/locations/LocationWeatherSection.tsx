import { useCachedResource } from "../../hooks/useCachedResource";
import { PlatformBadge } from "../../components/PlatformBadge";
import { StatHintTooltip } from "../../components/StatHintTooltip";
import { TableWrap } from "../../components/tableUx/TableWrap";
import { useNarrowViewport } from "../../components/tableUx/useNarrowViewport";
import { getLocationWeather, type LocationWeatherMonth } from "../../lib/api";
import { formatDate, formatInt, pluralizeRu } from "../../lib/format";
import { formatTemp, temperatureTone, type WeatherRecord } from "../../lib/weather";

// Блок «Погода на стартах»: климат по месяцам из реальных стартов площадки,
// рекорды (самый холодный, жаркий, мокрый, ветреный, снежный старт), «год
// назад в эту субботу» и явка по погоде. Данные — архив Open-Meteo в точке
// старта, см. start_weather_service.

const RECORD_TITLES: Array<{ key: string; label: string; value: (record: WeatherRecord) => string }> = [
  { key: "coldest", label: "Самый холодный", value: (r) => formatTemp(r.weather.temperature_c) },
  { key: "hottest", label: "Самый жаркий", value: (r) => formatTemp(r.weather.temperature_c) },
  {
    key: "wettest",
    label: "Самый мокрый",
    value: (r) => `${(r.weather.precipitation_run_mm ?? 0).toFixed(1)} мм`,
  },
  {
    key: "windiest",
    label: "Самый ветреный",
    value: (r) => `${Math.round(r.weather.wind_gusts_ms ?? 0)} м/с`,
  },
  { key: "snowiest", label: "Самый снежный", value: (r) => `${Math.round(r.weather.snow_depth_cm ?? 0)} см` },
];

function percent(share: number | null): string {
  if (share == null) {
    return "—";
  }
  return `${Math.round(share * 100)}%`;
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

function MonthRow({ month }: { month: LocationWeatherMonth }) {
  if (month.basis === "none") {
    return (
      <tr className="loc-weather-row-empty">
        <td className="loc-weather-month">{month.label}</td>
        <td colSpan={5} className="muted">
          данных нет
        </td>
      </tr>
    );
  }
  const tone = temperatureTone(month.temperature_median_c);
  return (
    <tr className={month.basis === "saturdays" ? "loc-weather-row-saturdays" : undefined}>
      <td className="loc-weather-month">
        {month.label}
        {month.basis === "saturdays" && (
          <StatHintTooltip text="В этом месяце стартов ещё не было — цифры по субботам площадки без старта">
            <span className="loc-weather-basis-note"> · без стартов</span>
          </StatHintTooltip>
        )}
      </td>
      <td className={`loc-weather-temp temp-${tone}`}>{formatTemp(month.temperature_median_c)}</td>
      <td className="muted loc-weather-range">
        {formatTemp(month.temperature_min_c)} … {formatTemp(month.temperature_max_c)}
      </td>
      <td>{percent(month.rain_share)}</td>
      <td>{percent(month.snow_share)}</td>
      <td className="muted">{month.starts > 0 ? formatInt(month.starts) : "—"}</td>
    </tr>
  );
}

function MonthCard({ month }: { month: LocationWeatherMonth }) {
  const tone = temperatureTone(month.temperature_median_c);
  return (
    <div className="rowcard loc-weather-card">
      <div className="rowcard-mid">
        <div className="rowcard-title">
          {month.label}
          {month.basis === "saturdays" && <span className="muted"> · без стартов</span>}
        </div>
        <div className="rowcard-sub">
          {month.basis === "none"
            ? "данных нет"
            : `${formatTemp(month.temperature_min_c)} … ${formatTemp(month.temperature_max_c)} · дождь ${percent(
                month.rain_share,
              )} · снег ${percent(month.snow_share)}`}
        </div>
      </div>
      <div className="rowcard-right">
        <div className={`rowcard-value temp-${tone}`}>{formatTemp(month.temperature_median_c)}</div>
        <div className="rowcard-sub">на старте</div>
      </div>
    </div>
  );
}

export function LocationWeatherSection({ slug }: { slug: string }) {
  const { data, error } = useCachedResource(
    `locations:weather:${slug}`,
    () => getLocationWeather(slug),
    [slug],
    { errorText: "Не удалось загрузить погоду" },
  );
  const narrowViewport = useNarrowViewport();

  if (error) {
    return null;
  }
  if (!data) {
    return (
      <section className="card loc-section">
        <h2 className="section-title">Погода на стартах</h2>
        <p className="muted">Загрузка…</p>
      </section>
    );
  }
  if (!data.has_data) {
    return null;
  }

  const records = RECORD_TITLES.flatMap(({ key, label, value }) => {
    const record = data.records[key];
    return record ? [{ key, label, value: value(record), record }] : [];
  });

  return (
    <section className="card loc-section loc-weather">
      <h2 className="section-title">
        Погода на стартах
        <StatHintTooltip
          text={
            "Архив погоды Open-Meteo в точке старта, снятый в час старта. Температура и ветер надёжны, " +
            "осадки сетка размазывает: дождём считаем от 1 мм в окне «час до старта — два часа после». " +
            "По месяцам — медиана по реальным стартам площадки."
          }
        >
          <span className="loc-section-title-info" aria-label="Как считается">
            ⓘ
          </span>
        </StatHintTooltip>
      </h2>

      {(data.latest || data.years_ago.length > 0) && (
        <div className="loc-weather-now">
          {data.latest && (
            <div className="loc-weather-now-item loc-weather-now-latest">
              <div className="loc-weather-now-label">
                {data.latest.start ? "Последний старт" : "Последняя суббота"} · {formatDate(data.latest.weather.date)}
                {data.latest.weather.is_preliminary && <span className="muted"> · предварительно</span>}
              </div>
              <div className="loc-weather-now-value">
                <span aria-hidden>{data.latest.weather.icon}</span> {data.latest.weather.summary}
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
                <span aria-hidden>{item.weather.icon}</span> {item.weather.summary}
              </div>
            </div>
          ))}
        </div>
      )}

      <h3 className="loc-weather-subtitle">По месяцам</h3>
      {narrowViewport ? (
        <div className="rowcards loc-weather-cards">
          {data.months.map((month) => (
            <MonthCard month={month} key={month.month} />
          ))}
        </div>
      ) : (
        <TableWrap className="loc-weather-wrap">
          <table className="data-table loc-weather-table">
            <colgroup>
              <col className="loc-weather-col-month" />
              <col className="loc-weather-col-temp" />
              <col className="loc-weather-col-range" />
              <col className="loc-weather-col-share" />
              <col className="loc-weather-col-share" />
              <col className="loc-weather-col-starts" />
            </colgroup>
            <thead>
              <tr>
                <th>Месяц</th>
                <th title="Медиана температуры в час старта">На старте</th>
                <th title="Самый холодный и самый тёплый старт месяца">Разброс</th>
                <th title="Доля стартов с дождём (от 1 мм за старт)">Дождь</th>
                <th title="Доля стартов со снегом на трассе или снегопадом">Снег</th>
                <th title="Стартов площадки в этом месяце с погодой">Стартов</th>
              </tr>
            </thead>
            <tbody>
              {data.months.map((month) => (
                <MonthRow month={month} key={month.month} />
              ))}
            </tbody>
          </table>
        </TableWrap>
      )}

      {records.length > 0 && (
        <>
          <h3 className="loc-weather-subtitle">Рекорды площадки</h3>
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
                <div className="loc-weather-record-summary muted">{record.weather.summary}</div>
              </div>
            ))}
          </div>
        </>
      )}

      {data.attendance.length > 0 && (
        <>
          <h3 className="loc-weather-subtitle">
            Явка и погода
            <StatHintTooltip text="Среднее число финишёров на стартах площадки в такую погоду. Корзины по температуре в час старта и отдельно сухие старты против дождливых.">
              <span className="loc-section-title-info" aria-label="Как считается">
                ⓘ
              </span>
            </StatHintTooltip>
          </h3>
          <div className="loc-weather-attendance">
            {data.attendance.map((item) => (
              <div className="loc-weather-attendance-item" key={item.key}>
                <div className="loc-weather-attendance-value">
                  {item.avg_finishers != null ? formatInt(Math.round(item.avg_finishers)) : "—"}
                </div>
                <div className="loc-weather-attendance-label">{item.label}</div>
                <div className="loc-weather-attendance-sub muted">
                  {pluralizeRu(item.starts, ["старт", "старта", "стартов"])}
                </div>
              </div>
            ))}
          </div>
        </>
      )}
    </section>
  );
}
