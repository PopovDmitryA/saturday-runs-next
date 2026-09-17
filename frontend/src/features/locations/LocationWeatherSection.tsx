import { useCachedResource } from "../../hooks/useCachedResource";
import { StatHintTooltip } from "../../components/StatHintTooltip";
import { WeatherChip } from "../../components/WeatherChip";
import { WeatherForecastCard } from "../../components/WeatherForecastCard";
import { getLocationWeather, type LocationWeatherMonth } from "../../lib/api";
import { formatDate } from "../../lib/format";
import { formatTemp, temperatureTone } from "../../lib/weather";

// Короткий блок «Погода на стартах» на странице локации: последняя суббота,
// «год назад», лента двенадцати месяцев одной строкой — и приглашение на
// отдельную страницу с графиками и рекордами (/locations/{slug}/weather).

const MONTH_SHORT = ["Я", "Ф", "М", "А", "М", "И", "И", "А", "С", "О", "Н", "Д"];

export function MonthStrip({ months, slug }: { months: LocationWeatherMonth[]; slug: string }) {
  return (
    <a className="loc-weather-strip" href={`/locations/${encodeURIComponent(slug)}/weather`} title="Погода по месяцам">
      {months.map((month, index) => {
        const tone = temperatureTone(month.temperature_median_c);
        const empty = month.basis === "none";
        return (
          <span
            key={month.month}
            className={`loc-weather-strip-cell temp-bg-${empty ? "none" : tone}${empty ? " loc-weather-strip-empty" : ""}`}
            title={
              empty
                ? `${month.label}: данных нет`
                : `${month.label}: на старте ${formatTemp(month.temperature_median_c)}, дождь ${Math.round(
                    (month.rain_share ?? 0) * 100,
                  )}%, снег ${Math.round((month.snow_share ?? 0) * 100)}%`
            }
          >
            <span className="loc-weather-strip-month">{MONTH_SHORT[index]}</span>
            <span className="loc-weather-strip-temp">{empty ? "·" : formatTemp(month.temperature_median_c)}</span>
          </span>
        );
      })}
    </a>
  );
}

export function LocationWeatherSection({ slug }: { slug: string }) {
  const { data, error } = useCachedResource(
    `locations:weather:${slug}`,
    () => getLocationWeather(slug),
    [slug],
    { errorText: "Не удалось загрузить погоду" },
  );

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
  // Прогноз показываем, даже если архива нет: новой площадке он тоже полезен.
  if (!data.has_data) {
    return data.forecast ? (
      <section className="card loc-section loc-weather loc-weather-compact">
        <h2 className="section-title">Погода на стартах</h2>
        <WeatherForecastCard forecast={data.forecast} />
      </section>
    ) : null;
  }
  const detailsHref = `/locations/${encodeURIComponent(slug)}/weather`;
  const yearAgo = data.years_ago[0] ?? null;
  const coldest = data.records.coldest;
  const hottest = data.records.hottest;

  return (
    <section className="card loc-section loc-weather loc-weather-compact">
      <div className="loc-section-head">
        <h2 className="section-title">
          Погода на стартах
          <StatHintTooltip text="Погода из архива Open-Meteo: координаты локации, время старта. По месяцам — медиана по реальным стартам локации.">
            <span className="loc-section-title-info" aria-label="Как считается">
              ⓘ
            </span>
          </StatHintTooltip>
        </h2>
        <a className="loc-stat-details-link" href={detailsHref}>
          Подробнее →
        </a>
      </div>

      <WeatherForecastCard forecast={data.forecast} />

      <div className="loc-weather-now loc-weather-now-compact">
        {data.latest && (
          <div className="loc-weather-now-item loc-weather-now-latest">
            <div className="loc-weather-now-label">
              {data.latest.start ? "Последний старт" : "Последняя суббота"} · {formatDate(data.latest.weather.date)}
            </div>
            <div className="loc-weather-now-value">
              <WeatherChip weather={data.latest.weather} locationSlug={slug} locationName={data.name} full />
            </div>
          </div>
        )}
        {yearAgo && (
          <div className="loc-weather-now-item">
            <div className="loc-weather-now-label">
              Год назад · {formatDate(yearAgo.weather.date)}
              {!yearAgo.start && <span className="muted"> · старта не было</span>}
            </div>
            <div className="loc-weather-now-value">
              <WeatherChip weather={yearAgo.weather} locationSlug={slug} locationName={data.name} full />
            </div>
          </div>
        )}
      </div>

      <MonthStrip months={data.months} slug={slug} />

      {(coldest || hottest) && (
        <p className="loc-weather-compact-records muted">
          {coldest && (
            <>
              Самый холодный старт{" "}
              <b className={`temp-${temperatureTone(coldest.weather.temperature_c)}`}>
                {formatTemp(coldest.weather.temperature_c)}
              </b>{" "}
              ({formatDate(coldest.weather.date)})
            </>
          )}
          {coldest && hottest && " · "}
          {hottest && (
            <>
              самый жаркий{" "}
              <b className={`temp-${temperatureTone(hottest.weather.temperature_c)}`}>
                {formatTemp(hottest.weather.temperature_c)}
              </b>{" "}
              ({formatDate(hottest.weather.date)})
            </>
          )}
          {" · "}
          <a href={detailsHref}>рекорды, месяцы и явка по погоде →</a>
        </p>
      )}
    </section>
  );
}
