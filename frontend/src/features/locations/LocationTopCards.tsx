/**
 * Карточки топов локации: по лучшему времени и по числу побед.
 *
 * Живут отдельным модулем, потому что показываются в двух местах: на странице
 * локации — пятёркой с ссылкой «весь топ», и на странице топов целиком.
 */

import { PlatformBadge } from "../../components/PlatformBadge";
import { StatHintTooltip } from "../../components/StatHintTooltip";
import { formatDate, formatInt, pluralFormRu } from "../../lib/format";
import type { LocationFastestRunner, LocationTopWinner } from "../../lib/api";

/** Имя участника; ссылка — только на профиль ВНУТРИ сайта. */
export function RunnerName({ name, handle }: { name: string | null; handle?: string | null }) {
  const label = name?.trim() || "—";
  if (!handle || label === "—") {
    return <>{label}</>;
  }
  return (
    <a className="loc-runner-link" href={`/users/${encodeURIComponent(handle)}`}>
      {label}
    </a>
  );
}

/** «00:18:08» → «18:08»: часовых времён на пятёрке не бывает. */
export function stripLeadingHours(display: string | null): string {
  if (!display) {
    return "—";
  }
  return display.replace(/^00:/, "");
}

export const TOP_TIME_HINT =
  "Один участник — одна строка: его лучшее время именно на этой локации. " +
  "Аккаунты человека в разных системах мы не склеиваем — это разные внешние " +
  "аккаунты, и у каждого своя строка со своей системой в колонке. В одну строку " +
  "сводятся только аккаунты, привязанные к единому профилю на сайте: тогда в " +
  "колонке стоят все системы, чьи протоколы учтены.";

function MoreLink({ href, label }: { href: string; label: string }) {
  return (
    <p className="loc-leaders-more">
      <a className="loc-events-link" href={href}>
        {label} →
      </a>
    </p>
  );
}

function SectionTitle({ title, hint }: { title: string; hint: string }) {
  return (
    <h2 className="section-title">
      {title}
      <StatHintTooltip text={hint}>
        <span className="loc-section-title-info" aria-label="Как считается">
          ⓘ
        </span>
      </StatHintTooltip>
    </h2>
  );
}

/** Топ по личному лучшему времени — одна строка на аккаунт участника. */
export function FastestRunnersCard({
  title,
  rows,
  narrowViewport,
  limit,
  moreHref,
  moreLabel,
}: {
  title: string;
  rows: LocationFastestRunner[];
  narrowViewport: boolean;
  /** Сколько строк показать. Без него — все, что приехали. */
  limit?: number;
  moreHref?: string;
  moreLabel?: string;
}) {
  if (rows.length === 0) {
    return null;
  }
  const shown = limit === undefined ? rows : rows.slice(0, limit);
  return (
    <section className="card loc-section">
      <SectionTitle title={title} hint={TOP_TIME_HINT} />
      {narrowViewport ? (
        <div className="rowcards loc-leaders-cards">
          {shown.map((row, index) => (
            <div className="rowcard" key={`${row.name}-${index}`}>
              <div className="rowcard-rank">{row.place}</div>
              <div className="rowcard-mid">
                <div className="rowcard-title">
                  <RunnerName name={row.name} handle={row.handle} />
                </div>
                <div className="rowcard-sub loc-top-card-sub">
                  {row.platform_codes.map((code) => (
                    <PlatformBadge key={code} code={code} />
                  ))}
                  {row.event_date ? formatDate(row.event_date) : "—"}
                </div>
              </div>
              <div className="rowcard-right">
                <div className="rowcard-value">{stripLeadingHours(row.best_time_display)}</div>
                <div className="rowcard-sub">лучшее</div>
              </div>
            </div>
          ))}
        </div>
      ) : (
        <table className="data-table loc-leaders-table">
          <colgroup>
            <col className="loc-leaders-col-rank" />
            <col />
            <col className="loc-leaders-col-system" />
            <col className="loc-leaders-col-time" />
            <col className="loc-leaders-col-date" />
          </colgroup>
          <thead>
            <tr>
              <th>#</th>
              <th>Участник</th>
              <th title="Системы, чьи протоколы учтены в строке">Система</th>
              <th title="Лучшее время участника здесь">Время</th>
              <th title="Когда показано это время">Дата</th>
            </tr>
          </thead>
          <tbody>
            {shown.map((row, index) => (
              <tr key={`${row.name}-${index}`}>
                <td className="loc-leaders-rank">{row.place}</td>
                <td>
                  <RunnerName name={row.name} handle={row.handle} />
                </td>
                <td>
                  {row.platform_codes.length === 0 ? (
                    "—"
                  ) : (
                    <div className="loc-top-systems">
                      {row.platform_codes.map((code) => (
                        <PlatformBadge key={code} code={code} />
                      ))}
                    </div>
                  )}
                </td>
                <td>{stripLeadingHours(row.best_time_display)}</td>
                <td>{row.event_date ? formatDate(row.event_date) : "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {moreHref && moreLabel && rows.length > shown.length && (
        <MoreLink href={moreHref} label={moreLabel} />
      )}
    </section>
  );
}

/** Топ по числу побед на локации. */
export function TopWinnersCard({
  title,
  hint,
  rows,
  narrowViewport,
  limit,
  moreHref,
  moreLabel,
}: {
  title: string;
  hint: string;
  rows: LocationTopWinner[];
  narrowViewport: boolean;
  limit?: number;
  moreHref?: string;
  moreLabel?: string;
}) {
  if (rows.length === 0) {
    return null;
  }
  const shown = limit === undefined ? rows : rows.slice(0, limit);
  return (
    <section className="card loc-section">
      <SectionTitle title={title} hint={hint} />
      {narrowViewport ? (
        <div className="rowcards loc-leaders-cards">
          {shown.map((row, index) => (
            <div className="rowcard" key={`${row.name}-${index}`}>
              <div className="rowcard-rank">{row.place}</div>
              <div className="rowcard-mid">
                <div className="rowcard-title">
                  <RunnerName name={row.name} handle={row.handle} />
                </div>
                <div className="rowcard-sub loc-top-card-sub">
                  {row.platform_codes.map((code) => (
                    <PlatformBadge key={code} code={code} />
                  ))}
                  {row.last_win_date ? `последняя — ${formatDate(row.last_win_date)}` : "—"}
                </div>
              </div>
              <div className="rowcard-right">
                <div className="rowcard-value">{formatInt(row.wins_count)}</div>
                <div className="rowcard-sub">
                  {pluralFormRu(row.wins_count, ["победа", "победы", "побед"])}
                </div>
              </div>
            </div>
          ))}
        </div>
      ) : (
        <table className="data-table loc-leaders-table">
          <colgroup>
            <col className="loc-leaders-col-rank" />
            <col />
            <col className="loc-leaders-col-system" />
            <col className="loc-leaders-col-count" />
            <col className="loc-leaders-col-date" />
          </colgroup>
          <thead>
            <tr>
              <th>#</th>
              <th>Участник</th>
              <th title="Система, в протоколах которой засчитаны победы">Система</th>
              <th title="Сколько раз финишировал здесь первым">Побед</th>
              <th title="Дата последней победы">Последняя</th>
            </tr>
          </thead>
          <tbody>
            {shown.map((row, index) => (
              <tr key={`${row.name}-${index}`}>
                <td className="loc-leaders-rank">{row.place}</td>
                <td>
                  <RunnerName name={row.name} handle={row.handle} />
                </td>
                <td>
                  {row.platform_codes.length === 0 ? (
                    "—"
                  ) : (
                    <div className="loc-top-systems">
                      {row.platform_codes.map((code) => (
                        <PlatformBadge key={code} code={code} />
                      ))}
                    </div>
                  )}
                </td>
                <td>{formatInt(row.wins_count)}</td>
                <td>{row.last_win_date ? formatDate(row.last_win_date) : "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {moreHref && moreLabel && rows.length > shown.length && (
        <MoreLink href={moreHref} label={moreLabel} />
      )}
    </section>
  );
}

export const WINS_OVERALL_HINT =
  "Победа — лучшее время старта в абсолютном зачёте. Учтены все старты локации " +
  "во всех системах, где она работала; система победы — в колонке. Если время у " +
  "двоих совпало, победа засчитана обоим. Старт, который выиграл участник без " +
  "штрихкода, победы не приносит никому.";

export const WINS_FEMALE_HINT =
  "Победа — лучшее время старта среди женщин. Учтены все старты локации во всех " +
  "системах, где она работала; система победы — в колонке. Если время у двоих " +
  "совпало, победа засчитана обеим.";
