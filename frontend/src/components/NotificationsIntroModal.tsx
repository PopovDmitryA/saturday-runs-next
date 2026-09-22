import { useEffect, useState } from "react";
import { createPortal } from "react-dom";
import { dismissNotificationNudge, getNotificationNudge } from "../lib/api";
import { PORTAL_NOTIFICATIONS_SETTINGS_HREF } from "../lib/portalRoutes";

// Модалка «На сайте появились уведомления» — один раз за вход, пока
// уведомления выключены. «Не сейчас» прячет её до следующего входа
// (sessionStorage живёт до закрытия вкладки/браузера), «Больше не напоминать»
// запоминается на сервере навсегда.
const SNOOZE_KEY = "notify-intro-snoozed";

function snoozed(): boolean {
  try {
    return sessionStorage.getItem(SNOOZE_KEY) === "1";
  } catch {
    return false;
  }
}

function snooze(): void {
  try {
    sessionStorage.setItem(SNOOZE_KEY, "1");
  } catch {
    // приватный режим — покажем ещё раз, не страшно
  }
}

export function NotificationsIntroModal() {
  const [open, setOpen] = useState(false);

  useEffect(() => {
    if (snoozed()) {
      return;
    }
    let cancelled = false;
    void getNotificationNudge()
      .then((state) => {
        if (!cancelled && state.show) {
          setOpen(true);
        }
      })
      .catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    if (!open) {
      return;
    }
    const previous = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      document.body.style.overflow = previous;
    };
  }, [open]);

  if (!open) {
    return null;
  }

  const later = () => {
    snooze();
    setOpen(false);
  };
  const never = () => {
    snooze();
    setOpen(false);
    void dismissNotificationNudge().catch(() => undefined);
  };

  return createPortal(
    <div className="modal-overlay" onClick={later}>
      <div
        className="modal-panel notify-intro"
        role="dialog"
        aria-modal="true"
        aria-labelledby="notify-intro-title"
        onClick={(event) => event.stopPropagation()}
      >
        <div className="notify-intro-hero" aria-hidden="true">
          <span className="notify-intro-bell">🔔</span>
        </div>
        <h2 id="notify-intro-title" className="modal-title notify-intro-title">
          На сайте появились уведомления
        </h2>
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
          <a className="btn primary modal-btn" href={PORTAL_NOTIFICATIONS_SETTINGS_HREF} onClick={snooze}>
            Включить в настройках
          </a>
        </div>
        <button type="button" className="link-button notify-intro-never" onClick={never}>
          Больше не напоминать
        </button>
      </div>
    </div>,
    document.body,
  );
}
