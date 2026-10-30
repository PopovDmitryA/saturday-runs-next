import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { ConfirmModal } from "./ConfirmModal";
import { ParticipantNameSearch } from "./ParticipantNameSearch";
import { PlatformBadge } from "./PlatformBadge";
import { QrCodeModal } from "./QrCodeModal";
import { Snackbar } from "./Snackbar";
import { SignupCta } from "./SystemSignupLink";
import {
  ApiError,
  listProfileLinks,
  triggerSyncRefreshPlatform,
  unlinkProfile,
  type PlatformLink,
} from "../lib/api";
import {
  COUNT_FORMS,
  formatDate,
  formatDateTime,
  platformCodeLabel,
  platformScanCode,
  pluralizeRu,
} from "../lib/format";
import { platformProfileUrl } from "../lib/platformProfileUrl";
import { useProfileLinksChanged } from "../lib/profileLinksEvents";
import { SIGNUP_LINK_HOWTO, isSignupPlatform } from "../lib/systemSignup";
import "../features/portal/systemRegistration.css";

type ParticipantIdConfig = {
  label: string;
  field: "barcode_id" | "external_user_id";
  show?: (value: string) => boolean;
};

type PlatformConfig = {
  code: string;
  participantId?: ParticipantIdConfig;
};

// Привязка живёт в едином поисковом поле сверху (имя / код / ссылка) — у
// карточек систем осталась только витрина привязанного профиля (решение
// Дмитрия 25.08.2026: два способа привязки рядом сбивали с толку).
const PLATFORMS: PlatformConfig[] = [
  {
    code: "five_verst",
    participantId: {
      label: "Код участника",
      field: "external_user_id",
      show: (v) => /^\d+$/.test(v),
    },
  },
  { code: "s95", participantId: { label: "Штрихкод", field: "barcode_id" } },
  { code: "parkrun", participantId: { label: "Штрихкод", field: "barcode_id" } },
  { code: "runpark", participantId: { label: "Штрихкод", field: "barcode_id" } },
];

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

// Одна фраза у даты и у кнопки: профиль parkrun иногда перечитывает очередь,
// и дата двигается сама, — «автообновления нет» спорило бы с ней.
const PARKRUN_MANUAL_HINT = "Новые пробежки parkrun сами не приходят — после своей пробежки нажмите «Обновить».";

// Подсказка к дате у кнопки «Обновить»: когда обновились данные, какой старт
// на сайте последний и нужна ли вообще кнопка. Голая дата читается как «с тех
// пор ничего не приходило» — и «Обновить» жмут впустую. Строки идут в title:
// на компьютере его показывает браузер, на телефоне — общий слой
// TapTooltipLayer по тапу, и он же делит текст по переносам. StatHintTooltip
// здесь не годится: на тач-экране он закрывается тем же тапом, что открыл.
function dataFreshnessHint(
  platformCode: string,
  updatedAt: string | null | undefined,
  throughDate: string | null | undefined,
): string[] {
  const label = platformCodeLabel(platformCode);
  return [
    updatedAt
      ? `Данные ${label} на сайте обновлены ${formatDateTime(updatedAt)}.`
      : `Данные ${label} на сайт ещё не загружались.`,
    throughDate ? `Ваш последний старт на сайте — ${formatDate(throughDate)}.` : "Ваших стартов на сайте пока нет.",
    platformCode === "parkrun"
      ? PARKRUN_MANUAL_HINT
      : "Новые результаты приходят сами из протоколов. Если чего-то не хватает — нажмите «Обновить».",
  ];
}

type PlatformCardProps = {
  config: PlatformConfig;
  linked: PlatformLink | undefined;
  stats?: { runs: number; volunteering: number };
  unlinkLoading: boolean;
  isSyncing: boolean;
  syncLoading: boolean;
  onUnlink: () => void;
  onSyncRequest: () => void;
  onShowQr: () => void;
};

function PlatformCard({
  config,
  linked,
  stats,
  unlinkLoading,
  isSyncing,
  syncLoading,
  onUnlink,
  onSyncRequest,
  onShowQr,
}: PlatformCardProps) {
  // Код берётся из привязки: штрихкод системы, а для 5 вёрст — «A» + номер
  // участника. Кнопки нет, пока привязки нет или код из неё не собирается.
  const scanCode = linked
    ? platformScanCode(config.code, linked.barcode_id, linked.external_user_id)
    : null;

  // Когда данные системы на сайте обновлялись любым путём — протокол по
  // расписанию или кнопка (считает сервер). Время одного синка по кнопке
  // месяцами стояло на месте (карточка бэклога 06.10.2026). К
  // last_user_sync_at не откатываемся: сервер намеренно не считает
  // обновлением отметку самой привязки.
  const dataUpdatedAt = linked?.data_updated_at ?? null;
  const syncTimeLabel = isSyncing ? "Обновление…" : dataUpdatedAt
    ? formatDateTime(dataUpdatedAt)
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
              {pluralizeRu(stats.runs, COUNT_FORMS.runs)} · {pluralizeRu(stats.volunteering, COUNT_FORMS.volunteering)}
            </p>
          )}
          <div className="profile-platform-card-meta">
            {config.participantId && (() => {
              const id = linked[config.participantId.field];
              if (!id) return null;
              if (config.participantId.show && !config.participantId.show(id)) return null;
              return (
                <span className="profile-platform-card-id muted">
                  {config.participantId.label}: <span className="profile-participant-id">{id}</span>
                </span>
              );
            })()}
            <span
              className="profile-sync-updated"
              title={dataFreshnessHint(config.code, dataUpdatedAt, linked.data_through_date).join("\n")}
            >
              {syncTimeLabel}
            </span>
            <button
              type="button"
              className={`profile-sync-refresh-btn${isSyncing ? " profile-sync-refresh-btn-spinning" : ""}`}
              aria-label={`Обновить данные ${platformCodeLabel(config.code)}`}
              title={
                config.code === "parkrun"
                  ? `Запросить свежие результаты (не чаще раза в 30 минут). ${PARKRUN_MANUAL_HINT}`
                  : "Запросить свежие результаты (не чаще раза в 30 минут)"
              }
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
          <div className="profile-platform-card-actions">
            {scanCode && (
              <button
                type="button"
                className="btn secondary btn-sm qr-btn"
                title="QR-код участника"
                aria-label="QR-код участника"
                onClick={() => onShowQr()}
              >
                <svg viewBox="0 0 24 24" width="14" height="14" aria-hidden="true">
                  <path
                    fill="currentColor"
                    d="M3 3h8v8H3V3zm2 2v4h4V5H5zm8-2h8v8h-8V3zm2 2v4h4V5h-4zM3 13h8v8H3v-8zm2 2v4h4v-4H5zm10-2h2v2h-2v-2zm4 0h2v2h-2v-2zm-4 4h2v2h-2v-2zm4 0h2v2h-2v-2zm-2 4h2v2h-2v-2zm-4 0h2v2h-2v-2z"
                  />
                </svg>
              </button>
            )}
            {externalUrl && (
              <a className="btn secondary btn-sm" href={externalUrl} target="_blank" rel="noreferrer">
                Профиль ↗
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
      ) : (
        <>
          <p className="profile-platform-card-cta-hint muted">
            Найдите себя в поиске выше — по имени, коду участника или ссылке на профиль{" "}
            {platformCodeLabel(config.code)}.
          </p>
          {isSignupPlatform(config.code) && (
            <p className="signup-hint">
              Профиля нет? <SignupCta code={config.code} place="cabinet" /> — потом{" "}
              {SIGNUP_LINK_HOWTO[config.code]}.
            </p>
          )}
        </>
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
  const [links, setLinks] = useState<PlatformLink[]>([]);
  const [loadingLinks, setLoadingLinks] = useState(true);
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

  // Профиль привязали мимо этой секции — из окна «Это вы?» (поиск по сайту
  // или окно после входа): перечитываем список и сводку кабинета.
  useProfileLinksChanged(() => {
    void loadLinks({ background: true });
    onLinksChange?.();
  });

  useEffect(() => {
    if (window.location.hash !== "#profiles" || didScrollToProfilesRef.current || loadingLinks) {
      return;
    }
    const element = document.getElementById("profiles");
    element?.scrollIntoView({ behavior: "smooth", block: "start" });
    didScrollToProfilesRef.current = true;
  }, [loadingLinks]);

  // #scancode=five_verst — кнопка «Мой штрихкод» под напоминанием о плане в
  // VK и в письме (в Telegram код присылает бот): сразу окно с QR.
  useEffect(() => {
    const match = /^#scancode=([a-z0-9_]+)$/.exec(window.location.hash);
    if (!match || loadingLinks) {
      return;
    }
    const linked = links.find((link) => link.platform_code === match[1]);
    const code = linked ? platformScanCode(match[1], linked.barcode_id, linked.external_user_id) : null;
    window.history.replaceState(window.history.state, "", `${window.location.pathname}${window.location.search}#profiles`);
    document.getElementById("profiles")?.scrollIntoView({ block: "start" });
    if (linked && code) {
      setQrModal({ platformCode: match[1], code, displayName: linked.display_name ?? null });
    }
  }, [loadingLinks, links]);

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
      await loadLinks();
      onLinksChange?.();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Не удалось отвязать профиль");
    } finally {
      setUnlinkingPlatform(null);
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
        {links.length < PLATFORMS.length && (
          <a className="link profile-link-wizard" href="/welcome">
            Мастер привязки →
          </a>
        )}
      </div>

      {loadingLinks && links.length === 0 && <p className="muted">Загрузка…</p>}

      {(!loadingLinks || links.length > 0) && links.length < PLATFORMS.length && (
        <div className="profile-name-search-block">
          <p className="profile-name-search-title">Найдите себя</p>
          <p className="muted">
            Одно поле на все системы: имя из протокола, код участника (например, A7035519) или
            ссылка на профиль.
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
            const stats = byPlatform[config.code];
            return (
              <PlatformCard
                key={config.code}
                config={config}
                linked={linked}
                stats={stats}
                unlinkLoading={unlinkingPlatform === config.code}
                isSyncing={syncingPlatformCodes.has(config.code)}
                syncLoading={syncLoadingPlatform === config.code}
                onShowQr={() => {
                  const code = linked
                    ? platformScanCode(config.code, linked.barcode_id, linked.external_user_id)
                    : null;
                  if (code) {
                    setQrModal({ platformCode: config.code, code, displayName: linked?.display_name ?? null });
                  }
                }}
                onUnlink={() => requestUnlink(config.code)}
                onSyncRequest={() => requestSync(config.code)}
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
