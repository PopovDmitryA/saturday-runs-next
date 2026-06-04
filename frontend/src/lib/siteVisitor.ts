const VISITOR_STORAGE_KEY = "sr_visitor_id";

/** Stable anonymous browser id for daily unique-visitor counting. */
export function getOrCreateVisitorId(): string {
  try {
    const existing = localStorage.getItem(VISITOR_STORAGE_KEY);
    if (existing && existing.length >= 8 && existing.length <= 64) {
      return existing;
    }
    const created = crypto.randomUUID();
    localStorage.setItem(VISITOR_STORAGE_KEY, created);
    return created;
  } catch {
    return "ephemeral";
  }
}

export function buildVisitorKey(authenticated: boolean, userId: string | undefined): string {
  if (authenticated && userId) {
    return `u:${userId}`;
  }
  return `a:${getOrCreateVisitorId()}`;
}
