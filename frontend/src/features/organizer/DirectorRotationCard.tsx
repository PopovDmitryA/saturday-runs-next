import { type OrganizerTeamLoadResponse } from "../../lib/api";
import { pluralizeRu } from "../../lib/format";

export type DirectorRotation = NonNullable<OrganizerTeamLoadResponse["director_rotation"]>;

/**
 * Светофор ротации организаторов.
 *
 * Организатор — роль, выгорание в которой закрывает площадку целиком, поэтому
 * она вынесена из общей таблицы ролей отдельной карточкой. Стоит в двух местах:
 * на главной кабинета рядом со «Здоровьем локации» (просьба Дмитрия 03.09.2026)
 * и на странице «Команда и нагрузка» над таблицей ролей.
 */
export function DirectorRotationCard({ rotation }: { rotation: DirectorRotation }) {
  const label =
    rotation.level === "green"
      ? "ротация здоровая"
      : rotation.level === "yellow"
        ? "стоит подстраховаться"
        : "держится на одном человеке";
  const badge =
    rotation.level === "green"
      ? "org-badge org-badge-new"
      : rotation.level === "yellow"
        ? "org-badge org-badge-pb"
        : "org-badge org-badge-comeback";
  return (
    <section className="card org-director-card">
      <header className="org-table-head">
        <h2 className="section-title">
          <span className="org-table-emoji" aria-hidden="true">
            🚦
          </span>
          Ротация организаторов
        </h2>
        <span className={badge}>{label}</span>
      </header>
      <p className="org-director-line">
        За {rotation.months} месяцев старт вели{" "}
        <strong>{pluralizeRu(rotation.people, ["человек", "человека", "человек"])}</strong> на{" "}
        {pluralizeRu(rotation.slots, ["старт", "старта", "стартов"])}.
        {rotation.top_name && (
          <>
            {" "}
            Чаще всех — {rotation.top_name}: <strong>{rotation.top_share_pct}%</strong> стартов (
            {rotation.top_count}).
          </>
        )}
      </p>
      <p className="muted org-director-note">
        Здоровой считается ротация, где самый частый организатор ведёт не больше 40% стартов, а
        людей в роли хотя бы четверо — так живут две трети площадок страны. Больше 70% у одного
        человека или один организатор на всё — повод искать сменщиков.
      </p>
    </section>
  );
}
