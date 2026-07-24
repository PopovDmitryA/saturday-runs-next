import { useEffect, useState, type ReactNode } from "react";
import { LocationStatusLabel } from "../../components/LocationStatusBadge";
import { PlatformBadge } from "../../components/PlatformBadge";
import { RequireAuth } from "../../components/RequireAuth";
import { StatHintTooltip } from "../../components/StatHintTooltip";
import {
  ApiError,
  getLocationLeaders,
  getLocationPage,
  type LocationCourseRecord,
  type LocationLastEvent,
  type LocationLeaders,
  type LocationPage as LocationPageData,
} from "../../lib/api";
import { formatDate, platformCodeLabel, pluralFormRu, pluralizeRu } from "../../lib/format";
import { PortalSectionShell } from "../portal/PortalSectionShell";
import { LocationFinishHistogram } from "./LocationFinishHistogram";
import { LocationMiniMap } from "./LocationMiniMap";
import { LocationRatingPrompt } from "./LocationRatingPrompt";
import { LocationRecordsModal, type RecordType } from "./LocationRecordsModal";

function StatTile({
  value,
  label,
  sub,
  badge,
  onDetails,
}: {
  value: ReactNode;
  label: string;
  sub?: ReactNode;
  badge?: { text: string; title: string };
  onDetails?: () => void;
}) {
  return (
    <div className="stat-card loc-stat-card">
      <span className="stat-value loc-stat-value">
        {value}
        {badge && (
          <StatHintTooltip text={badge.title}>
            <span className="loc-record-badge" aria-label={badge.title}>
              {badge.text}
            </span>
          </StatHintTooltip>
        )}
      </span>
      <span className="stat-label">{label}</span>
      {sub && <span className="loc-stat-sub">{sub}</span>}
      {onDetails && (
        <button type="button" className="loc-stat-details-link" onClick={onDetails}>
          подробнее
        </button>
      )}
    </div>
  );
}

function courseRecordSub(record: LocationCourseRecord): string {
  const parts: string[] = [];
  if (record.runner_name) {
    parts.push(record.runner_name);
  }
  if (record.event_date) {
    parts.push(formatDate(record.event_date));
  }
  return parts.join(" · ");
}

function DeltaHint({ deltaSec }: { deltaSec: number | null }): ReactNode {
  if (!deltaSec) {
    return null;
  }
  const increased = deltaSec > 0;
  const arrow = increased ? "↑" : "↓";
  const className = `loc-stat-delta-badge ${increased ? "loc-stat-delta-badge-better" : "loc-stat-delta-badge-worse"}`;
  return (
    <StatHintTooltip text="Изменение после последнего старта">
      <span className={className}>
        {arrow} {Math.abs(deltaSec)} сек
      </span>
    </StatHintTooltip>
  );
}

function LastEventSection({ lastEvent }: { lastEvent: LocationLastEvent }) {
  return (
    <section className="card loc-section">
      <h2 className="section-title">Последний старт</h2>
      <div className="loc-stats-grid">
        <StatTile value={formatDate(lastEvent.event_date)} label={platformCodeLabel(lastEvent.platform_code)} />
        {lastEvent.finishers !== null && <StatTile value={lastEvent.finishers} label="финишей" />}
        {lastEvent.volunteers !== null && <StatTile value={lastEvent.volunteers} label="волонтёров" />}
        {lastEvent.avg_time_sec !== null && (
          <StatTile value={stripLeadingHours(lastEvent.avg_time_display)} label="среднее время" />
        )}
        {lastEvent.best_male_time_sec !== null && (
          <StatTile value={stripLeadingHours(lastEvent.best_male_time_display)} label="лучшее · М" />
        )}
        {lastEvent.best_female_time_sec !== null && (
          <StatTile value={stripLeadingHours(lastEvent.best_female_time_display)} label="лучшее · Ж" />
        )}
      </div>
    </section>
  );
}

function PlatformTimeline({ page }: { page: LocationPageData }) {
  const withEvents = page.platforms.filter((platform) => platform.events_count > 0);
  const rest = page.platforms.filter((platform) => platform.events_count === 0);
  return (
    <ol className="loc-timeline">
      {[...withEvents, ...rest].map((platform) => (
        <li key={platform.platform_code} className="loc-timeline-item">
          <div className="loc-timeline-head">
            <PlatformBadge code={platform.platform_code} />
            {platform.is_active === true && <span className="loc-timeline-current">сейчас</span>}
          </div>
          <div className="loc-timeline-name">
            {platform.is_active === true && platform.url ? (
              <a href={platform.url} target="_blank" rel="noreferrer">
                {platform.location_name}
              </a>
            ) : (
              platform.location_name
            )}
          </div>
          <div className="loc-timeline-dates muted">
            {platform.first_event_date ? (
              <>
                {formatDate(platform.first_event_date)}
                {" — "}
                {platform.last_event_date ? formatDate(platform.last_event_date) : "…"}
                {" · "}
                {pluralizeRu(platform.events_count, ["старт", "старта", "стартов"])}
              </>
            ) : (
              "нет данных о стартах"
            )}
          </div>
        </li>
      ))}
    </ol>
  );
}

function LocationLeadersSection({ slug }: { slug: string }) {
  const [leaders, setLeaders] = useState<LocationLeaders | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    getLocationLeaders(slug)
      .then((data) => {
        if (!cancelled) {
          setLeaders(data);
        }
      })
      .catch((err) => {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : "Не удалось загрузить рейтинги");
        }
      });
    return () => {
      cancelled = true;
    };
  }, [slug]);

  if (error) {
    return null;
  }
  if (!leaders) {
    return (
      <section className="card loc-section">
        <h2 className="section-title">Рейтинги локации</h2>
        <p className="muted">Загрузка…</p>
      </section>
    );
  }
  if (leaders.runners.length === 0 && leaders.volunteers.length === 0) {
    return null;
  }

  return (
    <div className="loc-columns">
      {leaders.runners.length > 0 && (
        <section className="card loc-section">
          <h2 className="section-title">
            Топ по пробежкам
            <StatHintTooltip text="Число пробежек и лучшее время считаются только на этой локации. Если у участника единый профиль на сайте (привязаны аккаунты нескольких систем), пробежки во всех системах суммируются в одну строку. Без привязки аккаунты разных систем объединить нельзя — они остаются отдельными строками.">
              <span className="loc-section-title-info" aria-label="Как считается">
                ⓘ
              </span>
            </StatHintTooltip>
          </h2>
          <table className="data-table loc-leaders-table">
            <colgroup>
              <col className="loc-leaders-col-rank" />
              <col />
              <col className="loc-leaders-col-num" />
              <col className="loc-leaders-col-time" />
            </colgroup>
            <thead>
              <tr>
                <th>#</th>
                <th>Участник</th>
                <th title="Пробежек на этой локации">Пробежек</th>
                <th title="Лучшее время участника здесь">Лучшее</th>
              </tr>
            </thead>
            <tbody>
              {leaders.runners.map((runner, index) => (
                <tr key={`${runner.name}-${index}`}>
                  <td className="loc-leaders-rank">{index + 1}</td>
                  <td>{runner.name ?? "—"}</td>
                  <td>{runner.runs_count}</td>
                  <td>{stripLeadingHours(runner.best_time_display)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>
      )}
      {leaders.volunteers.length > 0 && (
        <section className="card loc-section">
          <h2 className="section-title">
            Топ по волонтёрствам
            <StatHintTooltip text="Число волонтёрств считается только на этой локации. Если у волонтёра единый профиль на сайте (привязаны аккаунты нескольких систем), волонтёрства во всех системах суммируются в одну строку. Без привязки аккаунты разных систем объединить нельзя — они остаются отдельными строками.">
              <span className="loc-section-title-info" aria-label="Как считается">
                ⓘ
              </span>
            </StatHintTooltip>
          </h2>
          <table className="data-table loc-leaders-table">
            <colgroup>
              <col className="loc-leaders-col-rank" />
              <col />
              <col className="loc-leaders-col-num" />
            </colgroup>
            <thead>
              <tr>
                <th>#</th>
                <th>Волонтёр</th>
                <th title="Волонтёрств на этой локации">Волонтёрств</th>
              </tr>
            </thead>
            <tbody>
              {leaders.volunteers.map((volunteer, index) => (
                <tr key={`${volunteer.name}-${index}`}>
                  <td className="loc-leaders-rank">{index + 1}</td>
                  <td>{volunteer.name ?? "—"}</td>
                  <td>{volunteer.count}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>
      )}
    </div>
  );
}

function stripLeadingHours(display: string | null): string {
  if (!display) {
    return "—";
  }
  return display.replace(/^00:/, "");
}

function LocationInfoCard({ page }: { page: LocationPageData }) {
  const placeParts = [page.city, page.region, page.country].filter(
    (part, index, all) => part && all.indexOf(part) === index,
  );
  return (
    <div className="loc-info">
      {page.latitude !== null && page.longitude !== null && (
        <LocationMiniMap latitude={page.latitude} longitude={page.longitude} name={page.name} />
      )}
      <ul className="loc-info-list">
        {placeParts.length > 0 && (
          <li>
            <span className="loc-info-label">Где:</span> {placeParts.join(", ")}
          </li>
        )}
        {page.start_point_url && (
          <li>
            <span className="loc-info-label">Точка старта:</span>{" "}
            <a href={page.start_point_url} target="_blank" rel="noreferrer">
              открыть на карте
            </a>
          </li>
        )}
        {page.map_url && (
          <li>
            <span className="loc-info-label">Схема маршрута:</span>{" "}
            <a href={page.map_url} target="_blank" rel="noreferrer">
              посмотреть
            </a>
          </li>
        )}
        {(() => {
          // Официальную страницу даём только в актуальной системе — старые
          // (parkrun-эра и т.п.) часто мертвы или неинформативны.
          const current = page.platforms.find((platform) => platform.is_active === true && platform.url);
          if (!current) {
            return null;
          }
          return (
            <li>
              <span className="loc-info-label">Официальная страница:</span>{" "}
              <a href={current.url ?? "#"} target="_blank" rel="noreferrer">
                {platformCodeLabel(current.platform_code)}
              </a>
            </li>
          );
        })()}
      </ul>
      <p className="muted loc-info-note">
        Старты проходят по субботам утром. Расписание и переносы уточняйте на официальной странице локации.
      </p>
    </div>
  );
}

function LocationPageContent({ slug }: { slug: string }) {
  const [page, setPage] = useState<LocationPageData | null>(null);
  const [notFound, setNotFound] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [recordsModalType, setRecordsModalType] = useState<RecordType | null>(null);

  useEffect(() => {
    let cancelled = false;
    setPage(null);
    setNotFound(false);
    setError(null);
    getLocationPage(slug)
      .then((data) => {
        if (cancelled) {
          return;
        }
        setPage(data);
        // Канонический URL страницы — slug основной системы локации.
        if (data.slug && data.slug !== slug) {
          window.history.replaceState(null, "", `/locations/${data.slug}`);
        }
      })
      .catch((err) => {
        if (cancelled) {
          return;
        }
        if (err instanceof ApiError && err.status === 404) {
          setNotFound(true);
        } else {
          setError(err instanceof Error ? err.message : "Не удалось загрузить локацию");
        }
      });
    return () => {
      cancelled = true;
    };
  }, [slug]);

  if (notFound) {
    return (
      <PortalSectionShell>
        <div className="card">
          <p className="muted">Локация не найдена.</p>
          <p>
            <a href="/locations">Все локации</a>
          </p>
        </div>
      </PortalSectionShell>
    );
  }

  if (error) {
    return (
      <PortalSectionShell>
        <div className="card error">
          <p>{error}</p>
        </div>
      </PortalSectionShell>
    );
  }

  if (!page) {
    return (
      <PortalSectionShell>
        <p className="muted">Загрузка…</p>
      </PortalSectionShell>
    );
  }

  const stats = page.stats;
  const records = stats.course_records;

  return (
    <PortalSectionShell>
      <header className="loc-header loc-wide-page">
        <p className="muted loc-header-breadcrumb">
          <a href="/locations">← Все локации</a> / {page.name}
        </p>
        <div className="loc-header-title">
          <h1>{page.name}</h1>
          <LocationStatusLabel isPaused={page.is_paused} isCancelled={page.is_cancelled} />
        </div>
        <p className="muted loc-header-place">
          {[page.city, page.region, page.country]
            .filter((part, index, all) => part && all.indexOf(part) === index)
            .join(" · ")}
        </p>
        <div className="loc-header-platforms">
          {page.platforms.map((platform) => (
            <PlatformBadge key={platform.platform_code} code={platform.platform_code} />
          ))}
        </div>
      </header>

      <LocationRatingPrompt identityKey={page.identity_key} />

      <section className="card loc-section">
        <div className="loc-section-head">
          <h2 className="section-title">Локация в цифрах</h2>
          <a className="loc-events-link" href={`/locations/${page.slug}/events`}>
            Журнал протоколов →
          </a>
        </div>
        <div className="loc-stats-grid">
          <StatTile
            value={stats.events_count}
            label={pluralFormRu(stats.events_count, ["старт", "старта", "стартов"])}
            sub={
              stats.first_event_date
                ? `с ${formatDate(stats.first_event_date)}`
                : undefined
            }
          />
          <StatTile
            value={stats.finishers_total}
            label="финишей"
            sub={stats.avg_finishers ? `в среднем ${stats.avg_finishers} на старте` : undefined}
          />
          <StatTile value={stats.unique_participants} label="уникальных участников" />
          <StatTile value={stats.volunteers_total} label="волонтёрств" />
          <StatTile value={stats.unique_volunteers} label="уникальных волонтёров" />
          {stats.attendance_record && (
            <StatTile
              value={stats.attendance_record.finishers}
              label="рекорд посещаемости"
              sub={
                stats.attendance_record.event_date
                  ? formatDate(stats.attendance_record.event_date)
                  : undefined
              }
              onDetails={() => setRecordsModalType("attendance")}
            />
          )}
          {stats.avg_finish_time_sec !== null && (
            <StatTile
              value={formatTime(stats.avg_finish_time_sec)}
              label="среднее время"
              sub={<DeltaHint deltaSec={stats.avg_finish_time_delta_sec} />}
            />
          )}
          {records.male && (
            <StatTile
              value={formatTime(records.male.finish_time_sec)}
              label="рекорд трассы · М"
              sub={courseRecordSub(records.male)}
              badge={{ text: "🏆 М", title: "Рекорд трассы среди мужчин" }}
              onDetails={() => setRecordsModalType("male")}
            />
          )}
          {records.female && (
            <StatTile
              value={formatTime(records.female.finish_time_sec)}
              label="рекорд трассы · Ж"
              sub={courseRecordSub(records.female)}
              badge={{ text: "🏆 Ж", title: "Рекорд трассы среди женщин" }}
              onDetails={() => setRecordsModalType("female")}
            />
          )}
          {stats.median_finish_time_sec !== null && (
            <StatTile
              value={formatTime(stats.median_finish_time_sec)}
              label="медианное время"
              sub={<DeltaHint deltaSec={stats.median_finish_time_delta_sec} />}
            />
          )}
        </div>
      </section>

      {stats.last_event && <LastEventSection lastEvent={stats.last_event} />}

      <section className="card loc-section">
        <h2 className="section-title">Распределение финишных времён</h2>
        <LocationFinishHistogram rows={page.histogram.rows} binSizeSec={page.histogram.bin_size_sec} />
      </section>

      <LocationLeadersSection slug={page.slug} />

      <div className="loc-columns">
        <section className="card loc-section">
          <h2 className="section-title">История систем</h2>
          <PlatformTimeline page={page} />
        </section>

        <section className="card loc-section">
          <h2 className="section-title">Как добраться</h2>
          <LocationInfoCard page={page} />
        </section>
      </div>

      <LocationRecordsModal
        slug={page.slug}
        type="attendance"
        open={recordsModalType === "attendance"}
        onClose={() => setRecordsModalType(null)}
      />
      <LocationRecordsModal
        slug={page.slug}
        type="male"
        open={recordsModalType === "male"}
        onClose={() => setRecordsModalType(null)}
      />
      <LocationRecordsModal
        slug={page.slug}
        type="female"
        open={recordsModalType === "female"}
        onClose={() => setRecordsModalType(null)}
      />
    </PortalSectionShell>
  );
}

export function LocationPage({ slug }: { slug: string }) {
  return <RequireAuth>{() => <LocationPageContent slug={slug} />}</RequireAuth>;
}

function formatTime(totalSec: number): string {
  const minutes = Math.floor(totalSec / 60);
  const seconds = totalSec % 60;
  return `${minutes}:${String(seconds).padStart(2, "0")}`;
}
