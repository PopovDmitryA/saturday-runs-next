import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  ApiError,
  linkParticipant,
  searchParticipants,
  type ParticipantSearchResult,
  type PlatformLink,
} from "../lib/api";
import { formatDate, pluralizeRu } from "../lib/format";
import { PlatformBadge } from "./PlatformBadge";

const SEARCH_DEBOUNCE_MS = 400;
const MIN_QUERY_LENGTH = 3;

type ParticipantNameSearchProps = {
  linkedPlatformCodes: Set<string>;
  onLinked: (link: PlatformLink) => void;
  autoFocus?: boolean;
  placeholder?: string;
};

function resultMetaLine(result: ParticipantSearchResult): string {
  const parts: string[] = [];
  if (result.total_runs > 0) {
    parts.push(pluralizeRu(result.total_runs, ["пробежка", "пробежки", "пробежек"]));
  } else {
    parts.push("пока без пробежек");
  }
  if (result.total_volunteering > 0) {
    parts.push(pluralizeRu(result.total_volunteering, ["волонтёрство", "волонтёрства", "волонтёрств"]));
  }
  if (result.last_run_date) {
    parts.push(`последняя ${formatDate(result.last_run_date)}`);
  }
  return parts.join(" · ");
}

function resultLocationLine(result: ParticipantSearchResult): string | null {
  if (!result.home_location_name) {
    return null;
  }
  return result.home_location_city
    ? `${result.home_location_name} (${result.home_location_city})`
    : result.home_location_name;
}

export function ParticipantNameSearch({
  linkedPlatformCodes,
  onLinked,
  autoFocus = false,
  placeholder = "Фамилия и имя, как в протоколах — например, Иванов Иван",
}: ParticipantNameSearchProps) {
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<ParticipantSearchResult[] | null>(null);
  const [truncated, setTruncated] = useState(false);
  const [searching, setSearching] = useState(false);
  const [searchError, setSearchError] = useState<string | null>(null);
  const [linkingId, setLinkingId] = useState<string | null>(null);
  const [justLinkedIds, setJustLinkedIds] = useState<Set<string>>(() => new Set());
  const [cardErrors, setCardErrors] = useState<Record<string, string>>({});
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
    } catch (err) {
      if (err instanceof DOMException && err.name === "AbortError") {
        return;
      }
      setResults(null);
      setSearchError(err instanceof Error ? err.message : "Не удалось выполнить поиск");
    } finally {
      if (!controller.signal.aborted) {
        setSearching(false);
      }
    }
  }, []);

  const handleQueryChange = (value: string) => {
    setQuery(value);
    setSearchError(null);
    setCardErrors({});
    if (debounceRef.current !== null) {
      window.clearTimeout(debounceRef.current);
    }
    const trimmed = value.trim();
    if (trimmed.length < MIN_QUERY_LENGTH) {
      abortRef.current?.abort();
      setResults(null);
      setTruncated(false);
      setSearching(false);
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

  const visibleResults = useMemo(() => results ?? [], [results]);
  const trimmedQuery = query.trim();

  return (
    <div className="participant-name-search">
      <label className="field">
        <span className="field-label">Имя и фамилия</span>
        <input
          className="input participant-name-search-input"
          type="search"
          value={query}
          autoFocus={autoFocus}
          onChange={(event) => handleQueryChange(event.target.value)}
          placeholder={placeholder}
          autoComplete="off"
        />
      </label>

      {searching && (
        <p className="muted participant-name-search-status" role="status">
          Ищем во всех системах…
        </p>
      )}

      {searchError && (
        <div className="profile-form-error" role="alert">
          <p>{searchError}</p>
        </div>
      )}

      {!searching && results !== null && visibleResults.length === 0 && !searchError && (
        <div className="participant-name-search-empty">
          <p>
            По запросу «{trimmedQuery}» никого не нашли. Проверьте написание — имя должно совпадать с
            протоколами пробежек.
          </p>
          <p className="muted">
            Если вы ещё не участвовали в пробежках или профиль совсем свежий, привяжите его по ссылке или
            штрихкоду ниже.
          </p>
        </div>
      )}

      {visibleResults.length > 0 && (
        <ul className="participant-search-results">
          {visibleResults.map((result) => {
            const isLinkedToMe = result.linked_to_me || justLinkedIds.has(result.participant_id);
            const platformBusy = linkedPlatformCodes.has(result.platform_code) && !isLinkedToMe;
            const takenByOther = result.already_linked && !result.linked_to_me && !justLinkedIds.has(result.participant_id);
            const locationLine = resultLocationLine(result);
            const cardError = cardErrors[result.participant_id];
            return (
              <li
                key={result.participant_id}
                className={`participant-search-card${isLinkedToMe ? " participant-search-card-linked" : ""}`}
              >
                <div className="participant-search-card-head">
                  <PlatformBadge code={result.platform_code} />
                  <span className="participant-search-card-name">{result.display_name}</span>
                </div>
                <div className="participant-search-card-meta">
                  {locationLine && <span className="participant-search-card-location">{locationLine}</span>}
                  <span className="muted">{resultMetaLine(result)}</span>
                  {result.club_name && <span className="muted">Клуб: {result.club_name}</span>}
                </div>
                <div className="participant-search-card-actions">
                  {result.profile_url && result.profile_url.startsWith("http") && (
                    <a className="link" href={result.profile_url} target="_blank" rel="noreferrer">
                      Открыть профиль ↗
                    </a>
                  )}
                  {isLinkedToMe ? (
                    <span className="participant-search-card-done">Привязан ✓</span>
                  ) : takenByOther ? (
                    <span className="muted">Уже привязан к другому аккаунту</span>
                  ) : platformBusy ? (
                    <span className="muted">В этой системе у вас уже есть профиль</span>
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

      {truncated && (
        <p className="muted participant-name-search-status">
          Показаны не все совпадения — уточните запрос, добавив имя или фамилию целиком.
        </p>
      )}
    </div>
  );
}
