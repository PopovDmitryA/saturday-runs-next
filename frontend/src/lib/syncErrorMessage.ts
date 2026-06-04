export type SyncErrorVariant = "info" | "error";

export type SyncErrorPresentation = {
  variant: SyncErrorVariant;
  summary: string;
  details: string | null;
};

const UNLINK_MESSAGE = "Синхронизация отменена: профиль отвязан";

const TECHNICAL_MARKERS = [
  "INSERT INTO",
  "[SQL:",
  "(Background on this error",
  "sqlalchemy",
  "psycopg",
  "IntegrityError",
];

function isTechnicalDump(text: string): boolean {
  if (text.length > 320) {
    return true;
  }
  const lower = text.toLowerCase();
  return TECHNICAL_MARKERS.some((marker) => lower.includes(marker.toLowerCase()));
}

function humanizeSummary(raw: string): string {
  const text = raw.trim();
  if (!text) {
    return "";
  }

  if (text.includes(UNLINK_MESSAGE) || /отвязан/i.test(text)) {
    return text.includes(UNLINK_MESSAGE) ? UNLINK_MESSAGE : text;
  }

  const lower = text.toLowerCase();
  if (lower.includes("uniqueviolation") || lower.includes("duplicate key")) {
    const eventMatch = text.match(/['"]event_name['"][^'"]*['"]([^'"]+)['"]/);
    if (eventMatch) {
      return `Конфликт данных: событие «${eventMatch[1]}» уже есть в базе.`;
    }
    return "Конфликт данных: такая запись о мероприятии уже есть в базе.";
  }

  if (/rate limit|429/i.test(text)) {
    return "Сайт платформы временно ограничил запросы. Попробуйте обновить позже.";
  }

  if (/timeout|timed out/i.test(text)) {
    return "Превышено время ожидания ответа от сайта платформы.";
  }

  if (isTechnicalDump(text)) {
    for (const line of text.split("\n")) {
      const candidate = line.trim();
      if (!candidate || candidate.length > 220) {
        continue;
      }
      if (isTechnicalDump(candidate) && candidate.length > 80) {
        continue;
      }
      if (candidate.startsWith("[")) {
        continue;
      }
      return candidate;
    }
    return "Ошибка при синхронизации данных. Повторите обновление позже.";
  }

  return text.length > 500 ? `${text.slice(0, 497)}…` : text;
}

export function presentSyncError(
  errorMessage: string | null | undefined,
  errorDetails?: string | null | undefined,
): SyncErrorPresentation | null {
  if (!errorMessage?.trim()) {
    return null;
  }

  const raw = errorMessage.trim();
  const details =
    errorDetails?.trim() && errorDetails.trim() !== raw ? errorDetails.trim() : null;
  const technicalSource = details ?? (isTechnicalDump(raw) ? raw : null);
  const summary = humanizeSummary(raw);

  const variant: SyncErrorVariant =
    summary.includes(UNLINK_MESSAGE) || /отвязан/i.test(summary) ? "info" : "error";

  return {
    variant,
    summary,
    details: technicalSource && technicalSource !== summary ? technicalSource : null,
  };
}
