import { useEffect, useId, useRef, useState } from "react";
import {
  ApiError,
  getNotificationSettings,
  updateNotificationSettings,
  type NotificationKindState,
  type NotificationSettingsState,
} from "../../lib/api";
import { PORTAL_NOTIFICATIONS_SETTINGS_HREF } from "../../lib/portalRoutes";
import { PLAN_NOTIFICATIONS_ANCHOR } from "./plannerLinks";
import "./planner.css";

/**
 * «Уведомления о планах» внизу «Моих планов» — свёрнуты, по якорю
 * #plan-notifications раскрываются сами (решение Дмитрия 27.10.2026). Кто
 * видит план, здесь больше не настраивается: «Публично | Приватно» — в окне
 * самого плана.
 *
 * Виды отдельные: можно планировать без напоминаний накануне и всё равно
 * узнать об отмене старта или о паузе постоянного плана. Переключатели — те
 * же виды, что в «О чём присылать» в настройках, через тот же API; сюда ведут
 * «Выключить» в самом напоминании и ссылка из настроек сайта.
 */

// Коды видов — как в app/notification_kinds.py.
const PLAN_NOTIFICATION_KINDS: { code: string; label: string }[] = [
  { code: "plan_reminders", label: "Напоминать накануне" },
  { code: "plan_cancellations", label: "Сообщать об отмене старта" },
  // Постоянный план встал на паузу — одно сообщение «Ещё ходите?» за паузу.
  { code: "plan_series", label: "Постоянный план на паузе" },
];

// Сколько держится вспышка раздела после перехода по якорю — как у
// анимации settings-anchor-flash в index.css.
const ANCHOR_FLASH_MS = 2200;

// Сколько после перехода по якорю догоняем раздел, пока над ним догружаются
// постоянный план и календарь.
const ANCHOR_FOLLOW_MS = 2500;
const ANCHOR_FOLLOW_STEP_MS = 150;

function scrollBehavior(): ScrollBehavior {
  return window.matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth";
}

function atAnchor(): boolean {
  return typeof window !== "undefined" && window.location.hash === `#${PLAN_NOTIFICATIONS_ANCHOR}`;
}

/**
 * Пришли по якорю — докрутить и моргнуть, когда раздел раскрыт и загружен:
 * раньше браузер промахивается. Раздел внизу страницы, а календарь и
 * постоянный план над ним приезжают своими запросами позже — блок уезжал
 * вниз, и по ссылке из напоминания человек оказывался в календаре. Поэтому
 * ещё пару секунд догоняем раздел, пока человек сам не тронул страницу.
 * Повторный переход по той же ссылке ловим по hashchange.
 */
function useAnchorReveal(ready: boolean) {
  const ref = useRef<HTMLElement | null>(null);
  const [flash, setFlash] = useState(false);
  useEffect(() => {
    if (!ready) {
      return;
    }
    let timer = 0;
    let follow = 0;
    const stopFollow = () => {
      window.clearInterval(follow);
      follow = 0;
      for (const type of ["wheel", "touchstart", "keydown", "pointerdown"] as const) {
        window.removeEventListener(type, stopFollow);
      }
    };
    const reveal = () => {
      if (!atAnchor()) {
        return;
      }
      ref.current?.scrollIntoView({ behavior: scrollBehavior(), block: "start" });
      setFlash(false);
      window.clearTimeout(timer);
      // Снять и снова поставить класс — иначе повторный переход не моргнёт.
      requestAnimationFrame(() => setFlash(true));
      timer = window.setTimeout(() => setFlash(false), ANCHOR_FLASH_MS);
      stopFollow();
      const top = () => (ref.current ? ref.current.getBoundingClientRect().top + window.scrollY : 0);
      let last = top();
      const until = performance.now() + ANCHOR_FOLLOW_MS;
      follow = window.setInterval(() => {
        const now = top();
        if (Math.abs(now - last) > 2) {
          ref.current?.scrollIntoView({ block: "start" });
        }
        last = now;
        if (performance.now() > until) {
          stopFollow();
        }
      }, ANCHOR_FOLLOW_STEP_MS);
      for (const type of ["wheel", "touchstart", "keydown", "pointerdown"] as const) {
        window.addEventListener(type, stopFollow, { passive: true });
      }
    };
    reveal();
    window.addEventListener("hashchange", reveal);
    return () => {
      window.removeEventListener("hashchange", reveal);
      window.clearTimeout(timer);
      stopFollow();
    };
  }, [ready]);
  return { ref, flash };
}

/**
 * «Напоминать накануне», «Сообщать об отмене старта», «Постоянный план на
 * паузе». Уведомления выключены (ни одного канала) — переключатели стоят, но не
 * нажимаются, как в «О чём присылать»: включать вид, который всё равно никуда
 * не придёт, незачем.
 */
function PlanNotificationRows({
  state,
  onChange,
}: {
  state: NotificationSettingsState;
  onChange: (next: NotificationSettingsState) => void;
}) {
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const toggle = async (code: string, enabled: boolean) => {
    setBusy(true);
    setError(null);
    try {
      onChange(await updateNotificationSettings({ kinds: { [code]: enabled } }));
    } catch (err) {
      setError(err instanceof ApiError && err.message ? err.message : "Не удалось сохранить");
    } finally {
      setBusy(false);
    }
  };

  const kinds = new Map<string, NotificationKindState>(state.kinds.map((kind) => [kind.code, kind]));
  const rows = PLAN_NOTIFICATION_KINDS.filter((item) => kinds.get(item.code)?.available);

  return (
    <>
      {!state.enabled && (
        <p className="muted plan-notify-off">
          Уведомления выключены: каналов нет — напоминать и сообщать об отмене некуда.{" "}
          <a href={PORTAL_NOTIFICATIONS_SETTINGS_HREF}>Включить уведомления</a>
        </p>
      )}
      {rows.length > 0 && (
        <ul className="settings-platform-list plan-notify-list">
          {rows.map((item) => {
            const kind = kinds.get(item.code) as NotificationKindState;
            return (
              <li className="settings-platform-row plan-notify-row" key={item.code}>
                <div className="settings-platform-info">
                  <span className="settings-platform-name">{item.label}</span>
                  <span className="muted notify-channel-hint">{kind.description}</span>
                </div>
                <label className="toggle-switch" aria-label={item.label}>
                  <input
                    type="checkbox"
                    className="toggle-switch-input"
                    checked={kind.enabled}
                    disabled={busy || !state.enabled}
                    onChange={(event) => void toggle(item.code, event.target.checked)}
                  />
                  <span className="toggle-switch-track">
                    <span className="toggle-switch-thumb" />
                  </span>
                </label>
              </li>
            );
          })}
        </ul>
      )}
      {error && <p className="error-text">{error}</p>}
    </>
  );
}

export function PlanNotificationsSection() {
  const [open, setOpen] = useState(atAnchor);
  const [state, setState] = useState<NotificationSettingsState | null>(null);
  const [error, setError] = useState<string | null>(null);
  const panelId = useId();

  // Ссылка на раздел с этой же страницы — раскрыть, даже если свернули.
  useEffect(() => {
    const onHash = () => {
      if (atAnchor()) {
        setOpen(true);
      }
    };
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, []);

  // Настройки уведомлений грузим, только когда раздел раскрыли.
  useEffect(() => {
    if (!open || state !== null) {
      return;
    }
    let cancelled = false;
    setError(null);
    getNotificationSettings()
      .then((next) => {
        if (!cancelled) {
          setState(next);
        }
      })
      .catch((err) => {
        if (!cancelled) {
          setError(err instanceof Error && err.message ? err.message : "Не удалось загрузить уведомления");
        }
      });
    return () => {
      cancelled = true;
    };
  }, [open, state]);

  const { ref, flash } = useAnchorReveal(open && (state !== null || error !== null));

  return (
    <section
      ref={(node) => {
        ref.current = node;
      }}
      id={PLAN_NOTIFICATIONS_ANCHOR}
      className={`card plan-notify-card${flash ? " settings-anchor-flash" : ""}`}
    >
      <h2 className="section-title plan-notify-head">
        <button
          type="button"
          className="plan-notify-toggle"
          aria-expanded={open}
          aria-controls={panelId}
          onClick={() => setOpen((value) => !value)}
        >
          <span>Уведомления о планах</span>
          <span className="plan-notify-chevron" aria-hidden="true">
            {open ? "▾" : "▸"}
          </span>
        </button>
      </h2>
      {open && (
        <div className="plan-notify-box" id={panelId}>
          {!state && !error && <p className="muted">Загрузка…</p>}
          {error && <p className="error-text">{error}</p>}
          {state && <PlanNotificationRows state={state} onChange={setState} />}
        </div>
      )}
    </section>
  );
}
