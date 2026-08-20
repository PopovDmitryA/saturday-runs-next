import { useCallback, useEffect, useMemo, useState } from "react";
import { LocationMap } from "../../components/LocationMap";
import { getCatalogLocationsMap, type MapLocationPoint } from "../../lib/api";
import { formatInt, pluralFormRu } from "../../lib/format";
import { MAX_SELECTED_LOCATIONS, type TouristMapState } from "./useTouristMap";

type TouristMapPanelProps = {
  state: TouristMapState;
  /** «бегали» / «волонтёрили» — рейтинг решает, чем набирается визит. */
  verb: string;
  /** Прокрутить страницу к таблице со светофорами. */
  onShowTable: () => void;
};

function visitorsLabel(count: number): string {
  return `${formatInt(count)} ${pluralFormRu(count, ["человек", "человека", "человек"])}`;
}

/**
 * Содержимое спойлера «Карта туристов»: обычная карта локаций, но с числом
 * рядом с каждой точкой — сколько человек из верхушки рейтинга там было.
 * Клик по точке добавляет её столбец светофоров в таблицу (state.toggle).
 */
export function TouristMapPanel({ state, verb, onShowTable }: TouristMapPanelProps) {
  const [points, setPoints] = useState<MapLocationPoint[] | null>(null);
  const [pointsError, setPointsError] = useState<string | null>(null);
  const [isFullscreen, setIsFullscreen] = useState(false);
  // Показывать ли недействующие площадки: «Не действует» (пауза) и закрытые.
  const [withInactive, setWithInactive] = useState(true);
  const {
    countsByIdentity,
    selectedKeys,
    selected,
    toggle,
    clear,
    atLimit,
    map,
    loading,
    error,
    topSteps,
    topLimit,
    setTopLimit,
  } = state;
  // Глубина светофоров в таблице — всегда вся таблица; фильтр ниже меняет
  // только числа у точек.
  const tableLimit = map?.limit ?? 1000;

  useEffect(() => {
    let cancelled = false;
    getCatalogLocationsMap()
      .then((data) => {
        if (!cancelled) {
          setPoints(data.points);
        }
      })
      .catch(() => {
        if (!cancelled) {
          setPointsError("Не удалось загрузить карту локаций");
        }
      });
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    if (!isFullscreen) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") setIsFullscreen(false);
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [isFullscreen]);

  // «Только действующие» убирает с карты закрытые площадки и те, что не
  // действуют: их числа — история, а не ответ на вопрос «куда ездят сейчас».
  const visiblePoints = useMemo(() => {
    if (!points || withInactive) {
      return points;
    }
    return points.filter((point) => !point.is_paused && !point.is_cancelled);
  }, [points, withInactive]);

  // Подпись в попапе точки: на тачскринах всплывающей подсказки нет, и попап —
  // единственное место, где число можно прочитать словами.
  const countLabel = useCallback(
    (count: number) =>
      count > 0
        ? `Здесь ${verb} ${visitorsLabel(count)} из топ-${topLimit}`
        : `Из топ-${topLimit} здесь не был никто`,
    [topLimit, verb],
  );

  // Кнопка в попапе: площадку клик уже выбрал, остаётся довезти человека до
  // таблицы — из полноэкранной карты сначала выходим.
  const showDetails = useCallback(() => {
    setIsFullscreen(false);
    onShowTable();
  }, [onShowTable]);

  const legend = (
    <div className="map-legend lb-tourist-legend">
      <span className="map-legend-item">
        {/* Образец не должен противоречить ступени: при «топ-10» число 12
            в легенде читалось как ошибка. */}
        <span className="lb-tourist-legend-badge">{Math.min(12, topLimit)}</span>
        человек из топ-{topLimit} были здесь
      </span>
      <span className="map-legend-item">
        <span className="lb-tourist-legend-cluster">7</span>
        столько локаций рядом — приблизьте
      </span>
      <span className="map-legend-item muted">Клик по точке — столбец в таблице</span>
    </div>
  );

  return (
    <div className="lb-tourist-map">
      <p className="lb-tourist-caption muted">
        Число у точки — сколько человек из топ-{topLimit} рейтинга здесь {verb}. Нажимайте
        на точки: каждая добавит в таблицу свой столбец «Был здесь» — зелёный, если был,
        красный, если нет. Светофоры в столбцах считаются по всей таблице (топ-
        {tableLimit}), какую бы ступень вы ни выбрали.
      </p>

      {/* Ступень влияет только на числа у точек: «сколько человек из топ-50
          сюда доехало» — вопрос про элиту туризма, а не про всю тысячу. */}
      <div className="lb-tourist-top">
        <span className="lb-tourist-top-label muted">Показывать по</span>
        <div className="lb-gender-tabs" role="group" aria-label="Какой топ считать на карте">
          {topSteps.map((step) => (
            <button
              key={step}
              type="button"
              aria-pressed={topLimit === step}
              className={`lb-gender-tab${topLimit === step ? " lb-gender-tab-active" : ""}`}
              onClick={() => setTopLimit(step)}
            >
              топ-{step}
            </button>
          ))}
        </div>
        {/* Недействующие площадки (пауза и закрытые) на карте нужны не всегда:
            их числа — история, а не «куда ездят сейчас». */}
        <span className="lb-tourist-top-label muted">Локации</span>
        <div className="lb-gender-tabs" role="group" aria-label="Какие локации показывать">
          <button
            type="button"
            aria-pressed={withInactive}
            className={`lb-gender-tab${withInactive ? " lb-gender-tab-active" : ""}`}
            onClick={() => setWithInactive(true)}
          >
            Все
          </button>
          <button
            type="button"
            aria-pressed={!withInactive}
            className={`lb-gender-tab${!withInactive ? " lb-gender-tab-active" : ""}`}
            onClick={() => setWithInactive(false)}
          >
            Только действующие
          </button>
        </div>
      </div>

      {(loading || !points) && !error && !pointsError && (
        <p className="muted">Считаем карту туристов…</p>
      )}
      {(error || pointsError) && (
        <div className="lb-error">
          <p>{error ?? pointsError}</p>
          <button type="button" className="btn btn-sm" onClick={state.reload}>
            Повторить
          </button>
        </div>
      )}

      {selected.length > 0 && (
        <div className="lb-tourist-selected">
          <span className="lb-tourist-selected-label muted">
            В таблице ({selected.length}):
          </span>
          {selected.map(({ location }) => (
            <button
              key={location.key}
              type="button"
              className="lb-tourist-chip"
              title="Убрать столбец из таблицы"
              onClick={() => toggle(location.key)}
            >
              {location.name}
              <span className="lb-tourist-chip-count">
                {location.visitors > 0 ? visitorsLabel(location.visitors) : "никого"}
              </span>
              <span className="lb-tourist-chip-remove" aria-hidden>
                ×
              </span>
            </button>
          ))}
          <button type="button" className="btn btn-ghost btn-sm" onClick={onShowTable}>
            К таблице ↓
          </button>
          <button type="button" className="btn btn-ghost btn-sm" onClick={clear}>
            Очистить
          </button>
        </div>
      )}

      {atLimit && (
        <p className="lb-tourist-limit muted">
          Больше {MAX_SELECTED_LOCATIONS}{" "}
          {pluralFormRu(MAX_SELECTED_LOCATIONS, ["столбца", "столбцов", "столбцов"])} таблица
          не покажет — уберите лишнюю локацию, чтобы добавить новую.
        </p>
      )}

      {visiblePoints && map && (
        <div className={isFullscreen ? "map-panel-fullscreen" : undefined}>
          <LocationMap
            points={visiblePoints}
            variant="catalog"
            emptyMessage="Локации не найдены"
            legend={legend}
            countsByIdentity={countsByIdentity}
            selectedIdentities={selectedKeys}
            onSelectPoint={toggle}
            onShowDetails={showDetails}
            countLabel={countLabel}
            isFullscreen={isFullscreen}
            onToggleFullscreen={() => setIsFullscreen((value) => !value)}
          />
        </div>
      )}
    </div>
  );
}
