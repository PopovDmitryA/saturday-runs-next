import { presentSyncError } from "../lib/syncErrorMessage";

type SyncJobErrorProps = {
  errorMessage: string | null | undefined;
  errorDetails?: string | null | undefined;
};

export function SyncJobError({ errorMessage, errorDetails }: SyncJobErrorProps) {
  const presentation = presentSyncError(errorMessage, errorDetails);
  if (!presentation) {
    return null;
  }

  return (
    <div className={`sync-job-error sync-job-error-${presentation.variant}`} role="alert">
      <p className="sync-job-error-summary">{presentation.summary}</p>
      {presentation.details && (
        <details className="sync-job-error-details">
          <summary>Технические подробности</summary>
          <pre>{presentation.details}</pre>
        </details>
      )}
    </div>
  );
}
