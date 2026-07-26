/**
 * Блок «Поделиться публичным профилем» — единственное, что осталось от старой
 * страницы дашборда: сам дашборд живёт в портальном кабинете
 * (PortalCabinetDashboardPage), а этот блок он переиспользует.
 */
import { useCallback, useState } from "react";

export function PublicProfileShareBlock({ handle }: { handle: string | number }) {
  const [copied, setCopied] = useState(false);
  // Если задан уникальный slug — ссылка на него, иначе на числовой serial_id.
  const url = `${window.location.origin}/users/${handle}`;

  const handleCopy = useCallback(async () => {
    try {
      await navigator.clipboard.writeText(url);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 2000);
    } catch {
      // ignore — clipboard API unavailable, user can still select the link text
    }
  }, [url]);

  return (
    <div className="card public-profile-share">
      <p className="public-profile-share-title">Мой публичный профиль</p>
      <p className="muted">Поделитесь ссылкой — по ней видна ваша статистика пробежек и волонтёрств.</p>
      <div className="public-profile-share-row">
        <input className="input" type="text" value={url} readOnly onFocus={(e) => e.target.select()} />
        <button type="button" className="btn secondary" onClick={() => void handleCopy()}>
          {copied ? "Скопировано ✓" : "Скопировать"}
        </button>
        <a className="btn btn-ghost" href={url} target="_blank" rel="noreferrer">
          Открыть →
        </a>
      </div>
    </div>
  );
}
