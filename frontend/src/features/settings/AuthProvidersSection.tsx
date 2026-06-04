import { useCallback, useEffect, useMemo, useState } from "react";
import { ConfirmModal } from "../../components/ConfirmModal";
import { PlatformBadge } from "../../components/PlatformBadge";
import {
  confirmAccountMerge,
  getAuthIdentities,
  getMergePreview,
  oauthStartUrl,
  unlinkAuthProvider,
  type AuthIdentity,
  type MergePreview,
} from "../../lib/api";
import { platformCodeLabel } from "../../lib/format";

const PROVIDERS: Array<{ id: AuthIdentity["provider"]; title: string; hint: string }> = [
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

  const handleLink = (provider: AuthIdentity["provider"]) => {
    setError(null);
    setLinkingProvider(provider);
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
        Можно войти через VK или Яндекс и привязать оба способа к одному профилю. При объединении аккаунтов
        привязки 5 вёрст / S95 / parkrun у поглощаемого профиля будут сброшены.
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
                      onClick={() => handleLink(provider.id)}
                    >
                      {linkingProvider === provider.id ? "Перенаправление…" : "Привязать"}
                    </button>
                  )}
                </div>
              </li>
            );
          })}
        </ul>
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
