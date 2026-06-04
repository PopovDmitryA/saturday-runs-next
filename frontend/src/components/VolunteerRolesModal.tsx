import { useEffect, useState } from "react";
import { type VolunteerRoleStatItem } from "../lib/api";
import { useAppDataSource } from "../lib/appDataSource";
import { PlatformBadge } from "./PlatformBadge";
import { DetailModal } from "./DetailModal";

type VolunteerRolesModalProps = {
  open: boolean;
  onClose: () => void;
  includeTest?: boolean;
};

export function VolunteerRolesModal({ open, onClose, includeTest = false }: VolunteerRolesModalProps) {
  const { getVolunteerRoleStats: fetchVolunteerRoleStats } = useAppDataSource();
  const [items, setItems] = useState<VolunteerRoleStatItem[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!open) {
      return;
    }
    let cancelled = false;
    setLoading(true);
    setError(null);
    fetchVolunteerRoleStats(includeTest)
      .then((data) => {
        if (!cancelled) {
          setItems(data);
        }
      })
      .catch((err: unknown) => {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : "Не удалось загрузить роли волонтёра");
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
  }, [open, includeTest, fetchVolunteerRoleStats]);

  return (
    <DetailModal open={open} title="Роли волонтёра" onClose={onClose}>
      {loading && <p className="muted">Загрузка…</p>}
      {error && <p className="error-text">{error}</p>}
      {!loading && !error && items.length === 0 && (
        <p className="muted unique-locations-empty">Нет ролей волонтёра.</p>
      )}
      {!loading && !error && items.length > 0 && (
        <div className="unique-locations-table-wrap">
          <table className="data-table unique-locations-table volunteer-roles-table">
            <thead>
              <tr>
                <th>Роль</th>
                <th>Система</th>
                <th>Волонтёрств</th>
              </tr>
            </thead>
            <tbody>
              {items.map((item) => (
                <tr key={`${item.platform_code}-${item.role}`}>
                  <td className="col-role">{item.role}</td>
                  <td className="col-platform">
                    <PlatformBadge code={item.platform_code} />
                  </td>
                  <td className="col-count">{item.count}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </DetailModal>
  );
}
