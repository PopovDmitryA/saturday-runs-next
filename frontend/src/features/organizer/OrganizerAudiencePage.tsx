import { useEffect, useRef, useState } from "react";
import { RequireAuth } from "../../components/RequireAuth";
import {
  ApiError,
  getOrganizerAudience,
  type OrganizerAudienceResponse,
} from "../../lib/api";
import { formatInt } from "../../lib/format";
import { PORTAL_LOGIN_HREF } from "../../lib/portalRoutes";
import { locationHintFor } from "../../lib/locationHint";
import { TableWrap } from "../../components/tableUx/TableWrap";
import { useNarrowViewport } from "../../components/tableUx/useNarrowViewport";
import { PortalSectionShell } from "../portal/PortalSectionShell";
import { OrganizerBreadcrumbs } from "./OrganizerBreadcrumbs";
import { OrganizerDenied } from "./OrganizerDenied";
import "./organizer.css";

/** Портрет участника: возраст, пол и клубы тех, кто ходит на локацию. */
function OrganizerAudienceContent({ slug }: { slug: string }) {
  const [audience, setAudience] = useState<OrganizerAudienceResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [forbidden, setForbidden] = useState(false);
  const [notFound, setNotFound] = useState(false);
  // Клубы не должны быть выше пирамиды: список бывает вдвое длиннее, и колонки
  // разъезжались (правка Дмитрия 17.09.2026). Высоту соседа меряем и вешаем
  // на карточку клубов потолком — внутри неё таблица скроллится. На узком
  // экране карточки идут друг под другом, там резать нечего.
  const pyramidRef = useRef<HTMLElement | null>(null);
  const [pyramidHeight, setPyramidHeight] = useState<number | null>(null);
  const narrow = useNarrowViewport();

  useEffect(() => {
    let cancelled = false;
    getOrganizerAudience(slug)
      .then((payload) => {
        if (!cancelled) {
          setAudience(payload);
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
          setError(err instanceof Error ? err.message : "Не удалось загрузить портрет участника");
        }
      });
    return () => {
      cancelled = true;
    };
  }, [slug]);

  useEffect(() => {
    const node = pyramidRef.current;
    if (!node || typeof ResizeObserver === "undefined") {
      return;
    }
    const observer = new ResizeObserver(() => {
      setPyramidHeight(node.getBoundingClientRect().height);
    });
    observer.observe(node);
    return () => observer.disconnect();
  }, [audience]);

  const name = audience?.location.name ?? locationHintFor(slug)?.name ?? null;
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

  // Ширина столбиков пирамиды — от самой населённой ячейки, а не от суммы
  // строки: иначе редкие возрасты превращаются в невидимые полоски.
  const maxAgeCell = Math.max(
    1,
    ...(audience?.age_pyramid ?? []).flatMap((row) => [row.male_finishes, row.female_finishes]),
  );

  return (
    <PortalSectionShell sidebar={sidebar}>
      <header className="loc-header">
        <OrganizerBreadcrumbs slug={slug} locationName={name} tool="Портрет участника" />
        <div className="loc-header-title">
          <h1>{name ?? "Локация"} — портрет участника</h1>
        </div>
        <p className="muted">
          Кто к нам ходит: возраст, пол и клубы участников за последние 12 месяцев.
        </p>
      </header>

      {error && (
        <div className="card error">
          <p>{error}</p>
        </div>
      )}

      {!error && audience === null && <p className="muted">Загрузка…</p>}

      {!error && audience !== null && audience.finishes_total === 0 && (
        <div className="card">
          <p className="muted">За последний год финишей на локации не было.</p>
        </div>
      )}

      {!error && audience !== null && audience.finishes_total > 0 && (
        <>
          <section className="card org-toolbar-card">
            <div className="org-toolbar-row">
              <span className="muted">
                За 12 месяцев: {formatInt(audience.finishes_total)} финишей от{" "}
                {formatInt(audience.people_total)} разных участников
                {audience.genders.map((gender) => (
                  <span key={gender.label}>
                    {" "}
                    · {gender.label.toLowerCase()}: {gender.share_pct}%
                  </span>
                ))}
              </span>
            </div>
          </section>

          <div className="org-two-col">
            <section className="card org-table-card" ref={pyramidRef}>
              <header className="org-table-head">
                <h2 className="section-title">
                  <span className="org-table-emoji" aria-hidden="true">
                    🎂
                  </span>
                  Возраст и пол
                </h2>
                <span className="muted org-table-count">финишей за период</span>
              </header>
              {/* Пирамида: мужчины слева, женщины справа, между ними — годы.
                  Так две половины видно рядом; раньше категории «Ж35-39» и
                  «М35-39» стояли в одном алфавитном списке двумя блоками друг
                  под другом и не читались (заявка из бэклога сайта). */}
              <div className="org-pyramid">
                <div className="org-pyramid-head">
                  <span className="org-pyramid-head-male">Мужчины</span>
                  <span className="org-pyramid-head-age">лет</span>
                  <span className="org-pyramid-head-female">Женщины</span>
                </div>
                {audience.age_pyramid.map((row) => (
                  <div key={row.range} className="org-pyramid-row">
                    {/* Числа стоят у центральной оси, столбики растут от них
                        к краям: так подпись всегда рядом со своим столбиком, а
                        дорожки у всех строк одинаковой ширины и длины
                        сопоставимы. */}
                    <div
                      className="org-pyramid-side org-pyramid-side-male"
                      title={`Мужчины ${row.range}: ${formatInt(row.male_finishes)} финишей (${row.male_share_pct}%)`}
                    >
                      <div
                        className="org-pyramid-bar org-pyramid-bar-male"
                        style={{ width: `${(row.male_finishes / maxAgeCell) * 100}%` }}
                      />
                      <span className="org-pyramid-value">
                        {row.male_finishes ? formatInt(row.male_finishes) : ""}
                      </span>
                    </div>
                    <span className="org-pyramid-age">{row.range}</span>
                    <div
                      className="org-pyramid-side org-pyramid-side-female"
                      title={`Женщины ${row.range}: ${formatInt(row.female_finishes)} финишей (${row.female_share_pct}%)`}
                    >
                      <span className="org-pyramid-value">
                        {row.female_finishes ? formatInt(row.female_finishes) : ""}
                      </span>
                      <div
                        className="org-pyramid-bar org-pyramid-bar-female"
                        style={{ width: `${(row.female_finishes / maxAgeCell) * 100}%` }}
                      />
                    </div>
                  </div>
                ))}
              </div>
            </section>

            <section
              className="card org-table-card org-audience-clubs"
              style={!narrow && pyramidHeight ? { maxHeight: pyramidHeight } : undefined}
            >
              <header className="org-table-head">
                <h2 className="section-title">
                  <span className="org-table-emoji" aria-hidden="true">
                    👥
                  </span>
                  Клубы на локации
                </h2>
                <span className="muted org-table-count">по клубу в профиле участника</span>
              </header>
              {audience.clubs.length === 0 ? (
                <p className="muted">Участники с клубами в профиле пока не отметились.</p>
              ) : (
                <TableWrap>
                  <table className="data-table org-svod-table">
                    <thead>
                      <tr>
                        <th>Клуб</th>
                        <th>Людей</th>
                        <th>Финишей</th>
                      </tr>
                    </thead>
                    <tbody>
                      {audience.clubs.map((club) => (
                        <tr key={club.club}>
                          <td>{club.club}</td>
                          <td>{formatInt(club.people)}</td>
                          <td>{formatInt(club.finishes)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </TableWrap>
              )}
            </section>
          </div>

        </>
      )}
    </PortalSectionShell>
  );
}

export function OrganizerAudiencePage({ slug }: { slug: string }) {
  return (
    <RequireAuth loginHref={PORTAL_LOGIN_HREF}>
      {() => <OrganizerAudienceContent slug={slug} />}
    </RequireAuth>
  );
}
