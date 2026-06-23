import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { ConfirmModal } from "./ConfirmModal";
import { Snackbar } from "./Snackbar";
import {
  ApiError,
  confirmFiveVerstProfile,
  confirmParkrunProfile,
  confirmRunparkProfile,
  confirmS95Profile,
  listProfileLinks,
  previewFiveVerstProfile,
  previewParkrunProfile,
  previewRunparkProfile,
  previewS95Profile,
  triggerSyncRefreshPlatform,
  unlinkProfile,
  type PlatformLink,
  type ProfilePreview,
} from "../lib/api";
import {
  formatDate,
  formatDateTime,
  platformCodeLabel,
  profileDataFreshnessLines,
  profilePlatformSummaryLabel,
  syncStatusLabel,
} from "../lib/format";

type PlatformConfig = {
  code: string;
  hint: string;
  placeholder: string;
  openLabel: string;
  inputMode: "url" | "text";
  preview: (url: string, signal?: AbortSignal) => Promise<ProfilePreview>;
  confirm: (url: string, options?: { linkParkrun?: boolean }) => Promise<unknown>;
  emptyInputError: string;
  confirmSuccess: string;
  confirmSuccessBoth?: string;
  hasPublicProfile?: boolean;
};

const PLATFORMS: PlatformConfig[] = [
  {
    code: "five_verst",
    hint: "Ссылка https://5verst.ru/userstats/… или код участника — цифры либо буква A и цифры.",
    placeholder: "https://5verst.ru/userstats/… или 790096427",
    openLabel: "Открыть на 5verst.ru",
    inputMode: "text",
    preview: previewFiveVerstProfile,
    confirm: confirmFiveVerstProfile,
    emptyInputError: "Введите ссылку на профиль 5 вёрст или код участника",
    confirmSuccess: "Профиль 5 вёрст привязан. Синхронизация запущена.",
  },
  {
    code: "s95",
    hint: "Вставьте ссылку на профиль участника, например https://s95.ru/athletes/5207/",
    placeholder: "https://s95.ru/athletes/…",
    openLabel: "Открыть на s95.ru",
    inputMode: "url",
    preview: previewS95Profile,
    confirm: (url, options) => confirmS95Profile(url, options?.linkParkrun ?? false),
    emptyInputError: "Введите ссылку на профиль С95",
    confirmSuccess:
      "Профиль С95 привязан. Синхронизация поставлена в очередь (может занять несколько минут).",
    confirmSuccessBoth:
      "Профили С95 и parkrun привязаны. Синхронизация поставлена в очередь (может занять несколько минут).",
  },
  {
    code: "parkrun",
    hint: "Ссылка на parkrun.org.uk, штрихкод — A7035519 или просто ID 7035519.",
    placeholder: "https://www.parkrun.org.uk/parkrunner/… или 7035519",
    openLabel: "Открыть на parkrun.org.uk",
    inputMode: "text",
    preview: previewParkrunProfile,
    confirm: confirmParkrunProfile,
    emptyInputError: "Введите ссылку parkrun или штрихкод",
    confirmSuccess:
      "Профиль parkrun привязан. Синхронизация в очереди (запросы к parkrun.org.uk идут с паузой).",
  },
  {
    code: "runpark",
    hint: "Введите штрихкод участника RunPark — буква A и цифры, например A6871786.",
    placeholder: "A6871786",
    openLabel: "Открыть на runpark.ru",
    inputMode: "text",
    preview: previewRunparkProfile,
    confirm: confirmRunparkProfile,
    emptyInputError: "Введите штрихкод участника RunPark",
    confirmSuccess: "Профиль RunPark привязан.",
    hasPublicProfile: false,
  },
];

type PlatformFormState = {
  profileUrl: string;
  preview: ProfilePreview | null;
  previewLoading: boolean;
  confirmLoading: boolean;
  formError: string | null;
};

type SnackbarState = {
  open: boolean;
  variant: "default" | "error";
  title: string;
  message: ReactNode;
};

const closedSnackbar = (): SnackbarState => ({
  open: false,
  variant: "default",
  title: "",
  message: null,
});

function ProfileDataFreshness({
  dataUpdatedAt,
  dataThroughDate,
  dataSource,
}: {
  dataUpdatedAt?: string | null;
  dataThroughDate?: string | null;
  dataSource?: string | null;
}) {
  const { updatedLine, throughLine } = profileDataFreshnessLines(dataUpdatedAt, dataThroughDate);
  if (!updatedLine && !throughLine) {
    return null;
  }
  return (
    <div className="profile-data-freshness" role="note">
      {updatedLine && <p className="muted">{updatedLine}</p>}
      {throughLine && <p className="muted">{throughLine}</p>}
    </div>
  );
}

const emptyFormState = (): PlatformFormState => ({
  profileUrl: "",
  preview: null,
  previewLoading: false,
  confirmLoading: false,
  formError: null,
});

type PlatformSpoilerProps = {
  config: PlatformConfig;
  linked: PlatformLink | undefined;
  parkrunLinked: boolean;
  stats?: { runs: number; volunteering: number };
  form: PlatformFormState;
  unlinkLoading: boolean;
  isSyncing: boolean;
  syncLoading: boolean;
  onProfileUrlChange: (value: string) => void;
  onPreview: () => void;
  onConfirm: (linkParkrun?: boolean) => void;
  onUnlink: () => void;
  onSyncRequest: () => void;
};

function PlatformSpoiler({
  config,
  linked,
  parkrunLinked,
  stats,
  form,
  unlinkLoading,
  isSyncing,
  syncLoading,
  onProfileUrlChange,
  onPreview,
  onConfirm,
  onUnlink,
  onSyncRequest,
}: PlatformSpoilerProps) {
  const skipParkrunLookup = config.code === "s95" && parkrunLinked;
  const linkedDetailsLabel = linked
    ? profilePlatformSummaryLabel(linked.display_name ?? linked.external_user_id, stats)
    : null;

  const syncTimeLabel = isSyncing ? "Обновление…" : linked?.last_user_sync_at
    ? formatDateTime(linked.last_user_sync_at)
    : "Не обновлялось";

  return (
    <details
      className={`profile-platform-spoiler ${linked ? "profile-platform-linked" : "profile-platform-unlinked"}`}
    >
      <summary className="profile-platform-summary">
        <span className="profile-platform-summary-text">
          <span className="profile-platform-summary-platform">{platformCodeLabel(config.code)}</span>
          {linked ? (
            <>
              <span className="profile-platform-summary-sep" aria-hidden>
                ·
              </span>
              <span className="profile-platform-summary-details">{linkedDetailsLabel}</span>
            </>
          ) : (
            <>
              <span className="profile-platform-summary-sep" aria-hidden>
                ·
              </span>
              <span className="profile-platform-summary-unlinked">профиль не привязан</span>
            </>
          )}
        </span>
        {linked && (
          <span className="profile-sync-time-wrap">
            <span className="profile-sync-updated" title="Последнее обновление данных с сайта">
              {syncTimeLabel}
            </span>
            <button
              type="button"
              className={`profile-sync-refresh-btn${isSyncing ? " profile-sync-refresh-btn-spinning" : ""}`}
              aria-label={`Обновить данные ${platformCodeLabel(config.code)}`}
              aria-describedby={`profile-sync-tooltip-${config.code}`}
              disabled={isSyncing || syncLoading}
              onClick={(event) => {
                event.preventDefault();
                onSyncRequest();
              }}
            >
              <svg viewBox="0 0 24 24" width="16" height="16" aria-hidden="true">
                <path
                  fill="currentColor"
                  d="M17.65 6.35A7.958 7.958 0 0 0 12 4c-4.42 0-7.99 3.58-7.99 8s3.57 8 7.99 8c3.73 0 6.84-2.55 7.73-6h-2.08a5.99 5.99 0 0 1-5.65 4c-3.31 0-6-2.69-6-6s2.69-6 6-6c1.66 0 3.14.69 4.22 1.78L13 11h7V4l-2.35 2.35z"
                />
              </svg>
            </button>
            <span
              id={`profile-sync-tooltip-${config.code}`}
              className="profile-sync-tooltip"
              role="tooltip"
            >
              <span className="profile-sync-tooltip-title">Обновить данные с сайта</span>
              <span className="profile-sync-tooltip-hint">
                Нажмите на иконку — запросим свежие результаты (не чаще раза в сутки)
              </span>
            </span>
          </span>
        )}
      </summary>
      <div className="profile-platform-body">
        {linked ? (
          <>
            <p>
              <strong>{linked.display_name ?? linked.external_user_id}</strong>
            </p>
            <p className="muted">Статус: {isSyncing ? syncStatusLabel("syncing") : syncStatusLabel(linked.sync_status)}</p>
            <ProfileDataFreshness
              dataUpdatedAt={linked.data_updated_at}
              dataThroughDate={linked.data_through_date}
            />
            {config.hasPublicProfile !== false && linked.external_url.startsWith("http") && (
              <a className="link" href={linked.external_url} target="_blank" rel="noreferrer">
                {config.openLabel}
              </a>
            )}
            {config.hasPublicProfile === false && (
              <p className="muted">
                Штрихкод: <span className="profile-participant-id">{linked.external_user_id}</span>
              </p>
            )}
            <div className="actions-row profile-linked-actions">
              <button
                type="button"
                className="btn btn-ghost btn-danger"
                disabled={unlinkLoading || isSyncing}
                onClick={() => onUnlink()}
              >
                {unlinkLoading ? "Отвязка…" : "Отвязать профиль"}
              </button>
              {isSyncing && (
                <span className="muted">Дождитесь окончания синхронизации</span>
              )}
            </div>
          </>
        ) : (
          <>
            <p className="muted">{config.hint}</p>
            <label className="field">
              <span className="field-label">Ссылка на профиль</span>
              <input
                className={`input${form.formError ? " input-invalid" : ""}`}
                type={config.inputMode === "text" ? "text" : "url"}
                inputMode={config.inputMode === "text" ? "text" : "url"}
                value={form.profileUrl}
                onChange={(event) => onProfileUrlChange(event.target.value)}
                placeholder={config.placeholder}
              />
            </label>
            {form.formError && (
              <div className="profile-form-error" role="alert">
                <p>{form.formError}</p>
              </div>
            )}
            <div className="actions-row">
              <button
                type="button"
                className="btn secondary"
                onClick={() => onPreview()}
                disabled={form.previewLoading || form.confirmLoading}
              >
                {form.previewLoading ? "Загрузка…" : "Предпросмотр"}
              </button>
            </div>
            {form.previewLoading && (
              <p className="profile-preview-wait-hint" role="status">
                Проверяем базу сайта и при необходимости загружаем профиль с платформы — это может занять время.
                {config.code === "s95" && !skipParkrunLookup && " При коротком штрихкоде также проверим профиль в parkrun."}
                {" "}
                Пожалуйста, ожидайте и не закрывайте страницу.
              </p>
            )}
            {form.preview && (
              <div className="profile-preview-block">
                <ProfileDataFreshness
                  dataUpdatedAt={form.preview.data_updated_at}
                  dataThroughDate={form.preview.data_through_date}
                  dataSource={form.preview.data_source}
                />
                <p>
                  <strong>{form.preview.display_name}</strong>
                </p>
                {form.preview.barcode_id && <p>Штрихкод: {form.preview.barcode_id}</p>}
                {form.preview.platform_code === "s95" && form.preview.planning_location && (
                  <p>
                    Собирается в: {form.preview.planning_location}
                    {form.preview.planning_location_seen_at ? (
                      <span className="muted">
                        {" "}
                        (с {new Date(form.preview.planning_location_seen_at).toLocaleString("ru-RU")})
                      </span>
                    ) : null}
                  </p>
                )}
                {form.preview.parkrun_eligible && !form.preview.parkrun_match && !skipParkrunLookup && (
                  <p className="muted">
                    Профиль parkrun по этому штрихкоду не найден — можно привязать только С95.
                  </p>
                )}
                {skipParkrunLookup && (
                  <p className="muted">Профиль parkrun уже привязан — привяжем только С95.</p>
                )}
                {form.preview.club_name && <p>Клуб: {form.preview.club_name}</p>}
                {form.preview.platform_code === "parkrun" && form.preview.age_category && (
                  <p>Возрастная группа: {form.preview.age_category}</p>
                )}
                <p>
                  Пробежек: {form.preview.total_runs ?? "—"}, волонтёрств:{" "}
                  {form.preview.total_volunteering ?? "—"}
                </p>
                {form.preview.recent_activities.length > 0 && (
                  <div className="profile-preview-recent">
                    <p className="profile-preview-recent-title">Последние события</p>
                    <ul className="profile-preview-recent-list">
                      {form.preview.recent_activities.map((activity, index) => (
                        <li key={`${activity.kind}-${activity.event_date}-${index}`}>
                          <span className="profile-preview-recent-kind">
                            {activity.kind === "run" ? "Пробежка" : "Волонтёрство"}
                          </span>
                          <span>{formatDate(activity.event_date)}</span>
                          <span>{activity.location_name}</span>
                          {activity.kind === "run" ? (
                            <span>{activity.finish_time_display ?? "—"}</span>
                          ) : (
                            <span>{activity.role ?? "—"}</span>
                          )}
                        </li>
                      ))}
                    </ul>
                  </div>
                )}
                {form.preview.parkrun_match && !skipParkrunLookup && (
                  <div className="profile-parkrun-match-block">
                    <p className="profile-parkrun-match-title">
                      Также найден профиль в parkrun — можно привязать оба сразу
                    </p>
                    <ProfileDataFreshness
                      dataUpdatedAt={form.preview.parkrun_match.data_updated_at}
                      dataThroughDate={form.preview.parkrun_match.data_through_date}
                      dataSource={form.preview.parkrun_match.data_source}
                    />
                    <p>
                      <strong>{form.preview.parkrun_match.display_name}</strong>
                    </p>
                    {form.preview.parkrun_match.age_category && (
                      <p>Возрастная группа: {form.preview.parkrun_match.age_category}</p>
                    )}
                    <p>
                      Пробежек: {form.preview.parkrun_match.total_runs ?? "—"}, волонтёрств:{" "}
                      {form.preview.parkrun_match.total_volunteering ?? "—"}
                    </p>
                  </div>
                )}
                <div className="actions-row">
                  {form.preview.parkrun_match && !skipParkrunLookup ? (
                    <>
                      <button
                        type="button"
                        className="btn secondary"
                        onClick={() => onConfirm(false)}
                        disabled={form.confirmLoading}
                      >
                        {form.confirmLoading ? "Привязка…" : "Только С95"}
                      </button>
                      <button
                        type="button"
                        className="btn primary"
                        onClick={() => onConfirm(true)}
                        disabled={form.confirmLoading}
                      >
                        {form.confirmLoading ? "Привязка…" : "Привязать оба"}
                      </button>
                    </>
                  ) : (
                    <button
                      type="button"
                      className="btn primary"
                      onClick={() => onConfirm()}
                      disabled={form.confirmLoading}
                    >
                      {form.confirmLoading ? "Привязка…" : "Подтвердить привязку"}
                    </button>
                  )}
                </div>
              </div>
            )}
          </>
        )}
      </div>
    </details>
  );
}

type ProfileLinkSectionProps = {
  byPlatform?: Record<string, { runs: number; volunteering: number }>;
  onLinksChange?: () => void;
};

export function ProfileLinkSection({ byPlatform = {}, onLinksChange }: ProfileLinkSectionProps) {
  const [links, setLinks] = useState<PlatformLink[]>([]);
  const [loadingLinks, setLoadingLinks] = useState(true);
  const [forms, setForms] = useState<Record<string, PlatformFormState>>(() =>
    Object.fromEntries(PLATFORMS.map((p) => [p.code, emptyFormState()])),
  );
  const [unlinkingPlatform, setUnlinkingPlatform] = useState<string | null>(null);
  const [unlinkConfirm, setUnlinkConfirm] = useState<{
    platformCode: string;
    platformTitle: string;
  } | null>(null);
  const [syncConfirm, setSyncConfirm] = useState<{
    platformCode: string;
    platformTitle: string;
  } | null>(null);
  const [syncLoadingPlatform, setSyncLoadingPlatform] = useState<string | null>(null);
  const [pendingSyncPlatforms, setPendingSyncPlatforms] = useState<Set<string>>(() => new Set());
  const [snackbar, setSnackbar] = useState<SnackbarState>(closedSnackbar);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);
  const hasLoadedLinksRef = useRef(false);
  const didScrollToProfilesRef = useRef(false);
  const previewAbortControllersRef = useRef<Partial<Record<string, AbortController>>>({});

  useEffect(() => {
    return () => {
      for (const controller of Object.values(previewAbortControllersRef.current)) {
        controller?.abort();
      }
    };
  }, []);

  const showSnackbar = (state: Omit<SnackbarState, "open">) => {
    setSnackbar({ ...state, open: true });
  };

  const syncingPlatformCodes = useMemo(() => {
    const codes = new Set(pendingSyncPlatforms);
    if (syncLoadingPlatform) {
      codes.add(syncLoadingPlatform);
    }
    for (const link of links) {
      if (link.sync_status === "syncing") {
        codes.add(link.platform_code);
      }
    }
    return codes;
  }, [links, pendingSyncPlatforms, syncLoadingPlatform]);

  const syncInProgress = syncingPlatformCodes.size > 0;

  const updateForm = (code: string, patch: Partial<PlatformFormState>) => {
    setForms((prev) => ({
      ...prev,
      [code]: { ...prev[code], ...patch },
    }));
  };

  const resetForm = (code: string) => {
    updateForm(code, emptyFormState());
  };

  const loadLinks = useCallback(async (options?: { background?: boolean }) => {
    const background = options?.background ?? hasLoadedLinksRef.current;
    if (!background) {
      setLoadingLinks(true);
    }
    try {
      const data = await listProfileLinks();
      setLinks(data);
      setPendingSyncPlatforms((prev) => {
        const next = new Set(prev);
        for (const link of data) {
          if (link.sync_status !== "syncing") {
            next.delete(link.platform_code);
          }
        }
        return next;
      });
      hasLoadedLinksRef.current = true;
    } catch (err) {
      setError(err instanceof Error ? err.message : "Не удалось загрузить профили");
    } finally {
      setLoadingLinks(false);
    }
  }, []);

  useEffect(() => {
    void loadLinks();
  }, [loadLinks]);

  useEffect(() => {
    if (window.location.hash !== "#profiles" || didScrollToProfilesRef.current || loadingLinks) {
      return;
    }
    const element = document.getElementById("profiles");
    element?.scrollIntoView({ behavior: "smooth", block: "start" });
    didScrollToProfilesRef.current = true;
  }, [loadingLinks]);

  useEffect(() => {
    if (!syncInProgress) {
      return;
    }
    const timer = window.setInterval(() => {
      void loadLinks({ background: true });
      onLinksChange?.();
    }, 4000);
    return () => window.clearInterval(timer);
  }, [syncInProgress, loadLinks, onLinksChange]);

  const requestUnlink = (platformCode: string) => {
    setUnlinkConfirm({
      platformCode,
      platformTitle: platformCodeLabel(platformCode),
    });
  };

  const requestSync = (platformCode: string) => {
    setSyncConfirm({
      platformCode,
      platformTitle: platformCodeLabel(platformCode),
    });
  };

  const performUnlink = async () => {
    if (!unlinkConfirm) {
      return;
    }
    const { platformCode, platformTitle } = unlinkConfirm;
    setUnlinkingPlatform(platformCode);
    setError(null);
    setSuccess(null);
    try {
      const result = await unlinkProfile(platformCode);
      const extra =
        result.cancelled_sync_jobs > 0
          ? ` Отменено фоновых задач: ${result.cancelled_sync_jobs}.`
          : "";
      setSuccess(`Профиль ${platformTitle} отвязан.${extra}`);
      setUnlinkConfirm(null);
      resetForm(platformCode);
      await loadLinks();
      onLinksChange?.();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Не удалось отвязать профиль");
    } finally {
      setUnlinkingPlatform(null);
    }
  };

  const handlePreview = async (config: PlatformConfig) => {
    const form = forms[config.code];
    const url = form.profileUrl.trim();
    if (!url) {
      updateForm(config.code, { formError: config.emptyInputError });
      return;
    }
    previewAbortControllersRef.current[config.code]?.abort();
    const controller = new AbortController();
    previewAbortControllersRef.current[config.code] = controller;
    updateForm(config.code, {
      previewLoading: true,
      formError: null,
      preview: null,
    });
    setError(null);
    setSuccess(null);
    try {
      const preview = await config.preview(url, controller.signal);
      if (controller.signal.aborted) {
        return;
      }
      updateForm(config.code, { preview });
    } catch (err) {
      if (err instanceof DOMException && err.name === "AbortError") {
        return;
      }
      updateForm(config.code, {
        formError: err instanceof Error ? err.message : "Не удалось проверить профиль",
      });
    } finally {
      if (previewAbortControllersRef.current[config.code] === controller) {
        delete previewAbortControllersRef.current[config.code];
      }
      if (!controller.signal.aborted) {
        updateForm(config.code, { previewLoading: false });
      } else {
        updateForm(config.code, { previewLoading: false, preview: null });
      }
    }
  };

  const handleConfirm = async (config: PlatformConfig, linkParkrun = false) => {
    const form = forms[config.code];
    const url = form.profileUrl.trim();
    if (!url) {
      updateForm(config.code, {
        formError: "Сначала укажите ссылку или код и нажмите «Предпросмотр»",
      });
      return;
    }
    if (!form.preview) {
      updateForm(config.code, {
        formError: "Сначала нажмите «Предпросмотр» и проверьте данные профиля",
      });
      return;
    }
    if (linkParkrun && !form.preview.parkrun_match) {
      updateForm(config.code, {
        formError: "Профиль parkrun не найден — можно привязать только С95",
      });
      return;
    }
    updateForm(config.code, { confirmLoading: true, formError: null });
    setError(null);
    setSuccess(null);
    try {
      const result = await config.confirm(url, { linkParkrun });
      if (linkParkrun && config.confirmSuccessBoth) {
        const payload = result as { message?: string; parkrun_link?: PlatformLink | null };
        if (payload.message === "linked_s95_parkrun_skipped") {
          setSuccess(
            "Профиль С95 привязан. Профиль parkrun уже был привязан или недоступен для привязки.",
          );
        } else {
          setSuccess(config.confirmSuccessBoth);
        }
      } else {
        setSuccess(config.confirmSuccess);
      }
      resetForm(config.code);
      await loadLinks();
      onLinksChange?.();
    } catch (err) {
      updateForm(config.code, {
        formError: err instanceof Error ? err.message : "Не удалось привязать профиль",
      });
    } finally {
      updateForm(config.code, { confirmLoading: false });
    }
  };

  const handleSyncRefresh = async () => {
    if (!syncConfirm) {
      return;
    }
    const { platformCode } = syncConfirm;
    setPendingSyncPlatforms((prev) => new Set(prev).add(platformCode));
    setSyncLoadingPlatform(platformCode);
    try {
      const response = await triggerSyncRefreshPlatform(platformCode);
      setSyncConfirm(null);
      setLinks((prev) =>
        prev.map((link) =>
          link.platform_code === platformCode ? { ...link, sync_status: "syncing" } : link,
        ),
      );
      showSnackbar({
        variant: "default",
        title: "Запрос принят",
        message: response.message,
      });
      await loadLinks({ background: true });
      onLinksChange?.();
    } catch (err) {
      setPendingSyncPlatforms((prev) => {
        const next = new Set(prev);
        next.delete(platformCode);
        return next;
      });
      if (err instanceof ApiError && err.status === 429) {
        showSnackbar({
          variant: "error",
          title: "Слишком частые запросы",
          message: "Подождите 30 минут и попробуйте снова.",
        });
      } else {
        showSnackbar({
          variant: "error",
          title: "Не удалось запустить обновление",
          message: err instanceof Error ? err.message : "Попробуйте позже.",
        });
      }
      setSyncConfirm(null);
    } finally {
      setSyncLoadingPlatform(null);
    }
  };

  return (
    <section id="profiles" className="card profile-link-section">
      <details className="profile-spoilers-root" open>
        <summary className="profile-spoilers-root-summary">Профили</summary>
        <div className="profile-spoilers-inner">
          {loadingLinks && links.length === 0 && <p className="muted">Загрузка…</p>}

          {(!loadingLinks || links.length > 0) &&
            PLATFORMS.map((config) => {
              const linked = links.find((link) => link.platform_code === config.code);
              const parkrunLinked = links.some((link) => link.platform_code === "parkrun");
              const form = forms[config.code];
              const stats = byPlatform[config.code];
              return (
                <PlatformSpoiler
                  key={config.code}
                  config={config}
                  linked={linked}
                  parkrunLinked={parkrunLinked}
                  stats={stats}
                  form={form}
                  unlinkLoading={unlinkingPlatform === config.code}
                  isSyncing={syncingPlatformCodes.has(config.code)}
                  syncLoading={syncLoadingPlatform === config.code}
                  onProfileUrlChange={(value) =>
                    updateForm(config.code, { profileUrl: value, formError: null })
                  }
                  onPreview={() => void handlePreview(config)}
                  onConfirm={(linkParkrun) => void handleConfirm(config, linkParkrun)}
                  onUnlink={() => requestUnlink(config.code)}
                  onSyncRequest={() => requestSync(config.code)}
                />
              );
            })}
        </div>
      </details>

      <ConfirmModal
        open={unlinkConfirm !== null}
        title={
          unlinkConfirm ? `Отвязать ${unlinkConfirm.platformTitle}?` : "Отвязать профиль?"
        }
        variant="danger"
        confirmLabel="Отвязать"
        cancelLabel="Оставить"
        confirmLoading={unlinkingPlatform !== null}
        onCancel={() => {
          if (unlinkingPlatform === null) {
            setUnlinkConfirm(null);
          }
        }}
        onConfirm={() => void performUnlink()}
      >
        {unlinkConfirm && (
          <>
            <p>
              Пробежки и волонтёрства{" "}
              <span className="modal-body-highlight">{unlinkConfirm.platformTitle}</span> исчезнут
              из личного кабинета.
            </p>
            <p className="muted">
              В общей базе данные сохранятся — при повторной привязке они подтянутся снова.
            </p>
          </>
        )}
      </ConfirmModal>

      <ConfirmModal
        open={syncConfirm !== null}
        title="Точно ли вы хотите сделать запрос для обновления данных на сайт?"
        confirmLabel="Обновить данные"
        cancelLabel="Отмена"
        confirmLoading={syncLoadingPlatform !== null}
        onCancel={() => {
          if (syncLoadingPlatform === null) {
            setSyncConfirm(null);
          }
        }}
        onConfirm={() => void handleSyncRefresh()}
      >
        {syncConfirm && (
          <>
            <p>
              Будет запрошено обновление профиля{" "}
              <span className="modal-body-highlight">{syncConfirm.platformTitle}</span>.
            </p>
            <p className="muted">
              Пожалуйста, не делайте лишних запросов: обновление каждой системы доступно не чаще
              одного раза в 30 минут.
            </p>
          </>
        )}
      </ConfirmModal>

      <Snackbar
        open={snackbar.open}
        title={snackbar.title}
        variant={snackbar.variant}
        onDismiss={() => setSnackbar(closedSnackbar())}
      >
        {snackbar.message}
      </Snackbar>

      {error && (
        <div className="profile-section-message profile-section-message-error" role="alert">
          <p>{error}</p>
        </div>
      )}

      {success && (
        <div className="profile-section-message profile-section-message-success">
          <p>{success}</p>
        </div>
      )}
    </section>
  );
}
