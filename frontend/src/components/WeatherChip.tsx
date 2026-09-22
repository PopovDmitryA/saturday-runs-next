import { useState } from "react";
import { DetailModal } from "./DetailModal";
import { RAIN_KIND_LABELS, formatTemp, temperatureTone, type WeatherBrief } from "../lib/weather";

// Погода одной строкой в таблице: значок и градусы, по клику — окно с
// подробностями (ощущается, ветер, дождь за час забега, снег, восход) и
// ссылкой на страницу погоды локации. Одна и та же плитка во всех таблицах.

type Props = {
  weather: WeatherBrief | null | undefined;
  /** Куда вести ссылку «погода на этой локации» (slug локации). */
  locationSlug?: string | null;
  locationName?: string | null;
  /** Полная строка вместо «значок + градусы» (шапка протокола, последняя суббота). */
  full?: boolean;
  /** Только значок и словесная погода, без градусов: рядом с крупной цифрой. */
  labelOnly?: boolean;
  className?: string;
};

function Row({ label, value }: { label: string; value: string | null | undefined }) {
  if (value == null || value === "") {
    return null;
  }
  return (
    <div className="weather-detail-row">
      <span className="weather-detail-label">{label}</span>
      <span className="weather-detail-value">{value}</span>
    </div>
  );
}

function ms(value: number | null): string | null {
  return value == null ? null : `${Math.round(value)} м/с`;
}

function mm(value: number | null): string | null {
  return value == null ? null : `${value.toFixed(1)} мм`;
}

export function WeatherChip({ weather, locationSlug, locationName, full = false, labelOnly = false, className }: Props) {
  const [open, setOpen] = useState(false);
  if (!weather) {
    return <span className="muted">—</span>;
  }
  const tone = temperatureTone(weather.temperature_c);
  const label = full
    ? weather.summary
    : labelOnly
      ? weather.label || weather.summary
      : `${weather.icon} ${formatTemp(weather.temperature_c)}`;
  const dayRange =
    weather.day_temperature_min_c != null && weather.day_temperature_max_c != null
      ? `${formatTemp(weather.day_temperature_min_c)} … ${formatTemp(weather.day_temperature_max_c)}`
      : null;
  return (
    <>
      <button
        type="button"
        className={`weather-chip weather-chip-${tone}${full ? " weather-chip-full" : ""}${className ? ` ${className}` : ""}`}
        onClick={(event) => {
          event.stopPropagation();
          setOpen(true);
        }}
        title={weather.is_preliminary ? "Предварительно, архив уточнит в понедельник" : "Подробнее о погоде"}
        data-tap-tooltip="off"
      >
        {(full || labelOnly) && <span aria-hidden>{weather.icon} </span>}
        {label}
        {weather.is_preliminary && <span className="weather-chip-prelim" aria-label="предварительно">*</span>}
      </button>
      <DetailModal
        open={open}
        title={`Погода на старте · ${weather.date.split("-").reverse().join(".")}`}
        onClose={() => setOpen(false)}
        width="narrow"
      >
        <div className="weather-detail">
          <div className={`weather-detail-head temp-${tone}`}>
            <span aria-hidden>{weather.icon}</span> {formatTemp(weather.temperature_c)}
            <span className="weather-detail-head-label">{weather.label}</span>
          </div>
          <Row label="Время старта" value={weather.start_time_local} />
          <Row label="Ощущается" value={formatTemp(weather.apparent_temperature_c)} />
          <Row label="За день" value={dayRange} />
          <Row label="Ветер" value={ms(weather.wind_speed_ms)} />
          <Row label="Порывы" value={ms(weather.wind_gusts_ms)} />
          <Row label="Влажность" value={weather.humidity_pct != null ? `${weather.humidity_pct}%` : null} />
          <Row
            label="Дождь за час забега"
            value={
              weather.precipitation_run_mm != null
                ? `${mm(weather.precipitation_run_mm)} · ${RAIN_KIND_LABELS[weather.rain_kind]}`
                : null
            }
          />
          <Row label="Дождь за три часа до старта" value={mm(weather.precipitation_before_mm)} />
          <Row label="Осадки за сутки" value={mm(weather.day_precipitation_mm)} />
          <Row
            label="Снег на трассе"
            value={weather.snow_depth_cm != null && weather.snow_depth_cm >= 1 ? `${Math.round(weather.snow_depth_cm)} см` : null}
          />
          <Row label="Восход" value={weather.sunrise_local} />
          <p className="muted weather-detail-note">
            Данные из архива погоды Open-Meteo: координаты локации, время старта. Температура и ветер
            надёжны, а осадки модель размазывает по площади — летний ливень из соседнего квартала она
            может засчитать и вашему парку.
            {weather.is_preliminary && " Строка предварительная, архив уточнит её в понедельник."}
          </p>
          {locationSlug && (
            <p className="weather-detail-link">
              <a href={`/locations/${encodeURIComponent(locationSlug)}/weather`}>
                Погода на стартах{locationName ? ` · ${locationName}` : ""} →
              </a>
            </p>
          )}
        </div>
      </DetailModal>
    </>
  );
}
