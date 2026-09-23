import { useState } from "react";
import { ConfirmModal } from "../../components/ConfirmModal";
import {
  ApiError,
  updateNotificationSettings,
  type NotificationChannelCode,
  type NotificationSettingsState,
} from "../../lib/api";
import { platformCodeLabel } from "../../lib/format";

// Отмены стартов публикуют только эти две системы: у parkrun и RunPark
// недельных отмен в источниках нет (см. app/services/location_activity_status).
const CANCELLATION_PLATFORMS = ["five_verst", "s95"] as const;

// «О чём присылать»: переключатели видов и выбор основного канала. Живёт
// модалкой, чтобы не раздувать «Способы входа» — там только тумблеры каналов.
export function NotificationPrefsModal({
  open,
  state,
  onClose,
  onChange,
}: {
  open: boolean;
  state: NotificationSettingsState;
  onClose: () => void;
  onChange: (next: NotificationSettingsState) => void;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const enabledChannels = state.channels.filter((c) => c.enabled);

  const apply = async (body: Parameters<typeof updateNotificationSettings>[0]) => {
    setBusy(true);
    setError(null);
    try {
      onChange(await updateNotificationSettings(body));
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Не удалось сохранить");
    } finally {
      setBusy(false);
    }
  };

  return (
    <ConfirmModal
      open={open}
      title="О чём присылать"
      confirmLabel="Готово"
      cancelLabel="Закрыть"
      onConfirm={onClose}
      onCancel={onClose}
    >
      <ul className="settings-platform-list notify-kind-list">
        {state.kinds.map((kind) => (
          <li className={`settings-platform-row notify-kind-row ${kind.available ? "" : "notify-kind-soon"}`} key={kind.code}>
            <div className="settings-platform-info">
              <span className="settings-platform-name">
                {kind.title}
                {!kind.available && <span className="notify-soon-badge">скоро</span>}
              </span>
              <span className="muted notify-channel-hint">{kind.description}</span>
            </div>
            <label className="toggle-switch" aria-label={kind.title}>
              <input
                type="checkbox"
                className="toggle-switch-input"
                checked={kind.enabled}
                disabled={busy || !kind.available || !state.enabled}
                onChange={(event) => void apply({ kinds: { [kind.code]: event.target.checked } })}
              />
              <span className="toggle-switch-track">
                <span className="toggle-switch-thumb" />
              </span>
            </label>
            {kind.code === "cancellations" && kind.enabled && state.enabled && (
              // Пустой список на бэкенде означает «все системы», поэтому снятая
              // последняя галочка равна выбору обеих — так и подписано.
              <div className="notify-kind-extra">
                <span className="muted notify-channel-hint">Про какие системы сообщать:</span>
                <div className="notify-platform-options">
                  {CANCELLATION_PLATFORMS.map((code) => {
                    const chosen =
                      state.cancellation_platforms.length === 0 || state.cancellation_platforms.includes(code);
                    return (
                      <label className="notify-platform-option" key={code}>
                        <input
                          type="checkbox"
                          checked={chosen}
                          disabled={busy}
                          onChange={(event) => {
                            const current =
                              state.cancellation_platforms.length === 0
                                ? [...CANCELLATION_PLATFORMS]
                                : state.cancellation_platforms;
                            const next = event.target.checked
                              ? Array.from(new Set([...current, code]))
                              : current.filter((item) => item !== code);
                            void apply({ cancellation_platforms: next });
                          }}
                        />
                        <span>{platformCodeLabel(code)}</span>
                      </label>
                    );
                  })}
                </div>
              </div>
            )}
          </li>
        ))}
      </ul>

      {enabledChannels.length > 1 && (
        <div className="notify-primary-block">
          <p className="settings-platform-name">Куда писать в первую очередь</p>
          <p className="muted notify-channel-hint">
            Не доставилось в основной канал — сообщение уйдёт в следующий.
          </p>
          <div className="notify-primary-options">
            {enabledChannels.map((channel) => (
              <label className="notify-primary-option" key={channel.channel}>
                <input
                  type="radio"
                  name="primary-channel"
                  checked={(state.primary_channel ?? enabledChannels[0].channel) === channel.channel}
                  disabled={busy}
                  onChange={() => void apply({ primary_channel: channel.channel as NotificationChannelCode })}
                />
                <span>{channel.title}</span>
              </label>
            ))}
          </div>
        </div>
      )}
      {!state.enabled && (
        <p className="muted settings-platform-hint">
          Включите уведомления хотя бы на одном способе входа — тогда переключатели заработают.
        </p>
      )}
      {error && <p className="error-text">{error}</p>}
    </ConfirmModal>
  );
}
