import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  ApiError,
  confirmFiveVerstProfile,
  confirmParkrunProfile,
  confirmS95Profile,
  linkParticipant,
  previewFiveVerstProfile,
  previewParkrunProfile,
  previewS95Profile,
  searchParticipants,
  type ParticipantSearchResult,
  type PlatformLink,
  type ProfilePreview,
  type ProfilePreviewActivity,
} from "../lib/api";
import { formatDate, platformCodeLabel, pluralizeRu } from "../lib/format";
import { PlatformBadge } from "./PlatformBadge";

const SEARCH_DEBOUNCE_MS = 400;
const MIN_QUERY_LENGTH = 3;

type UrlPlatformCode = "five_verst" | "s95" | "parkrun" | "runpark";

type UrlState = {
  platformCode: UrlPlatformCode;
  url: string;
  loading: boolean;
  confirming: boolean;
  preview: ProfilePreview | null;
  linked: boolean;
  message: string | null;
  error: string | null;
};

type ParticipantNameSearchProps = {
  linkedPlatformCodes: Set<string>;
  onLinked: (link: PlatformLink) => void;
  autoFocus?: boolean;
  placeholder?: string;
};

/** Ссылка на профиль беговой системы, вставленная прямо в поиск. */
function detectUrlPlatform(value: string): UrlPlatformCode | null {
  const lower = value.toLowerCase();
  if (!lower.includes(".")) {
    return null;
  }
  if (lower.includes("5verst.ru")) {
    return "five_verst";
  }
  if (lower.includes("s95.ru")) {
    return "s95";
  }
  if (lower.includes("parkrun.")) {
    return "parkrun";
  }
  if (lower.includes("runpark.ru")) {
    return "runpark";
  }
  return null;
}

function countsLine(totalRuns: number | null, totalVolunteering: number | null): string {
  const parts: string[] = [];
  if (totalRuns !== null && totalRuns > 0) {
    parts.push(pluralizeRu(totalRuns, ["пробежка", "пробежки", "пробежек"]));
  } else {
    parts.push("пока без пробежек");
  }
  if (totalVolunteering !== null && totalVolunteering > 0) {
    parts.push(pluralizeRu(totalVolunteering, ["волонтёрство", "волонтёрства", "волонтёрств"]));
  }
  return parts.join(" · ");
}

/** «00:29:07» → «29:07»: часы у пятикилометровых финишей всегда нулевые. */
function shortFinishTime(display: string | null): string {
  if (!display) {
    return "—";
  }
  return display.replace(/^00:/, "");
}

function resultLocationLine(result: ParticipantSearchResult): string | null {
  if (!result.home_location_name) {
    return null;
  }
  return result.home_location_city
    ? `${result.home_location_name} (${result.home_location_city})`
    : result.home_location_name;
}

function RecentActivitiesSpoiler({ activities }: { activities: ProfilePreviewActivity[] }) {
  if (activities.length === 0) {
    return null;
  }
  return (
    <details className="participant-search-card-recent-spoiler">
      <summary>Последние события</summary>
      <ul className="participant-search-card-recent">
        {activities.slice(0, 3).map((activity, index) => (
          <li key={`${activity.kind}-${activity.event_date}-${index}`}>
            <span
              className={`participant-search-card-recent-kind participant-search-card-recent-kind-${activity.kind}`}
            >
              {activity.kind === "run" ? "Пробежка" : "Волонтёрство"}
            </span>
            <span className="participant-search-card-recent-date">{formatDate(activity.event_date)}</span>
            <span className="participant-search-card-recent-loc">{activity.location_name}</span>
            <span className="participant-search-card-recent-detail">
              {activity.kind === "run" ? shortFinishTime(activity.finish_time_display) : activity.role ?? ""}
            </span>
          </li>
        ))}
      </ul>
    </details>
  );
}

export function ParticipantNameSearch({
  linkedPlatformCodes,
  onLinked,
  autoFocus = false,
  placeholder = "Иванов Иван, код A7035519 или ссылка на профиль",
}: ParticipantNameSearchProps) {
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<ParticipantSearchResult[] | null>(null);
  const [truncated, setTruncated] = useState(false);
  const [hiddenLinkedCodes, setHiddenLinkedCodes] = useState<string[]>([]);
  const [searching, setSearching] = useState(false);
  const [searchError, setSearchError] = useState<string | null>(null);
  const [linkingId, setLinkingId] = useState<string | null>(null);
  const [justLinkedIds, setJustLinkedIds] = useState<Set<string>>(() => new Set());
  const [cardErrors, setCardErrors] = useState<Record<string, string>>({});
  const [urlState, setUrlState] = useState<UrlState | null>(null);
  const abortRef = useRef<AbortController | null>(null);
  const debounceRef = useRef<number | null>(null);

  useEffect(() => {
    return () => {
      abortRef.current?.abort();
      if (debounceRef.current !== null) {
        window.clearTimeout(debounceRef.current);
      }
    };
  }, []);

  const runSearch = useCallback(async (value: string) => {
    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;
    setSearching(true);
    setSearchError(null);
    try {
      const response = await searchParticipants(value, controller.signal);
      if (controller.signal.aborted) {
        return;
      }
      setResults(response.results);
      setTruncated(response.truncated);
      setHiddenLinkedCodes(response.hidden_linked_platform_codes ?? []);
    } catch (err) {
      if (err instanceof DOMException && err.name === "AbortError") {
        return;
      }
      setResults(null);
      setHiddenLinkedCodes([]);
      setSearchError(err instanceof Error ? err.message : "Не удалось выполнить поиск");
    } finally {
      if (!controller.signal.aborted) {
        setSearching(false);
      }
    }
  }, []);

  const runUrlPreview = useCallback(async (platformCode: UrlPlatformCode, url: string) => {
    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;
    setUrlState({
      platformCode,
      url,
      loading: true,
      confirming: false,
      preview: null,
      linked: false,
      message: null,
      error: null,
    });
    const previewFn =
      platformCode === "five_verst"
        ? previewFiveVerstProfile
        : platformCode === "s95"
          ? previewS95Profile
          : previewParkrunProfile;
    try {
      const preview = await previewFn(url, controller.signal);
      if (controller.signal.aborted) {
        return;
      }
      setUrlState((prev) =>
        prev && prev.url === url ? { ...prev, loading: false, preview } : prev,
      );
    } catch (err) {
      if (err instanceof DOMException && err.name === "AbortError") {
        return;
      }
      setUrlState((prev) =>
        prev && prev.url === url
          ? {
              ...prev,
              loading: false,
              error: err instanceof Error ? err.message : "Не удалось загрузить профиль",
            }
          : prev,
      );
    }
  }, []);

  const handleQueryChange = (value: string) => {
    setQuery(value);
    setSearchError(null);
    setCardErrors({});
    setUrlState(null);
    if (debounceRef.current !== null) {
      window.clearTimeout(debounceRef.current);
    }
    const trimmed = value.trim();
    if (trimmed.length < MIN_QUERY_LENGTH) {
      abortRef.current?.abort();
      setResults(null);
      setTruncated(false);
      setHiddenLinkedCodes([]);
      setSearching(false);
      return;
    }

    const urlPlatform = detectUrlPlatform(trimmed);
    if (urlPlatform !== null) {
      abortRef.current?.abort();
      setResults(null);
      setTruncated(false);
      setSearching(false);
      if (linkedPlatformCodes.has(urlPlatform)) {
        setUrlState({
          platformCode: urlPlatform,
          url: trimmed,
          loading: false,
          confirming: false,
          preview: null,
          linked: false,
          message: null,
          error: `Система ${platformCodeLabel(urlPlatform)} уже привязана к вашему аккаунту.`,
        });
        return;
      }
      if (urlPlatform === "runpark") {
        setUrlState({
          platformCode: urlPlatform,
          url: trimmed,
          loading: false,
          confirming: false,
          preview: null,
          linked: false,
          message: null,
          error: "Для RunPark введите штрихкод участника — буква A и цифры, например A6871786.",
        });
        return;
      }
      debounceRef.current = window.setTimeout(() => {
        void runUrlPreview(urlPlatform, trimmed);
      }, SEARCH_DEBOUNCE_MS);
      return;
    }

    debounceRef.current = window.setTimeout(() => {
      void runSearch(trimmed);
    }, SEARCH_DEBOUNCE_MS);
  };

  const handleLink = async (result: ParticipantSearchResult) => {
    setLinkingId(result.participant_id);
    setCardErrors((prev) => {
      const next = { ...prev };
      delete next[result.participant_id];
      return next;
    });
    try {
      const response = await linkParticipant(result.participant_id);
      setJustLinkedIds((prev) => new Set(prev).add(result.participant_id));
      // Система закрыта привязкой — остальные найденные в ней профили прячем,
      // остаётся только привязанная карточка (поиск по занятым системам не работает).
      setResults((prev) =>
        prev === null
          ? prev
          : prev.filter(
              (item) =>
                item.platform_code !== result.platform_code ||
                item.participant_id === result.participant_id,
            ),
      );
      onLinked(response.link);
    } catch (err) {
      const message =
        err instanceof ApiError && err.status === 409
          ? err.message
          : err instanceof Error
            ? err.message
            : "Не удалось привязать профиль";
      setCardErrors((prev) => ({ ...prev, [result.participant_id]: message }));
    } finally {
      setLinkingId(null);
    }
  };

  const handleUrlConfirm = async (linkParkrun: boolean) => {
    if (!urlState || urlState.preview === null) {
      return;
    }
    const { platformCode, url } = urlState;
    setUrlState((prev) => (prev ? { ...prev, confirming: true, error: null } : prev));
    try {
      let link: PlatformLink;
      let message: string | null = null;
      if (platformCode === "s95") {
        const response = await confirmS95Profile(url, linkParkrun);
        link = response.link;
        if (linkParkrun) {
          message =
            response.message === "linked_s95_parkrun_skipped"
              ? "Профиль С95 привязан; parkrun уже был привязан или недоступен."
              : "Профили С95 и parkrun привязаны.";
        }
      } else if (platformCode === "five_verst") {
        link = (await confirmFiveVerstProfile(url)).link;
      } else {
        link = (await confirmParkrunProfile(url)).link;
      }
      setUrlState((prev) =>
        prev ? { ...prev, confirming: false, linked: true, message } : prev,
      );
      onLinked(link);
    } catch (err) {
      setUrlState((prev) =>
        prev
          ? {
              ...prev,
              confirming: false,
              error: err instanceof Error ? err.message : "Не удалось привязать профиль",
            }
          : prev,
      );
    }
  };

  const visibleResults = useMemo(() => results ?? [], [results]);
  const trimmedQuery = query.trim();
  const urlPreview = urlState?.preview ?? null;
  const showParkrunPair =
    urlState?.platformCode === "s95" &&
    urlPreview?.parkrun_match != null &&
    !linkedPlatformCodes.has("parkrun");

  return (
    <div className="participant-name-search">
      <label className="field">
        <span className="field-label">Имя, код участника или ссылка на профиль</span>
        <span className="participant-name-search-input-wrap">
          <svg
            className="participant-name-search-icon"
            viewBox="0 0 24 24"
            width="18"
            height="18"
            aria-hidden
          >
            <path
              fill="none"
              stroke="currentColor"
              strokeWidth="2"
              strokeLinecap="round"
              d="M10.5 18a7.5 7.5 0 1 1 0-15 7.5 7.5 0 0 1 0 15Zm10.4 2.9-4.6-4.6"
            />
          </svg>
          <input
            className="input participant-name-search-input"
            type="search"
            value={query}
            autoFocus={autoFocus}
            onChange={(event) => handleQueryChange(event.target.value)}
            placeholder={placeholder}
            autoComplete="off"
          />
        </span>
      </label>

      {searching && (
        <div className="participant-name-search-skeletons" role="status" aria-label="Ищем во всех системах">
          {[0, 1, 2].map((index) => (
            <div className="participant-name-search-skeleton" key={index} />
          ))}
        </div>
      )}

      {searchError && (
        <div className="profile-form-error" role="alert">
          <p>{searchError}</p>
        </div>
      )}

      {urlState?.loading && (
        <>
          <div className="participant-name-search-skeletons" role="status" aria-label="Загружаем профиль">
            <div className="participant-name-search-skeleton" />
          </div>
          <p className="muted participant-name-search-status">
            Проверяем базу сайта и при необходимости загружаем профиль с платформы — это может занять
            время. Пожалуйста, не закрывайте страницу.
          </p>
        </>
      )}

      {urlState?.error && (
        <div className="profile-form-error" role="alert">
          <p>{urlState.error}</p>
        </div>
      )}

      {urlPreview && (
        <ul className="participant-search-results">
          <li
            className={`participant-search-card${urlState?.linked ? " participant-search-card-linked" : ""}`}
          >
            <div className="participant-search-card-head">
              <PlatformBadge code={urlPreview.platform_code} />
              <span className="participant-search-card-name">{urlPreview.display_name}</span>
              {urlPreview.profile_url.startsWith("http") && (
                <a
                  className="link participant-search-card-open"
                  href={urlPreview.profile_url}
                  target="_blank"
                  rel="noreferrer"
                >
                  Открыть профиль ↗
                </a>
              )}
            </div>
            <div className="participant-search-card-body">
              <div className="participant-search-card-meta">
                <span className="muted">
                  {countsLine(urlPreview.total_runs, urlPreview.total_volunteering)}
                </span>
                {urlPreview.club_name && <span className="muted">Клуб: {urlPreview.club_name}</span>}
                {showParkrunPair && urlPreview.parkrun_match && (
                  <span className="muted">
                    Найден и parkrun: {urlPreview.parkrun_match.display_name} (
                    {countsLine(
                      urlPreview.parkrun_match.total_runs,
                      urlPreview.parkrun_match.total_volunteering,
                    )}
                    )
                  </span>
                )}
              </div>
              <div className="participant-search-card-cta">
                {urlState?.linked ? (
                  <span className="participant-search-card-done">
                    {urlState.message ?? "Привязан ✓"}
                  </span>
                ) : showParkrunPair ? (
                  <>
                    <button
                      type="button"
                      className="btn secondary btn-sm"
                      disabled={urlState?.confirming}
                      onClick={() => void handleUrlConfirm(false)}
                    >
                      Только С95
                    </button>{" "}
                    <button
                      type="button"
                      className="btn primary btn-sm"
                      disabled={urlState?.confirming}
                      onClick={() => void handleUrlConfirm(true)}
                    >
                      {urlState?.confirming ? "Привязка…" : "Привязать оба"}
                    </button>
                  </>
                ) : (
                  <button
                    type="button"
                    className="btn primary btn-sm"
                    disabled={urlState?.confirming}
                    onClick={() => void handleUrlConfirm(false)}
                  >
                    {urlState?.confirming ? "Привязка…" : "Это я — привязать"}
                  </button>
                )}
              </div>
            </div>
            <RecentActivitiesSpoiler activities={urlPreview.recent_activities} />
          </li>
        </ul>
      )}

      {!searching && results !== null && visibleResults.length === 0 && !searchError && (
        <div className="participant-name-search-empty">
          {hiddenLinkedCodes.length > 0 ? (
            <p>
              По запросу «{trimmedQuery}» есть бегун в{" "}
              {hiddenLinkedCodes.length === 1
                ? `системе ${platformCodeLabel(hiddenLinkedCodes[0])}, но к вашему профилю уже привязана учётная запись в этой системе`
                : `системах ${hiddenLinkedCodes.map(platformCodeLabel).join(", ")}, но к вашему профилю уже привязаны учётные записи в этих системах`}{" "}
              — поиск работает только по системам без привязки.
            </p>
          ) : (
            <>
              <p>
                По запросу «{trimmedQuery}» никого не нашли. Проверьте написание — имя должно
                совпадать с протоколами пробежек.
              </p>
              <p className="muted">
                Ещё можно вставить в это же поле ссылку на профиль с сайта системы — например,
                https://5verst.ru/userstats/….
              </p>
            </>
          )}
        </div>
      )}

      {visibleResults.length > 0 && (
        <ul className="participant-search-results">
          {visibleResults.map((result, index) => {
            const isLinkedToMe = result.linked_to_me || justLinkedIds.has(result.participant_id);
            const platformBusy = linkedPlatformCodes.has(result.platform_code) && !isLinkedToMe;
            const takenByOther = result.already_linked && !result.linked_to_me && !justLinkedIds.has(result.participant_id);
            const locationLine = resultLocationLine(result);
            const cardError = cardErrors[result.participant_id];
            return (
              <li
                key={result.participant_id}
                className={`participant-search-card${isLinkedToMe ? " participant-search-card-linked" : ""}`}
                style={{ animationDelay: `${Math.min(index, 8) * 60}ms` }}
              >
                <div className="participant-search-card-head">
                  <PlatformBadge code={result.platform_code} />
                  <span className="participant-search-card-name">{result.display_name}</span>
                  {/* Ссылки на найденный профиль здесь нет: в выдаче по имени
                      это чужие люди (однофамильцы), а согласия на обработку
                      данных они нам не давали. Отличить себя помогают локация,
                      счётчики и клуб на карточке. Ссылка осталась только в
                      разборе адреса, который человек вставил сам
                      (Дмитрий 04.09.2026). */}
                </div>
                <div className="participant-search-card-body">
                  <div className="participant-search-card-meta">
                    {locationLine && <span className="participant-search-card-location">{locationLine}</span>}
                    <span className="muted">{countsLine(result.total_runs, result.total_volunteering)}</span>
                    {result.club_name && <span className="muted">Клуб: {result.club_name}</span>}
                  </div>
                  <div className="participant-search-card-cta">
                    {isLinkedToMe ? (
                      <span className="participant-search-card-done">Привязан ✓</span>
                    ) : takenByOther ? (
                      <span className="muted participant-search-card-note">Привязан к другому аккаунту</span>
                    ) : platformBusy ? (
                      <span className="muted participant-search-card-note">Система уже привязана</span>
                    ) : (
                      <button
                        type="button"
                        className="btn primary btn-sm"
                        disabled={linkingId !== null}
                        onClick={() => void handleLink(result)}
                      >
                        {linkingId === result.participant_id ? "Привязка…" : "Это я — привязать"}
                      </button>
                    )}
                  </div>
                </div>
                <RecentActivitiesSpoiler activities={result.recent_activities} />
                {cardError && (
                  <div className="profile-form-error" role="alert">
                    <p>{cardError}</p>
                  </div>
                )}
              </li>
            );
          })}
        </ul>
      )}

      {visibleResults.length > 0 && hiddenLinkedCodes.length > 0 && (
        <p className="muted participant-name-search-status">
          Совпадения в уже привязанных системах ({hiddenLinkedCodes.map(platformCodeLabel).join(", ")})
          не показываются.
        </p>
      )}

      {truncated && (
        <p className="muted participant-name-search-status">
          Показаны не все совпадения — уточните запрос, добавив имя или фамилию целиком.
        </p>
      )}
    </div>
  );
}
