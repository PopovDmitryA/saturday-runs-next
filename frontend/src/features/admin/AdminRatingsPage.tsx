import { useCallback, useEffect, useMemo, useState } from "react";
import { AdminShell } from "./AdminShell";
import { RequireAdmin } from "../../components/RequireAdmin";
import { PlatformBadge } from "../../components/PlatformBadge";
import {
  getAdminLocationRatings,
  getAdminRatings,
  type AdminLocationRatings,
  type AdminRatingRow,
  type AdminRatings,
  type AdminRatingsStatGroup,
} from "../../lib/api";
import { formatDate, formatDateTime, platformCodeLabel } from "../../lib/format";
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
            <span className="admin-ratings-stat-value">{group[item.key]}</span>
            <span className="admin-ratings-stat-label">{item.label}</span>
          </div>
        ))}
      </div>
    </div>
  );
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

  const sortArrow = (key: RawSortKey) =>
    sort === key ? (direction === "asc" ? " ▲" : " ▼") : "";

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
            планируется показывать с {locations?.min_voters ?? 10} разными оценившими.
          </p>
          <div className="table-wrap">
            <table className="data-table">
              <thead>
                <tr>
                  <th>Локация</th>
                  <th>Оценок</th>
                  <th>Оценивших</th>
                  <th>Общая</th>
                  <th>Организация</th>
                  <th>Трасса</th>
                  <th>Сообщество</th>
                </tr>
              </thead>
              <tbody>
                {locations && locations.locations.length === 0 && (
                  <tr>
                    <td colSpan={7} className="muted">Пока нет оценок</td>
                  </tr>
                )}
                {locations?.locations.map((loc) => (
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
              onChange={(e) => setSearch(e.target.value)}
            />
            <select
              className="input admin-ratings-platform"
              value={platform}
              onChange={(e) => setPlatform(e.target.value)}
            >
              <option value="all">Все системы</option>
              {platformCodes.map((code) => (
                <option key={code} value={code}>
                  {platformCodeLabel(code)}
                </option>
              ))}
            </select>
            <span className="muted admin-ratings-count">
              Показано: {visibleRatings.length}
              {raw && visibleRatings.length !== raw.ratings.length ? ` из ${raw.ratings.length}` : ""}
            </span>
          </div>
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
                {visibleRatings.map((r) => (
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
