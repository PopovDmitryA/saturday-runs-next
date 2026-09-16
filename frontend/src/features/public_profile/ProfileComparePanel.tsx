import { useEffect, useState } from "react";
import {
  getCoRunners,
  getDashboard,
  getProfileCompareLocations,
  type CoRunnerItem,
  type DashboardStats,
  type ProfileCompareLocationRow,
  type User,
} from "../../lib/api";
import {
  formatDate,
  formatFinishTimeValue,
  formatInt,
  formatKm,
  formatNumber,
  pluralizeRu,
} from "../../lib/format";
import { userLabel } from "../../lib/userLabel";
import { TableWrap } from "../../components/tableUx/TableWrap";
import { HeaderHint } from "../../components/tableUx/HeaderHint";

/** Кто выигрывает строку: у времён меньше — лучше, у профильных строк никто. */
type Better = "more" | "less" | "none";

type Row = {
  key: string;
  label: string;
  hint?: string;
  mine: number | null;
  theirs: number | null;
  better: Better;
  format: (value: number) => string;
};

type Group = { group: string; rows: Row[] };

const asTime = (value: number) => formatFinishTimeValue(null, Math.round(value)).replace(/^00:/, "");
const asPace = (value: number) => `${asTime(value)} /км`;
const asPct = (value: number) => `${formatNumber(value)}%`;
const asDate = (value: number) => formatDate(new Date(value).toISOString().slice(0, 10));

/** Дату сравниваем числом (timestamp), а показываем датой. */
function dateValue(iso: string | null): number | null {
  if (!iso) {
    return null;
  }
  const parsed = Date.parse(iso);
  return Number.isFinite(parsed) ? parsed : null;
}

/**
 * Что сравниваем и почему именно это.
 *
 * Сырые «всего пробежек» двоим с разным стажем говорят мало, поэтому рядом с
 * каждым итогом стоит срез за последние 12 месяцев и за текущий год.
 */
function metricGroups(mine: DashboardStats, theirs: DashboardStats): Group[] {
  const my = mine.analytics;
  const their = theirs.analytics;
  const sameWinScope = my.wins_scope === their.wins_scope;
  const groups: Group[] = [
    {
      group: "Бег",
      rows: [
        {
          key: "runs",
          label: "Пробежек всего",
          mine: mine.total_runs,
          theirs: theirs.total_runs,
          better: "more",
          format: formatInt,
        },
        {
          key: "runs_12m",
          label: "Пробежек за 12 месяцев",
          hint: "Честнее итога за всё время: не зависит от того, кто раньше начал",
          mine: my.runs_last_12_months,
          theirs: their.runs_last_12_months,
          better: "more",
          format: formatInt,
        },
        {
          key: "runs_year",
          label: "Пробежек в этом году",
          mine: my.runs_current_year,
          theirs: their.runs_current_year,
          better: "more",
          format: formatInt,
        },
        {
          key: "distance",
          label: "Набегано",
          mine: my.total_distance_km,
          theirs: their.total_distance_km,
          better: "more",
          format: formatKm,
        },
        {
          key: "first_run",
          label: "Первая пробежка",
          hint: "Просто контекст: у кого длиннее история, а не кто лучше",
          mine: dateValue(my.first_run_date),
          theirs: dateValue(their.first_run_date),
          better: "none",
          format: asDate,
        },
      ],
    },
    {
      group: "Скорость",
      rows: [
        {
          key: "best",
          label: "Лучшее время",
          mine: my.best_finish_time_sec,
          theirs: their.best_finish_time_sec,
          better: "less",
          format: asTime,
        },
        {
          key: "avg",
          label: "Среднее время",
          mine: my.avg_finish_time_sec,
          theirs: their.avg_finish_time_sec,
          better: "less",
          format: asTime,
        },
        {
          key: "pace",
          label: "Средний темп",
          mine: my.avg_pace_sec_per_km,
          theirs: their.avg_pace_sec_per_km,
          better: "less",
          format: asPace,
        },
        {
          key: "prs",
          label: "Личных рекордов",
          mine: my.pr_count,
          theirs: their.pr_count,
          better: "more",
          format: formatInt,
        },
        {
          key: "prs_12m",
          label: "Личных рекордов за 12 месяцев",
          mine: my.pr_last_12_months,
          theirs: their.pr_last_12_months,
          better: "more",
          format: formatInt,
        },
        {
          key: "wins",
          label: "Побед",
          // У женщин зачёт побед — среди женщин, у мужчин — в абсолюте
          // (analytics.wins_scope). В разнополой паре это разные величины, и
          // победителя строки мы не назначаем.
          hint: sameWinScope
            ? undefined
            : "У мужчин победы считаются в абсолюте, у женщин — в женском зачёте: числа посчитаны по разным правилам и напрямую не сравниваются",
          mine: my.wins_count ?? null,
          theirs: their.wins_count ?? null,
          better: sameWinScope ? "more" : "none",
          format: formatInt,
        },
      ],
    },
    {
      group: "Туризм",
      rows: [
        {
          key: "locations",
          label: "Локаций пробежек",
          mine: my.unique_run_locations,
          theirs: their.unique_run_locations,
          better: "more",
          format: formatInt,
        },
        {
          key: "new_locations",
          label: "Новых локаций за 12 месяцев",
          mine: my.new_locations_last_12_months,
          theirs: their.new_locations_last_12_months,
          better: "more",
          format: formatInt,
        },
        {
          key: "cities",
          label: "Городов",
          mine: my.unique_run_cities,
          theirs: their.unique_run_cities,
          better: "more",
          format: formatInt,
        },
        {
          key: "regions",
          label: "Регионов",
          mine: my.unique_run_regions,
          theirs: their.unique_run_regions,
          better: "more",
          format: formatInt,
        },
      ],
    },
    {
      group: "Волонтёрство",
      rows: [
        {
          key: "vol",
          label: "Волонтёрств всего",
          mine: mine.total_volunteering,
          theirs: theirs.total_volunteering,
          better: "more",
          format: formatInt,
        },
        {
          key: "vol_12m",
          label: "Волонтёрств за 12 месяцев",
          mine: my.volunteering_last_12_months,
          theirs: their.volunteering_last_12_months,
          better: "more",
          format: formatInt,
        },
        {
          key: "vol_roles",
          label: "Разных ролей",
          mine: my.unique_volunteer_roles,
          theirs: their.unique_volunteer_roles,
          better: "more",
          format: formatInt,
        },
        {
          key: "vol_locations",
          label: "Локаций волонтёрства",
          mine: my.unique_volunteer_locations,
          theirs: their.unique_volunteer_locations,
          better: "more",
          format: formatInt,
        },
      ],
    },
    {
      group: "Постоянство",
      rows: [
        {
          key: "streak_current",
          label: "Серия суббот сейчас",
          hint: "Подряд идущие субботы с пробежкой или волонтёрством",
          mine: my.saturday_streak_current ?? my.saturday_streak,
          theirs: their.saturday_streak_current ?? their.saturday_streak,
          better: "more",
          format: formatInt,
        },
        {
          key: "streak_max",
          label: "Самая длинная серия суббот",
          mine: my.saturday_streak_max ?? null,
          theirs: their.saturday_streak_max ?? null,
          better: "more",
          format: formatInt,
        },
        {
          key: "consistency",
          label: "Суббот из возможных",
          hint: "Доля суббот с участием за всё время, что человек в движении",
          mine: my.saturday_consistency_pct,
          theirs: their.saturday_consistency_pct,
          better: "more",
          format: asPct,
        },
      ],
    },
  ];
  return groups
    .map((group) => ({
      group: group.group,
      rows: group.rows.filter((row) => row.mine !== null || row.theirs !== null),
    }))
    .filter((group) => group.rows.length > 0);
}

function winner(row: Row): "mine" | "theirs" | null {
  if (row.better === "none" || row.mine === null || row.theirs === null || row.mine === row.theirs) {
    return null;
  }
  const mineWins = row.better === "more" ? row.mine > row.theirs : row.mine < row.theirs;
  return mineWins ? "mine" : "theirs";
}

/**
 * «Сравнить со мной» — самая популярная заявка публичного бэклога сайта
 * («Добавление в друзья, сравнения»). Живёт во вкладке «Встречи»: очный счёт
 * там уже посчитан, и сравнение — его естественное продолжение.
 *
 * Считать нечего: обе стороны берём из готовых дашбордов.
 */
export function ProfileComparePanel({
  viewer,
  theirName,
  theirSerialId,
  theirStats,
}: {
  viewer: User;
  theirName: string;
  theirSerialId: number;
  theirStats: DashboardStats | null;
}) {
  const [myStats, setMyStats] = useState<DashboardStats | null>(null);
  const [meeting, setMeeting] = useState<CoRunnerItem | null>(null);
  const [locations, setLocations] = useState<ProfileCompareLocationRow[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    getDashboard()
      .then((payload) => {
        if (!cancelled) {
          setMyStats(payload.stats);
        }
      })
      .catch((err) => {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : "Не удалось загрузить свою статистику");
        }
      });
    // Очный счёт лежит в списке «Встреч»: там уже посчитаны совместные старты
    // и кто кого чаще опережал. Берём максимум, который отдаёт эндпоинт, —
    // если человек не попал в этот список, блок просто не показываем.
    getCoRunners(200)
      .then((items) => {
        if (!cancelled) {
          setMeeting(items.find((item) => item.site_serial_id === theirSerialId) ?? null);
        }
      })
      .catch(() => {
        // Очный счёт — приятное дополнение, без него сравнение работает.
      });
    getProfileCompareLocations(theirSerialId)
      .then((payload) => {
        if (!cancelled) {
          setLocations(payload.items);
        }
      })
      .catch(() => {
        if (!cancelled) {
          setLocations([]);
        }
      });
    return () => {
      cancelled = true;
    };
  }, [theirSerialId]);

  if (error) {
    return (
      <div className="card error">
        <p>{error}</p>
      </div>
    );
  }
  if (!myStats || !theirStats) {
    return <p className="muted">Считаем сравнение…</p>;
  }

  const groups = metricGroups(myStats, theirStats);
  const rows = groups.flatMap((group) => group.rows);
  const myName = userLabel(viewer);
  const myScore = rows.filter((row) => winner(row) === "mine").length;
  const theirScore = rows.filter((row) => winner(row) === "theirs").length;
  const togetherCount = (locations ?? []).filter((row) => row.together_runs > 0).length;

  return (
    <>
      <section className="card">
        <h2 className="section-title">Очный счёт</h2>
        {meeting ? (
          <>
            <p className="cmp-score">
              <span className={meeting.my_wins > meeting.their_wins ? "cmp-win" : undefined}>
                {formatInt(meeting.my_wins)}
              </span>
              <span className="cmp-score-dash">:</span>
              <span className={meeting.their_wins > meeting.my_wins ? "cmp-win" : undefined}>
                {formatInt(meeting.their_wins)}
              </span>
            </p>
            <p className="muted">
              {pluralizeRu(meeting.meetings, [
                "совместный старт",
                "совместных старта",
                "совместных стартов",
              ])}
              {meeting.timed_meetings > 0 && (
                <>
                  {", из них с результатом у обоих — "}
                  {formatInt(meeting.timed_meetings)}
                </>
              )}
              {meeting.first_meeting_date && (
                <>
                  {". Первый — "}
                  {formatDate(meeting.first_meeting_date)}
                  {meeting.last_meeting_date &&
                  meeting.last_meeting_date !== meeting.first_meeting_date
                    ? `, последний — ${formatDate(meeting.last_meeting_date)}`
                    : ""}
                  .
                </>
              )}
            </p>
          </>
        ) : (
          <p className="muted">
            Общих стартов с этим участником у вас пока нет — сравниваем только статистику.
          </p>
        )}
      </section>

      <section className="card">
        <div className="loc-section-head">
          <h2 className="section-title">Статистика бок о бок</h2>
          <span className="muted">
            {formatInt(myScore)} : {formatInt(theirScore)} по показателям
          </span>
        </div>
        <TableWrap>
          <table className="data-table cmp-table">
            <thead>
              <tr>
                <th>Показатель</th>
                <th>{myName}</th>
                <th>{theirName}</th>
              </tr>
            </thead>
            {groups.map((group) => (
              <tbody key={group.group}>
                <tr className="cmp-group">
                  <th colSpan={3} scope="colgroup">
                    {group.group}
                  </th>
                </tr>
                {group.rows.map((row) => {
                  const best = winner(row);
                  return (
                    <tr key={row.key}>
                      <td>
                        {row.label}
                        {row.hint && <HeaderHint text={row.hint} />}
                      </td>
                      <td className={best === "mine" ? "cmp-cell-win" : undefined}>
                        {row.mine === null ? "—" : row.format(row.mine)}
                      </td>
                      <td className={best === "theirs" ? "cmp-cell-win" : undefined}>
                        {row.theirs === null ? "—" : row.format(row.theirs)}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            ))}
          </table>
        </TableWrap>
      </section>

      {/* Общие локации — просьба Дмитрия 17.09.2026: мало итогов, хочется
          увидеть, кто как проходит одни и те же трассы. Старты, где стояли
          рядом, помечены отдельной колонкой: общая площадка ещё не значит,
          что виделись. */}
      <section className="card">
        <div className="loc-section-head">
          <h2 className="section-title">Общие локации</h2>
          {locations !== null && locations.length > 0 && (
            <span className="muted">
              {pluralizeRu(locations.length, ["локация", "локации", "локаций"])}
              {togetherCount > 0 && `, вместе бежали на ${formatInt(togetherCount)}`}
            </span>
          )}
        </div>
        {locations === null && <p className="muted">Считаем…</p>}
        {locations !== null && locations.length === 0 && (
          <p className="muted">Локаций, где бегали оба, пока нет.</p>
        )}
        {locations !== null && locations.length > 0 && (
          <TableWrap stickyFirstCol>
            <table className="data-table cmp-table cmp-locations-table">
              <thead>
                <tr>
                  <th>Локация</th>
                  <th>
                    Вместе
                    <HeaderHint text="На скольких стартах здесь вы стояли рядом. Прочерк — площадка общая, но бегали в разное время" />
                  </th>
                  <th>{myName}</th>
                  <th>{theirName}</th>
                </tr>
              </thead>
              <tbody>
                {locations.map((row) => {
                  const bothTimed = row.my_best_sec !== null && row.their_best_sec !== null;
                  const iAmFaster =
                    bothTimed && (row.my_best_sec as number) < (row.their_best_sec as number);
                  const theyAreFaster =
                    bothTimed && (row.their_best_sec as number) < (row.my_best_sec as number);
                  return (
                    <tr key={row.identity_key}>
                      <td className="td-location">
                        {row.slug ? <a href={`/locations/${row.slug}`}>{row.name}</a> : row.name}
                      </td>
                      <td className="td-compact">
                        {row.together_runs > 0 ? formatInt(row.together_runs) : "—"}
                      </td>
                      <td className={iAmFaster ? "cmp-cell-win" : undefined}>
                        {row.my_best_sec === null ? "—" : asTime(row.my_best_sec)}
                        <span className="muted cmp-runs"> · {formatInt(row.my_runs)}</span>
                      </td>
                      <td className={theyAreFaster ? "cmp-cell-win" : undefined}>
                        {row.their_best_sec === null ? "—" : asTime(row.their_best_sec)}
                        <span className="muted cmp-runs"> · {formatInt(row.their_runs)}</span>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </TableWrap>
        )}
        <p className="muted cmp-locations-note">
          В колонках участников — лучшее время на этой локации и через точку число пробежек.
        </p>
      </section>
    </>
  );
}
