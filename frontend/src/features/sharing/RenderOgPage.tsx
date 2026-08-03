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
import { getLocationPage, type LocationPage } from "../../lib/api";
import { ensureShareFontsLoaded } from "./fonts";
import { LOCATION_LOOKS, RUNNER_LOOKS } from "./looks";
import { ShareCardView } from "./ShareCardView";
import { locationCardSubject } from "./subjects";
import type { ShareCardData } from "./types";
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
  const [page, setPage] = useState<LocationPage | null>(null);
  const [failed, setFailed] = useState(false);
  const ready = usePaintedAfter(page !== null);

  useEffect(() => {
    let cancelled = false;
    Promise.all([getLocationPage(slug), ensureShareFontsLoaded("golos")])
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
  }, [slug]);

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
        font="golos"
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
  const [loaded, setLoaded] = useState(false);
  const ready = usePaintedAfter(loaded);
  useEffect(() => {
    void ensureShareFontsLoaded("golos").then(() => setLoaded(true));
  }, []);
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
        font="golos"
      />
    </RenderStage>
  );
}
