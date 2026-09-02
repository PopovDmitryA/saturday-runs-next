import { useEffect, useMemo, useState } from "react";
import { RequireAuth } from "../../components/RequireAuth";
import {
  ApiError,
  getOrganizerTeamLoad,
  type OrganizerTeamLoadResponse,
  type OrganizerTeamRole,
} from "../../lib/api";
import { formatInt, pluralizeRu } from "../../lib/format";
import { PORTAL_LOGIN_HREF } from "../../lib/portalRoutes";
import { locationHintFor } from "../../lib/locationHint";
import { TableWrap } from "../../components/tableUx/TableWrap";
import { useFloatingTableHead } from "../../lib/useFloatingTableHead";
import { HeaderHint } from "../../components/tableUx/HeaderHint";
import { ColumnHeader } from "../../components/activityTable/ColumnHeader";
import { PortalSectionShell } from "../portal/PortalSectionShell";
import { OrganizerBreadcrumbs } from "./OrganizerBreadcrumbs";
import { OrganizerDenied } from "./OrganizerDenied";
import "./organizer.css";

// Насколько роль устойчива: bus-фактор — сколько человек закрывают
// 80% волонтёрств в роли. Слово «смена» в проекте запрещено (18.08.2026).
function BusBadge({ role }: { role: OrganizerTeamRole }) {
  if (role.bus_factor <= 1) {
    return (
      <span
        className="org-badge org-badge-comeback"
        title="80% волонтёрств в роли закрывает один человек"
      >
        держится на одном
      </span>
    );
  }
  if (role.bus_factor <= 3 && role.is_critical) {
    return (
      <span
        className="org-badge org-badge-pb"
        title={`80% волонтёрств в роли закрывают ${role.bus_factor} человека`}
      >
        узкое место
      </span>
    );
  }
  return (
    <span
      className="org-badge org-badge-new"
      title={`80% волонтёрств в роли закрывают ${role.bus_factor} чел.`}
    >
      устойчива
    </span>
  );
}

function RotationCell({ role }: { role: OrganizerTeamRole }) {
  if (role.network_rotation_pct == null) {
    return <span className="muted">{role.rotation_pct}%</span>;
  }
  // Красим наш процент по ОТНОШЕНИЮ к системе (не по абсолютной дельте):
  // не хуже системы — зелёный, в пределах −15% от неё — жёлтый, ниже — красный.
  const ratio = role.network_rotation_pct > 0 ? role.rotation_pct / role.network_rotation_pct : 1;
  const cls = ratio >= 1 ? "org-delta-good" : ratio >= 0.85 ? "org-delta-mid" : "org-delta-bad";
  return (
    <span>
      <strong className={cls}>{role.rotation_pct}%</strong>{" "}
      {/* Что за число в скобках — сказано в подсказке колонки; повторять
          «в системе» в каждой строке значит шуметь (Дмитрий 02.09.2026). */}
      <span className="muted">({role.network_rotation_pct}%)</span>
    </span>
  );
}

type TeamSortKey = "stability" | "bus" | "people" | "slots" | "rotation";
type TeamSortState = { key: TeamSortKey; asc: boolean };

function teamSortValue(role: OrganizerTeamRole, key: TeamSortKey): number {
  switch (key) {
    case "stability":
    case "bus":
      return role.bus_factor;
    case "people":
      return role.people;
    case "slots":
      return role.slots;
    case "rotation":
      return role.rotation_pct;
  }
}

function DirectorRotationCard({
  rotation,
}: {
  rotation: NonNullable<OrganizerTeamLoadResponse["director_rotation"]>;
}) {
  // Организатор — роль, выгорание в которой закрывает площадку целиком,
  // поэтому она вынесена из общей таблицы отдельным светофором.
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
            Чаще всех — {rotation.top_name}: <strong>{rotation.top_share_pct}%</strong> стартов
            ({rotation.top_count}).
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

function OrganizerTeamContent({ slug }: { slug: string }) {
  const attachRolesHead = useFloatingTableHead();
  const attachLoadHead = useFloatingTableHead();
  const [data, setData] = useState<OrganizerTeamLoadResponse | null>(null);
  // null — серверный порядок «ключевые роли с наибольшим риском первыми».
  const [sort, setSort] = useState<TeamSortState | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [forbidden, setForbidden] = useState(false);
  const [notFound, setNotFound] = useState(false);
  // «Только чистые смены»: не засчитывать дни, когда человек в этот же день
  // где-то бежал — здесь или на соседней площадке.
  const [pureOnly, setPureOnly] = useState(false);

  // В этом режиме и число, и доля, и порядок считаются по чистым сменам:
  // иначе список остался бы отсортированным по общему счёту, и первым стоял бы
  // тот, кто почти всегда совмещал волонтёрство с пробежкой.
  const loadRows = useMemo(() => {
    const rows = data?.top_load ?? [];
    if (!pureOnly) {
      return rows;
    }
    const total = rows.reduce((sum, person) => sum + (person.pure_slots ?? person.slots), 0);
    return [...rows]
      .map((person) => {
        const value = person.pure_slots ?? person.slots;
        return { ...person, share_pct: total ? Math.round((value / total) * 100) : 0 };
      })
      .filter((person) => (person.pure_slots ?? person.slots) > 0)
      .sort((a, b) => (b.pure_slots ?? b.slots) - (a.pure_slots ?? a.slots));
  }, [data, pureOnly]);

  useEffect(() => {
    let cancelled = false;
    getOrganizerTeamLoad(slug)
      .then((payload) => {
        if (!cancelled) {
          setData(payload);
        }
      })
      .catch((err) => {
        if (cancelled) {
          return;
        }
        if (err instanceof ApiError && err.status === 403) {
          setForbidden(true);
        } else if (err instanceof ApiError && err.status === 404) {
          setNotFound(true);
        } else {
          setError(err instanceof Error ? err.message : "Не удалось загрузить данные команды");
        }
      });
    return () => {
      cancelled = true;
    };
  }, [slug]);

  const roles = useMemo(() => {
    const items = [...(data?.roles ?? [])];
    if (sort) {
      items.sort((a, b) => {
        const left = teamSortValue(a, sort.key);
        const right = teamSortValue(b, sort.key);
        if (left === right) {
          return a.role < b.role ? -1 : 1;
        }
        return sort.asc ? left - right : right - left;
      });
    }
    return items;
  }, [data, sort]);

  const toggleSort = (key: TeamSortKey) => {
    setSort((current) =>
      current && current.key === key ? { key, asc: !current.asc } : { key, asc: true },
    );
  };

  const sortProps = (key: TeamSortKey) => ({
    filterable: false,
    sortActive: sort?.key === key,
    sortAsc: sort?.asc ?? false,
    onSort: () => toggleSort(key),
  });

  const name = data?.location.name ?? locationHintFor(slug)?.name ?? null;
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

  return (
    <PortalSectionShell sidebar={sidebar}>
      <header className="loc-header">
        <OrganizerBreadcrumbs slug={slug} locationName={name} tool="Команда и нагрузка" />
        <div className="loc-header-title">
          <h1>{name ?? "Локация"} — команда и нагрузка</h1>
        </div>
        <p className="muted">
          Насколько устойчива оргкоманда: какие ключевые роли держатся на одном-двух людях и кто
          тянет непропорционально много. Данные за последние 12 месяцев.
        </p>
      </header>

      {error && (
        <div className="card error">
          <p>{error}</p>
        </div>
      )}

      {!error && data === null && <p className="muted">Загрузка…</p>}

      {!error && data !== null && data.roles.length === 0 && (
        <div className="card">
          <p className="muted">За последний год волонтёрств на локации не зафиксировано.</p>
        </div>
      )}

      {!error && data !== null && data.roles.length > 0 && (
        <>
          <section className="card org-toolbar-card">
            <div className="org-toolbar-row">
              <span className="muted">
                За 12 месяцев: {pluralizeRu(data.events_total, ["старт", "старта", "стартов"])} ·{" "}
                {pluralizeRu(data.volunteers_total, ["волонтёр закрыл", "волонтёра закрыли", "волонтёров закрыли"])}{" "}
                {pluralizeRu(data.slots_total, ["волонтёрство", "волонтёрства", "волонтёрств"])}
                {data.avg_per_event != null && (
                  <> · в среднем {data.avg_per_event} волонтёров на старт</>
                )}
                . «Ротация» — доля разных людей среди волонтёрств роли: чем выше, тем меньше риск
                выгорания. Рядом с нашей ротацией — средняя по системе для той же роли.
              </span>
            </div>
          </section>

          {data.director_rotation && (
            <DirectorRotationCard rotation={data.director_rotation} />
          )}

          <section className="card org-table-card">
            <header className="org-table-head">
              <h2 className="section-title">
                <span className="org-table-emoji" aria-hidden="true">
                  🧯
                </span>
                Устойчивость ролей
              </h2>
              <span className="muted org-table-count">
                наверху — ключевые роли с наибольшим риском
              </span>
            </header>
            <TableWrap stickyFirstCol innerRef={attachRolesHead}>
              <table className="data-table org-svod-table">
                <thead>
                  <tr>
                    <th>
                      Роль
                      <HeaderHint text="Ключом 🔑 отмечены роли, без которых старт не состоится" />
                    </th>
                    <ColumnHeader
                      label="Устойчивость"
                      hint="Оценка по bus-фактору: «держится на одном» — всё на одном человеке; «узкое место» — ключевая роль на 2–3 людях; «устойчива» — закрывающих достаточно"
                      {...sortProps("stability")}
                    />
                    <ColumnHeader
                      label="Bus-фактор"
                      hint="Считаем так: людей и роли выстраиваем по числу выходов за год и смотрим, сколько самых активных ВМЕСТЕ закрывают 80% всех выходов. Пример: секундомер выходил 50 раз, из них 42 раза — один и тот же человек. 42 из 50 — это больше 80%, значит bus-фактор 1: не придёт он — роль закрывать некому"
                      {...sortProps("bus")}
                    />
                    <ColumnHeader
                      label="Людей"
                      hint="Сколько разных людей хотя бы раз выходили в этой роли за год"
                      {...sortProps("people")}
                    />
                    <ColumnHeader
                      label="Волонтёрств"
                      hint="Сколько раз за год эту роль кто-то закрыл. Прошло 53 старта, и на каждом стоял секундомер — в столбце будет 53; кто именно стоял, не важно. «Людей» отвечает, КТО закрывал роль, «Волонтёрств» — СКОЛЬКО РАЗ её закрывали, а из их отношения считается ротация"
                      {...sortProps("slots")}
                    />
                    <th>
                      Главная опора
                      <HeaderHint text="Кто выходит в этой роли чаще всех — и какая доля волонтёрств роли на нём" />
                    </th>
                    <ColumnHeader
                      label="Ротация"
                      hint="Разных людей ÷ волонтёрств в роли. 100% — каждый раз новый человек, низкий процент — одни и те же. Рядом — средняя ротация этой же роли по системе; цвет — как наша локация смотрится на её фоне"
                      {...sortProps("rotation")}
                    />
                  </tr>
                </thead>
                <tbody>
                  {roles.map((role) => (
                    <tr key={role.role_key}>
                      <td>
                        {role.role}
                        {role.is_critical && (
                          <span title="Ключевая роль: без неё старт не состоится"> 🔑</span>
                        )}
                      </td>
                      <td>
                        <BusBadge role={role} />
                      </td>
                      <td>{formatInt(role.bus_factor)}</td>
                      <td>{formatInt(role.people)}</td>
                      <td>{formatInt(role.slots)}</td>
                      <td>
                        {role.top_name ?? "—"}
                        <span className="muted"> · {role.top_share_pct}% волонтёрств</span>
                      </td>
                      <td>
                        <RotationCell role={role} />
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </TableWrap>
          </section>

          <section className="card org-table-card">
            <header className="org-table-head">
              <h2 className="section-title">
                <span className="org-table-emoji" aria-hidden="true">
                  🏋️
                </span>
                Кто тянет больше всех
              </h2>
              <span className="muted org-table-count">
                топ по числу волонтёрств за год — этим людям особенно нужны подмена и отдых
              </span>
            </header>
            <div className="org-toolbar-row org-load-controls">
              <label className="org-toolbar-label org-toolbar-checkbox">
                <input
                  type="checkbox"
                  checked={pureOnly}
                  onChange={(event) => setPureOnly(event.target.checked)}
                />{" "}
                Только чистые смены
              </label>
              <span className="muted">
                Не засчитывать дни, когда человек в этот же день ещё и бежал — здесь или на
                любой другой площадке.
              </span>
            </div>
            <TableWrap innerRef={attachLoadHead}>
              <table className="data-table org-svod-table">
                <thead>
                  <tr>
                    <th>Имя</th>
                    <th>
                      Волонтёрств за год
                      <HeaderHint text="Сколько раз человек выходил волонтёрить на этой локации за последние 12 месяцев, во всех ролях" />
                    </th>
                    <th>
                      Пробежек за год
                      <HeaderHint text="Сколько раз человек бегал на этой локации за тот же период — видно, только помогает он или ещё и бегает" />
                    </th>
                    <th>
                      Доля нагрузки
                      <HeaderHint text="Какая часть всех волонтёрств локации за год приходится на этого человека" />
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {loadRows.map((person) => (
                    <tr key={person.participant_id}>
                      <td>
                        {person.profile_url ? (
                          <a href={person.profile_url} target="_blank" rel="noreferrer">
                            {person.name ?? "—"}
                          </a>
                        ) : (
                          person.name ?? "—"
                        )}
                      </td>
                      <td>{formatInt(pureOnly ? (person.pure_slots ?? person.slots) : person.slots)}</td>
                      <td>{formatInt(person.runs_here ?? 0)}</td>
                      <td>
                        <div className="org-bar-row">
                          <div
                            className="org-bar"
                            style={{ width: `${Math.min(person.share_pct * 5, 100)}%` }}
                          />
                          <span>{person.share_pct}%</span>
                        </div>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </TableWrap>
          </section>
        </>
      )}
    </PortalSectionShell>
  );
}

export function OrganizerTeamPage({ slug }: { slug: string }) {
  return (
    <RequireAuth loginHref={PORTAL_LOGIN_HREF}>
      {() => <OrganizerTeamContent slug={slug} />}
    </RequireAuth>
  );
}
