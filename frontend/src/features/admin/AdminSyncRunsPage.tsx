import { useCallback, useEffect, useRef, useState } from "react";
import { AppShell } from "../../components/AppShell";
import { RequireAdmin } from "../../components/RequireAdmin";
import {
  getAdminSyncRuns,
  type AdminSyncRunsResponse,
  type SyncPlatformSummary,
  type SyncRunItem,
  type SyncRunMetric,
} from "../../lib/api";
import { formatDateTime } from "../../lib/format";
import { AdminSubnav } from "./AdminSubnav";

const PERIODS = [
  { label: "Сегодня", days: 1 },
  { label: "3 дня", days: 3 },
  { label: "7 дней", days: 7 },
  { label: "30 дней", days: 30 },
] as const;

const PLATFORM_FILTERS = [
  { value: "", label: "Все системы" },
  { value: "five_verst", label: "5 вёрст" },
  { value: "s95", label: "S95" },
  { value: "parkrun", label: "parkrun" },
  { value: "runpark", label: "RunPark" },
] as const;

const STATUS_FILTERS = [
  { value: "", label: "Все запуски" },
  { value: "problems", label: "Только с ошибками" },
  { value: "skipped", label: "Только пропущенные" },
] as const;

const STATUS_LABELS: Record<string, string> = {
  ok: "✅ успешно",
  error: "⚠️ с ошибками",
  failed: "⛔ упал",
  skipped: "⏭️ пропущен",
};

// Почему запуск не состоялся — коды из run_reported_sync.
const SKIP_REASON_LABELS: Record<string, string> = {
  batch_queue_full: "очередь переполнена",
  s95_fetch_cooldown: "охлаждение после ошибок s95",
  duplicate_hour_slot: "дубль в том же часу",
};

function statusLabel(status: string): string {
  return STATUS_LABELS[status] ?? status;
}

function pluralRuns(count: number): string {
  const tailHundred = count % 100;
  if (tailHundred >= 11 && tailHundred <= 14) {
    return "запусков";
  }
  const tail = count % 10;
  if (tail === 1) {
    return "запуск";
  }
  if (tail >= 2 && tail <= 4) {
    return "запуска";
  }
  return "запусков";
}

function formatDuration(ms: number | null): string {
  if (ms === null) {
    return "—";
  }
  if (ms < 1000) {
    return `${ms} мс`;
  }
  const seconds = Math.round(ms / 1000);
  if (seconds < 60) {
    return `${seconds} c`;
  }
  const minutes = Math.floor(seconds / 60);
  const rest = seconds % 60;
  return rest > 0 ? `${minutes} мин ${rest} c` : `${minutes} мин`;
}

function formatMetricValue(value: SyncRunMetric["value"]): string {
  if (typeof value === "boolean") {
    return value ? "да" : "нет";
  }
  if (typeof value === "number") {
    return value.toLocaleString("ru-RU");
  }
  return value;
}

function MetricList({ metrics, limit }: { metrics: SyncRunMetric[]; limit?: number }) {
  const shown = limit ? metrics.slice(0, limit) : metrics;
  if (shown.length === 0) {
    return <span className="muted">—</span>;
  }
  return (
    <ul className="sync-runs-metrics">
      {shown.map((metric) => (
        <li key={metric.key}>
          <span className="muted">{metric.label}:</span> {formatMetricValue(metric.value)}
        </li>
      ))}
    </ul>
  );
}

function PlatformCard({ platform }: { platform: SyncPlatformSummary }) {
  return (
    <section className="card">
      <h2 className="section-title">
        {platform.platform_label} — {platform.runs} {pluralRuns(platform.runs)}
        {platform.problems > 0 && <span className="sync-runs-badge error"> · {platform.problems} с ошибками</span>}
        {platform.skipped > 0 && <span className="muted"> · пропущено {platform.skipped}</span>}
      </h2>

      <MetricList metrics={platform.metrics} limit={8} />

      <div className="table-scroll">
        <table className="data-table">
          <thead>
            <tr>
              <th>Скрипт</th>
              <th>Запусков</th>
              <th>Ошибок</th>
              <th>Пропущено</th>
              <th>Последний запуск</th>
              <th>Что обновилось</th>
            </tr>
          </thead>
          <tbody>
            {platform.pipelines.map((pipeline) => (
              <tr key={pipeline.pipeline}>
                <td>{pipeline.pipeline_label}</td>
                <td>{pipeline.runs}</td>
                <td className={pipeline.problems > 0 ? "sync-runs-badge error" : "muted"}>
                  {pipeline.problems > 0 ? pipeline.problems : "—"}
                </td>
                <td className="muted">{pipeline.skipped > 0 ? pipeline.skipped : "—"}</td>
                <td>
                  {pipeline.last_run_at ? formatDateTime(pipeline.last_run_at) : "—"}
                  {pipeline.last_status && (
                    <div className="muted">{statusLabel(pipeline.last_status)}</div>
                  )}
                </td>
                <td>
                  <MetricList metrics={pipeline.metrics} limit={4} />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}

function RunRow({ run }: { run: SyncRunItem }) {
  const [expanded, setExpanded] = useState(false);
  const hasDetails = run.metrics.length > 0 || run.errors.length > 0;

  return (
    <>
      <tr>
        <td>{formatDateTime(run.started_at)}</td>
        <td>{run.pipeline_label}</td>
        <td>
          {statusLabel(run.status)}
          {run.skip_reason && (
            <div className="muted">{SKIP_REASON_LABELS[run.skip_reason] ?? run.skip_reason}</div>
          )}
        </td>
        <td>{formatDuration(run.duration_ms)}</td>
        <td>{run.records_updated > 0 ? run.records_updated.toLocaleString("ru-RU") : "—"}</td>
        <td>
          {hasDetails ? (
            <button type="button" className="btn secondary" onClick={() => setExpanded((value) => !value)}>
              {expanded ? "Скрыть" : "Подробнее"}
            </button>
          ) : (
            <span className="muted">—</span>
          )}
        </td>
      </tr>
      {expanded && (
        <tr>
          <td colSpan={6}>
            <MetricList metrics={run.metrics} />
            {run.errors.length > 0 && (
              <ul className="sync-runs-errors">
                {run.errors.map((error, index) => (
                  <li key={index}>{error}</li>
                ))}
              </ul>
            )}
          </td>
        </tr>
      )}
    </>
  );
}

const PAGE_SIZE = 100;

function AdminSyncRunsContent() {
  const [periodDays, setPeriodDays] = useState<number>(7);
  const [platform, setPlatform] = useState("");
  const [status, setStatus] = useState("");
  const [offset, setOffset] = useState(0);
  const [data, setData] = useState<AdminSyncRunsResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const loadSeq = useRef(0);

  const load = useCallback(async () => {
    const seq = ++loadSeq.current;
    setLoading(true);
    setError(null);
    try {
      const payload = await getAdminSyncRuns({
        periodDays,
        platform: platform || undefined,
        status: status || undefined,
        limit: PAGE_SIZE,
        offset,
      });
      if (seq !== loadSeq.current) {
        return;
      }
      setData(payload);
    } catch (err) {
      if (seq !== loadSeq.current) {
        return;
      }
      setError(err instanceof Error ? err.message : "Не удалось загрузить историю");
    } finally {
      if (seq === loadSeq.current) {
        setLoading(false);
      }
    }
  }, [periodDays, platform, status, offset]);

  useEffect(() => {
    void load();
  }, [load]);

  const totalRuns = data?.platforms.reduce((sum, item) => sum + item.runs, 0) ?? 0;
  const totalProblems = data?.platforms.reduce((sum, item) => sum + item.problems, 0) ?? 0;

  return (
    <AppShell title="Автообновление" activePath="/admin">
      <AdminSubnav activePath="/admin/sync-runs" />

      <div className="admin-stats-toolbar">
        <div className="admin-stats-periods" role="tablist" aria-label="Период">
          {PERIODS.map((item) => (
            <button
              key={item.days}
              type="button"
              className={periodDays === item.days ? "btn primary" : "btn secondary"}
              onClick={() => {
                setOffset(0);
                setPeriodDays(item.days);
              }}
            >
              {item.label}
            </button>
          ))}
        </div>
        {data?.generated_at && (
          <span className="muted admin-stats-generated">Обновлено: {formatDateTime(data.generated_at)}</span>
        )}
      </div>

      {!loading && !error && data && (
        <p className={totalProblems > 0 ? "sync-runs-badge error" : "muted"}>
          Всего за период: {totalRuns} {pluralRuns(totalRuns)}
          {totalProblems > 0 ? `, из них с ошибками: ${totalProblems}` : ", ошибок нет"}
        </p>
      )}

      {loading && <p className="muted">Загрузка…</p>}
      {error && (
        <div className="card error">
          <p>{error}</p>
        </div>
      )}

      {!loading && !error && data && (
        <>
          {data.platforms.length === 0 ? (
            <section className="card">
              <p className="muted">
                За выбранный период запусков не было. История копится с момента включения раздела:
                каждая задача расписания пишет сюда свой итог.
              </p>
            </section>
          ) : (
            data.platforms.map((item) => <PlatformCard key={item.platform} platform={item} />)
          )}

          <section className="card">
            <h2 className="section-title">Лента запусков</h2>

            <div className="admin-stats-periods">
              {PLATFORM_FILTERS.map((item) => (
                <button
                  key={item.value}
                  type="button"
                  className={platform === item.value ? "btn primary" : "btn secondary"}
                  onClick={() => {
                    setOffset(0);
                    setPlatform(item.value);
                  }}
                >
                  {item.label}
                </button>
              ))}
            </div>
            <div className="admin-stats-periods">
              {STATUS_FILTERS.map((item) => (
                <button
                  key={item.value}
                  type="button"
                  className={status === item.value ? "btn primary" : "btn secondary"}
                  onClick={() => {
                    setOffset(0);
                    setStatus(item.value);
                  }}
                >
                  {item.label}
                </button>
              ))}
            </div>

            {data.runs.length === 0 ? (
              <p className="muted">Запусков под фильтр не нашлось.</p>
            ) : (
              <div className="table-scroll">
                <table className="data-table">
                  <thead>
                    <tr>
                      <th>Когда</th>
                      <th>Скрипт</th>
                      <th>Статус</th>
                      <th>Длительность</th>
                      <th>Обновлено</th>
                      <th />
                    </tr>
                  </thead>
                  <tbody>
                    {data.runs.map((run) => (
                      <RunRow key={run.id} run={run} />
                    ))}
                  </tbody>
                </table>
              </div>
            )}

            {data.total > PAGE_SIZE && (
              <div className="admin-stats-periods">
                <button
                  type="button"
                  className="btn secondary"
                  disabled={offset === 0}
                  onClick={() => setOffset((value) => Math.max(value - PAGE_SIZE, 0))}
                >
                  Назад
                </button>
                <span className="muted">
                  {offset + 1}–{Math.min(offset + PAGE_SIZE, data.total)} из {data.total}
                </span>
                <button
                  type="button"
                  className="btn secondary"
                  disabled={offset + PAGE_SIZE >= data.total}
                  onClick={() => setOffset((value) => value + PAGE_SIZE)}
                >
                  Дальше
                </button>
              </div>
            )}
          </section>

          <p className="muted admin-stats-footnote">
            Сюда пишется каждый запуск задач расписания. «Пропущен» — задача не пошла: очередь была
            переполнена, действовало охлаждение после ошибок или в том же часу уже был запуск.
            Раз в сутки в 21:50 итоги уходят одним сообщением в ВК. История хранится 120 дней.
          </p>
        </>
      )}
    </AppShell>
  );
}

export function AdminSyncRunsPage() {
  return <RequireAdmin>{() => <AdminSyncRunsContent />}</RequireAdmin>;
}
