import type { ReactNode } from "react";
import {
  formatMonthTitle,
  formatWeekdayDayMonth,
  monthGrid,
  parseIsoDay,
  shiftMonthKey,
  WEEKDAYS_SHORT_MON_FIRST,
  weekdayMonFirst,
} from "./plannerDates";
import "./plansPage.css";

/** Сколько месяцев листаем в обе стороны: планы — на год вперёд, история — на год назад. */
export const CALENDAR_MONTH_SPAN = 12;

// Общий календарь месяца: «Мои планы», вкладка «Планы» профиля и оргкабинет.
// Отдельным файлом: профилю и оргкабинету тянуть страницу «Мои планы» с
// окном плана незачем.

export type CalendarDayView = {
  /** Есть что показать по клику — день становится кнопкой. */
  active: boolean;
  /** Подпись для экранного диктора; без неё — «сб 31 октября». */
  label?: string;
  className?: string;
  /** Мелкая подпись под числом — в кабинете название локации плана. */
  caption?: ReactNode;
  /**
   * Подпись читает и экранный диктор (по умолчанию — нет, хватает label
   * кнопки): в профиле день не нажимается, а в подписи — ссылки на локации.
   */
  captionAccessible?: boolean;
  /** Всплывающая подсказка клетки (наведение, на телефоне — тап): полный список локаций дня. */
  title?: string;
  /** Отметки под числом: точки, ✓, счётчик. */
  marks?: ReactNode;
};

/**
 * Сетка месяца пн–вс со стрелками. Что в клетке — решает страница через
 * dayView; клик по дню только выбирает его, детали страница рисует под сеткой.
 * Повторный клик по выбранному дню и Esc снимают выбор (onSelect(null)):
 * без выбора сетка видна целиком — цвет особых дат и отметки ничем не закрыты.
 */
export function MonthCalendar({
  month,
  todayIso,
  selected,
  onSelect,
  onMonthChange,
  dayView,
  highlightSaturdays = false,
  pastMonths = CALENDAR_MONTH_SPAN,
}: {
  month: string;
  todayIso: string;
  selected: string | null;
  onSelect: (iso: string | null) => void;
  onMonthChange: (month: string) => void;
  dayView: (iso: string) => CalendarDayView;
  /** Подсветить все субботы — в кабинете это даты, на которые можно планировать. */
  highlightSaturdays?: boolean;
  /** Сколько месяцев назад листать; у профиля прошлого нет — 0. */
  pastMonths?: number;
}) {
  const todayMonth = todayIso.slice(0, 7);
  const minMonth = shiftMonthKey(todayMonth, -pastMonths);
  const maxMonth = shiftMonthKey(todayMonth, CALENDAR_MONTH_SPAN);
  const [year, monthNumber] = month.split("-").map(Number);
  const cells = monthGrid(month);
  return (
    <div
      className="plans-cal"
      onKeyDown={(event) => {
        if (event.key === "Escape" && selected !== null) {
          event.preventDefault();
          event.stopPropagation();
          onSelect(null);
        }
      }}
    >
      <div className="plans-cal-head">
        <button
          type="button"
          className="plans-cal-nav"
          aria-label="Предыдущий месяц"
          disabled={month <= minMonth}
          onClick={() => onMonthChange(shiftMonthKey(month, -1))}
        >
          ‹
        </button>
        <h2 className="plans-cal-title">{formatMonthTitle(year, monthNumber - 1)}</h2>
        <button
          type="button"
          className="plans-cal-nav"
          aria-label="Следующий месяц"
          disabled={month >= maxMonth}
          onClick={() => onMonthChange(shiftMonthKey(month, 1))}
        >
          ›
        </button>
        {/* Только листает к текущему месяцу, день не выбирает: «сегодня» там
            видно по подписи под числом. */}
        {month !== todayMonth && (
          <button
            type="button"
            className="btn btn-ghost btn-sm plans-cal-today"
            title="Перейти к текущему месяцу"
            onClick={() => onMonthChange(todayMonth)}
          >
            К сегодня
          </button>
        )}
      </div>
      <div className="plans-cal-weekdays" aria-hidden="true">
        {WEEKDAYS_SHORT_MON_FIRST.map((name) => (
          <span key={name}>{name}</span>
        ))}
      </div>
      <div className="plans-cal-grid">
        {cells.map((cell) => {
          if (!cell.inMonth) {
            return <span key={cell.iso} className="plans-day plans-day-out" aria-hidden="true" />;
          }
          const view = dayView(cell.iso);
          const isToday = cell.iso === todayIso;
          // Выбор держится только за кнопку: неактивный день выбрать нельзя.
          const isSelected = view.active && selected === cell.iso;
          const classes = [
            "plans-day",
            highlightSaturdays && weekdayMonFirst(parseIsoDay(cell.iso)) === 5 ? "plans-day-sat" : null,
            isToday ? "plans-day-today" : null,
            cell.iso < todayIso ? "plans-day-past" : null,
            view.className ?? null,
            isSelected ? "plans-day-selected" : null,
          ]
            .filter(Boolean)
            .join(" ");
          // «Сегодня» — тихая подпись под числом, без рамки и заливки: рамка и
          // цвет клетки заняты выбором, днём старта и особой датой.
          const content = (
            <>
              <span className="plans-day-num" aria-hidden={!view.active && view.label ? true : undefined}>
                {cell.day}
              </span>
              {isToday && <span className="plans-day-today-label">сегодня</span>}
              {view.caption && (
                <span className="plans-day-caption" aria-hidden={view.captionAccessible ? undefined : true}>
                  {view.caption}
                </span>
              )}
              {view.marks && (
                <span className="plans-day-marks" aria-hidden="true">
                  {view.marks}
                </span>
              )}
            </>
          );
          if (!view.active) {
            // Неактивный день с подписью для диктора: дата — словами перед
            // подписью, число в клетке ему ни о чём не скажет.
            return (
              <span key={cell.iso} className={classes} title={view.title} aria-current={isToday ? "date" : undefined}>
                {view.label && <span className="visually-hidden">{view.label}</span>}
                {content}
              </span>
            );
          }
          const today = isToday ? ", сегодня" : "";
          return (
            <button
              key={cell.iso}
              type="button"
              className={classes}
              data-iso={cell.iso}
              aria-pressed={isSelected}
              aria-current={isToday ? "date" : undefined}
              aria-label={view.label ?? `${formatWeekdayDayMonth(cell.iso)}${today}`}
              onClick={() => onSelect(isSelected ? null : cell.iso)}
            >
              {content}
            </button>
          );
        })}
      </div>
    </div>
  );
}

/** «Сб 31 октября» — с заглавной, для заголовка дня. */
export function dayTitle(iso: string): string {
  const text = formatWeekdayDayMonth(iso);
  return text.charAt(0).toUpperCase() + text.slice(1);
}

/**
 * Заголовок деталей выбранного дня с крестиком «Снять выбор» — то же, что
 * повторное нажатие на день, но заметно и на телефоне, где Esc нет.
 */
export function DayDetailsHead({
  iso,
  onClear,
  children,
}: {
  iso: string;
  onClear: () => void;
  children: ReactNode;
}) {
  return (
    <div className="plans-details-head">
      <h3 className="plans-details-title">{children}</h3>
      <button
        type="button"
        className="plans-details-clear"
        aria-label="Снять выбор дня"
        title="Снять выбор"
        onClick={() => {
          onClear();
          // Крестик исчезнет вместе с деталями — фокус возвращаем на клетку дня.
          requestAnimationFrame(() =>
            document.querySelector<HTMLElement>(`.plans-day[data-iso="${iso}"]`)?.focus(),
          );
        }}
      >
        ✕
      </button>
    </div>
  );
}
