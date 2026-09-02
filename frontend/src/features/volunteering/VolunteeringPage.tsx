import { useCallback, useEffect, useMemo, useState } from "react";
import {
  FilterGroup,
  FilterPanel,
  FilterRow,
} from "../../components/filters/FilterPanel";
import { ActivityTableCols } from "../../components/activityTable/ActivityTableCols";
import { CheckboxListFilter } from "../../components/activityTable/CheckboxListFilter";
import { ColumnHeader } from "../../components/activityTable/ColumnHeader";
import { ActivityDateLink } from "../../components/ActivityDateLink";
import { AppShell } from "../../components/AppShell";
import { EmptyActivityState } from "../../components/EmptyActivityState";
import { LocationNameLink } from "../../components/LocationNameLink";
import { PlatformBadge } from "../../components/PlatformBadge";
import { RateRunModal } from "../../components/RateRunModal";
import { RunRatingStar } from "../../components/RunRatingStar";
import { Snackbar } from "../../components/Snackbar";
import { useSnackbar } from "../../hooks/useSnackbar";
import { useVolunteeringFilters } from "../../hooks/useVolunteeringFilters";
import { useNarrowViewport } from "../../components/tableUx/useNarrowViewport";
import { TableWrap } from "../../components/tableUx/TableWrap";
import {
  getEligibleRuns,
  getMyRatings,
  listProfileLinks,
  type EligibleRun,
  type MyRating,
  type VolunteeringItem,
} from "../../lib/api";
import { useAppDataSource } from "../../lib/appDataSource";
import { useOptionalUser } from "../../lib/useOptionalUser";
import { createFullSelection, sortVolunteering, toggleDateSort, uniquePlatforms } from "../../lib/activityList";
import { formatInt, platformCodeLabel } from "../../lib/format";
import { ShareRowButton } from "../sharing/ShareRowButton";
import { volunteeringSubject } from "../sharing/subjects";

// bare — отдать только тело страницы, без AppShell: портальный ЛК (/new/*)
// оборачивает контент в собственный каркас с сайдбаром.
function VolunteeringContent({ bare = false }: { bare?: boolean } = {}) {
  const { listVolunteering, mode } = useAppDataSource();
  const [items, setItems] = useState<VolunteeringItem[]>([]);
  const [hasProfileLink, setHasProfileLink] = useState(false);
  const [includeTest, setIncludeTest] = useState(false);
  const [includeDuplicates, setIncludeDuplicates] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // Оценка стартов — только в своём разделе волонтёрств.
  const showRating = mode === "auth";
  const currentUser = useOptionalUser();
  const [ratingsMap, setRatingsMap] = useState<Map<string, MyRating>>(new Map());
  const [canRate, setCanRate] = useState(false);
  // entry_id стартов, доступных к оценке прямо сейчас (правило считает бэк).
  const [eligibleIds, setEligibleIds] = useState<Set<string>>(new Set());
  // Доступные старты по ключу «дата + площадка». Нужны для случая, когда в этот
  // день на площадке была и пробежка: бэкенд намеренно оставляет один старт —
  // как пробежку (см. list_eligible_runs), и волонтёрская строка своего
  // entry_id в списке не находит. Без этой карты она показывала прочерк с
  // подсказкой про 30 дней — неправдой.
  const [eligibleByPlace, setEligibleByPlace] = useState<Map<string, EligibleRun>>(new Map());
  // Уже поставленные оценки ПРОБЕЖЕК по тому же ключу «дата + площадка».
  // Список доступных стартов покрывает только окно 30 дней и добор истории;
  // пробежка старше, но оценённая, в нём отсутствует — а «Пробежки» её звезду
  // показывают. Правило одно: у старта одна оценка, и волонтёрская строка
  // обязана показывать её так же (Дмитрий 02.09.2026).
  const [runRatingsByPlace, setRunRatingsByPlace] = useState<Map<string, MyRating>>(new Map());
  const [ratingsVersion, setRatingsVersion] = useState(0);
  const [activeRun, setActiveRun] = useState<EligibleRun | null>(null);
  const { snackbar, showSnackbar, dismissSnackbar } = useSnackbar();

  // parkrun volunteering: counted but not shown in table
  // счётчик — из parkrun_total_credits (см. parkrun_total_credits в бэкенде,
  // берётся из "Total Credits" профиля parkrun), а не числа строк ролей:
  // одна смена волонтёрства может дать кредит сразу нескольким ролям.
  const tableItems = useMemo(() => items.filter((i) => i.platform_code !== "parkrun"), [items]);
  const parkrunCount = useMemo(() => {
    const rowWithCredits = items.find(
      (i) => i.platform_code === "parkrun" && i.parkrun_total_credits != null,
    );
    if (rowWithCredits) return rowWithCredits.parkrun_total_credits as number;
    return items.filter((i) => i.platform_code === "parkrun").length;
  }, [items]);

  const allPlatforms = useMemo(() => uniquePlatforms(tableItems), [tableItems]);

  const filters = useVolunteeringFilters(tableItems);
  // На телефоне роль и локация схлопываются в одну колонку (роль крупно,
  // система и локация подстрокой), дата остаётся отдельной — по ней нужны
  // сортировка и фильтр. Фильтры роли и локации переезжают в общую шторку.
  const narrowViewport = useNarrowViewport();

  const displayedItems = useMemo(
    () =>
      sortVolunteering(
        includeDuplicates ? filters.filtered : filters.filtered.filter((i) => !i.is_crosslinked),
        filters.sort,
      ),
    [filters.filtered, filters.sort, includeDuplicates],
  );

  const visibleVolCount = useMemo(
    () => (includeDuplicates ? tableItems : tableItems.filter((i) => !i.is_crosslinked)).length,
    [tableItems, includeDuplicates],
  );

  const platformCounts = useMemo(() => {
    const base = includeDuplicates ? tableItems : tableItems.filter((i) => !i.is_crosslinked);
    const counts: Record<string, number> = {};
    for (const item of base) counts[item.platform_code] = (counts[item.platform_code] ?? 0) + 1;
    return allPlatforms.map((code) => ({ code, count: counts[code] ?? 0 }));
  }, [tableItems, includeDuplicates, allPlatforms]);

  const activePlatformFilter =
    filters.platformFilterActive ? [...filters.selectedPlatforms][0] ?? "all" : "all";

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await listVolunteering(includeTest);
      setItems(data);
      if (mode === "public-profile") {
        setHasProfileLink(false);
      } else {
        const links = await listProfileLinks();
        setHasProfileLink(links.length > 0);
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "Не удалось загрузить волонтёрства");
    } finally {
      setLoading(false);
    }
  }, [includeTest, mode, listVolunteering]);

  useEffect(() => {
    void load();
  }, [load]);

  useEffect(() => {
    if (!showRating) {
      return;
    }
    let cancelled = false;
    Promise.all([getMyRatings(), getEligibleRuns()])
      .then(([res, eligible]) => {
        if (cancelled) {
          return;
        }
        setCanRate(res.can_rate);
        setEligibleIds(new Set(eligible.runs.map((entry) => entry.entry_id)));
        setEligibleByPlace(
          new Map(
            eligible.runs.map((entry) => [`${entry.event_date}|${entry.location_name}`, entry]),
          ),
        );
        // В таблице волонтёрств показываем только волонтёрские оценки
        // (run_result_id пуст), keyed по entry_id.
        setRatingsMap(
          new Map(
            res.ratings
              .filter((item) => item.participation_type === "volunteer")
              .map((item) => [item.entry_id, item]),
          ),
        );
        setRunRatingsByPlace(
          new Map(
            res.ratings
              .filter((item) => item.participation_type === "run")
              .map((item) => [`${item.event_date}|${item.location_name}`, item]),
          ),
        );
      })
      .catch(() => {
        // тихо — звёзды просто не покажутся
      });
    return () => {
      cancelled = true;
    };
  }, [showRating, ratingsVersion]);

  const reloadRatings = useCallback(() => setRatingsVersion((v) => v + 1), []);

  // Волонтёрство доступно к оценке, если бэк вернул его в списке доступных.
  const isEligible = useCallback(
    (ratingEntryId: string) => eligibleIds.has(ratingEntryId),
    [eligibleIds],
  );

  /** Старт этого дня на этой площадке, если он оценивается как пробежка.
   *
   * В такой день человек и бежал, и волонтёрил: оценка у старта одна, и бэкенд
   * отдаёт её как пробежку. Оценить из волонтёрств всё равно даём — открываем
   * ту же карточку, просто это будет оценка бегуна. */
  const runEntryFor = useCallback(
    (item: VolunteeringItem): EligibleRun | undefined => {
      const key = `${item.event_date}|${item.location_name}`;
      const entry = eligibleByPlace.get(key);
      if (entry && entry.participation_type === "run") {
        return entry;
      }
      // Пробежка вне окна оценки, но уже оценённая: собираем карточку из
      // самой оценки — та же форма, что строит «Пробежки» в buildEligibleRun.
      const rated = runRatingsByPlace.get(key);
      if (!rated) {
        return undefined;
      }
      return {
        entry_id: rated.entry_id,
        participation_type: "run",
        run_result_id: rated.run_result_id,
        event_date: rated.event_date,
        platform_code: rated.platform_code,
        location_name: rated.location_name,
        location_city: rated.location_city,
        finish_time_display: rated.finish_time_display,
        position: rated.position,
        is_pr: rated.is_pr,
        volunteer_role: null,
        event_url: rated.event_url,
        my_rating: rated,
      };
    },
    [eligibleByPlace, runRatingsByPlace],
  );

  /** Оценка из карточки доступного старта в виде, который ждёт звезда:
   * контекст (дата, площадка, время) лежит рядом, в самой карточке. */
  const ratingOfEntry = useCallback((entry: EligibleRun | undefined): MyRating | undefined => {
    if (!entry?.my_rating) {
      return undefined;
    }
    return {
      ...entry.my_rating,
      event_date: entry.event_date,
      platform_code: entry.platform_code,
      location_name: entry.location_name,
      location_city: entry.location_city,
      finish_time_display: entry.finish_time_display,
      position: entry.position,
      is_pr: entry.is_pr,
      event_url: entry.event_url ?? null,
    };
  }, []);

  const buildEligibleRun = useCallback(
    (item: VolunteeringItem, rating: MyRating | undefined): EligibleRun => ({
      entry_id: item.rating_entry_id ?? "",
      participation_type: "volunteer",
      run_result_id: null,
      event_date: item.event_date,
      platform_code: item.platform_code,
      location_name: item.location_name,
      location_city: item.location_city,
      finish_time_display: null,
      position: null,
      is_pr: false,
      volunteer_role: item.role,
      event_url: item.event_url ?? null,
      my_rating: rating ?? null,
    }),
    [],
  );

  const showEmpty = !loading && !error && items.length === 0;
  const dateSortActive = filters.sort === "date_desc" || filters.sort === "date_asc";

  const pageBody = (
    <>
      {loading && <p className="muted">Загрузка…</p>}

      {error && (
        <div className="card error">
          <p>{error}</p>
          <button type="button" className="btn secondary" onClick={() => void load()}>
            Повторить
          </button>
        </div>
      )}

      {showEmpty && (
        <EmptyActivityState
          activityLabel="Волонтёрств"
          ownerHint="Попробуйте себя волонтёром на ближайшем старте — и записи появятся здесь."
          publicHint="Как только появится первое волонтёрство, оно окажется здесь."
          hasProfileLink={hasProfileLink}
          isPublicProfile={mode === "public-profile"}
        />
      )}

      {!loading && !error && items.length > 0 && (
        <>
          <FilterPanel>
            <FilterRow>
              <FilterGroup label="Система">
          <div className="map-mode-tabs" role="tablist" aria-label="Фильтр по системам">
            <button
              type="button"
              role="tab"
              aria-selected={activePlatformFilter === "all"}
              className={activePlatformFilter === "all" ? "map-mode-tab active" : "map-mode-tab"}
              onClick={() => filters.setSelectedPlatforms(createFullSelection(allPlatforms))}
            >
              Все ({formatInt(visibleVolCount)})
            </button>
            {platformCounts.map(({ code, count }) => (
              <button
                key={code}
                type="button"
                role="tab"
                aria-selected={activePlatformFilter === code}
                className={activePlatformFilter === code ? "map-mode-tab active" : "map-mode-tab"}
                onClick={() => filters.setSelectedPlatforms(new Set([code]))}
              >
                {platformCodeLabel(code)} ({formatInt(count)})
              </button>
            ))}
            {parkrunCount > 0 && (
              <button
                type="button"
                role="tab"
                aria-selected={false}
                className="map-mode-tab"
                disabled
                title="Волонтёрства parkrun учтены в общей статистике, но не отображаются в таблице"
              >
                parkrun ({formatInt(parkrunCount)})
              </button>
            )}
          </div>
              </FilterGroup>
              {/* Видны и в демо (main убрал !isDemo 31.08.2026): гость тоже
                  волен смотреть тестовые и незачётные. */}
              <FilterGroup label="Показывать">
                  <div className="loc-index-filters">
                    <label className="loc-index-paused">
                      <input
                        type="checkbox"
                        checked={includeTest}
                        onChange={(event) => setIncludeTest(event.target.checked)}
                      />{" "}
                      тестовые
                    </label>
                    <label className="loc-index-paused">
                      <input
                        type="checkbox"
                        checked={includeDuplicates}
                        onChange={(event) => setIncludeDuplicates(event.target.checked)}
                      />{" "}
                      незачётные
                    </label>
                  </div>
              </FilterGroup>
            </FilterRow>
          </FilterPanel>

          {narrowViewport ? (
            <TableWrap>
              <table className="data-table data-table-filterable data-table-vol-mobile">
                <colgroup>
                  <col className="col-date" />
                  <col />
                  {showRating && <col className="col-rating" />}
                </colgroup>
                <thead>
                  <tr>
                    <ColumnHeader
                      label="Дата"
                      filterActive={filters.dateFilterActive}
                      sortActive={dateSortActive}
                      sortAsc={filters.sort === "date_asc"}
                      onSort={() => filters.setSort((current) => toggleDateSort(current))}
                      filterTitle="Фильтр по дате"
                      filterContent={
                        <div className="date-filter-fields">
                          <label className="filter-field">
                            <span className="filter-field-label">С</span>
                            <input
                              className="filter-field-input"
                              type="date"
                              value={filters.dateFrom}
                              onChange={(event) => filters.setDateFrom(event.target.value)}
                            />
                          </label>
                          <label className="filter-field">
                            <span className="filter-field-label">По</span>
                            <input
                              className="filter-field-input"
                              type="date"
                              value={filters.dateTo}
                              onChange={(event) => filters.setDateTo(event.target.value)}
                            />
                          </label>
                        </div>
                      }
                      filterFooter={
                        filters.dateFilterActive ? (
                          <button
                            type="button"
                            className="filter-popover-link"
                            onClick={() => {
                              filters.setDateFrom("");
                              filters.setDateTo("");
                            }}
                          >
                            Сбросить фильтр
                          </button>
                        ) : undefined
                      }
                    />
                    {/* Роль и локация схлопнуты в одну колонку — фильтры обоих
                        живут в общей шторке. Система не нужна: она фильтруется
                        табами над таблицей. */}
                    <ColumnHeader
                      label="Волонтёрство"
                      filterActive={filters.roleFilterActive || filters.locationFilterActive}
                      filterTitle="Роль и локация"
                      filterContent={
                        <div className="filter-groups">
                          <div className="filter-group">
                            <p className="filter-group-title">Роль</p>
                            <CheckboxListFilter
                              options={filters.roleOptions}
                              selected={filters.selectedRoles}
                              onSelectedChange={filters.setSelectedRoles}
                              searchPlaceholder="Поиск роли…"
                            />
                          </div>
                          <div className="filter-group">
                            <p className="filter-group-title">Локация</p>
                            <CheckboxListFilter
                              options={filters.locationOptions}
                              selected={filters.selectedLocations}
                              onSelectedChange={filters.setSelectedLocations}
                              searchPlaceholder="Поиск локации…"
                            />
                          </div>
                        </div>
                      }
                    />
                    {showRating && (
                      <ColumnHeader
                        label="★"
                        filterable={false}
                        headerTitle="Оценка старта — жёлтая звезда, если вы оценили"
                      />
                    )}
                  </tr>
                </thead>
                <tbody>
                  {displayedItems.length === 0 ? (
                    <tr>
                      <td colSpan={showRating ? 3 : 2} className="table-empty-cell">
                        <span className="muted">Нет строк по фильтрам</span>
                        {filters.hasActiveFilters && (
                          <button
                            type="button"
                            className="btn btn-ghost btn-sm table-empty-reset"
                            onClick={filters.resetFilters}
                          >
                            Сбросить
                          </button>
                        )}
                      </td>
                    </tr>
                  ) : (
                    displayedItems.map((item, index) => {
                      const rating = item.rating_entry_id
                        ? ratingsMap.get(item.rating_entry_id)
                        : undefined;
                      const ownEligible =
                        !!item.rating_entry_id && !item.is_crosslinked && isEligible(item.rating_entry_id);
                      // Своей строки в списке нет — возможно, старт этого дня оценивается
                      // как пробежка. Тогда звезду показываем и открываем её карточку.
                      const viaRun =
                        ownEligible || item.is_crosslinked ? undefined : runEntryFor(item);
                      const shownRating = rating ?? ratingOfEntry(viaRun);
                      const canCreate = canRate && !shownRating && (ownEligible || !!viaRun);
                      return (
                        <tr
                          key={`${item.platform_code}-${item.event_date}-${item.location_name}-${index}`}
                          className={item.is_crosslinked ? "run-crosslinked" : undefined}
                        >
                          <td className="td-date">
                            <ActivityDateLink date={item.event_date} target={item} url={item.event_url} />
                            {item.is_test_event && <span className="badge">тест</span>}
                            {item.is_crosslinked && (
                              <span className="badge badge-crosslinked">не в зачёте</span>
                            )}
                          </td>
                          <td className="td-vol">
                            <div className="td-vol-role">{item.role ?? "—"}</div>
                            <div className="td-vol-sub">
                              <PlatformBadge code={item.platform_code} />{" "}
                              <LocationNameLink
                                name={item.location_name}
                                slug={item.location_slug}
                              />
                            </div>
                          </td>
                          {showRating && (
                            <td className="td-rating">
                              <span className="s2-row-actions">
                                <RunRatingStar
                                  rating={shownRating}
                                  canCreate={canCreate}
                                  canRate={canRate}
                                  naTitle={
                                    item.is_crosslinked
                                      ? "Старт не в зачёте — оценка не ставится"
                                      : "Оценить можно только старты за последние 30 дней"
                                  }
                                  onOpen={() => setActiveRun(viaRun ?? buildEligibleRun(item, shownRating))}
                                />
                                <ShareRowButton
                                  subject={volunteeringSubject(item, currentUser ?? null)}
                                  entry="volunteering"
                                />
                              </span>
                            </td>
                          )}
                        </tr>
                      );
                    })
                  )}
                </tbody>
              </table>
            </TableWrap>
          ) : (
          <div className="table-wrap">
            <table className="data-table data-table-filterable data-table-layout-fixed data-table-volunteering">
              <ActivityTableCols variant="volunteering" withRating={showRating} />
              <thead>
                <tr>
                  <ColumnHeader
                    label="Дата"
                    filterActive={filters.dateFilterActive}
                    sortActive={dateSortActive}
                    sortAsc={filters.sort === "date_asc"}
                    onSort={() => filters.setSort((current) => toggleDateSort(current))}
                    filterTitle="Фильтр по дате"
                    filterContent={
                      <div className="date-filter-fields">
                        <label className="filter-field">
                          <span className="filter-field-label">С</span>
                          <input
                            className="filter-field-input"
                            type="date"
                            value={filters.dateFrom}
                            onChange={(event) => filters.setDateFrom(event.target.value)}
                          />
                        </label>
                        <label className="filter-field">
                          <span className="filter-field-label">По</span>
                          <input
                            className="filter-field-input"
                            type="date"
                            value={filters.dateTo}
                            onChange={(event) => filters.setDateTo(event.target.value)}
                          />
                        </label>
                      </div>
                    }
                    filterFooter={
                      filters.dateFilterActive ? (
                        <button
                          type="button"
                          className="filter-popover-link"
                          onClick={() => {
                            filters.setDateFrom("");
                            filters.setDateTo("");
                          }}
                        >
                          Сбросить фильтр
                        </button>
                      ) : undefined
                    }
                  />
                  <ColumnHeader
                    label="Система"
                    filterable={false}
                  />
                  <ColumnHeader
                    label="Локация"
                    filterActive={filters.locationFilterActive}
                    filterTitle="Фильтр по локации"
                    filterContent={
                      <CheckboxListFilter
                        options={filters.locationOptions}
                        selected={filters.selectedLocations}
                        onSelectedChange={filters.setSelectedLocations}
                        searchPlaceholder="Поиск локации…"
                      />
                    }
                  />
                  <ColumnHeader
                    label="Роль"
                    filterActive={filters.roleFilterActive}
                    filterTitle="Фильтр по роли"
                    filterContent={
                      <CheckboxListFilter
                        options={filters.roleOptions}
                        selected={filters.selectedRoles}
                        onSelectedChange={filters.setSelectedRoles}
                        searchPlaceholder="Поиск роли…"
                      />
                    }
                  />
                  {showRating && (
                    <ColumnHeader
                      label="★"
                      filterable={false}
                      headerTitle="Оценка старта — жёлтая звезда, если вы оценили"
                    />
                  )}
                </tr>
              </thead>
              <tbody>
                {displayedItems.length === 0 ? (
                  <tr>
                    <td colSpan={showRating ? 5 : 4} className="table-empty-cell">
                      <span className="muted">Нет строк по фильтрам</span>
                      {filters.hasActiveFilters && (
                        <button
                          type="button"
                          className="btn btn-ghost btn-sm table-empty-reset"
                          onClick={filters.resetFilters}
                        >
                          Сбросить
                        </button>
                      )}
                    </td>
                  </tr>
                ) : (
                  displayedItems.map((item, index) => (
                    <tr
                      key={`${item.platform_code}-${item.event_date}-${item.location_name}-${index}`}
                      className={item.is_crosslinked ? "run-crosslinked" : undefined}
                    >
                      <td className="td-date">
                        <ActivityDateLink date={item.event_date} target={item} url={item.event_url} />
                        {item.is_test_event && <span className="badge">тест</span>}
                        {item.is_crosslinked && (
                          <span
                            className="badge badge-crosslinked"
                            title="Волонтёрство не учтено в общем зачёте — в этот день учтено волонтёрство по другой системе на той же локации"
                          >
                            не в зачёте
                          </span>
                        )}
                      </td>
                      <td className="td-platform">
                        <PlatformBadge code={item.platform_code} />
                      </td>
                      <td className="td-location">
                        <LocationNameLink name={item.location_name} slug={item.location_slug} />
                      </td>
                      <td className="td-role">{item.role ?? "—"}</td>
                      {showRating &&
                        (() => {
                          const rating = item.rating_entry_id
                            ? ratingsMap.get(item.rating_entry_id)
                            : undefined;
                          const ownEligible =
                            !!item.rating_entry_id && !item.is_crosslinked && isEligible(item.rating_entry_id);
                          // Своей строки в списке нет — возможно, старт этого дня оценивается
                          // как пробежка. Тогда звезду показываем и открываем её карточку.
                          const viaRun =
                            ownEligible || item.is_crosslinked ? undefined : runEntryFor(item);
                          const shownRating = rating ?? ratingOfEntry(viaRun);
                          const canCreate = canRate && !shownRating && (ownEligible || !!viaRun);
                          return (
                            <td className="td-rating">
                              <span className="s2-row-actions">
                                <RunRatingStar
                                  rating={shownRating}
                                  canCreate={canCreate}
                                  canRate={canRate}
                                  naTitle={
                                    item.is_crosslinked
                                      ? "Старт не в зачёте — оценка не ставится"
                                      : "Оценить можно только старты за последние 30 дней"
                                  }
                                  onOpen={() => setActiveRun(viaRun ?? buildEligibleRun(item, shownRating))}
                                />
                                <ShareRowButton
                                  subject={volunteeringSubject(item, currentUser ?? null)}
                                  entry="volunteering"
                                />
                              </span>
                            </td>
                          );
                        })()}
                    </tr>
                  ))
                )}
              </tbody>
            </table>
          </div>
          )}

          <p className="table-foot muted">
            <span>
              Показано: {formatInt(displayedItems.length)} из {formatInt(visibleVolCount)}
            </span>
            {filters.hasActiveFilters && (
              <button type="button" className="btn btn-ghost btn-sm" onClick={filters.resetAll}>
                Сбросить всё
              </button>
            )}
          </p>
        </>
      )}

      {activeRun && (
        <RateRunModal
          run={activeRun}
          onClose={() => setActiveRun(null)}
          onSaved={() => {
            reloadRatings();
            setActiveRun(null);
            showSnackbar({ variant: "default", title: "Спасибо!", message: "Отзыв сохранён" });
          }}
          onDeleted={() => {
            reloadRatings();
            setActiveRun(null);
          }}
        />
      )}

      <Snackbar open={snackbar.open} title={snackbar.title} variant={snackbar.variant} onDismiss={dismissSnackbar}>
        {snackbar.message}
      </Snackbar>
    </>
  );

  if (bare || mode === "public-profile") {
    return <>{pageBody}</>;
  }

  return <AppShell title="Волонтёрство">{pageBody}</AppShell>;
}

export { VolunteeringContent };

