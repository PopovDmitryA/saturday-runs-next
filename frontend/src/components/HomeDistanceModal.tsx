import { useEffect, useMemo, useState } from "react";
import { DetailModal } from "./DetailModal";
import { FilterGroup, FilterPanel, FilterRow } from "./filters/FilterPanel";
import { PlatformFilter } from "./filters/PlatformFilter";
import { PlatformBadge } from "./PlatformBadge";
import { setTourismPlatforms, type HomeDistanceDetail, type HomeDistanceLocation } from "../lib/api";
import { useAppDataSource } from "../lib/appDataSource";
import { formatInt, formatKm, platformCodeLabel } from "../lib/format";

// Порядок систем в фильтре — как у бейджей по всему сайту.
const PLATFORM_ORDER = ["five_verst", "s95", "parkrun", "runpark"];

function orderedPlatformCodes(rows: HomeDistanceLocation[]): string[] {
  const present = new Set(rows.flatMap((row) => row.platform_codes));
  const known = PLATFORM_ORDER.filter((code) => present.has(code));
  const rest = [...present].filter((code) => !PLATFORM_ORDER.includes(code)).sort();
  return [...known, ...rest];
}

const HINT =
  "Расстояние по прямой от домашней локации до локации. Каждая точка даёт свои " +
  "километры один раз, сколько бы раз вы туда ни ездили. Домашняя локация меняется " +
  "в настройках.";

// В чужом профиле те же цифры, но про другого человека: «вы» и совет заглянуть
// в настройки там не к месту.
const PUBLIC_HINT =
  "Расстояние по прямой от домашней локации участника до локации. Каждая точка " +
  "даёт свои километры один раз, сколько бы раз он туда ни ездил.";

/** Город, а регион — только если он не повторяет город (у Москвы и Питера они совпадают). */
function placeSubtitle(row: HomeDistanceLocation): string {
  const parts = [row.city];
  if (row.region && row.region !== row.city) {
    parts.push(row.region);
  }
  return parts.filter(Boolean).join(", ");
}

function LocationCell({ row }: { row: HomeDistanceLocation }) {
  const subtitle = placeSubtitle(row);
  return (
    <span className="home-distance-place">
      {row.location_slug ? (
        <a href={`/locations/${row.location_slug}`}>{row.name}</a>
      ) : (
        <span>{row.name}</span>
      )}
      {subtitle && (
        <span className="muted home-distance-place-sub"> · {subtitle}</span>
      )}
      {row.platform_codes.length > 0 && (
        <span className="home-distance-place-badges">
          {row.platform_codes.map((code) => (
            <PlatformBadge key={code} code={code} />
          ))}
        </span>
      )}
    </span>
  );
}

function DistanceCell({ row }: { row: HomeDistanceLocation }) {
  if (row.is_home) {
    return <span className="home-distance-home-badge">дом</span>;
  }
  if (row.distance_km == null) {
    // Закрытые зарубежные локации, которых нет в мировом каталоге parkrun:
    // координат нет, поэтому и в зачёт километров они не идут.
    return <span className="muted">нет координат</span>;
  }
  return <span className="num">{formatKm(row.distance_km)}</span>;
}

/**
 * С какой таблицы начинать. Модалку открывают две плитки «Бегового туризма»:
 * «дальность от дома» — про прошлое (где был), «ближайшая новая локация» —
 * про будущее (куда дальше). Данные одни, но первым идёт тот блок, ради
 * которого нажали: иначе за списком «куда дальше» приходилось листать в самый
 * низ (замечание Дмитрия 08.09.2026).
 */
export type HomeDistanceFocus = "visited" | "unvisited";

export function HomeDistanceModal({
  open,
  onClose,
  focus = "visited",
  onPreferencesChanged,
}: {
  open: boolean;
  onClose: () => void;
  focus?: HomeDistanceFocus;
  /** Сохранили системы для плитки «Куда дальше» — сводке пора обновиться. */
  onPreferencesChanged?: () => void;
}) {
  // В чужом профиле источник подменён на публичный эндпоинт владельца профиля:
  // раньше модалка звала личный и показывала километры смотрящего.
  const { getHomeDistanceDetail, mode } = useAppDataSource();
  const isPublicProfile = mode === "public-profile";
  const [data, setData] = useState<HomeDistanceDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  // Фильтр «Куда дальше» по системе: кто-то коллекционирует только 5 вёрст
  // (Дмитрий, 08.09.2026). "all" — все системы. Стартует с того, что человек
  // сохранил для плитки, и его же можно сохранить кнопкой ниже.
  const [platformFilter, setPlatformFilter] = useState<string>("all");
  const [savingFilter, setSavingFilter] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);

  useEffect(() => {
    if (!open) {
      return;
    }
    setData(null);
    setError(null);
    let cancelled = false;
    getHomeDistanceDetail()
      .then((payload) => {
        if (!cancelled) {
          setData(payload);
          setPlatformFilter(payload.tourism_platforms?.[0] ?? "all");
          setSaveError(null);
        }
      })
      .catch((err: unknown) => {
        if (!cancelled) {
          setError(
            err instanceof Error
              ? err.message
              : "Не удалось загрузить дальность",
          );
        }
      });
    return () => {
      cancelled = true;
    };
  }, [open, getHomeDistanceDetail]);

  const platformCodes = useMemo(() => orderedPlatformCodes(data?.unvisited ?? []), [data]);
  const unvisitedRows = useMemo(
    () =>
      (data?.unvisited ?? []).filter(
        (row) => platformFilter === "all" || row.platform_codes.includes(platformFilter),
      ),
    [data, platformFilter],
  );
  // Что сохранено для плитки: пусто — все системы.
  const savedFilter = data?.tourism_platforms?.[0] ?? "all";
  const filterIsSaved = savedFilter === platformFilter;

  const applyFilterToTile = async () => {
    setSavingFilter(true);
    setSaveError(null);
    try {
      const result = await setTourismPlatforms(platformFilter === "all" ? [] : [platformFilter]);
      setData((prev) => (prev ? { ...prev, tourism_platforms: result.platforms } : prev));
      onPreferencesChanged?.();
    } catch (err: unknown) {
      setSaveError(err instanceof Error ? err.message : "Не удалось сохранить");
    } finally {
      setSavingFilter(false);
    }
  };

  // Таблицы собраны заранее, чтобы ниже только выбрать порядок. data может
  // ещё не приехать — тогда они не рисуются вовсе (см. условие в разметке).
  const visitedTable = (
    <>
      <h3 className="home-distance-table-title">
        {isPublicProfile ? "Где участник был" : "Где вы были"}
      </h3>
      <div className="unique-locations-table-wrap">
        <table className="data-table unique-locations-table home-distance-table">
          <thead>
            <tr>
              <th>Локация</th>
              <th className="col-num">В зачёте</th>
              <th className="col-num">Пробежек</th>
            </tr>
          </thead>
          <tbody>
            {(data?.visited ?? []).map((row) => (
              <tr
                key={row.catalog_identity_key}
                className={row.is_home ? "home-distance-row-home" : undefined}
              >
                <td>
                  <LocationCell row={row} />
                </td>
                <td className="col-num">
                  <DistanceCell row={row} />
                </td>
                <td className="col-num num">{formatInt(row.run_count)}</td>
              </tr>
            ))}
            {data?.visited.length === 0 && (
              <tr>
                <td colSpan={3} className="muted">
                  Пока нет пробежек.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </>
  );
  const unvisitedTable = (
    <>
      <h3 className="home-distance-table-title">
        {isPublicProfile ? "Где участник ещё не был" : "Где вы ещё не были"}
      </h3>
      <p className="muted home-distance-table-note">
        Действующие локации, до которых вы пока не доехали, — от ближней к
        дальней.
      </p>
      {platformCodes.length > 1 && (
        <FilterPanel>
          <FilterRow>
            <PlatformFilter
              mode="single"
              value={platformFilter}
              onChange={setPlatformFilter}
              options={platformCodes.map((code) => ({ code, label: platformCodeLabel(code) }))}
            />
            {/* Закрепить срез за плиткой — только у себя: в чужом профиле это
                настройка владельца, гостю она недоступна. Своей группой в том
                же ряду, чтобы подпись и кнопка встали на общие горизонтали. */}
            {!isPublicProfile && (
              <FilterGroup label="Плитка «Куда дальше»">
                {filterIsSaved ? (
                  <span className="home-distance-filter-note muted">
                    {platformFilter === "all" ? "все системы" : platformCodeLabel(platformFilter)}
                  </span>
                ) : (
                  <button
                    type="button"
                    className="btn secondary btn-sm"
                    disabled={savingFilter}
                    onClick={() => void applyFilterToTile()}
                  >
                    {platformFilter === "all"
                      ? "Показывать все системы"
                      : `Показывать только ${platformCodeLabel(platformFilter)}`}
                  </button>
                )}
              </FilterGroup>
            )}
          </FilterRow>
          {saveError && <p className="error-text">{saveError}</p>}
        </FilterPanel>
      )}
      <div className="unique-locations-table-wrap">
        <table className="data-table unique-locations-table home-distance-table home-distance-table-unvisited">
          <thead>
            <tr>
              <th>Локация</th>
              <th className="col-num">От дома</th>
            </tr>
          </thead>
          <tbody>
            {unvisitedRows.map((row) => (
              <tr key={row.catalog_identity_key}>
                <td>
                  <LocationCell row={row} />
                </td>
                <td className="col-num">
                  <DistanceCell row={row} />
                </td>
              </tr>
            ))}
            {data && unvisitedRows.length === 0 && (
              <tr>
                <td colSpan={2} className="muted">
                  {platformFilter === "all"
                    ? "Вы побывали на всех действующих локациях."
                    : `В системе ${platformCodeLabel(platformFilter)} непосещённых локаций не осталось.`}
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </>
  );

  return (
    <DetailModal
      open={open}
      title={focus === "unvisited" ? "Куда дальше" : "Дальность от дома"}
      onClose={onClose}
    >
      {error && <p className="error-text">{error}</p>}
      {!error && !data && <p className="muted">Загрузка…</p>}
      {!error && data && (
        <>
          <p className="muted personal-records-hint">
            {isPublicProfile ? PUBLIC_HINT : HINT}
          </p>
          {/* «Куда дальше» — только про будущее: таблица «Где вы были» тут
              дублировала плитку «Локации с пробежками» того же блока
              (Дмитрий, 08.09.2026). У входа «Дальность от дома» она остаётся —
              это единственное место, где видно вклад каждой локации в зачёт. */}
          {focus === "unvisited" ? unvisitedTable : visitedTable}
        </>
      )}
    </DetailModal>
  );
}
