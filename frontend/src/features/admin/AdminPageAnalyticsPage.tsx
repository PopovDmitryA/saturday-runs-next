import { useCallback, useEffect, useRef, useState } from "react";
import { AdminShell } from "./AdminShell";
import { RequireAdmin } from "../../components/RequireAdmin";
import { getAdminPageAnalytics, type PageAnalyticsEntity, type PageAnalyticsResponse } from "../../lib/api";
import { formatDate, formatDateTime } from "../../lib/format";
import { AdminSubnav } from "./AdminSubnav";

const PERIODS = [
  { label: "1 день", days: 1 },
  { label: "3 дня", days: 3 },
  { label: "7 дней", days: 7 },
  { label: "30 дней", days: 30 },
  { label: "90 дней", days: 90 },
  { label: "Год", days: 365 },
] as const;

// Канон page_type — _STATIC_PAGE_TYPES в backend/app/services/page_analytics_service.py.
// Добавили роут — добавьте строку и там, и здесь.
const PAGE_TYPE_LABELS: Record<string, string> = {
  landing: "Главная (старый лендинг, до портала)",
  login: "Вход (старый, до портала)",
  oauth_callback: "Возврат из OAuth",
  welcome: "Онбординг (/welcome)",
  dashboard: "Дашборд",
  runs: "Пробежки",
  achievements: "Достижения",
  co_runners: "Встречи",
  volunteering: "Волонтёрство",
  maps: "Карта",
  history: "Моя история",
  profile: "Публичные профили",
  locations_index: "Локации (список)",
  last_results: "Результаты последней субботы",
  unified_protocol: "Единый протокол недели",
  location: "Локация (карточка)",
  location_events: "Локация (забеги)",
  location_protocol: "Локация (протокол)",
  ratings_hub: "Рейтинги (хаб)",
  ratings_runs: "Рейтинг: пробежки",
  ratings_volunteering: "Рейтинг: волонтёрство",
  ratings_locations: "Рейтинг: локации",
  ratings_wins: "Рейтинг: победы",
  ratings_win_locations: "Рейтинг: победные локации",
  share: "Поделиться",
  settings: "Настройки",
  about: "О проекте (старый, до портала)",
  demo: "Демо (все страницы)",
  portal_home: "Главная",
  portal_about: "О проекте",
  portal_blog: "Блог",
  updates: "Обновления (релизы)",
  blog_post_click: "Блог: переходы на посты",
  portal_login: "Вход",
  portal_map_lab: "Портал: карта (лаб)",
  admin: "Админка",
  backlog: "Бэклог",
  organizer_index: "Кабинет организатора (список)",
  organizer_location: "Кабинет организатора (локация)",
  cabinet_preview: "Превью кабинета (демо)",
  sweep_hq: "Штаб обхода parkrun",
  og_render: "Служебный рендер OG-картинок",
  redirect: "Редиректы и старые адреса кабинета",
  legacy_grafana: "Старые адреса Grafana",
  other: "Прочее (неизвестные адреса)",
};

function pageTypeLabel(pageType: string): string {
  return PAGE_TYPE_LABELS[pageType] ?? pageType;
}

// Ярлыки событий шаринга. Канон значений — frontend/src/features/sharing/.
const SHARE_FUNNEL_LABELS: Record<string, string> = {
  share_moment_shown: "Показы приглашений",
  share_open: "Открытия шторки",
  share_customize: "Заходы в «Настроить»",
  share_success: "Отправленные постеры",
};

const SHARE_SUBJECT_LABELS: Record<string, string> = {
  milestone: "Веха",
  run: "Пробежка",
  volunteering: "Волонтёрство",
  summary: "Сводка",
  location_event: "Локация: последний старт",
  location_card: "Локация: визитка",
  location_me: "Я на этой локации",
  rating: "Позиция в рейтинге",
};

const SHARE_ENTRY_LABELS: Record<string, string> = {
  dashboard: "дашборд",
  runs: "строка пробежки",
  volunteering: "строка волонтёрства",
  history: "вехи",
  on_this_day: "«В этот день»",
  location: "страница локации",
  rating: "рейтинги",
  gallery: "страница /share",
};

const SHARE_CHANNEL_LABELS: Record<string, string> = {
  system: "поделились (системный шит)",
  download: "скачали PNG",
  copy: "скопировали",
};

const SHARE_LOOK_LABELS: Record<string, string> = {
  indigo: "Индиго",
  night: "Ночь",
  porcelain: "Светлый",
  sunrise: "Рассвет",
  forest: "Лес",
  photo: "Своё фото",
};

const SHARE_FORMAT_LABELS: Record<string, string> = {
  story: "Сториз 9:16",
  square: "Квадрат 1:1",
  wide: "Широкий",
};

function formatDuration(seconds: number | null): string {
  if (seconds === null) {
    return "—";
  }
  if (seconds < 60) {
    return `${seconds} c`;
  }
  const minutes = Math.floor(seconds / 60);
  const rest = seconds % 60;
  return rest > 0 ? `${minutes} мин ${rest} c` : `${minutes} мин`;
}

function StatsCells({ row }: { row: { views: number; unique_viewers: number; avg_duration_sec: number | null } }) {
  return (
    <>
      <td className="page-analytics-num">{row.views}</td>
      <td className="page-analytics-num">{row.unique_viewers}</td>
      <td className="page-analytics-num">{formatDuration(row.avg_duration_sec)}</td>
    </>
  );
}

function EntityTable({
  title,
  rows,
  entityHeader,
  showSelfViews,
  emptyText,
}: {
  title: string;
  rows: PageAnalyticsEntity[];
  entityHeader: string;
  showSelfViews?: boolean;
  emptyText: string;
}) {
  return (
    <section className="card">
      <h2 className="section-title">{title}</h2>
      {rows.length === 0 ? (
        <p className="muted">{emptyText}</p>
      ) : (
        <div className="table-scroll">
          <table className="data-table page-analytics-table">
            <thead>
              <tr>
                <th>#</th>
                <th>{entityHeader}</th>
                <th>Просмотры</th>
                <th>Уникальные</th>
                <th>Среднее время</th>
                {showSelfViews && <th>Самопросмотры</th>}
              </tr>
            </thead>
            <tbody>
              {rows.map((row, index) => (
                <tr key={row.entity_key}>
                  <td className="muted">{index + 1}</td>
                  <td>{row.href ? <a href={row.href}>{row.label}</a> : row.label}</td>
                  <StatsCells row={row} />
                  {showSelfViews && <td className="page-analytics-num">{row.self_views}</td>}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}

/** Период отчёта: быстрая кнопка (N дней) либо произвольный диапазон дат. */
type PeriodSelection = { days: number } | { from: string; to: string };

function AdminPageAnalyticsContent() {
  const [period, setPeriod] = useState<PeriodSelection>({ days: 30 });
  const [customFrom, setCustomFrom] = useState("");
  const [customTo, setCustomTo] = useState("");
  const [data, setData] = useState<PageAnalyticsResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const loadSeq = useRef(0);

  const load = useCallback(async (selection: PeriodSelection) => {
    const seq = ++loadSeq.current;
    setLoading(true);
    setError(null);
    try {
      const payload = await getAdminPageAnalytics(
        "days" in selection
          ? { periodDays: selection.days }
          : { dateFrom: selection.from || undefined, dateTo: selection.to || undefined },
      );
      if (seq !== loadSeq.current) {
        return;
      }
      setData(payload);
    } catch (err) {
      if (seq !== loadSeq.current) {
        return;
      }
      setError(err instanceof Error ? err.message : "Не удалось загрузить данные");
    } finally {
      if (seq === loadSeq.current) {
        setLoading(false);
      }
    }
  }, []);

  useEffect(() => {
    void load(period);
  }, [load, period]);

  const activeDays = "days" in period ? period.days : null;
  const customApplied = !("days" in period);
  const canApplyCustom = customFrom !== "" || customTo !== "";

  const applyCustomRange = () => {
    if (canApplyCustom) {
      setPeriod({ from: customFrom, to: customTo });
    }
  };

  const totalViews = data?.sections.reduce((sum, section) => sum + section.views, 0) ?? 0;

  return (
    <AdminShell title="Популярность разделов">
      <AdminSubnav activePath="/admin/page-analytics" />

      <div className="admin-stats-toolbar">
        <div className="admin-stats-periods" role="tablist" aria-label="Период">
          {PERIODS.map((item) => (
            <button
              key={item.days}
              type="button"
              className={activeDays === item.days ? "btn primary" : "btn secondary"}
              onClick={() => {
                setCustomFrom("");
                setCustomTo("");
                setPeriod({ days: item.days });
              }}
            >
              {item.label}
            </button>
          ))}
        </div>
        {data?.generated_at && (
          <span className="muted admin-stats-generated">Обновлено: {formatDateTime(data.generated_at)}</span>
        )}
      </div>

      <div className="page-analytics-range">
        <label className="filter-field">
          <span className="filter-field-label">С</span>
          <input
            className="filter-field-input"
            type="date"
            value={customFrom}
            max={customTo || undefined}
            onChange={(event) => setCustomFrom(event.target.value)}
          />
        </label>
        <label className="filter-field">
          <span className="filter-field-label">По</span>
          <input
            className="filter-field-input"
            type="date"
            value={customTo}
            min={customFrom || undefined}
            onChange={(event) => setCustomTo(event.target.value)}
          />
        </label>
        <button
          type="button"
          className={customApplied ? "btn primary" : "btn secondary"}
          disabled={!canApplyCustom}
          onClick={applyCustomRange}
        >
          Показать
        </button>
        {data && (
          <span className="muted page-analytics-range-label">
            Период: {formatDate(data.date_from)} — {formatDate(data.date_to)}
          </span>
        )}
      </div>

      {loading && <p className="muted">Загрузка…</p>}
      {error && (
        <div className="card error">
          <p>{error}</p>
        </div>
      )}

      {!loading && !error && data && (
        <>
          {data.funnel.length > 0 && (
            <section className="card">
              <h2 className="section-title">Воронка регистрации</h2>
              <p className="muted">
                Считаются посетители, а не клики: открыл главную → нажал кнопку входа → дошёл до
                VK/Яндекса → завёл аккаунт → привязал платформу. «От предыдущей» показывает, на
                какой именно ступени рвётся. Привязка не ограничена концом периода — свежим
                регистрациям нужно время. Пишется с 22.08.2026.
              </p>
              <div className="table-scroll">
                <table className="data-table page-analytics-table">
                  <thead>
                    <tr>
                      <th>Ступень</th>
                      <th>Человек</th>
                      <th>От начала</th>
                      <th>От предыдущей</th>
                    </tr>
                  </thead>
                  <tbody>
                    {data.funnel.map((row) => (
                      <tr key={row.step}>
                        <td>{row.step}</td>
                        <td>{row.visitors}</td>
                        <td>{row.pct_of_start === null ? "—" : `${row.pct_of_start}%`}</td>
                        <td>{row.pct_of_prev === null ? "—" : `${row.pct_of_prev}%`}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </section>
          )}

          {data.home_ab.length > 0 && (
            <section className="card">
              <h2 className="section-title">Показы вариантов главной (архив АБ-теста)</h2>
              <p className="muted">
                Тест шёл 27.07–22.08.2026 и завершён: вариант B победил по конверсии в
                регистрацию и стал единственной главной. За периоды вне теста блок пустой.
              </p>
              <div className="table-scroll">
                <table className="data-table page-analytics-table">
                  <thead>
                    <tr>
                      <th>Вариант</th>
                      <th>Показы</th>
                      <th>Посетители</th>
                    </tr>
                  </thead>
                  <tbody>
                    {data.home_ab.map((row) => (
                      <tr key={row.variant}>
                        <td>Вариант {row.variant}</td>
                        <td>{row.views}</td>
                        <td>{row.viewers}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </section>
          )}

          {data.home_links.length > 0 && (
            <section className="card">
              <h2 className="section-title">Переходы с главной</h2>
              <p className="muted">
                Клики по ссылкам в текстах главной: названия локаций и имена участников.
                Пишется с 01.08.2026 — за более ранние периоды таблица пустая.
              </p>
              <div className="table-scroll">
                <table className="data-table page-analytics-table">
                  <thead>
                    <tr>
                      <th>Куда</th>
                      <th>Тип</th>
                      <th>Переходы</th>
                      <th>Посетители</th>
                    </tr>
                  </thead>
                  <tbody>
                    {data.home_links.map((row) => (
                      <tr key={`${row.kind}:${row.entity_key}`}>
                        <td>
                          {row.href ? (
                            <a href={row.href} target="_blank" rel="noreferrer">
                              {row.label}
                            </a>
                          ) : (
                            row.label
                          )}
                        </td>
                        <td className="muted">
                          {row.kind === "location" ? "Локация" : "Участник"}
                        </td>
                        <td>{row.clicks}</td>
                        <td>{row.visitors}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </section>
          )}

          {data.share.funnel.length > 0 && (
            <section className="card">
              <h2 className="section-title">Шаринг</h2>
              <p className="muted">
                Фича «Поделиться»: воронка от показа приглашения до отправленного постера.
                Пишется с релиза «Поделиться 2.0» — за более ранние периоды таблицы пустые.
              </p>
              <div className="table-scroll">
                <table className="data-table page-analytics-table">
                  <thead>
                    <tr>
                      <th>Шаг воронки</th>
                      <th>Событий</th>
                      <th>Посетителей</th>
                    </tr>
                  </thead>
                  <tbody>
                    {data.share.funnel.map((row) => (
                      <tr key={row.event_type}>
                        <td>{SHARE_FUNNEL_LABELS[row.event_type] ?? row.event_type}</td>
                        <td>{row.events}</td>
                        <td>{row.visitors}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              {data.share.pairs.length > 0 && (
                <div className="table-scroll">
                  <table className="data-table page-analytics-table">
                    <thead>
                      <tr>
                        <th>Где нажали</th>
                        <th>Сюжет</th>
                        <th>Показы</th>
                        <th>Открытия</th>
                      </tr>
                    </thead>
                    <tbody>
                      {data.share.pairs.map((row) => (
                        <tr key={`${row.subject}:${row.entry}`}>
                          <td>{SHARE_ENTRY_LABELS[row.entry] ?? row.entry}</td>
                          <td className="muted">
                            {SHARE_SUBJECT_LABELS[row.subject] ?? row.subject}
                          </td>
                          <td>{row.shown > 0 ? row.shown : "—"}</td>
                          <td>{row.opens}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
              {(data.share.channels.length > 0 ||
                data.share.looks.length > 0 ||
                data.share.formats.length > 0 ||
                data.share.photo_added > 0) && (
                <p className="muted">
                  {data.share.channels.length > 0 && (
                    <>
                      Итоги:{" "}
                      {data.share.channels
                        .map(
                          (row) =>
                            `${SHARE_CHANNEL_LABELS[row.channel] ?? row.channel} — ${row.successes}`,
                        )
                        .join(" · ")}
                      .{" "}
                    </>
                  )}
                  {data.share.looks.length > 0 && (
                    <>
                      Фоны:{" "}
                      {data.share.looks
                        .map((row) => `${SHARE_LOOK_LABELS[row.value] ?? row.value} — ${row.count}`)
                        .join(" · ")}
                      .{" "}
                    </>
                  )}
                  {data.share.formats.length > 0 && (
                    <>
                      Форматы:{" "}
                      {data.share.formats
                        .map(
                          (row) => `${SHARE_FORMAT_LABELS[row.value] ?? row.value} — ${row.count}`,
                        )
                        .join(" · ")}
                      .{" "}
                    </>
                  )}
                  {data.share.photo_added > 0 && <>Своё фото добавили: {data.share.photo_added}.</>}
                </p>
              )}
            </section>
          )}

          {data.og_fetches.length > 0 && (
            <section className="card">
              <h2 className="section-title">Разворачивания ссылок</h2>
              <p className="muted">
                Боты мессенджеров и поисковиков запрашивали превью страниц — прокси-метрика
                «ссылку на сайт кинули в чат». Считается по заходам на пререндер.
              </p>
              <div className="table-scroll">
                <table className="data-table page-analytics-table">
                  <thead>
                    <tr>
                      <th>Страница</th>
                      <th>Тип</th>
                      <th>Запросов</th>
                      <th>Ботов</th>
                    </tr>
                  </thead>
                  <tbody>
                    {data.og_fetches.map((row) => (
                      <tr key={`${row.page_type}:${row.entity_key}`}>
                        <td>
                          {row.href ? (
                            <a href={row.href} target="_blank" rel="noreferrer">
                              {row.label}
                            </a>
                          ) : (
                            row.label
                          )}
                        </td>
                        <td className="muted">{pageTypeLabel(row.page_type)}</td>
                        <td>{row.fetches}</td>
                        <td>{row.bots}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </section>
          )}

          <section className="card">
            <h2 className="section-title">Разделы сайта</h2>
            {data.sections.length === 0 ? (
              <p className="muted">
                Данных за период пока нет — события копятся с момента включения аналитики, агрегаты
                пересчитываются раз в час.
              </p>
            ) : (
              <div className="table-scroll">
                <table className="data-table page-analytics-table">
                  <thead>
                    <tr>
                      <th>Раздел</th>
                      <th>Просмотры</th>
                      <th>Уникальные</th>
                      <th>Среднее время</th>
                      <th>Доля</th>
                    </tr>
                  </thead>
                  <tbody>
                    {data.sections.map((section) => (
                      <tr key={section.page_type}>
                        <td>{pageTypeLabel(section.page_type)}</td>
                        <StatsCells row={section} />
                        <td className="page-analytics-num muted">
                          {totalViews > 0 ? `${Math.round((section.views / totalViews) * 100)}%` : "—"}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </section>

          <EntityTable
            title="Топ публичных профилей"
            rows={data.top_profiles}
            entityHeader="Профиль"
            showSelfViews
            emptyText="Просмотров публичных профилей за период не было."
          />
          <EntityTable
            title="Топ локаций"
            rows={data.top_locations}
            entityHeader="Локация"
            emptyText="Просмотров страниц локаций за период не было."
          />

          <p className="muted admin-stats-footnote">
            Уникальные — сумма дневных уникальных посетителей (один человек в разные дни учитывается
            несколько раз). Среднее время — по просмотрам с зафиксированной длительностью.
            Самопросмотры (владелец открыл собственный профиль) входят в «Просмотры» и показаны отдельно.
            Агрегаты обновляются раз в час; сырые события хранятся 90 дней, дневные итоги — бессрочно.
          </p>
        </>
      )}
    </AdminShell>
  );
}

export function AdminPageAnalyticsPage() {
  return <RequireAdmin>{() => <AdminPageAnalyticsContent />}</RequireAdmin>;
}
