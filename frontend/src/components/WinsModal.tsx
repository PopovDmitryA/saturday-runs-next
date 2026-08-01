import { useEffect, useState } from "react";
import { type WinItem, type WinScope } from "../lib/api";
import { useAppDataSource } from "../lib/appDataSource";
import { formatFinishTimeValue } from "../lib/format";
import { ActivityDateLink } from "./ActivityDateLink";
import { DetailModal } from "./DetailModal";
import { PlatformBadge } from "./PlatformBadge";

type WinsModalProps = {
  open: boolean;
  onClose: () => void;
  /** Разрез побед: женщинам — среди женщин, мужчинам — в абсолюте. */
  scope: WinScope;
  includeTest?: boolean;
};

export function winsModalTitle(scope: WinScope): string {
  return scope === "female" ? "Победы среди женщин" : "Победы в абсолюте";
}

function formatPlace(item: WinItem, scope: WinScope): string {
  const place = scope === "female" ? item.gender_position : item.position;
  if (place == null) {
    return "—";
  }
  // Знаменатель показываем, только если он что-то добавляет: у parkrun и на
  // старых стартах протокол в базе может быть неполным, и «1 из 1» выглядит
  // поломкой, а не победой.
  if (item.field_size == null || item.field_size <= 1) {
    return String(place);
  }
  return `${place} из ${item.field_size}`;
}

export function WinsModal({ open, onClose, scope, includeTest = false }: WinsModalProps) {
  const { getWins: fetchWins } = useAppDataSource();
  const [items, setItems] = useState<WinItem[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!open) {
      return;
    }
    let cancelled = false;
    setLoading(true);
    setError(null);
    fetchWins(includeTest)
      .then((data) => {
        if (!cancelled) {
          setItems(data);
        }
      })
      .catch((err: unknown) => {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : "Не удалось загрузить победы");
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
  }, [open, includeTest, fetchWins]);

  const isFemale = scope === "female";

  return (
    <DetailModal open={open} title={winsModalTitle(scope)} onClose={onClose}>
      <p className="muted wins-modal-note">
        {isFemale
          ? "Старты, где вы финишировали первой среди женщин."
          : "Старты, где вы финишировали первым в протоколе."}
      </p>
      {loading && <p className="muted">Загрузка…</p>}
      {error && <p className="error-text">{error}</p>}
      {!loading && !error && items.length === 0 && (
        <p className="muted unique-locations-empty">Побед пока нет — всё впереди.</p>
      )}
      {!loading && !error && items.length > 0 && (
        <div className="unique-locations-table-wrap">
          <table className="data-table unique-locations-table wins-table">
            <thead>
              <tr>
                <th>Дата</th>
                <th>Локация</th>
                <th>Система</th>
                <th>Результат</th>
                <th>{isFemale ? "Среди женщин" : "Место"}</th>
                {isFemale && <th>В абсолюте</th>}
              </tr>
            </thead>
            <tbody>
              {items.map((item) => (
                <tr key={`${item.platform_code}-${item.event_date}-${item.location_name}`}>
                  <td className="col-date">
                    <ActivityDateLink date={item.event_date} url={item.event_url} />
                  </td>
                  <td className="col-location">
                    <span className="unique-locations-name">{item.location_name}</span>
                    {item.location_city && (
                      <span className="muted best-results-city">{item.location_city}</span>
                    )}
                  </td>
                  <td className="col-platform">
                    <PlatformBadge code={item.platform_code} />
                  </td>
                  <td className="col-result">
                    {formatFinishTimeValue(item.finish_time_display, item.finish_time_sec)}
                  </td>
                  <td className="col-place wins-place">{formatPlace(item, scope)}</td>
                  {isFemale && <td className="col-place muted">{item.position ?? "—"}</td>}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </DetailModal>
  );
}
