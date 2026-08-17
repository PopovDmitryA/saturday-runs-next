import { Fragment, useCallback, useEffect, useState, type ReactNode } from "react";
import { LocationStatusLabel } from "../../components/LocationStatusBadge";
import { PlatformBadge } from "../../components/PlatformBadge";
import { StatHintTooltip } from "../../components/StatHintTooltip";
import { TableWrap } from "../../components/tableUx/TableWrap";
import { useNarrowViewport } from "../../components/tableUx/useNarrowViewport";
import {
  ApiError,
  getLocationLeaders,
  getLocationPage,
  getLocationPersonalStats,
  type LocationAgeGroupRecord,
  type LocationAgeGroupStanding,
  type LocationCourseRecord,
  type LocationDescription,
  type LocationHomeDistance,
  type LocationLastEvent,
  type LocationLeaders,
  type LocationPage as LocationPageData,
  type LocationPersonalStats,
} from "../../lib/api";
import { applyPageMeta, locationLeadSentences, locationPageMeta } from "../../lib/pageMeta";
import { locationHintFor, rememberLocationHint } from "../../lib/locationHint";
import { flushMetrikaHit } from "../../lib/metrika";
import {
  formatDate,
  formatInt,
  formatKm,
  formatStatValue,
  platformCodeLabel,
  pluralFormRu,
  pluralizeRu,
} from "../../lib/format";
import { PromoLoginCard } from "../../components/PromoLoginCard";
import { cabinetTabHref } from "../../lib/portalRoutes";
import { useOptionalUser } from "../../lib/useOptionalUser";
import { PortalSectionShell } from "../portal/PortalSectionShell";
import { useOptionalShareSheet } from "../sharing/ShareSheetContext";
import { locationCardSubject, locationEventSubject, locationMeSubject } from "../sharing/subjects";
import { LocationFinishHistogram } from "./LocationFinishHistogram";
import { LocationMiniMap } from "./LocationMiniMap";
import { LocationRatingPrompt } from "./LocationRatingPrompt";
import { LocationRecordsModal, type RecordType } from "./LocationRecordsModal";

function StatTile({
  value,
  label,
  sub,
  badge,
  hint,
  onDetails,
  detailsLabel,
  link,
}: {
  value: ReactNode;
  label: string;
  sub?: ReactNode;
  badge?: { text: string; title: string };
  // Пояснение «как это считается» — значком «i» рядом с подписью.
  hint?: string;
  onDetails?: () => void;
  // Подпись кнопки подробностей, если «подробнее» не по смыслу.
  detailsLabel?: string;
  // Ссылка-действие внизу плитки (например «журнал протоколов →» у стартов).
  link?: { href: string; label: string };
}) {
  return (
    <div className="stat-card loc-stat-card">
      <span className="stat-value loc-stat-value">
        {formatStatValue(value)}
        {/* Значок «i» сразу после цифры: подсказка относится к самому числу,
            а не к подписи под ним. */}
        {hint && (
          <StatHintTooltip text={hint}>
            <span className="loc-stat-info" aria-label="Как считается">
              i
            </span>
          </StatHintTooltip>
        )}
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
          {detailsLabel ?? "подробнее"}
        </button>
      )}
      {link && (
        <a className="loc-stat-details-link" href={link.href}>
          {link.label}
        </a>
      )}
    </div>
  );
}

/**
 * Плитка «сколько отсюда до дома». Зелёная — здесь уже бегали, серая — ещё нет:
 * по цвету видно, добавит эта площадка километров в зачёт или уже добавила.
 */
function HomeDistanceTile({ home }: { home: LocationHomeDistance }) {
  if (home.is_home) {
    return (
      <div className="stat-card loc-stat-card loc-stat-home loc-stat-home-visited">
        <span className="stat-value loc-stat-value">дом</span>
        <span className="stat-label">ваша домашняя локация</span>
        <span className="loc-stat-sub">от неё считается дальность ваших стартов</span>
      </div>
    );
  }
  const visitedClass = home.visited ? " loc-stat-home-visited" : " loc-stat-home-unvisited";
  return (
    <div className={`stat-card loc-stat-card loc-stat-home${visitedClass}`}>
      <span className="stat-value loc-stat-value">
        {home.distance_km == null ? "—" : formatKm(home.distance_km)}
        <StatHintTooltip
          text={`Расстояние по прямой от вашей домашней локации («${home.home_name}») до этой площадки. Домашняя локация меняется в настройках.`}
        >
          <span className="loc-stat-info" aria-label="Как считается">
            i
          </span>
        </StatHintTooltip>
      </span>
      <span className="stat-label">от дома</span>
      <span className="loc-stat-sub">
        {home.visited ? "вы здесь уже бегали" : "вы здесь ещё не были"}
      </span>
    </div>
  );
}

/**
 * Адрес вкладки «Пробежки» кабинета с предзаполненным фильтром по локации:
 * бегун попадает сразу к своим стартам на этой площадке.
 */
function runsAtLocationHref(user: { serial_id?: number | null; public_slug?: string | null } | null, locationName: string): string {
  const base = cabinetTabHref(user, "runs");
  return `${base}?location=${encodeURIComponent(locationName)}`;
}

/** Имя участника: ссылкой на профиль сайта, если участник привязал систему. */
function RunnerName({ name, handle }: { name: string | null; handle?: string | null }) {
  const label = name?.trim() || "—";
  if (!handle || label === "—") {
    return <>{label}</>;
  }
  return (
    <a className="loc-runner-link" href={`/users/${encodeURIComponent(handle)}`}>
      {label}
    </a>
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

function LastEventSection({ lastEvent, page }: { lastEvent: LocationLastEvent; page: LocationPageData }) {
  const sheet = useOptionalShareSheet();
  const newcomers =
    lastEvent.debutants !== null || lastEvent.first_at_location !== null
      ? (lastEvent.debutants ?? 0) + (lastEvent.first_at_location ?? 0)
      : null;
  return (
    // Акцентная заливка — как у «Последней субботы» на главной: свежий старт
    // не должен теряться среди агрегатов за всю историю.
    <section className="card loc-section loc-section-accent">
      <div className="loc-section-head">
        <h2 className="section-title">Последний старт</h2>
        {sheet !== null && (
          <button
            type="button"
            className="s2-trigger"
            onClick={() => {
              const subject = locationEventSubject(page);
              if (subject) {
                sheet.open({ subject, entry: "location" });
              }
            }}
          >
            📤 Поделиться
          </button>
        )}
      </div>
      <div className="loc-stats-grid">
        <StatTile value={formatDate(lastEvent.event_date)} label={platformCodeLabel(lastEvent.platform_code)} />
        {lastEvent.finishers !== null && <StatTile value={lastEvent.finishers} label="финишей" />}
        {lastEvent.volunteers !== null && <StatTile value={lastEvent.volunteers} label="волонтёров" />}
        {newcomers !== null && (
          <StatTile
            value={newcomers}
            label="новичков"
            sub={
              lastEvent.debutants || lastEvent.first_at_location
                ? [
                    lastEvent.debutants ? `${lastEvent.debutants} дебют в системе` : null,
                    lastEvent.first_at_location
                      ? `${lastEvent.first_at_location} впервые здесь`
                      : null,
                  ]
                    .filter(Boolean)
                    .join(" · ")
                : undefined
            }
          />
        )}
        {lastEvent.prs !== null && <StatTile value={lastEvent.prs} label="личных рекордов" />}
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

/**
 * Ширина полосы в строке топа: 100% у лидера группы, дальше пропорционально
 * отставанию. Считается от размаха самой пятёрки, иначе на плотных группах
 * (все в пределах минуты) полосы были бы неотличимы.
 */
function topBarWidth(seconds: number, rows: { best_time_sec: number }[]): number {
  const best = rows[0]?.best_time_sec ?? seconds;
  const worst = rows[rows.length - 1]?.best_time_sec ?? seconds;
  if (worst <= best) {
    return 100;
  }
  return Math.round(100 - ((seconds - best) / (worst - best)) * 55);
}

function AgeGroupRecordsTable({
  records,
  openKey,
  onToggle,
}: {
  records: LocationAgeGroupRecord[];
  openKey: string | null;
  onToggle: (key: string) => void;
}) {
  return (
    <TableWrap className="loc-age-records-wrap">
      <table className="data-table loc-age-records-table">
        <colgroup>
          <col className="loc-age-records-col-group" />
          <col className="loc-age-records-col-time" />
          <col />
          <col className="loc-age-records-col-date" />
        </colgroup>
        <thead>
          <tr>
            <th>Группа</th>
            <th>Время</th>
            <th>Рекордсмен</th>
            <th>Дата</th>
          </tr>
        </thead>
        <tbody>
          {records.map((record) => {
            const open = openKey === record.key;
            return (
              <Fragment key={record.key}>
                {/* id на строке — якорь для плитки «место в группе» из блока «Вы на этой локации». */}
                <tr id={record.key} className={open ? "loc-age-records-row-open" : undefined}>
                  <td className="loc-age-records-group">
                    {record.top.length > 0 ? (
                      <button
                        type="button"
                        className="loc-age-records-toggle"
                        aria-expanded={open}
                        onClick={() => onToggle(record.key)}
                        title={open ? "Скрыть топ-5 группы" : "Показать топ-5 группы"}
                      >
                        <span className="loc-age-records-caret" aria-hidden="true">
                          {open ? "▾" : "▸"}
                        </span>
                        {record.age_group}
                      </button>
                    ) : (
                      record.age_group
                    )}
                  </td>
                  <td className="loc-age-records-time">
                    {stripLeadingHours(record.finish_time_display)}
                  </td>
                  <td>
                    <RunnerName name={record.runner_name} handle={record.runner_handle} />
                  </td>
                  <td className="loc-age-records-date">
                    {record.event_date ? formatDate(record.event_date) : "—"}
                  </td>
                </tr>
                {open && (
                  <tr className="loc-age-records-top-row">
                    <td colSpan={4}>
                      <div className="loc-age-top">
                        <div className="loc-age-top-head">
                          Топ-{record.top.length} группы {record.age_group}
                          <span className="loc-age-top-hint">
                            лучшее время каждого участника
                            {/* Размер группы: без него непонятно, топ-5 из скольких. */}
                            {record.runners_total > 0 && (
                              <>
                                {" · всего "}
                                {pluralizeRu(record.runners_total, ["участник", "участника", "участников"])}
                                {record.finishes_total > 0 &&
                                  ` и ${pluralizeRu(record.finishes_total, ["финиш", "финиша", "финишей"])}`}
                              </>
                            )}
                          </span>
                        </div>
                        <ol className="loc-age-top-list">
                          {record.top.map((row) => (
                            <li key={`${record.key}-${row.place}-${row.name}`} className="loc-age-top-item">
                              <span className="loc-age-top-place">{row.place}</span>
                              <span className="loc-age-top-name">
                                <RunnerName name={row.name} handle={row.handle} />
                              </span>
                              {/* Полоса длиной от лучшего времени в группе: видно отрыв. */}
                              <span className="loc-age-top-bar" aria-hidden="true">
                                <span
                                  className="loc-age-top-bar-fill"
                                  style={{ width: `${topBarWidth(row.best_time_sec, record.top)}%` }}
                                />
                              </span>
                              <span className="loc-age-top-time">
                                {stripLeadingHours(row.best_time_display)}
                              </span>
                            </li>
                          ))}
                        </ol>
                      </div>
                    </td>
                  </tr>
                )}
              </Fragment>
            );
          })}
        </tbody>
      </table>
    </TableWrap>
  );
}

function AgeGroupRecordsSection({
  records,
  openKey,
  onToggle,
}: {
  records: LocationAgeGroupRecord[];
  openKey: string | null;
  onToggle: (key: string) => void;
}) {
  if (records.length === 0) {
    return null;
  }
  const male = records.filter((record) => record.gender === "male");
  const female = records.filter((record) => record.gender === "female");
  return (
    <section className="card loc-section">
      <h2 className="section-title">
        Рекорды по возрастным группам
        <StatHintTooltip text="Считается только по 5 вёрст — остальные системы возрастную группу в протоколе не публикуют. Нажмите на группу, чтобы раскрыть её топ-5: уникальные участники и лучшее время каждого именно в этой категории.">
          <span className="loc-section-title-info" aria-label="Как считается">
            ⓘ
          </span>
        </StatHintTooltip>
      </h2>
      <div className="loc-columns">
        {male.length > 0 && (
          <div>
            <h3 className="loc-age-records-subtitle">Мужчины</h3>
            <AgeGroupRecordsTable records={male} openKey={openKey} onToggle={onToggle} />
          </div>
        )}
        {female.length > 0 && (
          <div>
            <h3 className="loc-age-records-subtitle">Женщины</h3>
            <AgeGroupRecordsTable records={female} openKey={openKey} onToggle={onToggle} />
          </div>
        )}
      </div>
    </section>
  );
}

function LocationLeadersSection({ slug }: { slug: string }) {
  const [leaders, setLeaders] = useState<LocationLeaders | null>(null);
  const [error, setError] = useState<string | null>(null);
  // На телефоне топы — карточки: имя не тесно соседствует с цифрами.
  const narrowViewport = useNarrowViewport();

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
          {narrowViewport ? (
            <div className="rowcards loc-leaders-cards">
              {leaders.runners.map((runner, index) => (
                <div className="rowcard" key={`${runner.name}-${index}`}>
                  <div className="rowcard-rank">{index + 1}</div>
                  <div className="rowcard-mid">
                    <div className="rowcard-title">
                      <RunnerName name={runner.name} handle={runner.handle} />
                    </div>
                    <div className="rowcard-sub">
                      {pluralizeRu(runner.runs_count, ["пробежка", "пробежки", "пробежек"])} здесь
                    </div>
                  </div>
                  <div className="rowcard-right">
                    <div className="rowcard-value">{stripLeadingHours(runner.best_time_display)}</div>
                    <div className="rowcard-sub">лучшее</div>
                  </div>
                </div>
              ))}
            </div>
          ) : (
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
                    <td>
                      <RunnerName name={runner.name} handle={runner.handle} />
                    </td>
                    <td>{formatInt(runner.runs_count)}</td>
                    <td>{stripLeadingHours(runner.best_time_display)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
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
          {narrowViewport ? (
            <div className="rowcards loc-leaders-cards">
              {leaders.volunteers.map((volunteer, index) => (
                <div className="rowcard" key={`${volunteer.name}-${index}`}>
                  <div className="rowcard-rank">{index + 1}</div>
                  <div className="rowcard-mid">
                    <div className="rowcard-title">
                      <RunnerName name={volunteer.name} handle={volunteer.handle} />
                    </div>
                    <div className="rowcard-sub">
                      {pluralizeRu(volunteer.count, ["волонтёрство", "волонтёрства", "волонтёрств"])}{" "}
                      здесь
                    </div>
                  </div>
                </div>
              ))}
            </div>
          ) : (
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
                    <td>
                      <RunnerName name={volunteer.name} handle={volunteer.handle} />
                    </td>
                    <td>{formatInt(volunteer.count)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
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

function paragraphsOf(text: string): string[] {
  return text
    .split("\n\n")
    .map((part) => part.trim())
    .filter(Boolean);
}

function TextBlock({ text }: { text: string }) {
  return (
    <div className="loc-about-text">
      {paragraphsOf(text).map((part, index) => (
        <p key={index}>{part}</p>
      ))}
    </div>
  );
}

/**
 * Подблок с текстом, взятым со страницы системы.
 *
 * Вынесен в отдельную рамку с шапкой «Описание с сайта …» намеренно: это
 * цитата с чужого сайта, а не наши данные, и читатель должен видеть границу.
 * Раньше приписка про источник стояла в конце длинного текста — к тому моменту
 * уже не было понятно, к чему она относится.
 *
 * Тот же текст серверный пререндер отдаёт роботу
 * (seo_service._location_description_rows): расхождение человека и робота
 * поисковик считает подменой.
 */
function LocationDescriptionQuote({ description }: { description: LocationDescription }) {
  const schedule = (description.schedule_text ?? "").trim();
  const course = (description.course_text ?? "").trim();
  const travelText = (description.travel_text ?? "").trim();
  const sections = (description.travel_sections ?? []).filter((section) => section.text.trim());
  const links = (description.links ?? []).filter((link) => link.url);
  if (!schedule && !course && !travelText && sections.length === 0) {
    return null;
  }

  const platform = platformCodeLabel(description.platform_code);
  return (
    <div className="loc-about-quote">
      <div className="loc-about-quote-head">
        <h3 className="loc-about-subtitle">
          Описание с официального сайта{" "}
          {description.source_url ? (
            <a href={description.source_url} target="_blank" rel="noreferrer nofollow">
              {platform}
            </a>
          ) : (
            platform
          )}
        </h3>
        {description.updated_at && (
          <span className="muted loc-about-source">обновлено {formatDate(description.updated_at)}</span>
        )}
      </div>

      {schedule && (
        <>
          <h4 className="loc-about-quote-title">Где и когда</h4>
          <TextBlock text={schedule} />
        </>
      )}

      {course && (
        <>
          <h4 className="loc-about-quote-title">Трасса</h4>
          <TextBlock text={course} />
        </>
      )}

      {(travelText || sections.length > 0) && (
        <>
          <h4 className="loc-about-quote-title">Как добраться</h4>
          {travelText && <TextBlock text={travelText} />}
          {sections.length > 0 && (
            <div className="loc-about-ways">
              {sections.map((section, index) => (
                <div className="loc-about-way" key={`${section.title ?? "way"}-${index}`}>
                  {section.title && <h5>{section.title}</h5>}
                  <TextBlock text={section.text} />
                </div>
              ))}
            </div>
          )}
        </>
      )}

      {links.length > 0 && (
        <ul className="loc-about-links">
          {links.map((link) => (
            <li key={link.url}>
              <a href={link.url} target="_blank" rel="noreferrer nofollow">
                {link.title || "Ссылка"}
              </a>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

/**
 * Нижний блок страницы — всё про саму площадку.
 *
 * Порядок: сначала наше (карта, адрес, ссылки, история систем), потом отдельным
 * подблоком описание с сайта системы. Своё и чужое не перемешано, поэтому видно,
 * где кончаются наши данные и начинается цитата.
 */
function LocationAboutSection({ page }: { page: LocationPageData }) {
  return (
    <section className="card loc-section loc-about">
      <h2 className="section-title">О площадке</h2>

      <div className="loc-about-top">
        <div className="loc-about-place">
          <LocationInfoCard page={page} />
        </div>
        <div className="loc-about-history">
          <h3 className="loc-about-subtitle">История систем</h3>
          <PlatformTimeline page={page} />
        </div>
      </div>

      {page.description && <LocationDescriptionQuote description={page.description} />}
    </section>
  );
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
      </ul>
    </div>
  );
}

/**
 * Плитка «место в группе»: у участника их столько, сколько возрастных
 * категорий он успел пройти на этой площадке. Ссылка ведёт к топ-5 этой же
 * группы — в таблицу «Рекорды по возрастным группам» ниже по странице.
 */
function AgeGroupPlaceTile({
  group,
  onOpen,
}: {
  group: LocationAgeGroupStanding;
  onOpen: (key: string) => void;
}) {
  if (group.place == null) {
    return null;
  }
  return (
    <StatTile
      // Знаменатель обязателен: «#35» без него читается как слабый результат,
      // хотя это 35-е из 54 — место осмысленно только в размере группы.
      value={
        group.total > 0 ? (
          <>
            #{group.place}
            {/* Знаменатель мельче: место — главная цифра, размер группы лишь
                придаёт ей смысл и не должен занимать столько же места. */}
            <span className="loc-stat-value-denominator">из {formatInt(group.total)}</span>
          </>
        ) : (
          `#${group.place}`
        )
      }
      label={`место в группе ${group.label}`}
      hint="Место по лучшему времени внутри своей возрастной категории на этой площадке. Считается только по 5 вёрст — остальные системы категорию в протоколе не публикуют. Групп столько, сколько категорий вы успели пройти здесь."
      sub={`результат ${stripLeadingHours(group.best_time_display)}`}
      onDetails={() => onOpen(group.key)}
      detailsLabel="топ-5 группы →"
    />
  );
}

/**
 * Блок «Вы на этой локации»: залогиненному с привязанным профилем — личные
 * цифры площадки, анониму — продающая карточка «войдите и увидите свою
 * статистику». Пользователю без привязки — подсказка привязать профиль.
 */
function LocationPersonalSection({
  slug,
  onOpenAgeGroup,
}: {
  slug: string;
  // Плитка «место в группе» раскрывает и подсвечивает её топ-5 в «Рекордах» ниже.
  onOpenAgeGroup: (key: string) => void;
}) {
  const user = useOptionalUser();
  const personalSheet = useOptionalShareSheet();
  const [stats, setStats] = useState<LocationPersonalStats | null>(null);

  useEffect(() => {
    if (!user) {
      return;
    }
    let cancelled = false;
    setStats(null);
    getLocationPersonalStats(slug)
      .then((result) => {
        if (!cancelled) setStats(result);
      })
      .catch(() => {
        // тихо: блок просто не покажется
      });
    return () => {
      cancelled = true;
    };
  }, [user, slug]);

  // Сессия ещё проверяется — не мигаем карточкой-призывом перед залогиненным.
  if (user === undefined) {
    return null;
  }

  if (user === null) {
    return (
      <PromoLoginCard
        icon="🏃"
        title="Бегали здесь?"
        text="Войдите и привяжите профиль своей беговой системы — покажем вашу личную статистику на этой локации: пробежки, лучшее и среднее время, место в топе площадки."
      />
    );
  }

  if (!stats) {
    return null;
  }

  if (stats.runs_count === 0 && stats.volunteering_count === 0) {
    return (
      <section className="card loc-section loc-personal-cta">
        <div className="loc-personal-cta-text">
          <h2 className="section-title">Вы на этой локации</h2>
          <p className="muted">
            {stats.total_runs === 0
              ? "Привяжите профиль своей беговой системы в настройках — и здесь появится ваша личная статистика площадки."
              : "Вы здесь ещё не бегали — самое время открыть новую точку на своей карте."}
          </p>
        </div>
        {stats.total_runs === 0 && (
          <a className="btn secondary" href="/settings">
            К настройкам
          </a>
        )}
        {stats.organizer_access && (
          <a className="btn secondary" href={`/organizer/${stats.slug}`}>
            Кабинет организатора →
          </a>
        )}
        {stats.home_distance && (
          <div className="loc-stats-grid loc-stats-grid-single">
            <HomeDistanceTile home={stats.home_distance} />
          </div>
        )}
      </section>
    );
  }

  const sharePct =
    stats.total_runs > 0 ? Math.round((stats.runs_count / stats.total_runs) * 100) : null;

  return (
    // Свой блок среди общих: выделяем, чтобы взгляд цеплялся за личные цифры.
    <section className="card loc-section loc-section-personal">
      <div className="loc-section-head">
        <h2 className="section-title">Вы на этой локации</h2>
        {personalSheet !== null && (
          <button
            type="button"
            className="s2-trigger"
            onClick={() => {
              const subject = locationMeSubject(stats, user);
              if (subject) {
                personalSheet.open({ subject, entry: "location" });
              }
            }}
          >
            📤 Поделиться
          </button>
        )}
      </div>
      <div className="loc-stats-grid">
        <StatTile
          value={stats.runs_count}
          label={pluralFormRu(stats.runs_count, ["пробежка", "пробежки", "пробежек"])}
          sub={sharePct != null && sharePct > 0 ? `${sharePct}% всех ваших стартов` : undefined}
        />
        {stats.best_time_display && (
          <StatTile
            value={stripLeadingHours(stats.best_time_display)}
            label="лучшее время здесь"
            sub={stats.best_time_date ? formatDate(stats.best_time_date) : undefined}
          />
        )}
        {stats.avg_time_display && (
          <StatTile value={stripLeadingHours(stats.avg_time_display)} label="среднее время здесь" />
        )}
        {/* Топ по пробежкам — только внутри своего пола. Общий топ убран:
            сравнение мужчин и женщин одной строкой бегуну мало что говорит, а
            в знаменатель попадали «неизвестные» из протоколов — у них пола нет,
            поэтому срез по полу отсекает их сам. У всех привязанных
            пользователей пол известен, так что плитка не пропадёт. */}
        {stats.rank_by_runs_gender != null && stats.runs_count > 0 && (
          <StatTile
            value={`#${stats.rank_by_runs_gender}`}
            label={`в топе по пробежкам · ${stats.gender === "female" ? "Ж" : "М"}`}
            hint={`Место по числу пробежек на этой площадке среди ${
              stats.gender === "female" ? "женщин" : "мужчин"
            } за всю её историю — во всех системах сразу, включая parkrun-эпоху. Привязанные профили считаются одним человеком, неопознанные финишёры протокола в счёт не идут.`}
            sub={
              stats.runners_total_gender != null
                ? `из ${formatInt(stats.runners_total_gender)} ${pluralFormRu(
                    stats.runners_total_gender,
                    ["бегуна", "бегунов", "бегунов"],
                  )}`
                : undefined
            }
          />
        )}
        {stats.first_run_date && (
          <StatTile value={formatDate(stats.first_run_date)} label="первый старт здесь" />
        )}
        {stats.last_run_date && stats.last_run_date !== stats.first_run_date && (
          <StatTile value={formatDate(stats.last_run_date)} label="последний старт" />
        )}
        {stats.home_distance && <HomeDistanceTile home={stats.home_distance} />}
        {(stats.age_groups ?? []).map((group) => (
          <AgeGroupPlaceTile key={group.key} group={group} onOpen={onOpenAgeGroup} />
        ))}
        {stats.volunteering_count > 0 && (
          <StatTile
            value={stats.volunteering_count}
            label={pluralFormRu(stats.volunteering_count, [
              "волонтёрство",
              "волонтёрства",
              "волонтёрств",
            ])}
            sub="здесь"
          />
        )}
        {stats.top_volunteer_role && (
          <StatTile
            value={stats.top_volunteer_role.role}
            label="любимая роль здесь"
            sub={`${formatInt(stats.top_volunteer_role.count)} ${pluralFormRu(stats.top_volunteer_role.count, [
              "раз",
              "раза",
              "раз",
            ])}`}
            hint="Роль, в которой вы выходили на этой локации чаще всего. Ярлыки разных систем считаются одной ролью."
          />
        )}
      </div>
      {(stats.runs_count > 0 || stats.organizer_access) && (
        <div className="loc-personal-actions">
          {/* Открывает «Пробежки» кабинета сразу с фильтром по этой локации. */}
          {stats.runs_count > 0 && (
            <a className="btn secondary btn-sm" href={runsAtLocationHref(user, stats.name)}>
              Мои пробежки здесь →
            </a>
          )}
          {stats.organizer_access && (
            <a className="btn secondary btn-sm" href={`/organizer/${stats.slug}`}>
              Кабинет организатора →
            </a>
          )}
        </div>
      )}
    </section>
  );
}

function LocationPageContent({ slug }: { slug: string }) {
  const shareSheet = useOptionalShareSheet();
  const [page, setPage] = useState<LocationPageData | null>(null);
  const [notFound, setNotFound] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [recordsModalType, setRecordsModalType] = useState<RecordType | null>(null);
  // Раскрытая возрастная группа в «Рекордах». Живёт на уровне страницы, потому
  // что открывать её умеет и плитка «место в группе» из блока «Вы на этой локации».
  const [openAgeGroupKey, setOpenAgeGroupKey] = useState<string | null>(null);

  const toggleAgeGroup = useCallback((key: string) => {
    setOpenAgeGroupKey((current) => (current === key ? null : key));
  }, []);

  const revealAgeGroup = useCallback((key: string) => {
    setOpenAgeGroupKey(key);
    // Скролл после перерисовки: до неё строки топа ещё нет и высота другая.
    requestAnimationFrame(() => {
      document.getElementById(key)?.scrollIntoView({ behavior: "smooth", block: "center" });
    });
  }, []);

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
        rememberLocationHint({ slug: data.slug, name: data.name });
        // Родовой заголовок «Локация — run5k.run» из App.tsx уточняем именем
        // и цифрами, как только данные приехали.
        applyPageMeta(locationPageMeta(data));
        flushMetrikaHit();
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
        // Просмотр был, пусть и неудачный — досылаем с родовым заголовком.
        flushMetrikaHit();
      });
    return () => {
      cancelled = true;
    };
  }, [slug]);

  // Имя из подсказки, пока грузятся данные: иначе подпункт сайдбара с
  // названием площадки мигает при переходах внутри локации.
  const sidebarLocation = page ? { slug: page.slug, name: page.name } : locationHintFor(slug);

  if (notFound) {
    return (
      <PortalSectionShell sidebar={{ active: "locations", location: sidebarLocation }}>
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
      <PortalSectionShell sidebar={{ active: "locations", location: sidebarLocation }}>
        <div className="card error">
          <p>{error}</p>
        </div>
      </PortalSectionShell>
    );
  }

  if (!page) {
    return (
      <PortalSectionShell sidebar={{ active: "locations", location: sidebarLocation }}>
        <p className="muted">Загрузка…</p>
      </PortalSectionShell>
    );
  }

  const stats = page.stats;
  const records = stats.course_records;

  return (
    <PortalSectionShell sidebar={{ active: "locations", location: sidebarLocation }}>
      <header className="loc-header loc-wide-page">
        <p className="muted loc-header-breadcrumb">
          <a href="/locations">← Все локации</a> / {page.name}
        </p>
        <div className="loc-header-title">
          <h1>{page.name}</h1>
          <LocationStatusLabel isPaused={page.is_paused} isCancelled={page.is_cancelled} />
          {shareSheet !== null && (
            <button
              type="button"
              className="s2-trigger loc-header-share"
              onClick={() =>
                shareSheet.open({ subject: locationCardSubject(page), entry: "location" })
              }
            >
              📤 Поделиться
            </button>
          )}
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
        {/* Тот же текст, что серверный пререндер отдаёт роботу: если человек и
            робот видят разные страницы, поисковик считает это подменой. Плюс
            связная фраза полезнее плашек тому, кто попал сюда из поиска и
            ещё не понял, что за место. */}
        <p className="loc-header-lead">{locationLeadSentences(page).join(" ")}</p>
      </header>

      <LocationRatingPrompt identityKey={page.identity_key} />

      <LocationPersonalSection slug={page.slug} onOpenAgeGroup={revealAgeGroup} />

      {stats.last_event && <LastEventSection lastEvent={stats.last_event} page={page} />}

      <section className="card loc-section">
        <div className="loc-section-head">
          <h2 className="section-title">Локация в цифрах</h2>
          {shareSheet !== null && (
            <button
              type="button"
              className="s2-trigger"
              onClick={() =>
                shareSheet.open({ subject: locationCardSubject(page), entry: "location" })
              }
            >
              📤 Поделиться
            </button>
          )}
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
            link={{ href: `/locations/${page.slug}/events`, label: "журнал протоколов →" }}
          />
          <StatTile
            value={stats.finishers_total}
            label="финишей"
            sub={stats.avg_finishers ? `в среднем ${formatInt(stats.avg_finishers)} на старте` : undefined}
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


      <section className="card loc-section">
        <h2 className="section-title">Распределение финишных времён</h2>
        <LocationFinishHistogram rows={page.histogram.rows} binSizeSec={page.histogram.bin_size_sec} />
      </section>

      <AgeGroupRecordsSection
        records={page.age_group_records ?? []}
        openKey={openAgeGroupKey}
        onToggle={toggleAgeGroup}
      />

      <LocationLeadersSection slug={page.slug} />

      {/* Карта, адрес, описание и история систем — одним блоком в самом низу:
          это справка о месте, а не статистика, ради которой страницу открывают. */}
      <LocationAboutSection page={page} />

      {/* Кластер города: запрос «5 вёрст [город]» — не про одну площадку,
          человеку (и поисковику) нужен весь город. Тот же список уходит
          роботу в пререндере — контент обязан совпадать. */}
      {page.city && (page.city_locations?.length ?? 0) > 0 && (
        <section className="card loc-section">
          <h2 className="section-title">{page.city}: другие площадки</h2>
          {/* Чипы, а не колонки текста: ссылки в несколько колонок сливались
              в сплошные строки (репорт Дмитрия 06.08.2026) — рамка вокруг
              каждой площадки делает границы элементов видимыми. */}
          <ul className="loc-city-neighbors">
            {(page.city_locations ?? []).map((item) => (
              <li key={item.slug}>
                <a className="loc-city-neighbor" href={`/locations/${item.slug}`}>
                  {item.name}
                  <span className="loc-city-neighbor-count">
                    {formatInt(item.events_count)} {pluralFormRu(item.events_count, ["старт", "старта", "стартов"])}
                  </span>
                </a>
              </li>
            ))}
          </ul>
        </section>
      )}

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

// Страница открыта без логина (публичная витрина); личные блоки внутри
// сами решают, что показать анониму.
export function LocationPage({ slug }: { slug: string }) {
  return <LocationPageContent slug={slug} />;
}

function formatTime(totalSec: number): string {
  const minutes = Math.floor(totalSec / 60);
  const seconds = totalSec % 60;
  return `${minutes}:${String(seconds).padStart(2, "0")}`;
}
