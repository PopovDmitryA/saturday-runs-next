import { formatDate } from "../lib/format";
import { temperatureTone, type WeatherForecast } from "../lib/weather";

// Прогноз на ближайший старт: крупная строка погоды и советы «во что одеться».
// Обновляется раз в сутки; насколько ему верить, подписано горизонтом.

/** «обновлено сегодня в 22:38» — короткая подпись вместо рассуждений о доверии. */
function updatedNote(iso: string | null): string {
  if (!iso) {
    return "";
  }
  const moment = new Date(iso);
  if (Number.isNaN(moment.getTime())) {
    return "";
  }
  const time = moment.toLocaleTimeString("ru-RU", { hour: "2-digit", minute: "2-digit" });
  const today = new Date();
  const sameDay = moment.toDateString() === today.toDateString();
  const yesterday = new Date(today);
  yesterday.setDate(today.getDate() - 1);
  if (sameDay) {
    return `обновлено сегодня в ${time}`;
  }
  if (moment.toDateString() === yesterday.toDateString()) {
    return `обновлено вчера в ${time}`;
  }
  return `обновлено ${moment.toLocaleDateString("ru-RU", { day: "2-digit", month: "2-digit" })} в ${time}`;
}

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
        <span className="weather-forecast-title">Прогноз на старт</span>
        <span className="muted">
          {" "}
          {formatDate(forecast.target_date)}
          {forecast.start_time_local ? ` · ${forecast.start_time_local}` : ""}
        </span>
      </div>
      <div className={`weather-forecast-main temp-${tone}`}>
        <span aria-hidden>{forecast.icon}</span> {forecast.summary}
        {/* Вероятность осадков — хвостом той же строки, мелким: отдельная
            строка ради пяти процентов растила плитку без пользы. */}
        {chance > 0 && <span className="weather-forecast-chance muted"> · осадки {chance}%</span>}
      </div>
      <ul className="weather-forecast-advice">
        {(compact ? forecast.advice.slice(0, 2) : forecast.advice).map((item) => (
          <li key={item}>{item}</li>
        ))}
      </ul>
      <div className="weather-forecast-note muted">{updatedNote(forecast.updated_at)}</div>
    </div>
  );
}
