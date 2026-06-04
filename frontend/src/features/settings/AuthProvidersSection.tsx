import { useCallback, useEffect, useMemo, useState } from "react";
import { ConfirmModal } from "../../components/ConfirmModal";
import { PlatformBadge } from "../../components/PlatformBadge";
import {
  confirmAccountMerge,
  createLoginRequest,
  getAuthIdentities,
  getLoginRequestStatus,
  getMergePreview,
  oauthStartUrl,
  unlinkAuthProvider,
  type AuthIdentity,
  type MergePreview,
} from "../../lib/api";
import { platformCodeLabel } from "../../lib/format";

const PROVIDERS: Array<{ id: AuthIdentity["provider"]; title: string; hint: string }> = [
  { id: "telegram", title: "Telegram", hint: "Подтверждение через бота" },
  { id: "vk", title: "VK", hint: "Вход через VK ID" },
  { id: "yandex", title: "Яндекс", hint: "Вход через Яндекс ID" },
];

type AuthProvidersSectionProps = {
  initialMergeToken?: string | null;
};

export function AuthProvidersSection({ initialMergeToken = null }: AuthProvidersSectionProps) {
  const [identities, setIdentities] = useState<AuthIdentity[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [linkingProvider, setLinkingProvider] = useState<AuthIdentity["provider"] | null>(null);
  const [telegramToken, setTelegramToken] = useState<string | null>(null);
  const [mergePreview, setMergePreview] = useState<MergePreview | null>(null);
  const [mergeLoading, setMergeLoading] = useState(false);
  const [mergeConfirmLoading, setMergeConfirmLoading] = useState(false);

  const linkedProviders = useMemo(
    () => new Set(identities.map((item) => item.provider)),
    [identities],
  );

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      setIdentities(await getAuthIdentities());
    } catch (err) {
      setError(err instanceof Error ? err.message : "Не удалось загрузить способы входа");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  useEffect(() => {
    if (!initialMergeToken) {
      return;
    }
    setMergeLoading(true);
    void getMergePreview(initialMergeToken)
      .then(setMergePreview)
      .catch((err) => setError(err instanceof Error ? err.message : "Не удалось загрузить объединение"))
      .finally(() => setMergeLoading(false));
  }, [initialMergeToken]);

  useEffect(() => {
    if (!telegramToken) {
      return;
    }
    const interval = window.setInterval(async () => {
      try {
        const status = await getLoginRequestStatus(telegramToken);
        if (status.status === "linked") {
          window.clearInterval(interval);
          setTelegramToken(null);
          setLinkingProvider(null);
          void load();
        } else if (status.status === "merge_required" && status.merge_token) {
          window.clearInterval(interval);
          setTelegramToken(null);
          setLinkingProvider(null);
          const preview = await getMergePreview(status.merge_token);
          setMergePreview(preview);
        } else if (status.status === "expired") {
          window.clearInterval(interval);
          setTelegramToken(null);
          setLinkingProvider(null);
          setError("Время ожидания Telegram истекло");
        }
      } catch {
        // ignore polling errors
      }
    }, 2000);
    return () => window.clearInterval(interval);
  }, [telegramToken, load]);

  const handleLink = async (provider: AuthIdentity["provider"]) => {
    setError(null);
    setLinkingProvider(provider);
    if (provider === "telegram") {
      try {
        const data = await createLoginRequest(true);
        setTelegramToken(data.request_token);
        window.open(data.bot_url, "_blank", "noopener,noreferrer");
      } catch (err) {
        setLinkingProvider(null);
        setError(err instanceof Error ? err.message : "Не удалось начать привязку Telegram");
      }
      return;
    }
    window.location.href = oauthStartUrl(provider, "link");
  };

  const handleUnlink = async (provider: AuthIdentity["provider"]) => {
    setError(null);
    try {
      await unlinkAuthProvider(provider);
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Не удалось отвязать способ входа");
    }
  };

  const handleConfirmMerge = async () => {
    if (!mergePreview) {
      return;
    }
    setMergeConfirmLoading(true);
    setError(null);
    try {
      await confirmAccountMerge(mergePreview.merge_token);
      setMergePreview(null);
      const url = new URL(window.location.href);
      url.searchParams.delete("merge_token");
      window.history.replaceState({}, "", url.toString());
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Не удалось объединить профили");
    } finally {
      setMergeConfirmLoading(false);
    }
  };

  return (
    <section className="card">
      <h2 className="section-title">Способы входа</h2>
      <p className="muted settings-lead">
        Можно войти через Telegram, VK или Яндекс и привязать несколько способов к одному профилю.
        При объединении аккаунтов привязки 5 вёрст / S95 / parkrun у поглощаемого профиля будут сброшены.
      </p>

      {loading && <p className="muted">Загрузка…</p>}
      {error && <p className="error-text">{error}</p>}

      {!loading && (
        <ul className="settings-platform-list auth-provider-list">
          {PROVIDERS.map((provider) => {
            const linked = linkedProviders.has(provider.id);
            const identity = identities.find((item) => item.provider === provider.id);
            return (
              <li key={provider.id} className="settings-platform-row auth-provider-row">
                <div className="settings-platform-info">
                  <span className="settings-platform-name">{provider.title}</span>
                  <span className="muted settings-platform-hint">
                    {linked
                      ? identity?.display_name || identity?.email || `ID ${identity?.external_id}`
                      : provider.hint}
                  </span>
                  <span className={`settings-toggle-label ${linked ? "on" : "off"}`}>
                    {linked ? "Привязан" : "Не привязан"}
                  </span>
                </div>
                <div className="auth-provider-actions">
                  {linked ? (
                    identities.length > 1 && (
                      <button
                        type="button"
                        className="btn secondary btn-sm"
                        onClick={() => void handleUnlink(provider.id)}
                      >
                        Отвязать
                      </button>
                    )
                  ) : (
                    <button
                      type="button"
                      className="btn secondary btn-sm"
                      disabled={linkingProvider === provider.id}
                      onClick={() => void handleLink(provider.id)}
                    >
                      {linkingProvider === provider.id ? "Ожидание…" : "Привязать"}
                    </button>
                  )}
                </div>
              </li>
            );
          })}
        </ul>
      )}

      {linkingProvider === "telegram" && telegramToken && (
        <p className="muted auth-provider-wait">Подтвердите привязку в Telegram-боте…</p>
      )}

      <ConfirmModal
        open={mergePreview != null}
        title="Объединить профили?"
        confirmLabel="Объединить"
        variant="danger"
        confirmLoading={mergeConfirmLoading}
        onCancel={() => setMergePreview(null)}
        onConfirm={() => void handleConfirmMerge()}
      >
        {mergeLoading && <p className="muted">Загрузка…</p>}
        {mergePreview && (
          <>
            <p>{mergePreview.warning}</p>
            {mergePreview.platform_links_to_reset.length > 0 ? (
              <ul className="auth-merge-links-list">
                {mergePreview.platform_links_to_reset.map((item) => (
                  <li key={`${item.platform_code}-${item.external_user_id}`}>
                    <PlatformBadge code={item.platform_code} />{" "}
                    {platformCodeLabel(item.platform_code)} — {item.external_user_id}
                  </li>
                ))}
              </ul>
            ) : (
              <p className="muted">У поглощаемого профиля нет привязанных учёток платформ.</p>
            )}
          </>
        )}
      </ConfirmModal>
    </section>
  );
}
