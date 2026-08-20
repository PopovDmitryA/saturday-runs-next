import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  getTouristMap,
  type CountBy,
  type LeaderboardMetric,
  type PlatformFilter,
  type TouristMapLocation,
  type TouristMapResponse,
  type TouristMapVisit,
} from "./leaderboardsApi";

/**
 * Сколько площадок разрешаем держать в таблице одновременно. Столбцы — это
 * ширина: пятнадцать светофоров рядом с именем ещё читаются, дальше таблица
 * превращается в горизонтальную простыню.
 */
export const MAX_SELECTED_LOCATIONS = 15;

/**
 * Ступени фильтра «какой топ считать на карте», пока не приехал ответ с их
 * набором (его задаёт бэкенд — см. top_steps).
 */
export const DEFAULT_TOP_STEPS = [10, 30, 50, 100, 300, 500, 1000];

type Options = {
  metric: LeaderboardMetric;
  /** Спойлер раскрыт — до этого момента карту не грузим вовсе. */
  enabled: boolean;
  minVisits: number;
  platform: PlatformFilter;
  countBy: CountBy;
  roles: string[] | null;
};

/** Выбранная площадка вместе с её светофорами по строкам таблицы. */
export type SelectedLocation = {
  location: TouristMapLocation;
  /** row_key -> визиты. Пусто, пока грузится. */
  visits: Map<string, TouristMapVisit>;
  loading: boolean;
};

export type TouristMapState = {
  map: TouristMapResponse | null;
  loading: boolean;
  error: string | null;
  reload: () => void;
  /** Ключи выбранных площадок — по одному столбцу светофоров на каждую. */
  selectedKeys: string[];
  /** Выбранные площадки в порядке добавления, со своими светофорами. */
  selected: SelectedLocation[];
  /** Клик по точке: добавляет площадку в таблицу либо убирает её оттуда. */
  toggle: (key: string, name?: string) => void;
  clear: () => void;
  atLimit: boolean;
  /** Строки, попавшие в расчёт: у остальных светофор не горит вовсе. */
  coveredRows: Set<string>;
  countsByIdentity: Map<string, number>;
  /**
   * Ступени фильтра «какой топ считать» и выбранная. Фильтр меняет ТОЛЬКО числа
   * у точек: светофоры в таблице всегда считаются по всем строкам рейтинга
   * (решение Дмитрия 19.08.2026).
   */
  topSteps: number[];
  topLimit: number;
  setTopLimit: (value: number) => void;
};

/**
 * Данные карты туристов. Двумя запросами, а не одним: числа по площадкам — это
 * все точки карты (лёгкий ответ), а матрица «участник × площадка» целиком
 * весила бы сотни килобайт, поэтому визиты приходят по выбранной площадке.
 * Каждая добавленная площадка грузится отдельно и остаётся в памяти — снятие и
 * повторный выбор второго запроса уже не стоят.
 */
export function useTouristMap({
  metric,
  enabled,
  minVisits,
  platform,
  countBy,
  roles,
}: Options): TouristMapState {
  const [map, setMap] = useState<TouristMapResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [selectedKeys, setSelectedKeys] = useState<string[]>([]);
  // Имена площадок, на которые нажали: у точки, где не был никто из верхушки,
  // ответ карты пуст — а столбец (все светофоры красные) показать всё равно
  // надо, и заголовку нужно название.
  const [names, setNames] = useState<Record<string, string>>({});
  const [details, setDetails] = useState<Record<string, TouristMapResponse>>({});
  const [pending, setPending] = useState<string[]>([]);
  const [reloadToken, setReloadToken] = useState(0);
  // null — «вся таблица»: пока карта не приехала, глубину её ступеней мы не
  // знаем, а по умолчанию показываем числа по всем строкам.
  const [topLimit, setTopLimit] = useState<number | null>(null);
  // Гонки при быстрых кликах: ответ по площадке, которую уже сняли, игнорируем.
  const requestedRef = useRef(new Set<string>());

  // Массив ролей — новая ссылка на каждый рендер, поэтому в зависимости идёт
  // его строковый ключ (иначе запрос уходил бы бесконечно).
  const rolesKey = roles ? [...roles].sort().join(",") : "";
  const query = useMemo(
    () => ({
      minVisits,
      platform,
      countBy,
      roles: rolesKey ? rolesKey.split(",") : null,
    }),
    [minVisits, platform, countBy, rolesKey],
  );

  useEffect(() => {
    if (!enabled) {
      return;
    }
    let cancelled = false;
    setLoading(true);
    setError(null);
    getTouristMap(metric, query)
      .then((data) => {
        if (!cancelled) {
          setMap(data);
        }
      })
      .catch(() => {
        if (!cancelled) {
          setError("Не удалось загрузить карту туристов");
        }
      })
      .finally(() => {
        if (!cancelled) {
          setLoading(false);
        }
      });
    return () => {
      cancelled = true;
    };
  }, [metric, enabled, query, reloadToken]);

  // Фильтры сменились — прежние светофоры посчитаны по другому срезу рейтинга,
  // их надо перечитать. Сам набор выбранных площадок при этом сохраняем.
  useEffect(() => {
    setDetails({});
    requestedRef.current.clear();
  }, [query, reloadToken]);

  // Догружаем визиты для площадок, которых ещё нет в памяти.
  useEffect(() => {
    if (!enabled) {
      return;
    }
    const missing = selectedKeys.filter((key) => !requestedRef.current.has(key));
    if (missing.length === 0) {
      return;
    }
    for (const key of missing) {
      requestedRef.current.add(key);
    }
    setPending((current) => [...current, ...missing]);
    let cancelled = false;
    Promise.all(
      missing.map((key) =>
        getTouristMap(metric, { ...query, locationKey: key })
          .then((data) => [key, data] as const)
          .catch(() => [key, null] as const),
      ),
    ).then((results) => {
      if (cancelled) {
        return;
      }
      setDetails((current) => {
        const next = { ...current };
        for (const [key, data] of results) {
          if (data) {
            next[key] = data;
          } else {
            // Не сложилось — даём следующему клику попробовать заново.
            requestedRef.current.delete(key);
          }
        }
        return next;
      });
      setPending((current) => current.filter((key) => !missing.includes(key)));
    });
    return () => {
      cancelled = true;
    };
  }, [enabled, metric, query, selectedKeys]);

  // Смена рейтинга — другой набор строк и площадок: выбор сбрасываем.
  useEffect(() => {
    setSelectedKeys([]);
    setNames({});
    setDetails({});
    requestedRef.current.clear();
    setMap(null);
    setTopLimit(null);
  }, [metric]);

  const topSteps = useMemo(
    () => (map?.top_steps?.length ? map.top_steps : DEFAULT_TOP_STEPS),
    [map],
  );
  // По умолчанию — самая широкая ступень: карта открывается такой же, какой
  // была до появления фильтра.
  const effectiveTop = topLimit ?? topSteps[topSteps.length - 1];

  const countsByIdentity = useMemo(() => {
    const counts = new Map<string, number>();
    const widest = topSteps[topSteps.length - 1];
    for (const location of map?.locations ?? []) {
      // visitors_by_top приходит по всем ступеням разом; visitors — запасной
      // вариант для самой широкой ступени и для старых ответов без разбивки.
      const byTop = location.visitors_by_top?.[String(effectiveTop)];
      counts.set(
        location.key,
        byTop ?? (effectiveTop >= widest ? location.visitors : 0),
      );
    }
    return counts;
  }, [map, effectiveTop, topSteps]);

  const coveredRows = useMemo(() => new Set(map?.row_keys ?? []), [map]);

  const selected = useMemo<SelectedLocation[]>(() => {
    return selectedKeys.map((key) => {
      const detail = details[key];
      const known =
        detail?.location ?? map?.locations.find((location) => location.key === key);
      const visits = new Map<string, TouristMapVisit>();
      for (const visit of detail?.visits ?? []) {
        visits.set(visit.row_key, visit);
      }
      return {
        location: known ?? {
          // Площадка, где не был никто из верхушки: в ответе её нет вовсе, но
          // столбец с честными красными светофорами — тоже ответ.
          key,
          name: names[key] ?? "Локация",
          slug: null,
          visitors: 0,
          visits: 0,
        },
        visits,
        loading: pending.includes(key),
      };
    });
  }, [selectedKeys, details, map, names, pending]);

  const toggle = useCallback((key: string, name?: string) => {
    setNames((current) => (name && current[key] !== name ? { ...current, [key]: name } : current));
    setSelectedKeys((current) => {
      if (current.includes(key)) {
        return current.filter((item) => item !== key);
      }
      // Упёрлись в лимит столбцов — молча ничего не добавляем; про предел
      // витрина скажет сама (см. atLimit).
      if (current.length >= MAX_SELECTED_LOCATIONS) {
        return current;
      }
      return [...current, key];
    });
  }, []);

  const clear = useCallback(() => setSelectedKeys([]), []);
  const reload = useCallback(() => setReloadToken((token) => token + 1), []);

  return {
    map,
    loading,
    error,
    reload,
    selectedKeys,
    selected,
    toggle,
    clear,
    atLimit: selectedKeys.length >= MAX_SELECTED_LOCATIONS,
    coveredRows,
    countsByIdentity,
    topSteps,
    topLimit: effectiveTop,
    setTopLimit,
  };
}
