import { useCallback, useEffect, useRef, useState } from "react";
import { AppShell } from "../../components/AppShell";
import { PlatformBadge } from "../../components/PlatformBadge";
import { RequireAdmin } from "../../components/RequireAdmin";
import { AdminSubnav } from "../admin/AdminSubnav";
import { getSyncQueue, type SyncQueueJob, type SyncQueueJobUser, type SyncQueueResponse } from "../../lib/api";
import {
  celeryStateLabel,
  formatDateTime,
  platformCodeLabel,
  syncStatusLabel,
  syncTriggerLabel,
} from "../../lib/format";

const POLL_INTERVAL_MS = 4000;

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

function QueueContent() {
  const [data, setData] = useState<SyncQueueResponse | null>(null);
  const [initialLoading, setInitialLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const hasLoadedRef = useRef(false);

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
    if (!data || data.active_jobs_count === 0) {
      return;
    }
    const timer = window.setInterval(() => {
      void load({ background: true });
    }, POLL_INTERVAL_MS);
    return () => window.clearInterval(timer);
  }, [data?.active_jobs_count, load]);

  return (
    <AppShell title="Очередь задач" activePath="/admin">
      <AdminSubnav activePath="/admin/queue" />

      <p className="muted queue-intro">
        Все заявки на обновление данных от пользователей. Статус обновляется автоматически, пока есть
        активные задачи.
      </p>

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

          {data.jobs.length === 0 ? (
            <p className="muted">Заявок на обновление пока нет.</p>
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
                  {data.jobs.map((job) => {
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
                        <td
                          className="queue-cell-error"
                          title={errorText || undefined}
                        >
                          {errorText ? (
                            <span className="queue-error-text">{errorText}</span>
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
    </AppShell>
  );
}

export function QueuePage() {
  return <RequireAdmin>{() => <QueueContent />}</RequireAdmin>;
}
