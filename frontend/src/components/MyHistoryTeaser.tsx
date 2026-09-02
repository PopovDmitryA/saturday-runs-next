import { useEffect, useState } from "react";
import { ShareIcon } from "./ShareIcon";
import { milestoneTitle, milestoneVisual } from "../features/history/HistoryPage";
import { useOptionalShareSheet } from "../features/sharing/ShareSheetContext";
import { milestoneSubject } from "../features/sharing/subjects";
import { useOptionalUser } from "../lib/useOptionalUser";
import type { MyHistory, MyHistoryMilestone } from "../lib/api";
import { formatDateLong, formatInt, parseIsoDate, pluralFormRu } from "../lib/format";

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


type MyHistoryTeaserProps = {
  load: () => Promise<MyHistory>;
  /** Ссылка на полный таймлайн («Моя история»). */
  href: string;
};

// Тизер «Моей истории» на дашборде: вехи последнего дня + ссылка на таймлайн.
export function MyHistoryTeaser({ load, href }: MyHistoryTeaserProps) {
  const [data, setData] = useState<MyHistory | null>(null);
  // Кнопка открывает шторку «Поделиться» с этой вехой; вне провайдера
  // (превью-режим) её просто нет.
  const shareSheet = useOptionalShareSheet();
  const currentUser = useOptionalUser();

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
              {shareSheet !== null && (
                <button
                  type="button"
                  className="history-share"
                  title="Сделать картинку-сториз с этой вехой"
                  aria-label={`Поделиться: ${milestoneTitle(milestone)}`}
                  onClick={() =>
                    shareSheet.open({
                      subject: milestoneSubject(milestone, currentUser ?? null),
                      entry: "dashboard",
                    })
                  }
                >
                  <ShareIcon />
                  <span className="history-share-label">Поделиться</span>
                </button>
              )}
            </li>
          ))}
        </ul>
      </span>
      <a className="history-teaser-more" href={href}>
        {formatInt(count)} {pluralFormRu(count, MILESTONE_FORMS)} →
      </a>
    </section>
  );
}
