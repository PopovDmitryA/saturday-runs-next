import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { ColumnHeader } from "../../components/activityTable/ColumnHeader";
import { PlatformBadge } from "../../components/PlatformBadge";
import { ScrollToTopButton } from "../../components/ScrollToTopButton";
import { StatHintTooltip } from "../../components/StatHintTooltip";
import { TableWrap } from "../../components/tableUx/TableWrap";
import {
  FilterGroup,
  FilterPanel,
  FilterRow,
  FilterSearch,
} from "../../components/filters/FilterPanel";
import { GenderFilter } from "../../components/filters/GenderFilter";
import { PlatformFilter } from "../../components/filters/PlatformFilter";
import { TableViewToggle } from "../../components/tableUx/TableViewToggle";
import { useTableColumns } from "../../components/tableUx/useTableColumns";
import type { AdaptiveColumn } from "../../components/tableUx/useAdaptiveColumns";
import { PortalSectionShell } from "../portal/PortalSectionShell";
import { PromoLoginCard } from "../../components/PromoLoginCard";
import { useOptionalUser } from "../../lib/useOptionalUser";
import {
  getUnifiedProtocol,
  getUnifiedProtocolWeeks,
  type UnifiedProtocol,
  type UnifiedProtocolRow,
  type UnifiedProtocolWeekRef,
} from "../../lib/api";
import { formatDate, formatInt } from "../../lib/format";
import { useFloatingTableHead } from "../../lib/useFloatingTableHead";
import { flushMetrikaHit } from "../../lib/metrika";

type GenderFilter = "all" | "male" | "female";

export type UnifiedProtocolParams = {
  /** Любой день недели; без даты — последняя неделя с данными. */
  saturday: string | null;
};

const PER_PAGE = 100;

export function unifiedProtocolHref(saturday: string | null): string {
  return saturday ? `/protocol/${saturday}` : "/protocol";
}

/** «00:18:08» → «18:08» — часовых времён на пятёрке не бывает. */
function stripHours(display: string | null): string {
  if (!display) {
    return "—";
  }
  return display.replace(/^00:/, "");
}

/** 🥇🥈🥉 первым трём — видно сразу, без наведения. */
function medal(place: number | null): string | null {
  if (place === 1) return "🥇";
  if (place === 2) return "🥈";
  if (place === 3) return "🥉";
  return null;
}

/** В возрастной группе — только золото: групп под сотню, три медали в каждой
 *  превратили бы страницу в ёлку. */
function groupMedal(place: number | null): string {
  return place === 1 ? "🥇" : "";
}

function StatTile({
  value,
  label,
  sub,
  hint,
}: {
  value: string | number | null;
  label: string;
  sub?: ReactNode;
  hint?: string;
}) {
  return (
    <div className="stat-card loc-stat-card">
      <span className="stat-value loc-stat-value">
        {value ?? "—"}
        {hint && (
          <StatHintTooltip text={hint}>
            <span className="loc-stat-info" aria-label="Как считается">
              i
            </span>
          </StatHintTooltip>
        )}
      </span>
      <span className="stat-label">{label}</span>
      {sub && <span className="loc-stat-sub">{sub}</span>}
    </div>
  );
}

// Колонки в порядке важности; место, участник и время — минимум краткого вида.
// Ширины — зеркало CSS-классов .uniprot-table .col-*.
const UNIFIED_COLUMNS: AdaptiveColumn[] = [
  { key: "place", width: 67, required: true },
  { key: "runner", width: 168, required: true },
  { key: "time", width: 96, required: true },
  { key: "location", width: 208 },
  { key: "gender_place", width: 122 },
  { key: "age_group", width: 184 },
  { key: "platform", width: 109 },
  { key: "pace", width: 88 },
  { key: "location_position", width: 115 },
  { key: "club", width: 168 },
];

function RunnerCell({ row }: { row: UnifiedProtocolRow }) {
  return (
    <span className="protocol-runner">
      {row.serial_id !== null ? (
        <a href={`/users/${row.serial_id}`}>{row.name ?? "—"}</a>
      ) : (
        <span>{row.name ?? "—"}</span>
      )}
      {row.is_me && <span className="protocol-row-badge protocol-badge-me">вы</span>}
      {row.is_first_run && (
        <StatHintTooltip text="Первый старт в системе">
          <span className="protocol-row-badge">дебют</span>
        </StatHintTooltip>
      )}
      {row.is_pr && (
        <StatHintTooltip text="Личный рекорд в системе на этом старте">
          <span className="protocol-row-badge">ЛР</span>
        </StatHintTooltip>
      )}
    </span>
  );
}

function LocationCell({ row }: { row: UnifiedProtocolRow }) {
  const title = row.location_name || "—";
  return (
    <span className="uniprot-location">
      {row.location_slug ? (
        <a href={`/locations/${encodeURIComponent(row.location_slug)}`}>{title}</a>
      ) : (
        <span>{title}</span>
      )}
      {row.city && row.city !== title && <span className="muted"> · {row.city}</span>}
    </span>
  );
}

function UnifiedProtocolContent({ saturday }: UnifiedProtocolParams) {
  // undefined — сессия ещё проверяется: пока не знаем, баннер не мигаем.
  const viewer = useOptionalUser();
  const [data, setData] = useState<UnifiedProtocol | null>(null);
  const [weeks, setWeeks] = useState<UnifiedProtocolWeekRef[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [platform, setPlatform] = useState<string | null>(null);
  const [gender, setGender] = useState<GenderFilter>("all");
  const [ageGroup, setAgeGroup] = useState<string | null>(null);
  const [search, setSearch] = useState("");
  const [query, setQuery] = useState("");
  const [page, setPage] = useState(1);
  const tableSectionRef = useRef<HTMLElement | null>(null);
  const attachFloatingHead = useFloatingTableHead(".tview-bar");
  const tableColumns = useTableColumns(UNIFIED_COLUMNS);
  const show = tableColumns.show;
  const showFull = tableColumns.showFull;

  // Поиск по имени дёргает сервер — ждём паузы в наборе.
  useEffect(() => {
    const timer = window.setTimeout(() => setQuery(search.trim()), 350);
    return () => window.clearTimeout(timer);
  }, [search]);

  // Смена недели — это новая страница: сбрасываем всё, кроме выбранной системы
  // (её обычно хотят удержать, листая недели).
  useEffect(() => {
    setGender("all");
    setAgeGroup(null);
    setSearch("");
    setQuery("");
    setPage(1);
  }, [saturday]);

  useEffect(() => {
    setPage(1);
  }, [platform, gender, ageGroup, query]);

  // Смена системы обнуляет возрастную группу: ступени у систем разные, а у S95
  // категорий нет вовсе — иначе фильтр оставался бы висеть невидимым и
  // протокол пустовал бы без объяснений.
  useEffect(() => {
    setAgeGroup(null);
  }, [platform]);

  useEffect(() => {
    let cancelled = false;
    getUnifiedProtocolWeeks()
      .then((payload) => {
        if (!cancelled) {
          setWeeks(payload.weeks);
        }
      })
      .catch(() => {
        /* без списка недель страница живёт: остаются стрелки «пред./след.» */
      });
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    getUnifiedProtocol(saturday, {
      platform,
      gender: gender === "all" ? null : gender,
      ageGroup,
      q: query || null,
      page,
      perPage: PER_PAGE,
    })
      .then((payload) => {
        if (!cancelled) {
          setData(payload);
          setLoading(false);
          flushMetrikaHit();
        }
      })
      .catch((err) => {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : "Не удалось загрузить протокол");
          setLoading(false);
          flushMetrikaHit();
        }
      });
    return () => {
      cancelled = true;
    };
  }, [saturday, platform, gender, ageGroup, query, page]);

  const goToPage = useCallback((next: number) => {
    setPage(next);
    tableSectionRef.current?.scrollIntoView({ behavior: "smooth", block: "start" });
  }, []);

  const weekOptions = useMemo(() => [...weeks].reverse(), [weeks]);

  if (error) {
    return (
      <PortalSectionShell sidebar={{ active: "unified-protocol" }}>
        <div className="card error">
          <p>{error}</p>
        </div>
      </PortalSectionShell>
    );
  }

  if (!data) {
    return (
      <PortalSectionShell sidebar={{ active: "unified-protocol" }}>
        <p className="muted">Собираем протокол недели…</p>
      </PortalSectionShell>
    );
  }

  const summary = data.summary;
  const scopeTitle =
    data.platforms.find((item) => item.platform_code === data.scope_platform)?.title ?? null;
  // Подпись зачёта собирается из всех трёх ограничений: «5 вёрст · женщины ·
  // 40–44». Она же объясняет, почему «№» в таблице начинается с единицы.
  const scopeParts = [
    scopeTitle,
    gender === "male" ? "мужчины" : gender === "female" ? "женщины" : null,
    ageGroup,
  ].filter(Boolean);
  // Подпись зачёта показываем только когда он сужен: «зачёт: все системы» —
  // пустой шум, а вот «5 вёрст · женщины · 40–44» объясняет, почему «№»
  // начинается с единицы.
  const scopeLabel = scopeParts.join(" · ");
  const scopeHintLabel = scopeLabel || "все системы";
  const genderCounts = data.gender_counts;
  const genderKnown = genderCounts.male + genderCounts.female > 0;
  const hasAgeGroups = data.age_groups.length > 0;
  const hasClubs = summary.clubs_count > 0;
  const hasPace = data.results.some((row) => row.pace_display);
  const visibleCount =
    3 +
    (show("location") ? 1 : 0) +
    (show("gender_place") ? 1 : 0) +
    (hasAgeGroups && show("age_group") ? 1 : 0) +
    (show("platform") ? 1 : 0) +
    (hasPace && show("pace") ? 1 : 0) +
    (show("location_position") ? 1 : 0) +
    (hasClubs && show("club") ? 1 : 0);

  const renderRow = (row: UnifiedProtocolRow, keyPrefix: string) => (
    <tr
      key={`${keyPrefix}-${row.place ?? "x"}-${row.platform_code}-${row.external_user_id ?? row.name ?? ""}`}
      className={row.is_me ? "protocol-row-me" : row.is_unknown ? "protocol-row-unknown" : undefined}
    >
      <td className="td-compact">
        {medal(row.place) ?? ""}
        {row.place ?? "—"}
      </td>
      <td>
        <RunnerCell row={row} />
      </td>
      <td className="td-compact">{stripHours(row.finish_time_display)}</td>
      {show("location") && (
        <td>
          <LocationCell row={row} />
        </td>
      )}
      {show("gender_place") && (
        <td className="td-compact">
          {row.gender_place === null ? (
            "—"
          ) : (
            <span className="uniprot-place">
              {medal(row.gender_place) ?? ""}
              {row.gender === "female" ? "Ж" : "М"} {row.gender_place}
              <span className="muted"> / {formatInt(row.gender_total ?? 0)}</span>
            </span>
          )}
        </td>
      )}
      {hasAgeGroups && show("age_group") && (
        <td className="td-compact">
          {row.age_group === null ? (
            "—"
          ) : (
            <span className="uniprot-place">
              {groupMedal(row.age_group_place)}
              {row.age_category ?? row.age_group}
              {row.age_group_place !== null && (
                <span className="muted">
                  {" "}
                  · {row.age_group_place} / {formatInt(row.age_group_total ?? 0)}
                </span>
              )}
            </span>
          )}
        </td>
      )}
      {show("platform") && (
        <td className="td-compact">
          <PlatformBadge code={row.platform_code} />
        </td>
      )}
      {hasPace && show("pace") && <td className="td-compact">{row.pace_display ?? "—"}</td>}
      {show("location_position") && (
        <td className="td-compact">
          {row.location_position ?? "—"}
        </td>
      )}
      {hasClubs && show("club") && <td>{row.club_name ?? "—"}</td>}
    </tr>
  );

  return (
    <PortalSectionShell sidebar={{ active: "unified-protocol" }}>
      <header className="loc-header loc-wide-page">
        <p className="muted loc-header-breadcrumb">
          <a href="/results">← Результаты</a> / Единый протокол
        </p>
        <h1>Единый протокол — {formatDate(data.saturday)}</h1>
        <p className="protocol-subtitle">
          Все площадки всех систем за одну неделю, выстроенные по времени финиша.
        </p>
        <nav className="protocol-nav" aria-label="Выбор недели">
          {data.previous_saturday ? (
            <a href={unifiedProtocolHref(data.previous_saturday)}>
              ← {formatDate(data.previous_saturday)}
            </a>
          ) : (
            <span className="muted">первая неделя</span>
          )}
          {weekOptions.length > 1 && (
            <select
              className="protocol-age-select uniprot-week-select"
              value={data.saturday}
              onChange={(event) => {
                window.location.href = unifiedProtocolHref(event.target.value || null);
              }}
              aria-label="Неделя"
            >
              {weekOptions.map((week) => (
                <option key={week.saturday} value={week.saturday}>
                  {formatDate(week.saturday)} · {formatInt(week.finishers)}
                </option>
              ))}
            </select>
          )}
          {data.next_saturday ? (
            <a href={unifiedProtocolHref(data.next_saturday)}>
              {formatDate(data.next_saturday)} →
            </a>
          ) : (
            <span className="muted">последняя неделя</span>
          )}
        </nav>
      </header>

      <div className="loc-stats-grid protocol-stats-grid uniprot-stats-grid">
        <StatTile
          value={formatInt(summary.finishers)}
          label="финишёров"
          sub={
            genderKnown ? (
              // Раскладкой, а не строкой: «6 885 М · 4 713 Ж · 903 без данных»
              // сливалось в кашу и переносилось по случайному месту.
              <span className="uniprot-stat-split">
                <span className="uniprot-stat-split-part">
                  <b>{formatInt(summary.male)}</b> М
                </span>
                <span className="uniprot-stat-split-part">
                  <b>{formatInt(summary.female)}</b> Ж
                </span>
                {summary.unknown_gender > 0 && (
                  <span className="uniprot-stat-split-part uniprot-stat-split-rest">
                    <b>{formatInt(summary.unknown_gender)}</b> без данных
                    {summary.finishers > 0 &&
                      ` (${Math.round((summary.unknown_gender / summary.finishers) * 100)}%)`}
                  </span>
                )}
              </span>
            ) : null
          }
        />
        <StatTile
          value={formatInt(summary.locations)}
          label="локаций"
          sub={scopeLabel || null}
        />
        {/* Волонтёры — при старте, а не при результате: у волонтёрства нет ни
            времени, ни возрастной группы, поэтому цифра не сужается срезом. */}
        <StatTile
          value={formatInt(summary.volunteer_people)}
          label="волонтёров"
          sub={
            summary.volunteers > summary.volunteer_people
              ? `${formatInt(summary.volunteers)} волонтёрств`
              : null
          }
          hint="Люди, а не роли: за один старт волонтёр часто берёт несколько ролей"
        />
        <StatTile
          value={stripHours(summary.median_time_display)}
          label="медианное время"
          sub={summary.avg_time_display ? `среднее ${stripHours(summary.avg_time_display)}` : null}
          hint="Медиана: половина страны финишировала быстрее этого времени, половина — медленнее"
        />
        {gender !== "female" && (
          <StatTile
            value={stripHours(summary.best_male?.time_display ?? null)}
            label="лучшее время (М)"
            sub={
              summary.best_male
                ? `${summary.best_male.name ?? ""} · ${summary.best_male.location_name}`
                : null
            }
          />
        )}
        {gender !== "male" && (
          <StatTile
            value={stripHours(summary.best_female?.time_display ?? null)}
            label="лучшее время (Ж)"
            sub={
              summary.best_female
                ? `${summary.best_female.name ?? ""} · ${summary.best_female.location_name}`
                : null
            }
          />
        )}
        <StatTile
          value={formatInt(summary.debutants)}
          label="новичков"
          sub={
            summary.scope_finishers > 0
              ? `${Math.round((summary.debutants / summary.scope_finishers) * 100)}% финишёров`
              : null
          }
          hint="Первый старт в своей системе"
        />
        <StatTile
          value={formatInt(summary.prs)}
          label="личных рекордов"
          sub={
            summary.scope_finishers > 0
              ? `${Math.round((summary.prs / summary.scope_finishers) * 100)}% финишёров`
              : null
          }
          hint="Результат лучше всех прежних в своей системе"
        />
      </div>

      {/* Анониму — на месте «Вашего результата» призыв войти: страница открыта
          всем, но найти в ней СЕБЯ можно только с привязанным профилем (тот же
          приём, что в рейтингах — RatingsLoginBanner). */}
      {viewer === null && (
        <PromoLoginCard
          className="uniprot-login-card"
          icon="🏁"
          title="А где здесь вы?"
          text="Войдите и привяжите профиль своей беговой системы — ваш результат этой недели поднимется наверх страницы и подсветится в протоколе: место по стране, среди своего пола и в возрастной группе."
        />
      )}

      {data.my_results.length > 0 && (
        <section className="loc-section">
          <h2>Ваш результат</h2>
          <TableWrap className="protocol-table-wrap">
            <table className="data-table uniprot-table">
              <thead>
                <tr>
                  <ColumnHeader label="№" filterable={false} />
                  <ColumnHeader label="Участник" filterable={false} />
                  <ColumnHeader label="Время" filterable={false} />
                  <ColumnHeader label="Локация" filterable={false} />
                  <ColumnHeader label="М/Ж" filterable={false} />
                  {hasAgeGroups && <ColumnHeader label="Возр. группа" filterable={false} />}
                  <ColumnHeader label="Система" filterable={false} />
                </tr>
              </thead>
              <tbody>
                {data.my_results.map((row) => (
                  <tr key={`me-${row.platform_code}-${row.place ?? "x"}`} className="protocol-row-me">
                    <td className="td-compact">{row.place ?? "—"}</td>
                    <td>
                      <RunnerCell row={row} />
                    </td>
                    <td className="td-compact">{stripHours(row.finish_time_display)}</td>
                    <td>
                      <LocationCell row={row} />
                    </td>
                    <td className="td-compact">
                      {row.gender_place === null
                        ? "—"
                        : `${row.gender === "female" ? "Ж" : "М"} ${row.gender_place} / ${formatInt(
                            row.gender_total ?? 0,
                          )}`}
                    </td>
                    {hasAgeGroups && (
                      <td className="td-compact">
                        {row.age_group_place === null
                          ? "—"
                          : `${row.age_category ?? row.age_group} · ${row.age_group_place} / ${formatInt(
                              row.age_group_total ?? 0,
                            )}`}
                      </td>
                    )}
                    <td className="td-compact">
                      <PlatformBadge code={row.platform_code} />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </TableWrap>
        </section>
      )}

      {hasAgeGroups && (
        <details className="card uniprot-groups-spoiler">
          <summary className="uniprot-groups-summary">
            Возрастные группы
            <span className="muted"> ({formatInt(data.age_groups.length)})</span>
            {ageGroup && <span className="uniprot-groups-chosen"> · выбрана {ageGroup}</span>}
            {/* Что со ступенями можно делать — иначе полосы читаются как
                картинка, а они кликабельные. */}
            <span className="uniprot-groups-howto">
              разверните и щёлкните по ступени — протокол пересоберётся внутри неё
            </span>
          </summary>
          <div className="uniprot-groups-body">
          <p className="muted uniprot-scope-note">
            Группа берётся из категории самого протокола, поэтому 5 вёрст и RunPark считаются в
            одних ступенях. У S95 категорий в протоколе нет, у parkrun вместо категории стоит age
            grade — их строки в групповом зачёте не участвуют.
          </p>
          <div className="protocol-age-groups">
            {data.age_groups.map((group) => {
              const max = Math.max(...data.age_groups.map((item) => item.total), 1);
              const active = ageGroup === group.age_group;
              return (
                <button
                  type="button"
                  className={
                    active ? "protocol-age-row uniprot-age-row active" : "protocol-age-row uniprot-age-row"
                  }
                  key={group.age_group}
                  aria-pressed={active}
                  onClick={() => setAgeGroup(active ? null : group.age_group)}
                  title={
                    active
                      ? "Снять фильтр по группе"
                      : `Оставить в протоколе только группу ${group.age_group}`
                  }
                >
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
                  <span className="protocol-age-total">{formatInt(group.total)}</span>
                  <span className="protocol-age-gender muted">{formatInt(group.male)} М</span>
                  <span className="protocol-age-gender muted">{formatInt(group.female)} Ж</span>
                </button>
              );
            })}
          </div>
          </div>
        </details>
      )}

      <section className="loc-section" ref={tableSectionRef}>
        <h2>Протокол</h2>
        {/* Система, пол и возрастная группа — отдельные зачёты: что выберете,
            внутри того и пересчитаются места. Отдельной секцией «Зачёт» система
            стояла в стороне от остальных фильтров (правка Дмитрия 29.08.2026). */}
        <p className="muted uniprot-scope-note">
          Система, пол и возрастная группа — отдельные зачёты: что выберете, внутри того и
          пересчитаются места.
        </p>
        <FilterPanel>
          <FilterRow>
          {/* Зачёт: внутри выбранной системы места пересчитываются, поэтому
              выбор ровно один (mode="single"). */}
          <PlatformFilter
            mode="single"
            value={platform ?? "all"}
            onChange={(next) => setPlatform(next === "all" ? null : next)}
            options={data.platforms.map((item) => ({
              code: item.platform_code,
              label: item.title,
              count: item.finishers,
            }))}
          />
          <GenderFilter
            value={gender}
            onChange={(next) => setGender(next as GenderFilter)}
            options={[
              { value: "all", count: genderCounts.total },
              { value: "male", count: genderCounts.male, disabled: !genderKnown },
              { value: "female", count: genderCounts.female, disabled: !genderKnown },
            ]}
          />
          {hasAgeGroups && (
            <FilterGroup label="Возрастная группа">
            <select
              className="protocol-age-select"
              value={ageGroup ?? ""}
              onChange={(event) => setAgeGroup(event.target.value || null)}
              aria-label="Фильтр по возрастной группе"
            >
              <option value="">Все группы</option>
              {data.age_groups.map((group) => (
                <option key={group.age_group} value={group.age_group}>
                  {group.age_group} ({formatInt(group.total)})
                </option>
              ))}
            </select>
            </FilterGroup>
          )}
          {tableColumns.hasToggle && (
            <FilterGroup label="Колонки">
              <TableViewToggle columns={tableColumns} inline />
            </FilterGroup>
          )}
          <FilterGroup label="Поиск" trailing>
            <FilterSearch
              value={search}
              onChange={setSearch}
              placeholder="Имя, площадка или клуб"
              ariaLabel="Поиск по имени, площадке или клубу"
            />
          </FilterGroup>
          </FilterRow>
          {loading && <span className="muted uniprot-total">Считаем…</span>}
        </FilterPanel>
        {summary.skipped_foreign_parkrun > 0 && (
          <p className="muted uniprot-scope-note">
            Зарубежный parkrun в зачёт не входит ({formatInt(summary.skipped_foreign_parkrun)}{" "}
            {summary.skipped_foreign_parkrun === 1 ? "результат" : "результатов"}): по таким
            площадкам в базе не протокол, а результаты наших туристов, среди них попадаются
            junior parkrun на 2 км.
          </p>
        )}

        <TableWrap
          innerRef={attachFloatingHead}
          outerRef={tableColumns.measureRef}
          className="protocol-table-wrap"
          stickyFirstCol={showFull}
        >
          <table
            className={`data-table data-table-layout-fixed uniprot-table${
              showFull ? "" : " data-table-short"
            }`}
            style={showFull ? undefined : { minWidth: tableColumns.minWidth }}
          >
            <colgroup>
              <col className="col-number" />
              <col className="col-runner" />
              <col className="col-time" />
              {show("location") && <col className="col-location" />}
              {show("gender_place") && <col className="col-gender" />}
              {hasAgeGroups && show("age_group") && <col className="col-age" />}
              {show("platform") && <col className="col-platform" />}
              {hasPace && show("pace") && <col className="col-pace" />}
              {show("location_position") && <col className="col-local-place" />}
              {hasClubs && show("club") && <col className="col-club" />}
            </colgroup>
            <thead>
              <tr>
                <ColumnHeader
                  label="№"
                  hint={`Место в едином протоколе выбранного зачёта: ${scopeHintLabel}`}
                  filterable={false}
                />
                <ColumnHeader label="Участник" filterable={false} />
                <ColumnHeader label="Время" filterable={false} />
                {show("location") && <ColumnHeader label="Локация" filterable={false} />}
                {show("gender_place") && (
                  <ColumnHeader
                    label="М/Ж"
                    hint="Место среди своего пола за всю неделю своей системы — от выбранного среза не зависит"
                    filterable={false}
                  />
                )}
                {hasAgeGroups && show("age_group") && (
                  <ColumnHeader
                    label="Возр. группа"
                    hint="Категория из протокола и место в своей возрастной группе за всю неделю своей системы"
                    filterable={false}
                  />
                )}
                {show("platform") && <ColumnHeader label="Система" filterable={false} />}
                {hasPace && show("pace") && <ColumnHeader label="Темп" filterable={false} />}
                {show("location_position") && (
                  <ColumnHeader
                    label="У себя"
                    hint="Место на своей площадке — то, что стоит в протоколе системы"
                    filterable={false}
                  />
                )}
                {hasClubs && show("club") && <ColumnHeader label="Клуб" filterable={false} />}
              </tr>
            </thead>
            <tbody>
              {data.results.length === 0 ? (
                <tr>
                  <td colSpan={visibleCount} className="table-empty-cell">
                    <span className="muted">
                      {summary.finishers === 0
                        ? "Протоколов этой недели в базе пока нет"
                        : "Никого не нашлось по выбранным фильтрам"}
                    </span>
                  </td>
                </tr>
              ) : (
                data.results.map((row) => renderRow(row, "row"))
              )}
            </tbody>
          </table>
        </TableWrap>

        {data.pages > 1 && (
          <nav className="uniprot-pager" aria-label="Страницы протокола">
            <button
              type="button"
              className="btn secondary"
              onClick={() => goToPage(data.page - 1)}
              disabled={data.page <= 1 || loading}
            >
              ← Назад
            </button>
            <span className="muted">
              Строки {formatInt((data.page - 1) * data.per_page + 1)}—
              {formatInt(Math.min(data.page * data.per_page, data.total))} из {formatInt(data.total)}
            </span>
            <button
              type="button"
              className="btn secondary"
              onClick={() => goToPage(data.page + 1)}
              disabled={data.page >= data.pages || loading}
            >
              Вперёд →
            </button>
          </nav>
        )}
      </section>

      <ScrollToTopButton />
    </PortalSectionShell>
  );
}

export function UnifiedProtocolPage(params: UnifiedProtocolParams) {
  // key по неделе: смена адреса пересобирает состояние страницы с нуля.
  return <UnifiedProtocolContent key={params.saturday ?? "latest"} {...params} />;
}
