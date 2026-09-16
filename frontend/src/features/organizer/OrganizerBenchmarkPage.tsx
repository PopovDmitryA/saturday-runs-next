import { useEffect, useMemo, useRef, useState } from "react";
import { FilterSelect } from "../../components/filters/FilterPanel";
import { RequireAuth } from "../../components/RequireAuth";
import {
  ApiError,
  getLocationsIndex,
  getOrganizerBenchmark,
  type LocationIndexItem,
  type OrganizerBenchmarkResponse,
} from "../../lib/api";
import { formatFinishTimeValue, formatInt, formatNumber, platformCodeLabel } from "../../lib/format";
import { PORTAL_LOGIN_HREF } from "../../lib/portalRoutes";
import { locationHintFor } from "../../lib/locationHint";
import { TableWrap } from "../../components/tableUx/TableWrap";
import { PortalSectionShell } from "../portal/PortalSectionShell";
import { OrganizerBreadcrumbs } from "./OrganizerBreadcrumbs";
import { OrganizerDenied } from "./OrganizerDenied";
import "./organizer.css";

// «Одна локация» — последней: это точечное сравнение, а первые четыре отвечают
// на вопрос «как мы на общем фоне».
const SCOPES = [
  { key: "city", label: "Город" },
  { key: "region", label: "Регион" },
  { key: "nearest", label: "3 ближайшие" },
  { key: "network", label: "Вся система" },
  { key: "location", label: "Одна локация" },
];

// Период сравнения. 0 — текущий календарный год; «всё время» — 10 лет:
// старше данных в системе нет.
const PERIOD_OPTIONS = [
  { months: 0, label: "текущий год" },
  { months: 6, label: "полгода" },
  { months: 12, label: "12 месяцев" },
  { months: 120, label: "всё время" },
];

const SUGGEST_LIMIT = 12;

type BenchmarkMetric = OrganizerBenchmarkResponse["metrics"][number];

/** Время финиша показываем временем, остальное — числом с разрядами. */
function metricValue(metric: BenchmarkMetric, value: number | null): string {
  if (value === null) {
    return "—";
  }
  if (metric.key === "avg_finish_time_sec") {
    return formatFinishTimeValue(null, Math.round(value)).replace(/^00:/, "");
  }
  if (metric.key === "protocol_delay_hours") {
    // Часы с десятыми: «1,8 ч» читается лучше, чем 108 минут.
    return `${formatNumber(Math.round(value * 10) / 10)} ч`;
  }
  return formatNumber(value);
}

/**
 * Цвет процента: хорошо — когда отклонение идёт в сторону «лучше». Отставание
 * красим не красным, а тем же приглушённым оранжевым, что и остальные дельты
 * кабинета (`org-delta-mid`): это не ошибка, а «ниже середины». У метрик-
 * профилей (доля женщин) цвета нет вовсе.
 */
function deltaClass(metric: BenchmarkMetric, delta: number): string {
  if (metric.higher_is_better === null) {
    return "org-delta";
  }
  return delta >= 0 === metric.higher_is_better ? "org-delta-good" : "org-delta-mid";
}

/**
 * Скобки — внутри окрашенного span: снаружи они оставались базового кегля,
 * процент рядом набирался мельче, и цифра «наезжала» на скобку (правка
 * Дмитрия 14.09.2026).
 */
function DeltaPct({
  metric,
  delta,
  parens = false,
}: {
  metric: BenchmarkMetric;
  delta: number;
  parens?: boolean;
}) {
  return (
    <span className={deltaClass(metric, delta)}>
      {parens ? "(" : ""}
      {delta > 0 ? "+" : ""}
      {delta}%
      {parens ? ")" : ""}
    </span>
  );
}

/** Метрики идут разделами: явка, поле, новые лица, команда. */
function groupMetrics(metrics: BenchmarkMetric[]): { group: string; rows: BenchmarkMetric[] }[] {
  const groups: { group: string; rows: BenchmarkMetric[] }[] = [];
  for (const metric of metrics) {
    const last = groups[groups.length - 1];
    if (last && last.group === metric.group) {
      last.rows.push(metric);
    } else {
      groups.push({ group: metric.group, rows: [metric] });
    }
  }
  return groups;
}

/** Поиск площадки для сравнения: ввод по названию, подсказки из каталога. */
function PeerPicker({
  ownSlug,
  peer,
  onPick,
}: {
  ownSlug: string;
  peer: LocationIndexItem | null;
  onPick: (item: LocationIndexItem | null) => void;
}) {
  const [items, setItems] = useState<LocationIndexItem[] | null>(null);
  const [query, setQuery] = useState("");
  const [open, setOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    let cancelled = false;
    getLocationsIndex()
      .then((payload) => {
        if (!cancelled) {
          setItems(payload.items);
        }
      })
      .catch(() => {
        if (!cancelled) {
          setItems([]);
        }
      });
    return () => {
      cancelled = true;
    };
  }, []);

  // Клик мимо закрывает подсказки — иначе список висит поверх таблицы.
  useEffect(() => {
    if (!open) {
      return;
    }
    const onPointerDown = (event: MouseEvent) => {
      if (!rootRef.current?.contains(event.target as Node)) {
        setOpen(false);
      }
    };
    document.addEventListener("mousedown", onPointerDown);
    return () => document.removeEventListener("mousedown", onPointerDown);
  }, [open]);

  const suggestions = useMemo(() => {
    const normalized = query.trim().toLowerCase();
    if (!items || normalized.length < 2) {
      return [];
    }
    return items
      .filter((item) => item.slug !== ownSlug)
      .filter((item) =>
        [item.name, item.city, item.region]
          .filter(Boolean)
          .join(" ")
          .toLowerCase()
          .includes(normalized),
      )
      .slice(0, SUGGEST_LIMIT);
  }, [items, query, ownSlug]);

  return (
    <div className="org-peer-picker" ref={rootRef}>
      <input
        className="fp-search org-peer-input"
        type="search"
        value={peer ? peer.name : query}
        placeholder={items === null ? "Загружаем каталог…" : "Название локации или город"}
        aria-label="Локация для сравнения"
        disabled={items === null}
        onFocus={() => setOpen(true)}
        onChange={(event) => {
          if (peer) {
            onPick(null);
          }
          setQuery(event.target.value);
          setOpen(true);
        }}
      />
      {open && suggestions.length > 0 && (
        <ul className="org-peer-list">
          {suggestions.map((item) => (
            <li key={item.identity_key}>
              <button
                type="button"
                className="org-peer-option"
                onClick={() => {
                  onPick(item);
                  setQuery("");
                  setOpen(false);
                }}
              >
                <span className="org-peer-name">{item.name}</span>
                <span className="muted org-peer-meta">
                  {[item.city, item.platform_codes.map(platformCodeLabel).join(" · ")]
                    .filter(Boolean)
                    .join(" · ")}
                </span>
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

/**
 * «Мы и соседи» — отдельный инструмент кабинета.
 *
 * Раньше жил внизу «Портрета участника», и заявка из бэклога сайта прямо об
 * этом: «не совсем интуитивно понятно, что в портрете участников идёт
 * сравнение локаций». Заодно появился скоуп «одна локация» — сравнение с любой
 * выбранной площадкой, в том числе из другой системы.
 */
function OrganizerBenchmarkContent({ slug }: { slug: string }) {
  const [benchmark, setBenchmark] = useState<OrganizerBenchmarkResponse | null>(null);
  const [scope, setScope] = useState("network");
  const [months, setMonths] = useState(12);
  const [peer, setPeer] = useState<LocationIndexItem | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [forbidden, setForbidden] = useState(false);
  const [notFound, setNotFound] = useState(false);

  useEffect(() => {
    let cancelled = false;
    setBenchmark(null);
    getOrganizerBenchmark(slug, scope, months, scope === "location" ? peer?.slug : null)
      .then((payload) => {
        if (!cancelled) {
          setBenchmark(payload);
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
          setError(err instanceof Error ? err.message : "Не удалось посчитать сравнение");
        }
      });
    return () => {
      cancelled = true;
    };
  }, [slug, scope, months, peer]);

  const name = benchmark?.location.name ?? locationHintFor(slug)?.name ?? null;
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

  const byLocation = scope === "location";
  const peerName = benchmark?.peer_location?.name ?? peer?.name ?? "выбранная локация";

  return (
    <PortalSectionShell sidebar={sidebar}>
      <header className="loc-header">
        <OrganizerBreadcrumbs slug={slug} locationName={name} tool="Мы и соседи" />
        <div className="loc-header-title">
          <h1>{name ?? "Локация"} — мы и соседи</h1>
        </div>
        <p className="muted">
          Как локация выглядит на фоне других: по городу, региону, ближайшим соседям, всей
          системе — или рядом с любой выбранной площадкой.
        </p>
      </header>

      {error && (
        <div className="card error">
          <p>{error}</p>
        </div>
      )}

      <section className="card org-table-card">
        {/* Фильтры — отдельной строкой под заголовком: тип сравнения + период
            вместе, размером друг под друга (правка Дмитрия 24.08.2026). */}
        <div className="org-benchmark-controls">
          <span className="muted org-benchmark-controls-label">Сравниваем с:</span>
          <div className="map-mode-tabs org-benchmark-tabs" role="tablist">
            {SCOPES.filter((item) => {
              // Вкладка есть, только когда в выборке есть с кем сравнивать
              // (наша локация + хотя бы одна соседняя). Пока размеры не
              // приехали — показываем всё, чтобы табы не прыгали.
              const sizes = benchmark?.scope_sizes;
              if (!sizes || item.key === "network" || item.key === "location") {
                return true;
              }
              return (sizes[item.key] ?? 0) >= 2;
            }).map((item) => (
              <button
                key={item.key}
                type="button"
                role="tab"
                aria-selected={scope === item.key}
                className={scope === item.key ? "map-mode-tab active" : "map-mode-tab"}
                onClick={() => setScope(item.key)}
              >
                {item.label}
              </button>
            ))}
          </div>
          <span className="muted org-benchmark-controls-label">за</span>
          <FilterSelect
            ariaLabel="Период сравнения"
            title="За какой период сравниваем локации"
            value={months}
            onChange={setMonths}
            options={PERIOD_OPTIONS.map((option) => ({
              value: option.months,
              label: option.label,
            }))}
          />
        </div>

        {byLocation && (
          <div className="org-benchmark-controls">
            <span className="muted org-benchmark-controls-label">Локация:</span>
            <PeerPicker ownSlug={slug} peer={peer} onPick={setPeer} />
          </div>
        )}

        {benchmark === null && !error && <p className="muted">Считаем сравнение…</p>}
        {benchmark !== null && benchmark.peer_note !== null && (
          <p className="muted">{benchmark.peer_note}</p>
        )}
        {benchmark !== null && benchmark.peer_note === null && benchmark.metrics.length === 0 && (
          <p className="muted">Пока не с кем сравнивать.</p>
        )}
        {benchmark !== null && benchmark.metrics.length > 0 && (
          <>
            <p className="muted org-benchmark-note">
              {byLocation ? (
                <>
                  Сравнение с локацией «{peerName}»: цифры за один и тот же период у обеих
                  площадок.
                </>
              ) : benchmark.scope === "nearest" ? (
                <>
                  Сравнение с тремя ближайшими:{" "}
                  {benchmark.peers
                    .filter((peerItem) => !peerItem.is_ours)
                    .map((peerItem) => peerItem.name)
                    .join(", ")}
                  .
                </>
              ) : (
                <>
                  Сравнение: {benchmark.scope_label}, локаций в выборке —{" "}
                  {formatInt(benchmark.peers_total)} (с 5+ стартами за период).
                </>
              )}
            </p>
            <TableWrap>
              <table className="data-table org-svod-table org-benchmark-table">
                <thead>
                  <tr>
                    <th>Метрика</th>
                    <th>Мы</th>
                    {byLocation ? (
                      <>
                        <th>{peerName}</th>
                        <th title="На сколько процентов наша цифра больше или меньше">
                          Разница
                        </th>
                      </>
                    ) : (
                      <>
                        <th title="Середина выборки: половина локаций выше, половина ниже">
                          Медиана
                        </th>
                        <th>Лучшая</th>
                        <th>Наше место</th>
                      </>
                    )}
                  </tr>
                </thead>
                {groupMetrics(benchmark.metrics).map((group) => (
                  <tbody key={group.group}>
                    {/* Раздел таблицы: метрик стало много, и без разбивки
                        «явка / поле / новые лица / команда» они читались одним
                        сплошным списком. */}
                    <tr className="org-benchmark-group">
                      <th colSpan={byLocation ? 4 : 5} scope="colgroup">
                        {group.group}
                      </th>
                    </tr>
                    {group.rows.map((metric) => (
                      <tr key={metric.key}>
                        <td>{metric.label}</td>
                        <td>
                          <strong>{metricValue(metric, metric.our_value)}</strong>
                          {!byLocation && metric.delta_vs_median_pct != null && (
                            <>
                              {" "}
                              <DeltaPct metric={metric} delta={metric.delta_vs_median_pct} parens />
                            </>
                          )}
                        </td>
                        {byLocation ? (
                          <>
                            <td>{metricValue(metric, metric.peer_value)}</td>
                            <td>
                              {metric.delta_vs_peer_pct == null ? (
                                "—"
                              ) : (
                                <DeltaPct metric={metric} delta={metric.delta_vs_peer_pct} />
                              )}
                            </td>
                          </>
                        ) : (
                          <>
                            <td>{metricValue(metric, metric.median)}</td>
                            <td>{metricValue(metric, metric.best)}</td>
                            <td>
                              {metric.rank != null ? `${metric.rank} из ${metric.peers}` : "—"}
                            </td>
                          </>
                        )}
                      </tr>
                    ))}
                  </tbody>
                ))}
              </table>
            </TableWrap>
          </>
        )}
      </section>
    </PortalSectionShell>
  );
}

export function OrganizerBenchmarkPage({ slug }: { slug: string }) {
  return (
    <RequireAuth loginHref={PORTAL_LOGIN_HREF}>
      {() => <OrganizerBenchmarkContent slug={slug} />}
    </RequireAuth>
  );
}
