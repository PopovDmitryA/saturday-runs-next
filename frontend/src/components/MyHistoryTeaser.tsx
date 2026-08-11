import { useEffect, useState } from "react";
import { milestoneTitle, milestoneVisual } from "../features/history/HistoryPage";
import { storeMilestoneShare } from "../features/history/milestoneShare";
import type { MyHistory, MyHistoryMilestone } from "../lib/api";
import { formatDateLong, parseIsoDate, pluralFormRu } from "../lib/format";

const MILESTONE_FORMS = ["веха", "вехи", "вех"] as const;

// Тизер показывает только «свежую» веху — если с последней прошло больше
// месяца, карточка на дашборде не нужна (это уже не свежее достижение, а
// просто история); полный таймлайн всегда доступен на /history.
const TEASER_FRESHNESS_DAYS = 30;

function daysSince(isoDate: string): number | null {
  const date = parseIsoDate(isoDate);
  if (!date) {
    return null;
  }
  const diffMs = Date.now() - date.getTime();
  return Math.floor(diffMs / (1000 * 60 * 60 * 24));
}

function ShareIcon() {
  return (
    <svg viewBox="0 0 24 24" width="16" height="16" aria-hidden="true" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <circle cx="18" cy="5" r="3" />
      <circle cx="6" cy="12" r="3" />
      <circle cx="18" cy="19" r="3" />
      <line x1="8.6" y1="13.5" x2="15.4" y2="17.5" />
      <line x1="15.4" y1="6.5" x2="8.6" y2="10.5" />
    </svg>
  );
}

type MyHistoryTeaserProps = {
  load: () => Promise<MyHistory>;
  /** Ссылка на полный таймлайн: /history или /demo/history. */
  href: string;
  /** Адрес мастера «Поделиться»; без него кнопки не рисуем (превью-режим). */
  shareBase?: string;
};

// Тизер «Моей истории» на дашборде: вехи последнего дня + ссылка на таймлайн.
export function MyHistoryTeaser({ load, href, shareBase }: MyHistoryTeaserProps) {
  const [data, setData] = useState<MyHistory | null>(null);

  useEffect(() => {
    let cancelled = false;
    load()
      .then((result) => {
        if (!cancelled) {
          setData(result);
        }
      })
      .catch(() => {
        // тихо — тизер просто не покажется
      });
    return () => {
      cancelled = true;
    };
  }, [load]);

  const milestones = data?.milestones ?? [];
  if (milestones.length === 0) {
    return null;
  }
  const last = milestones[0];
  const age = daysSince(last.event_date);
  // Свежих вех больше месяца не было — тизер прячем с дашборда, вся история
  // всё равно остаётся доступной на /history.
  if (age != null && age > TEASER_FRESHNESS_DAYS) {
    return null;
  }
  // В одну субботу вех бывает несколько (клуб + рекорд локации + новый регион),
  // а показывалась только самая свежая (репорт Дмитрия 11.08.2026). Берём все
  // вехи этого дня — список отсортирован по дате, поэтому просто отрезаем
  // хвост с другой датой.
  const sameDay: MyHistoryMilestone[] = [];
  for (const milestone of milestones) {
    if (milestone.event_date !== last.event_date) {
      break;
    }
    sameDay.push(milestone);
  }
  const visual = milestoneVisual(last);
  const count = milestones.length;

  return (
    <section className="card history-teaser" aria-label="Моя история — свежие вехи">
      <span className={`history-teaser-icon ${visual.className}`} aria-hidden="true">
        {visual.icon}
      </span>
      <span className="history-teaser-body">
        <span className="history-teaser-head">
          <a className="history-teaser-title" href={href}>
            Моя история
          </a>
          <span className="history-teaser-date muted">{formatDateLong(last.event_date)}</span>
        </span>
        <ul className="history-teaser-list">
          {sameDay.map((milestone, index) => (
            <li key={`${milestone.kind}-${milestone.number ?? index}`} className="history-teaser-item">
              <span className="history-teaser-last">{milestoneTitle(milestone)}</span>
              {shareBase && (
                <a
                  className="history-share"
                  href={`${shareBase}?story=milestone`}
                  title="Сделать картинку-сториз с этой вехой"
                  aria-label={`Поделиться: ${milestoneTitle(milestone)}`}
                  onClick={() => storeMilestoneShare(milestone)}
                >
                  <ShareIcon />
                  <span className="history-share-label">Поделиться</span>
                </a>
              )}
            </li>
          ))}
        </ul>
      </span>
      <a className="history-teaser-more" href={href}>
        {count} {pluralFormRu(count, MILESTONE_FORMS)} →
      </a>
    </section>
  );
}
