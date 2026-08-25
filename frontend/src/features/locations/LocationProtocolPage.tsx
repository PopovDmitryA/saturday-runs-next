import { useEffect, useMemo, useRef, useState } from "react";
import { ColumnHeader } from "../../components/activityTable/ColumnHeader";
import { PlatformBadge } from "../../components/PlatformBadge";
import { ScrollToTopButton } from "../../components/ScrollToTopButton";
import { StatHintTooltip } from "../../components/StatHintTooltip";
import {
  ApiError,
  getLocationProtocol,
  type LocationHistogramRow,
  type LocationProtocol,
  type ProtocolResult,
} from "../../lib/api";
import { LocationFinishHistogram } from "./LocationFinishHistogram";
import { applyPageMeta, locationProtocolMeta } from "../../lib/pageMeta";
import { flushMetrikaHit } from "../../lib/metrika";
import { formatDate, formatInt, platformCodeLabel } from "../../lib/format";
import { useFloatingTableHead } from "../../lib/useFloatingTableHead";
import { TableWrap } from "../../components/tableUx/TableWrap";
import { TableViewToggle } from "../../components/tableUx/TableViewToggle";
import { useTableColumns } from "../../components/tableUx/useTableColumns";
import type { AdaptiveColumn } from "../../components/tableUx/useAdaptiveColumns";
import { PortalSectionShell } from "../portal/PortalSectionShell";
import { locationHintFor, rememberLocationHint } from "../../lib/locationHint";
import { useOptionalShareSheet } from "../sharing/ShareSheetContext";
import { locationProtocolSubject } from "../sharing/subjects";

type GenderFilter = "all" | "male" | "female";

type SortKey = "position" | "time" | "age_grade" | "history";

type SortState = { key: SortKey; asc: boolean };

export type LocationProtocolParams = {
  slug: string;
  platformCode: string;
  eventDate: string;
};

function protocolHref(slug: string, platformCode: string, eventDate: string): string {
  return `/locations/${encodeURIComponent(slug)}/protocol/${encodeURIComponent(platformCode)}/${eventDate}`;
}

/** Единый протокол той же недели: адрес подписан субботой (пн — начало недели). */
function unifiedWeekHref(eventDate: string): string {
  const day = new Date(`${eventDate}T00:00:00`);
  const monday = new Date(day);
  monday.setDate(day.getDate() - ((day.getDay() + 6) % 7));
  monday.setDate(monday.getDate() + 5);
  return `/protocol/${monday.toISOString().slice(0, 10)}`;
}

/** «00:21:07» → секунды; null, если времени нет. */
function timeToSec(display: string | null): number | null {
  if (!display) {
    return null;
  }
  const parts = display.split(":").map(Number);
  if (parts.some(Number.isNaN)) {
    return null;
  }
  return parts.reduce((acc, part) => acc * 60 + part, 0);
}

/** «00:18:08» → «18:08» (часовые времена на 5 км не встречаются). */
function stripHours(display: string | null): string {
  if (!display) {
    return "—";
  }
  return display.replace(/^00:/, "");
}

function sortValue(row: ProtocolResult, key: SortKey): number | null {
  switch (key) {
    case "position":
      return row.position;
    case "time":
      return row.finish_time_sec;
    case "age_grade":
      return row.age_grade;
    case "history":
      return row.history_rank;
  }
}

function StatTile({
  value,
  label,
  sub,
  hint,
  badge,
  delta,
  deltaKind = "count",
  deltaHint,
}: {
  value: string | number | null;
  label: string;
  sub?: string | null;
  hint?: string;
  badge?: string;
  // Разница с прошлым стартом: +37 зелёным, −12 красным, 0 не показываем.
  delta?: number | null;
  // «seconds» — дельта времени (±м:сс), меньше = лучше = зелёное.
  deltaKind?: "count" | "seconds";
  deltaHint?: string;
}) {
  const deltaShown = delta !== undefined && delta !== null && delta !== 0;
  const deltaGood = deltaShown && (deltaKind === "seconds" ? delta < 0 : delta > 0);
  const deltaText = !deltaShown
    ? ""
    : deltaKind === "seconds"
      ? `${delta > 0 ? "+" : "−"}${Math.floor(Math.abs(delta) / 60)}:${String(Math.abs(delta) % 60).padStart(2, "0")}`
      : delta > 0
        ? `+${formatInt(delta)}`
        : `−${formatInt(Math.abs(delta))}`;
  return (
    <div className="stat-card loc-stat-card">
      <span className="stat-value loc-stat-value">
        {value ?? "—"}
        {deltaShown && (
          <StatHintTooltip text={deltaHint ?? "Разница с предыдущим стартом площадки"}>
            <span
              className={
                deltaGood ? "protocol-delta protocol-delta-up" : "protocol-delta protocol-delta-down"
              }
            >
              {deltaText}
            </span>
          </StatHintTooltip>
        )}
        {hint && (
          <StatHintTooltip text={hint}>
            <span className="loc-stat-info" aria-label="Как считается">
              i
            </span>
          </StatHintTooltip>
        )}
        {badge && (
          <StatHintTooltip text={badge}>
            <span className="loc-record-badge" aria-label={badge}>
              🏆
            </span>
          </StatHintTooltip>
        )}
      </span>
      <span className="stat-label">{label}</span>
      {sub && <span className="loc-stat-sub">{sub}</span>}
    </div>
  );
}

/** 🥇🥈🥉 для первых трёх мест — видно сразу, без наведения. */
function medal(place: number | null): string | null {
  if (place === 1) return "🥇";
  if (place === 2) return "🥈";
  if (place === 3) return "🥉";
  return null;
}

function RowBadge({ text, title }: { text: string; title: string }) {
  return (
    <StatHintTooltip text={title}>
      <span className="protocol-row-badge">{text}</span>
    </StatHintTooltip>
  );
}

// Колонки протокола в порядке важности; место и участник с временем — минимум.
// Ширины — зеркало CSS-классов .protocol-table .col-* (1rem = 16px).
// Исторические колонки («В истории», «В истории группы») стоят в самом
// правом краю с подкрашенным фоном: они сравнивают результат со всей
// историей площадки, а не с этим стартом.
const PROTOCOL_COLUMNS: AdaptiveColumn[] = [
  { key: "position", width: 58, required: true },
  { key: "runner", width: 220, required: true },
  { key: "time", width: 112, required: true },
  { key: "gender_place", width: 112 },
  { key: "age_category", width: 152 },
  { key: "run_number", width: 128 },
  { key: "age_grade", width: 120 },
  { key: "pace", width: 88 },
  { key: "club", width: 176 },
  { key: "history", width: 168 },
  { key: "history_group", width: 176 },
];

function LocationProtocolContent({ slug, platformCode, eventDate }: LocationProtocolParams) {
  const [data, setData] = useState<LocationProtocol | null>(null);
  const [notFound, setNotFound] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [genderFilter, setGenderFilter] = useState<GenderFilter>("all");
  const [ageFilter, setAgeFilter] = useState<string | null>(null);
  const [nameFilter, setNameFilter] = useState("");
  const [roleFilter, setRoleFilter] = useState<string | null>(null);
  const [clubFilter, setClubFilter] = useState<string | null>(null);
  // Клик по клубу листает страницу к протоколу — иначе отфильтрованная
  // таблица остаётся за экраном и кажется, что ничего не произошло.
  const protocolSectionRef = useRef<HTMLElement | null>(null);
  const [sort, setSort] = useState<SortState>({ key: "position", asc: true });
  const attachFloatingHead = useFloatingTableHead(".tview-bar");
  const sheet = useOptionalShareSheet();
  const tableColumns = useTableColumns(PROTOCOL_COLUMNS);
  const show = tableColumns.show;
  const showFull = tableColumns.showFull;

  useEffect(() => {
    let cancelled = false;
    setData(null);
    setNotFound(false);
    setError(null);
    setGenderFilter("all");
    setAgeFilter(null);
    setNameFilter("");
    setRoleFilter(null);
    setClubFilter(null);
    setSort({ key: "position", asc: true });
    getLocationProtocol(slug, platformCode, eventDate)
      .then((payload) => {
        if (!cancelled) {
          setData(payload);
          rememberLocationHint({ slug: payload.slug, name: payload.name });
          applyPageMeta(locationProtocolMeta(payload));
          flushMetrikaHit();
        }
      })
      .catch((err) => {
        if (cancelled) {
          return;
        }
        if (err instanceof ApiError && err.status === 404) {
          setNotFound(true);
        } else {
          setError(err instanceof Error ? err.message : "Не удалось загрузить протокол");
        }
        flushMetrikaHit();
      });
    return () => {
      cancelled = true;
    };
  }, [slug, platformCode, eventDate]);

  // Возрастные категории для фильтра — ровно те, что стоят в протоколе.
  const ageCategories = useMemo(() => {
    const seen = new Map<string, number>();
    for (const row of data?.results ?? []) {
      if (row.age_category) {
        seen.set(row.age_category, (seen.get(row.age_category) ?? 0) + 1);
      }
    }
    return [...seen.entries()].sort((a, b) => a[0].localeCompare(b[0], "ru"));
  }, [data]);

  const hasAgeCategories = useMemo(
    () => (data?.results ?? []).some((row) => row.age_category),
    [data],
  );
  const hasAgeGrade = useMemo(
    () => (data?.results ?? []).some((row) => row.age_grade !== null),
    [data],
  );
  const hasClubs = useMemo(
    () => (data?.results ?? []).some((row) => row.club_name),
    [data],
  );
  const hasPace = useMemo(
    () => (data?.results ?? []).some((row) => row.pace_display),
    [data],
  );

  // Гистограмма времён этого старта — из строк протокола, бэкенд не нужен.
  const histogramRows = useMemo<LocationHistogramRow[]>(() => {
    const bins = new Map<string, LocationHistogramRow>();
    for (const row of data?.results ?? []) {
      if (!row.finish_time_sec) {
        continue;
      }
      const startSec = Math.floor(row.finish_time_sec / 10) * 10;
      const key = `${startSec}:${row.gender ?? ""}:${row.age_group ?? ""}`;
      const bin = bins.get(key);
      if (bin) {
        bin.count += 1;
      } else {
        bins.set(key, {
          start_sec: startSec,
          gender: row.gender,
          age_group: row.age_group,
          count: 1,
        });
      }
    }
    return [...bins.values()].sort((a, b) => a.start_sec - b.start_sec);
  }, [data]);

  // Роли для фильтра волонтёров — ровно те, что есть на этом старте.
  const volunteerRoles = useMemo(() => {
    const counts = new Map<string, number>();
    for (const person of data?.volunteers ?? []) {
      for (const role of person.roles) {
        counts.set(role, (counts.get(role) ?? 0) + 1);
      }
    }
    return [...counts.entries()].sort((a, b) => a[0].localeCompare(b[0], "ru"));
  }, [data]);

  const volunteerRows = useMemo(() => {
    const all = data?.volunteers ?? [];
    return roleFilter ? all.filter((person) => person.roles.includes(roleFilter)) : all;
  }, [data, roleFilter]);

  const rows = useMemo(() => {
    if (!data) {
      return [];
    }
    const needle = nameFilter.trim().toLowerCase();
    const filtered = data.results.filter((row) => {
      if (genderFilter !== "all" && row.gender !== genderFilter) {
        return false;
      }
      if (ageFilter && row.age_category !== ageFilter) {
        return false;
      }
      if (needle && !(row.name ?? "").toLowerCase().includes(needle)) {
        return false;
      }
      if (clubFilter && row.club_name !== clubFilter) {
        return false;
      }
      return true;
    });
    filtered.sort((a, b) => {
      const left = sortValue(a, sort.key);
      const right = sortValue(b, sort.key);
      if (left === right) {
        return (a.position ?? 0) - (b.position ?? 0);
      }
      // null-значения всегда в конец, независимо от направления.
      if (left === null) {
        return 1;
      }
      if (right === null) {
        return -1;
      }
      const compare = left < right ? -1 : 1;
      return sort.asc ? compare : -compare;
    });
    return filtered;
  }, [data, genderFilter, ageFilter, nameFilter, clubFilter, sort]);

  const toggleClubFilter = (club: string) => {
    setClubFilter((current) => (current === club ? null : club));
    protocolSectionRef.current?.scrollIntoView({ behavior: "smooth", block: "start" });
  };

  const toggleSort = (key: SortKey) => {
    setSort((current) =>
      current.key === key
        ? { key, asc: !current.asc }
        : { key, asc: key !== "age_grade" },
    );
  };

  const sortProps = (key: SortKey) => ({
    filterable: false,
    sortActive: sort.key === key,
    sortAsc: sort.asc,
    onSort: () => toggleSort(key),
  });

  const sidebarLocation = data ? { slug: data.slug, name: data.name } : locationHintFor(slug);

  if (notFound) {
    return (
      <PortalSectionShell sidebar={{ active: "locations", location: sidebarLocation }}>
        <div className="card">
          <p className="muted">Протокол не найден.</p>
          <p>
            <a href={`/locations/${encodeURIComponent(slug)}/events`}>Журнал протоколов локации</a>
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

  if (!data) {
    return (
      <PortalSectionShell sidebar={{ active: "locations", location: sidebarLocation }}>
        <p className="muted">Загрузка…</p>
      </PortalSectionShell>
    );
  }

  const summary = data.summary;
  // Ровно столько колонок, сколько реально отрисовано (для colSpan пустой строки).
  const visibleCount =
    3 +
    (show("gender_place") ? 1 : 0) +
    (show("age_category") ? 1 : 0) +
    (show("history") ? 1 : 0) +
    (hasAgeCategories && show("history_group") ? 1 : 0) +
    (show("run_number") ? 1 : 0) +
    (hasAgeGrade && show("age_grade") ? 1 : 0) +
    (hasPace && show("pace") ? 1 : 0) +
    (hasClubs && show("club") ? 1 : 0);
  const genderKnown = summary.male + summary.female > 0;
  const newcomers = summary.debutants + summary.first_at_location;
  // Обладатели рекорда трассы этого дня — бейдж прямо в строке.
  const courseRecordMale =
    summary.is_course_record_male && summary.best_male_runner_name
      ? summary.best_male_runner_name
      : null;
  const courseRecordFemale =
    summary.is_course_record_female && summary.best_female_runner_name
      ? summary.best_female_runner_name
      : null;
  // Лучшие времена этого старта в секундах — для дельт со вчерашними лучшими.
  const bestMaleSec = timeToSec(summary.best_male_time_display);
  const bestFemaleSec = timeToSec(summary.best_female_time_display);
  const knownTotal = Math.max(
    data.declared_finishers ?? 0,
    ...data.results.map((row) => row.position ?? 0),
  );
  const mostlyMissing = knownTotal > 0 && data.results.length < knownTotal * 0.8;

  return (
    <PortalSectionShell sidebar={{ active: "locations", location: sidebarLocation }}>
      <header className="loc-header loc-wide-page">
        <p className="muted loc-header-breadcrumb">
          <a href="/locations">← Все локации</a> /{" "}
          <a href={`/locations/${data.slug}`}>{data.name}</a> /{" "}
          <a href={`/locations/${data.slug}/events`}>Журнал</a> / Протокол
        </p>
        <div className="loc-header-title">
          <h1>
            {data.name} — протокол {data.event_number ? `№${data.event_number}` : "старта"}
          </h1>
          {sheet !== null && data.summary.finishers > 0 && (
            <button
              type="button"
              className="s2-trigger"
              onClick={() => sheet.open({ subject: locationProtocolSubject(data), entry: "location" })}
            >
              📤 Поделиться
            </button>
          )}
        </div>
        <p className="protocol-subtitle">
          <PlatformBadge code={data.platform_code} />{" "}
          <span>{formatDate(data.event_date)}</span>
          {data.overall_number !== null && (
            <StatHintTooltip text="Сквозной номер: какой это по счёту сбор локации за всю историю, по всем системам вместе">
              <span className="muted"> · {data.overall_number}-й сбор площадки</span>
            </StatHintTooltip>
          )}
        </p>
        <nav className="protocol-nav" aria-label="Соседние старты">
          {data.previous ? (
            <a href={protocolHref(data.slug, data.previous.platform_code, data.previous.event_date)}>
              ← {formatDate(data.previous.event_date)}
              {data.previous.platform_code !== data.platform_code
                ? ` (${platformCodeLabel(data.previous.platform_code)})`
                : ""}
            </a>
          ) : (
            <span className="muted">первый старт</span>
          )}
          <a href={`/locations/${data.slug}/events`}>журнал</a>
          {/* Тот же старт в масштабе страны: где эти времена в общем протоколе недели. */}
          <a href={unifiedWeekHref(data.event_date)}>единый протокол недели</a>
          {data.next ? (
            <a href={protocolHref(data.slug, data.next.platform_code, data.next.event_date)}>
              {formatDate(data.next.event_date)}
              {data.next.platform_code !== data.platform_code
                ? ` (${platformCodeLabel(data.next.platform_code)})`
                : ""}{" "}
              →
            </a>
          ) : (
            <span className="muted">последний старт</span>
          )}
        </nav>
      </header>

      <div className="loc-stats-grid protocol-stats-grid">
        <StatTile
          value={formatInt(summary.finishers)}
          label="финишёров"
          delta={
            data.previous?.finishers != null ? summary.finishers - data.previous.finishers : null
          }
          sub={
            genderKnown
              ? `${formatInt(summary.male)} М · ${formatInt(summary.female)} Ж${
                  summary.unknown_gender ? ` · ${formatInt(summary.unknown_gender)} без данных` : ""
                }`
              : null
          }
          badge={
            summary.is_attendance_record
              ? "Рекорд посещаемости локации на момент этого старта"
              : undefined
          }
        />
        <StatTile
          value={stripHours(summary.best_male_time_display)}
          label="лучшее время (М)"
          sub={summary.best_male_runner_name}
          delta={
            summary.best_time_sec !== null &&
            summary.best_male_time_display !== null &&
            data.previous?.best_male_time_sec != null &&
            bestMaleSec !== null
              ? bestMaleSec - data.previous.best_male_time_sec
              : null
          }
          deltaKind="seconds"
          deltaHint="Разница с лучшим мужским временем предыдущего старта"
          badge={
            summary.is_course_record_male
              ? "Новый рекорд трассы среди мужчин на момент этого старта"
              : undefined
          }
        />
        <StatTile
          value={stripHours(summary.best_female_time_display)}
          label="лучшее время (Ж)"
          sub={summary.best_female_runner_name}
          delta={
            data.previous?.best_female_time_sec != null && bestFemaleSec !== null
              ? bestFemaleSec - data.previous.best_female_time_sec
              : null
          }
          deltaKind="seconds"
          deltaHint="Разница с лучшим женским временем предыдущего старта"
          badge={
            summary.is_course_record_female
              ? "Новый рекорд трассы среди женщин на момент этого старта"
              : undefined
          }
        />
        <StatTile
          value={stripHours(summary.median_time_display)}
          label="медианное время"
          sub={summary.avg_time_display ? `среднее ${stripHours(summary.avg_time_display)}` : null}
          delta={
            data.previous?.avg_time_sec != null && summary.avg_time_sec !== null
              ? summary.avg_time_sec - data.previous.avg_time_sec
              : null
          }
          deltaKind="seconds"
          deltaHint="Среднее время против предыдущего старта"
          hint="Медиана: половина участников финишировала быстрее этого времени, половина — медленнее"
        />
        <StatTile
          value={newcomers ? formatInt(newcomers) : summary.finishers ? "0" : null}
          label="новичков"
          delta={
            data.previous?.debutants != null || data.previous?.first_at_location != null
              ? newcomers -
                ((data.previous?.debutants ?? 0) + (data.previous?.first_at_location ?? 0))
              : null
          }
          sub={
            newcomers
              ? `${formatInt(summary.debutants)} дебют · ${formatInt(summary.first_at_location)} впервые здесь`
              : null
          }
          hint="Дебютанты движения + участники, впервые пришедшие на эту локацию"
        />
        <StatTile
          value={summary.prs ? formatInt(summary.prs) : summary.finishers ? "0" : null}
          label="личных рекордов"
          delta={data.previous?.prs != null ? summary.prs - data.previous.prs : null}
          hint="Сколько участников улучшили в этот день своё лучшее время в системе"
        />
        <StatTile
          value={formatInt(summary.volunteers)}
          label="волонтёров"
          delta={
            data.previous?.volunteers != null ? summary.volunteers - data.previous.volunteers : null
          }
        />
      </div>

      {data.is_partial && (
        <p className="protocol-partial-note muted">
          Протокол неполный: в нашей базе {formatInt(data.results.length)}{" "}
          {data.declared_finishers
            ? `из ${formatInt(data.declared_finishers)} финишёров`
            : "строк, часть позиций отсутствует"}
          {/* Дыра в половину протокола и больше — зарубежный parkrun, где мы
              собираем только своих; пара потерянных строк у русского
              исторического протокола такого объяснения не заслуживает. */}
          {data.platform_code === "parkrun" && mostlyMissing
            ? " — по зарубежным паркранам мы собираем только результаты участников наших систем"
            : ""}
          .
        </p>
      )}

      {histogramRows.length > 0 && summary.finishers >= 10 && (
        <section className="loc-section">
          <h2>Распределение времён</h2>
          <p className="muted protocol-histogram-note">
            Финишные времена этого старта. Тап по столбику — детали, растянуть — приблизить.
          </p>
          <LocationFinishHistogram rows={histogramRows} binSizeSec={10} />
        </section>
      )}

      {(data.age_groups.length > 0 || summary.top_clubs.length > 0) && (
        <div
          className={
            data.age_groups.length > 0 && summary.top_clubs.length > 0
              ? "protocol-columns"
              : undefined
          }
        >
          {data.age_groups.length > 0 && (
            <section className="loc-section protocol-column">
              <h2>Возрастные группы</h2>
              <div className="protocol-age-groups">
                {data.age_groups.map((group) => {
                  const max = Math.max(...data.age_groups.map((item) => item.total), 1);
                  return (
                    <div className="protocol-age-row" key={group.age_group}>
                      <span className="protocol-age-label">{group.age_group}</span>
                      <span className="protocol-age-bar-track">
                        <span
                          className="protocol-age-bar protocol-age-bar-male"
                          style={{ width: `${(group.male / max) * 100}%` }}
                        />
                        <span
                          className="protocol-age-bar protocol-age-bar-female"
                          style={{ width: `${(group.female / max) * 100}%` }}
                        />
                      </span>
                      {/* Числа колонками с выравниванием по правому краю —
                          «66 М» под «55 М», а не пляшущие скобки. */}
                      <span className="protocol-age-total">{formatInt(group.total)}</span>
                      <span className="protocol-age-gender muted">{formatInt(group.male)} М</span>
                      <span className="protocol-age-gender muted">{formatInt(group.female)} Ж</span>
                    </div>
                  );
                })}
              </div>
            </section>
          )}
          {summary.top_clubs.length > 0 && (
            <section className="loc-section protocol-column protocol-clubs">
              {/* Блок держит высоту соседних возрастных групп, список клубов
                  скроллится внутри. */}
              <div className="protocol-clubs-frame">
                <h2>Клубы на старте ({formatInt(summary.clubs_count)})</h2>
                <div className="protocol-clubs-scroll">
                  <table className="data-table protocol-clubs-table">
                    <thead>
                      <tr>
                        <th>Клуб</th>
                        <th className="protocol-clubs-count">Финишёров</th>
                      </tr>
                    </thead>
                    <tbody>
                      {summary.top_clubs.map((club) => (
                        <tr
                          key={club.name}
                          className={
                            clubFilter === club.name
                              ? "protocol-club-row protocol-club-row-active"
                              : "protocol-club-row"
                          }
                        >
                          <td>
                            {/* Клик по клубу оставляет в протоколе только его
                                бегунов; повторный — снимает фильтр. */}
                            <button
                              type="button"
                              className="protocol-club-button"
                              onClick={() => toggleClubFilter(club.name)}
                              aria-pressed={clubFilter === club.name}
                            >
                              {club.name}
                            </button>
                          </td>
                          <td className="protocol-clubs-count">{formatInt(club.count)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>
            </section>
          )}
        </div>
      )}

      <section className="loc-section" ref={protocolSectionRef}>
        <h2>Протокол</h2>
        <div className="protocol-filters">
          <div className="map-mode-tabs" role="tablist" aria-label="Фильтр по полу">
            {(
              [
                ["all", `Все (${data.results.length})`],
                ["male", `Мужчины (${summary.male})`],
                ["female", `Женщины (${summary.female})`],
              ] as [GenderFilter, string][]
            ).map(([key, label]) => (
              <button
                key={key}
                type="button"
                role="tab"
                aria-selected={genderFilter === key}
                className={genderFilter === key ? "map-mode-tab active" : "map-mode-tab"}
                onClick={() => setGenderFilter(key)}
                disabled={key !== "all" && !genderKnown}
              >
                {label}
              </button>
            ))}
          </div>
          {ageCategories.length > 1 && (
            <select
              className="protocol-age-select"
              value={ageFilter ?? ""}
              onChange={(event) => setAgeFilter(event.target.value || null)}
              aria-label="Фильтр по возрастной группе"
            >
              <option value="">Все группы</option>
              {ageCategories.map(([category, count]) => (
                <option key={category} value={category}>
                  {category} ({count})
                </option>
              ))}
            </select>
          )}
          <input
            type="search"
            className="protocol-name-filter"
            placeholder="Поиск по имени"
            value={nameFilter}
            onChange={(event) => setNameFilter(event.target.value)}
            aria-label="Поиск по имени"
          />
          {clubFilter && (
            <button
              type="button"
              className="protocol-club-chip"
              onClick={() => setClubFilter(null)}
              title="Снять фильтр по клубу"
            >
              Клуб: {clubFilter} ✕
            </button>
          )}
        </div>

        <TableViewToggle columns={tableColumns} />
        <TableWrap
          innerRef={attachFloatingHead}
          outerRef={tableColumns.measureRef}
          className="protocol-table-wrap"
          stickyFirstCol={showFull}
        >
          <table
            className={`data-table data-table-layout-fixed protocol-table${
              showFull ? "" : " data-table-short"
            }`}
            style={showFull ? undefined : { minWidth: tableColumns.minWidth }}
          >
            <colgroup>
              <col className="col-number" />
              <col />
              <col className="col-time" />
              {show("gender_place") && <col className="col-gender" />}
              {show("age_category") && <col className="col-age" />}
              {show("run_number") && <col className="col-run-number" />}
              {hasAgeGrade && show("age_grade") && <col className="col-grade" />}
              {hasPace && show("pace") && <col className="col-pace" />}
              {hasClubs && show("club") && <col className="col-club" />}
              {show("history") && <col className="col-history protocol-col-history" />}
              {hasAgeCategories && show("history_group") && (
                <col className="col-history-group protocol-col-history" />
              )}
            </colgroup>
            <thead>
              <tr>
                <ColumnHeader label="№" {...sortProps("position")} />
                <ColumnHeader label="Участник" filterable={false} />
                <ColumnHeader label="Время" hint="Наведите на время: место результата в истории площадки" {...sortProps("time")} />
                {show("gender_place") && (
                  <ColumnHeader
                    label="М/Ж"
                    hint="Место среди своего пола на этом старте"
                    filterable={false}
                  />
                )}
                {show("age_category") && (
                  <ColumnHeader
                    label="Возр. группа"
                    hint="Возрастная группа из протокола и место в ней на этом старте"
                    filterable={false}
                  />
                )}
                {show("run_number") && (
                  <ColumnHeader
                    label="Пробежка"
                    hint="Какая это пробежка по счёту у участника: у зарегистрированных на сайте — сквозь все системы, у остальных — в своей"
                    filterable={false}
                  />
                )}
                {hasAgeGrade && show("age_grade") && (
                  <ColumnHeader
                    label="Age grade"
                    hint="Возрастной коэффициент parkrun: результат относительно мирового уровня своего возраста и пола"
                    {...sortProps("age_grade")}
                  />
                )}
                {hasPace && show("pace") && <ColumnHeader label="Темп" filterable={false} />}
                {hasClubs && show("club") && <ColumnHeader label="Клуб" filterable={false} />}
                {show("history") && (
                  <ColumnHeader
                    className="protocol-cell-history"
                    label="В истории М/Ж"
                    hint="Место результата среди всех времён своего пола за историю площадки, по всем системам"
                    {...sortProps("history")}
                  />
                )}
                {hasAgeCategories && show("history_group") && (
                  <ColumnHeader
                    className="protocol-cell-history"
                    label="В истории группы"
                    hint="Место результата среди всех времён своей возрастной группы за историю площадки"
                    filterable={false}
                  />
                )}
              </tr>
            </thead>
            <tbody>
              {rows.length === 0 ? (
                <tr>
                  <td colSpan={visibleCount} className="table-empty-cell">
                    <span className="muted">
                      {data.results.length === 0
                        ? "Полного протокола этого старта в базе нет"
                        : "Никого не нашлось по выбранным фильтрам"}
                    </span>
                  </td>
                </tr>
              ) : (
                rows.map((row) => (
                  <tr
                    key={`${row.position ?? "x"}-${row.external_user_id ?? row.name ?? ""}`}
                    className={
                      row.is_me
                        ? "protocol-row-me"
                        : row.is_unknown
                          ? "protocol-row-unknown"
                          : undefined
                    }
                  >
                    <td className="td-compact">{row.position ?? "—"}</td>
                    <td>
                      <span className="protocol-runner">
                        {row.serial_id !== null ? (
                          <a href={`/users/${row.serial_id}`}>{row.name ?? "—"}</a>
                        ) : (
                          <span>{row.name ?? "—"}</span>
                        )}
                        {row.is_me && <span className="protocol-row-badge protocol-badge-me">вы</span>}
                        {row.is_first_run && <RowBadge text="дебют" title="Первый старт в системе" />}
                        {!row.is_first_run && row.is_first_run_at_location && (
                          <RowBadge text="впервые здесь" title="Первый старт на этой локации" />
                        )}
                        {row.is_pr && (
                          <RowBadge text="ЛР" title="Личный рекорд в системе на этом старте" />
                        )}
                        {((row.gender === "male" && row.name === courseRecordMale) ||
                          (row.gender === "female" && row.name === courseRecordFemale)) && (
                          <RowBadge
                            text="🏆 рекорд трассы"
                            title="Новый рекорд трассы на момент этого старта"
                          />
                        )}
                        {row.is_age_group_record && (
                          <RowBadge
                            text="🎖 рекорд группы"
                            title={`Новый рекорд площадки в группе ${row.age_category ?? ""} на момент этого старта`}
                          />
                        )}
                      </span>
                    </td>
                    <td className="td-time">{stripHours(row.finish_time_display)}</td>
                    {show("gender_place") && (
                      <td className="td-compact">
                        {row.gender_position !== null ? (
                          <span className="protocol-place">
                            {medal(row.gender_position)}
                            {row.gender_position}
                            {row.gender_total ? ` / ${row.gender_total}` : ""}
                          </span>
                        ) : (
                          "—"
                        )}
                      </td>
                    )}
                    {show("age_category") && (
                      <td className="td-compact">
                        {row.age_category ? (
                          <span className="protocol-place">
                            {row.age_category}
                            {row.age_group_position !== null && (
                              <span className="muted">
                                {" "}
                                {/* Медаль — только победителю группы: три медали на
                                    каждую из ~30 групп превращали колонку в шум. */}
                                · {row.age_group_position === 1 ? "🥇" : ""}
                                {row.age_group_position}
                                {row.age_group_total ? `/${row.age_group_total}` : ""}
                              </span>
                            )}
                          </span>
                        ) : (
                          "—"
                        )}
                      </td>
                    )}
                    {show("run_number") && (
                      <td className="td-compact">
                        {row.run_number !== null ? (
                          <StatHintTooltip
                            text={
                              row.run_number_all_systems
                                ? `${formatInt(row.run_number)}-я пробежка по всем системам участника`
                                : `${formatInt(row.run_number)}-я пробежка в системе ${platformCodeLabel(data.platform_code)}`
                            }
                          >
                            <span className="protocol-place">{formatInt(row.run_number)}-я</span>
                          </StatHintTooltip>
                        ) : (
                          "—"
                        )}
                      </td>
                    )}
                    {hasAgeGrade && show("age_grade") && (
                      <td className="td-compact">
                        {row.age_grade !== null ? `${row.age_grade.toFixed(2)}%` : "—"}
                      </td>
                    )}
                    {hasPace && show("pace") && (
                      <td className="td-compact">{row.pace_display ?? "—"}</td>
                    )}
                    {hasClubs && show("club") && (
                      <td className="td-compact">
                        {row.club_name ? (
                          <button
                            type="button"
                            className="protocol-club-button"
                            onClick={() => toggleClubFilter(row.club_name as string)}
                            aria-pressed={clubFilter === row.club_name}
                          >
                            {row.club_name}
                          </button>
                        ) : (
                          "—"
                        )}
                      </td>
                    )}
                    {show("history") && (
                      <td className="td-compact protocol-cell-history">
                        {row.history_rank !== null ? (
                          <StatHintTooltip
                            text={`${formatInt(row.history_rank)}-е время среди ${
                              row.gender === "female" ? "женщин" : "мужчин"
                            } за всю историю площадки (из ${formatInt(row.history_total ?? 0)})`}
                          >
                            <span
                              className={
                                row.history_rank <= 10
                                  ? "protocol-place protocol-history-top"
                                  : "protocol-place"
                              }
                            >
                              {row.history_rank <= 10 ? "🏅" : ""}
                              {formatInt(row.history_rank)}
                              <span className="muted"> / {formatInt(row.history_total ?? 0)}</span>
                            </span>
                          </StatHintTooltip>
                        ) : (
                          "—"
                        )}
                      </td>
                    )}
                    {hasAgeCategories && show("history_group") && (
                      <td className="td-compact protocol-cell-history">
                        {row.age_group_history_rank !== null ? (
                          <StatHintTooltip
                            text={`${formatInt(row.age_group_history_rank)}-е время в группе ${
                              row.age_category ?? row.age_group ?? ""
                            } за всю историю площадки (из ${formatInt(row.age_group_history_total ?? 0)})`}
                          >
                            <span
                              className={
                                row.age_group_history_rank <= 10
                                  ? "protocol-place protocol-history-top"
                                  : "protocol-place"
                              }
                            >
                              {row.age_group_history_rank <= 10 ? "🏅" : ""}
                              {formatInt(row.age_group_history_rank)}
                              <span className="muted">
                                {" "}
                                / {formatInt(row.age_group_history_total ?? 0)}
                              </span>
                            </span>
                          </StatHintTooltip>
                        ) : (
                          "—"
                        )}
                      </td>
                    )}
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </TableWrap>
      </section>

      {data.volunteers.length > 0 && (
        <section className="loc-section">
          <h2>Волонтёры ({data.volunteers.length})</h2>
          {volunteerRoles.length > 1 && (
            <div className="protocol-filters">
              <select
                className="protocol-age-select"
                value={roleFilter ?? ""}
                onChange={(event) => setRoleFilter(event.target.value || null)}
                aria-label="Фильтр по роли"
              >
                <option value="">Все роли</option>
                {volunteerRoles.map(([role, count]) => (
                  <option key={role} value={role}>
                    {role} ({count})
                  </option>
                ))}
              </select>
            </div>
          )}
          <TableWrap className="protocol-volunteers-wrap">
            <table className="data-table data-table-layout-fixed protocol-volunteers-table">
              <colgroup>
                <col className="col-vol-name" />
                <col />
                <col className="col-vol-number" />
              </colgroup>
              <thead>
                <tr>
                  <ColumnHeader label="Волонтёр" filterable={false} />
                  <ColumnHeader label="Роли на этом старте" filterable={false} />
                  <ColumnHeader
                    label="Волонтёрство"
                    hint="Какое это волонтёрство по счёту у человека в этой системе"
                    filterable={false}
                  />
                </tr>
              </thead>
              <tbody>
                {volunteerRows.length === 0 ? (
                  <tr>
                    <td colSpan={3} className="table-empty-cell">
                      <span className="muted">Никого с такой ролью на этом старте</span>
                    </td>
                  </tr>
                ) : null}
                {volunteerRows.map((person, index) => (
                  <tr key={`${person.external_user_id ?? index}`}>
                    <td>
                      <span className="protocol-runner">
                        {person.serial_id !== null ? (
                          <a href={`/users/${person.serial_id}`}>{person.name ?? "—"}</a>
                        ) : (
                          <span>{person.name ?? "—"}</span>
                        )}
                        {person.is_me && (
                          <span className="protocol-row-badge protocol-badge-me">вы</span>
                        )}
                        {person.is_first_volunteering ? (
                          <RowBadge text="дебют" title="Первое волонтёрство в системе" />
                        ) : (
                          person.is_first_here && (
                            <RowBadge
                              text="впервые здесь"
                              title="Первое волонтёрство на этой локации"
                            />
                          )
                        )}
                      </span>
                    </td>
                    <td>
                      <span className="protocol-roles">
                        {person.roles.map((role) =>
                          person.new_roles.includes(role) ? (
                            <StatHintTooltip key={role} text="Впервые в этой роли">
                              <span className="protocol-role-chip protocol-role-chip-new">
                                {role} ✦
                              </span>
                            </StatHintTooltip>
                          ) : (
                            <span key={role} className="protocol-role-chip">
                              {role}
                            </span>
                          ),
                        )}
                      </span>
                    </td>
                    <td className="td-compact">
                      {person.volunteer_number !== null
                        ? `${formatInt(person.volunteer_number)}-е`
                        : "—"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </TableWrap>
        </section>
      )}

      {data.source_url && (
        <p className="muted protocol-source-link">
          <a href={data.source_url} target="_blank" rel="noreferrer">
            Протокол на сайте {platformCodeLabel(data.platform_code)} →
          </a>
        </p>
      )}
      <ScrollToTopButton />
    </PortalSectionShell>
  );
}

// Протокол открыт без логина, как и вся витрина локаций; логин добавляет
// только подсветку своей строки.
export function LocationProtocolPage(params: LocationProtocolParams) {
  return <LocationProtocolContent {...params} />;
}
