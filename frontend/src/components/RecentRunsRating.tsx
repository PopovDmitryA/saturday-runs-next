import { useCallback, useEffect, useState } from "react";
import { PlatformBadge } from "./PlatformBadge";
import { RateRunModal } from "./RateRunModal";
import { getEligibleRuns, type EligibleRun, type RatingEligibility, type RunRating } from "../lib/api";
import { formatDateLong } from "../lib/format";

export function RecentRunsRating() {
  const [data, setData] = useState<RatingEligibility | null>(null);
  const [activeRun, setActiveRun] = useState<EligibleRun | null>(null);

  useEffect(() => {
    let cancelled = false;
    getEligibleRuns()
      .then((result) => {
        if (!cancelled) setData(result);
      })
      .catch(() => {
        // тихо — блок просто не покажется
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const applyRating = useCallback((entryId: string, rating: RunRating | null) => {
    setData((prev) =>
      prev
        ? {
            ...prev,
            runs: prev.runs.map((run) =>
              run.entry_id === entryId ? { ...run, my_rating: rating } : run,
            ),
          }
        : prev,
    );
  }, []);

  // Показываем только НЕоценённые старты: оценил → ушёл из блока (иначе за
  // месяц копится 5–6 карточек и блок растягивает экран). Все оценены → блок скрыт.
  const pending = data ? data.runs.filter((run) => run.my_rating == null) : [];

  if (!data || pending.length === 0) {
    return null;
  }

  return (
    <section className="recent-ratings" aria-label="Оценка недавних пробежек">
      <div className="recent-ratings-head">
        <h2 className="recent-ratings-title">Оцените недавние старты</h2>
        <p className="recent-ratings-lead">
          Старты за последние {data.window_days} дней — оцените, как всё прошло.
        </p>
      </div>

      {!data.can_rate && (
        <p className="recent-ratings-gate">
          Оценивать можно после {data.min_runs_required} пробежек в истории — у вас пока{" "}
          {data.total_runs}. Пробегите ещё немного и возвращайтесь 🙂
        </p>
      )}

      <ul className="recent-ratings-list">
        {pending.map((run) => {
          const details: string[] = [];
          if (run.participation_type === "volunteer") details.push("волонтёрство");
          if (run.finish_time_display) details.push(run.finish_time_display);
          if (run.position != null) details.push(`${run.position} место`);
          return (
            <li key={run.entry_id} className="recent-ratings-item">
              <div className="recent-ratings-info">
                <div className="recent-ratings-loc">
                  <span className="recent-ratings-name">{run.location_name}</span>
                  <PlatformBadge code={run.platform_code} />
                </div>
                <div className="recent-ratings-meta">
                  {formatDateLong(run.event_date)}
                  {details.length > 0 && ` · ${details.join(" · ")}`}
                </div>
              </div>
              <div className="recent-ratings-action">
                <button
                  type="button"
                  className="btn primary"
                  disabled={!data.can_rate}
                  onClick={() => setActiveRun(run)}
                >
                  Оценить
                </button>
              </div>
            </li>
          );
        })}
      </ul>

      {activeRun && (
        <RateRunModal
          run={activeRun}
          onClose={() => setActiveRun(null)}
          onSaved={(rating) => {
            applyRating(activeRun.entry_id, rating);
            setActiveRun(null);
          }}
          onDeleted={() => {
            applyRating(activeRun.entry_id, null);
            setActiveRun(null);
          }}
        />
      )}
    </section>
  );
}
