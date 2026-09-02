// Карточка-момент на дашборде: сайт сам предлагает поделиться, когда есть
// чем — свежий личный рекорд, новая локация, свежая суббота, длинная серия.
// Паттерн Duolingo: предлагать шаринг на пике, а не кнопкой в углу.

import { useEffect, useMemo, useState } from "react";
import { ShareIcon } from "../../components/ShareIcon";
import { listRuns, type DashboardStats, type RunItem, type User } from "../../lib/api";
import { formatDate, pluralFormRu } from "../../lib/format";
import { trackShareMomentShown } from "./analytics";
import { useOptionalShareSheet } from "./ShareSheetContext";
import { runSubject, summarySubject } from "./subjects";
import type { ShareSubject } from "./types";

/** Пробежка «свежая», пока ей меньше недели: суббота + пара дней на эмоции. */
function isFresh(run: RunItem): boolean {
  const eventTime = new Date(`${run.event_date}T00:00:00`).getTime();
  return Date.now() - eventTime < 7 * 24 * 3600 * 1000;
}

type Moment = {
  id: string;
  emoji: string;
  title: string;
  subtitle: string;
  subject: ShareSubject;
};


export function ShareMomentCard({ stats, user }: { stats: DashboardStats; user: User }) {
  const sheet = useOptionalShareSheet();
  const [lastRun, setLastRun] = useState<RunItem | null | undefined>(undefined);

  useEffect(() => {
    let cancelled = false;
    listRuns(false, 1)
      .then((runs) => {
        if (!cancelled) {
          setLastRun(runs[0] ?? null);
        }
      })
      .catch(() => {
        if (!cancelled) {
          setLastRun(null);
        }
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const moment: Moment | null = useMemo(() => {
    if (lastRun === undefined) {
      return null;
    }
    if (lastRun && isFresh(lastRun)) {
      const subject = runSubject(lastRun, user);
      if (lastRun.is_global_pr || lastRun.is_pr) {
        return {
          id: `pr-${lastRun.event_date}`,
          emoji: "⚡",
          title: "Новый личный рекорд!",
          subtitle: `${lastRun.location_name} · ${lastRun.finish_time_display ?? ""} — поделитесь с друзьями`,
          subject,
        };
      }
      if (lastRun.is_first_run_at_location) {
        return {
          id: `newloc-${lastRun.event_date}`,
          emoji: "🧭",
          title: "Новая локация в коллекции",
          subtitle: `${lastRun.location_name} · ${formatDate(lastRun.event_date)} — расскажите об этом`,
          subject,
        };
      }
      return {
        id: `run-${lastRun.event_date}`,
        emoji: "🏁",
        title: "Суббота засчитана",
        subtitle: `${lastRun.location_name} · ${formatDate(lastRun.event_date)} — поделитесь стартом`,
        subject,
      };
    }
    const streak = stats.analytics.saturday_streak ?? 0;
    if (streak >= 5) {
      return {
        id: `streak-${streak}`,
        emoji: "🔥",
        title: `Серия: ${streak} ${pluralFormRu(streak, ["суббота", "субботы", "суббот"])} подряд`,
        subtitle: "Такой стрик заслуживает сториз",
        subject: summarySubject(stats, user, "year", []),
      };
    }
    return null;
  }, [lastRun, stats, user]);

  useEffect(() => {
    if (moment) {
      trackShareMomentShown(moment.subject.kind, "dashboard");
    }
  }, [moment?.id, moment?.subject.kind]);

  if (!moment || sheet === null) {
    return null;
  }

  return (
    <section className="s2-moment-card" aria-label="Поделиться моментом">
      <span aria-hidden="true" style={{ fontSize: "1.6rem" }}>
        {moment.emoji}
      </span>
      <div className="s2-moment-card-text">
        <div className="s2-moment-card-title">{moment.title}</div>
        <div className="s2-moment-card-sub">{moment.subtitle}</div>
      </div>
      <button
        type="button"
        className="share-cta"
        onClick={() => sheet.open({ subject: moment.subject, entry: "dashboard" })}
      >
        <ShareIcon />
        Поделиться
      </button>
    </section>
  );
}
