import { useEffect, useMemo, useState } from "react";
import { FilterSelect } from "../../components/filters/FilterPanel";
import { RequireAuth } from "../../components/RequireAuth";
import {
  ApiError,
  getOrganizerProtocols,
  type OrganizerProtocolItem,
  type OrganizerProtocolsResponse,
} from "../../lib/api";
import { formatInt, pluralizeRu } from "../../lib/format";
import { PORTAL_LOGIN_HREF } from "../../lib/portalRoutes";
import { locationHintFor } from "../../lib/locationHint";
import { TableWrap } from "../../components/tableUx/TableWrap";
import { useFloatingTableHead } from "../../lib/useFloatingTableHead";
import { HeaderHint } from "../../components/tableUx/HeaderHint";
import { PortalSectionShell } from "../portal/PortalSectionShell";
import { OrganizerBreadcrumbs } from "./OrganizerBreadcrumbs";
import { OrganizerDenied } from "./OrganizerDenied";
import "./organizer.css";

// Светофор скорости протокола: пороги согласованы 23.08.2026. Число всегда
// с одним знаком («5.0 ч», не «5 ч») и фиксированной ширины — иначе бейджи
// по строкам гуляют и колонка выглядит неопрятно.
function DelayBadge({ item }: { item: OrganizerProtocolItem }) {
  if (item.delay_hours == null || item.level == null) {
    return <span className="muted">—</span>;
  }
  const label =
    item.level === "green" ? "быстро" : item.level === "yellow" ? "день в день" : "с опозданием";
  const cls =
    item.level === "green"
      ? "org-badge org-badge-new"
      : item.level === "yellow"
        ? "org-badge org-badge-pb"
        : "org-badge org-badge-comeback";
  return (
    <span className="org-delay-cell">
      <span className="org-delay-num">{item.delay_hours.toFixed(1)} ч</span>
      <span className={cls} title="Задержка от финиша последнего участника до появления протокола">
        {label}
      </span>
    </span>
  );
}

function revisionSummary(item: OrganizerProtocolItem): string {
  const parts: string[] = [];
  for (const revision of item.revisions) {
    const d = revision.details;
    const chunk: string[] = [];
    if (d.added) {
      chunk.push(`строк добавлено: ${d.added}`);
    }
    if (d.removed) {
      chunk.push(`убрано: ${d.removed}`);
    }
    if (d.time_changes_total) {
      chunk.push(`времён изменено: ${d.time_changes_total}`);
    }
    if (d.position_changes) {
      chunk.push(`позиций сдвинуто: ${d.position_changes}`);
    }
    const when = revision.detected_at.slice(0, 16).replace("T", " ");
    parts.push(`${when}: ${chunk.join(", ") || revision.kind}`);
  }
  return parts.join("\n");
}

function OrganizerProtocolsContent({ slug }: { slug: string }) {
  const attachFloatingHead = useFloatingTableHead();
  const [data, setData] = useState<OrganizerProtocolsResponse | null>(null);
  // Период сводки «по организаторам»: месяцы, 0 — всё время.
  const [directorMonths, setDirectorMonths] = useState(12);
  const [error, setError] = useState<string | null>(null);
  const [forbidden, setForbidden] = useState(false);
  const [notFound, setNotFound] = useState(false);

  useEffect(() => {
    let cancelled = false;
    getOrganizerProtocols(slug)
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
          setError(err instanceof Error ? err.message : "Не удалось загрузить протоколы");
        }
      });
    return () => {
      cancelled = true;
    };
  }, [slug]);

  const directorStats = useMemo(() => {
    if (!data) {
      return [];
    }
    const cutoff = directorMonths
      ? (() => {
          const edge = new Date();
          edge.setMonth(edge.getMonth() - directorMonths);
          return edge.toISOString().slice(0, 10);
        })()
      : "";
    const acc = new Map<string, { sum: number; count: number }>();
    for (const item of data.items) {
      if (item.delay_hours == null || item.date < cutoff) {
        continue;
      }
      for (const director of item.directors) {
        const entry = acc.get(director) ?? { sum: 0, count: 0 };
        entry.sum += item.delay_hours;
        entry.count += 1;
        acc.set(director, entry);
      }
    }
    return [...acc.entries()]
      .map(([director, entry]) => ({
        director,
        events: entry.count,
        avg: entry.sum / entry.count,
      }))
      .sort((a, b) => a.avg - b.avg);
  }, [data, directorMonths]);

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
        <OrganizerBreadcrumbs slug={slug} locationName={name} tool="Протоколы" />
        <div className="loc-header-title">
          <h1>{name ?? "Локация"} — протоколы</h1>
        </div>
        <p className="muted">
          Как быстро протокол появляется на 5 вёрст: задержка считается от финиша последнего
          участника до момента, когда протокол впервые замечен на сайте.
        </p>
      </header>

      {error && (
        <div className="card error">
          <p>{error}</p>
        </div>
      )}

      {!error && data === null && <p className="muted">Загрузка…</p>}

      {!error && data !== null && !data.supported && (
        <div className="card">
          <p className="muted">
            Наблюдение за выгрузкой пока работает только для стартов 5 вёрст — у этой локации их
            нет.
          </p>
        </div>
      )}

      {!error && data !== null && data.supported && (
        <>
          <section className="card org-toolbar-card">
            <div className="org-toolbar-row">
              <span className="muted">
                {data.median_delay_hours_12m != null && (
                  <>
                    Медиана за 12 месяцев: <strong>{data.median_delay_hours_12m} ч</strong>
                  </>
                )}
                {/* Место показываем только из топ-50: рейтинг «вы 131-е из 196»
                    никого не мотивирует, а расстраивает (решение Дмитрия). */}
                {data.network_rank != null && data.network_size != null && data.network_rank <= 50 && (
                  <>
                    {" "}
                    · по скорости выгрузки —{" "}
                    <strong>
                      {formatInt(data.network_rank)}-е место из {formatInt(data.network_size)}
                    </strong>{" "}
                    локаций 5 вёрст
                  </>
                )}
                {data.median_delay_hours_12m == null && "Фактов выгрузки пока не накопилось."}
              </span>
            </div>
          </section>

          <section className="card org-table-card">
            <header className="org-table-head">
              <h2 className="section-title">
                <span className="org-table-emoji" aria-hidden="true">
                  ⏱
                </span>
                Выгрузка по стартам
              </h2>
              <span className="muted org-table-count">
                {pluralizeRu(data.items.length, ["старт", "старта", "стартов"])}
                {data.tz_offset_moscow !== 0 && (
                  <> · время местное (МСК{data.tz_offset_moscow > 0 ? "+" : ""}{data.tz_offset_moscow})</>
                )}
              </span>
            </header>
            <TableWrap stickyFirstCol innerRef={attachFloatingHead}>
              <table className="data-table org-svod-table">
                <thead>
                  <tr>
                    <th>Дата</th>
                    <th>№</th>
                    <th>
                      Старт
                      <HeaderHint text="Время старта из описания локации, с учётом сезонного расписания" />
                    </th>
                    <th>Участников</th>
                    <th>
                      Финиш последнего
                      <HeaderHint text="Время финиша последнего участника — от него считается задержка выгрузки" />
                    </th>
                    <th>
                      Протокол появился
                      <HeaderHint text="Когда протокол впервые замечен на 5verst.ru (местное время локации). Точность — минута" />
                    </th>
                    <th>
                      Задержка
                      <HeaderHint text="От финиша последнего участника до появления протокола. Зелёное — до 3 часов, жёлтое — день в день, красное — на следующий день и позже" />
                    </th>
                    <th>
                      Организатор дня
                      <HeaderHint text="Кто был организатором на этом старте — по протоколу волонтёрств" />
                    </th>
                    <th>
                      Правки
                      <HeaderHint text="Сколько раз протокол менялся после первой загрузки. Замена «Неизвестного» на имя правкой не считается" />
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {data.items.map((item) => (
                    <tr key={item.date}>
                      <td>{item.date_display}</td>
                      <td>{item.event_number ?? "—"}</td>
                      <td>{item.start_time ?? "—"}</td>
                      <td>{item.finishers > 0 ? formatInt(item.finishers) : "—"}</td>
                      <td>{item.last_finish_display ?? "—"}</td>
                      {/* Прочерк, а не «не замечен»: за момент выгрузки мы
                          ручаемся, только если сами видели, как протокол
                          появился. Не видели — значит не знаем, и говорить
                          что-либо о скорости организатора не вправе. */}
                      <td>{item.first_seen_display ?? <span className="muted">—</span>}</td>
                      <td>
                        <DelayBadge item={item} />
                      </td>
                      <td>{item.directors.length > 0 ? item.directors.join(", ") : "—"}</td>
                      <td>
                        {item.revisions.length > 0 ? (
                          <span
                            className="org-badge org-badge-jubilee"
                            title={revisionSummary(item)}
                          >
                            {formatInt(item.revisions.length)}
                          </span>
                        ) : (
                          "—"
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </TableWrap>
          </section>

          {directorStats.length > 0 && (
            <section className="card org-table-card">
              <header className="org-table-head">
                <h2 className="section-title">
                  <span className="org-table-emoji" aria-hidden="true">
                    🪇
                  </span>
                  По организаторам
                </h2>
                <label className="muted org-period-label">
                  Период:{" "}
                  <FilterSelect
                    ariaLabel="Период"
                    title="За какой период считаем среднее"
                    value={directorMonths}
                    onChange={setDirectorMonths}
                    options={[
                      { value: 6, label: "полгода" },
                      { value: 12, label: "год" },
                      { value: 0, label: "всё время" },
                    ]}
                  />
                </label>
              </header>
              {/* Рамка этики — как на волонтёрской скамейке: метрика справочная,
                  а не кнут (требование Дмитрия 24.08.2026). */}
              <div className="org-notice">
                <span className="org-notice-icon" aria-hidden="true">
                  🤝
                </span>
                <div className="org-notice-text">
                  Время публикации протокола не всегда зависит от организатора: бывает связь,
                  техника, погода и просто жизнь. Это справочная метрика — просим не использовать
                  её для претензий к человеку.
                </div>
              </div>
              <TableWrap>
                <table className="data-table org-svod-table">
                  <thead>
                    <tr>
                      <th>Организатор</th>
                      <th>
                        Стартов
                        <HeaderHint text="Сколько стартов с известной задержкой публикации человек провёл организатором за период" />
                      </th>
                      <th>
                        Средняя публикация
                        <HeaderHint text="Среднее время от финиша последнего участника до появления протокола на его стартах" />
                      </th>
                    </tr>
                  </thead>
                  <tbody>
                    {directorStats.map((row) => (
                      <tr key={row.director}>
                        <td>{row.director}</td>
                        <td>{formatInt(row.events)}</td>
                        <td>
                          <span className="org-delay-num">{row.avg.toFixed(1)} ч</span>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </TableWrap>
            </section>
          )}
        </>
      )}
    </PortalSectionShell>
  );
}

export function OrganizerProtocolsPage({ slug }: { slug: string }) {
  return (
    <RequireAuth loginHref={PORTAL_LOGIN_HREF}>
      {() => <OrganizerProtocolsContent slug={slug} />}
    </RequireAuth>
  );
}
