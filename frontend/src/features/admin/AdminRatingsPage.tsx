import { useCallback, useEffect, useMemo, useState } from "react";
import { AdminShell } from "./AdminShell";
import { RequireAdmin } from "../../components/RequireAdmin";
import { PlatformBadge } from "../../components/PlatformBadge";
import {
  getAdminLocationRatings,
  getAdminRatings,
  type AdminLocationRatingRow,
  type AdminLocationRatings,
  type AdminRatingRow,
  type AdminRatings,
  type AdminRatingsStatGroup,
} from "../../lib/api";
import { formatDate, formatDateTime, formatInt, formatStatValue, platformCodeLabel } from "../../lib/format";
import { AdminSubnav } from "./AdminSubnav";

function num(value: number | null): string {
  return value == null ? "—" : value.toFixed(2);
}

const STAT_ITEMS = [
  { key: "last_1d", label: "за сутки" },
  { key: "last_7d", label: "за 7 дней" },
  { key: "last_30d", label: "за 30 дней" },
  { key: "total", label: "всего в базе" },
] as const;

function StatGroup({ title, hint, group }: { title: string; hint: string; group: AdminRatingsStatGroup }) {
  return (
    <div className="admin-ratings-stat-group">
      <div className="admin-ratings-stat-group-head">
        <span className="admin-ratings-stat-group-title">{title}</span>
        <span className="admin-ratings-stat-group-hint muted">{hint}</span>
      </div>
      <div className="admin-ratings-stats">
        {STAT_ITEMS.map((item) => (
          <div className="admin-ratings-stat" key={item.key}>
            <span className="admin-ratings-stat-value">{formatStatValue(group[item.key])}</span>
            <span className="admin-ratings-stat-label">{item.label}</span>
          </div>
        ))}
      </div>
    </div>
  );
}

const PAGE_SIZE_OPTIONS = [25, 50, 100, 250] as const;
const DEFAULT_PAGE_SIZE = 50;

// Оба блока страницы приходят целиком одним запросом и режутся на страницы уже
// здесь: строк на сотни, а фильтровать и сортировать их удобнее по всему
// набору, а не по видимой странице.
function Pagination({
  page,
  pageCount,
  pageSize,
  total,
  shownFrom,
  shownTo,
  onPage,
  onPageSize,
}: {
  page: number;
  pageCount: number;
  pageSize: number;
  total: number;
  shownFrom: number;
  shownTo: number;
  onPage: (value: number) => void;
  onPageSize: (value: number) => void;
}) {
  return (
    <div className="admin-ratings-pagination">
      <span className="muted admin-ratings-pagination-label">
        {total === 0
          ? "Найдено: 0"
          : `Показано ${formatInt(shownFrom)}–${formatInt(shownTo)} из ${formatInt(total)}`}
      </span>
      <div className="admin-ratings-pagination-controls">
        <button
          type="button"
          className="btn secondary btn-sm"
          disabled={page <= 1}
          onClick={() => onPage(Math.max(1, page - 1))}
        >
          Назад
        </button>
        <span className="muted admin-ratings-pagination-label">
          Страница {formatInt(page)} из {formatInt(pageCount)}
        </span>
        <button
          type="button"
          className="btn secondary btn-sm"
          disabled={page >= pageCount}
          onClick={() => onPage(Math.min(pageCount, page + 1))}
        >
          Вперёд
        </button>
        <select
          className="input admin-ratings-page-size"
          value={pageSize}
          onChange={(event) => onPageSize(Number(event.target.value))}
          aria-label="Строк на странице"
        >
          {PAGE_SIZE_OPTIONS.map((size) => (
            <option key={size} value={size}>
              по {size}
            </option>
          ))}
        </select>
      </div>
    </div>
  );
}

type LocationSortKey =
  | "location"
  | "ratings"
  | "voters"
  | "overall"
  | "organization"
  | "route"
  | "community";

function compareLocations(
  a: AdminLocationRatingRow,
  b: AdminLocationRatingRow,
  key: LocationSortKey,
): number {
  switch (key) {
    case "location":
      return a.location_name.localeCompare(b.location_name, "ru");
    case "ratings":
      return a.ratings - b.ratings;
    case "voters":
      return a.voters - b.voters;
    case "overall":
      return cmpNullableScore(a.avg_overall, b.avg_overall);
    case "organization":
      return cmpNullableScore(a.avg_organization, b.avg_organization);
    case "route":
      return cmpNullableScore(a.avg_route, b.avg_route);
    case "community":
      return cmpNullableScore(a.avg_community, b.avg_community);
    default:
      return 0;
  }
}

type RawSortKey =
  | "created_at"
  | "event_date"
  | "user"
  | "location"
  | "city"
  | "platform"
  | "overall"
  | "organization"
  | "route"
  | "community"
  | "comment";
type SortDir = "asc" | "desc";

// Незаполненный критерий (null) считаем самым низким: при ▼ он внизу, при ▲ — вверху.
function cmpNullableScore(a: number | null, b: number | null): number {
  return (a ?? -1) - (b ?? -1);
}

function compareRows(a: AdminRatingRow, b: AdminRatingRow, key: RawSortKey): number {
  switch (key) {
    case "created_at":
      return a.created_at.localeCompare(b.created_at);
    case "event_date":
      return a.event_date.localeCompare(b.event_date);
    case "user": {
      // Сначала строки со ссылкой на профиль (есть serial), потом по имени.
      const rank = (r: AdminRatingRow) => (r.user_serial != null ? 0 : 1);
      const byLink = rank(a) - rank(b);
      if (byLink !== 0) return byLink;
      return (a.user_display ?? "").localeCompare(b.user_display ?? "", "ru");
    }
    case "location":
      return (a.location_name ?? "").localeCompare(b.location_name ?? "", "ru");
    case "city":
      return (a.location_city ?? "").localeCompare(b.location_city ?? "", "ru");
    case "platform":
      return a.platform_code.localeCompare(b.platform_code);
    case "overall":
      return a.score_overall - b.score_overall;
    case "organization":
      return cmpNullableScore(a.score_organization, b.score_organization);
    case "route":
      return cmpNullableScore(a.score_route, b.score_route);
    case "community":
      return cmpNullableScore(a.score_community, b.score_community);
    case "comment": {
      // Сначала строки с комментарием (▲), потом без; внутри — по алфавиту.
      const rank = (r: AdminRatingRow) => (r.comment ? 0 : 1);
      const byPresence = rank(a) - rank(b);
      if (byPresence !== 0) return byPresence;
      return (a.comment ?? "").localeCompare(b.comment ?? "", "ru");
    }
    default:
      return 0;
  }
}

function AdminRatingsContent() {
  const [raw, setRaw] = useState<AdminRatings | null>(null);
  // Фото открывается прямо на странице: уходить в бакет за картинкой, чтобы
  // понять, что приложил участник, — лишний шаг.
  const [photoPreview, setPhotoPreview] = useState<string | null>(null);
  const [locations, setLocations] = useState<AdminLocationRatings | null>(null);
  const [excludeLocals, setExcludeLocals] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const [search, setSearch] = useState("");
  const [platform, setPlatform] = useState("all");
  const [sort, setSort] = useState<RawSortKey>("created_at");
  const [direction, setDirection] = useState<SortDir>("desc");
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState<number>(DEFAULT_PAGE_SIZE);

  // Рейтинг локаций: свои фильтр, сортировка и страница — блоки живут независимо.
  const [locationSearch, setLocationSearch] = useState("");
  const [onlyThreshold, setOnlyThreshold] = useState(false);
  const [locationSort, setLocationSort] = useState<LocationSortKey>("ratings");
  const [locationDirection, setLocationDirection] = useState<SortDir>("desc");
  const [locationPage, setLocationPage] = useState(1);
  const [locationPageSize, setLocationPageSize] = useState<number>(DEFAULT_PAGE_SIZE);

  const loadRaw = useCallback(() => {
    getAdminRatings()
      .then(setRaw)
      .catch((err) => setError(err instanceof Error ? err.message : "Не удалось загрузить оценки"));
  }, []);

  const loadLocations = useCallback((exclude: boolean) => {
    getAdminLocationRatings(exclude)
      .then(setLocations)
      .catch((err) => setError(err instanceof Error ? err.message : "Не удалось загрузить рейтинг"));
  }, []);

  useEffect(() => {
    loadRaw();
  }, [loadRaw]);

  useEffect(() => {
    loadLocations(excludeLocals);
  }, [loadLocations, excludeLocals]);

  const handleSort = (key: RawSortKey) => {
    if (sort === key) {
      setDirection((current) => (current === "desc" ? "asc" : "desc"));
    } else {
      setSort(key);
      const numericDesc: RawSortKey[] = [
        "created_at",
        "event_date",
        "overall",
        "organization",
        "route",
        "community",
      ];
      setDirection(numericDesc.includes(key) ? "desc" : "asc");
    }
    setPage(1);
  };

  const handleLocationSort = (key: LocationSortKey) => {
    if (locationSort === key) {
      setLocationDirection((current) => (current === "desc" ? "asc" : "desc"));
    } else {
      setLocationSort(key);
      // Числовые столбцы интереснее сверху вниз, название — по алфавиту.
      setLocationDirection(key === "location" ? "asc" : "desc");
    }
    setLocationPage(1);
  };

  const platformCodes = useMemo(() => {
    const set = new Set<string>();
    raw?.ratings.forEach((r) => set.add(r.platform_code));
    return Array.from(set).sort();
  }, [raw]);

  const visibleRatings = useMemo(() => {
    if (!raw) return [];
    const needle = search.trim().toLowerCase();
    const filtered = raw.ratings.filter((r) => {
      if (platform !== "all" && r.platform_code !== platform) return false;
      if (!needle) return true;
      const haystack = [r.user_display, r.location_name, r.location_city, r.comment, r.user_serial != null ? `#${r.user_serial}` : null]
        .filter(Boolean)
        .join(" ")
        .toLowerCase();
      return haystack.includes(needle);
    });
    const sorted = [...filtered].sort((a, b) => compareRows(a, b, sort));
    if (direction === "desc") sorted.reverse();
    return sorted;
  }, [raw, search, platform, sort, direction]);

  const visibleLocations = useMemo(() => {
    if (!locations) return [];
    const needle = locationSearch.trim().toLowerCase();
    const filtered = locations.locations.filter((loc) => {
      if (onlyThreshold && !loc.meets_threshold) return false;
      if (!needle) return true;
      return loc.location_name.toLowerCase().includes(needle);
    });
    const sorted = [...filtered].sort((a, b) => compareLocations(a, b, locationSort));
    if (locationDirection === "desc") sorted.reverse();
    return sorted;
  }, [locations, locationSearch, onlyThreshold, locationSort, locationDirection]);

  // Страница могла «уехать» за конец после смены фильтра — возвращаемся к последней.
  const pageCount = Math.max(1, Math.ceil(visibleRatings.length / pageSize));
  const currentPage = Math.min(page, pageCount);
  const pagedRatings = visibleRatings.slice((currentPage - 1) * pageSize, currentPage * pageSize);

  const locationPageCount = Math.max(1, Math.ceil(visibleLocations.length / locationPageSize));
  const currentLocationPage = Math.min(locationPage, locationPageCount);
  const pagedLocations = visibleLocations.slice(
    (currentLocationPage - 1) * locationPageSize,
    currentLocationPage * locationPageSize,
  );

  const sortArrow = (key: RawSortKey) =>
    sort === key ? (direction === "asc" ? " ▲" : " ▼") : "";

  const locationSortArrow = (key: LocationSortKey) =>
    locationSort === key ? (locationDirection === "asc" ? " ▲" : " ▼") : "";

  const LocationSortTh = ({ label, sortKey }: { label: string; sortKey: LocationSortKey }) => (
    <th>
      <button
        type="button"
        className={`admin-sort-th${locationSort === sortKey ? " active" : ""}`}
        onClick={() => handleLocationSort(sortKey)}
      >
        {label}
        {locationSortArrow(sortKey)}
      </button>
    </th>
  );

  const SortTh = ({
    label,
    sortKey,
    title,
  }: {
    label: string;
    sortKey: RawSortKey;
    title?: string;
  }) => (
    <th>
      <button
        type="button"
        className={`admin-sort-th${sort === sortKey ? " active" : ""}`}
        onClick={() => handleSort(sortKey)}
        title={title}
      >
        {label}
        {sortArrow(sortKey)}
      </button>
    </th>
  );

  return (
    <AdminShell title="Рейтинг">
      <div className="admin-ratings-page">
        <AdminSubnav activePath="/admin/ratings" />

        {error && <div className="card error"><p>{error}</p></div>}

        <section className="card admin-ratings-section">
          <h2 className="section-title">Статистика оценок</h2>
          {raw ? (
            <div className="admin-ratings-stat-groups">
              <StatGroup
                title="По дате оценки"
                hint="когда оценку оставили"
                group={raw.stats.by_rating_date}
              />
              <StatGroup
                title="По дате пробежки"
                hint="когда прошёл оцениваемый старт"
                group={raw.stats.by_event_date}
              />
            </div>
          ) : (
            <p className="muted">Загрузка…</p>
          )}
        </section>

        <section className="card admin-ratings-section">
          <div className="admin-ratings-head">
            <h2 className="section-title">Рейтинг локаций</h2>
            <label className="checkbox-row">
              <input
                type="checkbox"
                checked={excludeLocals}
                onChange={(e) => setExcludeLocals(e.target.checked)}
              />
              Без местных (исключить домашние локации оценивших)
            </label>
          </div>
          <p className="muted admin-ratings-lead">
            Среднее по критериям (1–5). «Оценивших» — число разных пользователей; на прод рейтинг
            планируется показывать с {locations?.min_voters ?? 10} разными оценившими. Клик по
            заголовку столбца — сортировка.
          </p>
          <div className="admin-ratings-toolbar">
            <input
              className="input admin-ratings-search"
              type="search"
              placeholder="Поиск по названию локации…"
              value={locationSearch}
              onChange={(e) => {
                setLocationSearch(e.target.value);
                setLocationPage(1);
              }}
            />
            <label className="checkbox-row">
              <input
                type="checkbox"
                checked={onlyThreshold}
                onChange={(e) => {
                  setOnlyThreshold(e.target.checked);
                  setLocationPage(1);
                }}
              />
              Только прошедшие порог
            </label>
          </div>
          <Pagination
            page={currentLocationPage}
            pageCount={locationPageCount}
            pageSize={locationPageSize}
            total={visibleLocations.length}
            shownFrom={(currentLocationPage - 1) * locationPageSize + 1}
            shownTo={(currentLocationPage - 1) * locationPageSize + pagedLocations.length}
            onPage={setLocationPage}
            onPageSize={(value) => {
              setLocationPageSize(value);
              setLocationPage(1);
            }}
          />
          <div className="table-wrap">
            <table className="data-table">
              <thead>
                <tr>
                  <LocationSortTh label="Локация" sortKey="location" />
                  <LocationSortTh label="Оценок" sortKey="ratings" />
                  <LocationSortTh label="Оценивших" sortKey="voters" />
                  <LocationSortTh label="Общая" sortKey="overall" />
                  <LocationSortTh label="Организация" sortKey="organization" />
                  <LocationSortTh label="Трасса" sortKey="route" />
                  <LocationSortTh label="Сообщество" sortKey="community" />
                </tr>
              </thead>
              <tbody>
                {locations && visibleLocations.length === 0 && (
                  <tr>
                    <td colSpan={7} className="muted">
                      {locations.locations.length === 0 ? "Пока нет оценок" : "Ничего не найдено"}
                    </td>
                  </tr>
                )}
                {pagedLocations.map((loc) => (
                  <tr key={loc.location_key}>
                    <td>
                      {loc.location_name}
                      {loc.meets_threshold && (
                        <span className="badge admin-ratings-threshold" title="Достаточно оценивших для показа">
                          порог
                        </span>
                      )}
                    </td>
                    <td>{loc.ratings}</td>
                    <td>{loc.voters}</td>
                    <td className="admin-ratings-avg">{num(loc.avg_overall)}</td>
                    <td>{num(loc.avg_organization)}</td>
                    <td>{num(loc.avg_route)}</td>
                    <td>{num(loc.avg_community)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>

        <section className="card admin-ratings-section admin-ratings-raw-card">
          <h2 className="section-title">Все оценки (сырьё)</h2>
          <p className="muted admin-ratings-lead">
            Всего {raw?.ratings.length ?? 0} шт. Замороженные — старту больше 3 месяцев. Клик по
            заголовку столбца — сортировка.
          </p>
          <div className="admin-ratings-toolbar">
            <input
              className="input admin-ratings-search"
              type="search"
              placeholder="Поиск: пользователь, локация, город, комментарий…"
              value={search}
              onChange={(e) => {
                setSearch(e.target.value);
                setPage(1);
              }}
            />
            <select
              className="input admin-ratings-platform"
              value={platform}
              onChange={(e) => {
                setPlatform(e.target.value);
                setPage(1);
              }}
            >
              <option value="all">Все системы</option>
              {platformCodes.map((code) => (
                <option key={code} value={code}>
                  {platformCodeLabel(code)}
                </option>
              ))}
            </select>
            <span className="muted admin-ratings-count">
              Отобрано: {formatInt(visibleRatings.length)}
              {raw && visibleRatings.length !== raw.ratings.length
                ? ` из ${formatInt(raw.ratings.length)}`
                : ""}
            </span>
          </div>
          <Pagination
            page={currentPage}
            pageCount={pageCount}
            pageSize={pageSize}
            total={visibleRatings.length}
            shownFrom={(currentPage - 1) * pageSize + 1}
            shownTo={(currentPage - 1) * pageSize + pagedRatings.length}
            onPage={setPage}
            onPageSize={(value) => {
              setPageSize(value);
              setPage(1);
            }}
          />
          <div className="table-scroll">
            <table className="data-table admin-ratings-raw-table">
              <thead>
                <tr>
                  <SortTh label="Дата оценки" sortKey="created_at" />
                  <SortTh label="Дата пробежки" sortKey="event_date" />
                  <SortTh
                    label="Пользователь"
                    sortKey="user"
                    title="Сортировка: сначала строки со ссылкой на профиль (▲), затем по имени"
                  />
                  <SortTh label="Локация" sortKey="location" />
                  <SortTh label="Город" sortKey="city" />
                  <SortTh label="Система" sortKey="platform" />
                  <SortTh label="Общая" sortKey="overall" />
                  <SortTh label="Орг." sortKey="organization" />
                  <SortTh label="Трасса" sortKey="route" />
                  <SortTh label="Сообщ." sortKey="community" />
                  <th>Публично</th>
                  <th>Статус</th>
                  <SortTh
                    label="Комментарий"
                    sortKey="comment"
                    title="Сортировка: сначала строки с комментарием (▲)"
                  />
                  <th>Фото</th>
                </tr>
              </thead>
              <tbody>
                {raw && visibleRatings.length === 0 && (
                  <tr>
                    <td colSpan={14} className="muted">
                      {raw.ratings.length === 0 ? "Пока нет оценок" : "Ничего не найдено"}
                    </td>
                  </tr>
                )}
                {pagedRatings.map((r) => (
                  <tr key={r.id}>
                    <td className="admin-ratings-nowrap" title={formatDateTime(r.created_at)}>
                      {formatDateTime(r.created_at)}
                    </td>
                    <td className="admin-ratings-nowrap">{formatDate(r.event_date)}</td>
                    <td>
                      {r.user_serial != null ? (
                        <a
                          href={`/users/${r.user_serial}`}
                          target="_blank"
                          rel="noreferrer"
                          className="admin-platform-link"
                        >
                          {r.user_display}
                          <span className="muted"> #{r.user_serial}</span>
                        </a>
                      ) : (
                        <span>{r.user_display}</span>
                      )}
                    </td>
                    <td>{r.location_name}</td>
                    <td>{r.location_city ?? "—"}</td>
                    <td><PlatformBadge code={r.platform_code} /></td>
                    <td className="admin-ratings-avg">{r.score_overall}</td>
                    <td>{r.score_organization ?? "—"}</td>
                    <td>{r.score_route ?? "—"}</td>
                    <td>{r.score_community ?? "—"}</td>
                    <td>{r.is_public ? "да" : "аноним"}</td>
                    <td>{r.editable ? "можно менять" : "заморожена"}</td>
                    <td className="admin-ratings-comment">
                      {r.comment ? (
                        <span className="admin-ratings-comment-text" title={r.comment}>
                          {r.comment}
                        </span>
                      ) : (
                        "—"
                      )}
                    </td>
                    <td className="admin-ratings-photos">
                      {r.photos.length > 0 ? (
                        r.photos.map((photo, index) => (
                          <button
                            key={photo.id}
                            type="button"
                            className="admin-ratings-photo-thumb"
                            onClick={() => setPhotoPreview(photo.url)}
                            title={`Фото ${index + 1} из ${r.photos.length} — открыть`}
                          >
                            <img src={photo.url} alt="" loading="lazy" />
                          </button>
                        ))
                      ) : (
                        "—"
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      </div>

      {photoPreview && (
        <div
          className="admin-photo-overlay"
          role="dialog"
          aria-label="Фото отзыва"
          onClick={() => setPhotoPreview(null)}
        >
          <img src={photoPreview} alt="Фото отзыва" />
          <button type="button" className="admin-photo-close" aria-label="Закрыть">
            ×
          </button>
        </div>
      )}
    </AdminShell>
  );
}

export function AdminRatingsPage() {
  return <RequireAdmin>{() => <AdminRatingsContent />}</RequireAdmin>;
}
