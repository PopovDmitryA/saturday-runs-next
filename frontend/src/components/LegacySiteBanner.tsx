import { useCallback, useState } from "react";
import { LEGACY_SITE_HREF } from "../lib/siteBrand";

const DISMISS_STORAGE_KEY = "sr_legacy_site_banner_dismissed";

function readDismissed(): boolean {
  try {
    return localStorage.getItem(DISMISS_STORAGE_KEY) === "1";
  } catch {
    return false;
  }
}

export function LegacySiteBanner() {
  const [dismissed, setDismissed] = useState(readDismissed);

  const dismiss = useCallback(() => {
    try {
      localStorage.setItem(DISMISS_STORAGE_KEY, "1");
    } catch {
      // ignore private mode / blocked storage
    }
    setDismissed(true);
  }, []);

  if (dismissed) {
    return null;
  }

  return (
    <aside className="legacy-site-banner" role="note" aria-label="Уведомление о новой версии сайта">
      <div className="legacy-site-banner-inner">
        <div className="legacy-site-banner-body">
          <p>
            Новый личный кабинет: привяжите профили из 5 вёрст, С95 и parkrun — вся статистика и
            аналитика в одном месте.
          </p>
          <p>
            Мы переезжаем постепенно; рейтинги и другие блоки тоже появятся здесь. Пока прежний сайт с
            Grafana —{" "}
            <a href={LEGACY_SITE_HREF} className="legacy-site-banner-link">
              по ссылке
            </a>
            .
          </p>
        </div>
        <button
          type="button"
          className="legacy-site-banner-dismiss"
          aria-label="Закрыть уведомление"
          title="Закрыть"
          onClick={dismiss}
        >
          ×
        </button>
      </div>
    </aside>
  );
}
