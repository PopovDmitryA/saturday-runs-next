import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { AppShell } from "../../components/AppShell";
import { PlatformBadge } from "../../components/PlatformBadge";
import { StatHintTooltip } from "../../components/StatHintTooltip";
import { RequireAdmin } from "../../components/RequireAdmin";
import { AdminSubnav } from "../admin/AdminSubnav";
import { getSyncQueue, type SyncQueueJob, type SyncQueueJobUser, type SyncQueueParkrunQueue, type SyncQueueResponse } from "../../lib/api";
import {
  celeryStateLabel,
  formatDateTime,
  formatDurationShort,
  platformCodeLabel,
  syncStatusLabel,
  syncTriggerLabel,
} from "../../lib/format";

const POLL_ACTIVE_MS = 4000;
const POLL_IDLE_MS = 15000;

function queueUserLabel(user: SyncQueueJobUser): string {
  const customName = user.display_name?.trim();
  if (customName) {
    return customName;
  }
  if (user.telegram_username) {
    const login = user.telegram_username.replace(/^@/, "");
    return `@${login}`;
  }
  if (user.telegram_id != null) {
    return `TG ${user.telegram_id}`;
  }
  return "Участник (без Telegram)";
}

function platformCell(code: string | null): string {
  if (!code) {
    return "—";
  }
  if (code === "all") {
    return "Все";
  }
  return platformCodeLabel(code);
}

const PLATFORM_FILTER_NONE = "__none__";
const PLATFORM_FILTER_ORDER = ["five_verst", "s95", "parkrun", "all", PLATFORM_FILTER_NONE];

function jobPlatformKey(job: SyncQueueJob): string {
  return job.platform_code ?? PLATFORM_FILTER_NONE;
}

function platformFilterLabel(key: string): string {
  return key === PLATFORM_FILTER_NONE ? "—" : platformCell(key);
}

function taskQueueSummary(job: SyncQueueJob): string {
  if (job.tasks.length === 0) {
    if (job.status === "queued" || job.status === "running") {
      return "ожидание";
    }
    return "—";
  }
  return job.tasks
    .map((task) => {
      const label = platformCodeLabel(task.suffix);
      if (task.celery_state === "PENDING" && task.queue_position !== null) {
        return `${label}: ${task.queue_position}/${task.queue_length}`;
      }
      if (task.celery_state === "STARTED") {
        return `${label}: выполняется`;
      }
      return `${label}: ${celeryStateLabel(task.celery_state)}`;
    })
    .join("; ");
}

function jobStatusClass(status: string): string {
  if (status === "queued" || status === "running") {
    return "queue-status-active";
  }
  if (status === "success") {
    return "queue-status-success";
  }
  if (status === "failed") {
    return "queue-status-failed";
  }
  return "";
}

function parkrunStatClass(value: number | boolean): string {
  if (typeof value === "boolean") {
    return value ? "queue-parkrun-stat-alert" : "";
  }
  return value > 0 ? "queue-parkrun-stat-alert" : "";
}

function ParkrunQueuePanel({ stats }: { stats: SyncQueueParkrunQueue }) {
  const needsAttention =
    stats.pending > 0 ||
    stats.failed > 0 ||
    stats.stuck_done > 0 ||
    stats.captcha_pending ||
    (stats.cooldown_remaining_seconds ?? 0) > 0 ||
    stats.s95_pending > 0 ||
    stats.s95_failed > 0 ||
    stats.s95_processing > 0;

  const workerLabel =
    stats.worker_status === "running"
      ? "работает"
      : stats.worker_status === "queued"
        ? "в очереди"
        : stats.worker_alive
          ? "ожидает"
          : "не запущен";

  return (
    <section className={`card queue-parkrun-panel${needsAttention ? " queue-parkrun-panel-alert" : ""}`}>
      <div className="queue-parkrun-panel-head">
        <h2 className="section-title">Очередь ручной обработки (Mac)</h2>
      </div>
      <p className="muted queue-parkrun-intro">
        Заявки на обновление профилей parkrun и s95 (очередь <code>profile_fetch_pending</code>). При
        ненулевых значениях нужна реакция — обработка на Mac (<code>make parkrun</code>): parkrun через
        Chrome CDP, s95 — обычным фетчем.
      </p>
      <div className="queue-parkrun-stats">
        <div className={`queue-parkrun-stat ${parkrunStatClass(stats.pending)}`}>
          <span className="queue-parkrun-stat-value">{stats.pending}</span>
          <span className="queue-parkrun-stat-label">parkrun ожидают</span>
        </div>
        <div className={`queue-parkrun-stat ${parkrunStatClass(stats.failed)}`}>
          <span className="queue-parkrun-stat-value">{stats.failed}</span>
          <span className="queue-parkrun-stat-label">parkrun ошибка</span>
        </div>
        <div className={`queue-parkrun-stat ${parkrunStatClass(stats.stuck_done)}`}>
          <span className="queue-parkrun-stat-value">{stats.stuck_done}</span>
          <span className="queue-parkrun-stat-label">parkrun застряли done</span>
        </div>
        <div className={`queue-parkrun-stat ${parkrunStatClass(stats.processing)}`}>
          <span className="queue-parkrun-stat-value">{stats.processing}</span>
          <span className="queue-parkrun-stat-label">parkrun в обработке</span>
        </div>
        <div className={`queue-parkrun-stat ${parkrunStatClass(stats.celery_sync)}`}>
          <span className="queue-parkrun-stat-value">{stats.celery_sync}</span>
          <span className="queue-parkrun-stat-label">Celery sync</span>
        </div>
        <div className={`queue-parkrun-stat ${parkrunStatClass(stats.captcha_pending)}`}>
          <span className="queue-parkrun-stat-value">{stats.captcha_pending ? "да" : "нет"}</span>
          <span className="queue-parkrun-stat-label">капча</span>
        </div>
        <div
          className={`queue-parkrun-stat ${parkrunStatClass((stats.cooldown_remaining_seconds ?? 0) > 0)}`}
        >
          <span className="queue-parkrun-stat-value">
            {stats.cooldown_remaining_seconds != null && stats.cooldown_remaining_seconds > 0
              ? formatDurationShort(stats.cooldown_remaining_seconds)
              : "—"}
          </span>
          <span className="queue-parkrun-stat-label">cooldown</span>
        </div>
        <div className="queue-parkrun-stat">
          <span className="queue-parkrun-stat-value">{workerLabel}</span>
          <span className="queue-parkrun-stat-label">Mac worker</span>
        </div>
        <div className={`queue-parkrun-stat ${parkrunStatClass(stats.s95_pending)}`}>
          <span className="queue-parkrun-stat-value">{stats.s95_pending}</span>
          <span className="queue-parkrun-stat-label">s95 ожидают</span>
        </div>
        <div className={`queue-parkrun-stat ${parkrunStatClass(stats.s95_failed)}`}>
          <span className="queue-parkrun-stat-value">{stats.s95_failed}</span>
          <span className="queue-parkrun-stat-label">s95 ошибка</span>
        </div>
        <div className={`queue-parkrun-stat ${parkrunStatClass(stats.s95_processing)}`}>
          <span className="queue-parkrun-stat-value">{stats.s95_processing}</span>
          <span className="queue-parkrun-stat-label">s95 в обработке</span>
        </div>
      </div>
    </section>
  );
}

function QueueContent() {
  const [data, setData] = useState<SyncQueueResponse | null>(null);
  const [initialLoading, setInitialLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [platformFilter, setPlatformFilter] = useState<string | null>(null);
  const hasLoadedRef = useRef(false);

  const jobs = data?.jobs ?? [];

  const platformCounts = useMemo(() => {
    const counts = new Map<string, number>();
    for (const job of jobs) {
      const key = jobPlatformKey(job);
      counts.set(key, (counts.get(key) ?? 0) + 1);
    }
    return PLATFORM_FILTER_ORDER.filter((key) => counts.has(key)).map((key) => ({
      key,
      count: counts.get(key) ?? 0,
    }));
  }, [jobs]);

  const visibleJobs = useMemo(
    () => (platformFilter === null ? jobs : jobs.filter((job) => jobPlatformKey(job) === platformFilter)),
    [jobs, platformFilter],
  );

  const load = useCallback(async (options?: { background?: boolean }) => {
    const background = options?.background ?? hasLoadedRef.current;
    if (background) {
      setRefreshing(true);
    } else {
      setInitialLoading(true);
    }
    setError(null);
    try {
      const response = await getSyncQueue();
      setData(response);
      hasLoadedRef.current = true;
    } catch (err) {
      setError(err instanceof Error ? err.message : "Не удалось загрузить очередь");
    } finally {
      setInitialLoading(false);
      setRefreshing(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  useEffect(() => {
    if (!data) {
      return;
    }
    const interval = data.active_jobs_count > 0 ? POLL_ACTIVE_MS : POLL_IDLE_MS;
    const timer = window.setInterval(() => {
      void load({ background: true });
    }, interval);
    return () => window.clearInterval(timer);
  }, [data, load]);

  return (
    <AppShell title="Очередь задач" activePath="/admin">
      <AdminSubnav activePath="/admin/queue" />

      <p className="muted queue-intro">
        Заявки пользователей на обновление профилей — ниже. Отдельно от них работает фоновый пайплайн
        протоколов 5&nbsp;вёрст (субботние старты, реестр локаций).
      </p>

      {data?.parkrun_queue && <ParkrunQueuePanel stats={data.parkrun_queue} />}

      {data?.pipeline && (
        <section className="card queue-pipeline-panel">
          <h2 className="section-title">Пайплайн протоколов</h2>
          <p className="muted queue-pipeline-queues">
            Очередь протоколов 5&nbsp;вёрст: <strong>{data.pipeline.queue_depths.five_verst ?? 0}</strong>
            {data.pipeline.queue_depths.five_verst_user != null && (
              <> · профили 5&nbsp;вёрст: {data.pipeline.queue_depths.five_verst_user}</>
            )}
            {data.pipeline.queue_depths.s95 != null && (
              <> · s95 (протоколы): {data.pipeline.queue_depths.s95}</>
            )}
            {data.pipeline.queue_depths.s95_user != null && (
              <> · s95 (профили): {data.pipeline.queue_depths.s95_user}</>
            )}
            {data.pipeline.queue_depths.parkrun != null && (
              <> · parkrun: {data.pipeline.queue_depths.parkrun}</>
            )}
          </p>
          {data.pipeline.running.length === 0 ? (
            <p className="muted">Сейчас ничего не выполняется.</p>
          ) : (
            <ul className="queue-pipeline-running">
              {data.pipeline.running.map((item, index) => (
                <li key={`${item.label}-${index}`}>
                  <span>{item.label}</span>
                  {item.started_at && (
                    <span className="muted"> · с {formatDateTime(item.started_at)}</span>
                  )}
                </li>
              ))}
            </ul>
          )}
          {(data.pipeline.last_success?.length ?? 0) > 0 && (
            <>
              <h3 className="queue-pipeline-subtitle">Последние успешные прогоны</h3>
              <ul className="queue-pipeline-last-success">
                {data.pipeline.last_success!.map((item, index) => (
                  <li key={`${item.label}-ok-${index}`}>
                    <span>{item.label}</span>
                    {item.finished_at && (
                      <span className="muted"> · {formatDateTime(item.finished_at)}</span>
                    )}
                  </li>
                ))}
              </ul>
            </>
          )}
        </section>
      )}

      <div className="actions-row queue-actions">
        <button
          type="button"
          className="btn secondary btn-sm"
          onClick={() => void load()}
          disabled={initialLoading || refreshing}
        >
          {refreshing ? "Обновление…" : "Обновить"}
        </button>
        {data && (
          <p className="muted queue-summary-inline">
            {data.queues.map((queue, index) => (
              <span key={queue.queue}>
                {index > 0 && " · "}
                {queue.label}: {queue.length}
              </span>
            ))}
            {data.active_jobs_count > 0 && (
              <span className="queue-summary-active"> · активных: {data.active_jobs_count}</span>
            )}
          </p>
        )}
      </div>

      {initialLoading && !data && <p className="muted">Загрузка…</p>}

      {error && (
        <div className="card error">
          <p>{error}</p>
        </div>
      )}

      {data && (
        <>
          {refreshing && <p className="muted page-refresh-hint">Обновляем очередь…</p>}

          {jobs.length === 0 ? (
            <p className="muted">Заявок на обновление пока нет.</p>
          ) : (
            <>
              <div className="map-mode-tabs queue-platform-filter" role="tablist" aria-label="Фильтр по системам">
                <button
                  type="button"
                  role="tab"
                  aria-selected={platformFilter === null}
                  className={platformFilter === null ? "map-mode-tab active" : "map-mode-tab"}
                  onClick={() => setPlatformFilter(null)}
                >
                  Все ({jobs.length})
                </button>
                {platformCounts.map(({ key, count }) => (
                  <button
                    key={key}
                    type="button"
                    role="tab"
                    aria-selected={platformFilter === key}
                    className={platformFilter === key ? "map-mode-tab active" : "map-mode-tab"}
                    onClick={() => setPlatformFilter(key)}
                  >
                    {platformFilterLabel(key)} ({count})
                  </button>
                ))}
              </div>

              {visibleJobs.length === 0 ? (
                <p className="muted">Нет заявок по выбранной системе.</p>
              ) : (
                <div className="table-wrap">
                  <table className="data-table queue-table">
                <thead>
                  <tr>
                    <th>Пользователь</th>
                    <th>Платформа</th>
                    <th>Триггер</th>
                    <th>Статус</th>
                    <th>Создана</th>
                    <th>Очередь</th>
                    <th>Ошибка</th>
                  </tr>
                </thead>
                <tbody>
                  {visibleJobs.map((job) => {
                    const isActive = job.status === "queued" || job.status === "running";
                    const user = job.user;
                    const errorText = job.error_message ?? job.error_details ?? "";

                    return (
                      <tr key={job.id} className={isActive ? "queue-row-active" : undefined}>
                        <td className="queue-cell-user">
                          {user ? (
                            <>
                              <span className="queue-user-name">{queueUserLabel(user)}</span>
                              {user.telegram_id != null ? (
                                <span className="muted queue-user-id">{user.telegram_id}</span>
                              ) : null}
                            </>
                          ) : (
                            "—"
                          )}
                        </td>
                        <td className="queue-cell-platform">
                          {job.platform_code && job.platform_code !== "all" ? (
                            <PlatformBadge code={job.platform_code} />
                          ) : (
                            platformCell(job.platform_code)
                          )}
                        </td>
                        <td>{syncTriggerLabel(job.trigger)}</td>
                        <td>
                          <span className={`queue-status ${jobStatusClass(job.status)}`}>
                            {syncStatusLabel(job.status)}
                          </span>
                        </td>
                        <td className="queue-cell-time" title={formatDateTime(job.created_at)}>
                          {formatDateTime(job.created_at)}
                        </td>
                        <td className="queue-cell-queue">{taskQueueSummary(job)}</td>
                        <td className="queue-cell-error">
                          {errorText ? (
                            <StatHintTooltip
                              text={errorText}
                              className="queue-error-tooltip-trigger"
                            >
                              <span className="queue-error-text">{errorText}</span>
                            </StatHintTooltip>
                          ) : (
                            "—"
                          )}
                        </td>
                      </tr>
                    );
                  })}
                    </tbody>
                  </table>
                </div>
              )}
            </>
          )}
        </>
      )}
    </AppShell>
  );
}

export function QueuePage() {
  return <RequireAdmin>{() => <QueueContent />}</RequireAdmin>;
}
