import { useCallback, useEffect, useState } from "react";
import { milestoneTitle, milestoneVisual } from "../features/history/HistoryPage";
import { useOptionalShareSheet } from "../features/sharing/ShareSheetContext";
import { milestoneSubject, runSubject, volunteeringSubject } from "../features/sharing/subjects";
import type { ShareSubject } from "../features/sharing/types";
import { useSnackbar } from "../hooks/useSnackbar";
import {
  getEligibleRuns,
  getMyHistory,
  listRuns,
  listVolunteering,
  type EligibleRun,
  type LastSaturday,
  type MyHistoryMilestone,
  type RatingEligibility,
  type RunItem,
  type RunRating,
  type User,
  type VolunteeringItem,
} from "../lib/api";
import { formatDate, formatInt, pluralFormRu } from "../lib/format";
import { useOptionalUser } from "../lib/useOptionalUser";
import { PlatformBadge } from "./PlatformBadge";
import { RateRunModal } from "./RateRunModal";
import { ShareIcon } from "./ShareIcon";
import { Snackbar } from "./Snackbar";

/**
 * Герой дашборда «Твоя последняя суббота» — один день, одно событие.
 *
 * Слева — что было: пробежка (локация, время, темп, место, дельта к прошлому
 * визиту сюда) и кнопка «Поделиться» ею. Если в этот день только помогали —
 * героем становится роль. Пробежка и волонтёрство в один день — показываем
 * пробежку: одно событие, а не список (Дмитрий, 08.09.2026).
 *
 * Справа, только в своём кабинете, — вехи «Моей истории» за эту дату со
 * ссылкой на полный список и одна кнопка «Оценить». Раньше это были три
 * плашки под карточкой (момент для шаринга, «Оцените старты», тизер истории),
 * и три из них рассказывали одну новость — «новая локация». В чужом профиле
 * карточка остаётся одной колонкой без действий.
 */
/** Разница во времени: до минуты — в секундах, дальше — «м:сс», иначе
 *  крупная дельта («926 сек») читается как техническая величина. */
function formatDeltaValue(seconds: number): string {
  if (seconds < 60) {
    return `${seconds} сек`;
  }
  const minutes = Math.floor(seconds / 60);
  return `${minutes}:${String(seconds % 60).padStart(2, "0")}`;
}

const MILESTONE_FORMS = ["веха", "вехи", "вех"] as const;

type LastSaturdayCardProps = {
  data: LastSaturday;
  own?: boolean;
  /** Имя на постере «Поделиться»; без него берётся текущий пользователь. */
  user?: User | null;
  /** Ссылка на полный таймлайн «Моя история». */
  historyHref?: string;
};

/** Что подгружает своя карточка: вехи дня, что можно оценить, чем поделиться. */
type OwnState = {
  milestones: MyHistoryMilestone[];
  milestonesTotal: number;
  eligibility: RatingEligibility | null;
  lastRun: RunItem | null;
  volunteering: VolunteeringItem[];
};

/**
 * Единственный старт дня, который предлагаем оценить: пробежка — если герой
 * пробежка, иначе волонтёрство. Старты с той же площадки — впереди.
 */
function pickDayEntry(data: LastSaturday, eligibility: RatingEligibility | null): EligibleRun | null {
  if (!eligibility) {
    return null;
  }
  const wanted = data.kind === "volunteer" ? "volunteer" : "run";
  const candidates = eligibility.runs.filter(
    (entry) => entry.event_date === data.event_date && !entry.is_legacy && entry.participation_type === wanted,
  );
  return (
    candidates.find((entry) => entry.location_slug && entry.location_slug === data.location_slug) ??
    candidates[0] ??
    null
  );
}

export function LastSaturdayCard({ data, own = false, user, historyHref }: LastSaturdayCardProps) {
  const sheet = useOptionalShareSheet();
  const optionalUser = useOptionalUser();
  const shareUser = user ?? optionalUser ?? null;
  const [ownState, setOwnState] = useState<OwnState | null>(null);
  const [activeEntry, setActiveEntry] = useState<EligibleRun | null>(null);
  const { snackbar, showSnackbar, dismissSnackbar } = useSnackbar();

  useEffect(() => {
    if (!own) {
      return;
    }
    let cancelled = false;
    // Четыре запроса — те же, что раньше делали четыре отдельные плашки;
    // каждый падает тихо, и его часть карточки просто не показывается.
    Promise.all([
      getMyHistory().catch(() => null),
      getEligibleRuns().catch(() => null),
      listRuns(false, 1).catch(() => []),
      listVolunteering(false, 5).catch(() => []),
    ]).then(([history, eligibility, runs, volunteering]) => {
      if (cancelled) {
        return;
      }
      const all = history?.milestones ?? [];
      setOwnState({
        milestones: all.filter((item) => item.event_date === data.event_date),
        milestonesTotal: all.length,
        eligibility,
        lastRun: runs[0] ?? null,
        volunteering: volunteering.filter((item) => item.event_date === data.event_date),
      });
    });
    return () => {
      cancelled = true;
    };
  }, [own, data.event_date]);

  const applyRating = useCallback((entryId: string, rating: RunRating | null) => {
    setOwnState((prev) =>
      prev?.eligibility
        ? {
            ...prev,
            eligibility: {
              ...prev.eligibility,
              runs: prev.eligibility.runs.map((entry) =>
                entry.entry_id === entryId ? { ...entry, my_rating: rating } : entry,
              ),
            },
          }
        : prev,
    );
  }, []);

  const deltaSec = data.delta_vs_prev_sec;
  const deltaChip =
    deltaSec != null && deltaSec !== 0 ? (
      <span
        className={`last-saturday-delta ${deltaSec < 0 ? "last-saturday-delta-faster" : "last-saturday-delta-slower"}`}
      >
        {deltaSec < 0 ? "↓" : "↑"} {formatDeltaValue(Math.abs(deltaSec))}{" "}
        {deltaSec < 0 ? "быстрее" : "медленнее"}, чем здесь в прошлый раз
      </span>
    ) : deltaSec === 0 ? (
      <span className="last-saturday-delta">секунда в секунду с прошлым визитом сюда</span>
    ) : null;

  const isVolunteerDay = data.kind === "volunteer";
  // Две роли в один день — через точку: «Маршал · Фотограф».
  const roleLine = data.volunteering
    .map((item) => item.role)
    .filter((role): role is string => Boolean(role))
    .join(" · ");

  // Чем делиться: пробежкой этого дня, а если только волонтёрили — ролью.
  let shareSubject: ShareSubject | null = null;
  if (own && ownState) {
    if (!isVolunteerDay && ownState.lastRun && ownState.lastRun.event_date === data.event_date) {
      shareSubject = runSubject(ownState.lastRun, shareUser);
    } else if (isVolunteerDay && ownState.volunteering[0]) {
      shareSubject = volunteeringSubject(ownState.volunteering[0], shareUser);
    }
  }

  const eligibility = ownState?.eligibility ?? null;
  const dayEntry = own ? pickDayEntry(data, eligibility) : null;
  // Правая колонка — только про вехи: оценка и «Поделиться» относятся к
  // самой пробежке и стоят слева, в строке с датой.
  const hasSide = own && ownState != null && ownState.milestonesTotal > 0;
  const rateLabel = isVolunteerDay ? "волонтёрство" : "пробежку";

  const rateButton =
    dayEntry && eligibility ? (
      dayEntry.my_rating ? (
        <button
          type="button"
          className="last-saturday-action last-saturday-action-done"
          onClick={() => setActiveEntry(dayEntry)}
          title="Изменить оценку"
        >
          ★ {dayEntry.my_rating.score_overall}/5 — ваша оценка
        </button>
      ) : (
        <button
          type="button"
          className="last-saturday-action"
          disabled={!eligibility.can_rate}
          title={
            eligibility.can_rate
              ? "Оценить старт"
              : `Оценивать можно после ${formatInt(eligibility.min_runs_required)} пробежек в истории`
          }
          onClick={() => setActiveEntry(dayEntry)}
        >
          ★ Оценить {rateLabel}
        </button>
      )
    ) : null;

  const shareButton =
    shareSubject && sheet !== null ? (
      <button
        type="button"
        className="last-saturday-action"
        title="Сделать картинку-сториз"
        onClick={() => sheet.open({ subject: shareSubject, entry: "dashboard" })}
      >
        <ShareIcon />
        Поделиться
      </button>
    ) : null;

  return (
    <div className={`card last-saturday-card${hasSide ? " last-saturday-card-own" : ""}`}>
      <div className="last-saturday-primary">
        <div className="last-saturday-head">
          <p className="last-saturday-kicker">
            {own ? "Твоя последняя суббота" : "Последняя суббота"} · {formatDate(data.event_date)}
          </p>
          {(rateButton || shareButton) && (
            <div className="last-saturday-actions">
              {rateButton}
              {shareButton}
            </div>
          )}
        </div>
        <div className="last-saturday-main">
          {data.location_slug ? (
            <a className="last-saturday-location" href={`/locations/${data.location_slug}`}>
              {data.location_name}
            </a>
          ) : (
            <span className="last-saturday-location">{data.location_name}</span>
          )}
          <PlatformBadge code={data.platform_code} />
          {data.is_pr && <span className="badge badge-pr">PR</span>}
          {data.is_first_run_at_location && (
            <span className="last-saturday-first">впервые здесь</span>
          )}
          {isVolunteerDay && <span className="last-saturday-first">волонтёрство</span>}
        </div>
        {isVolunteerDay ? (
          <div className="last-saturday-stats">
            <span className="last-saturday-stat">
              <b>{roleLine || "Волонтёр"}</b>
            </span>
          </div>
        ) : (
          <div className="last-saturday-stats">
            {data.finish_time_display && (
              <span className="last-saturday-stat">
                <b>{data.finish_time_display}</b>
              </span>
            )}
            {data.pace_display && (
              <span className="last-saturday-stat">{data.pace_display} /км</span>
            )}
            {data.position != null && (
              <span className="last-saturday-stat">{data.position} место</span>
            )}
          </div>
        )}
        {deltaChip}
        {data.notables.length > 0 && (
          <ul className="last-saturday-notables">
            {data.notables.map((note) => (
              <li key={note}>{note}</li>
            ))}
          </ul>
        )}
      </div>

      {hasSide && ownState && (
        <div className="last-saturday-side">
          {ownState.milestonesTotal > 0 && (
            <>
              <p className="last-saturday-side-title">
                {ownState.milestones.length > 0 ? "Вехи этого дня" : "Моя история"}
              </p>
              {ownState.milestones.length > 0 && (
                <ul className="last-saturday-milestones">
                  {ownState.milestones.map((milestone, index) => {
                    const visual = milestoneVisual(milestone);
                    const title = milestoneTitle(milestone);
                    return (
                      <li
                        key={`${milestone.kind}-${milestone.number ?? index}`}
                        className="last-saturday-milestone"
                      >
                        <span
                          className={`last-saturday-milestone-icon ${visual.className}`}
                          aria-hidden="true"
                        >
                          {visual.icon}
                        </span>
                        <span className="last-saturday-milestone-title">{title}</span>
                        {sheet !== null && (
                          <button
                            type="button"
                            className="history-share"
                            title="Сделать картинку-сториз с этой вехой"
                            aria-label={`Поделиться: ${title}`}
                            onClick={() =>
                              sheet.open({
                                subject: milestoneSubject(milestone, shareUser),
                                entry: "dashboard",
                              })
                            }
                          >
                            <ShareIcon />
                          </button>
                        )}
                      </li>
                    );
                  })}
                </ul>
              )}
              {historyHref && (
                <a className="last-saturday-side-more" href={historyHref}>
                  Все вехи — {formatInt(ownState.milestonesTotal)}{" "}
                  {pluralFormRu(ownState.milestonesTotal, MILESTONE_FORMS)} →
                </a>
              )}
            </>
          )}

        </div>
      )}

      {activeEntry && (
        <RateRunModal
          run={activeEntry}
          onClose={() => setActiveEntry(null)}
          onSaved={(rating) => {
            applyRating(activeEntry.entry_id, rating);
            setActiveEntry(null);
            showSnackbar({ variant: "default", title: "Спасибо!", message: "Отзыв сохранён" });
          }}
          onDeleted={() => {
            applyRating(activeEntry.entry_id, null);
            setActiveEntry(null);
          }}
        />
      )}
      <Snackbar open={snackbar.open} title={snackbar.title} variant={snackbar.variant} onDismiss={dismissSnackbar}>
        {snackbar.message}
      </Snackbar>
    </div>
  );
}
