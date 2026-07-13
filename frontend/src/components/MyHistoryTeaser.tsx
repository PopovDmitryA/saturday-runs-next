import { useEffect, useState } from "react";
import { milestoneTitle, milestoneVisual } from "../features/history/HistoryPage";
import type { MyHistory } from "../lib/api";
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

type MyHistoryTeaserProps = {
  load: () => Promise<MyHistory>;
  /** Ссылка на полный таймлайн: /history или /demo/history. */
  href: string;
};

// Тизер «Моей истории» на дашборде: последняя веха + ссылка на весь таймлайн.
export function MyHistoryTeaser({ load, href }: MyHistoryTeaserProps) {
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
  const last = milestones[milestones.length - 1];
  const age = daysSince(last.event_date);
  // Свежих вех больше месяца не было — тизер прячем с дашборда, вся история
  // всё равно остаётся доступной на /history.
  if (age != null && age > TEASER_FRESHNESS_DAYS) {
    return null;
  }
  const visual = milestoneVisual(last);
  const count = milestones.length;

  return (
    <a className="card history-teaser" href={href} aria-label="Моя история — таймлайн вех">
      <span className={`history-teaser-icon ${visual.className}`} aria-hidden="true">
        {visual.icon}
      </span>
      <span className="history-teaser-body">
        <span className="history-teaser-title">Моя история</span>
        <span className="history-teaser-last">
          {milestoneTitle(last)} · {formatDateLong(last.event_date)}
        </span>
      </span>
      <span className="history-teaser-more">
        {count} {pluralFormRu(count, MILESTONE_FORMS)} →
      </span>
    </a>
  );
}
