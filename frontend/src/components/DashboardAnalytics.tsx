import { useState, type ReactNode } from "react";
import { ActivityCalendarHeatmap } from "./ActivityCalendarHeatmap";
import { ChartColumnTooltip } from "./ChartColumnTooltip";
import { FinishTimeDistribution } from "./FinishTimeDistribution";
import type { DashboardAnalytics as DashboardAnalyticsData } from "../lib/api";
import {
  DASHBOARD_ANALYTICS_CARD_ORDER,
  DASHBOARD_ANALYTICS_PANEL_ORDER,
  sortByLayoutOrder,
} from "../lib/dashboardLayout";
import { PlatformBadge } from "./PlatformBadge";
import { BestResultsModal } from "./BestResultsModal";
import { PersonalRecordsModal } from "./PersonalRecordsModal";
import { StatHintTooltip } from "./StatHintTooltip";
import { TopLocationValue } from "./TopLocationValue";
import { VolunteerRolesModal } from "./VolunteerRolesModal";
import { HomeDistanceModal } from "./HomeDistanceModal";
import { PORTAL_CABINET_SETTINGS_HREF } from "../lib/portalRoutes";
import { UniqueLocationsModal } from "./UniqueLocationsModal";
import { WinsModal } from "./WinsModal";
import { RegionsCitiesModal, type GroupBy } from "./RegionsCitiesModal";
import { LocationRecordsModal } from "./LocationRecordsModal";
import {
  formatDate,
  formatDuration,
  formatMonthShort,
  formatMonthYear,
  formatPace,
  daysCapLabel,
  formatKm,
  kilometersLabel,
  citiesWithRunsLabel,
  citiesWithVolunteeringLabel,
  locationsWithRunsLabel,
  locationsWithVolunteeringLabel,
  regionsWithRunsLabel,
  regionsWithVolunteeringLabel,
  pluralizeRu,
  prRunsLabel,
  runsCapLabel,
  runsFormLabel,
  saturdaysLabel,
  timesLabel,
  volunteerRolesLabel,
  volunteeringCapLabel,
  winsCapLabel,
} from "../lib/format";

type DashboardAnalyticsProps = {
  analytics: DashboardAnalyticsData | undefined;
  totalRuns: number;
  totalVolunteering: number;
};

type AnalyticsCardCategory = "runs" | "volunteering" | "wins";

type AnalyticsCard = {
  key: string;
  value: ReactNode;
  label: ReactNode;
  category?: AnalyticsCardCategory;
  wide?: boolean;
  /** Пара с другой half-картой делит ряд пополам (не на всю ширину, но и не в общей сетке). */
  half?: boolean;
  clickable?: boolean;
  modalTarget?:
    | "unique_locations"
    | "best_results"
    | "personal_records"
    | "volunteer_roles"
    | "unique_regions"
    | "unique_cities"
    | "location_records"
    | "age_group_records"
    | "home_distance"
    | "wins";
  modalActivity?: "all" | "runs" | "volunteering";
  firstVisitSince?: string;
  tooltipContent?: ReactNode;
  labelMultiline?: boolean;
};

const VOLUNTEERING_INDEX_TOOLTIP = (
  <>
    <span>
      Индекс отражает, насколько активно вы участвуете в волонтёрстве относительно пробежек.
    </span>
    <span className="stat-hint-tooltip-note">
      Помогайте в организации ваших стартов чаще. Субботние старты проводятся только силами
      волонтёров.
    </span>
  </>
);

const SATURDAY_CONSISTENCY_TOOLTIP = (
  <>
    <span>Доля суббот за последние 52 недели, когда была пробежка или волонтёрство.</span>
    <span className="stat-hint-tooltip-note">
      Считаются только прошедшие субботы — будущие не учитываются.
    </span>
  </>
);

const HOME_DISTANCE_TOOLTIP = (
  <>
    <span>
      Сумма расстояний по прямой от домашней локации до каждой площадки, где вы бежали.
    </span>
    <span className="stat-hint-tooltip-note">
      Каждая площадка засчитывается один раз, сколько бы раз вы туда ни ездили. Домашняя
      локация меняется в настройках.
    </span>
  </>
);

const TOTAL_DISTANCE_TOOLTIP = (
  <span>Примерная суммарная дистанция: 5 км на каждую пробежку.</span>
);

function twelveMonthsAgoIso(): string {
  const date = new Date();
  date.setDate(date.getDate() - 365);
  return date.toISOString().slice(0, 10);
}

function cardThemeClass(category?: AnalyticsCardCategory): string {
  if (category === "runs") {
    return " stat-card-runs";
  }
  if (category === "volunteering") {
    return " stat-card-volunteering";
  }
  // Победы — золотая плитка: единственная награда в сетке, зелёный «беговой»
  // фон её теряет среди счётчиков локаций и километров.
  if (category === "wins") {
    return " stat-card-wins";
  }
  return "";
}

function buildAnalyticsCards(
  analytics: DashboardAnalyticsData,
  totalRuns: number,
  totalVolunteering: number,
): AnalyticsCard[] {
  const cards: AnalyticsCard[] = [];

  if (analytics.unique_run_locations > 0) {
    cards.push({
      key: "unique_run_locations",
      value: String(analytics.unique_run_locations),
      label: locationsWithRunsLabel(analytics.unique_run_locations),
      category: "runs",
      clickable: true,
      modalTarget: "unique_locations",
      modalActivity: "runs",
    });
  }

  if ((analytics.unique_run_regions ?? 0) > 0) {
    cards.push({
      key: "unique_run_regions",
      value: String(analytics.unique_run_regions),
      label: regionsWithRunsLabel(analytics.unique_run_regions ?? 0),
      category: "runs",
      clickable: true,
      modalTarget: "unique_regions",
      modalActivity: "runs",
    });
  }

  if ((analytics.unique_run_cities ?? 0) > 0) {
    cards.push({
      key: "unique_run_cities",
      value: String(analytics.unique_run_cities),
      label: citiesWithRunsLabel(analytics.unique_run_cities ?? 0),
      category: "runs",
      clickable: true,
      modalTarget: "unique_cities",
      modalActivity: "runs",
    });
  }

  if (analytics.unique_volunteer_locations > 0) {
    cards.push({
      key: "unique_volunteer_locations",
      value: String(analytics.unique_volunteer_locations),
      label: locationsWithVolunteeringLabel(analytics.unique_volunteer_locations),
      category: "volunteering",
      clickable: true,
      modalTarget: "unique_locations",
      modalActivity: "volunteering",
    });
  }

  if ((analytics.unique_volunteer_regions ?? 0) > 0) {
    cards.push({
      key: "unique_volunteer_regions",
      value: String(analytics.unique_volunteer_regions),
      label: regionsWithVolunteeringLabel(analytics.unique_volunteer_regions ?? 0),
      category: "volunteering",
      clickable: true,
      modalTarget: "unique_regions",
      modalActivity: "volunteering",
    });
  }

  if ((analytics.unique_volunteer_cities ?? 0) > 0) {
    cards.push({
      key: "unique_volunteer_cities",
      value: String(analytics.unique_volunteer_cities),
      label: citiesWithVolunteeringLabel(analytics.unique_volunteer_cities ?? 0),
      category: "volunteering",
      clickable: true,
      modalTarget: "unique_cities",
      modalActivity: "volunteering",
    });
  }

  if (totalRuns > 0 && analytics.avg_position != null) {
    cards.push({
      key: "avg_position",
      value: String(analytics.avg_position),
      label: "Среднее место в протоколе",
      category: "runs",
    });
  }

  if (totalRuns > 0 && analytics.avg_gender_position != null) {
    cards.push({
      key: "avg_gender_position",
      value: String(analytics.avg_gender_position),
      label: "Среднее место по полу",
      category: "runs",
    });
  }

  if (totalRuns > 0 && analytics.avg_finish_time_sec != null) {
    cards.push({
      key: "avg_finish",
      value: formatDuration(analytics.avg_finish_time_sec),
      label: "Среднее время финиша",
      category: "runs",
    });
  }

  if (totalRuns > 0 && analytics.best_finish_time_sec != null) {
    const hasMultipleBestResults = (analytics.best_results_platform_count ?? 1) > 1;
    cards.push({
      key: "best_finish",
      value: formatDuration(analytics.best_finish_time_sec),
      label: "Лучший результат",
      category: "runs",
      clickable: hasMultipleBestResults,
      modalTarget: hasMultipleBestResults ? "best_results" : undefined,
    });
  }

  if (totalRuns > 0 && analytics.avg_pace_sec_per_km != null) {
    cards.push({
      key: "avg_pace",
      value: `${formatPace(analytics.avg_pace_sec_per_km)} /км`,
      label: "Средний темп",
      category: "runs",
    });
  }

  if (analytics.runs_last_12_months > 0) {
    cards.push({
      key: "runs_12m",
      value: String(analytics.runs_last_12_months),
      label: `${runsCapLabel(analytics.runs_last_12_months)} за 12 мес.`,
      category: "runs",
    });
  }

  if (analytics.runs_current_year > 0) {
    cards.push({
      key: "runs_year",
      value: String(analytics.runs_current_year),
      label: `${runsCapLabel(analytics.runs_current_year)} в этом году`,
      category: "runs",
    });
  }

  if ((analytics.volunteering_last_12_months ?? 0) > 0) {
    cards.push({
      key: "volunteering_12m",
      value: String(analytics.volunteering_last_12_months),
      label: `${volunteeringCapLabel(analytics.volunteering_last_12_months ?? 0)} за 12 мес.`,
      category: "volunteering",
    });
  }

  if ((analytics.volunteering_current_year ?? 0) > 0) {
    cards.push({
      key: "volunteering_year",
      value: String(analytics.volunteering_current_year),
      label: `${volunteeringCapLabel(analytics.volunteering_current_year ?? 0)} в этом году`,
      category: "volunteering",
    });
  }

  // Дальность от дома: сумма расстояний до уникальных площадок. Самый дальний
  // старт в подписи не называем (решение Дмитрия 03.08.2026 — перегружало
  // плитку), его видно первой строкой в модалке: она отсортирована по убыванию.
  const homeDistance = analytics.home_distance;
  if (homeDistance?.home && homeDistance.total_distance_km > 0) {
    cards.push({
      key: "home_distance",
      value: formatKm(homeDistance.total_distance_km),
      label: "бегового туризма",
      category: "runs",
      clickable: true,
      modalTarget: "home_distance",
      tooltipContent: HOME_DISTANCE_TOOLTIP,
    });
  }

  if (totalRuns > 0 && (analytics.total_distance_km ?? 0) > 0) {
    cards.push({
      key: "total_distance",
      value: String(analytics.total_distance_km),
      label: kilometersLabel(analytics.total_distance_km ?? 0),
      category: "runs",
      tooltipContent: TOTAL_DISTANCE_TOOLTIP,
    });
  }

  if (analytics.next_milestone_runs != null && analytics.runs_to_next_milestone != null && totalRuns > 0) {
    cards.push({
      key: "next_milestone",
      value: String(analytics.runs_to_next_milestone),
      label: `До ${analytics.next_milestone_runs} ${runsCapLabel(analytics.next_milestone_runs).toLowerCase()}`,
      category: "runs",
    });
  }

  if (
    analytics.saturday_consistency_pct != null &&
    analytics.saturday_consistency_total > 0
  ) {
    cards.push({
      key: "saturday_consistency",
      value: `${analytics.saturday_consistency_pct}%`,
      label: `${saturdaysLabel(analytics.saturday_consistency_active)} из ${analytics.saturday_consistency_total}`,
      tooltipContent: SATURDAY_CONSISTENCY_TOOLTIP,
    });
  }

  // Победы: у женщин — среди женщин, у мужчин — в абсолюте (разрез приходит
  // с бэка в wins_scope). Плитку без побед не показываем — как и остальные
  // счётчики, она появляется, когда есть что показать.
  const winsCount = analytics.wins_count ?? 0;
  if (winsCount > 0) {
    cards.push({
      key: "wins",
      value: String(winsCount),
      label:
        analytics.wins_scope === "female"
          ? `${winsCapLabel(winsCount)} среди женщин`
          : `${winsCapLabel(winsCount)} в абсолюте`,
      category: "wins",
      clickable: true,
      modalTarget: "wins",
      labelMultiline: true,
    });
  }

  if (analytics.pr_count > 0) {
    cards.push({
      key: "pr_count",
      value: String(analytics.pr_count),
      label: prRunsLabel(analytics.pr_count),
      category: "runs",
      clickable: true,
      modalTarget: "personal_records",
    });
  }

  // Плитки рекордов локаций показываем и когда все рекорды утеряны (счётчик 0,
  // но есть утерянные): участник должен суметь открыть модалку и увидеть, какой
  // рекорд он потерял, кем и когда перебит.
  const locationRecords = analytics.location_records;
  if (locationRecords && (locationRecords.current_count > 0 || locationRecords.lost_count > 0)) {
    cards.push({
      key: "location_records",
      value: String(locationRecords.current_count),
      label: locationRecords.current_count === 1 ? "Рекорд локации" : "Рекорды локаций",
      category: "runs",
      clickable: true,
      modalTarget: "location_records",
    });
  }

  const ageGroupRecords = analytics.age_group_records;
  if (ageGroupRecords && (ageGroupRecords.current_count > 0 || ageGroupRecords.lost_count > 0)) {
    cards.push({
      key: "age_group_records",
      value: String(ageGroupRecords.current_count),
      label:
        ageGroupRecords.current_count === 1
          ? "Рекорд в возрастной группе"
          : "Рекорды в возрастных группах",
      category: "runs",
      clickable: true,
      modalTarget: "age_group_records",
      labelMultiline: true,
    });
  }

  // Плашка «Последний PR» временно скрыта (02.07.2026) — вернём, если попросят пользователи.

  if (analytics.last_global_pr_date) {
    cards.push({
      key: "last_global_pr_date",
      value: formatDate(analytics.last_global_pr_date),
      label: "Последний глобальный PR",
      category: "runs",
      clickable: true,
      modalTarget: "personal_records",
    });
  }

  if ((analytics.new_locations_last_12_months ?? 0) > 0) {
    cards.push({
      key: "new_locations_12m",
      value: String(analytics.new_locations_last_12_months),
      label: pluralizeRu(analytics.new_locations_last_12_months ?? 0, [
        "новая локация за 12 мес.",
        "новые локации за 12 мес.",
        "новых локаций за 12 мес.",
      ]),
      category: "runs",
      clickable: true,
      modalTarget: "unique_locations",
      modalActivity: "all",
      firstVisitSince: twelveMonthsAgoIso(),
    });
  }

  if (analytics.saturday_streak > 0) {
    cards.push({
      key: "saturday_streak",
      value: String(analytics.saturday_streak),
      label: `${saturdaysLabel(analytics.saturday_streak)} подряд`,
    });
  }

  if (analytics.days_since_first_run != null && analytics.days_since_first_run >= 0) {
    cards.push({
      key: "days_since_first_run",
      value: String(analytics.days_since_first_run),
      label: `${daysCapLabel(analytics.days_since_first_run)} с первой пробежки`,
      category: "runs",
    });
  }

  if (analytics.volunteering_index) {
    cards.push({
      key: "volunteering_index",
      value: analytics.volunteering_index,
      category: "volunteering",
      label: (
        <>
          Индекс
          <br />
          волонтёрства
        </>
      ),
      labelMultiline: true,
      tooltipContent: VOLUNTEERING_INDEX_TOOLTIP,
    });
  }

  if (totalVolunteering > 0 && analytics.unique_volunteer_roles > 0) {
    cards.push({
      key: "unique_roles",
      value: String(analytics.unique_volunteer_roles),
      label: volunteerRolesLabel(analytics.unique_volunteer_roles),
      category: "volunteering",
      clickable: true,
      modalTarget: "volunteer_roles",
    });
  }

  if (analytics.top_volunteer_role) {
    cards.push({
      key: "top_role",
      value: analytics.top_volunteer_role.role,
      label: `Частая роль · ${analytics.top_volunteer_role.count} ${timesLabel(analytics.top_volunteer_role.count)}`,
      category: "volunteering",
      half: true,
    });
  }

  if (analytics.top_location) {
    cards.push({
      key: "top_location",
      value: <TopLocationValue topLocation={analytics.top_location} />,
      label: `Самая частая локация · ${analytics.top_location.count} ${timesLabel(analytics.top_location.count)}`,
      half: true,
    });
  }

  // «Период активности» убран — та же информация (первая и последняя веха)
  // теперь видна в «Моей истории» целиком, отдельной картой дублировать не нужно.

  return cards;
}

function ActivityMonthChart({ data }: { data: DashboardAnalyticsData["activity_by_month"] }) {
  const maxValue = Math.max(...data.map((item) => item.runs + item.volunteering), 1);

  return (
    <div className="analytics-chart">
      <div
        className="analytics-chart-bars analytics-chart-bars-month"
        style={{ gridTemplateColumns: `repeat(${data.length}, minmax(0, 1fr))` }}
        role="img"
        aria-label="Активность по месяцам"
      >
        {data.map((item) => {
          const total = item.runs + item.volunteering;
          const stackHeight = total > 0 ? (total / maxValue) * 100 : 0;
          const monthLabel = formatMonthYear(item.month);
          const tooltipLines = [
            pluralizeRu(item.runs, ["пробежка", "пробежки", "пробежек"]),
            pluralizeRu(item.volunteering, ["волонтёрство", "волонтёрства", "волонтёрств"]),
          ];
          return (
            <div key={item.month} className="analytics-chart-bar-wrap">
              <ChartColumnTooltip title={monthLabel} lines={tooltipLines}>
                <div className="analytics-chart-bar-stack" style={{ height: `${stackHeight}%` }}>
                  {item.volunteering > 0 && (
                    <span
                      className="analytics-chart-bar analytics-chart-bar-vol"
                      style={{ flex: item.volunteering }}
                    />
                  )}
                  {item.runs > 0 && (
                    <span
                      className="analytics-chart-bar analytics-chart-bar-run"
                      style={{ flex: item.runs }}
                    />
                  )}
                </div>
              </ChartColumnTooltip>
            </div>
          );
        })}
      </div>
      {/* Подписи вынесены из колонок в отдельный ряд: на телефоне они
          вертикальные и разной длины («нояб.» против «дек.»), а внутри колонки
          съедали её высоту неодинаково — столбцы стояли на разных уровнях и
          график «плясал». Своя сетка с теми же колонками держит их под баром. */}
      <div
        className="analytics-chart-month-labels"
        style={{ gridTemplateColumns: `repeat(${data.length}, minmax(0, 1fr))` }}
        aria-hidden="true"
      >
        {data.map((item) => (
          <span key={item.month} className="analytics-chart-label analytics-chart-label-month">
            {formatMonthShort(item.month)}
          </span>
        ))}
      </div>
      <div className="analytics-chart-legend">
        <span className="analytics-legend-item">
          <span className="analytics-legend-swatch analytics-legend-swatch-run" />
          Пробежки
        </span>
        <span className="analytics-legend-item">
          <span className="analytics-legend-swatch analytics-legend-swatch-vol" />
          Волонтёрство
        </span>
      </div>
    </div>
  );
}

type PacePoint = {
  key: string;
  axisLabel: string;
  tooltipTitle: string;
  avgPaceSecPerKm: number | null;
  avgFinishTimeSec: number | null;
};

function PaceTrendChart({
  monthly,
  yearly,
}: {
  monthly: DashboardAnalyticsData["pace_trend"];
  yearly: NonNullable<DashboardAnalyticsData["pace_trend_yearly"]>;
}) {
  const hasYearly = yearly.length > 1;
  const [period, setPeriod] = useState<"months" | "all">("months");
  const showYearly = period === "all" && hasYearly;

  const data: PacePoint[] = showYearly
    ? yearly.map((item) => ({
        key: item.year,
        axisLabel: item.year,
        tooltipTitle: item.year,
        avgPaceSecPerKm: item.avg_pace_sec_per_km,
        avgFinishTimeSec: item.avg_finish_time_sec ?? null,
      }))
    : monthly.map((item) => ({
        key: item.month,
        axisLabel: formatMonthShort(item.month),
        tooltipTitle: formatMonthYear(item.month),
        avgPaceSecPerKm: item.avg_pace_sec_per_km,
        avgFinishTimeSec: item.avg_finish_time_sec ?? null,
      }));

  if (data.length === 0) {
    return null;
  }

  const paces = data.map((item) => item.avgPaceSecPerKm ?? 0);
  const minPace = Math.min(...paces);
  const maxPace = Math.max(...paces);
  const spread = Math.max(maxPace - minPace, 1);

  const plotTop = 8;
  const plotBottom = 92;
  const plotRange = plotBottom - plotTop;

  const points = data.map((item, index) => {
    const pace = item.avgPaceSecPerKm ?? minPace;
    const x = ((index + 0.5) / data.length) * 100;
    const y = plotTop + ((pace - minPace) / spread) * plotRange;
    return { item, pace, x, y };
  });

  const linePoints = points.map((point) => `${point.x},${point.y}`).join(" ");

  return (
    <div className="analytics-chart analytics-chart-pace">
      <div className="analytics-chart-pace-toggle" role="tablist" aria-label="Период графика темпа">
        <button
          type="button"
          className={`insights-filter-chip${period === "months" ? " active" : ""}`}
          onClick={() => setPeriod("months")}
        >
          12 месяцев
        </button>
        {hasYearly && (
          <button
            type="button"
            className={`insights-filter-chip${period === "all" ? " active" : ""}`}
            onClick={() => setPeriod("all")}
          >
            Весь период
          </button>
        )}
      </div>
      <div
        className="analytics-chart-pace-line-wrap"
        role="img"
        aria-label={showYearly ? "Динамика темпа по годам" : "Динамика темпа по месяцам"}
      >
        <div className="analytics-chart-pace-plot">
          <svg className="analytics-chart-pace-svg" viewBox="0 0 100 100" preserveAspectRatio="none">
            <polyline
              className="analytics-chart-pace-line"
              points={linePoints}
              fill="none"
              vectorEffect="non-scaling-stroke"
            />
          </svg>
          {points.map((point) => (
            <span
              key={point.item.key}
              className="analytics-chart-pace-marker"
              style={{ left: `${point.x}%`, top: `${point.y}%` }}
              aria-hidden="true"
            />
          ))}
          <div
            className="analytics-chart-pace-hits"
            style={{ gridTemplateColumns: `repeat(${data.length}, minmax(0, 1fr))` }}
          >
            {points.map((point) => (
              <ChartColumnTooltip
                key={point.item.key}
                title={point.item.tooltipTitle}
                lines={[
                  `Средний темп: ${formatPace(point.pace)} /км`,
                  ...(point.item.avgFinishTimeSec != null
                    ? [`Среднее время финиша: ${formatDuration(point.item.avgFinishTimeSec)}`]
                    : []),
                ]}
              >
                <span className="analytics-chart-pace-hit" aria-hidden="true" />
              </ChartColumnTooltip>
            ))}
          </div>
        </div>
        <div
          className="analytics-chart-pace-labels"
          style={{ gridTemplateColumns: `repeat(${data.length}, minmax(0, 1fr))` }}
        >
          {points.map((point) => (
            <span key={point.item.key} className="analytics-chart-label analytics-chart-label-month">
              {point.item.axisLabel}
            </span>
          ))}
        </div>
      </div>
      <p className="muted analytics-chart-caption">Выше линия — быстрее темп</p>
    </div>
  );
}

export function DashboardAnalytics({
  analytics,
  totalRuns,
  totalVolunteering,
}: DashboardAnalyticsProps) {
  const [uniqueLocationsOpen, setUniqueLocationsOpen] = useState(false);
  const [bestResultsOpen, setBestResultsOpen] = useState(false);
  const [personalRecordsOpen, setPersonalRecordsOpen] = useState(false);
  const [winsOpen, setWinsOpen] = useState(false);
  const [volunteerRolesOpen, setVolunteerRolesOpen] = useState(false);
  const [locationRecordsOpen, setLocationRecordsOpen] = useState(false);
  const [ageGroupRecordsOpen, setAgeGroupRecordsOpen] = useState(false);
  const [regionsCitiesOpen, setRegionsCitiesOpen] = useState(false);
  const [homeDistanceOpen, setHomeDistanceOpen] = useState(false);
  const [regionsCitiesGroupBy, setRegionsCitiesGroupBy] = useState<GroupBy>("region");
  const [modalActivity, setModalActivity] = useState<"all" | "runs" | "volunteering">("all");
  const [uniqueLocationsFirstVisitSince, setUniqueLocationsFirstVisitSince] = useState<
    string | undefined
  >();

  const openUniqueLocations = (
    activity: "all" | "runs" | "volunteering",
    firstVisitSince?: string,
  ) => {
    setModalActivity(activity);
    setUniqueLocationsFirstVisitSince(firstVisitSince);
    setUniqueLocationsOpen(true);
  };

  const handleCardClick = (card: AnalyticsCard) => {
    if (card.modalTarget === "best_results") {
      setBestResultsOpen(true);
      return;
    }
    if (card.modalTarget === "personal_records") {
      setPersonalRecordsOpen(true);
      return;
    }
    if (card.modalTarget === "wins") {
      setWinsOpen(true);
      return;
    }
    if (card.modalTarget === "volunteer_roles") {
      setVolunteerRolesOpen(true);
      return;
    }
    if (card.modalTarget === "location_records") {
      setLocationRecordsOpen(true);
      return;
    }
    if (card.modalTarget === "age_group_records") {
      setAgeGroupRecordsOpen(true);
      return;
    }
    if (card.modalTarget === "home_distance") {
      setHomeDistanceOpen(true);
      return;
    }
    if (card.modalTarget === "unique_regions") {
      setRegionsCitiesGroupBy("region");
      setModalActivity(card.modalActivity ?? "all");
      setRegionsCitiesOpen(true);
      return;
    }
    if (card.modalTarget === "unique_cities") {
      setRegionsCitiesGroupBy("city");
      setModalActivity(card.modalActivity ?? "all");
      setRegionsCitiesOpen(true);
      return;
    }
    openUniqueLocations(card.modalActivity ?? "all", card.firstVisitSince);
  };

  if (!analytics) {
    return null;
  }

  // Красным подсвечиваем только шаткий автовыбор (ничья по числу пробежек или
  // вторая площадка почти вровень) — выбранное руками не трогаем, иначе баннер
  // висел бы у всех, кто в настройки просто не заходил.
  const homeAmbiguity = analytics.home_distance?.home?.ambiguity
    ? analytics.home_distance.home
    : null;

  const cards = sortByLayoutOrder(
    buildAnalyticsCards(analytics, totalRuns, totalVolunteering),
    DASHBOARD_ANALYTICS_CARD_ORDER,
  );
  const hasActivityChart = analytics.activity_by_month.some(
    (item) => item.runs > 0 || item.volunteering > 0,
  );
  const hasPlatformMetrics = analytics.platform_metrics.length > 0;
  const hasPaceTrend = analytics.pace_trend.length > 1;
  const activityCalendar = analytics.activity_calendar ?? [];
  const hasActivityCalendar = activityCalendar.length > 0;
  const finishTimes = analytics.finish_times_sec ?? [];
  const hasFinishDistribution = finishTimes.length >= 3;

  if (
    cards.length === 0 &&
    !hasActivityChart &&
    !hasPlatformMetrics &&
    !hasPaceTrend &&
    !hasActivityCalendar &&
    !hasFinishDistribution
  ) {
    return null;
  }

  const renderCardBody = (card: AnalyticsCard) => (
    <>
      <span className="stat-value stat-value-secondary">{card.value}</span>
      <span className={`stat-label${card.labelMultiline ? " stat-label-multiline" : ""}`}>
        {card.label}
      </span>
      {card.clickable && <span className="stat-card-hint">Подробнее</span>}
      {card.tooltipContent && !card.clickable && (
        <span className="stat-card-hint">Подробнее</span>
      )}
    </>
  );

  const renderCard = (card: AnalyticsCard) => {
    const cardClassName = `stat-card stat-card-secondary${cardThemeClass(card.category)}${
      card.wide ? " stat-card-wide" : ""
    }${card.tooltipContent ? " stat-card-with-tooltip" : ""}`;

    if (card.clickable) {
      return (
        <button
          key={card.key}
          type="button"
          className={`${cardClassName} stat-card-clickable`}
          onClick={() => handleCardClick(card)}
        >
          {renderCardBody(card)}
        </button>
      );
    }

    if (card.tooltipContent) {
      return (
        <StatHintTooltip
          key={card.key}
          content={card.tooltipContent}
          className={`stat-card-tooltip-wrap ${cardClassName}`}
        >
          {renderCardBody(card)}
        </StatHintTooltip>
      );
    }

    return (
      <div key={card.key} className={cardClassName}>
        {renderCardBody(card)}
      </div>
    );
  };

  const panels: Array<{ key: string; node: ReactNode }> = [];
  if (hasPlatformMetrics) {
    panels.push({
      key: "platform_metrics",
      node: (
        <>
          <h2 className="section-title">Средние показатели по платформам</h2>
          <ul className="platform-metrics-list">
            {analytics.platform_metrics.map((item) => (
              <li key={item.platform_code} className="platform-metrics-row">
                <PlatformBadge code={item.platform_code} />
                {(item.runs_count ?? 0) > 0 && (
                  <span className="platform-metrics-count">
                    <b>{item.runs_count}</b> {runsFormLabel(item.runs_count ?? 0)}
                  </span>
                )}
                <div className="platform-metrics-values">
                  {item.avg_finish_time_sec != null && (
                    <span>{formatDuration(item.avg_finish_time_sec)}</span>
                  )}
                  {item.avg_pace_sec_per_km != null && (
                    <span className="muted">{formatPace(item.avg_pace_sec_per_km)} /км</span>
                  )}
                </div>
              </li>
            ))}
          </ul>
        </>
      ),
    });
  }
  if (hasActivityChart) {
    panels.push({
      key: "activity_chart",
      node: (
        <>
          <h2 className="section-title">Активность по месяцам</h2>
          <ActivityMonthChart data={analytics.activity_by_month} />
        </>
      ),
    });
  }
  if (hasActivityCalendar) {
    panels.push({
      key: "activity_calendar",
      node: (
        <>
          <h2 className="section-title">Календарь суббот</h2>
          <ActivityCalendarHeatmap
            days={activityCalendar}
            saturdayStreakMax={analytics.saturday_streak_max ?? 0}
            bestStreak={{
              total: analytics.saturday_streak_max ?? 0,
              runs: analytics.saturday_run_streak_max ?? 0,
              volunteering: analytics.saturday_vol_streak_max ?? 0,
            }}
            currentStreak={{
              total: analytics.saturday_streak_current ?? 0,
              runs: analytics.saturday_run_streak_current ?? 0,
              volunteering: analytics.saturday_vol_streak_current ?? 0,
            }}
          />
        </>
      ),
    });
  }
  if (hasPaceTrend) {
    panels.push({
      key: "pace_trend",
      node: (
        <>
          <h2 className="section-title">Динамика темпа</h2>
          <PaceTrendChart monthly={analytics.pace_trend} yearly={analytics.pace_trend_yearly ?? []} />
        </>
      ),
    });
  }

  if (hasFinishDistribution) {
    panels.push({
      key: "finish_distribution",
      node: (
        <>
          <h2 className="section-title">Распределение результатов</h2>
          <FinishTimeDistribution times={finishTimes} />
        </>
      ),
    });
  }

  const orderedPanels = sortByLayoutOrder(panels, DASHBOARD_ANALYTICS_PANEL_ORDER);

  const gridCards = cards.filter((card) => !card.half);
  const halfCards = cards.filter((card) => card.half);

  return (
    <section className="dashboard-analytics" aria-label="Дополнительная аналитика">
      {homeAmbiguity && (
        <p className="home-location-warning" role="status">
          <b>Проверьте домашнюю локацию.</b> От неё считается дальность ваших стартов, а
          выбралась она автоматически и неуверенно: сейчас это {homeAmbiguity.name}
          {homeAmbiguity.runner_up_name && (
            <>
              , но почти столько же пробежек у вас на площадке «{homeAmbiguity.runner_up_name}»
            </>
          )}
          . Укажите домашнюю локацию в{" "}
          <a href={PORTAL_CABINET_SETTINGS_HREF}>настройках</a>.
        </p>
      )}
      {gridCards.length > 0 && (
        <div className="stats-grid stats-grid-secondary">{gridCards.map((card) => renderCard(card))}</div>
      )}

      {halfCards.length > 0 && (
        <div className="stats-grid stats-grid-halves">{halfCards.map((card) => renderCard(card))}</div>
      )}

      {orderedPanels.map((panel) => (
        <div key={panel.key} className="card analytics-panel">
          {panel.node}
        </div>
      ))}

      <UniqueLocationsModal
        open={uniqueLocationsOpen}
        onClose={() => {
          setUniqueLocationsOpen(false);
          setUniqueLocationsFirstVisitSince(undefined);
        }}
        activityFilter={modalActivity}
        firstVisitSince={uniqueLocationsFirstVisitSince}
      />

      <BestResultsModal open={bestResultsOpen} onClose={() => setBestResultsOpen(false)} />

      <PersonalRecordsModal
        open={personalRecordsOpen}
        onClose={() => setPersonalRecordsOpen(false)}
      />

      <WinsModal
        open={winsOpen}
        onClose={() => setWinsOpen(false)}
        scope={analytics.wins_scope ?? "absolute"}
      />

      <VolunteerRolesModal open={volunteerRolesOpen} onClose={() => setVolunteerRolesOpen(false)} />

      <HomeDistanceModal open={homeDistanceOpen} onClose={() => setHomeDistanceOpen(false)} />

      <LocationRecordsModal
        open={locationRecordsOpen}
        onClose={() => setLocationRecordsOpen(false)}
        kind="course"
        block={analytics.location_records}
      />

      <LocationRecordsModal
        open={ageGroupRecordsOpen}
        onClose={() => setAgeGroupRecordsOpen(false)}
        kind="age_group"
        block={analytics.age_group_records}
      />

      <RegionsCitiesModal
        open={regionsCitiesOpen}
        onClose={() => setRegionsCitiesOpen(false)}
        activityFilter={modalActivity}
        groupBy={regionsCitiesGroupBy}
      />
    </section>
  );
}
