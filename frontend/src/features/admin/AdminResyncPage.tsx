import { useCallback, useEffect, useRef, useState } from "react";
import type { FormEvent } from "react";
import { AdminShell } from "./AdminShell";
import { RequireAdmin } from "../../components/RequireAdmin";
import {
  createAdminResync,
  getAdminResync,
  listAdminResync,
  type AdminResyncDiff,
  type AdminResyncProtocol,
  type AdminResyncRequest,
  type AdminResyncResult,
} from "../../lib/api";
import { formatDateTime } from "../../lib/format";
import { AdminSubnav } from "./AdminSubnav";
import "./adminResync.css";

// Пока заявка ждёт или идёт — опрашиваем часто, чтобы шаги появлялись живьём.
const POLL_ACTIVE_MS = 2000;
const POLL_LIST_MS = 15000;

const PLATFORM_LABELS: Record<string, string> = {
  five_verst: "5 вёрст",
  s95: "S95",
};

const KIND_LABELS: Record<AdminResyncRequest["kind"], string> = {
  profile: "профиль",
  protocol: "протокол",
  location: "список стартов",
};

const STATUS_LABELS: Record<AdminResyncRequest["status"], string> = {
  queued: "ждёт очереди",
  running: "в работе",
  done: "готово",
  failed: "не удалось",
};

// Зеркало REASON_LABELS в admin_resync_service.py.
const REASON_LABELS: Record<string, string> = {
  requested: "по ссылке",
  new_summary: "нового старта нет в базе",
  summary_changed: "сводка изменилась",
  missing_protocol: "протокол не загружен",
  protocol_never_fetched: "протокол ни разу не качался целиком",
  protocol_debt: "протокол отстал от сводки",
  updated: "источник обновил протокол",
  new: "протокола нет в базе",
  missing_run: "пробежки нет в базе",
  time_mismatch: "не совпало время",
  missing_volunteering: "волонтёрства нет в базе",
  extra_in_db: "у нас есть строка, которой нет в профиле",
};

function isActive(request: AdminResyncRequest | null): boolean {
  return request !== null && (request.status === "queued" || request.status === "running");
}

function stepTime(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return "";
  }
  return date.toLocaleTimeString("ru-RU", { hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

function runLine(run: { name: string; position: number | null; time: string | null }): string {
  const position = run.position !== null ? `${run.position}. ` : "";
  const time = run.time ? ` — ${run.time}` : "";
  return `${position}${run.name}${time}`;
}

function DiffBlock({ diff }: { diff: AdminResyncDiff }) {
  if (!diff.changed) {
    return (
      <div className="muted">
        Без изменений: {diff.runs.after} финишей, {diff.volunteers.after} волонтёров.
      </div>
    );
  }
  const { runs, volunteers } = diff;
  const more = (shown: number, total: number) =>
    total > shown ? <li className="muted">… и ещё {total - shown}</li> : null;
  return (
    <div className="admin-resync-diff">
      {runs.added_total > 0 && (
        <div>
          <div className="admin-resync-diff-title added">Добавлено: {runs.added_total}</div>
          <ul>
            {runs.added.map((run, index) => (
              <li key={`a${index}`}>{runLine(run)}</li>
            ))}
            {more(runs.added.length, runs.added_total)}
          </ul>
        </div>
      )}
      {runs.removed_total > 0 && (
        <div>
          <div className="admin-resync-diff-title removed">Удалено: {runs.removed_total}</div>
          <ul>
            {runs.removed.map((run, index) => (
              <li key={`r${index}`}>{runLine(run)}</li>
            ))}
            {more(runs.removed.length, runs.removed_total)}
          </ul>
        </div>
      )}
      {runs.changed_total > 0 && (
        <div>
          <div className="admin-resync-diff-title changed">Поправлено: {runs.changed_total}</div>
          <ul>
            {runs.changed.map((run, index) => (
              <li key={`c${index}`}>
                {run.name}:{" "}
                {run.position_before !== run.position_after && (
                  <>
                    место {run.position_before ?? "—"} → {run.position_after ?? "—"}
                    {run.time_before !== run.time_after ? ", " : ""}
                  </>
                )}
                {run.time_before !== run.time_after && (
                  <>
                    время {run.time_before ?? "—"} → {run.time_after ?? "—"}
                  </>
                )}
              </li>
            ))}
            {more(runs.changed.length, runs.changed_total)}
          </ul>
        </div>
      )}
      {runs.identified_total > 0 && (
        <div>
          <div className="admin-resync-diff-title identified">Опознано: {runs.identified_total}</div>
          <ul>
            {runs.identified.map((run, index) => (
              <li key={`i${index}`}>{runLine(run)}</li>
            ))}
            {more(runs.identified.length, runs.identified_total)}
          </ul>
        </div>
      )}
      {(volunteers.added_total > 0 || volunteers.removed_total > 0 || volunteers.changed_total > 0) && (
        <div>
          <div className="admin-resync-diff-title">
            Волонтёры: {volunteers.before} → {volunteers.after}
          </div>
          <ul>
            {volunteers.added.map((item, index) => (
              <li key={`va${index}`}>+ {item.name}{item.role ? ` — ${item.role}` : ""}</li>
            ))}
            {volunteers.removed.map((item, index) => (
              <li key={`vr${index}`}>− {item.name}{item.role ? ` — ${item.role}` : ""}</li>
            ))}
            {volunteers.changed.map((item, index) => (
              <li key={`vc${index}`}>
                {item.name}: {item.role_before ?? "—"} → {item.role_after ?? "—"}
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}

function ProtocolCard({ protocol }: { protocol: AdminResyncProtocol }) {
  return (
    <div className="admin-resync-protocol">
      <h4>
        <span>{protocol.label}</span>
        <span className="muted">{REASON_LABELS[protocol.reason] ?? protocol.reason}</span>
        {protocol.error ? (
          <span className="admin-resync-status failed">ошибка</span>
        ) : protocol.changed ? (
          <span className="admin-resync-status running">изменился</span>
        ) : (
          <span className="admin-resync-status done">без изменений</span>
        )}
      </h4>
      {protocol.error && <div className="muted">{protocol.error}</div>}
      {protocol.diff && <DiffBlock diff={protocol.diff} />}
    </div>
  );
}

function LabeledList({ title, items }: { title: string; items: string[] | undefined; total?: number }) {
  if (!items || items.length === 0) {
    return null;
  }
  return (
    <div>
      <div className="admin-resync-diff-title">{title}</div>
      <ul>
        {items.map((item, index) => (
          <li key={index}>{item}</li>
        ))}
      </ul>
    </div>
  );
}

function ResultBlock({ request }: { request: AdminResyncRequest }) {
  const result: AdminResyncResult | null = request.result;
  if (!result) {
    return null;
  }
  return (
    <>
      {request.kind === "profile" && (
        <>
          <div className="admin-resync-facts">
            <span>
              Участник: <b>{result.participant?.name ?? result.label}</b>
            </span>
            <span>
              В профиле пробежек: <b>{result.profile_runs ?? "—"}</b>
              {result.profile_runs_declared != null && result.profile_runs_declared !== result.profile_runs
                ? ` (счётчик профиля ${result.profile_runs_declared})`
                : ""}
            </span>
            <span>
              В базе: <b>{result.db_runs_before ?? "—"}</b> → <b>{result.db_runs_after ?? "—"}</b>
            </span>
          </div>
          <div className="admin-resync-lists">
            <LabeledList
              title={`Не было в базе: ${result.missing_total ?? 0}`}
              items={result.missing?.map((item) => `${item.label}${item.time ? ` — ${item.time}` : ""}`)}
            />
            <LabeledList
              title={`Не совпало время: ${result.mismatched_total ?? 0}`}
              items={result.mismatched?.map(
                (item) => `${item.label}: у нас ${item.db_time ?? "—"}, в профиле ${item.profile_time ?? "—"}`,
              )}
            />
            <LabeledList
              title={`Протокол не качался целиком: ${result.not_fully_loaded_total ?? 0}`}
              items={result.not_fully_loaded?.map((item) => item.label)}
            />
            <LabeledList
              title={`Волонтёрств не было в базе: ${result.missing_volunteering_total ?? 0}`}
              items={result.missing_volunteering?.map((item) => `${item.label}${item.role ? ` — ${item.role}` : ""}`)}
            />
            <LabeledList
              title={`Лишние у нас (нет в профиле): ${result.extra_in_db_total ?? 0}`}
              items={result.extra_in_db?.map((item) => item.label)}
            />
          </div>
        </>
      )}
      {request.kind === "location" && (
        <div className="admin-resync-facts">
          <span>
            Стартов у источника: <b>{result.summaries_total ?? "—"}</b>
          </span>
          <span>
            Совпало: <b>{result.summaries_unchanged ?? "—"}</b>
          </span>
          <span>
            Разошлось: <b>{result.summaries_diverged ?? "—"}</b>
          </span>
          {result.reasons &&
            Object.entries(result.reasons).map(([reason, count]) => (
              <span key={reason} className="muted">
                {REASON_LABELS[reason] ?? reason}: {count}
              </span>
            ))}
        </div>
      )}
      <div className="admin-resync-facts">
        <span>
          Перекачано протоколов: <b>{result.protocols_checked}</b>
        </span>
        <span>
          Изменилось: <b>{result.protocols_changed}</b>
        </span>
        {result.protocols_failed > 0 && (
          <span>
            С ошибкой: <b>{result.protocols_failed}</b>
          </span>
        )}
        {result.deferred_total > 0 && (
          <span>
            В обычной очереди: <b>{result.deferred_total}</b>
          </span>
        )}
      </div>
      {result.protocols.map((protocol, index) => (
        <ProtocolCard key={`${protocol.slug}-${protocol.event_date}-${index}`} protocol={protocol} />
      ))}
      {result.deferred_total > 0 && (
        <LabeledList
          title={`Поставлены в обычную очередь батча (${result.deferred_total})`}
          items={result.deferred}
        />
      )}
    </>
  );
}

function RequestDetail({ request }: { request: AdminResyncRequest }) {
  const live = isActive(request);
  return (
    <section className="card">
      <h2 className="section-title">
        {PLATFORM_LABELS[request.platform_code] ?? request.platform_code} · {KIND_LABELS[request.kind]}{" "}
        <span className={`admin-resync-status ${request.status}`}>{STATUS_LABELS[request.status]}</span>
      </h2>
      <div>
        <a href={request.input_url} target="_blank" rel="noreferrer">
          {request.input_url}
        </a>
      </div>
      {request.status === "queued" && (
        <div className="muted">
          {request.queue_position !== null
            ? `Позиция в приоритетной очереди: ${request.queue_position} из ${request.queue_length ?? "?"}`
            : "Ждём воркер приоритетной очереди"}
        </div>
      )}
      {request.status === "failed" && request.error_message && (
        <div className="admin-resync-status failed" style={{ marginTop: "0.5rem", display: "block" }}>
          {request.error_message}
        </div>
      )}
      {request.status === "done" && request.summary && (
        <div style={{ marginTop: "0.5rem" }}>
          <b>{request.summary}</b>
        </div>
      )}
      <ul className="admin-resync-steps">
        {request.steps.map((step, index) => {
          const last = index === request.steps.length - 1;
          const cls = step.code === "failed" ? "failed" : step.code === "done" ? "done" : last && live ? "live" : "";
          return (
            <li key={`${step.at}-${index}`} className={cls}>
              <span className="admin-resync-step-time muted">{stepTime(step.at)}</span>
              {step.text}
            </li>
          );
        })}
        {live && request.steps.length === 0 && <li className="live">Ждём очереди</li>}
      </ul>
      <ResultBlock request={request} />
    </section>
  );
}

function RequestsTable({
  items,
  selectedId,
  onSelect,
}: {
  items: AdminResyncRequest[];
  selectedId: string | null;
  onSelect: (id: string) => void;
}) {
  if (items.length === 0) {
    return <p className="muted">Заявок ещё не было.</p>;
  }
  return (
    <div className="table-scroll">
      <table className="data-table admin-resync-list">
        <thead>
          <tr>
            <th>Когда</th>
            <th>Что</th>
            <th>Статус</th>
            <th>Итог</th>
          </tr>
        </thead>
        <tbody>
          {items.map((item) => (
            <tr
              key={item.id}
              className={item.id === selectedId ? "selected" : undefined}
              onClick={() => onSelect(item.id)}
            >
              <td>{formatDateTime(item.created_at)}</td>
              <td>
                {PLATFORM_LABELS[item.platform_code] ?? item.platform_code} · {KIND_LABELS[item.kind]}
                <span className="admin-resync-url muted" title={item.input_url}>
                  {item.result?.label ?? item.input_url}
                </span>
              </td>
              <td>
                <span className={`admin-resync-status ${item.status}`}>{STATUS_LABELS[item.status]}</span>
              </td>
              <td className="muted">{item.status === "failed" ? item.error_message : item.summary}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function AdminResyncContent() {
  const [url, setUrl] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);
  const [items, setItems] = useState<AdminResyncRequest[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [selected, setSelected] = useState<AdminResyncRequest | null>(null);
  const selectedRef = useRef<AdminResyncRequest | null>(null);
  selectedRef.current = selected;

  const refreshList = useCallback(async () => {
    try {
      const response = await listAdminResync(30);
      setItems(response.items);
    } catch {
      // Список — вспомогательный; ошибку покажет детальный запрос.
    }
  }, []);

  const refreshSelected = useCallback(async (id: string) => {
    try {
      const request = await getAdminResync(id);
      setSelected(request);
      setItems((current) => current.map((item) => (item.id === request.id ? request : item)));
    } catch (error) {
      setFormError(error instanceof Error ? error.message : String(error));
    }
  }, []);

  useEffect(() => {
    void refreshList();
    const timer = window.setInterval(() => void refreshList(), POLL_LIST_MS);
    return () => window.clearInterval(timer);
  }, [refreshList]);

  useEffect(() => {
    if (!selectedId) {
      setSelected(null);
      return;
    }
    void refreshSelected(selectedId);
    const timer = window.setInterval(() => {
      if (isActive(selectedRef.current)) {
        void refreshSelected(selectedId);
      }
    }, POLL_ACTIVE_MS);
    return () => window.clearInterval(timer);
  }, [selectedId, refreshSelected]);

  const handleSubmit = async (event: FormEvent) => {
    event.preventDefault();
    const value = url.trim();
    if (!value || submitting) {
      return;
    }
    setSubmitting(true);
    setFormError(null);
    try {
      const request = await createAdminResync(value);
      setUrl("");
      setItems((current) => [request, ...current.filter((item) => item.id !== request.id)]);
      setSelected(request);
      setSelectedId(request.id);
    } catch (error) {
      setFormError(error instanceof Error ? error.message : String(error));
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <>
      <section className="card">
        <h2 className="section-title">Обновить по ссылке</h2>
        <form className="admin-resync-form" onSubmit={(event) => void handleSubmit(event)}>
          <input
            type="url"
            value={url}
            onChange={(event) => setUrl(event.target.value)}
            placeholder="https://5verst.ru/…  или  https://s95.ru/…"
            disabled={submitting}
          />
          <button type="submit" className="btn" disabled={submitting || !url.trim()}>
            {submitting ? "Ставим в очередь…" : "Обновить"}
          </button>
        </form>
        <p className="admin-resync-hint muted">
          Профиль (5verst.ru/userstats/…, s95.ru/athletes/…) — сверяем каждую пробежку с базой и перекачиваем целиком
          протоколы, где человек есть у источника, а у нас нет. Протокол (…/results/DD.MM.YYYY/, s95.ru/activities/…) —
          перекачиваем целиком и показываем, что изменилось. Список стартов локации (…/results/all/, s95.ru/events/…) —
          сверяем сводку и перекачиваем только расходящиеся. Заявка идёт в приоритетной ветке общей очереди: батчи
          уступают, но правила источника соблюдаются.
        </p>
        {formError && <div className="admin-resync-status failed">{formError}</div>}
      </section>
      <div className="admin-resync-layout">
        <section className="card">
          <h2 className="section-title">Последние заявки</h2>
          <RequestsTable items={items} selectedId={selectedId} onSelect={setSelectedId} />
        </section>
        {selected ? (
          <RequestDetail request={selected} />
        ) : (
          <section className="card">
            <p className="muted">Выберите заявку, чтобы увидеть ход и итог.</p>
          </section>
        )}
      </div>
    </>
  );
}

export function AdminResyncPage() {
  return (
    <RequireAdmin>
      <AdminShell title="Обновить по ссылке">
        <AdminSubnav activePath="/admin/resync" />
        <AdminResyncContent />
      </AdminShell>
    </RequireAdmin>
  );
}
