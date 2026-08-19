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
  avatar_url: string | null;
  // Оригинал аватарки без пережатия — раскрывается по клику. null у аватарок,
  // загруженных до переезда на S3.
  avatar_full_url: string | null;
  // Публичный адрес участника: /users/{public_slug ?? serial_id}.
  serial_id: number | null;
  public_slug: string | null;
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
  barcode_id: string | null;
  sync_status: string;
  last_user_sync_at: string | null;
  data_updated_at?: string | null;
  data_through_date?: string | null;
};

export type LocationRecordEntry = {
  location_name: string;
  location_slug: string | null;
  location_city: string | null;
  // Уровень рекорда: "global" | код системы — только для площадок, живших в
  // нескольких системах; у монолокаций null.
  level: string | null;
  platform_code: string;
  // Возрастная группа («30–34») — только у рекордов по возрастным группам.
  age_group: string | null;
  finish_time_sec: number;
  finish_time_display: string;
  event_date: string;
  is_current: boolean;
  beaten_date: string | null;
  beaten_by: string | null;
  beaten_time_sec: number | null;
  beaten_time_display: string | null;
};

export type LocationRecordsBlock = {
  current_count: number;
  lost_count: number;
  entries: LocationRecordEntry[];
};

/** Строка таблиц «Дальности от дома»: посещённая или ещё не посещённая площадка. */
export type HomeDistanceLocation = {
  catalog_identity_key: string;
  location_slug: string | null;
  name: string;
  city: string | null;
  region: string | null;
  /** null — координат площадки нет, в зачёт километров она не идёт. */
  distance_km: number | null;
  run_count: number;
  last_visit_date: string | null;
  is_home: boolean;
  is_paused: boolean;
  /** Системы площадки — плашками рядом с названием. */
  platform_codes: string[];
};

export type HomeLocationSummary = {
  catalog_identity_key: string;
  location_slug: string | null;
  name: string;
  city: string | null;
  region: string | null;
  run_count: number;
  is_auto: boolean;
  /** "tie" — ничья по числу пробежек, "close" — вторая площадка рядом. */
  ambiguity: "tie" | "close" | null;
  runner_up_name: string | null;
  has_coordinates: boolean;
};

export type HomeDistance = {
  home: HomeLocationSummary | null;
  total_distance_km: number;
  farthest: HomeDistanceLocation | null;
  visited_count: number;
  counted_count: number;
  unknown_count: number;
};

export type HomeDistanceDetail = HomeDistance & {
  visited: HomeDistanceLocation[];
  unvisited: HomeDistanceLocation[];
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
  avg_gender_position?: number | null;
  pr_count: number;
  /** Победы: у женщин — среди женщин, у мужчин — в абсолюте (см. wins_scope). */
  wins_count?: number;
  wins_scope?: WinScope;
  unique_volunteer_roles: number;
  first_activity_date: string | null;
  last_activity_date: string | null;
  first_run_date: string | null;
  days_since_first_run: number | null;
  top_location: {
    name: string;
    slug?: string | null;
    platform_codes: string[];
    count: number;
    tied_count: number;
  } | null;
  top_volunteer_role: { role: string; count: number } | null;
  runs_last_12_months: number;
  runs_current_year: number;
  volunteering_last_12_months: number;
  volunteering_current_year: number;
  volunteering_index: string | null;
  saturday_streak: number;
  saturday_streak_max?: number;
  saturday_run_streak_max?: number;
  saturday_vol_streak_max?: number;
  saturday_streak_current?: number;
  saturday_run_streak_current?: number;
  saturday_vol_streak_current?: number;
  activity_calendar?: Array<{
    date: string;
    runs: number;
    volunteering: number;
    run_items?: Array<{ platform_code: string; location: string }>;
    volunteer_items?: Array<{ platform_code: string; location: string }>;
  }>;
  finish_times_sec?: number[];
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
    runs_count?: number;
    avg_finish_time_sec: number | null;
    avg_pace_sec_per_km: number | null;
  }>;
  activity_by_month: Array<{ month: string; runs: number; volunteering: number }>;
  pace_trend: Array<{
    month: string;
    avg_pace_sec_per_km: number | null;
    avg_finish_time_sec?: number | null;
  }>;
  pace_trend_yearly?: Array<{
    year: string;
    avg_pace_sec_per_km: number | null;
    avg_finish_time_sec?: number | null;
  }>;
  location_records?: LocationRecordsBlock;
  age_group_records?: LocationRecordsBlock;
  home_distance?: HomeDistance | null;
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
  serial_id: number | null;
  public_slug: string | null;
};

export type RunItem = {
  run_result_id?: string | null;
  platform_code: string;
  event_date: string;
  event_number: number | null;
  location_name: string;
  location_city: string | null;
  location_country: string | null;
  location_slug?: string | null;
  location_is_paused?: boolean;
  location_is_cancelled?: boolean;
  position: number | null;
  gender_position?: number | null;
  finish_time_display: string | null;
  finish_time_sec: number | null;
  pace_display: string | null;
  pace_sec_per_km: number | null;
  age_category: string | null;
  is_pr: boolean;
  is_global_pr: boolean;
  is_location_pr: boolean;
  is_crosslinked: boolean;
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
  is_pr?: boolean;
  is_global_pr: boolean;
  is_location_pr?: boolean;
  is_debut?: boolean;
  event_url?: string | null;
};

export type WinScope = "absolute" | "female";

export type WinItem = {
  platform_code: string;
  event_date: string;
  event_number: number | null;
  location_name: string;
  location_city: string | null;
  finish_time_display: string | null;
  finish_time_sec: number | null;
  position: number | null;
  gender_position: number | null;
  /** Сколько всего было финишёров в этом зачёте — знаменатель «1 из N». */
  field_size: number | null;
  scope: WinScope;
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
  location_slug?: string | null;
  location_is_paused?: boolean;
  location_is_cancelled?: boolean;
  role: string | null;
  volunteer_result_id?: string | null;
  rating_entry_id?: string | null;
  is_crosslinked: boolean;
  is_test_event: boolean;
  parkrun_total_credits?: number | null;
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
  finished_at?: string | null;
  source: string | null;
};

export type SyncQueuePipeline = {
  running: SyncQueuePipelineTask[];
  last_success?: SyncQueuePipelineTask[];
  queue_depths: Record<string, number>;
  checked_at: string | null;
};

export type SyncQueueParkrunQueue = {
  pending: number;
  failed: number;
  stuck_done: number;
  processing: number;
  celery_sync: number;
  captcha_pending: boolean;
  cooldown_remaining_seconds: number | null;
  worker_alive: boolean;
  worker_status: string;
  s95_pending: number;
  s95_failed: number;
  s95_processing: number;
};

export type SyncQueueResponse = {
  jobs: SyncQueueJob[];
  queues: Array<{ queue: string; label: string; length: number }>;
  active_jobs_count: number;
  pipeline?: SyncQueuePipeline | null;
  parkrun_queue?: SyncQueueParkrunQueue | null;
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

// Тот же разбор detail, что у apiFetch, но для «сырых» fetch-запросов
// (загрузка файлов идёт мимо apiFetch из-за multipart).
async function readErrorDetail(response: Response): Promise<string> {
  const rawText = await response.text().catch(() => "");
  let body: unknown = null;
  try {
    body = rawText ? JSON.parse(rawText) : null;
  } catch {
    body = null;
  }
  return extractApiErrorDetail(body, response.status, rawText);
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

async function apiFetch<T>(
  path: string,
  init?: RequestInit,
  options?: { timeoutMs?: number },
): Promise<T> {
  let response: Response;
  const controller = init?.signal ? null : new AbortController();
  const timeoutMs = options?.timeoutMs ?? DEFAULT_FETCH_TIMEOUT_MS;
  const timeoutId =
    controller === null
      ? null
      : window.setTimeout(() => controller.abort(), timeoutMs);
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

// На каждой странице /auth/me независимо запрашивают сразу несколько
// компонентов (App, PortalHeader, RequireAuth, сама страница) — получалось по
// 2-3 одинаковых запроса на переход, каждый по 150-700 мс. Склеиваем только
// одновременные вызовы: как только запрос завершился, кэш сбрасывается, поэтому
// после логина/логаута следующий вызов снова идёт на сервер.
let currentUserInFlight: Promise<User> | null = null;

export function getCurrentUser() {
  if (currentUserInFlight) {
    return currentUserInFlight;
  }
  const request = apiFetch<User>("/auth/me").finally(() => {
    if (currentUserInFlight === request) {
      currentUserInFlight = null;
    }
  });
  currentUserInFlight = request;
  return request;
}

/** Результат проверки сессии: «гость» и «не смогли проверить» — разные вещи. */
export type SessionProbe =
  | { state: "authenticated"; user: User }
  | { state: "guest" }
  | { state: "unknown" };

/** Сервер прямо сказал, что сессии нет. Всё остальное — не приговор. */
export function isSessionMissingError(error: unknown): boolean {
  return error instanceof ApiError && error.status === 401;
}

const SESSION_RETRY_DELAY_MS = 1500;

/**
 * Проверить сессию, не путая «пользователь не залогинен» с «сервер не ответил».
 *
 * Раньше любая ошибка /auth/me трактовалась как разлогин, и живая сессия
 * выглядела разлогиненной при 429 (общий лимит на IP — а мобильные операторы
 * NAT-ят много людей за один адрес), 5xx или таймауте. Транзиентную ошибку
 * пробуем повторить один раз, и только явный 401 считаем гостем.
 */
export async function probeCurrentUser(): Promise<SessionProbe> {
  try {
    return { state: "authenticated", user: await getCurrentUser() };
  } catch (error) {
    if (isSessionMissingError(error)) {
      return { state: "guest" };
    }
    await new Promise((resolve) => window.setTimeout(resolve, SESSION_RETRY_DELAY_MS));
    try {
      return { state: "authenticated", user: await getCurrentUser() };
    } catch (retryError) {
      return isSessionMissingError(retryError) ? { state: "guest" } : { state: "unknown" };
    }
  }
}

export function updateDisplayName(displayName: string) {
  return apiFetch<User>("/auth/me", {
    method: "PATCH",
    body: JSON.stringify({ display_name: displayName.trim() }),
  });
}

export function uploadAvatar(file: File) {
  const form = new FormData();
  form.append("file", file);
  // headers: {} затирает дефолтный Content-Type: application/json — браузер
  // сам проставит multipart/form-data с boundary.
  return apiFetch<User>(
    "/users/me/avatar",
    { method: "POST", body: form, headers: {} },
    { timeoutMs: 60_000 },
  );
}

export function deleteAvatar() {
  return apiFetch<User>("/users/me/avatar", { method: "DELETE" });
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

export function previewRunparkProfile(profileUrl: string, signal?: AbortSignal) {
  return apiFetch<ProfilePreview>("/profiles/runpark/preview", {
    method: "POST",
    body: JSON.stringify({ profile_url: profileUrl }),
    signal,
  });
}

export function confirmRunparkProfile(profileUrl: string) {
  return apiFetch<{ link: PlatformLink; message: string }>("/profiles/runpark/confirm", {
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

export type OnThisDayRun = {
  years_ago: number;
  event_date: string;
  location_name: string;
  location_city: string | null;
  platform_code: string;
  finish_time_display: string | null;
  finish_time_sec: number | null;
  position: number | null;
  is_pr: boolean;
  event_url: string | null;
};

export type OnThisDay = {
  kind: "anniversary" | null;
  run: OnThisDayRun | null;
  runs: OnThisDayRun[];
  also_count: number;
  today_iso: string;
};

export function getOnThisDay() {
  return apiFetch<OnThisDay>("/dashboard/on-this-day");
}

// ── Цели и достижения ────────────────────────────────────────────────────────

export type ChallengeLevel = "bronze" | "silver" | "gold";

export type ChallengeCell = {
  label: string;
  done: boolean;
  date: string | null;
  location: string | null;
  hint: string | null;
  platform_code: string | null;
  count?: number | null;
};

export type ChallengeLetter = {
  letter: string;
  done: boolean;
  date: string | null;
  location: string | null;
  locations: string[];
  locations_more: number;
  platform_code: string | null;
};

export type ChallengeDay = {
  key: string;
  date: string;
  location: string;
  platform_code: string | null;
};

export type ChallengeDetailItem = {
  date?: string;
  value?: string;
  location?: string;
  count?: number;
  occurrences?: Array<{ date: string; location: string }>;
};

export type ChallengeDetail = {
  cells?: ChallengeCell[];
  letters?: ChallengeLetter[];
  /** Сколько букв вообще доступно в текущем скоупе систем («Алфавит»). */
  available?: number;
  days?: ChallengeDay[];
  items?: ChallengeDetailItem[];
  example?: { value: string; location: string; note: string };
};

export type ChallengeLevelDates = {
  bronze: string | null;
  silver: string | null;
  gold: string | null;
};

// easy | medium | hard | solo (один тир на весь челлендж — вкладки не рисуются)
export type ChallengeTierKey = string;

export type ChallengeTier = {
  tier: ChallengeTierKey;
  label: string | null;
  levels: { bronze: number; silver: number; gold: number };
  target: number;
  level: ChallengeLevel | null;
  next_level: ChallengeLevel | null;
  to_next_level: number | null;
  to_next_label: string | null;
  pct: number;
  level_dates: ChallengeLevelDates;
};

export type Challenge = {
  code: string;
  title: string;
  icon: string;
  description: string;
  category: "collection" | "coincidence" | "scale" | "community";
  current: number;
  unit: string | null;
  detail: ChallengeDetail;
  // [easy, medium, hard] почти везде; у «Семи дней» один элемент "solo"
  tiers: ChallengeTier[];
  // Самый сложный тир, где взят хоть один уровень — им подписывается карточка
  best_tier: ChallengeTierKey | null;
  best_level: ChallengeLevel | null;
  // Вкладка, открытая по умолчанию: первый ещё не пройденный до золота тир
  default_tier: ChallengeTierKey;
  // Насколько последняя пробежка продвинула счётчик (0 — не продвинула)
  recent_delta: number;
  /**
   * Дата последнего дня активности, по которой посчитан recent_delta. «Детали»
   * подсвечивают ею клетки, закрытые именно этой пробежкой.
   */
  recent_date: string | null;
};

export type ClubEntry = {
  code: "runs" | "volunteering";
  title: string;
  icon: string;
  current: number;
  thresholds: number[];
  earned: number[];
  next_threshold: number | null;
  to_next: number | null;
  pct_to_next: number;
  level_dates: Record<string, string | null>;
};

export type ClubPlatform = {
  platform_code: string;
  entries: ClubEntry[];
};

export type Clubs = {
  overall: ClubEntry[];
  platforms: ClubPlatform[];
};

export type AchievementBadge = {
  code: string;
  title: string;
  icon: string;
  level: ChallengeLevel;
  tier: ChallengeTierKey | null;
  tier_label: string | null;
  achieved_at: string | null;
};

export type AchievementsResponse = {
  challenges: Challenge[];
  badges: AchievementBadge[];
  summary: { gold: number; silver: number; bronze: number; total: number };
  clubs: Clubs;
};

export function getAchievements(platform?: string) {
  const query = platform ? `?platform=${encodeURIComponent(platform)}` : "";
  return apiFetch<AchievementsResponse>(`/achievements${query}`);
}

export type StartNumberPlanEntry = {
  location: string;
  location_slug: string;
  platform_code: string;
  date: string;
};

export type StartNumberPlanRow = {
  number: number;
  done: boolean;
  // По одному списку на колонку, длиной week_count: [E, E+1, E+2]
  weeks: StartNumberPlanEntry[][];
};

export type StartNumberPlan = {
  code: string;
  // Система, к которой сужен план; null — все
  platform_code: string | null;
  low: number;
  high: number;
  generated_for: string;
  // Сколько колонок в строке; подписи строит фронт
  week_count: number;
  rows: StartNumberPlanRow[];
};

export function getStartNumbersPlan(code: string, platform?: string | null) {
  const query = new URLSearchParams({ code });
  if (platform) {
    query.set("platform", platform);
  }
  return apiFetch<StartNumberPlan>(`/achievements/start-numbers-plan?${query.toString()}`);
}

export type GoalPreset = {
  goal_type: string;
  title: string;
  icon: string;
  unit: string;
  kind: "count" | "time" | "streak" | "percent";
  default_target: number;
  min: number;
  max: number;
  description: string;
  current_value: number;
  current_display: string | null;
};

export type GoalProgress = {
  goal_type: string;
  year: number;
  target_value: number;
  title: string;
  icon: string;
  unit: string;
  kind: "count" | "time" | "streak" | "percent";
  current_value: number;
  pct: number;
  done: boolean;
  on_track: boolean | null;
  forecast_value: number | null;
  current_display: string | null;
  target_display: string | null;
  // Насколько последняя пробежка продвинула цель (0 — не продвинула)
  recent_delta: number;
};

export type GoalsResponse = {
  year: number;
  max_goals: number;
  goals: GoalProgress[];
  presets: GoalPreset[];
};

export function getGoals() {
  return apiFetch<GoalsResponse>("/achievements/goals");
}

export function saveGoals(goals: Array<{ goal_type: string; target_value: number }>) {
  return apiFetch<GoalsResponse>("/achievements/goals", {
    method: "PUT",
    body: JSON.stringify({ goals }),
  });
}

export function demoGetOnThisDay() {
  return apiFetch<OnThisDay>("/demo/dashboard/on-this-day");
}

// ── «Моя история» — таймлайн вех ─────────────────────────────────────────────

export type MyHistoryMilestoneKind =
  | "first_run"
  | "first_run_platform"
  | "run_club"
  | "run_club_platform"
  | "location_club"
  | "volunteer_location_club"
  | "global_pr"
  | "pr"
  | "location_pr"
  | "location_course_record"
  | "location_age_group_record"
  | "first_foreign_parkrun"
  | "first_foreign_run"
  | "new_region"
  | "new_city"
  | "new_country"
  | "new_location"
  | "first_volunteer"
  | "volunteer_club"
  | "volunteer_club_platform"
  | "saturday_streak"
  | "saturday_run_streak"
  | "saturday_volunteer_streak";

export type MyHistoryMilestone = {
  kind: MyHistoryMilestoneKind;
  // Номер пробежки/клуба/волонтёрства (для юбилеев и клубов); для вех рекордных
  // серий — длина серии в субботах.
  number: number | null;
  event_date: string;
  platform_code: string;
  location_name: string;
  location_city: string | null;
  finish_time_display: string | null;
  finish_time_sec: number | null;
  position: number | null;
  gender_position: number | null;
  pace_display: string | null;
  // На сколько секунд улучшен личный рекорд (kind=pr).
  delta_sec: number | null;
  is_global_pr: boolean;
  region: string | null;
  country: string | null;
  role: string | null;
  event_url: string | null;
  // Охват рекорда локации: "global" | код системы (мультисистемные площадки),
  // null — монолокация (kind=location_course_record).
  record_scope?: string | null;
  // Возрастная группа («30–34») — kind=location_age_group_record.
  age_group?: string | null;
};

export type MyHistory = {
  milestones: MyHistoryMilestone[];
  total: number;
};

export function getMyHistory() {
  return apiFetch<MyHistory>("/dashboard/my-history");
}

export function demoGetMyHistory() {
  return apiFetch<MyHistory>("/demo/dashboard/my-history");
}

// ── Админка: виды вех «Моя история» (вкл/выкл) ──────────────────────────────

export type HistoryMilestoneKindSetting = {
  kind: MyHistoryMilestoneKind;
  label: string;
  description: string;
  enabled: boolean;
};

export type EventReportLocationItem = {
  location_id: string;
  location_name: string;
  city: string | null;
  platform_code: string;
  platform_name: string;
  events_count: number;
  last_event_date: string;
};

export type EventReportDateItem = {
  event_id: string;
  event_date: string;
  event_number: number | null;
  finishers_count: number;
};

export type EventReportPerson = {
  participant_id: string | null;
  name: string | null;
  profile_url: string | null;
};

export type EventReportCountedPerson = EventReportPerson & { count: number };

export type EventReportOneStepPerson = EventReportCountedPerson & { next_milestone: number };

export type EventReportTopFinish = EventReportPerson & {
  position: number | null;
  finish_time_sec: number | null;
  finish_time_display: string;
  age_category: string | null;
  gender: string | null;
  gender_rank: number;
  gender_total: number;
  age_rank: number;
  age_total: number;
  gender_qualified: boolean;
  age_qualified: boolean;
};

export type EventReportCourseRecord = EventReportPerson & {
  gender: string;
  time_sec: number;
  previous_sec: number;
};

export type LocationContactLink = {
  id: string;
  telegram_url: string;
  label: string | null;
};

export type EventReport = {
  event: {
    event_id: string;
    event_date: string;
    event_number: number | null;
    location_id: string;
    location_name: string;
    canonical_name: string | null;
    platform_code: string;
    platform_name: string;
    source_url: string | null;
    events_total_at_location: number;
    telegram_contacts: LocationContactLink[];
    do_not_disturb: boolean;
  };
  header: {
    finishers: number;
    volunteers: number;
    avg_time_sec: number | null;
    best_male_sec: number | null;
    best_female_sec: number | null;
    previous_event_date: string | null;
    previous_event_finishers: number | null;
    attendance_record: boolean;
    prior_max_finishers: number | null;
  };
  course_records: EventReportCourseRecord[];
  top_finishes: EventReportTopFinish[];
  stats: {
    newcomers: EventReportPerson[];
    guests: EventReportPerson[];
    personal_bests: EventReportPerson[];
    location_bests: EventReportPerson[];
    comebacks: EventReportPerson[];
    first_volunteers: EventReportPerson[];
    first_volunteers_at_location: EventReportPerson[];
    new_role_volunteers: EventReportPerson[];
  };
  location_milestones: { runs: EventReportCountedPerson[]; volunteering: EventReportCountedPerson[] };
  one_step: { runs: EventReportOneStepPerson[]; volunteering: EventReportOneStepPerson[] };
  clubs: { runs: EventReportCountedPerson[]; volunteering: EventReportCountedPerson[] };
  global_run_jubilees: EventReportCountedPerson[];
  post_text: string;
};

export function getAdminEventReportLocations() {
  return apiFetch<{ items: EventReportLocationItem[] }>("/admin/event-report/locations");
}

export function getAdminEventReportDates(locationId: string) {
  return apiFetch<{ items: EventReportDateItem[] }>(
    `/admin/event-report/dates?location_id=${encodeURIComponent(locationId)}`,
  );
}

export type DigestDateItem = {
  event_date: string;
  events_count: number;
  locations_count: number;
};

export type DigestCourseRecord = {
  gender: string;
  record_sec: number;
  record_name: string | null;
  previous_sec: number | null;
  previous_date: string | null;
  previous_name: string | null;
  years_since: number;
};

export type DigestAgeRecord = {
  age_group: string | null;
  record_sec: number;
  record_name: string | null;
  previous_sec: number | null;
  previous_date: string | null;
  previous_name: string | null;
  group_total: number | null;
  years_since: number;
};

export type DigestCard = {
  location_id: string;
  location_name: string;
  city: string | null;
  country: string | null;
  location_url: string | null;
  platform_code: string;
  platform_name: string;
  telegram_contacts: LocationContactLink[];
  do_not_disturb: boolean;
  comment: string | null;
  score: number;
  importance: "high" | "medium" | "low";
  course_records: DigestCourseRecord[];
  age_records: DigestAgeRecord[];
  attendance_record: { finishers: number; prior_max: number } | null;
  milestones: {
    runs: Array<{ name: string | null; count: number }>;
    volunteering: Array<{ name: string | null; count: number }>;
  };
  post_text: string;
};

export type RecordsDigest = {
  event_date: string;
  generated_locations: number;
  with_telegram: number;
  without_telegram: number;
  do_not_disturb: number;
  cards: DigestCard[];
};

export function getAdminDigestDates(limit = 20) {
  return apiFetch<{ items: DigestDateItem[] }>(`/admin/records-digest/dates?limit=${limit}`);
}

export function getAdminRecordsDigest(eventDate: string) {
  return apiFetch<RecordsDigest>(
    `/admin/records-digest?event_date=${encodeURIComponent(eventDate)}`,
    undefined,
    { timeoutMs: 120_000 },
  );
}

// Разметка открытий локаций: какой старт считается торжественным открытием.
// У 5 вёрст, parkrun и RunPark это событие №1 из протокола, у С95 номер
// проставляется руками — по номерам её забегов открытие не опознать.
export type LocationOpeningEvent = {
  event_id: string;
  event_number: number | null;
  event_date: string;
  title: string | null;
  source_url: string | null;
  finishers: number | null;
  is_opening: boolean;
};

// Открытие той же физической локации, случившееся раньше в другой системе:
// у локации открытие одно (самое раннее), и тогда разметка здесь ни на что
// не влияет.
export type EarlierOpening = {
  platform_code: string;
  event_date: string;
  location_name: string;
};

export type LocationOpeningItem = {
  location_id: string;
  location_name: string;
  location_city: string | null;
  external_key: string;
  source_url: string | null;
  platform_code: string;
  opening_event_number: number | null;
  // manual — задано руками, auto — событие №1, none — открытия у площадки нет.
  opening_source: "manual" | "auto" | "none";
  opening_event: LocationOpeningEvent | null;
  opening_event_missing: boolean;
  earlier_opening: EarlierOpening | null;
  note: string | null;
  updated_at: string | null;
  updated_by: string | null;
  first_events: LocationOpeningEvent[];
};

export type LocationOpeningList = {
  platform: string;
  items: LocationOpeningItem[];
  total: number;
  with_opening: number;
  manual_total: number;
  needs_manual: boolean;
};

export type LocationOpeningSaved = {
  location_id: string;
  location_name: string;
  platform_code: string;
  opening_event_number: number | null;
  opening_source: "manual" | "auto" | "none";
  opening_event: LocationOpeningEvent | null;
  opening_event_missing: boolean;
  note: string | null;
  updated_at: string | null;
};

export function getAdminLocationOpenings(params: {
  platform: string;
  q?: string;
  onlyMissing?: boolean;
}) {
  const search = new URLSearchParams({ platform: params.platform });
  if (params.q) search.set("q", params.q);
  if (params.onlyMissing) search.set("only_missing", "true");
  return apiFetch<LocationOpeningList>(`/admin/location-openings?${search}`, undefined, {
    timeoutMs: 60_000,
  });
}

export function setAdminLocationOpening(
  locationId: string,
  body: { opening_event_number: number | null; note: string | null },
) {
  return apiFetch<LocationOpeningSaved>(`/admin/location-openings/${locationId}`, {
    method: "PUT",
    body: JSON.stringify(body),
  });
}

export function clearAdminLocationOpening(locationId: string) {
  return apiFetch<LocationOpeningSaved>(`/admin/location-openings/${locationId}`, {
    method: "DELETE",
  });
}

export type LocationContactPlatform = {
  location_id: string;
  location_name: string;
  platform_code: string;
  platform_name: string;
  is_cancelled: boolean;
  is_paused: boolean;
};

export type LocationContactItem = {
  group_key: string;
  // Локация, на которую вешаются общие для физической точки чат и настройки анонса.
  anchor_location_id: string;
  display_name: string;
  city: string | null;
  country: string | null;
  platforms: LocationContactPlatform[];
  contacts: LocationContactLink[];
  do_not_disturb: boolean;
  comment: string | null;
  updated_at: string | null;
};

export type LocationContactList = {
  items: LocationContactItem[];
  total: number;
  with_telegram: number;
  do_not_disturb_total: number;
};

export function getAdminLocationContacts(params: {
  q?: string;
  onlyMissing?: boolean;
  onlyDoNotDisturb?: boolean;
}) {
  const search = new URLSearchParams();
  if (params.q) search.set("q", params.q);
  if (params.onlyMissing) search.set("only_missing", "true");
  if (params.onlyDoNotDisturb) search.set("only_do_not_disturb", "true");
  const suffix = search.toString();
  return apiFetch<LocationContactList>(
    `/admin/location-contacts${suffix ? `?${suffix}` : ""}`,
    undefined,
    { timeoutMs: 60_000 },
  );
}

export function updateAdminLocationAnnounceSettings(
  locationId: string,
  body: { do_not_disturb: boolean; comment: string | null },
) {
  return apiFetch<{ location_id: string; do_not_disturb: boolean; comment: string | null; updated_at: string }>(
    `/admin/location-contacts/${locationId}/settings`,
    { method: "PUT", body: JSON.stringify(body) },
  );
}

export function createAdminLocationContactLink(
  locationId: string,
  body: { telegram_url: string; label: string | null },
) {
  return apiFetch<LocationContactLink>(`/admin/location-contacts/${locationId}/links`, {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export function updateAdminLocationContactLink(
  contactId: string,
  body: { telegram_url: string; label: string | null },
) {
  return apiFetch<LocationContactLink>(`/admin/location-contacts/links/${contactId}`, {
    method: "PUT",
    body: JSON.stringify(body),
  });
}

export function deleteAdminLocationContactLink(contactId: string) {
  return apiFetch<{ message: string }>(`/admin/location-contacts/links/${contactId}`, {
    method: "DELETE",
  });
}

export type BlogPostAdmin = {
  id: string;
  title: string;
  teaser: string;
  telegram_url: string;
  topic: string | null;
  published_at: string;
  clicks_count: number;
  is_published: boolean;
  created_at: string;
  updated_at: string;
};

export type BlogPostAdminPayload = {
  title: string;
  teaser: string;
  telegram_url: string;
  topic: string | null;
  published_at: string | null;
  is_published: boolean;
};

export function listAdminBlogPosts() {
  return apiFetch<{ items: BlogPostAdmin[]; total: number }>("/admin/blog/posts");
}

export function createAdminBlogPost(body: BlogPostAdminPayload) {
  return apiFetch<BlogPostAdmin>("/admin/blog/posts", {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export function updateAdminBlogPost(postId: string, body: BlogPostAdminPayload) {
  return apiFetch<BlogPostAdmin>(`/admin/blog/posts/${postId}`, {
    method: "PUT",
    body: JSON.stringify(body),
  });
}

export function deleteAdminBlogPost(postId: string) {
  return apiFetch<{ message: string }>(`/admin/blog/posts/${postId}`, { method: "DELETE" });
}

export type ReleaseAdmin = {
  id: string;
  version: string;
  title: string;
  body: string;
  released_at: string;
  is_published: boolean;
  created_at: string;
  updated_at: string;
};

export type ReleaseAdminPayload = {
  version: string;
  title: string;
  body: string;
  released_at: string | null;
  is_published: boolean;
};

/** Кандидаты следующей версии от последнего релиза в таблице (включая скрытые). */
export type ReleaseNextVersions = {
  current: string;
  major: string;
  minor: string;
  patch: string;
  fix: string;
};

export function listAdminReleases() {
  return apiFetch<{ items: ReleaseAdmin[]; total: number; next_versions: ReleaseNextVersions }>(
    "/admin/releases",
  );
}

export function createAdminRelease(body: ReleaseAdminPayload) {
  return apiFetch<ReleaseAdmin>("/admin/releases", {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export function updateAdminRelease(releaseId: string, body: ReleaseAdminPayload) {
  return apiFetch<ReleaseAdmin>(`/admin/releases/${releaseId}`, {
    method: "PUT",
    body: JSON.stringify(body),
  });
}

export function deleteAdminRelease(releaseId: string) {
  return apiFetch<{ message: string }>(`/admin/releases/${releaseId}`, { method: "DELETE" });
}

export function getAdminEventReport(eventId: string) {
  return apiFetch<EventReport>(
    `/admin/event-report?event_id=${encodeURIComponent(eventId)}`,
    undefined,
    { timeoutMs: 60_000 },
  );
}

// ── Настройки: персональный вкл/выкл видов вех «Моя история» ─────────────────

export type HistoryMilestoneSettings = {
  description: string;
  kinds: HistoryMilestoneKindSetting[];
};

export function getHistoryMilestoneSettings() {
  return apiFetch<HistoryMilestoneSettings>("/settings/history-milestones");
}

export function setHistoryMilestoneEnabled(kind: string, enabled: boolean) {
  return apiFetch<HistoryMilestoneSettings>(`/settings/history-milestones/${kind}`, {
    method: "PUT",
    body: JSON.stringify({ enabled }),
  });
}

// ── Оценки стартов (рейтинг парков) ─────────────────────────────────────────

export type ParticipationType = "run" | "volunteer";

// Загруженное фото: ссылку собирает бэкенд (публичный S3 на проде,
// /api/media в локальной разработке) — фронт её только показывает.
export type Photo = {
  id: string;
  url: string;
  width: number;
  height: number;
};

export type RunRating = {
  id: string;
  photos: Photo[];
  // Опаковый id старта: 'run:<uuid>' (бегун) / 'vol:<uuid>' (волонтёр).
  entry_id: string;
  participation_type: ParticipationType;
  run_result_id: string | null;
  score_overall: number;
  score_organization: number | null;
  score_route: number | null;
  score_community: number | null;
  comment: string | null;
  is_public: boolean;
  // можно ли ещё исправить/удалить (в пределах 3 месяцев) или уже зафиксировано
  editable: boolean;
  created_at: string;
  updated_at: string;
};

export type MyRating = RunRating & {
  event_date: string;
  platform_code: string;
  location_name: string;
  location_city: string | null;
  finish_time_display: string | null;
  position: number | null;
  is_pr: boolean;
  event_url: string | null;
};

export type MyRatings = {
  can_rate: boolean;
  total_runs: number;
  min_runs_required: number;
  create_window_days: number;
  edit_window_days: number;
  ratings: MyRating[];
};

export function getMyRatings() {
  return apiFetch<MyRatings>("/ratings/mine");
}

// ── Админка: рейтинг ────────────────────────────────────────────────────────

export type AdminRatingRow = {
  id: string;
  user_id: string;
  user_display: string;
  user_serial: number | null;
  event_date: string;
  platform_code: string;
  location_key: string;
  location_name: string;
  location_city: string | null;
  score_overall: number;
  score_organization: number | null;
  score_route: number | null;
  score_community: number | null;
  comment: string | null;
  is_public: boolean;
  editable: boolean;
  participation_type: ParticipationType;
  created_at: string;
  /** Фото, приложенные к отзыву. */
  photos: { id: string; url: string; width: number; height: number }[];
};

export type AdminRatingsStatGroup = {
  last_1d: number;
  last_7d: number;
  last_30d: number;
  total: number;
};

export type AdminRatingsStats = {
  by_rating_date: AdminRatingsStatGroup;
  by_event_date: AdminRatingsStatGroup;
};

export type AdminRatings = { ratings: AdminRatingRow[]; stats: AdminRatingsStats };

export function getAdminRatings() {
  return apiFetch<AdminRatings>("/admin/ratings");
}

export type AdminLocationRatingRow = {
  location_key: string;
  location_name: string;
  voters: number;
  ratings: number;
  avg_overall: number | null;
  avg_organization: number | null;
  avg_route: number | null;
  avg_community: number | null;
  meets_threshold: boolean;
};

export type AdminLocationRatings = {
  excluded_locals: boolean;
  min_voters: number;
  locations: AdminLocationRatingRow[];
};

export function getAdminLocationRatings(excludeLocals: boolean) {
  return apiFetch<AdminLocationRatings>(
    `/admin/ratings/locations?exclude_locals=${excludeLocals ? "true" : "false"}`,
  );
}

export type EligibleRun = {
  entry_id: string;
  participation_type: ParticipationType;
  run_result_id: string | null;
  event_date: string;
  platform_code: string;
  location_name: string;
  location_city: string | null;
  // Канонический ключ площадки — совпадает с identity_key страницы локации.
  // Опционально: страницы «Пробежки»/«Волонтёрство» собирают EligibleRun из
  // своих строк, где ключа нет, — он нужен только карточке на странице локации.
  location_identity_key?: string | null;
  finish_time_display: string | null;
  position: number | null;
  is_pr: boolean;
  volunteer_role: string | null;
  event_url: string | null;
  // Старт из добора истории: вне окна создания, единственный шанс оценить локацию.
  is_legacy?: boolean;
  my_rating: RunRating | null;
};

export type RatingEligibility = {
  can_rate: boolean;
  total_runs: number;
  min_runs_required: number;
  window_days: number;
  runs: EligibleRun[];
};

export type RatingUpsert = {
  score_overall: number;
  score_organization?: number | null;
  score_route?: number | null;
  score_community?: number | null;
  comment?: string | null;
  is_public: boolean;
};

export function getEligibleRuns() {
  return apiFetch<RatingEligibility>("/ratings/eligible-runs");
}

// Опаковый id старта для оценки бегуна (для волонтёра приходит с бэка).
export function runEntryId(runResultId: string) {
  return `run:${runResultId}`;
}

export function putRunRating(entryId: string, body: RatingUpsert) {
  return apiFetch<RunRating>(`/ratings/entry/${encodeURIComponent(entryId)}`, {
    method: "PUT",
    body: JSON.stringify(body),
  });
}

export function deleteRunRating(entryId: string) {
  return apiFetch<void>(`/ratings/entry/${encodeURIComponent(entryId)}`, {
    method: "DELETE",
  });
}

// Фото в отзыве: до 5 штук, сжатие до 2K делает сервер. Отдельный upload —
// apiFetch всегда ставит Content-Type: application/json, а multipart нужен
// со своим boundary, который проставляет сам браузер.
export const MAX_RATING_PHOTOS = 5;

export async function uploadRatingPhoto(entryId: string, file: File): Promise<Photo> {
  const form = new FormData();
  form.append("file", file);
  const response = await fetch(
    `${API_BASE}/ratings/entry/${encodeURIComponent(entryId)}/photos`,
    { method: "POST", credentials: "include", body: form },
  );
  if (!response.ok) {
    throw new ApiError(await readErrorDetail(response), response.status);
  }
  return (await response.json()) as Photo;
}

export async function deleteRatingPhoto(photoId: string): Promise<void> {
  const response = await fetch(`${API_BASE}/ratings/photos/${photoId}`, {
    method: "DELETE",
    credentials: "include",
  });
  if (!response.ok) {
    throw new ApiError(await readErrorDetail(response), response.status);
  }
}

export type CoRunnerItem = {
  participant_key: string;
  display_name: string | null;
  profile_urls: Record<string, string>;
  platform_codes: string[];
  site_serial_id: number | null;
  meetings: number;
  my_wins: number;
  their_wins: number;
  timed_meetings: number;
  first_meeting_date: string | null;
  last_meeting_date: string | null;
};

export type CoRunnerMeetingItem = {
  event_date: string;
  platform_code: string;
  location_name: string;
  my_time_sec: number | null;
  their_time_sec: number | null;
  my_position: number | null;
  their_position: number | null;
  event_url: string | null;
};

export function getCoRunners(limit = 100) {
  return apiFetch<CoRunnerItem[]>(`/runs/co-runners?limit=${limit}`);
}

export function getCoRunnerMeetings(participantKey: string) {
  return apiFetch<CoRunnerMeetingItem[]>(`/runs/co-runners/${participantKey}/meetings`);
}

export function listRuns(includeTest = false, limit = 200, offset = 0) {
  const params = new URLSearchParams({ limit: String(limit), offset: String(offset) });
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

export function getWins(includeTest = false) {
  const params = new URLSearchParams();
  if (includeTest) {
    params.set("include_test", "true");
  }
  const query = params.toString();
  return apiFetch<WinItem[]>(`/runs/wins${query ? `?${query}` : ""}`);
}

export function listVolunteering(includeTest = false, limit = 200, offset = 0) {
  const params = new URLSearchParams({ limit: String(limit), offset: String(offset) });
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
  location_slug?: string | null;
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
  location_slug?: string | null;
  name: string;
  city: string | null;
  region?: string | null;
  latitude: number | null;
  longitude: number | null;
  has_coordinates: boolean;
  is_paused: boolean;
  is_cancelled?: boolean;
  is_foreign?: boolean;
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

export function getHomeDistanceDetail(includeTest = false) {
  const query = includeTest ? "?include_test=true" : "";
  return apiFetch<HomeDistanceDetail>(`/locations/visited/home-distance${query}`);
}

export function getPublicProfileHomeDistanceDetail(serialId: number, includeTest = false) {
  const query = includeTest ? "?include_test=true" : "";
  return apiFetch<HomeDistanceDetail>(
    `/users/${serialId}/profile/locations/visited/home-distance${query}`,
  );
}

export function getCatalogLocationsMap() {
  return apiFetch<MapLocationsResponse>("/locations/catalog/map");
}

export type CatalogLocationTableRow = {
  row_key: string;
  catalog_identity_key: string;
  location_id: string;
  location_slug?: string | null;
  name: string;
  city: string | null;
  region: string | null;
  country: string | null;
  platform_code: string;
  is_paused: boolean;
  is_cancelled: boolean;
  has_coordinates: boolean;
  location_url: string | null;
  visited: boolean;
  first_visit_date: string | null;
  // Система самого раннего визита — может отличаться от platform_code строки
  first_visit_platform: string | null;
  // {система: дата первого визита} — отметка пересчитывается под фильтр систем
  visits_by_platform: Record<string, string>;
};

export type CatalogLocationsTableResponse = {
  rows: CatalogLocationTableRow[];
  total_rows: number;
};

export function getCatalogLocationsTable(includeTest = false) {
  const query = includeTest ? "?include_test=true" : "";
  return apiFetch<CatalogLocationsTableResponse>(`/locations/catalog/table${query}`);
}

export type LocationPagePlatform = {
  platform_code: string;
  location_name: string;
  external_key: string;
  url: string | null;
  first_event_date: string | null;
  last_event_date: string | null;
  events_count: number;
  is_active: boolean | null;
};

export type LocationCourseRecord = {
  finish_time_sec: number;
  finish_time_display: string;
  runner_name: string | null;
  event_date: string | null;
  platform_code: string | null;
};

export type LocationAttendanceRecord = {
  finishers: number;
  event_date: string | null;
  event_number: number | null;
  platform_code: string | null;
};

/** Юбиляр клубного порога на старте: «Елена Филиппова — 25-й финиш». */
export type LocationMilestone = { name: string; count: number };

/** Кому до клубного порога остался один финиш. */
export type LocationOneStep = { name: string; next: number };

export type LocationLastEvent = {
  event_date: string;
  event_number: number | null;
  platform_code: string;
  finishers: number | null;
  volunteers: number | null;
  avg_time_sec: number | null;
  avg_time_display: string | null;
  best_male_time_sec: number | null;
  best_male_time_display: string | null;
  best_female_time_sec: number | null;
  best_female_time_display: string | null;
  debutants: number | null;
  first_at_location: number | null;
  prs: number | null;
  male_finishers: number | null;
  female_finishers: number | null;
  best_male_name: string | null;
  best_female_name: string | null;
  milestones: LocationMilestone[];
  one_step: LocationOneStep[];
};

export type LocationPageStats = {
  events_count: number;
  finishers_total: number;
  unique_participants: number;
  volunteers_total: number;
  unique_volunteers: number;
  avg_finish_time_sec: number | null;
  avg_finish_time_display: string | null;
  avg_finishers: number | null;
  attendance_record: LocationAttendanceRecord | null;
  course_records: { male: LocationCourseRecord | null; female: LocationCourseRecord | null };
  first_event_date: string | null;
  last_event_date: string | null;
  median_finish_time_sec: number | null;
  median_finish_time_display: string | null;
  last_event: LocationLastEvent | null;
  avg_finish_time_delta_sec: number | null;
  median_finish_time_delta_sec: number | null;
};

export type LocationHistogramRow = {
  start_sec: number;
  gender: "male" | "female" | null;
  age_group: string | null;
  count: number;
};

export type LocationAgeGroupTopRow = {
  place: number;
  name: string | null;
  handle?: string | null;
  best_time_sec: number;
  best_time_display: string;
};

export type LocationAgeGroupRecord = {
  key: string;
  runner_handle?: string | null;
  gender: "male" | "female";
  age_group: string;
  finish_time_sec: number;
  finish_time_display: string | null;
  runner_name: string | null;
  event_date: string | null;
  platform_code: string | null;
  top: LocationAgeGroupTopRow[];
  // Размер группы: участников и финишей всего на локации
  runners_total: number;
  finishes_total: number;
};

export type LocationCityNeighbor = {
  slug: string;
  name: string;
  events_count: number;
};

export type LocationDescriptionSection = {
  title: string | null;
  text: string;
};

export type LocationDescriptionLink = {
  title: string;
  url: string;
};

/** Описание площадки с сайта системы: когда старт, трасса, как добраться. Текст чужой — source_url обязателен к показу. */
export type LocationDescription = {
  platform_code: string;
  schedule_text: string | null;
  course_text: string | null;
  travel_text: string | null;
  travel_sections: LocationDescriptionSection[];
  links: LocationDescriptionLink[];
  source_url: string | null;
  updated_at: string | null;
};

export type LocationPage = {
  slug: string;
  identity_key: string;
  name: string;
  city: string | null;
  region: string | null;
  country: string | null;
  is_paused: boolean;
  is_cancelled: boolean;
  latitude: number | null;
  longitude: number | null;
  map_url: string | null;
  start_point_url: string | null;
  platforms: LocationPagePlatform[];
  stats: LocationPageStats;
  histogram: { bin_size_sec: number; rows: LocationHistogramRow[] };
  age_group_records: LocationAgeGroupRecord[];
  city_locations?: LocationCityNeighbor[];
  description?: LocationDescription | null;
};

export type LocationIndexItem = {
  slug: string;
  identity_key: string;
  name: string;
  city: string | null;
  region: string | null;
  country: string | null;
  platform_codes: string[];
  is_paused: boolean;
  is_cancelled: boolean;
  events_count: number;
  finishers_total: number;
  first_event_date: string | null;
  last_event_date: string | null;
  best_male_time_sec: number | null;
  best_male_time_display: string | null;
  best_female_time_sec: number | null;
  best_female_time_display: string | null;
  attendance_record_finishers: number | null;
  attendance_record_date: string | null;
};

export type LocationsIndexResponse = {
  items: LocationIndexItem[];
  total: number;
};

export type LocationEventRow = {
  event_date: string;
  platform_code: string;
  event_number: number | null;
  overall_number: number;
  finishers: number | null;
  volunteers: number | null;
  best_male_time_sec: number | null;
  best_male_time_display: string | null;
  best_male_runner_name: string | null;
  best_male_runner_serial_id: number | null;
  best_female_time_sec: number | null;
  best_female_time_display: string | null;
  best_female_runner_name: string | null;
  best_female_runner_serial_id: number | null;
  avg_time_sec: number | null;
  avg_time_display: string | null;
  debutants: number | null;
  first_at_location: number | null;
  prs: number | null;
  has_protocol: boolean;
  protocol_url: string | null;
  is_attendance_record: boolean;
  is_course_record_male: boolean;
  is_course_record_female: boolean;
  is_platform_attendance_record: boolean;
  is_platform_course_record_male: boolean;
  is_platform_course_record_female: boolean;
};

export type LocationEvents = {
  slug: string;
  name: string;
  total: number;
  items: LocationEventRow[];
};

export function getLocationPage(slug: string) {
  return apiFetch<LocationPage>(`/locations/page/${encodeURIComponent(slug)}`);
}

export function getLocationEvents(slug: string) {
  return apiFetch<LocationEvents>(`/locations/page/${encodeURIComponent(slug)}/events`);
}

export type LocationLeaderRunner = {
  name: string | null;
  handle?: string | null;
  runs_count: number;
  best_time_sec: number | null;
  best_time_display: string | null;
};

export type LocationLeaderVolunteer = {
  name: string | null;
  handle?: string | null;
  count: number;
};

export type LocationLeaders = {
  slug: string;
  name: string;
  runners: LocationLeaderRunner[];
  volunteers: LocationLeaderVolunteer[];
};

export function getLocationLeaders(slug: string) {
  return apiFetch<LocationLeaders>(`/locations/page/${encodeURIComponent(slug)}/leaders`);
}

/** Место участника в топе локации по одной его возрастной группе. */
export type LocationAgeGroupStanding = {
  // Тот же ключ, что у строки в age_group_records: по нему плитка раскрывает топ-5.
  key: string;
  gender: "male" | "female";
  age_group: string;
  label: string;
  runs_count: number;
  best_time_sec: number;
  best_time_display: string;
  best_time_date: string | null;
  last_run_date: string | null;
  place: number | null;
  total: number;
};

/** Плитка «сколько отсюда до дома» на странице локации. */
export type LocationHomeDistance = {
  /** null — координат площадки или домашней локации нет. */
  distance_km: number | null;
  is_home: boolean;
  /** Зелёная маркировка плитки — «здесь уже бегал», серая — «ещё не был». */
  visited: boolean;
  run_count: number;
  home_name: string;
  home_slug: string | null;
  home_is_auto: boolean;
};

export type LocationPersonalStats = {
  slug: string;
  name: string;
  runs_count: number;
  total_runs: number;
  best_time_sec: number | null;
  best_time_display: string | null;
  best_time_date: string | null;
  avg_time_sec: number | null;
  avg_time_display: string | null;
  first_run_date: string | null;
  last_run_date: string | null;
  volunteering_count: number;
  /** Любимая роль на этой локации: чаще всего выходил. */
  top_volunteer_role: { role: string; count: number } | null;
  // Место в топе по пробежкам — только внутри своего пола
  gender: string | null;
  rank_by_runs_gender: number | null;
  runners_total_gender: number | null;
  age_groups: LocationAgeGroupStanding[];
  /** null — домашняя локация не определилась (у пользователя нет пробежек). */
  home_distance: LocationHomeDistance | null;
};

export function getLocationPersonalStats(slug: string) {
  return apiFetch<LocationPersonalStats>(`/locations/page/${encodeURIComponent(slug)}/me`);
}

export function getLocationsIndex() {
  return apiFetch<LocationsIndexResponse>("/locations/index");
}

export type LastResultsItem = {
  slug: string;
  identity_key: string;
  name: string;
  city: string | null;
  region: string | null;
  country: string | null;
  platform_codes: string[];
  is_paused: boolean;
  is_cancelled: boolean;
  event_date: string;
  event_platform_codes: string[];
  event_number: number | null;
  is_last_saturday: boolean;
  finishers: number | null;
  volunteers: number | null;
  debutants: number | null;
  prs: number | null;
  best_male_time_sec: number | null;
  best_male_time_display: string | null;
  best_female_time_sec: number | null;
  best_female_time_display: string | null;
  avg_time_sec: number | null;
  avg_time_display: string | null;
  has_protocol: boolean;
  protocol_url: string | null;
};

export type LastResultsResponse = {
  saturday_date: string | null;
  items: LastResultsItem[];
  total: number;
};

export function getLastResults() {
  return apiFetch<LastResultsResponse>("/locations/last-results");
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

export type PrivacySettings = {
  enabled: boolean;
  description: string;
};

export function getPrivacySettings() {
  return apiFetch<PrivacySettings>("/settings/privacy");
}

export function updatePrivacySettings(enabled: boolean) {
  return apiFetch<PrivacySettings>("/settings/privacy", {
    method: "PUT",
    body: JSON.stringify({ enabled }),
  });
}

export type HomeLocationCandidate = {
  catalog_identity_key: string;
  name: string;
  city: string | null;
  region: string | null;
  run_count: number;
  volunteer_count: number;
  platform_codes: string[];
};

export type HomeLocationSettings = {
  location: HomeLocationCandidate | null;
  is_auto: boolean;
};

export function getHomeLocation() {
  return apiFetch<HomeLocationSettings>("/settings/home-location");
}

export function getHomeLocationCandidates() {
  return apiFetch<HomeLocationCandidate[]>("/settings/home-location/candidates");
}

export type ProfileSlugSettings = {
  slug: string | null;
  public_url: string | null;
  min_length: number;
  max_length: number;
};

export type ProfileSlugCheck = {
  normalized: string;
  available: boolean;
  reason: string | null;
};

export function getProfileSlug() {
  return apiFetch<ProfileSlugSettings>("/settings/profile-slug");
}

export function checkProfileSlug(slug: string) {
  return apiFetch<ProfileSlugCheck>(`/settings/profile-slug/check?slug=${encodeURIComponent(slug)}`);
}

export function updateProfileSlug(slug: string | null) {
  return apiFetch<ProfileSlugSettings>("/settings/profile-slug", {
    method: "PUT",
    body: JSON.stringify({ slug }),
  });
}

export type ProfileHandleResolve = {
  serial_id: number;
  display_name: string | null;
  public_slug: string | null;
};

export function resolveProfileHandle(handle: string) {
  return apiFetch<ProfileHandleResolve>(`/users/resolve/${encodeURIComponent(handle)}`);
}

export function updateHomeLocation(catalogIdentityKey: string | null) {
  return apiFetch<HomeLocationSettings>("/settings/home-location", {
    method: "PUT",
    body: JSON.stringify({ catalog_identity_key: catalogIdentityKey }),
  });
}

export type AdminPlatformLinkBrief = {
  platform_code: string;
  external_user_id: string;
  external_url: string;
  display_name: string | null;
  sync_status: string;
  run_count: number;
  volunteer_count: number;
  barcode_id: string | null;
};

export type AdminUserAuthBrief = {
  provider: string;
  label: string;
  external_id: string;
};

export type AdminUserHomeLocationCandidate = {
  identity_key: string;
  name: string;
  city: string | null;
  slug: string | null;
  run_days: number;
  volunteer_days: number;
};

export type AdminUserHomeLocation = {
  identity_key: string;
  name: string;
  slug: string | null;
  city: string | null;
  region: string | null;
  run_days: number;
  volunteer_days: number;
  // Дом выбран руками в настройках, иначе определён автоматически.
  is_manual: boolean;
  // Правило отбора исчерпано: площадки поделили первое место, все они в tied.
  is_tie: boolean;
  tied: AdminUserHomeLocationCandidate[];
  locations_total: number;
};

export type AdminUserListItem = {
  id: string;
  serial_id: number | null;
  telegram_id: number | null;
  telegram_username: string | null;
  display_name: string | null;
  public_slug: string | null;
  profile_private: boolean;
  auth_logins: AdminUserAuthBrief[];
  news_subscribed: boolean;
  consent_accepted: boolean;
  created_at: string;
  last_login_at: string | null;
  total_runs: number | null;
  total_volunteering: number | null;
  platform_links: AdminPlatformLinkBrief[];
  home_location: AdminUserHomeLocation | null;
};

export type AdminUserListResponse = {
  items: AdminUserListItem[];
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
    avatar_url: string | null;
    avatar_full_url: string | null;
    auth_logins: AdminUserAuthBrief[];
  };
  stats: DashboardStats;
  computed_at: string | null;
  platform_links: AdminPlatformLinkBrief[];
};

export type AdminLoginEventItem = {
  ts: string;
  event_type: string;
  provider: string;
  ip: string;
  user_agent: string;
  device_ref: string;
  session_ref: string;
};

export type AdminLoginEventsResponse = {
  items: AdminLoginEventItem[];
  logins: number;
  logouts: number;
  devices: number;
  unexpected_relogins: number;
};

export function getAdminUserLoginEvents(userId: string, limit = 100) {
  return apiFetch<AdminLoginEventsResponse>(
    `/admin/users/${userId}/login-events?limit=${limit}`,
  );
}

export type AdminUsersSort = "created" | "runs" | "volunteering" | "profile";
export type AdminUsersSortDirection = "asc" | "desc";

export function listAdminUsers(
  query = "",
  limit = 100,
  offset = 0,
  sort: AdminUsersSort = "created",
  direction: AdminUsersSortDirection = "desc",
) {
  const params = new URLSearchParams();
  if (query.trim()) {
    params.set("q", query.trim());
  }
  params.set("limit", String(limit));
  params.set("offset", String(offset));
  params.set("sort", sort);
  params.set("direction", direction);
  return apiFetch<AdminUserListResponse>(`/admin/users?${params.toString()}`);
}

export function triggerAdminUserSyncPlatform(userId: string, platformCode: string) {
  return apiFetch<SyncRefreshResponse>(`/admin/users/${userId}/sync/${platformCode}`, {
    method: "POST",
  });
}

const ADMIN_PREVIEW_PAGE_SIZE = 200;

async function fetchAllAdminPreviewPages<T>(
  fetchPage: (limit: number, offset: number) => Promise<T[]>,
): Promise<T[]> {
  const items: T[] = [];
  let offset = 0;
  while (true) {
    const page = await fetchPage(ADMIN_PREVIEW_PAGE_SIZE, offset);
    items.push(...page);
    if (page.length < ADMIN_PREVIEW_PAGE_SIZE) {
      break;
    }
    offset += page.length;
  }
  return items;
}

export function getAllUserRuns(includeTest = false) {
  return fetchAllAdminPreviewPages((limit, offset) => listRuns(includeTest, limit, offset));
}

export function getAllUserVolunteering(includeTest = false) {
  return fetchAllAdminPreviewPages((limit, offset) => listVolunteering(includeTest, limit, offset));
}

// Public profile API (serial_id — числовой ID пользователя)
export function getPublicProfileDashboard(serialId: number) {
  return apiFetch<AdminUserPreviewDashboard>(`/users/${serialId}/profile/dashboard`);
}

export function getPublicProfileRuns(serialId: number, limit = 200, offset = 0, includeTest = false) {
  const params = new URLSearchParams({ limit: String(limit), offset: String(offset) });
  if (includeTest) params.set("include_test", "true");
  return apiFetch<RunItem[]>(`/users/${serialId}/profile/runs?${params.toString()}`);
}

export function getPublicProfileVolunteering(serialId: number, limit = 200, offset = 0, includeTest = false) {
  const params = new URLSearchParams({ limit: String(limit), offset: String(offset) });
  if (includeTest) params.set("include_test", "true");
  return apiFetch<VolunteeringItem[]>(`/users/${serialId}/profile/volunteering?${params.toString()}`);
}

export function getAllPublicProfileRuns(serialId: number, includeTest = false) {
  return fetchAllAdminPreviewPages((limit, offset) => getPublicProfileRuns(serialId, limit, offset, includeTest));
}

export function getAllPublicProfileVolunteering(serialId: number, includeTest = false) {
  return fetchAllAdminPreviewPages((limit, offset) =>
    getPublicProfileVolunteering(serialId, limit, offset, includeTest),
  );
}

export function getPublicProfileHistory(serialId: number, includeTest = false) {
  const query = includeTest ? "?include_test=true" : "";
  return apiFetch<MyHistory>(`/users/${serialId}/profile/history${query}`);
}

export function getPublicProfileVisitedMap(serialId: number, includeTest = false) {
  const query = includeTest ? "?include_test=true" : "";
  return apiFetch<MapLocationsResponse>(`/users/${serialId}/profile/locations/visited/map${query}`);
}

export function getPublicProfileVisitedDetail(serialId: number, includeTest = false) {
  const query = includeTest ? "?include_test=true" : "";
  return apiFetch<UniqueLocationsDetailResponse>(
    `/users/${serialId}/profile/locations/visited/detail${query}`,
  );
}

export function getPublicProfileCatalogTable(serialId: number, includeTest = false) {
  const query = includeTest ? "?include_test=true" : "";
  return apiFetch<CatalogLocationsTableResponse>(
    `/users/${serialId}/profile/locations/catalog/table${query}`,
  );
}

export function getPublicProfileAchievements(serialId: number, platform?: string) {
  const query = platform ? `?platform=${encodeURIComponent(platform)}` : "";
  return apiFetch<AchievementsResponse>(`/users/${serialId}/profile/achievements${query}`);
}

export function getPublicProfileCoRunners(serialId: number, limit = 100) {
  return apiFetch<CoRunnerItem[]>(`/users/${serialId}/profile/co-runners?limit=${limit}`);
}

export function getPublicProfileCoRunnerMeetings(serialId: number, participantKey: string) {
  return apiFetch<CoRunnerMeetingItem[]>(
    `/users/${serialId}/profile/co-runners/${participantKey}/meetings`,
  );
}

export function getPublicProfileBestResults(serialId: number, includeTest = false) {
  const query = includeTest ? "?include_test=true" : "";
  return apiFetch<BestResultItem[]>(`/users/${serialId}/profile/runs/best-results${query}`);
}

export function getPublicProfilePersonalRecords(serialId: number, includeTest = false) {
  const query = includeTest ? "?include_test=true" : "";
  return apiFetch<PersonalRecordItem[]>(`/users/${serialId}/profile/runs/personal-records${query}`);
}

export function getPublicProfileWins(serialId: number, includeTest = false) {
  const query = includeTest ? "?include_test=true" : "";
  return apiFetch<WinItem[]>(`/users/${serialId}/profile/runs/wins${query}`);
}

export function getPublicProfileVolunteerRoleStats(serialId: number, includeTest = false) {
  const query = includeTest ? "?include_test=true" : "";
  return apiFetch<VolunteerRoleStatItem[]>(
    `/users/${serialId}/profile/volunteering/role-stats${query}`,
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

export type BlockedSlugItem = {
  id: string;
  slug: string;
  comment: string | null;
  created_at: string;
};

export type BlockedSlugListResponse = {
  items: BlockedSlugItem[];
  system_slugs: string[];
};

export function listAdminBlockedSlugs() {
  return apiFetch<BlockedSlugListResponse>("/admin/profile-slugs/blocked");
}

export function createAdminBlockedSlug(body: { slug: string; comment?: string | null }) {
  return apiFetch<BlockedSlugItem>("/admin/profile-slugs/blocked", {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export function deleteAdminBlockedSlug(entryId: string) {
  return apiFetch<{ message: string }>(`/admin/profile-slugs/blocked/${encodeURIComponent(entryId)}`, {
    method: "DELETE",
  });
}

export type AdminSiteStatsOverview = {
  users_total: number;
  users_profile_public: number;
  users_profile_private: number;
  users_with_consent: number;
  users_active_period: number;
  users_new_period: number;
  users_with_any_link: number;
  users_with_all_three_links: number;
  platform_links_total: number;
  links_new_period: number;
  links_by_platform: Record<string, number>;
  pageviews_period: number;
  unique_visitors_period: number;
  logins_period: number;
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

// Точный набор привязанных систем: человек попадает ровно в одну строку.
export type AdminLinkCombinationRow = { codes: string[]; users: number };

export type AdminSiteStatsResponse = {
  period_days: number;
  generated_at: string;
  overview: AdminSiteStatsOverview;
  link_combinations: AdminLinkCombinationRow[];
  users_without_links: number;
  users_new_by_day: AdminSiteStatsDayPoint[];
  links_new_by_day: AdminSiteStatsDayPoint[];
  logins_by_day: AdminSiteStatsDayPoint[];
  login_requests_by_day: AdminSiteStatsDayPoint[];
  pageviews_by_day: AdminSiteStatsPageviewsDay[];
};

export function getAdminSiteStats(periodDays = 30) {
  return apiFetch<AdminSiteStatsResponse>(`/admin/stats?period_days=${periodDays}`);
}

export type AdminGeographyCityRow = {
  city: string;
  region: string | null;
  users: number;
  users_new_period: number;
  locations: number;
};

export type AdminGeographyLocationRow = {
  identity_key: string;
  name: string;
  slug: string | null;
  city: string | null;
  region: string | null;
  users: number;
  users_new_period: number;
};

export type AdminUsersGeographyResponse = {
  generated_at: string;
  period_days: number;
  users_total: number;
  users_new_period: number;
  users_with_home: number;
  users_new_with_home: number;
  users_without_home: number;
  users_without_links: number;
  cities_total: number;
  locations_total: number;
  cities: AdminGeographyCityRow[];
  locations: AdminGeographyLocationRow[];
};

// Отдельный запрос от /admin/stats: срез считается по протоколам и заметно
// дольше остальных чисел страницы — грузим его независимо.
export function getAdminUsersGeography(periodDays = 30) {
  return apiFetch<AdminUsersGeographyResponse>(
    `/admin/stats/geography?period_days=${periodDays}`,
  );
}

export function recordSitePageview(
  path: string,
  authenticated: boolean,
  visitorKey: string,
  viewId?: string,
) {
  return apiFetch<void>("/stats/pageview", {
    method: "POST",
    body: JSON.stringify({ path, authenticated, visitor_key: visitorKey, view_id: viewId }),
  }).catch(() => {
    // ignore analytics errors
  });
}

export type PageAnalyticsRowStats = {
  views: number;
  unique_viewers: number;
  self_views: number;
  avg_duration_sec: number | null;
};

export type PageAnalyticsSection = PageAnalyticsRowStats & { page_type: string };

export type PageAnalyticsEntity = PageAnalyticsRowStats & {
  entity_key: string;
  label: string;
  href: string | null;
};

export type HomeAbVariantStats = {
  variant: string;
  views: number;
  viewers: number;
};

/** Переход по ссылке с главной: локация или профиль участника. */
export type HomeLinkClickStats = {
  kind: string;
  entity_key: string;
  label: string;
  href: string | null;
  clicks: number;
  visitors: number;
};

export type ShareFunnelRow = { event_type: string; events: number; visitors: number };
export type SharePairRow = { subject: string; entry: string; shown: number; opens: number };
export type ShareChannelRow = { channel: string; successes: number };
export type ShareCountRow = { value: string; count: number };

/** Воронка и разрезы фичи «Поделиться» (канал experiment="share"). */
export type ShareStats = {
  funnel: ShareFunnelRow[];
  pairs: SharePairRow[];
  channels: ShareChannelRow[];
  looks: ShareCountRow[];
  formats: ShareCountRow[];
  photo_added: number;
};

/** Разворачивание ссылки ботом мессенджера/поисковика (превью в чате). */
export type OgFetchRow = {
  page_type: string;
  entity_key: string;
  label: string;
  href: string | null;
  fetches: number;
  bots: number;
};

export type PageAnalyticsResponse = {
  date_from: string;
  date_to: string;
  generated_at: string;
  home_ab: HomeAbVariantStats[];
  home_links: HomeLinkClickStats[];
  share: ShareStats;
  og_fetches: OgFetchRow[];
  sections: PageAnalyticsSection[];
  top_profiles: PageAnalyticsEntity[];
  top_locations: PageAnalyticsEntity[];
};

/** Либо periodDays (последние N дней), либо явный диапазон dateFrom/dateTo. */
export function getAdminPageAnalytics(params: {
  periodDays?: number;
  dateFrom?: string;
  dateTo?: string;
}) {
  const query = new URLSearchParams();
  if (params.periodDays) {
    query.set("period_days", String(params.periodDays));
  }
  if (params.dateFrom) {
    query.set("date_from", params.dateFrom);
  }
  if (params.dateTo) {
    query.set("date_to", params.dateTo);
  }
  return apiFetch<PageAnalyticsResponse>(`/admin/page-analytics?${query.toString()}`);
}

export type SyncRunMetric = {
  key: string;
  label: string;
  value: number | string | boolean;
};

export type SyncRunItem = {
  id: number;
  pipeline: string;
  pipeline_label: string;
  platform: string;
  platform_label: string;
  status: string;
  started_at: string;
  finished_at: string | null;
  duration_ms: number | null;
  records_updated: number;
  error_count: number;
  skip_reason: string | null;
  metrics: SyncRunMetric[];
  errors: string[];
};

export type SyncPipelineSummary = {
  pipeline: string;
  pipeline_label: string;
  runs: number;
  ok: number;
  problems: number;
  skipped: number;
  errors_total: number;
  metrics: SyncRunMetric[];
  last_run_at: string | null;
  last_status: string | null;
};

export type SyncPlatformSummary = {
  platform: string;
  platform_label: string;
  runs: number;
  ok: number;
  problems: number;
  skipped: number;
  errors_total: number;
  metrics: SyncRunMetric[];
  pipelines: SyncPipelineSummary[];
};

export type AdminSyncRunsResponse = {
  generated_at: string;
  date_from: string;
  date_to: string;
  platforms: SyncPlatformSummary[];
  runs: SyncRunItem[];
  total: number;
};

/** История автообновления: итоги по платформам + лента запусков. */
export function getAdminSyncRuns(params: {
  periodDays?: number;
  dateFrom?: string;
  dateTo?: string;
  platform?: string;
  status?: string;
  limit?: number;
  offset?: number;
}) {
  const query = new URLSearchParams();
  if (params.periodDays) {
    query.set("period_days", String(params.periodDays));
  }
  if (params.dateFrom) {
    query.set("date_from", params.dateFrom);
  }
  if (params.dateTo) {
    query.set("date_to", params.dateTo);
  }
  if (params.platform) {
    query.set("platform", params.platform);
  }
  if (params.status) {
    query.set("run_status", params.status);
  }
  if (params.limit) {
    query.set("limit", String(params.limit));
  }
  if (params.offset) {
    query.set("offset", String(params.offset));
  }
  return apiFetch<AdminSyncRunsResponse>(`/admin/sync-runs?${query.toString()}`);
}

export function getDemoDashboard() {
  return apiFetch<AdminUserPreviewDashboard>("/demo/dashboard");
}

export function demoListRuns(limit = 200) {
  return apiFetch<RunItem[]>(`/demo/runs?limit=${limit}`);
}

export function demoGetCoRunners(limit = 100) {
  return apiFetch<CoRunnerItem[]>(`/demo/runs/co-runners?limit=${limit}`);
}

export function demoGetCoRunnerMeetings(participantKey: string) {
  return apiFetch<CoRunnerMeetingItem[]>(`/demo/runs/co-runners/${participantKey}/meetings`);
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

export function demoGetWins() {
  return apiFetch<WinItem[]>("/demo/runs/wins");
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
