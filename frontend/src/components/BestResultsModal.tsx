import { useEffect, useState } from "react";
import { type BestResultItem } from "../lib/api";
import { useAppDataSource } from "../lib/appDataSource";
import { formatFinishTimeValue } from "../lib/format";
import { PlatformBadge } from "./PlatformBadge";
import { ActivityDateLink } from "./ActivityDateLink";
import { DetailModal } from "./DetailModal";

type BestResultsModalProps = {
  open: boolean;
  onClose: () => void;
  includeTest?: boolean;
};

function formatResult(item: BestResultItem): string {
  return formatFinishTimeValue(item.finish_time_display, item.finish_time_sec);
}

export function BestResultsModal({ open, onClose, includeTest = false }: BestResultsModalProps) {
  const { getBestResults: fetchBestResults } = useAppDataSource();
  const [items, setItems] = useState<BestResultItem[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!open) {
      return;
    }
    let cancelled = false;
    setLoading(true);
    setError(null);
    fetchBestResults(includeTest)
      .then((data) => {
        if (!cancelled) {
          setItems(data);
        }
      })
      .catch((err: unknown) => {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : "Не удалось загрузить лучшие результаты");
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
  }, [open, includeTest, fetchBestResults]);

  return (
    <DetailModal open={open} title="Лучшие результаты по системам" onClose={onClose}>
      {loading && <p className="muted">Загрузка…</p>}
      {error && <p className="error-text">{error}</p>}
      {!loading && !error && items.length === 0 && (
        <p className="muted unique-locations-empty">Нет результатов с зафиксированным временем.</p>
      )}
      {!loading && !error && items.length > 0 && (
        <div className="unique-locations-table-wrap">
          <table className="data-table unique-locations-table best-results-table">
            <thead>
              <tr>
                <th>Дата</th>
                <th>Локация</th>
                <th>Система</th>
                <th>Результат</th>
              </tr>
            </thead>
            <tbody>
              {items.map((item) => (
                <tr key={item.platform_code}>
                  <td className="col-date">
                    <ActivityDateLink date={item.event_date} target={item} url={item.event_url} />
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
                  <td className="col-result">{formatResult(item)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </DetailModal>
  );
}
