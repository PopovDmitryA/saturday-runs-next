import { useCallback, useEffect, useMemo, useState } from "react";
import { ConfirmModal } from "../../components/ConfirmModal";
import { PlatformBadge } from "../../components/PlatformBadge";
import {
  confirmAccountMerge,
  getAuthIdentities,
  getMergePreview,
  linkEmailIdentity,
  oauthStartUrl,
  requestEmailCode,
  unlinkAuthProvider,
  type AuthIdentity,
  type MergePreview,
} from "../../lib/api";
import { platformCodeLabel } from "../../lib/format";

const PROVIDERS: Array<{ id: "vk" | "yandex"; title: string; hint: string }> = [
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
  // Почту нельзя привязать редиректом к провайдеру: владение ящиком
  // подтверждается кодом, поэтому у неё свои два шага прямо в карточке.
  const [emailStep, setEmailStep] = useState<"idle" | "code">("idle");
  const [email, setEmail] = useState("");
  const [emailCode, setEmailCode] = useState("");
  const [emailBusy, setEmailBusy] = useState(false);
  const [emailNotice, setEmailNotice] = useState<string | null>(null);

  const linkedProviders = useMemo(
    () => new Set(identities.map((item) => item.provider)),
    [identities],
  );
  const emailIdentity = useMemo(
    () => identities.find((item) => item.provider === "email") ?? null,
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

  const handleLink = (provider: "vk" | "yandex") => {
    setError(null);
    setLinkingProvider(provider);
    window.location.href = oauthStartUrl(provider, "link");
  };

  const handleEmailCodeRequest = async () => {
    setError(null);
    setEmailBusy(true);
    try {
      await requestEmailCode(email.trim(), true);
      setEmailStep("code");
      setEmailNotice(`Код отправлен на ${email.trim()}`);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Не удалось отправить код");
    } finally {
      setEmailBusy(false);
    }
  };

  const handleEmailLink = async () => {
    setError(null);
    setEmailBusy(true);
    try {
      const result = await linkEmailIdentity(email.trim(), emailCode.trim());
      if (result.merge_token) {
        // Ящиком владеет другой профиль — показываем то же окно объединения,
        // что и при привязке VK или Яндекса.
        setMergeLoading(true);
        setMergePreview(await getMergePreview(result.merge_token));
        setMergeLoading(false);
      }
      setEmailStep("idle");
      setEmail("");
      setEmailCode("");
      setEmailNotice(null);
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Не удалось привязать почту");
    } finally {
      setEmailBusy(false);
    }
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
        Можно войти через VK, Яндекс или по коду на почту и привязать все способы к одному профилю —
        тогда любой из них приведёт в этот аккаунт. При объединении аккаунтов привязки
        5 вёрст / S95 / parkrun у поглощаемого профиля будут сброшены.
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

      {!loading && (
        <div className="auth-provider-email">
          <div className="settings-platform-info">
            <span className="settings-platform-name">Почта</span>
            <span className="muted settings-platform-hint">
              {emailIdentity
                ? emailIdentity.email || emailIdentity.external_id
                : "Вход по одноразовому коду из письма"}
            </span>
            <span className={`settings-toggle-label ${emailIdentity ? "on" : "off"}`}>
              {emailIdentity ? "Привязана" : "Не привязана"}
            </span>
          </div>

          {emailIdentity ? (
            identities.length > 1 && (
              <button
                type="button"
                className="btn secondary btn-sm"
                onClick={() => void handleUnlink("email")}
              >
                Отвязать
              </button>
            )
          ) : (
            <div className="auth-provider-email-form">
              {emailStep === "idle" ? (
                <>
                  <input
                    type="email"
                    inputMode="email"
                    autoComplete="email"
                    placeholder="you@example.com"
                    value={email}
                    onChange={(event) => setEmail(event.target.value)}
                  />
                  <button
                    type="button"
                    className="btn secondary btn-sm"
                    disabled={emailBusy || !email.trim()}
                    onClick={() => void handleEmailCodeRequest()}
                  >
                    {emailBusy ? "Отправляем…" : "Получить код"}
                  </button>
                </>
              ) : (
                <>
                  <input
                    type="text"
                    inputMode="numeric"
                    autoComplete="one-time-code"
                    placeholder="000000"
                    maxLength={6}
                    value={emailCode}
                    onChange={(event) => setEmailCode(event.target.value.replace(/\D/g, ""))}
                  />
                  <button
                    type="button"
                    className="btn secondary btn-sm"
                    disabled={emailBusy || emailCode.length < 6}
                    onClick={() => void handleEmailLink()}
                  >
                    {emailBusy ? "Проверяем…" : "Привязать"}
                  </button>
                </>
              )}
            </div>
          )}
          {emailNotice && <p className="muted settings-platform-hint">{emailNotice}</p>}
        </div>
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
