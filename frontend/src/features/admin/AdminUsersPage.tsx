import { Fragment, useCallback, useEffect, useMemo, useState } from "react";
import { AdminShell } from "./AdminShell";
import { RequireAdmin } from "../../components/RequireAdmin";
import { Snackbar } from "../../components/Snackbar";
import { AdminSubnav } from "./AdminSubnav";
import {
  getAdminUserLoginEvents,
  listAdminUsers,
  triggerAdminUserSyncPlatform,
  type AdminLoginEventsResponse,
  type AdminUserHomeLocation,
  type AdminUserListItem,
  type AdminUsersSort,
  type AdminUsersSortDirection,
} from "../../lib/api";
import { formatDateTime, formatInt, platformCodeLabel } from "../../lib/format";
import { platformProfileUrl } from "../../lib/platformProfileUrl";
import { authLoginUrl, authProviderLabel, userLoginLines } from "./adminUserDisplay";

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
  const [journalUserId, setJournalUserId] = useState<string | null>(null);
  const [journal, setJournal] = useState<AdminLoginEventsResponse | null>(null);
  const [journalLoading, setJournalLoading] = useState(false);
  const [journalError, setJournalError] = useState<string | null>(null);
  const [snackbar, setSnackbar] = useState<{
    open: boolean;
    title: string;
    message: string;
    variant: "default" | "error";
  }>({ open: false, title: "", message: "", variant: "default" });

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
                  <th>{platformCodeLabel("five_verst")}</th>
                  <th>{platformCodeLabel("s95")}</th>
                  <th>{platformCodeLabel("parkrun")}</th>
                  <th>{platformCodeLabel("runpark")}</th>
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
                    <td colSpan={12} className="muted">
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
                      <td>
                        {platformCell(user, "five_verst", syncingKey === `${user.id}:five_verst`, handleSync)}
                      </td>
                      <td>{platformCell(user, "s95", syncingKey === `${user.id}:s95`, handleSync)}</td>
                      <td>
                        {platformCell(user, "parkrun", syncingKey === `${user.id}:parkrun`, handleSync)}
                      </td>
                      <td>{platformCell(user, "runpark", false, handleSync)}</td>
                      <td><HomeLocationCell home={user.home_location} /></td>
                      <td>{user.total_runs ?? "—"}</td>
                      <td>{user.total_volunteering ?? "—"}</td>
                      <td title={formatDateTime(user.created_at)}>
                        {formatDateTime(user.created_at)}
                      </td>
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
                        </div>
                      </td>
                    </tr>
                    {journalUserId === user.id && (
                      <tr className="admin-login-journal-row">
                        <td colSpan={12}>
                          {journalLoading && <p className="muted">Загружаем журнал входов…</p>}
                          {journalError && <p className="form-error">{journalError}</p>}
                          {!journalLoading && !journalError && journal && <LoginJournal data={journal} />}
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
