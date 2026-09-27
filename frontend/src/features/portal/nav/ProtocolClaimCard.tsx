/**
 * Карточка человека из протоколов для «Это вы?» — в окне поиска и в окне
 * после входа. Только цифры, как в строке выдачи: ни ссылок на профиль в
 * беговой системе (согласия этих людей у нас нет), ни нажимаемого бейджа.
 */
import type { SearchClaimPerson } from "../../../lib/api";
import { PlatformBadge } from "../../../components/PlatformBadge";
import { initials, personStats, placeWithCity } from "./personRow";
import "./siteSearch.css";

export function ProtocolClaimCard({ person }: { person: SearchClaimPerson }) {
  return (
    <div className="site-search-claim-card">
      <span className="site-search-avatar site-search-avatar-ghost site-search-claim-avatar" aria-hidden="true">
        {initials(person.display_name)}
      </span>
      <span className="site-search-claim-main">
        <span className="site-search-claim-name">
          <b>{person.display_name}</b>
          <span className="visually-hidden">, система: </span>
          <PlatformBadge code={person.platform_code} />
        </span>
        <small>{personStats(person)}</small>
        {person.top_location_name && (
          <small>чаще всего: {placeWithCity(person.top_location_name, person.top_location_city)}</small>
        )}
      </span>
    </div>
  );
}
