import { Fragment } from "react";
import { LocationNameLink } from "../../components/LocationNameLink";
import { useCachedResource } from "../../hooks/useCachedResource";
import { profilePlansHref } from "../../lib/portalRoutes";
import { getProfilePlan, type ProfilePlan } from "./plannerApi";
import { planDayLabel } from "./plannerDates";
import "./plansPage.css";

/**
 * Строка в шапке профиля: «📅 Ближайший план: сб 31 октября — Зеленоград ·
 * все планы» (решения Дмитрия 26–27.10.2026). Ближайший ПУБЛИЧНЫЙ план — на
 * любую дату, не только эта суббота; «все планы» ведут во вкладку «Планы» с
 * календарём. Публичных планов впереди нет — строки нет. Приватные планы
 * другим не видны вовсе.
 *
 * Постоянный план («каждую субботу») — значок «↻» перед названием.
 *
 * Отдельный запрос, а не поле профиля: ответ зависит от зрителя и не
 * кэшируется ни сервером, ни пререндером.
 */
export function ProfilePlanLine({ handle, onPlansTab = false }: { handle: string; onPlansTab?: boolean }) {
  const { data } = useCachedResource(`planner:profile:${handle}`, () => getProfilePlan(handle), [handle]);
  if (!data || data.places.length === 0) {
    return null;
  }
  return <ProfilePlanText plan={data} plansHref={onPlansTab ? null : profilePlansHref(handle)} />;
}

/**
 * only_me сервер ставит только хозяину (другим такой строки не отдаёт) —
 * тогда «· видите только вы». Обычно хозяин сюда не попадает: свой адрес
 * открывает кабинет.
 */
function ProfilePlanText({ plan, plansHref }: { plan: ProfilePlan; plansHref: string | null }) {
  const day = planDayLabel(plan.date);
  const when = plan.kind === "special" && plan.title ? `${day} (${plan.title})` : day;
  // Приватный план хозяину (only_me) вебвизор Метрики не пишет — как свои
  // планы в кабинете (PRIV-5).
  return (
    <p className={`profile-plan-line${plan.only_me ? " ym-hide-content" : ""}`}>
      <span className="profile-plan-icon" aria-hidden="true">
        📅
      </span>
      <span>
        Ближайший план: {when} —{" "}
        {plan.places.map((place, index) => (
          <Fragment key={place.slug}>
            {index > 0 && ", "}
            {place.regular && (
              <span className="profile-plan-regular" title="Постоянный план — каждую субботу">
                <span aria-hidden="true">↻ </span>
                <span className="visually-hidden">каждую субботу: </span>
              </span>
            )}
            <LocationNameLink name={place.name} slug={place.slug} />
            {place.cancelled && <span className="profile-plan-cancel"> (старт отменён)</span>}
          </Fragment>
        ))}
        {plan.only_me && <span className="muted"> · видите только вы</span>}
        {plansHref && (
          <>
            {" · "}
            <a className="profile-plan-all" href={plansHref}>
              все планы
            </a>
          </>
        )}
      </span>
    </p>
  );
}
