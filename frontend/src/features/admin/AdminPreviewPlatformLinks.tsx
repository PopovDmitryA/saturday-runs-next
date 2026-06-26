import { useState } from "react";
import { PlatformBadge } from "../../components/PlatformBadge";
import { Snackbar } from "../../components/Snackbar";
import {
  triggerAdminUserSync,
  triggerAdminUserSyncPlatform,
  type AdminPlatformLinkBrief,
  type SyncRefreshResponse,
} from "../../lib/api";
import { platformCodeLabel, syncStatusLabel } from "../../lib/format";

type PlatformStats = {
  runs?: number;
  volunteering?: number;
};

type AdminPreviewPlatformLinksProps = {
  userId: string;
  links: AdminPlatformLinkBrief[];
  byPlatform: Record<string, PlatformStats>;
  onSyncQueued?: () => void;
};

type SnackbarState = {
  open: boolean;
  title: string;
  message: string;
  variant: "default" | "error";
};

const closedSnackbar = (): SnackbarState => ({
  open: false,
  title: "",
  message: "",
  variant: "default",
});

function platformStatsLabel(stats: PlatformStats | undefined): string | null {
  if (!stats) {
    return null;
  }
  const runs = stats.runs ?? 0;
  const volunteering = stats.volunteering ?? 0;
  return `${runs} проб · ${volunteering} вол.`;
}

export function AdminPreviewPlatformLinks({
  userId,
  links,
  byPlatform,
  onSyncQueued,
}: AdminPreviewPlatformLinksProps) {
  const [syncingAll, setSyncingAll] = useState(false);
  const [syncingPlatform, setSyncingPlatform] = useState<string | null>(null);
  const [snackbar, setSnackbar] = useState<SnackbarState>(closedSnackbar);

  const handleSyncResponse = (response: SyncRefreshResponse) => {
    setSnackbar({
      open: true,
      title: response.status === "already_queued" ? "Уже в очереди" : "Запрос принят",
      message: response.message,
      variant: "default",
    });
    onSyncQueued?.();
  };

  const handleSyncError = (err: unknown) => {
    setSnackbar({
      open: true,
      title: "Не удалось запустить обновление",
      message: err instanceof Error ? err.message : "Попробуйте позже.",
      variant: "error",
    });
  };

  const handleSyncAll = async () => {
    setSyncingAll(true);
    try {
      const response = await triggerAdminUserSync(userId);
      handleSyncResponse(response);
    } catch (err) {
      handleSyncError(err);
    } finally {
      setSyncingAll(false);
    }
  };

  const handleSyncPlatform = async (platformCode: string) => {
    setSyncingPlatform(platformCode);
    try {
      const response = await triggerAdminUserSyncPlatform(userId, platformCode);
      handleSyncResponse(response);
    } catch (err) {
      handleSyncError(err);
    } finally {
      setSyncingPlatform(null);
    }
  };

  if (links.length === 0) {
    return <p className="muted">Профили не привязаны.</p>;
  }

  return (
    <>
      <div className="admin-preview-links-toolbar">
        <button
          type="button"
          className="btn secondary btn-sm"
          disabled={syncingAll || syncingPlatform !== null}
          onClick={() => void handleSyncAll()}
        >
          {syncingAll ? "Отправка…" : "Обновить все профили"}
        </button>
      </div>
      <ul className="admin-preview-links">
        {links.map((link) => {
          const statsLabel = platformStatsLabel(byPlatform[link.platform_code]);
          const isSyncing =
            link.sync_status === "syncing" || syncingPlatform === link.platform_code;
          return (
            <li key={`${link.platform_code}-${link.external_user_id}`}>
              <div className="admin-preview-link-main">
                <PlatformBadge code={link.platform_code} />
                {link.platform_code === "runpark" ? (
                  <span>{link.barcode_id ?? link.external_user_id}</span>
                ) : (
                  <a href={link.external_url} target="_blank" rel="noreferrer">
                    {link.external_user_id}
                  </a>
                )}
                {link.display_name && <span className="muted"> · {link.display_name}</span>}
                {statsLabel && <span className="admin-preview-link-stats">{statsLabel}</span>}
              </div>
              <div className="admin-preview-link-actions">
                <span className="muted admin-preview-link-status">
                  {syncStatusLabel(isSyncing ? "syncing" : link.sync_status)}
                </span>
                <button
                  type="button"
                  className="btn btn-ghost btn-sm"
                  disabled={isSyncing || syncingAll}
                  aria-label={`Обновить ${platformCodeLabel(link.platform_code)}`}
                  onClick={() => void handleSyncPlatform(link.platform_code)}
                >
                  {isSyncing ? "Обновление…" : "Обновить"}
                </button>
              </div>
            </li>
          );
        })}
      </ul>
      <Snackbar
        open={snackbar.open}
        title={snackbar.title}
        variant={snackbar.variant}
        onDismiss={() => setSnackbar(closedSnackbar())}
      >
        {snackbar.message}
      </Snackbar>
    </>
  );
}
