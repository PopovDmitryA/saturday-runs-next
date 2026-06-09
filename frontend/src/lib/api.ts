const API_BASE = "/api";
const DEFAULT_FETCH_TIMEOUT_MS = 20_000;

export class ApiError extends Error {
  status: number;

  constructor(message: string, status: number) {
    super(message);
    this.status = status;
  }
}

export type User = {
  id: string;
  telegram_id: number | null;
  telegram_username: string | null;
  telegram_first_name: string | null;
  telegram_last_name: string | null;
  display_name: string | null;
  display_name_customized: boolean;
  consent_accepted: boolean;
  is_admin: boolean;
  auth_identities: AuthIdentity[];
};

export type AuthIdentity = {
  provider: "telegram" | "vk" | "yandex";
  external_id: string;
  display_name: string | null;
  email: string | null;
  linked_at: string;
  label: string;
};

export type MergePreview = {
  merge_token: string;
  merged_user_id: string;
  platform_links_to_reset: Array<{ platform_code: string; external_user_id: string }>;
  conflicting_platform_codes: string[];
  warning: string;
};

export type LoginRequestResponse = {
  request_token: string;
  bot_url: string;
  expires_in: number;
};

export type LoginRequestStatus = {
  status: string;
  merge_token?: string | null;
};

export type ProfilePreviewActivity = {
  kind: "run" | "volunteer";
  event_date: string;
  location_name: string;
  finish_time_display: string | null;
  role: string | null;
};

export type ProfilePreview = {
  platform_code: string;
  external_user_id: string;
  display_name: string;
  profile_url: string;
  total_runs: number | null;
  total_volunteering: number | null;
  club_name: string | null;
  barcode_id: string | null;
  planning_location: string | null;
  planning_location_seen_at?: string | null;
  age_category: string | null;
  parkrun_eligible: boolean;
  parkrun_match?: ProfilePreview | null;
  recent_activities: ProfilePreviewActivity[];
  data_source?: "database" | "live" | string;
  data_updated_at?: string | null;
  data_through_date?: string | null;
};

export type PlatformLink = {
  id: string;
  platform_code: string;
  platform_name: string;
  external_user_id: string;
  external_url: string;
  display_name: string | null;
  sync_status: string;
  last_user_sync_at: string | null;
  data_updated_at?: string | null;
  data_through_date?: string | null;
};

export type DashboardAnalytics = {
  analytics_version?: number;
  unique_locations: number;
  unique_run_locations: number;
  unique_run_regions: number;
  unique_run_cities: number;
  unique_volunteer_locations: number;
  unique_volunteer_regions: number;
  unique_volunteer_cities: number;
  avg_finish_time_sec: number | null;
  best_finish_time_sec: number | null;
  best_results_platform_count?: number;
  avg_pace_sec_per_km: number | null;
  avg_position: number | null;
  pr_count: number;
  unique_volunteer_roles: number;
  first_activity_date: string | null;
  last_activity_date: string | null;
  first_run_date: string | null;
  days_since_first_run: number | null;
  top_location: { name: string; platform_code: string; count: number; tied_count: number } | null;
  top_volunteer_role: { role: string; count: number } | null;
  runs_last_12_months: number;
  runs_current_year: number;
  volunteering_last_12_months: number;
  volunteering_current_year: number;
  volunteering_index: string | null;
  saturday_streak: number;
  saturday_consistency_pct: number | null;
  saturday_consistency_active: number;
  saturday_consistency_total: number;
  total_distance_km: number;
  next_milestone_runs: number | null;
  runs_to_next_milestone: number | null;
  last_pr_date: string | null;
  last_global_pr_date: string | null;
  pr_last_12_months: number;
  new_locations_last_12_months: number;
  run_clubs_earned: number[];
  next_run_club: number | null;
  avg_vs_field_pct: number | null;
  runs_with_field_avg_count: number;
  platform_metrics: Array<{
    platform_code: string;
    avg_finish_time_sec: number | null;
    avg_pace_sec_per_km: number | null;
  }>;
  activity_by_month: Array<{ month: string; runs: number; volunteering: number }>;
  pace_trend: Array<{ month: string; avg_pace_sec_per_km: number | null }>;
};

export type DashboardStats = {
  total_runs: number;
  total_volunteering: number;
  by_platform: Record<string, { runs: number; volunteering: number }>;
  analytics: DashboardAnalytics;
};

export type DashboardResponse = {
  stats: DashboardStats;
  computed_at: string;
  platform_links: Array<{
    platform_code: string;
    external_user_id: string;
    sync_status: string;
    last_user_sync_at: string | null;
  }>;
  sync_enqueued: boolean;
};

export type RunItem = {
  platform_code: string;
  event_date: string;
  event_number: number | null;
  location_name: string;
  location_city: string | null;
  location_country: string | null;
  location_is_paused?: boolean;
  location_is_cancelled?: boolean;
  position: number | null;
  finish_time_display: string | null;
  finish_time_sec: number | null;
  pace_display: string | null;
  pace_sec_per_km: number | null;
  age_category: string | null;
  is_pr: boolean;
  is_global_pr: boolean;
  is_first_run: boolean;
  is_first_run_at_location: boolean;
  club_name: string | null;
  achievement_labels: string[];
  status: string | null;
  is_test_event: boolean;
  event_url?: string | null;
};

export type BestResultItem = {
  platform_code: string;
  event_date: string;
  location_name: string;
  location_city: string | null;
  finish_time_display: string | null;
  finish_time_sec: number | null;
  event_url?: string | null;
};

export type PersonalRecordItem = {
  platform_code: string;
  event_date: string;
  location_name: string;
  location_city: string | null;
  finish_time_display: string | null;
  finish_time_sec: number | null;
  is_global_pr: boolean;
  event_url?: string | null;
};

export type VolunteerRoleStatItem = {
  platform_code: string;
  role: string;
  count: number;
};

export type VolunteeringItem = {
  platform_code: string;
  event_date: string;
  event_number: number | null;
  location_name: string;
  location_city: string | null;
  location_country: string | null;
  location_is_paused?: boolean;
  location_is_cancelled?: boolean;
  role: string | null;
  is_test_event: boolean;
  event_url?: string | null;
};

export type SyncStatusResponse = {
  platform_links: Array<{
    platform_code: string;
    sync_status: string;
    last_user_sync_at: string | null;
    error_message: string | null;
    error_details?: string | null;
  }>;
  latest_job: {
    id: string;
    status: string;
    trigger: string;
    started_at: string | null;
    finished_at: string | null;
    error_message: string | null;
    error_details?: string | null;
    created_at: string;
  } | null;
  dashboard_cache_computed_at: string | null;
};

export type SyncRefreshResponse = {
  job_id: string;
  status: string;
  message: string;
};

export type SyncQueueTask = {
  celery_task_id: string;
  suffix: string;
  queue: string;
  celery_state: string;
  queue_position: number | null;
  queue_length: number;
};

export type SyncQueueJobUser = {
  telegram_id: number | null;
  telegram_username: string | null;
  display_name: string | null;
};

export type SyncQueueJob = {
  id: string;
  trigger: string;
  status: string;
  platform_code: string | null;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
  error_message: string | null;
  error_details?: string | null;
  tasks: SyncQueueTask[];
  user?: SyncQueueJobUser | null;
};

export type SyncQueuePipelineTask = {
  label: string;
  started_at: string | null;
  source: string | null;
};

export type SyncQueuePipeline = {
  running: SyncQueuePipelineTask[];
  queue_depths: Record<string, number>;
  checked_at: string | null;
};

export type SyncQueueResponse = {
  jobs: SyncQueueJob[];
  queues: Array<{ queue: string; label: string; length: number }>;
  active_jobs_count: number;
  pipeline?: SyncQueuePipeline | null;
};

function extractApiErrorDetail(body: unknown, status: number, rawText: string): string {
  if (body && typeof body === "object") {
    const record = body as Record<string, unknown>;
    if (typeof record.detail === "string" && record.detail.trim()) {
      return sanitizeApiErrorMessage(record.detail);
    }
    if (Array.isArray(record.detail)) {
      const first = record.detail[0] as { msg?: string; type?: string; loc?: unknown[] } | undefined;
      if (first?.msg) {
        const loc = Array.isArray(first.loc) ? first.loc.filter((p) => typeof p === "string").join(".") : "";
        const hint = first.type === "int_type" && loc ? ` (поле ${loc}: ожидалось число)` : "";
        return `${first.msg}${hint}`;
      }
    }
    if (typeof record.message === "string" && record.message.trim()) {
      return record.message;
    }
  }
  const trimmed = rawText.trim();
  if (trimmed) {
    return sanitizeApiErrorMessage(trimmed);
  }
  return `Не удалось выполнить запрос (HTTP ${status})`;
}

function sanitizeApiErrorMessage(message: string): string {
  const trimmed = message.trim();
  if (/traceback \(most recent call last\)/i.test(trimmed)) {
    const lines = trimmed.split("\n").map((line) => line.trim()).filter(Boolean);
    const lastLine = lines.at(-1);
    if (lastLine && !lastLine.startsWith("File ")) {
      return lastLine.length > 500 ? `${lastLine.slice(0, 497)}…` : lastLine;
    }
    return "Внутренняя ошибка сервера. Попробуйте позже.";
  }
  return trimmed.length > 500 ? `${trimmed.slice(0, 497)}…` : trimmed;
}

async function apiFetch<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response;
  const controller = init?.signal ? null : new AbortController();
  const timeoutId =
    controller === null
      ? null
      : window.setTimeout(() => controller.abort(), DEFAULT_FETCH_TIMEOUT_MS);
  try {
    response = await fetch(`${API_BASE}${path}`, {
      credentials: "include",
      headers: {
        "Content-Type": "application/json",
        ...(init?.headers ?? {}),
      },
      ...init,
      signal: init?.signal ?? controller?.signal,
    });
  } catch (error) {
    if (error instanceof DOMException && error.name === "AbortError") {
      if (init?.signal) {
        throw error;
      }
      throw new ApiError(
        "Сервер не ответил вовремя. Обновите страницу и попробуйте снова.",
        0,
      );
    }
    if (error instanceof TypeError) {
      throw new ApiError(
        "Не удалось связаться с сервером. Проверьте подключение и подождите минуту — загрузка профиля может занять до 3 минут.",
        0,
      );
    }
    throw error;
  } finally {
    if (timeoutId !== null) {
      window.clearTimeout(timeoutId);
    }
  }

  const rawText = await response.text();

  if (!response.ok) {
    let body: unknown = null;
    if (rawText) {
      try {
        body = JSON.parse(rawText) as unknown;
      } catch {
        body = null;
      }
    }
    throw new ApiError(extractApiErrorDetail(body, response.status, rawText), response.status);
  }

  if (!rawText) {
    return undefined as T;
  }

  return JSON.parse(rawText) as T;
}

export function createLoginRequest(link = false) {
  const query = link ? "?link=true" : "";
  return apiFetch<LoginRequestResponse>(`/auth/login-request${query}`, { method: "POST" });
}

export function oauthStartUrl(provider: "vk" | "yandex", mode: "login" | "link", consent = false) {
  const params = new URLSearchParams({ mode, consent: consent ? "true" : "false" });
  return `${API_BASE}/auth/oauth/${provider}/start?${params.toString()}`;
}

export type OAuthFinishResult = {
  redirect: string;
  merge_token: string | null;
};

export function finishOAuthLogin(
  provider: "vk" | "yandex",
  payload: {
    code: string;
    state: string;
    device_id?: string | null;
    payload?: string | null;
  },
) {
  return apiFetch<OAuthFinishResult>(`/auth/oauth/${provider}/finish`, {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function getAuthIdentities() {
  return apiFetch<AuthIdentity[]>("/auth/identities");
}

export function unlinkAuthProvider(provider: string) {
  return apiFetch<{ message: string }>(`/auth/identities/${provider}`, { method: "DELETE" });
}

export function getMergePreview(mergeToken: string) {
  const params = new URLSearchParams({ merge_token: mergeToken });
  return apiFetch<MergePreview>(`/auth/merge/preview?${params.toString()}`);
}

export function confirmAccountMerge(mergeToken: string) {
  return apiFetch<{ message: string }>("/auth/merge/confirm", {
    method: "POST",
    body: JSON.stringify({ merge_token: mergeToken }),
  });
}

export function getLoginRequestStatus(requestToken: string) {
  return apiFetch<LoginRequestStatus>(`/auth/login-request/${requestToken}/status`);
}

export function getCurrentUser() {
  return apiFetch<User>("/auth/me");
}

export function updateDisplayName(displayName: string) {
  return apiFetch<User>("/auth/me", {
    method: "PATCH",
    body: JSON.stringify({ display_name: displayName.trim() }),
  });
}

export function logout() {
  return apiFetch<{ message: string }>("/auth/logout", { method: "POST" });
}

export function previewFiveVerstProfile(profileUrl: string, signal?: AbortSignal) {
  return apiFetch<ProfilePreview>("/profiles/five-verst/preview", {
    method: "POST",
    body: JSON.stringify({ profile_url: profileUrl }),
    signal,
  });
}

export function confirmFiveVerstProfile(profileUrl: string) {
  return apiFetch<{ link: PlatformLink; message: string }>("/profiles/five-verst/confirm", {
    method: "POST",
    body: JSON.stringify({ profile_url: profileUrl }),
  });
}

export function previewS95Profile(profileUrl: string, signal?: AbortSignal) {
  return apiFetch<ProfilePreview>("/profiles/s95/preview", {
    method: "POST",
    body: JSON.stringify({ profile_url: profileUrl }),
    signal,
  });
}

export function confirmS95Profile(profileUrl: string, linkParkrun = false) {
  return apiFetch<{
    link: PlatformLink;
    parkrun_link: PlatformLink | null;
    message: string;
  }>("/profiles/s95/confirm", {
    method: "POST",
    body: JSON.stringify({ profile_url: profileUrl, link_parkrun: linkParkrun }),
  });
}

export function previewParkrunProfile(profileUrl: string, signal?: AbortSignal) {
  return apiFetch<ProfilePreview>("/profiles/parkrun/preview", {
    method: "POST",
    body: JSON.stringify({ profile_url: profileUrl }),
    signal,
  });
}

export function confirmParkrunProfile(profileUrl: string) {
  return apiFetch<{ link: PlatformLink; message: string }>("/profiles/parkrun/confirm", {
    method: "POST",
    body: JSON.stringify({ profile_url: profileUrl }),
  });
}

export function listProfileLinks() {
  return apiFetch<PlatformLink[]>("/profiles");
}

export function unlinkProfile(platformCode: string) {
  return apiFetch<{ platform_code: string; message: string; cancelled_sync_jobs: number }>(
    `/profiles/${platformCode}`,
    { method: "DELETE" },
  );
}

export function getDashboard() {
  return apiFetch<DashboardResponse>("/dashboard");
}

export function listRuns(includeTest = false, limit = 200) {
  const params = new URLSearchParams({ limit: String(limit) });
  if (includeTest) {
    params.set("include_test", "true");
  }
  return apiFetch<RunItem[]>(`/runs?${params.toString()}`);
}

export function getBestResults(includeTest = false) {
  const params = new URLSearchParams();
  if (includeTest) {
    params.set("include_test", "true");
  }
  const query = params.toString();
  return apiFetch<BestResultItem[]>(`/runs/best-results${query ? `?${query}` : ""}`);
}

export function getPersonalRecords(includeTest = false) {
  const params = new URLSearchParams();
  if (includeTest) {
    params.set("include_test", "true");
  }
  const query = params.toString();
  return apiFetch<PersonalRecordItem[]>(`/runs/personal-records${query ? `?${query}` : ""}`);
}

export function listVolunteering(includeTest = false, limit = 200) {
  const params = new URLSearchParams({ limit: String(limit) });
  if (includeTest) {
    params.set("include_test", "true");
  }
  return apiFetch<VolunteeringItem[]>(`/volunteering?${params.toString()}`);
}

export function getVolunteerRoleStats(includeTest = false) {
  const params = new URLSearchParams();
  if (includeTest) {
    params.set("include_test", "true");
  }
  const query = params.toString();
  return apiFetch<VolunteerRoleStatItem[]>(`/volunteering/role-stats${query ? `?${query}` : ""}`);
}

export function getSyncStatus() {
  return apiFetch<SyncStatusResponse>("/sync/status");
}

export function getSyncQueue() {
  return apiFetch<SyncQueueResponse>("/sync/queue");
}

export function triggerSyncRefresh() {
  return apiFetch<SyncRefreshResponse>("/sync/refresh", { method: "POST" });
}

export function triggerSyncRefreshPlatform(platformCode: string) {
  return apiFetch<SyncRefreshResponse>(`/sync/refresh/${platformCode}`, { method: "POST" });
}

export type VolunteerRoleCount = {
  role: string;
  count: number;
};

export type MapLocationPlatformVisit = {
  platform_code: string;
  location_name: string;
  location_url?: string | null;
  run_dates: string[];
  volunteer_dates: string[];
  volunteer_roles?: VolunteerRoleCount[];
};

export type MapLocationPoint = {
  id: string;
  catalog_identity_key?: string | null;
  name: string;
  latitude: number;
  longitude: number;
  city: string | null;
  region?: string | null;
  platform_codes: string[];
  active_platform: string | null;
  location_url?: string | null;
  is_paused?: boolean;
  is_cancelled?: boolean;
  run_count: number;
  volunteer_count: number;
  visit_count: number;
  last_visit_date: string | null;
  run_dates?: string[];
  volunteer_dates?: string[];
  platform_visits?: MapLocationPlatformVisit[];
};

export type MapLocationsResponse = {
  points: MapLocationPoint[];
  total_locations: number;
  mapped_locations: number;
  unmapped_locations: number;
};

export type UniqueLocationDetail = {
  catalog_identity_key: string;
  name: string;
  city: string | null;
  region?: string | null;
  latitude: number | null;
  longitude: number | null;
  has_coordinates: boolean;
  is_paused: boolean;
  is_cancelled?: boolean;
  run_count: number;
  volunteer_count: number;
  first_visit_date: string | null;
  last_visit_date: string | null;
  platforms: MapLocationPlatformVisit[];
};

export type UniqueLocationsDetailResponse = {
  locations: UniqueLocationDetail[];
  total_locations: number;
  unique_run_locations: number;
  unique_volunteer_locations: number;
  mapped_locations: number;
  unmapped_locations: number;
  platform_summary: Array<{ platform_code: string; location_count: number }>;
};

export function getVisitedLocationsMap(includeTest = false) {
  const query = includeTest ? "?include_test=true" : "";
  return apiFetch<MapLocationsResponse>(`/locations/visited/map${query}`);
}

export function getUniqueLocationsDetail(includeTest = false) {
  const query = includeTest ? "?include_test=true" : "";
  return apiFetch<UniqueLocationsDetailResponse>(`/locations/visited/detail${query}`);
}

export function getCatalogLocationsMap() {
  return apiFetch<MapLocationsResponse>("/locations/catalog/map");
}

export type CatalogLocationTableRow = {
  row_key: string;
  catalog_identity_key: string;
  location_id: string;
  name: string;
  city: string | null;
  region: string | null;
  platform_code: string;
  is_paused: boolean;
  is_cancelled: boolean;
  has_coordinates: boolean;
  location_url: string | null;
  visited: boolean;
  first_visit_date: string | null;
};

export type CatalogLocationsTableResponse = {
  rows: CatalogLocationTableRow[];
  total_rows: number;
};

export function getCatalogLocationsTable(includeTest = false) {
  const query = includeTest ? "?include_test=true" : "";
  return apiFetch<CatalogLocationsTableResponse>(`/locations/catalog/table${query}`);
}

export type AutoSyncPlatformPreference = {
  platform_code: string;
  enabled: boolean;
  linked: boolean;
};

export type AutoSyncSettings = {
  interval_hours: number;
  last_login_auto_sync_at: string | null;
  platforms: AutoSyncPlatformPreference[];
};

export function getAutoSyncSettings() {
  return apiFetch<AutoSyncSettings>("/settings/auto-sync");
}

export function updateAutoSyncSettings(autoSyncByPlatform: Record<string, boolean>) {
  return apiFetch<AutoSyncSettings>("/settings/auto-sync", {
    method: "PUT",
    body: JSON.stringify({ auto_sync_by_platform: autoSyncByPlatform }),
  });
}

export type NotificationSettings = {
  enabled: boolean;
  description: string;
};

export function getNotificationSettings() {
  return apiFetch<NotificationSettings>("/settings/notifications");
}

export function updateNotificationSettings(enabled: boolean) {
  return apiFetch<NotificationSettings>("/settings/notifications", {
    method: "PUT",
    body: JSON.stringify({ enabled }),
  });
}

export type AdminPlatformLinkBrief = {
  platform_code: string;
  external_user_id: string;
  external_url: string;
  display_name: string | null;
  sync_status: string;
};

export type AdminUserAuthBrief = {
  provider: string;
  label: string;
  external_id: string;
};

export type AdminUserListItem = {
  id: string;
  telegram_id: number | null;
  telegram_username: string | null;
  display_name: string | null;
  auth_logins: AdminUserAuthBrief[];
  news_subscribed: boolean;
  consent_accepted: boolean;
  created_at: string;
  last_login_at: string | null;
  total_runs: number | null;
  total_volunteering: number | null;
  platform_links: AdminPlatformLinkBrief[];
};

export type AdminUserListResponse = {
  items: AdminUserListItem[];
  total: number;
  limit: number;
  offset: number;
  query: string | null;
};

export type AdminS95ParticipantListItem = {
  id: string;
  external_user_id: string;
  display_name: string | null;
  profile_url: string | null;
  barcode_id: string | null;
  club_name: string | null;
  planning_location: string | null;
  planning_location_seen_at: string | null;
  fetched_at: string | null;
  sync_status: string;
  error_message: string | null;
};

export type AdminS95ParticipantListResponse = {
  items: AdminS95ParticipantListItem[];
  total: number;
  limit: number;
  offset: number;
  query: string | null;
};

export type AdminUserPreviewDashboard = {
  user: {
    id: string;
    telegram_id: number | null;
    telegram_username: string | null;
    display_name: string | null;
    news_subscribed: boolean;
    auth_logins: AdminUserAuthBrief[];
  };
  stats: DashboardStats;
  computed_at: string | null;
  platform_links: AdminPlatformLinkBrief[];
};

export function listAdminUsers(query = "", limit = 25, offset = 0) {
  const params = new URLSearchParams();
  if (query.trim()) {
    params.set("q", query.trim());
  }
  params.set("limit", String(limit));
  params.set("offset", String(offset));
  return apiFetch<AdminUserListResponse>(`/admin/users?${params.toString()}`);
}

export function listAdminS95Participants(query = "", limit = 25, offset = 0) {
  const params = new URLSearchParams();
  if (query.trim()) {
    params.set("q", query.trim());
  }
  params.set("limit", String(limit));
  params.set("offset", String(offset));
  return apiFetch<AdminS95ParticipantListResponse>(`/admin/s95/participants?${params.toString()}`);
}

export function getAdminUserPreviewDashboard(userId: string) {
  return apiFetch<AdminUserPreviewDashboard>(`/admin/users/${userId}/preview/dashboard`);
}

export function getAdminUserPreviewRuns(userId: string, limit = 50, offset = 0) {
  const params = new URLSearchParams({ limit: String(limit), offset: String(offset) });
  return apiFetch<RunItem[]>(`/admin/users/${userId}/preview/runs?${params.toString()}`);
}

export function getAdminUserPreviewVolunteering(userId: string, limit = 50, offset = 0) {
  const params = new URLSearchParams({ limit: String(limit), offset: String(offset) });
  return apiFetch<VolunteeringItem[]>(`/admin/users/${userId}/preview/volunteering?${params.toString()}`);
}

export function getAdminUserPreviewVisitedMap(userId: string, includeTest = false) {
  const query = includeTest ? "?include_test=true" : "";
  return apiFetch<MapLocationsResponse>(`/admin/users/${userId}/preview/locations/visited/map${query}`);
}

export function getAdminUserPreviewVisitedDetail(userId: string, includeTest = false) {
  const query = includeTest ? "?include_test=true" : "";
  return apiFetch<UniqueLocationsDetailResponse>(
    `/admin/users/${userId}/preview/locations/visited/detail${query}`,
  );
}

export function getAdminUserPreviewCatalogTable(userId: string, includeTest = false) {
  const query = includeTest ? "?include_test=true" : "";
  return apiFetch<CatalogLocationsTableResponse>(
    `/admin/users/${userId}/preview/locations/catalog/table${query}`,
  );
}

export function getAdminUserPreviewBestResults(userId: string, includeTest = false) {
  const query = includeTest ? "?include_test=true" : "";
  return apiFetch<BestResultItem[]>(`/admin/users/${userId}/preview/runs/best-results${query}`);
}

export function getAdminUserPreviewPersonalRecords(userId: string, includeTest = false) {
  const query = includeTest ? "?include_test=true" : "";
  return apiFetch<PersonalRecordItem[]>(`/admin/users/${userId}/preview/runs/personal-records${query}`);
}

export function getAdminUserPreviewVolunteerRoleStats(userId: string, includeTest = false) {
  const query = includeTest ? "?include_test=true" : "";
  return apiFetch<VolunteerRoleStatItem[]>(
    `/admin/users/${userId}/preview/volunteering/role-stats${query}`,
  );
}

export type AbuseIpBlockItem = {
  ip: string;
  ttl_seconds: number | null;
  score: number;
  source: string;
  reason: string;
  created_at: string | null;
  created_by: string | null;
};

export type AbuseTelegramBanItem = {
  telegram_id: number;
  telegram_username: string | null;
  display_name: string | null;
  last_ip: string | null;
  ttl_seconds: number | null;
  source: string;
  reason: string;
  created_at: string | null;
  created_by: string | null;
};

export type AbuseBlockListResponse = {
  ip_blocks: AbuseIpBlockItem[];
  telegram_bans: AbuseTelegramBanItem[];
};

export function listAdminAbuseBlocks() {
  return apiFetch<AbuseBlockListResponse>("/admin/abuse/blocks");
}

export function createAdminAbuseBan(body: {
  target: string;
  duration_seconds: number;
  reason?: string;
  ban_ip?: boolean;
  ban_account?: boolean;
}) {
  return apiFetch<{ created: Array<{ kind: string; target: string }>; label: string }>("/admin/abuse/blocks", {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export function deleteAdminIpBlock(ip: string) {
  return apiFetch<{ message: string }>(`/admin/abuse/blocks/ip/${encodeURIComponent(ip)}`, {
    method: "DELETE",
  });
}

export function deleteAdminTelegramBan(telegramId: number) {
  return apiFetch<{ message: string }>(`/admin/abuse/blocks/telegram/${telegramId}`, {
    method: "DELETE",
  });
}

export function clearAdminIpAbuseScore(ip: string) {
  return apiFetch<{ message: string }>(`/admin/abuse/blocks/ip/${encodeURIComponent(ip)}/clear-score`, {
    method: "POST",
  });
}

export type AdminSiteStatsOverview = {
  users_total: number;
  users_with_consent: number;
  users_news_subscribed: number;
  users_active_period: number;
  users_with_any_link: number;
  users_with_all_three_links: number;
  platform_links_total: number;
  links_by_platform: Record<string, number>;
  participants_total: number;
  events_total: number;
  run_results_total: number;
  locations_total: number;
  sync_jobs_total: number;
  sync_jobs_active: number;
  login_requests_period: number;
};

export type AdminSiteStatsDayPoint = { date: string; value: number };

export type AdminSiteStatsPageviewsDay = {
  date: string;
  total: number;
  unique_visitors: number;
  landing: number;
  demo: number;
  app: number;
  login: number;
  about: number;
  admin: number;
  other: number;
  authenticated: number;
  anonymous: number;
};

export type AdminSiteStatsResponse = {
  period_days: number;
  generated_at: string;
  overview: AdminSiteStatsOverview;
  users_new_by_day: AdminSiteStatsDayPoint[];
  links_new_by_day: AdminSiteStatsDayPoint[];
  logins_by_day_db: AdminSiteStatsDayPoint[];
  logins_by_day: AdminSiteStatsDayPoint[];
  login_requests_by_day: AdminSiteStatsDayPoint[];
  pageviews_by_day: AdminSiteStatsPageviewsDay[];
};

export function getAdminSiteStats(periodDays = 30) {
  return apiFetch<AdminSiteStatsResponse>(`/admin/stats?period_days=${periodDays}`);
}

export type ParkrunSessionStatus = {
  captcha_pending: boolean;
  captcha_message: string | null;
  cooldown_remaining_seconds: number | null;
  storage_state_configured: boolean;
  storage_state_exists: boolean;
  storage_state_path: string;
  default_cdp_url: string;
  pending_queue_count: number;
  failed_queue_count: number;
  stuck_done_queue_count: number;
  worker_alive: boolean;
  worker_status: string;
  worker_requested_at: string | null;
  worker_last_handled_at: string | null;
  worker_progress: Record<string, unknown>;
};

export type ParkrunLocalWorkerStartResponse = {
  queued: boolean;
  message: string;
  requested_at?: string | null;
};

export type ParkrunCdpSaveResponse = {
  storage_path: string;
  page_title: string | null;
  cdp_url: string;
  message: string;
};

export type ParkrunProcessPendingResponse = {
  summary: Record<string, number>;
  details: string[];
};

export function getAdminParkrunSessionStatus() {
  return apiFetch<ParkrunSessionStatus>("/admin/parkrun/session");
}

export function saveAdminParkrunSession(cdpUrl = "") {
  return apiFetch<ParkrunCdpSaveResponse>("/admin/parkrun/save-session", {
    method: "POST",
    body: JSON.stringify({ cdp_url: cdpUrl }),
  });
}

export function clearAdminParkrunCaptchaWait() {
  return apiFetch<{ message: string }>("/admin/parkrun/clear-captcha-wait", {
    method: "POST",
    body: JSON.stringify({}),
  });
}

export function processAdminParkrunPending(limit = 10) {
  return apiFetch<ParkrunProcessPendingResponse>("/admin/parkrun/process-pending", {
    method: "POST",
    body: JSON.stringify({ limit }),
  });
}

export function resetAdminParkrunFailedPending() {
  return apiFetch<{ reset: number; message: string }>("/admin/parkrun/reset-failed-pending", {
    method: "POST",
    body: JSON.stringify({}),
  });
}

export function startAdminParkrunLocalWorker(resetFailed = true) {
  return apiFetch<ParkrunLocalWorkerStartResponse>("/admin/parkrun/local-worker/start", {
    method: "POST",
    body: JSON.stringify({ reset_failed: resetFailed }),
  });
}

export function recordSitePageview(path: string, authenticated: boolean, visitorKey: string) {
  return apiFetch<void>("/stats/pageview", {
    method: "POST",
    body: JSON.stringify({ path, authenticated, visitor_key: visitorKey }),
  }).catch(() => {
    // ignore analytics errors
  });
}

export function getDemoDashboard() {
  return apiFetch<AdminUserPreviewDashboard>("/demo/dashboard");
}

export function demoListRuns(limit = 200) {
  return apiFetch<RunItem[]>(`/demo/runs?limit=${limit}`);
}

export function demoListVolunteering(limit = 200) {
  return apiFetch<VolunteeringItem[]>(`/demo/volunteering?limit=${limit}`);
}

export function demoGetBestResults() {
  return apiFetch<BestResultItem[]>("/demo/runs/best-results");
}

export function demoGetPersonalRecords() {
  return apiFetch<PersonalRecordItem[]>("/demo/runs/personal-records");
}

export function demoGetVolunteerRoleStats() {
  return apiFetch<VolunteerRoleStatItem[]>("/demo/volunteering/role-stats");
}

export function demoGetVisitedLocationsMap() {
  return apiFetch<MapLocationsResponse>("/demo/locations/visited/map");
}

export function demoGetUniqueLocationsDetail() {
  return apiFetch<UniqueLocationsDetailResponse>("/demo/locations/visited/detail");
}

export function demoGetCatalogLocationsMap() {
  return apiFetch<MapLocationsResponse>("/demo/locations/catalog/map");
}

export function demoGetCatalogLocationsTable() {
  return apiFetch<CatalogLocationsTableResponse>("/demo/locations/catalog/table");
}
