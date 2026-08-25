import { useEffect, useMemo, useState } from "react";
import { PlatformBadge } from "../../components/PlatformBadge";
import { trackCtaClick, trackHomeLinkClick, useFunnelHomeView } from "../../lib/abTest";
import { cabinetTabHref, PORTAL_LOGIN_HREF } from "../../lib/portalRoutes";
import { useOptionalUser } from "../../lib/useOptionalUser";
import { CountUpNumber } from "./CountUpNumber";
import { PortalBlogSection } from "./PortalBlogSection";
import { PortalHomeAnchors } from "./PortalHomeAnchors";
import { PortalFooter } from "./PortalFooter";
import { PortalGeoMap } from "./PortalGeoMap";
import { PortalHeader } from "./PortalHeader";
import { PLATFORM_CHART_META, PortalTrendChart, type TrendPoint } from "./PortalTrendChart";
import { PortalTeaserCard } from "./PortalTeaser";
import {
  fetchPortalHome,
  fetchPortalMe,
  PortalHomeError,
  type PortalAttendanceTopRow,
  type PortalFastestRow,
  type PortalHomeResponse,
  type PortalMe,
} from "./portalTypes";
import "./portal.css";
import { COUNT_FORMS, formatInt, pluralFormRu, pluralizeRu } from "../../lib/format";

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

/**
 * Название локации ссылкой на её страницу.
 *
 * Главная — самая посещаемая страница сайта, и до этого она была тупиком:
 * десятки названий локаций и имён рекордсменов текстом, без единого перехода
 * вглубь. Слага может не быть (старый кэш ответа) — тогда остаётся текст.
 */
function LocationLink({ name, slug }: { name: string; slug?: string | null }) {
  if (!slug) {
    return <>{name}</>;
  }
  return (
    <a
      className="portal-inline-link"
      href={`/locations/${encodeURIComponent(slug)}`}
      title="Открыть страницу локации"
      onClick={() => trackHomeLinkClick("location", slug)}
    >
      {name}
    </a>
  );
}

/** Имя участника ссылкой на его профиль — если он привязал систему на сайте. */
function RunnerLink({ name, handle }: { name: string | null; handle?: string | null }) {
  const label = name?.trim() || "Участник";
  if (!handle) {
    return <>{label}</>;
  }
  return (
    <a
      className="portal-inline-link"
      href={`/users/${encodeURIComponent(handle)}`}
      title="Открыть профиль участника"
      onClick={() => trackHomeLinkClick("runner", handle)}
    >
      {label}
    </a>
  );
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
              <RunnerLink name={row.runner_name} handle={row.runner_handle} /> ·{" "}
              <LocationLink name={row.location_name} slug={row.location_slug} />,{" "}
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
          <span className="portal-top-name">
            <LocationLink name={row.location_name} slug={row.location_slug} />
            {/* Город второй строкой: список сплошь из названий парков ничего не
                говорит о географии. */}
            {row.location_city && (
              <span className="portal-top-city">{row.location_city}</span>
            )}
          </span>
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

type ChartTabKey = "finishes" | "newcomers" | "records" | "locations";

export function PortalHomePage() {
  // CTA «Найти себя в статистике» раньше вёл на /login безусловно: залогиненный
  // на секунду видел вход, пока тот сам проверял сессию и уводил в кабинет.
  // Кэшированная (см. useOptionalUser) сессия сразу даёт правильный адрес.
  const optionalUser = useOptionalUser();
  const ctaHref =
    optionalUser != null ? cabinetTabHref(optionalUser, "dashboard") : PORTAL_LOGIN_HREF;

  // Знаменатель воронки регистрации — до загрузки данных, см. useFunnelHomeView.
  useFunnelHomeView();

  const [data, setData] = useState<PortalHomeResponse | null>(null);
  // Блок блога скрывается, когда постов нет, — оглавление это учитывает.
  const [hasBlogPosts, setHasBlogPosts] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // Личная сводка для залогиненного (Т4). Грузится отдельным запросом ПОСЛЕ
  // главной: анонимный путь — самый массовый — ждать её не должен.
  const [me, setMe] = useState<PortalMe | null>(null);
  // По умолчанию «Последний год» — его цифры можно примерить на себя,
  // вечные итоги — абстракция.
  const [period, setPeriod] = useState<"all" | "year">("year");
  const [chartTab, setChartTab] = useState<ChartTabKey>("finishes");

  useEffect(() => {
    let cancelled = false;
    let timer: number | undefined;
    const load = () => {
      fetchPortalHome()
        .then((response) => {
          if (!cancelled) {
            setData(response);
            setError(null);
          }
        })
        .catch((err) => {
          if (cancelled) {
            return;
          }
          // 502/503/504 или сетевой сбой — стек перезапускается (обычно деплой):
          // говорим об этом по-человечески и пробуем снова сами.
          const status = err instanceof PortalHomeError ? err.status : null;
          const transient = status === null || status === 502 || status === 503 || status === 504;
          if (transient) {
            setError("Сайт как раз обновляется — статистика появится через минуту. Страница попробует ещё раз сама.");
            timer = window.setTimeout(load, 10_000);
          } else {
            setError(err instanceof Error ? err.message : "Не удалось загрузить данные");
          }
        });
    };
    load();
    return () => {
      cancelled = true;
      if (timer !== undefined) {
        window.clearTimeout(timer);
      }
    };
  }, []);

  // Личная сводка — только залогиненному и только когда сессия уже известна.
  // Молча гасим ошибку: не загрузилось — человек просто увидит обычную главную,
  // ронять её из-за необязательного блока нельзя.
  const viewerId = optionalUser?.id ?? null;
  useEffect(() => {
    if (viewerId === null) {
      setMe(null);
      return;
    }
    let cancelled = false;
    fetchPortalMe()
      .then((response) => {
        if (!cancelled) {
          setMe(response);
        }
      })
      .catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, [viewerId]);

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

  const personalRecordsProjection = useMemo<TrendPoint | null>(() => {
    if (!data?.personal_records_year_projection || period !== "all") {
      return null;
    }
    const projection = data.personal_records_year_projection;
    return {
      label: `${projection.year} (оценка)`,
      platforms: projection.platforms,
    };
  }, [data, period]);

  const personalRecordsPoints = useMemo<TrendPoint[]>(() => {
    if (!data) {
      return [];
    }
    if (period === "all") {
      return data.personal_records_by_year.map((point) => ({
        label: String(point.year),
        platforms: point.platforms,
      }));
    }
    return data.personal_records_by_week.map((point) => ({
      label: weekLabel(point.week_start),
      platforms: point.platforms,
    }));
  }, [data, period]);

  // Четыре показателя живут в ОДНОЙ панели с вкладками: раньше это были четыре
  // одинаковых графика подряд, которые занимали почти весь экран главной.
  const chartTabs = useMemo(() => {
    const weekly = period === "year";
    return [
      {
        key: "finishes" as ChartTabKey,
        label: "Финиши",
        title: "Финиши по системам",
        sub: weekly
          ? "Последние 52 недели — наведите на график, чтобы увидеть детали"
          : "Сколько финишей набрала каждая система в каждом году",
        points: chartPoints,
        projection: chartProjection,
        totalLabel: "Всего" as string | undefined,
      },
      {
        key: "newcomers" as ChartTabKey,
        label: "Новички",
        title: "Новички по системам",
        sub: weekly
          ? "Первые старты по неделям за последние 52 недели"
          : "Сколько людей пробежали свой первый старт в каждом году",
        points: newcomersPoints,
        projection: newcomersProjection,
        totalLabel: "Всего" as string | undefined,
      },
      {
        key: "records" as ChartTabKey,
        label: "Рекорды",
        title: "Личные рекорды по системам",
        sub: weekly
          ? "Обновления личных рекордов по неделям за последние 52 недели"
          : "Сколько раз участники улучшали свой лучший результат в каждой системе",
        points: personalRecordsPoints,
        projection: personalRecordsProjection,
        totalLabel: "Всего" as string | undefined,
      },
      {
        key: "locations" as ChartTabKey,
        label: "Локации",
        title: "Локации по системам",
        sub: weekly
          ? "Сколько локаций стартовали в каждую из 52 недель"
          : "Сколько локаций проводили старты в каждом году",
        points: locationsPoints,
        projection: null,
        totalLabel: undefined as string | undefined,
      },
    ];
  }, [
    period,
    chartPoints,
    chartProjection,
    newcomersPoints,
    newcomersProjection,
    personalRecordsPoints,
    personalRecordsProjection,
    locationsPoints,
  ]);

  const activeChart = chartTabs.find((tab) => tab.key === chartTab) ?? chartTabs[0];

  const hero = data ? (period === "year" && data.hero_last_year ? data.hero_last_year : data.hero) : null;

  // Т1 (вариант B): блок последней недели поднимается выше вечной статистики,
  // поэтому обе крупные секции собраны в константы и рендерятся в нужном порядке.
  const scopeSection = data ? (
    <>
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
                  <b className="num">
                    <CountUpNumber value={hero.finishes_total} format={formatInt} />
                  </b>
                  <span>{pluralFormRu(hero.finishes_total, COUNT_FORMS.finishes)}</span>
                </div>
                <div className="portal-count">
                  <b className="num">
                    <CountUpNumber value={hero.participants_total} format={formatInt} />
                  </b>
                  <span>{pluralFormRu(hero.participants_total, COUNT_FORMS.participants)}</span>
                </div>
                <div className="portal-count">
                  <b className="num">
                    <CountUpNumber value={hero.locations_total} format={formatInt} />
                  </b>
                  <span>{pluralFormRu(hero.locations_total, COUNT_FORMS.locations)}</span>
                </div>
                <div className="portal-count">
                  <b className="num">
                    <CountUpNumber value={hero.starts_total} format={formatInt} />
                  </b>
                  <span>{pluralFormRu(hero.starts_total, COUNT_FORMS.events)}</span>
                </div>
              </section>
            )}

            <section className="portal-panel portal-chart-panel">
              <div className="portal-panel-head portal-chart-panel-head">
                <div>
                  <h2>{activeChart.title}</h2>
                  <p className="portal-panel-sub">{activeChart.sub}</p>
                  {activeChart.projection && (
                    <p className="portal-panel-sub">
                      {activeChart.projection.label.replace(" (оценка)", "")} год ещё не закончился —
                      пунктир прогнозирует итог при текущем темпе
                    </p>
                  )}
                </div>
                <div className="portal-chart-tabs" role="tablist" aria-label="Показатель на графике">
                  {chartTabs.map((tab) => (
                    <button
                      key={tab.key}
                      type="button"
                      role="tab"
                      aria-selected={tab.key === activeChart.key}
                      className={tab.key === activeChart.key ? "active" : ""}
                      onClick={() => setChartTab(tab.key)}
                    >
                      {tab.label}
                    </button>
                  ))}
                </div>
              </div>
              <PortalTrendChart
                key={activeChart.key}
                points={activeChart.points}
                totalLabel={activeChart.totalLabel}
                showGrowth={period === "all"}
                projection={activeChart.projection}
              />
            </section>
            </section>
    </>
  ) : null;

  const weekSection = data ? (
    <>
            <section id="week" className="portal-week-scope" aria-label="Итоги последней недели">
            <div className="portal-week-heading">Последняя беговая неделя</div>

            {data.pulse && (
              <div className="portal-week-sticky">
                <section className="portal-pulse" aria-label="Последняя суббота">
                  <span className="portal-pulse-title">
                    <i />
                    {formatDateLong(data.pulse.event_date)}
                  </span>
                  <span className="portal-pulse-metric">
                    <b className="num">
                      <CountUpNumber value={data.pulse.starts} format={formatInt} />
                    </b>{" "}
                    {pluralFormRu(data.pulse.starts, COUNT_FORMS.events)}
                  </span>
                  <span className="portal-pulse-metric">
                    <b className="num">
                      <CountUpNumber value={data.pulse.finishes} format={formatInt} />
                    </b>{" "}
                    {pluralFormRu(data.pulse.finishes, COUNT_FORMS.finishes)}
                  </span>
                  <span className="portal-pulse-metric">
                    <b className="num">
                      <CountUpNumber value={data.pulse.newcomers} format={formatInt} />
                    </b>{" "}
                    {pluralFormRu(data.pulse.newcomers, COUNT_FORMS.newcomers)}
                  </span>
                  <span className="portal-pulse-metric">
                    <b className="num">
                      <CountUpNumber value={data.pulse.volunteers} format={formatInt} />
                    </b>{" "}
                    {pluralFormRu(data.pulse.volunteers, COUNT_FORMS.volunteers)}
                  </span>
                  <span className="portal-pulse-metric">
                    <b className="num">
                      <CountUpNumber value={data.pulse.personal_records} format={formatInt} />
                    </b>{" "}
                    {pluralFormRu(data.pulse.personal_records, COUNT_FORMS.prs)}
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
                      Локации, собравшие больше финишёров, чем когда-либо, и открытия новых площадок
                    </p>
                  </div>
                </div>
                {!data.week_records || data.week_records.attendance.length === 0 ? (
                  <p className="portal-empty-note">
                    На этой неделе рекордов посещаемости и открытий нет.
                  </p>
                ) : (
                  <ul className="portal-record-list">
                    {data.week_records.attendance.map((record) => (
                      <li
                        className="portal-record-row"
                        key={`${record.location_name}-${record.event_date}`}
                      >
                        <span
                          className={`portal-record-icon${record.is_debut ? " is-debut" : ""}`}
                          title={record.is_debut ? "Открытие площадки" : "Рекорд посещаемости"}
                        >
                          {record.is_debut ? "🎉" : "🏆"}
                        </span>
                        <span className="portal-record-main">
                          <b>
                            <LocationLink
                              name={record.location_name}
                              slug={record.location_slug}
                            />
                            {record.is_debut && (
                              <>
                                {" "}
                                <span className="portal-record-tag">открытие</span>
                              </>
                            )}
                          </b>
                          {/* Город перед датой: по одному названию парка не
                              понять, о каком конце страны речь. */}
                          <span>
                            {record.location_city ? `${record.location_city} · ` : ""}
                            {formatDateShort(record.event_date)}
                          </span>
                        </span>
                        <PlatformBadge code={record.platform_code} />
                        <span className="portal-record-value">
                          <b className="num">
                            {formatInt(record.finishers)}
                            {!record.is_debut && (
                              <span className="portal-delta up">
                                ↑ +{formatInt(record.finishers - record.previous_record)}
                              </span>
                            )}
                          </b>
                          <span>
                            {record.is_debut ? (
                              "первый старт"
                            ) : (
                              <>
                                было {formatInt(record.previous_record)}
                                {record.previous_record_date &&
                                  ` · ${formatDateCompact(record.previous_record_date)}`}
                              </>
                            )}
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
                          <span className="portal-bar-name">
                            <LocationLink name={row.location_name} slug={row.location_slug} />
                          </span>
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
                          <RunnerLink name={record.runner_name} handle={record.runner_handle} />{" "}
                          · <LocationLink name={record.location_name} slug={record.location_slug} />
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
                        {record.is_debut ? (
                          <span>первый рекорд трассы</span>
                        ) : (
                          record.previous_display && (
                            <span>
                              было {record.previous_display}
                              {record.previous_record_date &&
                                ` · ${formatDateCompact(record.previous_record_date)}`}
                            </span>
                          )
                        )}
                      </span>
                    </li>
                  ))}
                </ul>
              </section>
            )}
            </section>
    </>
  ) : null;


  return (
    <>
      <PortalHeader />
      <main className="portal-home">
        {!data && !error && <p className="portal-loading">Загрузка статистики…</p>}
        {error && <p className="portal-error">{error}</p>}

        {data && (
          <>
            {/* Залогиненному главная показывает не приглашение найти себя (он уже
                нашёл), а его собственную последнюю субботу — см. Т4 и
                portal_me_service. Анонимный экран не тронут. */}
            {optionalUser != null ? (
              <section className="portal-hero portal-hero-personal">
                {me?.linked === false ? (
                  <>
                    <p className="portal-eyebrow">Остался один шаг</p>
                    <h1>Аккаунт есть — а статистики пока нет</h1>
                    <p className="portal-hero-lead">
                      Укажите свой ID в 5 вёрстах, S95, parkrun или RunPark — и мы посчитаем
                      рекорды, серии суббот, карту визитов и встречи.
                    </p>
                  </>
                ) : me?.last_run ? (
                  <>
                    <p className="portal-eyebrow">Ваша последняя пробежка</p>
                    <h1>
                      <span className="portal-hero-time num">
                        {me.last_run.finish_time_display}
                      </span>
                      <span className="portal-hero-place">{me.last_run.location_name}</span>
                    </h1>
                    <p className="portal-hero-lead">
                      {formatDateShort(me.last_run.event_date)}
                      {(me.last_run.is_global_pr || me.last_run.is_pr) && (
                        <span className="portal-hero-pr">
                          {me.last_run.is_global_pr ? "личный рекорд" : "рекорд платформы"}
                        </span>
                      )}
                      {me.saturday_streak > 1 && (
                        <span className="portal-hero-streak">
                          {me.saturday_streak}{" "}
                          {plural(me.saturday_streak, "суббота", "субботы", "суббот")} подряд
                        </span>
                      )}
                    </p>
                  </>
                ) : (
                  <>
                    <p className="portal-eyebrow">Суббота · утро · парк · 5 км</p>
                    <h1>С возвращением</h1>
                    <p className="portal-hero-lead">
                      Рекорды, серии суббот, карта визитов и встречи — всё уже посчитано и ждёт
                      в кабинете.
                    </p>
                  </>
                )}
                <div className="portal-hero-cta">
                  <a className="btn primary" href={ctaHref} onClick={() => trackCtaClick("hero")}>
                    {me?.linked === false ? "Привязать профиль" : "Открыть кабинет"}
                  </a>
                </div>
              </section>
            ) : (
              <section className="portal-hero">
                <p className="portal-eyebrow">Суббота · утро · парк · 5 км</p>
                {/* Заголовок про посетителя, а не про сайт. */}
                <h1>Вся ваша беговая история — от первого старта до прошлой субботы</h1>
                <p className="portal-hero-lead">
                  5 вёрст, S95, parkrun и RunPark: рекорды, серии суббот, карта визитов и встречи —
                  уже посчитаны и ждут вас.
                </p>
                {/* CTA в верхней трети: главный источник регистраций по итогам АБ-теста. */}
                <div className="portal-hero-cta">
                  <a className="btn primary" href={ctaHref} onClick={() => trackCtaClick("hero")}>
                    Найти себя в статистике
                  </a>
                  {data.registered_parks > 0 && (
                    <span className="portal-hero-proof">
                      Участники из {formatInt(data.registered_parks)}{" "}
                      {plural(data.registered_parks, "парка", "парков", "парков")} уже нашли здесь
                      свою статистику
                    </span>
                  )}
                </div>
              </section>
            )}

            <PortalHomeAnchors hasBlog={hasBlogPosts} />

            {weekSection}
            {scopeSection}

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

            <PortalBlogSection onPostsLoaded={setHasBlogPosts} />

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
                          <b className="num">{pluralizeRu(row.finishers, COUNT_FORMS.finishes)}</b>
                          <span>{formatDateCompact(row.event_date)}</span>
                        </span>
                      </div>
                    </div>
                  );
                })}
              </div>
            </section>

            <section id="fastest" className="portal-panel" aria-label="Самые быстрые 5 км">
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
                  Выберите свою систему и введите ID — покажем предпросмотр вашей карточки прямо
                  сейчас, без регистрации. Полная версия — рекорды, серии суббот, карта визитов и
                  встречи — откроется после входа.
                </p>
                <div className="portal-cta-actions">
                  <a
                    className="btn primary"
                    href={ctaHref}
                    onClick={() => trackCtaClick("bottom")}
                  >
                    {optionalUser != null ? "Открыть кабинет" : "Найти себя в статистике"}
                  </a>
                </div>
              </div>

              {/* Живой предпросмотр по ID: затравка на реальных данных. */}
              <PortalTeaserCard />

              {/* А это уже макет самого кабинета — что откроется после входа. */}
              <div className="portal-poster" aria-hidden="true">
                <div className="portal-poster-badge">так выглядит кабинет</div>
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
                    <span>волонтёрства</span>
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
      <PortalFooter />
    </>
  );
}
