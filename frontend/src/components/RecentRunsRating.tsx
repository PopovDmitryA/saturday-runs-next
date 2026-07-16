import { useCallback, useEffect, useState } from "react";
import { PlatformBadge } from "./PlatformBadge";
import { ParticipationBadge } from "./ParticipationBadge";
import { RateRunModal } from "./RateRunModal";
import { getEligibleRuns, type EligibleRun, type RatingEligibility, type RunRating } from "../lib/api";
import { formatDateLong, pluralizeRu } from "../lib/format";

const DISMISSED_STORAGE_KEY = "recentRatingsDismissed";

function loadDismissed(): Set<string> {
  try {
    const raw = localStorage.getItem(DISMISSED_STORAGE_KEY);
    return raw ? new Set(JSON.parse(raw)) : new Set();
  } catch {
    return new Set();
  }
}

function saveDismissed(ids: Set<string>) {
  try {
    localStorage.setItem(DISMISSED_STORAGE_KEY, JSON.stringify([...ids]));
  } catch {
    // localStorage недоступен — просто не сохраняем
  }
}

export function RecentRunsRating() {
  const [data, setData] = useState<RatingEligibility | null>(null);
  const [activeRun, setActiveRun] = useState<EligibleRun | null>(null);
  const [dismissed, setDismissed] = useState<Set<string>>(() => loadDismissed());

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

  const dismissRun = useCallback((entryId: string) => {
    setDismissed((prev) => {
      const next = new Set(prev);
      next.add(entryId);
      saveDismissed(next);
      return next;
    });
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

  // Показываем только НЕоценённые и НЕзакрытые старты: оценил или закрыл — ушёл
  // из блока (иначе за месяц копится 5–6 карточек и блок растягивает экран).
  // Закрытие — только локально (не влияет на возможность оценить старт из
  // разделов «Пробежки»/«Волонтёрство»).
  // Старты из добора истории (is_legacy) здесь НЕ показываем: их бывает под
  // сотню, и блок про «недавние» превратился бы в ленту на весь экран. Они
  // живут под ⭐ в «Пробежках»/«Волонтёрстве» — сюда даём только подсказку.
  const pending = data
    ? data.runs.filter(
        (run) => run.my_rating == null && !run.is_legacy && !dismissed.has(run.entry_id),
      )
    : [];
  const legacyCount = data
    ? data.runs.filter((run) => run.my_rating == null && run.is_legacy).length
    : 0;

  if (!data || (pending.length === 0 && legacyCount === 0)) {
    return null;
  }

  return (
    <section className="recent-ratings" aria-label="Оценка недавних пробежек">
      <div className="recent-ratings-head">
        <h2 className="recent-ratings-title">Оцените недавние старты</h2>
        {pending.length > 0 && (
          <p className="recent-ratings-lead">
            Старты за последние {data.window_days} дней — оцените, как всё прошло.
          </p>
        )}
      </div>

      {legacyCount > 0 && (
        <p className="recent-ratings-legacy-hint muted">
          Из вашей истории можно оценить{" "}
          {pluralizeRu(legacyCount, ["локацию", "локации", "локаций"])} — по одному старту на
          каждую, под ⭐ в <a href="/runs">«Пробежках»</a> и{" "}
          <a href="/volunteering">«Волонтёрстве»</a>.
        </p>
      )}

      {!data.can_rate && (
        <p className="recent-ratings-gate">
          Оценивать можно после {data.min_runs_required} пробежек в истории — у вас пока{" "}
          {data.total_runs}. Пробегите ещё немного и возвращайтесь 🙂
        </p>
      )}

      <ul className="recent-ratings-list">
        {pending.map((run) => {
          const details: string[] = [];
          if (run.participation_type === "volunteer") {
            if (run.volunteer_role) details.push(run.volunteer_role);
          } else {
            if (run.finish_time_display) details.push(run.finish_time_display);
            if (run.position != null) details.push(`${run.position} место`);
          }
          return (
            <li key={run.entry_id} className="recent-ratings-item">
              <div className="recent-ratings-info">
                <div className="recent-ratings-loc">
                  <span className="recent-ratings-name">{run.location_name}</span>
                  <PlatformBadge code={run.platform_code} />
                  <ParticipationBadge type={run.participation_type} />
                </div>
                <div className="recent-ratings-meta">
                  {formatDateLong(run.event_date)}
                  {details.length > 0 && ` · ${details.join(" · ")}`}
                </div>
              </div>
              <div className="recent-ratings-action">
                <button
                  type="button"
                  className="recent-ratings-rate-btn"
                  disabled={!data.can_rate}
                  onClick={() => setActiveRun(run)}
                  title="Оценить старт"
                  aria-label="Оценить старт"
                >
                  <svg viewBox="0 0 24 24" aria-hidden="true">
                    <path
                      d="M12 2.5l2.9 6.06 6.6.86-4.85 4.55 1.24 6.53L12 17.9l-5.89 3.06 1.24-6.53L2.5 9.42l6.6-.86L12 2.5z"
                      fill="currentColor"
                      stroke="currentColor"
                      strokeWidth="1.2"
                      strokeLinejoin="round"
                    />
                  </svg>
                </button>
                <button
                  type="button"
                  className="recent-ratings-dismiss-btn"
                  onClick={() => dismissRun(run.entry_id)}
                  title="Не оценивать этот старт"
                  aria-label="Не оценивать этот старт"
                >
                  <svg viewBox="0 0 24 24" aria-hidden="true">
                    <path
                      d="M6 6l12 12M18 6L6 18"
                      fill="none"
                      stroke="currentColor"
                      strokeWidth="2"
                      strokeLinecap="round"
                    />
                  </svg>
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
