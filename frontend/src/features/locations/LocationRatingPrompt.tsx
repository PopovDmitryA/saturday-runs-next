import { useEffect, useState } from "react";
import { ParticipationBadge } from "../../components/ParticipationBadge";
import { PlatformBadge } from "../../components/PlatformBadge";
import { RateRunModal } from "../../components/RateRunModal";
import { Snackbar } from "../../components/Snackbar";
import { useSnackbar } from "../../hooks/useSnackbar";
import { getEligibleRuns, type EligibleRun } from "../../lib/api";
import { formatDateLong } from "../../lib/format";
import { loadDismissedRatings, saveDismissedRatings } from "../../lib/ratingDismissals";

/**
 * Карточка-рекомендация на странице локации: «вы бегали тут в дату X — оцените
 * свой старт». Показываем самый свежий доступный к оценке старт именно этой
 * площадки (включая старты из добора истории: страница локации — как раз то
 * место, где такое предложение уместно). Крестик — локальный отказ, общий с
 * блоком на дашборде; сам старт остаётся доступен под ⭐ в «Пробежках».
 */
export function LocationRatingPrompt({ identityKey }: { identityKey: string }) {
  const [run, setRun] = useState<EligibleRun | null>(null);
  const [canRate, setCanRate] = useState(false);
  const [modalOpen, setModalOpen] = useState(false);
  const [dismissed, setDismissed] = useState(false);
  const [rated, setRated] = useState(false);
  const { snackbar, showSnackbar, dismissSnackbar } = useSnackbar();

  useEffect(() => {
    let cancelled = false;
    setRun(null);
    setDismissed(false);
    setRated(false);
    getEligibleRuns()
      .then((result) => {
        if (cancelled) return;
        const dismissedIds = loadDismissedRatings();
        // runs приходят отсортированными по дате (свежие первыми).
        const match = result.runs.find(
          (item) =>
            item.location_identity_key === identityKey &&
            item.my_rating == null &&
            !dismissedIds.has(item.entry_id),
        );
        setCanRate(result.can_rate);
        setRun(match ?? null);
      })
      .catch(() => {
        // тихо — карточка просто не покажется
      });
    return () => {
      cancelled = true;
    };
  }, [identityKey]);

  // Право оценивать ещё не открыто — не дразним карточкой (полное объяснение
  // про порог живёт в блоке на дашборде).
  if (!run || !canRate || dismissed) {
    return null;
  }

  // После сохранения карточка прячется, но снекбар должен пережить её скрытие.
  if (rated) {
    return (
      <Snackbar open={snackbar.open} title={snackbar.title} variant={snackbar.variant} onDismiss={dismissSnackbar}>
        {snackbar.message}
      </Snackbar>
    );
  }

  const details: string[] = [];
  if (run.participation_type === "volunteer") {
    if (run.volunteer_role) details.push(run.volunteer_role);
  } else {
    if (run.finish_time_display) details.push(run.finish_time_display);
    if (run.position != null) details.push(`${run.position} место`);
  }

  const handleDismiss = () => {
    const next = loadDismissedRatings();
    next.add(run.entry_id);
    saveDismissedRatings(next);
    setDismissed(true);
  };

  return (
    <section className="card loc-rate-prompt" aria-label="Предложение оценить старт">
      <button
        type="button"
        className="loc-rate-prompt-close"
        onClick={handleDismiss}
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

      <div className="loc-rate-prompt-body">
        <p className="loc-rate-prompt-title">
          Вы {run.participation_type === "volunteer" ? "волонтёрили" : "бегали"} здесь{" "}
          {formatDateLong(run.event_date)} — оцените свой старт
        </p>
        <p className="loc-rate-prompt-meta">
          <PlatformBadge code={run.platform_code} />
          <ParticipationBadge type={run.participation_type} />
          {details.length > 0 && <span>{details.join(" · ")}</span>}
        </p>
      </div>

      <button type="button" className="btn btn-primary btn-sm" onClick={() => setModalOpen(true)}>
        ⭐ Оценить
      </button>

      {modalOpen && (
        <RateRunModal
          run={run}
          onClose={() => setModalOpen(false)}
          onSaved={() => {
            setModalOpen(false);
            setRated(true);
            showSnackbar({ variant: "default", title: "Спасибо!", message: "Отзыв сохранён" });
          }}
          onDeleted={() => {
            setModalOpen(false);
          }}
        />
      )}
    </section>
  );
}
