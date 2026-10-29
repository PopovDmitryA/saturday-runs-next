import type { ReactNode } from "react";
import type { SeriesPreview, SeriesPreviewDate } from "./plannerApi";
import { formatWeekdayDayMonthShort, planDayLabel, startFacts } from "./plannerDates";
import { runToText } from "./placeCase";
import { PROFILE_LINKS_HREF } from "./plannerLinks";
import "./planner.css";

/**
 * Часть окна постоянного плана: ближайшие субботы галочками (вид V3, выбор
 * Дмитрия 26.10.2026). Строится на предпросмотре правила с сервера — state у
 * каждой даты: on, skipped, away, cancelled.
 */

/**
 * Пояснение о паузе — без упрёка: план не удаляется. Правило одно для всех:
 * три старта подряд без вас в протоколе — пауза. Привязан профиль системы
 * этой локации — пробежка или волонтёрство там план продолжат сами; не
 * привязан — сайт ваших пробежек не видит, вернуть план — «Продолжить».
 * Про имя — только у плана с именем на странице: у приватного и у ребёнка
 * его там нет и до паузы.
 */
function SleepNote({ wakesByPresence, named }: { wakesByPresence: boolean; named: boolean }) {
  return (
    <p className="plan-modal-hint">
      Не будет вас в протоколе трёх стартов подряд — план встанет на паузу{named ? ", имя со страницы уйдёт" : ""}.{" "}
      {wakesByPresence ? (
        "Пробежите или поволонтёрите там снова — продолжится сам."
      ) : (
        <>
          Пока профиль системы не привязан, ваших пробежек мы не видим: <a href={PROFILE_LINKS_HREF}>привяжите его</a> — или
          вернёте план кнопкой «Продолжить».
        </>
      )}
    </p>
  );
}

function SeriesDateToggle({ item, on, onToggle }: { item: SeriesPreviewDate; on: boolean; onToggle: () => void }) {
  const blocked = item.state === "away" || item.state === "cancelled";
  const special = item.kind === "special";
  const facts = startFacts(item);
  const className = [
    "plan-date-chip",
    "plan-series-chip",
    special ? "plan-date-chip-special" : "",
    on && !blocked ? "plan-date-chip-selected" : "",
    !on && !blocked ? "plan-series-chip-off" : "",
  ]
    .filter(Boolean)
    .join(" ");
  return (
    <button type="button" className={className} aria-pressed={on && !blocked} disabled={blocked} onClick={onToggle}>
      <span className="plan-date-chip-day">
        <span className="plan-series-chip-box" aria-hidden="true">
          {on && !blocked ? "✓" : ""}
        </span>
        <span aria-hidden="true">{formatWeekdayDayMonthShort(item.date)}</span>
        <span className="visually-hidden">{planDayLabel(item.date)}</span>
      </span>
      {special && item.title && <span className="plan-date-chip-title">{item.title}</span>}
      {item.state === "away" ? (
        // В этот день разовый план в другой локации: «бегу в Битцу», а не
        // «ваш план — …» (замечание Дмитрия 26.10.2026).
        <span className="plan-date-chip-meta">{runToText(item.other?.name)}</span>
      ) : item.state === "cancelled" ? (
        <span className="plan-date-chip-cancel">старт отменён</span>
      ) : on ? (
        facts.length > 0 && <span className="plan-date-chip-meta">{facts.join(" · ")}</span>
      ) : (
        <span className="plan-date-chip-meta">пропущу</span>
      )}
    </button>
  );
}

/**
 * «С сб 31 октября, пока не остановите» и ближайшие субботы галочками.
 * Снятая — «Не бегаю» на этот день (имени на дате нет, напоминания тоже);
 * поездку и отменённый старт не снять и не вернуть — они серые.
 */
export function SeriesOptions({
  preview,
  until,
  editing,
  toggles,
  visible,
  onToggle,
  wakesByPresence = true,
  named = true,
  afterDates,
}: {
  preview: SeriesPreview;
  /** Срок плана, поставленный раньше; новый план срока не задаёт. */
  until: string | null;
  editing: boolean;
  toggles: Record<string, boolean>;
  visible: number;
  onToggle: (iso: string) => void;
  /**
   * Как план выходит из паузы: у действующего — PlanSeries.wakes_by_presence,
   * у нового — тот же признак из предпросмотра (без привязки к системам точки
   * обещать «продолжится сам» нельзя).
   */
  wakesByPresence?: boolean;
  /** Имя постоянного видно на странице локации: публично, профиль открыт, не ребёнок. */
  named?: boolean;
  /**
   * Сразу под субботами, до пояснения о паузе: локация и «Публично |
   * Приватно» окна. Иначе на телефоне переключатель уезжал под длинный
   * абзац о паузе, и сохраняли, не увидев его.
   */
  afterDates?: ReactNode;
}) {
  const term = until ? `до ${planDayLabel(until)}` : "пока не остановите";
  return (
    <>
      <p className="plan-series-lead">
        {editing ? `Каждую субботу ${term}.` : `С ${planDayLabel(preview.starts_on)}, ${term}.`} Субботы на год вперёд
        сайт держит сам.
      </p>
      <div className="plan-modal-field">
        <span className="plan-modal-label" id="plan-series-dates-label">
          Ближайшие субботы — снимите, если не придёте
        </span>
        <div className="plan-date-chips" role="group" aria-labelledby="plan-series-dates-label">
          {preview.dates.slice(0, visible).map((item) => (
            <SeriesDateToggle
              key={item.date}
              item={item}
              on={toggles[item.date] ?? item.state === "on"}
              onToggle={() => onToggle(item.date)}
            />
          ))}
        </div>
        <p className="plan-modal-hint">Снятая суббота — «Не бегаю»: имени на локации в этот день не будет.</p>
      </div>
      {afterDates}
      <SleepNote wakesByPresence={wakesByPresence} named={named} />
    </>
  );
}
