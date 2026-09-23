import type { NotificationChannelState } from "../../lib/api";

// «🔔 Уведомления» в одну строку с способом входа: тумблер и короткий статус.
// Красная подсказка (бот заблокирован, сообщество без разрешения) — отдельной
// строкой на всю ширину карточки, см. NotificationChannelProblem.
export function NotificationChannelToggle({
  state,
  busy,
  onToggle,
}: {
  state: NotificationChannelState;
  busy: boolean;
  onToggle: (enabled: boolean) => void;
}) {
  if (!state.available) {
    return null;
  }
  const status = state.enabled
    ? state.deliverable === false
      ? "не доходят"
      : "включены"
    : "выключены";
  const tone = state.enabled ? (state.deliverable === false ? "bad" : "on") : "off";
  return (
    <label className="notify-inline" aria-label={`Уведомления: ${state.title}`}>
      <span className="notify-inline-text">
        <span className="notify-inline-title">🔔 Уведомления</span>
        <span className={`notify-inline-status ${tone}`}>{status}</span>
      </span>
      <span className="toggle-switch">
        <input
          type="checkbox"
          className="toggle-switch-input"
          checked={state.enabled}
          disabled={busy}
          onChange={(event) => onToggle(event.target.checked)}
        />
        <span className="toggle-switch-track">
          <span className="toggle-switch-thumb" />
        </span>
      </span>
    </label>
  );
}

export function NotificationChannelProblem({
  state,
  busy,
  onRecheck,
}: {
  state: NotificationChannelState;
  busy: boolean;
  onRecheck: () => void;
}) {
  // Пока уведомления на этом способе входа выключены, человеку всё равно, может
  // ли бот писать — подсказка появляется только после включения.
  if (!state.available || !state.linked || !state.enabled) {
    return null;
  }
  if (state.problem) {
    return (
      <p className="notify-channel-error notify-row-problem">
        {state.problem}{" "}
        {state.bot_url && (
          <a href={state.bot_url} target="_blank" rel="noreferrer">
            Открыть бота
          </a>
        )}
        {state.allow_url && (
          <a href={state.allow_url} target="_blank" rel="noreferrer">
            Открыть диалог с сообществом
          </a>
        )}{" "}
        <button type="button" className="link-button" disabled={busy} onClick={onRecheck}>
          Проверить ещё раз
        </button>
      </p>
    );
  }
  if (state.last_error) {
    return <p className="notify-channel-error notify-row-problem">Последняя доставка не удалась: {state.last_error}</p>;
  }
  return null;
}
