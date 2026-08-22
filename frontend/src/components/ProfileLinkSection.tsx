import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { ConfirmModal } from "./ConfirmModal";
import { ParticipantNameSearch } from "./ParticipantNameSearch";
import { PlatformBadge } from "./PlatformBadge";
import { QrCodeModal } from "./QrCodeModal";
import { Snackbar } from "./Snackbar";
import { useOptionalUser } from "../lib/useOptionalUser";
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
  platformScanCode,
  profileDataFreshnessLines,
} from "../lib/format";
import { platformProfileUrl } from "../lib/platformProfileUrl";

type ParticipantIdConfig = {
  label: string;
  field: "barcode_id" | "external_user_id";
  show?: (value: string) => boolean;
};

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
  participantId?: ParticipantIdConfig;
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
    participantId: {
      label: "Код участника",
      field: "external_user_id",
      show: (v) => /^\d+$/.test(v),
    },
  },
  {
    code: "s95",
    hint: "Ссылка на профиль https://s95.ru/athletes/5207/ или только ID участника из неё — 5207.",
    placeholder: "https://s95.ru/athletes/… или 5207",
    openLabel: "Открыть на s95.ru",
    inputMode: "text",
    preview: previewS95Profile,
    confirm: (url, options) => confirmS95Profile(url, options?.linkParkrun ?? false),
    emptyInputError: "Введите ссылку на профиль С95 или ID участника",
    confirmSuccess:
      "Профиль С95 привязан. Синхронизация поставлена в очередь (может занять несколько минут).",
    confirmSuccessBoth:
      "Профили С95 и parkrun привязаны. Синхронизация поставлена в очередь (может занять несколько минут).",
    participantId: { label: "Штрихкод", field: "barcode_id" },
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
    participantId: { label: "Штрихкод", field: "barcode_id" },
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
    participantId: { label: "Штрихкод", field: "barcode_id" },
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
}: {
  dataUpdatedAt?: string | null;
  dataThroughDate?: string | null;
  dataSource?: string | null;
}) {
  const { updatedLine } = profileDataFreshnessLines(dataUpdatedAt, dataThroughDate);
  if (!updatedLine) {
    return null;
  }
  return (
    <div className="profile-data-freshness" role="note">
      <p className="muted">{updatedLine}</p>
    </div>
  );
}

/** «00:29:07» → «29:07»: часы у пятикилометровых финишей всегда нулевые. */
function shortFinishTime(display: string | null | undefined): string {
  if (!display) {
    return "—";
  }
  return display.replace(/^00:/, "");
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
  onShowQr: () => void;
  canShowQr: boolean;
  previewBlockRef?: (el: HTMLDivElement | null) => void;
};

function PlatformCard({
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
  onShowQr,
  canShowQr,
  previewBlockRef,
}: PlatformSpoilerProps) {
  // Форма привязки раскрывается по кнопке внутри карточки (вместо спойлера).
  const [expanded, setExpanded] = useState(false);
  const skipParkrunLookup = config.code === "s95" && parkrunLinked;
  // Фича на обкатке — до проверки на реальных стартах видна только админу
  // (см. feedback пользователя). Штрихкод как текст уже виден всем ниже.
  const scanCode = linked && canShowQr
    ? platformScanCode(config.code, linked.barcode_id, linked.external_user_id)
    : null;

  const syncTimeLabel = isSyncing ? "Обновление…" : linked?.last_user_sync_at
    ? formatDateTime(linked.last_user_sync_at)
    : "не обновлялось";

  const externalUrl = linked ? platformProfileUrl(linked) : null;

  return (
    <div className={`profile-platform-card ${linked ? "profile-platform-card-linked" : "profile-platform-card-unlinked"}`}>
      <div className="profile-platform-card-head">
        <PlatformBadge code={config.code} />
        {linked ? (
          <span className="profile-platform-card-status profile-platform-card-status-linked">
            ✓ привязан
          </span>
        ) : (
          <span className="profile-platform-card-status">не привязан</span>
        )}
      </div>

      {linked ? (
        <>
          <p className="profile-platform-card-name">{linked.display_name ?? linked.external_user_id}</p>
          {stats && (
            <p className="profile-platform-card-stats muted">
              {stats.runs} пробежек · {stats.volunteering} волонтёрств
            </p>
          )}
          {config.participantId && (() => {
            const id = linked[config.participantId.field];
            if (!id) return null;
            if (config.participantId.show && !config.participantId.show(id)) return null;
            return (
              <p className="profile-platform-card-id muted">
                {config.participantId.label}: <span className="profile-participant-id">{id}</span>
              </p>
            );
          })()}
          <div className="profile-platform-card-sync">
            <span className="profile-sync-updated" title="Последнее обновление данных с сайта">
              {syncTimeLabel}
            </span>
            <button
              type="button"
              className={`profile-sync-refresh-btn${isSyncing ? " profile-sync-refresh-btn-spinning" : ""}`}
              aria-label={`Обновить данные ${platformCodeLabel(config.code)}`}
              title="Запросить свежие результаты (не чаще раза в 30 минут)"
              disabled={isSyncing || syncLoading}
              onClick={() => onSyncRequest()}
            >
              <svg viewBox="0 0 24 24" width="16" height="16" aria-hidden="true">
                <path
                  fill="currentColor"
                  d="M17.65 6.35A7.958 7.958 0 0 0 12 4c-4.42 0-7.99 3.58-7.99 8s3.57 8 7.99 8c3.73 0 6.84-2.55 7.73-6h-2.08a5.99 5.99 0 0 1-5.65 4c-3.31 0-6-2.69-6-6s2.69-6 6-6c1.66 0 3.14.69 4.22 1.78L13 11h7V4l-2.35 2.35z"
                />
              </svg>
            </button>
          </div>
          {config.code === "parkrun" && (
            <p className="profile-platform-card-manual-note">
              Автообновление по системе parkrun невозможно, если вы побегали — нажмите кнопку
              обновления.
            </p>
          )}
          <div className="profile-platform-card-actions">
            {scanCode && (
              <button type="button" className="btn secondary btn-sm qr-btn" onClick={() => onShowQr()}>
                <svg viewBox="0 0 24 24" width="14" height="14" aria-hidden="true">
                  <path
                    fill="currentColor"
                    d="M3 3h8v8H3V3zm2 2v4h4V5H5zm8-2h8v8h-8V3zm2 2v4h4V5h-4zM3 13h8v8H3v-8zm2 2v4h4v-4H5zm10-2h2v2h-2v-2zm4 0h2v2h-2v-2zm-4 4h2v2h-2v-2zm4 0h2v2h-2v-2zm-2 4h2v2h-2v-2zm-4 0h2v2h-2v-2z"
                  />
                </svg>
                QR-код
              </button>
            )}
            {externalUrl && (
              <a className="btn secondary btn-sm" href={externalUrl} target="_blank" rel="noreferrer">
                Открыть профиль ↗
              </a>
            )}
            <button
              type="button"
              className="btn btn-ghost btn-danger btn-sm"
              disabled={unlinkLoading || isSyncing}
              onClick={() => onUnlink()}
            >
              {unlinkLoading ? "Отвязка…" : "Отвязать"}
            </button>
          </div>
          {isSyncing && <p className="muted profile-platform-card-syncing">Дождитесь окончания синхронизации</p>}
        </>
      ) : !expanded ? (
        <>
          <p className="profile-platform-card-cta-hint muted">
            Привяжите профиль — пробежки и волонтёрства {platformCodeLabel(config.code)} появятся в
            кабинете.
          </p>
          <div className="profile-platform-card-actions">
            <button type="button" className="btn primary btn-sm" onClick={() => setExpanded(true)}>
              Привязать
            </button>
          </div>
        </>
      ) : (
        <div className="profile-platform-card-form">
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
              className="btn secondary btn-sm"
              onClick={() => onPreview()}
              disabled={form.previewLoading || form.confirmLoading}
            >
              {form.previewLoading ? "Загрузка…" : "Предпросмотр"}
            </button>
            <button
              type="button"
              className="btn btn-ghost btn-sm"
              onClick={() => setExpanded(false)}
              disabled={form.previewLoading || form.confirmLoading}
            >
              Свернуть
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
            <div className="profile-preview-block" ref={previewBlockRef}>
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
                      <li
                        key={`${activity.kind}-${activity.event_date}-${index}`}
                        className="profile-preview-event"
                      >
                        <span
                          className={`profile-preview-event-dot profile-preview-event-dot-${activity.kind}`}
                          aria-hidden="true"
                        />
                        <span className="visually-hidden">
                          {activity.kind === "run" ? "Пробежка" : "Волонтёрство"}
                        </span>
                        <span className="profile-preview-event-date">
                          {formatDate(activity.event_date)}
                        </span>
                        <span className="profile-preview-event-place" title={activity.location_name}>
                          {activity.location_name}
                        </span>
                        <span className="profile-preview-event-value">
                          {activity.kind === "run"
                            ? shortFinishTime(activity.finish_time_display)
                            : (activity.role ?? "—")}
                        </span>
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
                      className="btn secondary btn-sm"
                      onClick={() => onConfirm(false)}
                      disabled={form.confirmLoading}
                    >
                      {form.confirmLoading ? "Привязка…" : "Только С95"}
                    </button>
                    <button
                      type="button"
                      className="btn primary btn-sm"
                      onClick={() => onConfirm(true)}
                      disabled={form.confirmLoading}
                    >
                      {form.confirmLoading ? "Привязка…" : "Привязать оба"}
                    </button>
                  </>
                ) : (
                  <button
                    type="button"
                    className="btn primary btn-sm"
                    onClick={() => onConfirm()}
                    disabled={form.confirmLoading}
                  >
                    {form.confirmLoading ? "Привязка…" : "Подтвердить привязку"}
                  </button>
                )}
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

type ProfileLinkSectionProps = {
  byPlatform?: Record<string, { runs: number; volunteering: number }>;
  onLinksChange?: () => void;
  onLinksLoaded?: (links: PlatformLink[]) => void;
};

export function ProfileLinkSection({ byPlatform = {}, onLinksChange, onLinksLoaded }: ProfileLinkSectionProps) {
  // QR-коды систем — на обкатке, до проверки на реальных стартах видны
  // только админу (см. platformScanCode/canShowQr в PlatformCard).
  const currentUser = useOptionalUser();
  const canShowQr = currentUser?.is_admin === true;
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
  const [qrModal, setQrModal] = useState<{
    platformCode: string;
    code: string;
    displayName: string | null;
  } | null>(null);
  const [snackbar, setSnackbar] = useState<SnackbarState>(closedSnackbar);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);
  const hasLoadedLinksRef = useRef(false);
  // Колбэк родителя держим в ref, чтобы инлайн-функция в пропсах не
  // пересоздавала loadLinks (и не перезапускала эффекты) на каждом рендере.
  const onLinksLoadedRef = useRef(onLinksLoaded);
  onLinksLoadedRef.current = onLinksLoaded;
  const didScrollToProfilesRef = useRef(false);
  const previewAbortControllersRef = useRef<Partial<Record<string, AbortController>>>({});
  const previewBlockRefs = useRef<Partial<Record<string, HTMLDivElement | null>>>({});

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
      onLinksLoadedRef.current?.(data);
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
      setTimeout(() => {
        previewBlockRefs.current[config.code]?.scrollIntoView({ behavior: "smooth", block: "nearest" });
      }, 50);
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
      <div className="profile-link-head">
        <h2 className="section-title">Профили беговых систем</h2>
        <span className="profile-link-count muted">
          Привязано {links.length} из {PLATFORMS.length}
        </span>
      </div>

      {loadingLinks && links.length === 0 && <p className="muted">Загрузка…</p>}

      {(!loadingLinks || links.length > 0) && links.length < PLATFORMS.length && (
        <div className="profile-name-search-block">
          <p className="profile-name-search-title">Найти себя по имени</p>
          <p className="muted">
            Поиск сразу по всем системам — привязка в один клик, без ссылок и штрихкодов.
          </p>
          <ParticipantNameSearch
            linkedPlatformCodes={new Set(links.map((link) => link.platform_code))}
            onLinked={() => {
              void loadLinks();
              onLinksChange?.();
            }}
          />
        </div>
      )}

      {(!loadingLinks || links.length > 0) && (
        <div className="profile-platform-grid">
          {PLATFORMS.map((config) => {
            const linked = links.find((link) => link.platform_code === config.code);
            const parkrunLinked = links.some((link) => link.platform_code === "parkrun");
            const form = forms[config.code];
            const stats = byPlatform[config.code];
            return (
              <PlatformCard
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
                canShowQr={canShowQr}
                onShowQr={() => {
                  const code = linked && canShowQr
                    ? platformScanCode(config.code, linked.barcode_id, linked.external_user_id)
                    : null;
                  if (code) {
                    setQrModal({ platformCode: config.code, code, displayName: linked?.display_name ?? null });
                  }
                }}
                onPreview={() => void handlePreview(config)}
                onConfirm={(linkParkrun) => void handleConfirm(config, linkParkrun)}
                onUnlink={() => requestUnlink(config.code)}
                onSyncRequest={() => requestSync(config.code)}
                previewBlockRef={(el) => { previewBlockRefs.current[config.code] = el; }}
              />
            );
          })}
        </div>
      )}

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

      <QrCodeModal
        open={qrModal !== null}
        platformCode={qrModal?.platformCode ?? ""}
        displayName={qrModal?.displayName ?? null}
        code={qrModal?.code ?? ""}
        onClose={() => setQrModal(null)}
      />

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
