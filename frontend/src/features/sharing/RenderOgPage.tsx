// Служебные страницы для серверного рендера OG-картинок (Л19).
//
// Их открывает Playwright из celery-задачи og_render (внутри docker-сети,
// снаружи путь закрыт в host-nginx) и снимает скриншот 1200×630. Карточка —
// те же React-компоненты, что в шторке «Поделиться»: один движок на
// интерактив и серверный рендер.
//
// Маркер #og-ready появляется, когда данные загружены и шрифты применены, —
// по нему Playwright понимает, что можно снимать.

import { useEffect, useState } from "react";
import {
  getLocationPage,
  getPublicProfileDashboard,
  resolveProfileHandle,
  type LocationPage,
} from "../../lib/api";
import { ensureShareFontsLoaded, shareFontFromQuery } from "./fonts";
import { LOCATION_LOOKS, RUNNER_LOOKS } from "./looks";
import { ShareCardView } from "./ShareCardView";
import { locationCardSubject, profileCardSubject } from "./subjects";
import type { ShareCardData, ShareSubject } from "./types";
import { shareFormat } from "./types";

const WIDE = shareFormat("wide");

function RenderStage({ ready, children }: { ready: boolean; children: React.ReactNode }) {
  return (
    <div id={ready ? "og-ready" : undefined} style={{ position: "fixed", top: 0, left: 0 }}>
      {children}
    </div>
  );
}

/** Двойной rAF: дождаться, пока браузер отрисует карточку загруженным шрифтом. */
function usePaintedAfter(loaded: boolean): boolean {
  const [ready, setReady] = useState(false);
  useEffect(() => {
    if (!loaded) {
      return;
    }
    let raf = 0;
    raf = requestAnimationFrame(() => {
      raf = requestAnimationFrame(() => setReady(true));
    });
    return () => cancelAnimationFrame(raf);
  }, [loaded]);
  return ready;
}

export function RenderOgLocationPage({ slug }: { slug: string }) {
  const font = shareFontFromQuery();
  const [page, setPage] = useState<LocationPage | null>(null);
  const [failed, setFailed] = useState(false);
  const ready = usePaintedAfter(page !== null);

  useEffect(() => {
    let cancelled = false;
    Promise.all([getLocationPage(slug), ensureShareFontsLoaded(font)])
      .then(([data]) => {
        if (!cancelled) {
          setPage(data);
        }
      })
      .catch(() => {
        if (!cancelled) {
          setFailed(true);
        }
      });
    return () => {
      cancelled = true;
    };
  }, [slug, font]);

  if (failed) {
    // Явный маркер ошибки: Playwright дождётся таймаута и запишет локацию в failed.
    return <div id="og-failed" />;
  }
  if (!page) {
    return null;
  }
  const subject = locationCardSubject(page);
  return (
    <RenderStage ready={ready}>
      <ShareCardView
        data={subject.data}
        format={WIDE}
        look={LOCATION_LOOKS[0]}
        photo={null}
        font={font}
      />
    </RenderStage>
  );
}

/**
 * Карточка публичного профиля для превью ссылки в чате. Приватный профиль
 * бэкенд сюда не пускает (пререндер отдаёт дефолтную картинку), но если
 * запрос всё же дошёл и API ответил 403 — рисуем маркер ошибки, а не
 * пустую карточку с чужими цифрами.
 */
export function RenderOgUserPage({ handle }: { handle: string }) {
  const font = shareFontFromQuery();
  const [subject, setSubject] = useState<ShareSubject | null>(null);
  const [failed, setFailed] = useState(false);
  const ready = usePaintedAfter(subject !== null);

  useEffect(() => {
    let cancelled = false;
    Promise.all([resolveProfileHandle(handle), ensureShareFontsLoaded(font)])
      .then(([resolved]) => getPublicProfileDashboard(resolved.serial_id).then((data) => ({ resolved, data })))
      .then(({ resolved, data }) => {
        if (cancelled) {
          return;
        }
        const name =
          (data.user.display_name || "").trim() ||
          (resolved.display_name || "").trim() ||
          "Участник";
        setSubject(profileCardSubject(data.stats, name));
      })
      .catch(() => {
        if (!cancelled) {
          setFailed(true);
        }
      });
    return () => {
      cancelled = true;
    };
  }, [handle, font]);

  if (failed) {
    return <div id="og-failed" />;
  }
  if (!subject) {
    return null;
  }
  return (
    <RenderStage ready={ready}>
      <ShareCardView
        data={subject.data}
        format={WIDE}
        look={RUNNER_LOOKS[0]}
        photo={null}
        font={font}
      />
    </RenderStage>
  );
}

// Дефолтная брендовая карточка сайта: og:image для страниц без собственной
// картинки. Снимается один раз и коммитится в frontend/public/og/default.png.
// Бренд run5k.run не повторяем — его рисует фирменный футер карточки.
const DEFAULT_CARD: ShareCardData = {
  audience: "runner",
  title: "Парковые пробежки",
  subtitle: "5 вёрст · S95 · parkrun · RunPark",
  hero: { value: "5 км", caption: "каждую субботу в 9:00" },
  metrics: [],
  fact: "Рекорды, рейтинги и вся история стартов — в одном кабинете",
};

export function RenderOgDefaultPage() {
  const font = shareFontFromQuery();
  const [loaded, setLoaded] = useState(false);
  const ready = usePaintedAfter(loaded);
  useEffect(() => {
    void ensureShareFontsLoaded(font).then(() => setLoaded(true));
  }, [font]);
  if (!loaded) {
    return null;
  }
  return (
    <RenderStage ready={ready}>
      <ShareCardView
        data={DEFAULT_CARD}
        format={WIDE}
        look={RUNNER_LOOKS[0]}
        photo={null}
        font={font}
      />
    </RenderStage>
  );
}
