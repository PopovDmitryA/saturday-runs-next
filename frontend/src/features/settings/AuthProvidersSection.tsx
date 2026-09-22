import { useCallback, useEffect, useMemo, useState } from "react";
import { ConfirmModal } from "../../components/ConfirmModal";
import { EmailSpamHint } from "../../components/EmailSpamHint";
import { PlatformBadge } from "../../components/PlatformBadge";
import {
  confirmAccountMerge,
  getAuthIdentities,
  getMergePreview,
  getTelegramLoginConfig,
  linkEmailIdentity,
  oauthStartUrl,
  requestEmailCode,
  telegramStartUrl,
  unlinkAuthProvider,
  type AuthIdentity,
  type MergeConflictChoice,
  type MergeLinkPreview,
  type MergePreview,
  type MergeStrategy,
} from "../../lib/api";
import {
  checkNotificationChannel,
  getNotificationSettings,
  requestTelegramNotificationLink,
  toggleNotificationChannel,
  type NotificationChannelCode,
  type NotificationSettingsState,
} from "../../lib/api";
import { platformCodeLabel } from "../../lib/format";
import { TelegramBotLogin } from "../auth/TelegramBotLogin";
import { NotificationChannelProblem, NotificationChannelToggle } from "./NotificationChannelToggle";
import { NotificationPrefsModal } from "./NotificationPrefsModal";

const PROVIDERS: Array<{ id: "vk" | "yandex" | "telegram"; title: string; hint: string }> = [
  { id: "vk", title: "VK", hint: "Вход через VK ID" },
  { id: "yandex", title: "Яндекс", hint: "Вход через Яндекс ID" },
  { id: "telegram", title: "Telegram", hint: "Вход подтверждением в Telegram" },
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
  // Развилка объединения: забрать привязки обоих профилей или оставить только
  // свои. Третьего варианта нет — забрать чужие, выбросив свои, нельзя.
  const [mergeStrategy, setMergeStrategy] = useState<MergeStrategy>("union");
  // Ответы по системам, привязанным с обеих сторон: одна учётка на аккаунт.
  const [conflictChoices, setConflictChoices] = useState<Record<string, MergeConflictChoice>>({});
  // Почту нельзя привязать редиректом к провайдеру: владение ящиком
  // подтверждается кодом, поэтому у неё свои два шага прямо в карточке.
  const [emailStep, setEmailStep] = useState<"idle" | "code">("idle");
  const [email, setEmail] = useState("");
  const [emailCode, setEmailCode] = useState("");
  const [emailBusy, setEmailBusy] = useState(false);
  const [emailNotice, setEmailNotice] = useState<string | null>(null);
  // Отправитель приходит в ответе на запрос кода: подсказка про «Спам» без
  // адреса бесполезна — искать письмо человек будет именно по нему.
  const [emailSender, setEmailSender] = useState("");
  // Бот жив — привязка подтверждением в нём, прямо в этой карточке; иначе
  // редирект в виджет Telegram, как у OAuth-провайдеров.
  const [telegramBotLogin, setTelegramBotLogin] = useState(false);
  const [telegramBotFlow, setTelegramBotFlow] = useState(false);
  // Уведомления живут прямо в карточках способов входа: у каждого канала свой
  // тумблер, «о чём присылать» — в модалке. Загружаются отдельно от привязок:
  // проверка доставляемости ходит к боту и VK и не должна тормозить список.
  const [notifyState, setNotifyState] = useState<NotificationSettingsState | null>(null);
  const [notifyBusy, setNotifyBusy] = useState(false);
  const [notifyError, setNotifyError] = useState<string | null>(null);
  const [prefsOpen, setPrefsOpen] = useState(false);

  const loadNotifications = useCallback(async () => {
    try {
      setNotifyState(await getNotificationSettings());
    } catch (err) {
      setNotifyError(err instanceof Error ? err.message : "Не удалось загрузить настройки уведомлений");
    }
  }, []);

  useEffect(() => {
    void loadNotifications();
  }, [loadNotifications]);

  // Пришли по якорю #notifications (модалка, письмо, страница отписки):
  // раздел внизу длинных настроек, а браузер сам к нему не прокручивает —
  // контент дорисовывается после загрузки. Прокручиваем, когда всё готово.
  useEffect(() => {
    if (loading || !notifyState || window.location.hash !== "#notifications") {
      return;
    }
    const node = document.getElementById("notifications");
    if (node) {
      window.setTimeout(() => node.scrollIntoView({ behavior: "smooth", block: "start" }), 50);
    }
  }, [loading, notifyState]);

  // Включаем канал, до которого бот или сообщество пока не дотягиваются:
  // окно открываем синхронно в клике (иначе браузер сочтёт его всплывающим),
  // адрес подставляем после ответа сервера и ждём, пока человек нажмёт Start.
  const [awaitingChannel, setAwaitingChannel] = useState<NotificationChannelCode | null>(null);

  const handleNotifyToggle = async (channel: NotificationChannelCode, enabled: boolean) => {
    const current = notifyChannel(channel);
    const needsPermission = enabled && current?.deliverable === false && channel !== "email";
    const popup = needsPermission ? window.open("", "_blank") : null;
    setNotifyBusy(true);
    setNotifyError(null);
    try {
      const next = await toggleNotificationChannel(channel, enabled);
      setNotifyState(next);
      const updated = next.channels.find((item) => item.channel === channel);
      if (popup) {
        let target: string | null = null;
        if (channel === "telegram") {
          try {
            target = (await requestTelegramNotificationLink()).connect_url;
          } catch {
            target = updated?.bot_url ?? null;
          }
        } else {
          target = updated?.allow_url ?? null;
        }
        if (target) {
          popup.location.href = target;
          setAwaitingChannel(channel);
        } else {
          popup.close();
        }
      }
    } catch (err) {
      popup?.close();
      setNotifyError(err instanceof Error ? err.message : "Не удалось сохранить уведомления");
    } finally {
      setNotifyBusy(false);
    }
  };

  // Ждём подтверждения в боте/сообществе: перепроверяем каждые несколько секунд
  // и при возврате во вкладку, пока проверка не позеленеет или не пройдёт минута.
  useEffect(() => {
    if (!awaitingChannel) {
      return;
    }
    let attempts = 0;
    let stopped = false;
    const poll = async () => {
      if (stopped) return;
      attempts += 1;
      try {
        const result = await checkNotificationChannel(awaitingChannel);
        if (result.ok) {
          stopped = true;
          setAwaitingChannel(null);
          await loadNotifications();
          return;
        }
      } catch {
        // сеть моргнула — попробуем ещё
      }
      if (attempts >= 20) {
        stopped = true;
        setAwaitingChannel(null);
        await loadNotifications();
      }
    };
    const timer = window.setInterval(() => void poll(), 4000);
    const onVisible = () => {
      if (document.visibilityState === "visible") void poll();
    };
    document.addEventListener("visibilitychange", onVisible);
    return () => {
      stopped = true;
      window.clearInterval(timer);
      document.removeEventListener("visibilitychange", onVisible);
    };
  }, [awaitingChannel, loadNotifications]);

  const handleNotifyRecheck = async (channel: NotificationChannelCode) => {
    setNotifyBusy(true);
    setNotifyError(null);
    try {
      await checkNotificationChannel(channel);
      await loadNotifications();
    } catch (err) {
      setNotifyError(err instanceof Error ? err.message : "Не удалось проверить канал");
    } finally {
      setNotifyBusy(false);
    }
  };

  const notifyChannel = (code: NotificationChannelCode) =>
    notifyState?.channels.find((item) => item.channel === code) ?? null;

  const renderNotifyToggle = (code: NotificationChannelCode) => {
    const channel = notifyChannel(code);
    if (!channel || !channel.linked) {
      return null;
    }
    return (
      <NotificationChannelToggle
        state={channel}
        busy={notifyBusy}
        onToggle={(enabled) => void handleNotifyToggle(code, enabled)}
      />
    );
  };

  const renderNotifyProblem = (code: NotificationChannelCode) => {
    const channel = notifyChannel(code);
    if (!channel || !channel.linked) {
      return null;
    }
    if (awaitingChannel === code) {
      return (
        <p className="notify-row-problem notify-row-waiting">
          {code === "telegram"
            ? "Без разрешения в боте писать вам мы не можем. Бот открыт в отдельном окне — нажмите там «Start», и уведомления заработают."
            : "Без разрешения сообщества писать вам мы не можем. Диалог открыт в отдельном окне — нажмите там «Разрешить сообщения», и уведомления заработают."}{" "}
          <span className="muted">Ждём подтверждения…</span>
        </p>
      );
    }
    return (
      <NotificationChannelProblem
        state={channel}
        busy={notifyBusy}
        onRecheck={() => void handleNotifyRecheck(code)}
      />
    );
  };

  // Какой канал даёт тумблер у этого способа входа: Яндекс — почтовый, если
  // отдельной почты нет (адрес берётся из него).
  const providerChannel = (providerId: "vk" | "yandex" | "telegram"): NotificationChannelCode | null => {
    if (providerId === "yandex") {
      return !emailIdentity && notifyChannel("email")?.email_source === "yandex" ? "email" : null;
    }
    return providerId;
  };

  const enabledChannelTitles = (notifyState?.channels ?? [])
    .filter((item) => item.enabled)
    .map((item) => item.title);

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
      void loadNotifications();
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
    let cancelled = false;
    void getTelegramLoginConfig()
      .then((config) => {
        if (!cancelled) {
          setTelegramBotLogin(Boolean(config.bot_login));
        }
      })
      .catch(() => {
        // Не узнали — привязываем виджетом, как раньше.
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const applyPreview = useCallback((preview: MergePreview) => {
    setMergePreview(preview);
    setMergeStrategy(preview.default_strategy);
    // Ни один конфликт заранее не решён: выбор профиля — сознательное действие
    // человека, предвыбранная галочка увела бы чужую учётку молча.
    setConflictChoices({});
  }, []);

  // Пока по каждой спорной системе не выбран профиль, объединять нечего:
  // бэкенд такой запрос отклонит, и незачем доводить человека до ошибки.
  const mergeHasUnresolvedConflicts = useMemo(() => {
    if (!mergePreview || mergeStrategy !== "union") {
      return false;
    }
    return mergePreview.conflicts.some((item) => !conflictChoices[item.platform_code]);
  }, [mergePreview, mergeStrategy, conflictChoices]);

  // Спорные системы в список «переедет сюда» не попадают: их судьба решается
  // отдельным выбором ниже, и показывать там чужой профиль как решённый —
  // значит противоречить самому себе.
  const mergedLinksWithoutConflicts = useMemo(() => {
    if (!mergePreview) {
      return [];
    }
    const disputed = new Set(mergePreview.conflicts.map((item) => item.platform_code));
    return mergePreview.merged_links.filter((item) => !disputed.has(item.platform_code));
  }, [mergePreview]);

  const closeMerge = useCallback(() => {
    setMergePreview(null);
    setConflictChoices({});
  }, []);

  useEffect(() => {
    if (!initialMergeToken) {
      return;
    }
    setMergeLoading(true);
    void getMergePreview(initialMergeToken)
      .then(applyPreview)
      .catch((err) => setError(err instanceof Error ? err.message : "Не удалось загрузить объединение"))
      .finally(() => setMergeLoading(false));
  }, [initialMergeToken]);

  const handleLink = (provider: "vk" | "yandex" | "telegram") => {
    setError(null);
    if (provider === "telegram" && telegramBotLogin) {
      setTelegramBotFlow(true);
      return;
    }
    setLinkingProvider(provider);
    // У Telegram свой старт: он не OAuth-провайдер, подтверждение приходит
    // подписанными данными, а не кодом обмена.
    window.location.href =
      provider === "telegram" ? telegramStartUrl("link") : oauthStartUrl(provider, "link");
  };

  const handleEmailCodeRequest = async () => {
    setError(null);
    setEmailBusy(true);
    try {
      const sent = await requestEmailCode(email.trim(), true);
      setEmailStep("code");
      setEmailSender(sent.sender ?? "");
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
        applyPreview(await getMergePreview(result.merge_token));
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
      await confirmAccountMerge(mergePreview.merge_token, {
        strategy: mergeStrategy,
        conflictChoices: mergeStrategy === "union" ? conflictChoices : {},
      });
      closeMerge();
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
    <section className="card" id="notifications">
      <h2 className="section-title">Способы входа и уведомления</h2>
      <p className="muted settings-lead">
        Можно войти через VK, Яндекс или по коду на почту и привязать все способы к одному профилю —
        тогда любой из них приведёт в этот аккаунт. Если способ входа уже занят вторым
        аккаунтом, профили можно объединить: учётки 5 вёрст / S95 / parkrun при этом либо
        соберутся вместе, либо останутся только у текущего профиля — выбор спросим.
      </p>
      {notifyState && (
        <div className="notify-summary">
          <div className="notify-summary-text">
            <span className="settings-platform-name">🔔 Уведомления</span>
            <span className="muted settings-platform-hint">
              {enabledChannelTitles.length > 0
                ? `Включены: ${enabledChannelTitles.join(", ")}. Приходят туда, где вы вошли на сайт.`
                : "Выключены. Включите тумблером у способа входа — сообщения придут туда же, где вы вошли."}
            </span>
          </div>
          <button type="button" className="btn secondary btn-sm" onClick={() => setPrefsOpen(true)}>
            О чём присылать
          </button>
        </div>
      )}
      {notifyError && <p className="error-text">{notifyError}</p>}

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
                {linked && providerChannel(provider.id) && renderNotifyToggle(providerChannel(provider.id)!)}
                {linked && providerChannel(provider.id) && renderNotifyProblem(providerChannel(provider.id)!)}
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

      {telegramBotFlow && (
        <TelegramBotLogin
          mode="link"
          fallbackHref={telegramStartUrl("link")}
          onCancel={() => setTelegramBotFlow(false)}
          onLinked={() => {
            setTelegramBotFlow(false);
            void load();
          }}
          onMergeRequired={(token) => {
            setTelegramBotFlow(false);
            setMergeLoading(true);
            void getMergePreview(token)
              .then(applyPreview)
              .catch((err) =>
                setError(err instanceof Error ? err.message : "Не удалось загрузить объединение"),
              )
              .finally(() => setMergeLoading(false));
          }}
        />
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
          {emailIdentity && renderNotifyToggle("email")}
          {emailIdentity && renderNotifyProblem("email")}

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
                    onKeyDown={(event) => {
                      // Enter в поле = нажатие кнопки рядом: набрал адрес и жмёшь
                      // ввод, не целясь мышью (Дмитрий 02.09.2026). Формы здесь нет,
                      // поэтому браузер сам этого не делает.
                      if (event.key === "Enter" && !emailBusy && email.trim()) {
                        event.preventDefault();
                        void handleEmailCodeRequest();
                      }
                    }}
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
                    onKeyDown={(event) => {
                      // Тот же приём на шаге кода: ввёл шесть цифр — Enter привязывает.
                      if (event.key === "Enter" && !emailBusy && emailCode.length >= 6) {
                        event.preventDefault();
                        void handleEmailLink();
                      }
                    }}
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
          {/* Про «Спам» пишем и здесь: письмо то же самое, и теряется так же. */}
          {emailStep === "code" && !emailIdentity && (
            <EmailSpamHint sender={emailSender} />
          )}
        </div>
      )}

      {notifyState && (
        <NotificationPrefsModal
          open={prefsOpen}
          state={notifyState}
          onClose={() => setPrefsOpen(false)}
          onChange={setNotifyState}
        />
      )}

      <ConfirmModal
        open={mergePreview != null}
        title="Объединить профили?"
        confirmLabel="Объединить"
        variant="danger"
        confirmLoading={mergeConfirmLoading}
        confirmDisabled={mergeHasUnresolvedConflicts}
        onCancel={closeMerge}
        onConfirm={() => void handleConfirmMerge()}
      >
        {mergeLoading && <p className="muted">Загрузка…</p>}
        {mergePreview && !mergePreview.requires_choice && (
          <>
            <p>У присоединяемого профиля нет привязанных учёток — объединяем без потерь.</p>
            <p className="muted">{mergePreview.warning}</p>
          </>
        )}
        {mergePreview && mergePreview.requires_choice && (
          <div className="auth-merge-fork">
            <p>Что сделать с привязанными учётками беговых систем?</p>

            <label className="auth-merge-option">
              <input
                type="radio"
                name="merge-strategy"
                checked={mergeStrategy === "union"}
                onChange={() => setMergeStrategy("union")}
              />
              <span>
                <strong>Объединить</strong>
                <span className="muted"> — учётки обоих профилей останутся у вас</span>
                <MergeLinkList
                  caption="Переедет сюда:"
                  items={mergedLinksWithoutConflicts}
                  empty="спорные системы — ниже"
                />
              </span>
            </label>

            <label className="auth-merge-option">
              <input
                type="radio"
                name="merge-strategy"
                checked={mergeStrategy === "survivor_only"}
                onChange={() => setMergeStrategy("survivor_only")}
              />
              <span>
                <strong>Оставить только текущие</strong>
                <span className="muted">
                  {" "}
                  — учётки присоединяемого профиля будут отвязаны
                </span>
                <MergeLinkList
                  caption="Останется только это:"
                  items={mergePreview.survivor_links}
                  empty="сейчас не привязано ничего"
                />
              </span>
            </label>

            {mergeStrategy === "union" && mergePreview.conflicts.length > 0 && (
              <div className="auth-merge-conflicts">
                <p>
                  Эти системы привязаны в обоих профилях. Учётка системы может быть только
                  одна — выберите, какую оставить.
                </p>
                {mergePreview.conflicts.map((conflict) => (
                  <fieldset key={conflict.platform_code} className="auth-merge-conflict">
                    <legend>
                      <PlatformBadge code={conflict.platform_code} />{" "}
                      {platformCodeLabel(conflict.platform_code)}
                    </legend>
                    {(
                      [
                        ["survivor", conflict.survivor, "текущий профиль"],
                        ["merged", conflict.merged, "присоединяемый профиль"],
                      ] as Array<[MergeConflictChoice, MergeLinkPreview, string]>
                    ).map(([side, item, hint]) => (
                      <label key={side} className="auth-merge-option">
                        <input
                          type="radio"
                          name={`merge-conflict-${conflict.platform_code}`}
                          checked={conflictChoices[conflict.platform_code] === side}
                          onChange={() =>
                            setConflictChoices((prev) => ({
                              ...prev,
                              [conflict.platform_code]: side,
                            }))
                          }
                        />
                        <span>
                          {item.display_name ?? item.external_user_id}
                          <span className="muted"> — {hint}</span>
                        </span>
                      </label>
                    ))}
                  </fieldset>
                ))}
              </div>
            )}

            <p className="muted">{mergePreview.warning}</p>
          </div>
        )}
      </ConfirmModal>
    </section>
  );
}

function MergeLinkList({
  caption,
  items,
  empty = "нет привязанных учёток",
}: {
  caption: string;
  items: MergeLinkPreview[];
  empty?: string;
}) {
  if (items.length === 0) {
    return <span className="muted auth-merge-links-empty"> ({empty})</span>;
  }
  return (
    <ul className="auth-merge-links-list" aria-label={caption}>
      {items.map((item) => (
        <li key={`${item.platform_code}-${item.external_user_id}`}>
          <PlatformBadge code={item.platform_code} /> {platformCodeLabel(item.platform_code)} —{" "}
          {item.display_name ?? item.external_user_id}
        </li>
      ))}
    </ul>
  );
}
