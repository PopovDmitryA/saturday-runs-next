import { useCallback, useEffect, useMemo, useState } from "react";
import { PlatformBadge } from "../../components/PlatformBadge";
import {
  getHomeLocation,
  getHomeLocationCandidates,
  updateHomeLocation,
  type HomeLocationCandidate,
  type HomeLocationSettings,
} from "../../lib/api";
import { pluralizeRu } from "../../lib/format";

function locationSubtitle(candidate: HomeLocationCandidate): string {
  const parts = [
    candidate.city,
    pluralizeRu(candidate.run_count, ["пробежка", "пробежки", "пробежек"]),
  ].filter(Boolean);
  return parts.join(" · ");
}

export function HomeLocationSection() {
  const [current, setCurrent] = useState<HomeLocationSettings | null>(null);
  const [candidates, setCandidates] = useState<HomeLocationCandidate[]>([]);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const [pickerOpen, setPickerOpen] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [home, list] = await Promise.all([getHomeLocation(), getHomeLocationCandidates()]);
      setCurrent(home);
      setCandidates(list);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Не удалось загрузить домашнюю локацию");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const filtered = useMemo(() => {
    const needle = query.trim().toLowerCase();
    if (!needle) {
      return candidates;
    }
    return candidates.filter(
      (candidate) =>
        candidate.name.toLowerCase().includes(needle) ||
        (candidate.city ?? "").toLowerCase().includes(needle),
    );
  }, [candidates, query]);

  const handlePick = async (catalogIdentityKey: string | null) => {
    setSaving(catalogIdentityKey ?? "auto");
    setError(null);
    try {
      const updated = await updateHomeLocation(catalogIdentityKey);
      setCurrent(updated);
      setPickerOpen(false);
      setQuery("");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Не удалось сохранить домашнюю локацию");
    } finally {
      setSaving(null);
    }
  };

  return (
    <section className="card">
      <h2 className="section-title">Домашняя локация</h2>
      <p className="muted settings-lead">
        По умолчанию выбирается локация, где вы бегали чаще всего — но вы можете выбрать любую
        из посещённых. Домашняя локация одна для всех систем (5 вёрст, С95, parkrun и RunPark).
      </p>

      {loading && <p className="muted">Загрузка…</p>}
      {error && <p className="error-text">{error}</p>}

      {!loading && current && (
        <div className="settings-platform-row home-location-current">
          <div className="settings-platform-info">
            {current.location ? (
              <>
                <span className="settings-platform-name">{current.location.name}</span>
                <span className="muted">
                  {locationSubtitle(current.location)}
                  {current.is_auto ? " · выбрано автоматически" : " · выбрано вручную"}
                </span>
              </>
            ) : (
              <span className="muted">Пока нет посещённых локаций</span>
            )}
          </div>
          <div className="home-location-actions">
            {!current.is_auto && (
              <button
                type="button"
                className="btn btn-ghost btn-sm"
                disabled={saving !== null}
                onClick={() => void handlePick(null)}
              >
                Сбросить (авто)
              </button>
            )}
            {candidates.length > 1 && (
              <button
                type="button"
                className="btn secondary btn-sm"
                onClick={() => setPickerOpen((open) => !open)}
              >
                {pickerOpen ? "Скрыть список" : "Выбрать другую"}
              </button>
            )}
          </div>
        </div>
      )}

      {!loading && pickerOpen && (
        <div className="home-location-picker">
          <input
            className="input home-location-search"
            type="search"
            placeholder="Поиск по названию или городу"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            autoFocus
          />
          <ul className="home-location-list">
            {filtered.map((candidate) => {
              const isCurrent =
                !current?.is_auto && current?.location?.catalog_identity_key === candidate.catalog_identity_key;
              return (
                <li key={candidate.catalog_identity_key}>
                  <button
                    type="button"
                    className={`home-location-option${isCurrent ? " home-location-option-active" : ""}`}
                    disabled={saving !== null || isCurrent}
                    onClick={() => void handlePick(candidate.catalog_identity_key)}
                  >
                    <span className="home-location-option-main">
                      <span className="home-location-option-name">{candidate.name}</span>
                      <span className="muted">{locationSubtitle(candidate)}</span>
                    </span>
                    <span className="co-runners-badges">
                      {candidate.platform_codes.map((code) => (
                        <PlatformBadge key={code} code={code} />
                      ))}
                    </span>
                  </button>
                </li>
              );
            })}
            {filtered.length === 0 && <li className="muted home-location-empty">Ничего не найдено</li>}
          </ul>
        </div>
      )}
    </section>
  );
}
