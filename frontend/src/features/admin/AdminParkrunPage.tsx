import { useCallback, useEffect, useState } from "react";
import { AppShell } from "../../components/AppShell";
import { RequireAdmin } from "../../components/RequireAdmin";
import {
  clearAdminParkrunCaptchaWait,
  getAdminParkrunSessionStatus,
  processAdminParkrunPending,
  resetAdminParkrunFailedPending,
  saveAdminParkrunSession,
  startAdminParkrunLocalWorker,
  type ParkrunCdpSaveResponse,
  type ParkrunProcessPendingResponse,
  type ParkrunSessionStatus,
} from "../../lib/api";
import { formatDurationShort } from "../../lib/format";
import { AdminSubnav } from "./AdminSubnav";

const CHROME_CMD = `/Applications/Google\\ Chrome.app/Contents/MacOS/Google\\ Chrome \\
  --remote-debugging-port=9222 \\
  --remote-debugging-address=0.0.0.0 \\
  --remote-allow-origins='*' \\
  --user-data-dir="$HOME/.chrome-parkrun-profile"`;

function AdminParkrunContent() {
  const [status, setStatus] = useState<ParkrunSessionStatus | null>(null);
  const [cdpUrl, setCdpUrl] = useState("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [actionLoading, setActionLoading] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);
  const [lastProcess, setLastProcess] = useState<ParkrunProcessPendingResponse | null>(null);

  const load = useCallback(async () => {
    setError(null);
    try {
      const response = await getAdminParkrunSessionStatus();
      setStatus(response);
      setCdpUrl((prev) => (prev.trim() ? prev : response.default_cdp_url));
    } catch (err) {
      setError(err instanceof Error ? err.message : "Не удалось загрузить статус parkrun");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  useEffect(() => {
    if (!status?.captcha_pending) {
      return;
    }
    const timer = window.setInterval(() => {
      void load();
    }, 10_000);
    return () => window.clearInterval(timer);
  }, [status?.captcha_pending, load]);

  useEffect(() => {
    const active =
      status?.worker_status === "queued" || status?.worker_status === "running";
    if (!active) {
      return;
    }
    const timer = window.setInterval(() => {
      void load();
    }, 2000);
    return () => window.clearInterval(timer);
  }, [status?.worker_status, load]);

  async function runAction(
    key: string,
    fn: () => Promise<
      { message?: string; reset?: number } | ParkrunProcessPendingResponse | ParkrunCdpSaveResponse
    >,
  ) {
    setActionLoading(key);
    setError(null);
    setSuccess(null);
    setLastProcess(null);
    try {
      const result = await fn();
      if ("summary" in result) {
        setLastProcess(result);
        const errCount = result.summary.error ?? 0;
        setSuccess(
          errCount > 0
            ? `Очередь обработана, ошибок: ${errCount}. См. детали ниже.`
            : "Очередь обработана успешно",
        );
      } else if ("storage_path" in result) {
        setSuccess(
          `${result.message} Файл: ${result.storage_path}` +
            (result.page_title ? ` (вкладка: ${result.page_title})` : ""),
        );
      } else if (result.message) {
        setSuccess(result.message);
      }
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Ошибка операции");
    } finally {
      setActionLoading(null);
    }
  }

  const waitingCaptcha = status?.captcha_pending ?? false;
  const cooldownSec = status?.cooldown_remaining_seconds ?? null;

  return (
    <AppShell title="Parkrun — сессия" activePath="/admin/parkrun">
      <AdminSubnav activePath="/admin/parkrun" />

      {waitingCaptcha ? (
        <section className="card" role="alert">
          <h2 className="section-title">Нужна капча</h2>
          <p>
            Запросы к parkrun приостановлены, пока вы не пройдёте проверку в Chrome и не сохраните
            сессию. В очереди: <strong>{status?.pending_queue_count ?? 0}</strong> профилей.
          </p>
          {status?.captcha_message ? (
            <p className="muted">
              <code>{status.captcha_message}</code>
            </p>
          ) : null}
        </section>
      ) : null}

      <section className="card">
        <h2 className="section-title">Статус</h2>
        {loading && !status ? <p className="muted">Загрузка…</p> : null}
        {error ? (
          <div className="card error" role="alert">
            <p>{error}</p>
          </div>
        ) : null}
        {success ? (
          <div className="card success" role="status">
            <p className="success-text">{success}</p>
          </div>
        ) : null}
        {status ? (
          <ul className="admin-kv-list">
            <li>
              Ожидание капчи: <strong>{status.captcha_pending ? "да" : "нет"}</strong>
            </li>
            <li>
              Cooldown:{" "}
              {cooldownSec != null && cooldownSec > 0
                ? formatDurationShort(Math.ceil(cooldownSec))
                : "нет"}
            </li>
            <li>
              Файл сессии:{" "}
              <code>{status.storage_state_path}</code> —{" "}
              {status.storage_state_exists ? "есть" : "нет"}
            </li>
            <li>
              Очередь (pending): <strong>{status.pending_queue_count}</strong>
              {status.failed_queue_count > 0 ? (
                <>
                  {" "}
                  · failed: <strong>{status.failed_queue_count}</strong>
                </>
              ) : null}
              {(status.stuck_done_queue_count ?? 0) > 0 ? (
                <>
                  {" "}
                  · done без привязки: <strong>{status.stuck_done_queue_count}</strong>
                  {" "}
                  (вернутся в очередь при запуске воркера)
                </>
              ) : null}
            </li>
          </ul>
        ) : null}
        <button type="button" className="btn secondary" onClick={() => void load()} disabled={loading}>
          Обновить
        </button>
      </section>

      <section className="card">
        <h2 className="section-title">1. Chrome с отладкой</h2>
        <p className="muted">
          Обычный Chrome может оставаться открытым — это другой процесс. Важно запустить{" "}
          <strong>отдельный</strong> Chrome с командой ниже (не вкладку в обычном окне).
        </p>
        <pre className="code-block">{CHROME_CMD}</pre>
        <ul className="muted admin-hints">
          <li>
            Профиль <code>~/.chrome-parkrun-profile</code> сохраняется между запусками — так проще
            пройти капчу, чем с пустым <code>/tmp/...</code>.
          </li>
          <li>
            Режим отладки parkrun часто воспринимает как бота: капча по кругу — нормальная реакция
            на «чистый» профиль. Подождите 1–2 минуты на странице после успеха, не обновляйте F5.
          </li>
          <li>
            Когда видите обычный сайт (не «Human Verification»), сразу нажмите «Сохранить сессию» —
            сервер <strong>не</strong> открывает parkrun заново, только снимает cookies с вкладки.
          </li>
          <li>
            Проверка с Mac: <code>curl -s http://127.0.0.1:9222/json/version</code> должен вернуть JSON
            (не ошибку 500). Иначе закройте Chrome и запустите команду выше заново.
          </li>
        </ul>
      </section>

      <section className="card">
        <h2 className="section-title">2. Сохранить cookies</h2>
        <p className="muted">
          На Mac кнопка ниже часто даёт <strong>HTTP 500</strong> (Docker не достучится до Chrome). Тогда в
          терминале в корне проекта (Chrome с отладкой запущен, parkrun без капчи):
        </p>
        <pre className="code-block">cd ~/Projects/saturday_runs_stats{"\n"}make parkrun-save-cdp-host</pre>
        <p className="muted">
          Нужен conda. Затем в админке «Обновить» — «Файл сессии — есть».
        </p>
        <p className="muted">
          <strong>Или из админки</strong> (если CDP из Docker работает)
        </p>
        <label className="form-label" htmlFor="parkrun-cdp-url">
          CDP URL (из контейнера api к Mac)
        </label>
        <input
          id="parkrun-cdp-url"
          className="input"
          value={cdpUrl}
          onChange={(e) => setCdpUrl(e.target.value)}
          placeholder="http://host.docker.internal:9222"
        />
        <div className="form-actions">
          <button
            type="button"
            className="btn primary"
            disabled={actionLoading !== null}
            onClick={() =>
              void runAction("save", () => saveAdminParkrunSession(cdpUrl.trim()))
            }
          >
            {actionLoading === "save" ? "Сохранение…" : "Сохранить сессию из Chrome"}
          </button>
          <button
            type="button"
            className="btn secondary"
            disabled={actionLoading !== null}
            onClick={() => void runAction("clear", clearAdminParkrunCaptchaWait)}
          >
            Сбросить ожидание капчи
          </button>
        </div>
      </section>

      <section className="card">
        <h2 className="section-title">3. Очередь на Mac (рекомендуется)</h2>
        <p className="muted">
          Один раз в день на Mac: <code>make parkrun</code> — очередь из БД, сам поднимает Chromium
          (как legacy .py), при капче ждёт в том же окне.
        </p>
        <pre className="code-block">cd ~/Projects/saturday_runs_stats{"\n"}make parkrun-seed-queue{"\n"}make parkrun</pre>
        <p className="muted">
          Статус в шапке вкладки Chrome и в терминале. Очередь (pending):{" "}
          <strong>{status?.pending_queue_count ?? 0}</strong>
          {(status?.stuck_done_queue_count ?? 0) > 0 ? (
            <> · done без привязки: {status?.stuck_done_queue_count}</>
          ) : null}
        </p>
        <details className="muted">
          <summary>Расширенно: кнопка из браузера (нужен отдельный воркер)</summary>
          <p className="muted">
            Воркер:{" "}
            <strong>
              {status?.worker_alive ? "запущен" : "не виден (make parkrun-local-worker)"}
            </strong>
            {status?.worker_status ? ` · ${status.worker_status}` : null}
          </p>
          <div className="form-actions">
            <button
              type="button"
              className="btn secondary"
              disabled={actionLoading !== null || !status?.worker_alive}
              onClick={() =>
                void runAction("local", () => startAdminParkrunLocalWorker(true))
              }
            >
              {actionLoading === "local" ? "Запуск…" : "Поставить задачу воркеру"}
            </button>
            <button
              type="button"
              className="btn secondary btn-sm"
              disabled={actionLoading !== null}
              onClick={() => void runAction("reset", resetAdminParkrunFailedPending)}
            >
              Вернуть failed в очередь
            </button>
          </div>
        </details>
        <p className="muted">
          Кнопка «Docker» ниже часто снова ловит капчу — для Mac используйте{" "}
          <code>make parkrun</code>.
        </p>
        <button
          type="button"
          className="btn secondary btn-sm"
          disabled={actionLoading !== null || waitingCaptcha}
          onClick={() => void runAction("process", () => processAdminParkrunPending(10))}
        >
          Обработать через Docker (не рекомендуется)
        </button>
        {lastProcess ? (
          <pre className="code-block">
            {JSON.stringify(lastProcess.summary, null, 2)}
            {"\n"}
            {lastProcess.details.join("\n")}
          </pre>
        ) : null}
      </section>
    </AppShell>
  );
}

export function AdminParkrunPage() {
  return <RequireAdmin>{() => <AdminParkrunContent />}</RequireAdmin>;
}
