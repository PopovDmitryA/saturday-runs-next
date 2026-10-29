import { useMemo } from "react";
import { RequireAuth } from "../../components/RequireAuth";
import { useCachedResource } from "../../hooks/useCachedResource";
import { useRestorableState } from "../../hooks/useRestorableState";
import { formatInt } from "../../lib/format";
import { locationHintFor } from "../../lib/locationHint";
import { PORTAL_LOGIN_HREF } from "../../lib/portalRoutes";
import { PortalSectionShell } from "../portal/PortalSectionShell";
import { moscowTodayIso } from "../planner/NextPlanCard";
import { PlanPeople, verbForCount } from "../planner/PlanPeople";
import { DayDetailsHead, dayTitle, MonthCalendar, type CalendarDayView } from "../planner/MonthCalendar";
import { getOrganizerCalendar, type OrganizerCalendarDay, type PlanAudience } from "../planner/plannerApi";
import { formatWeekdayDayMonth } from "../planner/plannerDates";
import { SERIES_ICON } from "../planner/seriesText";
import { organizerGuestHref, plannersLine } from "./guestParts";
import { OrganizerBreadcrumbs } from "./OrganizerBreadcrumbs";
import { OrganizerDenied } from "./OrganizerDenied";
import "../planner/plansPage.css";
import "./organizer.css";

/**
 * «Сб 31 октября · ≈№214» — что за старт; прошедший — с номером из протокола.
 * Времени старта в планировщике нет — план про дату (решение Дмитрия 26.10.2026).
 */
function startLine(day: OrganizerCalendarDay): string {
  const parts = [dayTitle(day.date)];
  if (day.title) parts.push(day.title);
  // Точка прилипает к слову слева: перенесённая строка не начинается с «·».
  const separator = " · ";
  if (day.past) {
    if (day.number != null) parts.push(`№${formatInt(day.number)}`);
    return parts.join(separator);
  }
  if (day.number_forecast != null) parts.push(`≈№${formatInt(day.number_forecast)}`);
  return parts.join(separator);
}

/**
 * Прошедший старт по частям: «планировали разово 4 — пришли 3», «постоянных
 * на старте 16 из 25»; без постоянных — «планировали 4 — пришли 3». Имён у
 * прошедшего дня сервер не отдаёт (people пуст, приватных и детей — 0),
 * поэтому разовых считаем как planned минус постоянные. dash — связка
 * «планировали» и «пришли»: на экране тире, читалке — запятая.
 */
function pastSummaryParts(day: OrganizerCalendarDay, dash: string): string[] {
  const regulars = day.regulars?.total ?? 0;
  const came = day.came == null ? null : `пришли ${formatInt(day.came)}`;
  if (regulars === 0) {
    return [`планировали ${formatInt(day.planned)}${dash}${came ?? "протокола пока нет"}`];
  }
  const oneOff = Math.max(0, day.planned - regulars);
  const parts: string[] = [];
  if (oneOff > 0) {
    parts.push(`планировали разово ${formatInt(oneOff)}${came ? `${dash}${came}` : ""}`);
  }
  parts.push(
    day.regulars_came == null
      ? `постоянных ${formatInt(regulars)}`
      : `постоянных на старте ${formatInt(day.regulars_came)} из ${formatInt(regulars)}`,
  );
  if (day.came == null && day.regulars_came == null) {
    parts.push("протокола пока нет");
  }
  return parts;
}

function dayAriaLabel(day: OrganizerCalendarDay, todayIso: string): string {
  const base = `${formatWeekdayDayMonth(day.date)}${day.date === todayIso ? ", сегодня" : ""}`;
  if (day.past) {
    if (day.planned === 0) return `${base}: старт прошёл`;
    return `${base}: ${pastSummaryParts(day, ", ").join("; ")}`;
  }
  if (day.planned === 0) return `${base}: старт, планов пока нет`;
  return `${base}: ${verbForCount(day.planned, "планирует", "планируют")} ${formatInt(day.planned)}`;
}

/**
 * Разовые и постоянные одним списком (решение Дмитрия 27.10.2026, как на
 * странице локации и в «Кто у нас бежит»): у постоянного — ↻ у имени. Порядок
 * — по числу пробежек, без привязок в конце; при равенстве разовые раньше
 * (сортировка устойчива). Приватные и дети — общими числами.
 */
function mergedAudience(day: OrganizerCalendarDay): PlanAudience {
  const regulars = day.regulars;
  const people = [
    ...day.people.map((person) => ({ ...person, regular: false })),
    ...(regulars?.people ?? []).map((person) => ({ ...person, regular: true })),
  ];
  const rank = (runs: number | null) => (runs == null ? -1 : runs);
  people.sort((a, b) => rank(b.runs) - rank(a.runs));
  return {
    people,
    private: day.private + (regulars?.private ?? 0),
    children: day.children + (regulars?.children ?? 0),
  };
}

/**
 * Будущий старт — все имена с числом пробежек и метками «впервые у нас» и
 * юбилеями, без потолка; у постоянных — ↻. На странице локации тот же список
 * короче: меток там нет, имён — не больше трёх строк (решение Дмитрия
 * 25.10.2026).
 */
function FutureDayDetails({ day, slug }: { day: OrganizerCalendarDay; slug: string }) {
  if (day.planned === 0) {
    return <p className="muted">Пока никто не отметил этот старт.</p>;
  }
  // Имя ведёт на страницу участника в оргкабинете, а не в профиль.
  const guestHref = (person: { handle: string | null }) =>
    person.handle ? organizerGuestHref(slug, person.handle) : null;
  return (
    <>
      <p className="plans-org-summary">{plannersLine(day.planned, day.regulars?.total ?? 0)}</p>
      <PlanPeople audience={mergedAudience(day)} personHref={guestHref} />
    </>
  );
}

/**
 * «Планировали разово 4 — пришли 3 · постоянных на старте 16 из 25». Без
 * постоянных — по-старому: «Планировали 4 — пришли 3».
 */
function PastDayDetails({ day }: { day: OrganizerCalendarDay }) {
  if (day.planned === 0) {
    return <p className="muted">Планов на этот старт не было.</p>;
  }
  const text = pastSummaryParts(day, " — ").join(" · ");
  return <p className="plans-org-summary">{text.charAt(0).toUpperCase() + text.slice(1)}</p>;
}

/**
 * Календарь локации для оргкоманды: сколько людей собирается пробежать на
 * каждом старте и сколько из планировавших пришли (сервер считает любое
 * присутствие в протоколе старта). Имена — только тех, у кого планы
 * публичные; приватные остаются числом, их не видит и организатор.
 */
function OrganizerCalendarContent({ slug }: { slug: string }) {
  const todayIso = useMemo(() => moscowTodayIso(), []);
  const [month, setMonth] = useRestorableState("organizer.calendar.month", () => todayIso.slice(0, 7));
  // День выбирает только организатор: при входе и при смене месяца выбора нет,
  // иначе рамка подставленного дня закрывала бы цвет особой даты. После
  // «Назад» выбор восстанавливается вместе с месяцем.
  const [picked, setPicked] = useRestorableState<string | null>("organizer.calendar.day", null);
  const key = `planner:organizer:${slug}:${month}`;
  // Ответ несёт свой ключ: пока грузится другой месяц, старты прежнего
  // в сетке нового не рисуем.
  const { data: loaded, error, forbidden, notFound } = useCachedResource(
    key,
    () => getOrganizerCalendar(slug, month).then((payload) => ({ key, payload })),
    [key],
    { errorText: "Не удалось загрузить календарь" },
  );
  const data = loaded?.key === key ? loaded.payload : null;

  const days = useMemo(() => {
    const map = new Map<string, OrganizerCalendarDay>();
    for (const day of data?.days ?? []) map.set(day.date, day);
    return map;
  }, [data]);

  const selected = picked && days.has(picked) ? picked : null;

  const name = loaded?.payload.location.name ?? locationHintFor(slug)?.name ?? null;
  const sidebar = {
    active: "organizer" as const,
    location: name ? { slug, name } : locationHintFor(slug),
  };

  if (forbidden || notFound) {
    return (
      <PortalSectionShell sidebar={sidebar}>
        <OrganizerDenied slug={slug} notFound={notFound} />
      </PortalSectionShell>
    );
  }

  const dayView = (iso: string): CalendarDayView => {
    const day = days.get(iso);
    if (!day) return { active: false };
    return {
      active: true,
      label: dayAriaLabel(day, todayIso),
      className: `plans-day-start${day.kind === "special" ? " plans-day-special" : ""}`,
      marks:
        day.planned > 0 ? (
          <span className={`plans-count${day.past ? " plans-count-past" : ""}`}>{formatInt(day.planned)}</span>
        ) : null,
    };
  };

  const selectedDay = selected ? days.get(selected) : undefined;
  // Оранжевый в сетке без подписи в легенде не читается — пункт показываем,
  // только когда особая дата есть в этом месяце.
  const monthHasSpecial = (data?.days ?? []).some((day) => day.kind === "special");

  return (
    <PortalSectionShell sidebar={sidebar}>
      <header className="loc-header">
        <OrganizerBreadcrumbs slug={slug} locationName={name} tool="Календарь локации" />
        <div className="loc-header-title">
          <h1>{name ?? "Локация"} — календарь локации</h1>
        </div>
        <p className="muted">
          Кто собирается пробежать на ближайшие старты. Участники отмечают дату кнопкой «Планирую» на
          странице локации, отметка ни к чему не обязывает. Постоянные — те, кто отметил «каждую субботу», — со значком{" "}
          {SERIES_ICON}. Имена видны только у публичных планов, по имени открывается страница участника. По прошедшим стартам — сколько из планировавших пришли.
        </p>
      </header>

      {error && !data && (
        <div className="card error">
          <p>{error}</p>
        </div>
      )}

      <section className="card plans-month-card">
        <MonthCalendar
          month={month}
          todayIso={todayIso}
          selected={selected}
          onSelect={setPicked}
          onMonthChange={(next) => {
            setMonth(next);
            setPicked(null);
          }}
          dayView={dayView}
        />
        <ul className="plans-legend" aria-label="Обозначения">
          <li>
            <span className="plans-legend-start" aria-hidden="true" /> день старта
          </li>
          {monthHasSpecial && (
            <li>
              <span className="plans-legend-special" aria-hidden="true" /> особые даты — дополнительные старты (1 января и
              другие)
            </li>
          )}
          <li>
            <span className="plans-count" aria-hidden="true">
              7
            </span>{" "}
            сколько планируют
          </li>
        </ul>

        {/* ym-hide-content: имена с метками только для организатора («250-я
            пробежка», «впервые у нас») вебвизор Метрики не пишет — как «Кто у
            нас бежит» и страницу участника. */}
        <section className="plans-details ym-hide-content" aria-live="polite">
          {!data ? (
            !error && <p className="muted">Загрузка…</p>
          ) : selectedDay ? (
            <>
              <DayDetailsHead iso={selectedDay.date} onClear={() => setPicked(null)}>
                {startLine(selectedDay)}
              </DayDetailsHead>
              {selectedDay.past ? (
                <PastDayDetails day={selectedDay} />
              ) : (
                <FutureDayDetails day={selectedDay} slug={slug} />
              )}
            </>
          ) : days.size === 0 ? (
            <p className="muted">В этом месяце стартов нет.</p>
          ) : (
            <p className="muted plans-details-hint">Нажмите на день старта, чтобы увидеть, кто собирается.</p>
          )}
        </section>
      </section>
    </PortalSectionShell>
  );
}

export function OrganizerCalendarPage({ slug }: { slug: string }) {
  return (
    <RequireAuth loginHref={PORTAL_LOGIN_HREF}>
      {() => <OrganizerCalendarContent slug={slug} />}
    </RequireAuth>
  );
}
