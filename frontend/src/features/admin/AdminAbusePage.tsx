import { useCallback, useEffect, useState } from "react";
import { AppShell } from "../../components/AppShell";
import { ConfirmModal } from "../../components/ConfirmModal";
import { RequireAdmin } from "../../components/RequireAdmin";
import {
  clearAdminIpAbuseScore,
  createAdminAbuseBan,
  deleteAdminIpBlock,
  deleteAdminTelegramBan,
  listAdminAbuseBlocks,
  type AbuseIpBlockItem,
  type AbuseTelegramBanItem,
} from "../../lib/api";
import { formatDateTime, formatDurationShort } from "../../lib/format";
import { AdminSubnav } from "./AdminSubnav";

const DURATION_PRESETS = [
  { label: "1 час", seconds: 3600 },
  { label: "24 часа", seconds: 86400 },
  { label: "7 дней", seconds: 7 * 86400 },
  { label: "30 дней", seconds: 30 * 86400 },
] as const;

function telegramLabel(item: AbuseTelegramBanItem): string {
  if (item.telegram_username) {
    return `@${item.telegram_username.replace(/^@/, "")}`;
  }
  if (item.display_name) {
    return item.display_name;
  }
  return String(item.telegram_id);
}

function sourceLabel(source: string): string {
  if (source === "manual") {
    return "вручную";
  }
  if (source === "auto") {
    return "авто";
  }
  return source;
}

function AdminAbuseContent() {
  const [ipBlocks, setIpBlocks] = useState<AbuseIpBlockItem[]>([]);
  const [telegramBans, setTelegramBans] = useState<AbuseTelegramBanItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [actionLoading, setActionLoading] = useState<string | null>(null);

  const [target, setTarget] = useState("");
  const [durationSeconds, setDurationSeconds] = useState(DURATION_PRESETS[1].seconds);
  const [reason, setReason] = useState("");
  const [banIp, setBanIp] = useState(true);
  const [banAccount, setBanAccount] = useState(true);
  const [formError, setFormError] = useState<string | null>(null);
  const [formSuccess, setFormSuccess] = useState<string | null>(null);

  const [confirmUnban, setConfirmUnban] = useState<
    { kind: "ip"; value: string } | { kind: "telegram"; value: number } | null
  >(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const response = await listAdminAbuseBlocks();
      setIpBlocks(response.ip_blocks);
      setTelegramBans(response.telegram_bans);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Не удалось загрузить блокировки");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const handleCreateBan = async () => {
    setFormError(null);
    setFormSuccess(null);
    const trimmed = target.trim();
    if (!trimmed) {
      setFormError("Укажите IP, Telegram ID или @username");
      return;
    }
    setActionLoading("create");
    try {
      const response = await createAdminAbuseBan({
        target: trimmed,
        duration_seconds: durationSeconds,
        reason,
        ban_ip: banIp,
        ban_account: banAccount,
      });
      setFormSuccess(`Блокировка создана: ${response.label}`);
      setTarget("");
      setReason("");
      await load();
    } catch (err) {
      setFormError(err instanceof Error ? err.message : "Не удалось создать блокировку");
    } finally {
      setActionLoading(null);
    }
  };

  const handleUnban = async () => {
    if (!confirmUnban) {
      return;
    }
    setActionLoading(`unban-${confirmUnban.kind}-${confirmUnban.value}`);
    try {
      if (confirmUnban.kind === "ip") {
        await deleteAdminIpBlock(confirmUnban.value);
      } else {
        await deleteAdminTelegramBan(confirmUnban.value);
      }
      setConfirmUnban(null);
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Не удалось снять блокировку");
    } finally {
      setActionLoading(null);
    }
  };

  const handleClearScore = async (ip: string) => {
    setActionLoading(`score-${ip}`);
    try {
      await clearAdminIpAbuseScore(ip);
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Не удалось сбросить score");
    } finally {
      setActionLoading(null);
    }
  };

  return (
    <AppShell title="Блокировки" activePath="/admin">
      <AdminSubnav activePath="/admin/abuse" />

      <section className="card admin-abuse-form">
        <h2 className="section-title">Заблокировать</h2>
        <p className="muted admin-abuse-form-hint">
          IP (например 203.0.113.10), Telegram ID или @username. Для пользователя можно заблокировать аккаунт
          и/или его последний известный IP (сохраняется при входе на сайт).
        </p>
        <label className="field">
          <span className="field-label">Цель</span>
          <input
            className="input"
            type="text"
            value={target}
            onChange={(event) => setTarget(event.target.value)}
            placeholder="IP, 336690860 или @username"
          />
        </label>
        <label className="field">
          <span className="field-label">Длительность</span>
          <select
            className="input"
            value={durationSeconds}
            onChange={(event) => setDurationSeconds(Number(event.target.value))}
          >
            {DURATION_PRESETS.map((preset) => (
              <option key={preset.seconds} value={preset.seconds}>
                {preset.label}
              </option>
            ))}
          </select>
        </label>
        <label className="field">
          <span className="field-label">Причина</span>
          <input
            className="input"
            type="text"
            value={reason}
            onChange={(event) => setReason(event.target.value)}
            placeholder="Необязательно"
          />
        </label>
        <div className="admin-abuse-form-checks">
          <label className="login-consent-field">
            <input type="checkbox" checked={banIp} onChange={(event) => setBanIp(event.target.checked)} />
            <span>Заблокировать IP</span>
          </label>
          <label className="login-consent-field">
            <input
              type="checkbox"
              checked={banAccount}
              onChange={(event) => setBanAccount(event.target.checked)}
            />
            <span>Заблокировать аккаунт Telegram</span>
          </label>
        </div>
        {formError && (
          <div className="profile-form-error" role="alert">
            <p>{formError}</p>
          </div>
        )}
        {formSuccess && <p className="success-text">{formSuccess}</p>}
        <div className="actions-row">
          <button
            type="button"
            className="btn primary"
            disabled={actionLoading === "create"}
            onClick={() => void handleCreateBan()}
          >
            {actionLoading === "create" ? "Блокировка…" : "Заблокировать"}
          </button>
        </div>
      </section>

      {loading && <p className="muted">Загрузка…</p>}
      {error && (
        <div className="card error">
          <p>{error}</p>
        </div>
      )}

      {!loading && !error && (
        <>
          <section className="card">
            <h2 className="section-title">IP-блокировки ({ipBlocks.length})</h2>
            {ipBlocks.length === 0 ? (
              <p className="muted">Активных IP-блокировок нет.</p>
            ) : (
              <div className="table-scroll">
                <table className="data-table admin-abuse-table">
                  <thead>
                    <tr>
                      <th>IP</th>
                      <th>Score</th>
                      <th>Источник</th>
                      <th>Причина</th>
                      <th>Создана</th>
                      <th>Осталось</th>
                      <th>Кем</th>
                      <th />
                    </tr>
                  </thead>
                  <tbody>
                    {ipBlocks.map((item) => (
                      <tr key={item.ip}>
                        <td>
                          <code>{item.ip}</code>
                        </td>
                        <td>{item.score}</td>
                        <td>{sourceLabel(item.source)}</td>
                        <td>{item.reason || "—"}</td>
                        <td>{formatDateTime(item.created_at)}</td>
                        <td>{formatDurationShort(item.ttl_seconds)}</td>
                        <td>{item.created_by || "—"}</td>
                        <td className="admin-abuse-actions">
                          <button
                            type="button"
                            className="btn btn-ghost"
                            disabled={actionLoading !== null}
                            onClick={() => setConfirmUnban({ kind: "ip", value: item.ip })}
                          >
                            Разблокировать
                          </button>
                          {item.score > 0 && (
                            <button
                              type="button"
                              className="btn btn-ghost"
                              disabled={actionLoading !== null}
                              onClick={() => void handleClearScore(item.ip)}
                            >
                              Сбросить score
                            </button>
                          )}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </section>

          <section className="card">
            <h2 className="section-title">Блокировки аккаунтов ({telegramBans.length})</h2>
            {telegramBans.length === 0 ? (
              <p className="muted">Заблокированных Telegram-аккаунтов нет.</p>
            ) : (
              <div className="table-scroll">
                <table className="data-table admin-abuse-table">
                  <thead>
                    <tr>
                      <th>Telegram</th>
                      <th>Последний IP</th>
                      <th>Источник</th>
                      <th>Причина</th>
                      <th>Создана</th>
                      <th>Осталось</th>
                      <th>Кем</th>
                      <th />
                    </tr>
                  </thead>
                  <tbody>
                    {telegramBans.map((item) => (
                      <tr key={item.telegram_id}>
                        <td>{telegramLabel(item)}</td>
                        <td>{item.last_ip ? <code>{item.last_ip}</code> : "—"}</td>
                        <td>{sourceLabel(item.source)}</td>
                        <td>{item.reason || "—"}</td>
                        <td>{formatDateTime(item.created_at)}</td>
                        <td>{formatDurationShort(item.ttl_seconds)}</td>
                        <td>{item.created_by || "—"}</td>
                        <td className="admin-abuse-actions">
                          <button
                            type="button"
                            className="btn btn-ghost"
                            disabled={actionLoading !== null}
                            onClick={() =>
                              setConfirmUnban({ kind: "telegram", value: item.telegram_id })
                            }
                          >
                            Разблокировать
                          </button>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </section>
        </>
      )}

      <ConfirmModal
        open={confirmUnban !== null}
        title="Снять блокировку?"
        confirmLabel="Разблокировать"
        confirmLoading={actionLoading?.startsWith("unban-") ?? false}
        onConfirm={() => void handleUnban()}
        onCancel={() => setConfirmUnban(null)}
      >
        {confirmUnban?.kind === "ip"
          ? `Разблокировать IP ${confirmUnban.value}?`
          : `Разблокировать Telegram ID ${confirmUnban?.value}?`}
      </ConfirmModal>
    </AppShell>
  );
}

export function AdminAbusePage() {
  return <RequireAdmin>{() => <AdminAbuseContent />}</RequireAdmin>;
}
