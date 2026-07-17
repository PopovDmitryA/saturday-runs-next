import { recordSitePageview } from "./api";

/**
 * Трекинг просмотра страницы с длительностью.
 *
 * На каждую смену роута startPageView() шлёт событие pageview (view_id
 * генерируется на клиенте), затем копит время, пока вкладка видима, и
 * отправляет накопленную длительность беконом /api/stats/pageleave: при уходе
 * со страницы (cleanup), сворачивании вкладки и pagehide. Сервер хранит
 * максимум из пришедших значений, поэтому повторные беконы безопасны.
 */
export function startPageView(
  path: string,
  authenticated: boolean,
  visitorKey: string,
): () => void {
  const viewId = crypto.randomUUID();
  void recordSitePageview(path, authenticated, visitorKey, viewId);

  let accumulatedMs = 0;
  let visibleSince: number | null = document.visibilityState === "visible" ? Date.now() : null;
  let sentDurationSec = 0;

  const currentDurationSec = (): number => {
    const live = visibleSince === null ? 0 : Date.now() - visibleSince;
    return Math.round((accumulatedMs + live) / 1000);
  };

  const sendDuration = () => {
    const durationSec = currentDurationSec();
    // Закрытие вкладки поднимает и visibilitychange, и pagehide — без этой
    // проверки каждый просмотр слал бы два одинаковых бекона. Сервер и так
    // хранит максимум, но лишний запрос ещё и капает в antiDDoS-счётчик IP.
    if (durationSec <= 0 || durationSec <= sentDurationSec) {
      return;
    }
    sentDurationSec = durationSec;
    const body = JSON.stringify({ view_id: viewId, duration_sec: durationSec });
    try {
      // sendBeacon доставляется и при закрытии вкладки; same-origin, CORS не нужен.
      navigator.sendBeacon("/api/stats/pageleave", new Blob([body], { type: "application/json" }));
    } catch {
      // ignore analytics errors
    }
  };

  const onVisibilityChange = () => {
    if (document.visibilityState === "hidden") {
      if (visibleSince !== null) {
        accumulatedMs += Date.now() - visibleSince;
        visibleSince = null;
      }
      sendDuration();
    } else if (visibleSince === null) {
      visibleSince = Date.now();
    }
  };

  document.addEventListener("visibilitychange", onVisibilityChange);
  window.addEventListener("pagehide", sendDuration);

  return () => {
    document.removeEventListener("visibilitychange", onVisibilityChange);
    window.removeEventListener("pagehide", sendDuration);
    sendDuration();
  };
}
