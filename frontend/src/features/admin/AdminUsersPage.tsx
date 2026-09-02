import { Fragment, useCallback, useEffect, useMemo, useState } from "react";
import { AdminShell } from "./AdminShell";
import { RequireAdmin } from "../../components/RequireAdmin";
import { Snackbar } from "../../components/Snackbar";
import { AdminSubnav } from "./AdminSubnav";
import {
  createAdminOrganizerGrant,
  deleteAdminOrganizerGrant,
  getAdminUserLoginEvents,
  getAdminUserOrganizerAccess,
  getAdminUserVisits,
  getLocationsIndex,
  listAdminUsers,
  triggerAdminUserSyncPlatform,
  type AdminLoginEventsResponse,
  type AdminOrganizerAccessResponse,
  type AdminPlatformLinkBrief,
  type AdminUserHomeLocation,
  type AdminUserListItem,
  type AdminUserVisitsResponse,
  type AdminUsersSort,
  type AdminUsersSortDirection,
  type LocationIndexItem,
} from "../../lib/api";
import { formatDateTime, formatInt, formatRelativeTime, platformCodeLabel } from "../../lib/format";
import { platformProfileUrl } from "../../lib/platformProfileUrl";
import { authLoginUrl, authProviderLabel, userLoginLines } from "./adminUserDisplay";
import { pageTypeLabel } from "./pageTypeLabels";

// Платформы, для которых бэкенд поддерживает ручной запуск синка (см. ADMIN_SUPPORTED_SYNC_PLATFORMS).
const SYNCABLE_PLATFORMS = new Set(["five_verst", "s95", "parkrun"]);

const LOGIN_PROVIDER_LABELS: Record<string, string> = {
  magic_link: "ссылка",
  merge: "объединение",
};

function loginProviderLabel(provider: string) {
  if (!provider) {
    return "";
  }
  return LOGIN_PROVIDER_LABELS[provider] ?? authProviderLabel(provider);
}

// Короткая подпись устройства: из UA достаточно понять «тот же браузер или нет».
function shortUserAgent(userAgent: string) {
  if (!userAgent) {
    return "—";
  }
  return userAgent.length > 48 ? `${userAgent.slice(0, 48)}…` : userAgent;
}

function LoginJournal({ data }: { data: AdminLoginEventsResponse }) {
  if (data.items.length === 0) {
    return <p className="muted">Журнал пуст — с момента включения журнала пользователь не входил.</p>;
  }
  return (
    <div className="admin-login-journal">
      <p className="admin-login-journal-summary">
        Входов: <strong>{data.logins}</strong> · выходов: <strong>{data.logouts}</strong> · устройств:{" "}
        <strong>{data.devices}</strong> ·{" "}
        <span className={data.unexpected_relogins > 0 ? "admin-login-journal-alert" : undefined}>
          входов без разлогина: <strong>{data.unexpected_relogins}</strong>
        </span>
      </p>
      {data.unexpected_relogins > 0 && (
        <p className="admin-login-journal-alert">
          Пользователь заходил заново с устройства, с которого не выходил — сессия слетала сама.
        </p>
      )}
      <table className="admin-table admin-login-journal-table">
        <thead>
          <tr>
            <th>Когда</th>
            <th>Событие</th>
            <th>Способ</th>
            <th>IP</th>
            <th>Устройство</th>
          </tr>
        </thead>
        <tbody>
          {data.items.map((event, index) => (
            <tr key={`${event.ts}-${index}`}>
              <td>{formatDateTime(event.ts)}</td>
              <td>{event.event_type === "logout" ? "выход" : "вход"}</td>
              <td>{loginProviderLabel(event.provider) || "—"}</td>
              <td>{event.ip || "—"}</td>
              <td title={event.user_agent}>{shortUserAgent(event.user_agent)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

// «Был на сайте»: в списке важно не точное время, а давность — «вчера» и
// «полгода назад» видно с одного взгляда. Точная дата — в подсказке.
function LastSeenCell({ value }: { value: string | null }) {
  if (!value) {
    return (
      <span className="muted" title="Ни одного просмотра страницы после включения отметки визитов">
        —
      </span>
    );
  }
  return <span title={formatDateTime(value)}>{formatRelativeTime(value)}</span>;
}

function formatVisitDuration(seconds: number): string {
  if (seconds < 60) {
    return `${seconds} с`;
  }
  const minutes = Math.round(seconds / 60);
  if (minutes < 60) {
    return `${minutes} мин`;
  }
  const hours = Math.floor(minutes / 60);
  const rest = minutes % 60;
  return rest ? `${hours} ч ${rest} мин` : `${hours} ч`;
}

function visitTimeRange(visit: { started_at: string; ended_at: string }): string {
  const start = new Date(visit.started_at);
  const end = new Date(visit.ended_at);
  const time = (date: Date) =>
    date.toLocaleTimeString("ru-RU", { hour: "2-digit", minute: "2-digit" });
  if (Number.isNaN(start.getTime()) || Number.isNaN(end.getTime())) {
    return formatDateTime(visit.started_at);
  }
  return `${formatDateTime(visit.started_at)} — ${time(end)}`;
}

// Журнал визитов: страницы, склеенные в заходы (разрыв меньше получаса — один
// заход). Отвечает на «когда человек последний раз пользовался сайтом», на что
// журнал входов не отвечает: сессия живёт месяцами.
function VisitsJournal({ data }: { data: AdminUserVisitsResponse }) {
  if (data.items.length === 0) {
    return (
      <p className="muted">
        {data.last_seen_at
          ? `Просмотров за последние ${data.retention_days} дней нет. Последний визит: ${formatDateTime(data.last_seen_at)} (${formatRelativeTime(data.last_seen_at)}).`
          : "Ни одного просмотра страницы — человек не заходил на сайт (или заходил до того, как визиты начали отмечаться)."}
      </p>
    );
  }
  return (
    <div className="admin-login-journal">
      <p className="admin-login-journal-summary">
        Последний визит: <strong>{formatDateTime(data.last_seen_at ?? data.last_view_at)}</strong>
        {data.last_seen_at && <> ({formatRelativeTime(data.last_seen_at)})</>} · заходов в журнале:{" "}
        <strong>{formatInt(data.visits_shown)}</strong> · страниц за {data.retention_days} дней:{" "}
        <strong>{formatInt(data.total_views)}</strong> · дней с визитами:{" "}
        <strong>{formatInt(data.days)}</strong>
      </p>
      {data.truncated && (
        <p className="muted">
          Показаны последние заходы: просмотров в окне больше, чем помещается в журнал.
        </p>
      )}
      <table className="admin-table admin-login-journal-table">
        <thead>
          <tr>
            <th>Заход</th>
            <th>Длительность</th>
            <th>Страниц</th>
            <th>Что смотрел</th>
          </tr>
        </thead>
        <tbody>
          {data.items.map((visit) => (
            <tr key={visit.started_at}>
              <td>{visitTimeRange(visit)}</td>
              <td>{formatVisitDuration(visit.duration_sec)}</td>
              <td>{formatInt(visit.views)}</td>
              <td>
                <ul className="admin-visit-pages">
                  {visit.pages.map((page, index) => (
                    <li key={`${page.ts}-${index}`} title={page.path}>
                      <a href={page.path} target="_blank" rel="noreferrer">
                        {pageTypeLabel(page.page_type)}
                      </a>
                      {page.duration_sec != null && (
                        <span className="muted"> · {formatVisitDuration(page.duration_sec)}</span>
                      )}
                    </li>
                  ))}
                  {visit.pages_hidden > 0 && (
                    <li className="muted">…и ещё {formatInt(visit.pages_hidden)}</li>
                  )}
                </ul>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

// Инлайн-панель «Оргдоступ»: автодоступ по волонтёрствам (read-only) + ручные
// гранты с добавлением/удалением. Селект локации — публичный индекс каталога.
function OrganizerAccessPanel({
  userId,
  data,
  locations,
  onChanged,
  onError,
}: {
  userId: string;
  data: AdminOrganizerAccessResponse;
  locations: LocationIndexItem[];
  onChanged: (next: AdminOrganizerAccessResponse) => void;
  onError: (message: string) => void;
}) {
  const [selectedKey, setSelectedKey] = useState("");
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);

  const manualKeys = useMemo(
    () => new Set(data.manual.map((item) => item.location_key)),
    [data.manual],
  );

  const handleAdd = async () => {
    if (!selectedKey) {
      return;
    }
    setBusy(true);
    try {
      onChanged(await createAdminOrganizerGrant(userId, selectedKey, note.trim() || undefined));
      setSelectedKey("");
      setNote("");
    } catch (err) {
      onError(err instanceof Error ? err.message : "Не удалось выдать доступ");
    } finally {
      setBusy(false);
    }
  };

  const handleDelete = async (grantId: string) => {
    setBusy(true);
    try {
      onChanged(await deleteAdminOrganizerGrant(userId, grantId));
    } catch (err) {
      onError(err instanceof Error ? err.message : "Не удалось отозвать доступ");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="admin-organizer-access">
      <p className="admin-organizer-access-title">
        <strong>Кабинет организатора</strong> — доступ к расширенным данным локаций.
      </p>
      <p>
        Автодоступ (был организатором):{" "}
        {data.derived.length === 0 ? (
          <span className="muted">нет</span>
        ) : (
          data.derived.map((item, index) => (
            <Fragment key={item.location_key}>
              {index > 0 && ", "}
              {item.location_slug ? (
                <a href={`/organizer/${item.location_slug}`} target="_blank" rel="noreferrer">
                  {item.location_name ?? item.location_key}
                </a>
              ) : (
                <span>{item.location_name ?? item.location_key}</span>
              )}
            </Fragment>
          ))
        )}
      </p>
      <p>
        Выдано вручную:{" "}
        {data.manual.length === 0 ? (
          <span className="muted">нет</span>
        ) : (
          data.manual.map((item, index) => (
            <Fragment key={item.id}>
              {index > 0 && ", "}
              <span title={item.note ?? undefined}>
                {item.location_slug ? (
                  <a href={`/organizer/${item.location_slug}`} target="_blank" rel="noreferrer">
                    {item.location_name ?? item.location_key}
                  </a>
                ) : (
                  item.location_name ?? item.location_key
                )}{" "}
                <button
                  type="button"
                  className="btn btn-ghost btn-sm"
                  disabled={busy}
                  title="Отозвать доступ"
                  onClick={() => void handleDelete(item.id)}
                >
                  ✕
                </button>
              </span>
            </Fragment>
          ))
        )}
      </p>
      <div className="admin-organizer-access-form">
        <select
          className="input"
          value={selectedKey}
          onChange={(event) => setSelectedKey(event.target.value)}
        >
          <option value="">Локация для выдачи доступа…</option>
          {locations.map((location) => (
            <option
              key={location.identity_key}
              value={location.identity_key}
              disabled={manualKeys.has(location.identity_key)}
            >
              {location.name}
              {location.city ? ` — ${location.city}` : ""}
            </option>
          ))}
        </select>
        <input
          className="input"
          type="text"
          placeholder="Заметка (необязательно)"
          value={note}
          onChange={(event) => setNote(event.target.value)}
        />
        <button
          type="button"
          className="btn secondary btn-sm"
          disabled={busy || !selectedKey}
          onClick={() => void handleAdd()}
        >
          Выдать доступ
        </button>
      </div>
    </div>
  );
}

function platformCell(
  user: AdminUserListItem,
  code: string,
  isSyncing: boolean,
  onSync: (userId: string, code: string) => void,
) {
  const link = user.platform_links.find((item) => item.platform_code === code);
  if (!link) {
    return <span className="muted">—</span>;
  }
  const counts = (
    <span className="admin-platform-counts muted"> ({formatInt(link.run_count)}/{formatInt(link.volunteer_count)})</span>
  );
  const label = link.display_name?.trim() || (code === "runpark" ? (link.barcode_id ?? link.external_user_id) : null);
  const url = platformProfileUrl(link);
  const content = !url ? (
    <span className="admin-platform-link">
      {label ?? <span className="muted">Профиль</span>}
      {counts}
    </span>
  ) : (
    <a href={url} target="_blank" rel="noreferrer" className="admin-platform-link">
      {label ?? <span className="muted">Профиль</span>}
      {counts}
    </a>
  );
  if (!SYNCABLE_PLATFORMS.has(code)) {
    return content;
  }
  return (
    <span className="admin-platform-cell">
      {content}
      <button
        type="button"
        className="btn-icon-sync"
        disabled={isSyncing}
        title={isSyncing ? "Обновление…" : `Обновить ${platformCodeLabel(code)}`}
        onClick={() => onSync(user.id, code)}
      >
        {isSyncing ? "…" : "↻"}
      </button>
    </span>
  );
}

// Порядок систем — тот же, что у колонок: от самой массовой к самой редкой.
const PLATFORM_CODES = ["five_verst", "s95", "parkrun", "runpark"] as const;

// Свёрнутый вид: четыре колонки систем схлопнуты в одну ячейку с чипами
// привязок. Имя в системе, счётчики и кнопка синка остаются в развёрнутом
// виде — в свёрнутом важно только «какие системы привязаны».
function PlatformsSummaryCell({ user }: { user: AdminUserListItem }) {
  const links = PLATFORM_CODES.map((code) =>
    user.platform_links.find((item) => item.platform_code === code),
  ).filter((link): link is AdminPlatformLinkBrief => Boolean(link));

  if (links.length === 0) {
    return <span className="muted">—</span>;
  }

  return (
    <span className="admin-users-systems">
      {links.map((link) => {
        const label = platformCodeLabel(link.platform_code);
        const hint = [
          link.display_name?.trim() || link.barcode_id || link.external_user_id || label,
          `пробежек: ${formatInt(link.run_count)}`,
          `волонтёрств: ${formatInt(link.volunteer_count)}`,
        ].join(" · ");
        const url = platformProfileUrl(link);
        if (!url) {
          return (
            <span key={link.platform_code} className="admin-system-chip" title={hint}>
              {label}
            </span>
          );
        }
        return (
          <a
            key={link.platform_code}
            className="admin-system-chip"
            href={url}
            target="_blank"
            rel="noreferrer"
            title={hint}
          >
            {label}
          </a>
        );
      })}
    </span>
  );
}

// Вид колонок систем переживает перезагрузку: админ листает пользователей
// пачками и не должен каждый раз схлопывать таблицу заново.
const PLATFORMS_VIEW_STORAGE_KEY = "admin-users-platforms-view";

function readPlatformsView(): "short" | "full" {
  try {
    return window.localStorage.getItem(PLATFORMS_VIEW_STORAGE_KEY) === "full" ? "full" : "short";
  } catch {
    return "short";
  }
}

// Сколько претендентов показываем до нажатия «ещё»: у отдельных туристов
// первое место делят десятки площадок, разворачивать их сразу нельзя.
const HOME_TIE_PREVIEW = 5;

function LocationLink({ name, slug }: { name: string; slug: string | null }) {
  if (!slug) {
    return <span>{name}</span>;
  }
  return (
    <a href={`/locations/${slug}`} target="_blank" rel="noreferrer" className="admin-platform-link">
      {name}
    </a>
  );
}

// Предполагаемый «дом» — та же площадка, что человек видит у себя в кабинете
// и в рейтинге дальности: больше пробежек → больше волонтёрств → раньше начал.
// Ручной выбор в настройках побеждает автоматику. Если правило исчерпано и
// площадки поделили первое место, показываем всех претендентов.
function HomeLocationCell({ home }: { home: AdminUserHomeLocation | null }) {
  const [expanded, setExpanded] = useState(false);

  if (!home) {
    return <span className="muted" title="Нет ни одной пробежки в базе — считать дом не из чего">—</span>;
  }

  const hint = [
    `пробежек здесь: ${formatInt(home.run_days)}`,
    `волонтёрств: ${formatInt(home.volunteer_days)}`,
    `всего площадок: ${formatInt(home.locations_total)}`,
  ].join(" · ");

  if (home.is_tie) {
    const shown = expanded ? home.tied : home.tied.slice(0, HOME_TIE_PREVIEW);
    const hidden = home.tied.length - shown.length;
    return (
      <span className="admin-users-home">
        <span
          className="badge admin-users-home-badge admin-users-home-badge-warn"
          title="Пробежки, волонтёрства и дата первой пробежки совпали — правило выбора дом не определило"
        >
          не определён
        </span>
        <ul className="admin-users-home-tie">
          {shown.map((item) => (
            <li key={item.identity_key} title={`пробежек: ${formatInt(item.run_days)}`}>
              <LocationLink name={item.name} slug={item.slug} />
              {item.city && <span className="muted"> · {item.city}</span>}
            </li>
          ))}
          {!expanded && hidden > 0 && <li className="muted">…</li>}
        </ul>
        {hidden > 0 && (
          <button type="button" className="btn btn-ghost btn-sm" onClick={() => setExpanded(true)}>
            ещё {formatInt(hidden)}
          </button>
        )}
        {expanded && home.tied.length > HOME_TIE_PREVIEW && (
          <button type="button" className="btn btn-ghost btn-sm" onClick={() => setExpanded(false)}>
            свернуть
          </button>
        )}
      </span>
    );
  }

  return (
    <span className="admin-users-home" title={hint}>
      <LocationLink name={home.name} slug={home.slug} />
      {home.city && <span className="muted admin-users-home-city"> · {home.city}</span>}
      <span
        className="badge admin-users-home-badge"
        title={
          home.is_manual
            ? "Участник выбрал дом сам в настройках профиля"
            : "Определено автоматически: больше пробежек → больше волонтёрств → раньше начал"
        }
      >
        {home.is_manual ? "вручную" : "авто"}
      </span>
    </span>
  );
}

const USERS_PAGE_SIZE = 100;

function AdminUsersContent() {
  const [query, setQuery] = useState("");
  const [draft, setDraft] = useState("");
  const [page, setPage] = useState(1);
  const [items, setItems] = useState<AdminUserListItem[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [sort, setSort] = useState<AdminUsersSort>("created");
  const [direction, setDirection] = useState<AdminUsersSortDirection>("desc");
  const [syncingKey, setSyncingKey] = useState<string | null>(null);
  const [platformsView, setPlatformsView] = useState<"short" | "full">(readPlatformsView);
  const [journalUserId, setJournalUserId] = useState<string | null>(null);
  const [journal, setJournal] = useState<AdminLoginEventsResponse | null>(null);
  const [journalLoading, setJournalLoading] = useState(false);
  const [journalError, setJournalError] = useState<string | null>(null);
  const [visitsUserId, setVisitsUserId] = useState<string | null>(null);
  const [visits, setVisits] = useState<AdminUserVisitsResponse | null>(null);
  const [visitsLoading, setVisitsLoading] = useState(false);
  const [visitsError, setVisitsError] = useState<string | null>(null);
  const [accessUserId, setAccessUserId] = useState<string | null>(null);
  const [access, setAccess] = useState<AdminOrganizerAccessResponse | null>(null);
  const [accessLoading, setAccessLoading] = useState(false);
  const [accessError, setAccessError] = useState<string | null>(null);
  // Индекс локаций для селекта выдачи — грузим один раз при первом открытии панели.
  const [accessLocations, setAccessLocations] = useState<LocationIndexItem[] | null>(null);
  const [snackbar, setSnackbar] = useState<{
    open: boolean;
    title: string;
    message: string;
    variant: "default" | "error";
  }>({ open: false, title: "", message: "", variant: "default" });

  const platformsFull = platformsView === "full";
  // Колонок в строке: девять постоянных плюс одна свёрнутая или четыре системы.
  const columnCount = 9 + (platformsFull ? PLATFORM_CODES.length : 1);

  const changePlatformsView = useCallback((view: "short" | "full") => {
    setPlatformsView(view);
    try {
      window.localStorage.setItem(PLATFORMS_VIEW_STORAGE_KEY, view);
    } catch {
      // приватный режим браузера — вид просто не запомнится
    }
  }, []);

  const offset = (page - 1) * USERS_PAGE_SIZE;
  const totalPages = useMemo(() => Math.max(1, Math.ceil(total / USERS_PAGE_SIZE)), [total]);

  const load = useCallback(
    async (search: string, pageOffset: number, sortKey: AdminUsersSort, sortDir: AdminUsersSortDirection) => {
      setLoading(true);
      setError(null);
      try {
        const response = await listAdminUsers(search, USERS_PAGE_SIZE, pageOffset, sortKey, sortDir);
        setItems(response.items);
        setTotal(response.total);
      } catch (err) {
        setError(err instanceof Error ? err.message : "Не удалось загрузить пользователей");
      } finally {
        setLoading(false);
      }
    },
    [],
  );

  const handleToggleJournal = useCallback(
    async (userId: string) => {
      if (journalUserId === userId) {
        setJournalUserId(null);
        return;
      }
      setJournalUserId(userId);
      setJournal(null);
      setJournalError(null);
      setJournalLoading(true);
      try {
        setJournal(await getAdminUserLoginEvents(userId));
      } catch (err) {
        setJournalError(err instanceof Error ? err.message : "Не удалось загрузить журнал входов");
      } finally {
        setJournalLoading(false);
      }
    },
    [journalUserId],
  );

  const handleToggleVisits = useCallback(
    async (userId: string) => {
      if (visitsUserId === userId) {
        setVisitsUserId(null);
        return;
      }
      setVisitsUserId(userId);
      setVisits(null);
      setVisitsError(null);
      setVisitsLoading(true);
      try {
        setVisits(await getAdminUserVisits(userId));
      } catch (err) {
        setVisitsError(err instanceof Error ? err.message : "Не удалось загрузить журнал визитов");
      } finally {
        setVisitsLoading(false);
      }
    },
    [visitsUserId],
  );

  const handleToggleAccess = useCallback(
    async (userId: string) => {
      if (accessUserId === userId) {
        setAccessUserId(null);
        return;
      }
      setAccessUserId(userId);
      setAccess(null);
      setAccessError(null);
      setAccessLoading(true);
      try {
        const [payload, locationsPayload] = await Promise.all([
          getAdminUserOrganizerAccess(userId),
          accessLocations ? Promise.resolve(null) : getLocationsIndex(),
        ]);
        setAccess(payload);
        if (locationsPayload) {
          setAccessLocations(locationsPayload.items);
        }
      } catch (err) {
        setAccessError(err instanceof Error ? err.message : "Не удалось загрузить оргдоступ");
      } finally {
        setAccessLoading(false);
      }
    },
    [accessUserId, accessLocations],
  );

  const handleSort = (key: AdminUsersSort) => {
    if (sort === key) {
      setDirection((current) => (current === "desc" ? "asc" : "desc"));
    } else {
      setSort(key);
      setDirection("desc");
    }
    setPage(1);
  };

  useEffect(() => {
    const timer = window.setTimeout(() => {
      const trimmed = draft.trim();
      setQuery((prev) => {
        if (trimmed !== prev) {
          setPage(1);
        }
        return trimmed;
      });
    }, 300);
    return () => window.clearTimeout(timer);
  }, [draft]);

  useEffect(() => {
    void load(query, (page - 1) * USERS_PAGE_SIZE, sort, direction);
  }, [load, query, page, sort, direction]);

  const handleSync = useCallback(async (userId: string, platformCode: string) => {
    const key = `${userId}:${platformCode}`;
    setSyncingKey(key);
    try {
      const response = await triggerAdminUserSyncPlatform(userId, platformCode);
      setSnackbar({
        open: true,
        title: response.status === "already_queued" ? "Уже в очереди" : "Запрос принят",
        message: response.message,
        variant: "default",
      });
    } catch (err) {
      setSnackbar({
        open: true,
        title: "Не удалось запустить обновление",
        message: err instanceof Error ? err.message : "Попробуйте позже.",
        variant: "error",
      });
    } finally {
      setSyncingKey((prev) => (prev === key ? null : prev));
    }
  }, []);

  return (
    <AdminShell title="Пользователи">
      <div className="admin-users-page">
      <AdminSubnav activePath="/admin/users" />

      <section className="card admin-users-card">
        <div className="admin-users-toolbar">
          <input
            className="input admin-users-search"
            type="search"
            placeholder="Почта, VK/Яндекс, Telegram, имя в ЛК или ФИО в 5 вёрst/S95/parkrun…"
            value={draft}
            onChange={(event) => setDraft(event.target.value)}
          />
          <div className="admin-users-view" role="group" aria-label="Колонки систем">
            <span className="muted admin-users-view-label">Системы</span>
            <div className="tview-toggle">
              <button
                type="button"
                aria-pressed={!platformsFull}
                className={`tview-tab${platformsFull ? "" : " tview-tab-active"}`}
                title="Одна колонка: только какие системы привязаны"
                onClick={() => changePlatformsView("short")}
              >
                Кратко
              </button>
              <button
                type="button"
                aria-pressed={platformsFull}
                className={`tview-tab${platformsFull ? " tview-tab-active" : ""}`}
                title="Колонка на каждую систему: имя, пробежки/волонтёрства, кнопка обновления"
                onClick={() => changePlatformsView("full")}
              >
                Полно
              </button>
            </div>
          </div>
          <span className="muted admin-users-count">
            {total === 0
              ? "Найдено: 0"
              : `Показано ${formatInt(offset + 1)}–${formatInt(offset + items.length)} из ${formatInt(total)}`}
          </span>
        </div>

        {!loading && !error && total > USERS_PAGE_SIZE && (
          <div className="admin-users-pagination">
            <button
              type="button"
              className="btn secondary btn-sm"
              disabled={page <= 1}
              onClick={() => setPage((current) => Math.max(1, current - 1))}
            >
              Назад
            </button>
            <span className="muted admin-users-pagination-label">
              Страница {formatInt(page)} из {formatInt(totalPages)}
            </span>
            <button
              type="button"
              className="btn secondary btn-sm"
              disabled={page >= totalPages}
              onClick={() => setPage((current) => Math.min(totalPages, current + 1))}
            >
              Вперёд
            </button>
          </div>
        )}

        {loading && <p className="muted">Загрузка…</p>}
        {error && (
          <div className="card error">
            <p>{error}</p>
          </div>
        )}

        {!loading && !error && (
          <div className="table-scroll">
            <table className="data-table admin-users-table">
              <thead>
                <tr>
                  <th>Сервисы входа</th>
                  {platformsFull ? (
                    PLATFORM_CODES.map((code) => <th key={code}>{platformCodeLabel(code)}</th>)
                  ) : (
                    <th title="5 вёрст, С95, parkrun, RunPark — нажмите «Полно», чтобы увидеть имена и счётчики">
                      Системы
                    </th>
                  )}
                  <th title="Площадка, которую человек считает домашней: ручной выбор из настроек или автовыбор по пробежкам">
                    Дом
                  </th>
                  <th>
                    <button
                      type="button"
                      className={`admin-sort-th${sort === "runs" ? " active" : ""}`}
                      onClick={() => handleSort("runs")}
                    >
                      Пробежки {sort === "runs" ? (direction === "asc" ? "▲" : "▼") : ""}
                    </button>
                  </th>
                  <th>
                    <button
                      type="button"
                      className={`admin-sort-th${sort === "volunteering" ? " active" : ""}`}
                      onClick={() => handleSort("volunteering")}
                    >
                      Волонт. {sort === "volunteering" ? (direction === "asc" ? "▲" : "▼") : ""}
                    </button>
                  </th>
                  <th>
                    <button
                      type="button"
                      className={`admin-sort-th${sort === "created" ? " active" : ""}`}
                      onClick={() => handleSort("created")}
                    >
                      Регистрация {sort === "created" ? (direction === "asc" ? "▲" : "▼") : ""}
                    </button>
                  </th>
                  <th>
                    <button
                      type="button"
                      className={`admin-sort-th${sort === "seen" ? " active" : ""}`}
                      onClick={() => handleSort("seen")}
                      title="Последний просмотр страницы на сайте — в отличие от входа, обновляется на каждом заходе"
                    >
                      Был на сайте {sort === "seen" ? (direction === "asc" ? "▲" : "▼") : ""}
                    </button>
                  </th>
                  <th>
                    <button
                      type="button"
                      className={`admin-sort-th${sort === "profile" ? " active" : ""}`}
                      onClick={() => handleSort("profile")}
                      title="Сортировка: сначала пользователи со ссылкой на профиль"
                    >
                      Ссылка профиля {sort === "profile" ? (direction === "asc" ? "▲" : "▼") : ""}
                    </button>
                  </th>
                  <th>Приватность</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {items.length === 0 && (
                  <tr>
                    <td colSpan={columnCount} className="muted">
                      Пользователи не найдены
                    </td>
                  </tr>
                )}
                {items.map((user) => {
                  const logins = userLoginLines(user);
                  return (
                    <Fragment key={user.id}>
                    <tr>
                      <td className="admin-users-telegram">
                        <ul className="admin-users-login-list">
                          {logins.length === 0 ? (
                            <li className="muted">—</li>
                          ) : (
                            logins.map((login) => {
                              const loginUrl = authLoginUrl(login, user);
                              return (
                                <li key={`${login.provider}-${login.external_id}`}>
                                  <span className="admin-users-login-provider">
                                    {authProviderLabel(login.provider)}
                                  </span>
                                  {loginUrl ? (
                                    <a href={loginUrl} target="_blank" rel="noreferrer">
                                      {login.label}
                                    </a>
                                  ) : (
                                    <span>{login.label}</span>
                                  )}
                                </li>
                              );
                            })
                          )}
                        </ul>
                      </td>
                      {platformsFull ? (
                        PLATFORM_CODES.map((code) => (
                          <td key={code}>
                            {platformCell(user, code, syncingKey === `${user.id}:${code}`, handleSync)}
                          </td>
                        ))
                      ) : (
                        <td>
                          <PlatformsSummaryCell user={user} />
                        </td>
                      )}
                      <td><HomeLocationCell home={user.home_location} /></td>
                      <td>{user.total_runs ?? "—"}</td>
                      <td>{user.total_volunteering ?? "—"}</td>
                      <td title={formatDateTime(user.created_at)}>
                        {formatDateTime(user.created_at)}
                      </td>
                      <td><LastSeenCell value={user.last_seen_at} /></td>
                      <td>
                        {user.public_slug ? (
                          <a
                            href={`/users/${user.public_slug}`}
                            target="_blank"
                            rel="noreferrer"
                            className="admin-platform-link"
                          >
                            /{user.public_slug}
                          </a>
                        ) : (
                          <span className="muted">—</span>
                        )}
                      </td>
                      <td>{user.profile_private ? "true" : "false"}</td>
                      <td>
                        <div className="admin-users-actions">
                          {user.serial_id != null && (
                            <a className="btn btn-ghost btn-sm" href={`/users/${user.serial_id}`} target="_blank" rel="noreferrer">
                              Профиль
                            </a>
                          )}
                          <button
                            type="button"
                            className="btn btn-ghost btn-sm"
                            onClick={() => void handleToggleJournal(user.id)}
                          >
                            {journalUserId === user.id ? "Скрыть входы" : "Входы"}
                          </button>
                          <button
                            type="button"
                            className="btn btn-ghost btn-sm"
                            onClick={() => void handleToggleVisits(user.id)}
                            title="Заходы на сайт: когда и по каким страницам ходил"
                          >
                            {visitsUserId === user.id ? "Скрыть визиты" : "Визиты"}
                          </button>
                          <button
                            type="button"
                            className="btn btn-ghost btn-sm"
                            onClick={() => void handleToggleAccess(user.id)}
                          >
                            {accessUserId === user.id ? "Скрыть оргдоступ" : "Оргдоступ"}
                          </button>
                        </div>
                      </td>
                    </tr>
                    {journalUserId === user.id && (
                      <tr className="admin-login-journal-row">
                        <td colSpan={columnCount}>
                          {journalLoading && <p className="muted">Загружаем журнал входов…</p>}
                          {journalError && <p className="form-error">{journalError}</p>}
                          {!journalLoading && !journalError && journal && <LoginJournal data={journal} />}
                        </td>
                      </tr>
                    )}
                    {visitsUserId === user.id && (
                      <tr className="admin-login-journal-row">
                        <td colSpan={columnCount}>
                          {visitsLoading && <p className="muted">Загружаем журнал визитов…</p>}
                          {visitsError && <p className="form-error">{visitsError}</p>}
                          {!visitsLoading && !visitsError && visits && <VisitsJournal data={visits} />}
                        </td>
                      </tr>
                    )}
                    {accessUserId === user.id && (
                      <tr className="admin-login-journal-row">
                        <td colSpan={columnCount}>
                          {accessLoading && <p className="muted">Загружаем оргдоступ…</p>}
                          {accessError && <p className="form-error">{accessError}</p>}
                          {!accessLoading && !accessError && access && (
                            <OrganizerAccessPanel
                              userId={user.id}
                              data={access}
                              locations={accessLocations ?? []}
                              onChanged={setAccess}
                              onError={(message) =>
                                setSnackbar({
                                  open: true,
                                  title: "Оргдоступ",
                                  message,
                                  variant: "error",
                                })
                              }
                            />
                          )}
                        </td>
                      </tr>
                    )}
                    </Fragment>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </section>
      </div>
      <Snackbar
        open={snackbar.open}
        title={snackbar.title}
        variant={snackbar.variant}
        onDismiss={() => setSnackbar((prev) => ({ ...prev, open: false }))}
      >
        {snackbar.message}
      </Snackbar>
    </AdminShell>
  );
}

export function AdminUsersPage() {
  return <RequireAdmin>{() => <AdminUsersContent />}</RequireAdmin>;
}
