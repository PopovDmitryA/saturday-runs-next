// Страница /share — «галерея моментов»: лента готовых сюжетов человека.
// Каждый сюжет показан живым мини-превью (тот же ShareCardView, ужатый
// transform-ом) и открывает шторку. Мастера больше нет: выбор данных — это
// выбор сюжета, а не чекбоксы.

import { useEffect, useMemo, useState } from "react";
import { AppShell } from "../../components/AppShell";
import {
  getDashboard,
  getMyHistory,
  listRuns,
  type DashboardResponse,
  type MyHistoryMilestone,
  type RunItem,
} from "../../lib/api";
import { useOptionalUser } from "../../lib/useOptionalUser";
import { trackShareMomentShown } from "./analytics";
import { ensureShareFontsLoaded, shareFontFromQuery } from "./fonts";
import { looksFor } from "./looks";
import { ShareCardView } from "./ShareCardView";
import { useOptionalShareSheet } from "./ShareSheetContext";
import { milestoneSubject, runSubject, summarySubject } from "./subjects";
import type { ShareSubject } from "./types";
import { shareFormat } from "./types";

const PREVIEW_WIDTH = 180;

type GalleryItem = {
  id: string;
  title: string;
  subtitle: string;
  subject: ShareSubject;
};

function MomentPreview({ subject }: { subject: ShareSubject }) {
  const font = shareFontFromQuery();
  const format = shareFormat(subject.defaultFormat);
  const look = looksFor(subject.data.audience)[0];
  const scale = PREVIEW_WIDTH / format.width;
  return (
    <div
      className="s2-gallery-preview"
      style={{ width: format.width * scale, height: format.height * scale }}
    >
      <div className="s2-preview-scale" style={{ transform: `scale(${scale})` }}>
        <ShareCardView data={subject.data} format={format} look={look} photo={null} font={font} />
      </div>
    </div>
  );
}

export function SharingContent({ bare = false }: { bare?: boolean } = {}) {
  const sheet = useOptionalShareSheet();
  const user = useOptionalUser();
  const [dashboard, setDashboard] = useState<DashboardResponse | null>(null);
  const [runs, setRuns] = useState<RunItem[] | null>(null);
  const [milestones, setMilestones] = useState<MyHistoryMilestone[] | null>(null);
  const [fontsReady, setFontsReady] = useState(false);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    let cancelled = false;
    void ensureShareFontsLoaded(shareFontFromQuery()).then(() => {
      if (!cancelled) {
        setFontsReady(true);
      }
    });
    Promise.all([getDashboard(), listRuns(false, 200), getMyHistory()])
      .then(([dashboardResponse, runsResponse, historyResponse]) => {
        if (cancelled) {
          return;
        }
        setDashboard(dashboardResponse);
        setRuns(runsResponse);
        setMilestones(historyResponse.milestones);
      })
      .catch(() => {
        if (!cancelled) {
          setFailed(true);
        }
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const items: GalleryItem[] = useMemo(() => {
    if (!dashboard || !runs) {
      return [];
    }
    const result: GalleryItem[] = [];
    const zeroRuns = runs.filter((run) => !run.is_crosslinked && !run.is_test_event);
    const lastRun = zeroRuns.reduce<RunItem | null>(
      (latest, run) => (latest === null || run.event_date > latest.event_date ? run : latest),
      null,
    );
    if (lastRun) {
      result.push({
        id: `run-${lastRun.event_date}`,
        title: "Последняя пробежка",
        subtitle: `${lastRun.location_name} · ${lastRun.event_date}`,
        subject: runSubject(lastRun, user ?? null),
      });
    }
    if (zeroRuns.length > 0) {
      result.push({
        id: "summary-year",
        title: "Итоги сезона",
        subtitle: "Пробежки, серия суббот, локации",
        subject: summarySubject(dashboard.stats, user ?? null, "year", runs),
      });
      result.push({
        id: "summary-all",
        title: "Вся история",
        subtitle: "Сквозные цифры за всё время",
        subject: summarySubject(dashboard.stats, user ?? null, "all", runs),
      });
    }
    for (const milestone of (milestones ?? []).slice(0, 4)) {
      result.push({
        id: `milestone-${milestone.kind}-${milestone.event_date}`,
        title: "Веха",
        subtitle: milestone.location_name ?? milestone.event_date,
        subject: milestoneSubject(milestone, user ?? null),
      });
    }
    return result;
  }, [dashboard, runs, milestones, user]);

  useEffect(() => {
    if (items.length > 0) {
      trackShareMomentShown(items[0].subject.kind, "gallery");
    }
  }, [items]);

  const pageBody = (
    <>
      <p className="muted s2-gallery-lead">
        Готовые сюжеты из вашей статистики: выберите момент, поменяйте оформление свайпом —
        и картинка уходит в чат одной кнопкой. Кнопки «Поделиться» есть и в самих разделах —
        на вехах, пробежках, рейтингах и страницах локаций.
      </p>

      {failed && (
        <div className="card">
          <p className="muted">
            Не удалось загрузить статистику. Если профиль ещё не привязан — начните с{" "}
            <a href="/dashboard#profiles">привязки профиля</a>.
          </p>
        </div>
      )}

      {!failed && items.length === 0 && (
        <p className="muted">Загрузка…</p>
      )}

      <div className="s2-gallery-grid">
        {fontsReady &&
          items.map((item) => (
            <button
              key={item.id}
              type="button"
              className="s2-gallery-item"
              onClick={() => sheet?.open({ subject: item.subject, entry: "gallery" })}
            >
              <MomentPreview subject={item.subject} />
              <span className="s2-gallery-item-title">{item.title}</span>
              <span className="s2-gallery-item-sub">{item.subtitle}</span>
            </button>
          ))}
      </div>
    </>
  );

  if (bare) {
    return <div className="portal-cab-stack">{pageBody}</div>;
  }
  return (
    <AppShell title="Поделиться" activePath="/share">
      {pageBody}
    </AppShell>
  );
}
