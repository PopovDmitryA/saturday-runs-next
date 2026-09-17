import { formatDate } from "../lib/format";
import { temperatureTone, type WeatherForecast } from "../lib/weather";

// Прогноз на ближайший старт: крупная строка погоды и советы «во что одеться».
// Обновляется раз в сутки; насколько ему верить, подписано горизонтом.

export function WeatherForecastCard({
  forecast,
  compact = false,
}: {
  forecast: WeatherForecast | null | undefined;
  compact?: boolean;
}) {
  if (!forecast) {
    return null;
  }
  const tone = temperatureTone(forecast.temperature_c);
  const chance = forecast.precipitation_probability_pct ?? 0;
  return (
    <div className={`weather-forecast${compact ? " weather-forecast-compact" : ""}`}>
      <div className="weather-forecast-head">
        <span className="weather-forecast-title">Прогноз на старт · {formatDate(forecast.target_date)}</span>
        {forecast.start_time_local && (
          <span className="muted"> · {forecast.start_time_local}</span>
        )}
      </div>
      <div className={`weather-forecast-main temp-${tone}`}>
        <span aria-hidden>{forecast.icon}</span> {forecast.summary}
      </div>
      {chance > 0 && (
        <div className="weather-forecast-chance muted">
          Вероятность осадков {chance}%
          {forecast.precipitation_mm != null && forecast.precipitation_mm > 0
            ? ` · ${forecast.precipitation_mm.toFixed(1)} мм за час вокруг старта`
            : ""}
        </div>
      )}
      <ul className="weather-forecast-advice">
        {forecast.advice.map((item) => (
          <li key={item}>{item}</li>
        ))}
      </ul>
      <div className="weather-forecast-note muted">{forecast.horizon_note}</div>
    </div>
  );
}
