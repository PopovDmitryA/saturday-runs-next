import { useEffect, useMemo, useState, type ReactNode } from "react";
import { useCachedResource } from "../../hooks/useCachedResource";
import { useRestorableState } from "../../hooks/useRestorableState";
import { PORTAL_CABINET_PLANS_HREF } from "../../lib/portalRoutes";
import { MonthCalendar, type CalendarDayView } from "./MonthCalendar";
import {
  getProfileCalendar,
  getProfilePlan,
  getSpecialDates,
  type ProfileCalendarDay,
  type ProfilePlanPlace,
} from "./plannerApi";
import {
  formatWeekdayDayMonth,
  moscowTodayIso,
  parseIsoDay,
  planDayLabel,
  toIsoDay,
  weekdayMonFirst,
} from "./plannerDates";
import { SERIES_ICON } from "./seriesText";
import "./plansPage.css";

/**
 * Вкладка «Планы» профиля: календарь публичных планов человека — все даты, а
 * не только ближайшая суббота (решение Дмитрия 27.10.2026: «если план
 * публичный — в его календарь можно провалиться»). Только чтение: названия
 * локаций прямо в клетках, публичная отметка «Не бегаю» — «не бегает».
 * Приватных планов здесь нет вовсе: нет публичных — пустой календарь и
 * короткая строка. Прошлого тоже нет (сервер отдаёт дни с сегодняшнего, назад
 * календарь не листается): «сбылось / не получилось» — личное, оно в «Моих
 * планах».
 *
 * Дни не нажимаются и деталей под сеткой нет (правка Дмитрия 27.10.2026:
 * «нажатие добавляет всего две строчки, это некрасиво»). На компьютере в
 * клетке все локации дня столбиком (1 января — до трёх), названия — ссылки на
 * локации; в узкой клетке — «Кусково +1», полный список — в подсказке
 * (наведение, на телефоне — тап по клетке).
 *
 * own — свой адрес: так планы видят другие, сверху — «Все мои планы».
 *
 * Ответ зависит от зрителя и не кэшируется ни сервером, ни пререндером
 * (Cache-Control: private), вкладка профиля в индекс не идёт.
 */

const PHONE_QUERY = "(max-width: 640px)";

/**
 * Телефон: названия в клетках — без ссылок. Клетка в седьмую часть экрана,
 * ссылка в ней — мишень в палец шириной, и тап мимо уводил бы со страницы;
 * тап по клетке показывает подсказку со всеми локациями дня.
 */
function usePhoneViewport(): boolean {
  const [phone, setPhone] = useState(
    () => typeof window !== "undefined" && window.matchMedia(PHONE_QUERY).matches,
  );
  useEffect(() => {
    const mql = window.matchMedia(PHONE_QUERY);
    const onChange = (event: MediaQueryListEvent) => setPhone(event.matches);
    mql.addEventListener("change", onChange);
    return () => mql.removeEventListener("change", onChange);
  }, []);
  return phone;
}

function isSaturday(iso: string): boolean {
  return weekdayMonFirst(parseIsoDay(iso)) === 5;
}

function placeName(place: ProfilePlanPlace, links: boolean): ReactNode {
  if (!links) {
    return place.name;
  }
  return (
    <a className="plans-cap-link" href={`/locations/${encodeURIComponent(place.slug)}`}>
      {place.name}
    </a>
  );
}

/**
 * Подпись в клетке. Широкая клетка — все локации дня строками («↻ Кузьминки»,
 * «Кусково» и «Битца» 1 января), узкая — «Кузьминки +1»; какая видна, решает
 * CSS по ширине клетки (.plans-cap-list / .plans-cap-short). Без мест —
 * «не бегает».
 */
function dayCaption(day: ProfileCalendarDay, links: boolean): ReactNode {
  const first = day.places[0];
  if (!first) {
    return day.rest ? <span className="plans-cap plans-cap-muted">не бегает</span> : null;
  }
  const more = day.places.length - 1;
  return (
    <>
      <span className="plans-cap-list">
        {day.places.map((place) => (
          <span key={place.slug} className="plans-cap plans-cap-plan plans-cap-line">
            {place.regular && (
              <span className="plans-cap-icon" aria-hidden="true">
                {SERIES_ICON}
              </span>
            )}
            <span className="plans-cap-name">
              {placeName(place, links)}
              {place.regular && <span className="visually-hidden">, каждую субботу</span>}
              {place.cancelled && <span className="visually-hidden">, старт отменён</span>}
            </span>
          </span>
        ))}
      </span>
      <span className="plans-cap plans-cap-plan plans-cap-short">
        {first.regular && (
          <span className="plans-cap-icon" aria-hidden="true">
            {SERIES_ICON}
          </span>
        )}
        <span className="plans-cap-name" aria-hidden="true">
          {first.name}
        </span>
        {more > 0 && (
          <span className="plans-cap-more" aria-hidden="true">
            +{more}
          </span>
        )}
        <span className="visually-hidden">{placesText(day)}</span>
      </span>
    </>
  );
}

/** «Кусково, Битца (каждую субботу)» — подсказка клетки и текст для диктора в узкой клетке. */
function placesText(day: ProfileCalendarDay): string {
  return day.places
    .map((place) => {
      const notes = [place.regular ? "каждую субботу" : null, place.cancelled ? "старт отменён" : null].filter(Boolean);
      return notes.length > 0 ? `${place.name} (${notes.join(", ")})` : place.name;
    })
    .join(", ");
}

/** Дата словами для диктора: число в клетке ему ни о чём не скажет. */
function dayLabel(iso: string, specialTitle: string | null): string {
  return `${formatWeekdayDayMonth(iso)}${specialTitle ? `, ${specialTitle}` : ""}:`;
}

export function ProfilePlansContent({ handle, own = false }: { handle: string; own?: boolean }) {
  const clientToday = useMemo(() => moscowTodayIso(), []);
  const [month, setMonth] = useRestorableState("profile.plans.month", () => clientToday.slice(0, 7));
  const phone = usePhoneViewport();

  const [year, monthNumber] = month.split("-").map(Number);
  const from = `${month}-01`;
  const to = toIsoDay(new Date(year, monthNumber, 0));
  const key = `planner:profile-cal:${handle}:${month}`;
  // Ответ несёт свой ключ: пока грузится другой месяц, планы прежнего в сетке
  // нового не рисуем.
  const { data: loaded, error, notFound } = useCachedResource(
    key,
    () => getProfileCalendar(handle, month).then((payload) => ({ key, payload })),
    [key],
    { errorText: "Не удалось загрузить планы" },
  );
  const data = loaded?.key === key ? loaded.payload : null;
  const todayIso = data?.today ?? clientToday;

  // Ближайший публичный план — куда листать из пустого месяца.
  const { data: nearest, loading: nearestLoading } = useCachedResource(
    `planner:profile:${handle}`,
    () => getProfilePlan(handle),
    [handle],
  );

  // Особые даты месяца — подсветить, как в «Моих планах»; сбой не страшен.
  const { data: specialData } = useCachedResource(
    `planner:special:${from}:${to}`,
    () => getSpecialDates(from, to),
    [from, to],
  );
  const specialTitles = useMemo(() => {
    const map = new Map<string, string>();
    for (const item of specialData?.items ?? []) {
      map.set(item.date, item.title);
    }
    for (const day of data?.days ?? []) {
      if (day.kind === "special" && day.title) {
        map.set(day.date, day.title);
      }
    }
    return map;
  }, [specialData, data]);

  const byDay = useMemo(() => {
    const map = new Map<string, ProfileCalendarDay>();
    for (const day of data?.days ?? []) {
      if (day.places.length > 0 || day.rest) {
        map.set(day.date, day);
      }
    }
    return map;
  }, [data]);

  const hasRegular = [...byDay.values()].some((day) => day.places.some((place) => place.regular));

  const dayView = (iso: string): CalendarDayView => {
    const day = byDay.get(iso);
    const specialTitle = specialTitles.get(iso) ?? null;
    return {
      // Дни не нажимаются: всё, что о них есть, — в самой клетке.
      active: false,
      label: day ? dayLabel(iso, specialTitle) : undefined,
      title: day && day.places.length > 0 ? placesText(day) : undefined,
      // Будни — не дни стартов: тише, как в «Моих планах».
      className: [
        specialTitle ? "plans-day-special" : !day && !isSaturday(iso) ? "plans-day-off" : null,
        day && day.places.length > 1 ? "plans-day-many" : null,
      ]
        .filter(Boolean)
        .join(" "),
      caption: day ? dayCaption(day, !phone) : null,
      captionAccessible: true,
    };
  };

  // Пустой месяц: подсказать ближайший публичный план, если он в другом месяце.
  // Хозяину сервер отдаёт ближайший любой (only_me — приватный): в этом
  // календаре его нет, вести к нему незачем.
  const nearestPublic = nearest && nearest.places.length > 0 && !nearest.only_me ? nearest : null;
  const nearestElsewhere = nearestPublic && !nearestPublic.date.startsWith(month) ? nearestPublic : null;

  if (notFound) {
    return (
      <section className="card">
        <p className="muted">Планы скрыты: профиль закрыт.</p>
      </section>
    );
  }

  return (
    <div className="portal-cab-stack plans-page profile-plans">
      {own && (
        <p className="profile-plans-own">
          Так ваши планы видят другие — только публичные.{" "}
          <a className="link" href={PORTAL_CABINET_PLANS_HREF}>
            Все мои планы
          </a>
        </p>
      )}
      <section className="card plans-month-card">
        <MonthCalendar
          month={month}
          todayIso={todayIso}
          selected={null}
          onSelect={() => undefined}
          onMonthChange={setMonth}
          dayView={dayView}
          highlightSaturdays
          pastMonths={0}
        />
        {hasRegular && (
          <ul className="plans-legend" aria-label="Обозначения">
            <li>
              <span className="plans-cap plans-cap-plan plans-legend-cap">{SERIES_ICON}</span> каждую субботу
            </li>
          </ul>
        )}
        {/* Под сеткой — только когда в месяце пусто или он грузится: дни не
            нажимаются, подсказывать «нажмите» нечего. */}
        {!data || byDay.size === 0 ? (
          <section className="plans-details" aria-live="polite">
            {error && !data ? (
              <p className="error-text">{error}</p>
            ) : !data ? (
              <p className="muted">Загрузка…</p>
            ) : (
              <p className="muted plans-details-hint">
                {nearestElsewhere ? (
                  <>
                    В этом месяце публичных планов нет. Ближайший —{" "}
                    <button
                      type="button"
                      className="link-button"
                      onClick={() => setMonth(nearestElsewhere.date.slice(0, 7))}
                    >
                      {planDayLabel(nearestElsewhere.date)}
                    </button>
                    .
                  </>
                ) : !nearestLoading && (nearest === null || nearest?.upcoming_count === 0) ? (
                  // Хозяину сервер отдаёт ближайший план любой (only_me), поэтому
                  // «нет вовсе» — по числу дат с публичными планами (UI-8).
                  "Публичных планов нет."
                ) : (
                  "В этом месяце публичных планов нет."
                )}
              </p>
            )}
          </section>
        ) : null}
      </section>
    </div>
  );
}
