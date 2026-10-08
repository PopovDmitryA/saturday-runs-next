import { useEffect, useState } from "react";
import { createPortal } from "react-dom";
import { lockBodyScroll } from "../lib/bodyScrollLock";
import {
  dismissNotificationNudge,
  getNotificationNudge,
  snoozeNotificationNudge,
  type NotificationNudgeState,
} from "../lib/api";
import { PORTAL_NOTIFICATIONS_SETTINGS_HREF } from "../lib/portalRoutes";
import { hasPendingSearchClaim } from "../features/portal/nav/searchClaim";

// Две модалки под одной механикой показа:
//  * «На сайте появились уведомления» — пока они выключены. «Не сейчас»
//    откладывает на два месяца (на сервере, на всех устройствах),
//    «Больше не напоминать» гасит навсегда. До 08.10.2026 «Не сейчас» жило
//    только в sessionStorage, и окно всплывало при каждом новом открытии
//    сайта — карточка бэклога «Навязчивый пуш включить уведомления».
//  * «Мы не можем вам написать» — уведомления включены, но ни один канал не
//    доставляет: бот заблокирован или сообщество без разрешения. Это поломка,
//    а не реклама, поэтому навсегда её не выключить — только починить или
//    выключить уведомления в настройках.
// Вкладка дополнительно помнит отказ в sessionStorage: тревогу о доставке
// сервер не откладывает, и без этого она всплывала бы на каждой странице.
// Пока ждёт ответа «Это вы?» из поиска (SearchClaimRunner) — молчим и не
// снузим: два окна разом после входа не нужны, призыв покажется на следующей
// странице кабинета.
const SNOOZE_KEY = "notify-prompt-snoozed";

function snoozed(kind: string): boolean {
  try {
    return sessionStorage.getItem(SNOOZE_KEY) === kind;
  } catch {
    return false;
  }
}

function snooze(kind: string): void {
  try {
    sessionStorage.setItem(SNOOZE_KEY, kind);
  } catch {
    // приватный режим — покажем ещё раз, не страшно
  }
}

export function NotificationsPromptModal() {
  const [state, setState] = useState<NotificationNudgeState | null>(null);

  useEffect(() => {
    if (hasPendingSearchClaim()) {
      return;
    }
    let cancelled = false;
    void getNotificationNudge()
      .then((next) => {
        if (!cancelled && next.show && next.kind && !snoozed(next.kind) && !hasPendingSearchClaim()) {
          setState(next);
        }
      })
      .catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    if (!state) {
      return;
    }
    return lockBodyScroll();
  }, [state]);

  if (!state || !state.kind) {
    return null;
  }

  const kind = state.kind;
  const close = () => {
    snooze(kind);
    setState(null);
  };
  // Отказ от призыва — на два месяца; тревогу о поломке откладываем только
  // до закрытия вкладки.
  const later = () => {
    close();
    if (kind === "enable") {
      void snoozeNotificationNudge().catch(() => undefined);
    }
  };
  const never = () => {
    close();
    void dismissNotificationNudge().catch(() => undefined);
  };

  const broken = state.broken;
  const action = broken.find((item) => item.action_url);

  return createPortal(
    <div className="modal-overlay" onClick={later}>
      <div
        className={`modal-panel notify-intro ${kind === "fix_delivery" ? "notify-intro-alert" : ""}`}
        role="dialog"
        aria-modal="true"
        aria-labelledby="notify-prompt-title"
        onClick={(event) => event.stopPropagation()}
      >
        <div className="notify-intro-hero" aria-hidden="true">
          <span className="notify-intro-bell">{kind === "fix_delivery" ? "⚠️" : "🔔"}</span>
        </div>
        <h2 id="notify-prompt-title" className="modal-title notify-intro-title">
          {kind === "fix_delivery" ? "Мы не можем вам написать" : "На сайте появились уведомления"}
        </h2>

        {kind === "fix_delivery" ? (
          <>
            <p className="notify-intro-lead">
              Уведомления включены, но {broken.length === 1 ? broken[0].title : "ни один канал"} не
              пропускает наши сообщения — они до вас не доходят.
            </p>
            <ul className="notify-intro-list">
              {broken.map((item) => (
                <li key={item.channel}>
                  <span className="notify-intro-icon">{item.channel === "vk" ? "💬" : "🤖"}</span>
                  <span>{item.problem ?? `${item.title}: доставка недоступна.`}</span>
                </li>
              ))}
            </ul>
            <p className="notify-intro-note">
              Разрешение занимает пару секунд: откроется {action?.channel === "vk" ? "диалог с сообществом" : "бот"},
              нужно нажать кнопку подтверждения. После этого уведомления заработают сами.
            </p>
            <div className="modal-actions notify-intro-actions">
              <button type="button" className="btn secondary modal-btn" onClick={later}>
                Не сейчас
              </button>
              {action?.action_url ? (
                <a
                  className="btn primary modal-btn"
                  href={action.action_url}
                  target="_blank"
                  rel="noreferrer"
                  onClick={close}
                >
                  Разрешить
                </a>
              ) : (
                <a className="btn primary modal-btn" href={PORTAL_NOTIFICATIONS_SETTINGS_HREF} onClick={close}>
                  Открыть настройки
                </a>
              )}
            </div>
            <a className="link-button notify-intro-never" href={PORTAL_NOTIFICATIONS_SETTINGS_HREF} onClick={close}>
              Настройки уведомлений
            </a>
          </>
        ) : (
          <>
            <p className="notify-intro-lead">
              Не нужно заходить и проверять — сайт сам напишет, когда есть повод.
            </p>
            <ul className="notify-intro-list">
              <li>
                <span className="notify-intro-icon">🏃</span>
                <span>
                  <b>Пробежка попала на сайт.</b> Время и место, новые уровни челленджей, вехи истории и
                  готовый постер для сториз — в одном сообщении.
                </span>
              </li>
              <li>
                <span className="notify-intro-icon">🦺</span>
                <span>
                  <b>Волонтёрство попало на сайт.</b> Локация, номер старта и все ваши роли — а если в тот
                  же день и бежали, одним сообщением с пробежкой.
                </span>
              </li>
              <li>
                <span className="notify-intro-icon">🚫</span>
                <span>
                  <b>Отмены стартов по стране.</b> Узнаете заранее — и про свою локацию, и про ту,
                  куда только собираетесь ехать.
                </span>
              </li>
              <li>
                <span className="notify-intro-icon">📈</span>
                <span>
                  <b>Движение в рейтингах.</b> Раз в неделю, в воскресенье, когда все протоколы субботы
                  уже на месте.
                </span>
              </li>
              <li>
                <span className="notify-intro-icon">💬</span>
                <span>
                  <b>Ваши карточки в бэклоге.</b> Ответ на идею, новые комментарии и смена статуса — от
                  «на рассмотрении» до «реализовано».
                </span>
              </li>
            </ul>
            <p className="notify-intro-note">
              Приходят туда, где вы вошли на сайт: Telegram, VK или почта. Отписка — в один клик из любого
              сообщения.
            </p>
            <div className="modal-actions notify-intro-actions">
              <button type="button" className="btn secondary modal-btn" onClick={later}>
                Не сейчас
              </button>
              <a className="btn primary modal-btn" href={PORTAL_NOTIFICATIONS_SETTINGS_HREF} onClick={close}>
                Включить в настройках
              </a>
            </div>
            <button type="button" className="link-button notify-intro-never" onClick={never}>
              Больше не напоминать
            </button>
          </>
        )}
      </div>
    </div>,
    document.body,
  );
}
