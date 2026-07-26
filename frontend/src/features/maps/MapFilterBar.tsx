import {
  hasActivePlatformFilter,
  isModeTogglePressed,
  type ActivityFilter,
  type MapMode,
  type MapModeToggle,
  type MapView,
  type PlatformFilters,
} from "./mapFilters";

// parkrun последний: своих локаций на карте у него нет (ушёл из России в 2022),
// переключатель управляет только тем, засчитывать ли визиты той эпохи —
// поэтому и подпись отдельная.
const PLATFORM_DEFS = [
  { code: "five_verst" as const, label: "5 вёрст", dot: "map-legend-dot-five-verst", title: undefined },
  { code: "s95" as const, label: "S95", dot: "map-legend-dot-s95", title: undefined },
  { code: "runpark" as const, label: "Runpark", dot: "map-legend-dot-runpark", title: undefined },
  {
    code: "parkrun" as const,
    label: "parkrun",
    dot: "map-legend-dot-parkrun",
    title: "Локации parkrun-эпохи: закрыты с 2022 года",
  },
];

/**
 * Пара независимых тумблеров «Где не был» / «Мои визиты» (мультивыбор, как у
 * систем — с галочками). Используется в общем баре и в тулбаре панели локаций.
 */
export function MapModeToggles({
  mode,
  onToggle,
  disabled = false,
  visitedLabel = "Мои визиты",
}: {
  mode: MapMode;
  onToggle: (which: MapModeToggle) => void;
  disabled?: boolean;
  visitedLabel?: string;
}) {
  const defs: { which: MapModeToggle; label: string }[] = [
    { which: "unvisited", label: "Где не был" },
    { which: "visited", label: visitedLabel },
  ];
  return (
    <div className="map-toolbar-group" role="group" aria-label="Показать на карте">
      {defs.map(({ which, label }) => {
        // На карте регионов тумблеры недоступны: показываем «Мои визиты» нажатым.
        const pressed = disabled ? which === "visited" : isModeTogglePressed(mode, which);
        return (
          <button
            key={which}
            type="button"
            className={pressed ? "map-platform-filter-btn active" : "map-platform-filter-btn"}
            aria-pressed={pressed}
            disabled={disabled}
            title={disabled ? "Карта регионов показывает покрытие ваших визитов" : undefined}
            onClick={() => onToggle(which)}
          >
            <span className="map-toggle-check" aria-hidden>
              ✓
            </span>
            {label}
          </button>
        );
      })}
    </div>
  );
}

type MapFilterBarProps = {
  view: MapView;
  onViewChange: (value: MapView) => void;
  activityFilter: ActivityFilter;
  onActivityChange: (value: ActivityFilter) => void;
  mapMode: MapMode;
  onToggleMode: (which: MapModeToggle) => void;
  platformFilters: PlatformFilters;
  onTogglePlatform: (code: keyof PlatformFilters) => void;
};

/** Единый фильтр-бар карт: вид (Локации/Регионы) + активность + режим + системы. */
export function MapFilterBar({
  view,
  onViewChange,
  activityFilter,
  onActivityChange,
  mapMode,
  onToggleMode,
  platformFilters,
  onTogglePlatform,
}: MapFilterBarProps) {
  return (
    <div className="map-toolbar map-toolbar-shared">
      <div className="map-toolbar-group" role="tablist" aria-label="Вид карты">
        <button
          type="button"
          role="tab"
          aria-selected={view === "locations"}
          className={view === "locations" ? "map-mode-tab active" : "map-mode-tab"}
          onClick={() => onViewChange("locations")}
        >
          Локации
        </button>
        <button
          type="button"
          role="tab"
          aria-selected={view === "regions"}
          className={view === "regions" ? "map-mode-tab active" : "map-mode-tab"}
          onClick={() => onViewChange("regions")}
        >
          Регионы
        </button>
      </div>
      <span className="map-toolbar-sep" aria-hidden />
      <div className="map-toolbar-group" role="tablist" aria-label="Активность">
        <button
          type="button"
          role="tab"
          aria-selected={activityFilter === "runs"}
          className={activityFilter === "runs" ? "map-mode-tab active" : "map-mode-tab"}
          onClick={() => onActivityChange("runs")}
        >
          Пробежки
        </button>
        <button
          type="button"
          role="tab"
          aria-selected={activityFilter === "volunteering"}
          className={activityFilter === "volunteering" ? "map-mode-tab active" : "map-mode-tab"}
          onClick={() => onActivityChange("volunteering")}
        >
          Волонтёрство
        </button>
      </div>
      <span className="map-toolbar-sep" aria-hidden />
      <MapModeToggles mode={mapMode} onToggle={onToggleMode} disabled={view === "regions"} />
      <span className="map-toolbar-sep" aria-hidden />
      <div className="map-toolbar-group" role="group" aria-label="Системы">
        {PLATFORM_DEFS.map(({ code, label, dot, title }) => (
          <button
            key={code}
            type="button"
            className={
              platformFilters[code] ? "map-platform-filter-btn active" : "map-platform-filter-btn"
            }
            onClick={() => onTogglePlatform(code)}
            aria-pressed={platformFilters[code]}
            title={title}
          >
            <span className={`map-legend-dot ${dot}`} aria-hidden />
            {label}
          </button>
        ))}
      </div>
    </div>
  );
}

export function togglePlatform(
  filters: PlatformFilters,
  code: keyof PlatformFilters,
): PlatformFilters {
  const next = { ...filters, [code]: !filters[code] };
  // Хотя бы одна система должна оставаться включённой.
  return hasActivePlatformFilter(next) ? next : filters;
}
