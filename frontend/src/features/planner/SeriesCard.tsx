import { forwardRef } from "react";
import { LocationNameLink } from "../../components/LocationNameLink";
import type { PlanSeries } from "./plannerApi";
import { formatDayMonth, planDayLabel } from "./plannerDates";
import { PROFILE_LINKS_HREF, SERIES_ANCHOR } from "./plannerLinks";
import { SERIES_ICON, seriesNeedsRenewal, seriesSpanText, shortDays } from "./seriesText";
import "./plansPage.css";

/**
 * Постоянный план над месяцем в «Планах»: «↻ Зеленоград — каждую субботу · с
 * 31 октября», ближайшая суббота, что пропускаете, и «Изменить · Остановить».
 * Срок (у планов, заведённых прежним окном) подходит к концу или кончился —
 * «Продлить». На паузе — без списка пропущенных стартов (решение 26.10.2026:
 * никаких счётчиков неявок), только «Продолжить · Остановить».
 *
 * «Остановить план» — красным контуром, а не серой «призрачной» кнопкой:
 * Дмитрий его не нашёл (отзыв 27.10.2026). Тот же выход — у «Убрать» в
 * деталях любой субботы постоянного и в окне «Изменить».
 */
export const SeriesCard = forwardRef<
  HTMLElement,
  {
    series: PlanSeries;
    todayIso: string;
    busy: boolean;
    onEdit: () => void;
    onStop: () => void;
    onResume: () => void;
    onOpenDay: (iso: string) => void;
  }
>(function SeriesCard({ series, todayIso, busy, onEdit, onStop, onResume, onOpenDay }, ref) {
  const renew = seriesNeedsRenewal(series, todayIso);
  const away = series.away.map((item) => `${shortDays([item.date])} — ${item.location.name}`);
  const skipped = series.skipped.length > 0 ? shortDays(series.skipped.slice(0, 6)) : null;
  const moreSkipped = series.skipped.length > 6 ? ` и ещё ${series.skipped.length - 6}` : "";
  return (
    <section
      ref={ref}
      id={SERIES_ANCHOR}
      tabIndex={-1}
      className={`card plans-series-card${series.sleeping ? " plans-series-card-sleeping" : ""}`}
      aria-labelledby="plans-series-title"
    >
      <h2 className="plans-series-title" id="plans-series-title">
        <span className="plans-series-icon" aria-hidden="true">
          {SERIES_ICON}
        </span>{" "}
        <LocationNameLink name={series.location.name} slug={series.location.slug} /> — каждую субботу
        {series.sleeping && <span className="plans-series-badge">на паузе</span>}
      </h2>
      <p className="plans-series-meta">
        {seriesSpanText(series)}
        {!series.until && " · пока не остановите"}
        {/* Публичность — одна на весь план, меняют её в «Изменить». */}
        {series.is_public === false && " · приватно"}
      </p>
      {series.sleeping ? (
        <p className="plans-series-text">
          План на паузе — на странице локации вас пока не видно.{" "}
          {series.wakes_by_presence ? (
            "Пробежите или поволонтёрите там — продолжится сам."
          ) : (
            <>
              Пока профиль системы не привязан, ваших пробежек мы не видим: <a href={PROFILE_LINKS_HREF}>привяжите его</a> — или
              нажмите «Продолжить».
            </>
          )}
        </p>
      ) : series.expired ? (
        <p className="plans-series-text">
          Срок закончился{series.until ? ` ${formatDayMonth(series.until)}` : ""}. Продлите — иначе через месяц план
          остановится сам.
        </p>
      ) : !series.plannable ? (
        <p className="plans-series-text">Локация сейчас не проводит старты — будущих суббот в плане нет.</p>
      ) : (
        series.next_date && (
          <p className="plans-series-text">
            Ближайшая:{" "}
            <button type="button" className="link-button" onClick={() => onOpenDay(series.next_date as string)}>
              {planDayLabel(series.next_date)}
            </button>
          </p>
        )
      )}
      {!series.sleeping && (skipped || away.length > 0) && (
        <p className="plans-series-skips muted">
          {skipped && `Пропускаете: ${skipped}${moreSkipped}`}
          {skipped && away.length > 0 && " · "}
          {away.length > 0 && `В других локациях: ${away.join(", ")}`}
        </p>
      )}
      <div className="actions-row plans-series-actions">
        {series.sleeping ? (
          <button type="button" className="btn primary btn-sm" disabled={busy} onClick={onResume}>
            {busy && <span className="btn-spinner" aria-hidden="true" />}
            Продолжить
          </button>
        ) : (
          <button type="button" className={`btn ${renew ? "primary" : "secondary"} btn-sm`} onClick={onEdit}>
            {renew ? "Продлить" : "Изменить"}
          </button>
        )}
        <button type="button" className="btn btn-ghost btn-danger btn-sm" disabled={busy} onClick={onStop}>
          Остановить план
        </button>
      </div>
    </section>
  );
});
