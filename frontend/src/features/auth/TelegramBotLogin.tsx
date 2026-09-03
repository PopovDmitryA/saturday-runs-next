import { useEffect, useRef, useState } from "react";
import {
  claimLoginRequest,
  createLoginRequest,
  getLoginRequestStatus,
  type LoginRequestResponse,
} from "../../lib/api";
import "./telegramBotLogin.css";

// Вход подтверждением в боте. Вкладка создаёт запрос, человек уходит по deep
// link в Telegram, жмёт там «Подтвердить вход» — и эта же вкладка, опрашивая
// статус, забирает сессию сама. Ссылка в боте остаётся страховкой на случай,
// если вкладку закрыли.
//
// Если бот перестал отмечаться (упала прокси, лёг контейнер), подтверждения
// не дождаться: после нескольких опросов без метки предлагаем запасной путь —
// виджет Telegram в браузере, ему сервер с доступом к Telegram не нужен.

export type TelegramBotLoginMode = "login" | "link";

type Phase = "starting" | "waiting" | "claiming" | "done" | "denied" | "expired" | "error";

type TelegramBotLoginProps = {
  mode: TelegramBotLoginMode;
  // Куда уводить, когда бот молчит: /auth/telegram/start виджета.
  fallbackHref: string;
  onLinked?: () => void;
  onMergeRequired?: (mergeToken: string) => void;
  onCancel: () => void;
};

const POLL_INTERVAL_MS = 2000;
// ~6 секунд без метки бота — показываем запасной путь, опрос не прекращаем.
const BOT_SILENT_POLLS = 3;

export function TelegramBotLogin({
  mode,
  fallbackHref,
  onLinked,
  onMergeRequired,
  onCancel,
}: TelegramBotLoginProps) {
  const [request, setRequest] = useState<LoginRequestResponse | null>(null);
  const [phase, setPhase] = useState<Phase>("starting");
  const [error, setError] = useState<string | null>(null);
  const [botSilent, setBotSilent] = useState(false);
  const silentPolls = useRef(0);
  const finished = useRef(false);

  useEffect(() => {
    let cancelled = false;
    void createLoginRequest({ link: mode === "link", consent: true })
      .then((result) => {
        if (cancelled) {
          return;
        }
        setRequest(result);
        setPhase("waiting");
      })
      .catch((err: unknown) => {
        if (cancelled) {
          return;
        }
        setError(err instanceof Error ? err.message : "Не удалось начать вход");
        setPhase("error");
      });
    return () => {
      cancelled = true;
    };
  }, [mode]);

  useEffect(() => {
    if (!request || phase !== "waiting") {
      return;
    }
    const token = request.request_token;
    let inFlight = false;

    const finish = (next: Phase) => {
      finished.current = true;
      setPhase(next);
    };

    const poll = async () => {
      if (inFlight || finished.current) {
        return;
      }
      inFlight = true;
      try {
        const status = await getLoginRequestStatus(token);
        if (finished.current) {
          return;
        }
        if (status.bot_alive === false) {
          silentPolls.current += 1;
          if (silentPolls.current >= BOT_SILENT_POLLS) {
            setBotSilent(true);
          }
        } else {
          silentPolls.current = 0;
          setBotSilent(false);
        }
        switch (status.status) {
          case "confirmed": {
            finish("claiming");
            const result = await claimLoginRequest(token);
            window.location.replace(`/${result.redirect}`);
            return;
          }
          case "claimed":
            // Сессию уже забрали (например, в соседней вкладке) — просто уходим.
            finish("done");
            window.location.replace("/dashboard");
            return;
          case "linked":
            finish("done");
            onLinked?.();
            return;
          case "merge_required":
            finish("done");
            if (status.merge_token) {
              onMergeRequired?.(status.merge_token);
            }
            return;
          case "denied":
            finish("denied");
            return;
          case "expired":
            finish("expired");
            return;
          default:
            return;
        }
      } catch (err) {
        if (!finished.current) {
          setError(err instanceof Error ? err.message : "Не удалось проверить статус входа");
          finish("error");
        }
      } finally {
        inFlight = false;
      }
    };

    const intervalId = window.setInterval(() => void poll(), POLL_INTERVAL_MS);
    // Вернулись из Telegram — не ждать следующего тика.
    const onVisible = () => {
      if (document.visibilityState === "visible") {
        void poll();
      }
    };
    document.addEventListener("visibilitychange", onVisible);
    window.addEventListener("focus", onVisible);
    return () => {
      window.clearInterval(intervalId);
      document.removeEventListener("visibilitychange", onVisible);
      window.removeEventListener("focus", onVisible);
    };
  }, [request, phase, onLinked, onMergeRequired]);

  const linking = mode === "link";
  const title = linking ? "Подтвердите привязку в Telegram" : "Подтвердите вход в Telegram";
  const fallbackLabel = linking ? "Привязать через Telegram в браузере" : "Войти через Telegram в браузере";

  return (
    <div className="tg-bot-login" role="status" aria-live="polite">
      <p className="tg-bot-login-title">{title}</p>

      {phase === "starting" && <p className="tg-bot-login-status">Готовим запрос…</p>}

      {phase === "waiting" && request && (
        <>
          <a
            className="btn primary tg-bot-login-open"
            href={request.bot_url}
            target="_blank"
            rel="noreferrer"
          >
            Открыть Telegram
          </a>
          <p className="tg-bot-login-hint">
            В боте нажмите «{linking ? "Подтвердить" : "Подтвердить вход"}» — эта вкладка{" "}
            {linking ? "обновится" : "войдёт"} сама. Запрос действует{" "}
            {Math.round(request.expires_in / 60)} минут.
          </p>
          <p className="tg-bot-login-status">
            <span className="tg-bot-login-spinner" aria-hidden="true" />
            Ждём подтверждения…
          </p>
          {botSilent && (
            <div className="tg-bot-login-fallback">
              <p>Бот сейчас не отвечает. Подтверждение из Telegram может не дойти.</p>
              <a className="btn secondary" href={fallbackHref} data-full-nav>
                {fallbackLabel}
              </a>
            </div>
          )}
        </>
      )}

      {phase === "claiming" && <p className="tg-bot-login-status">Входим…</p>}
      {phase === "done" && <p className="tg-bot-login-status">Готово</p>}

      {phase === "denied" && (
        <div className="tg-bot-login-fallback">
          <p>
            В Telegram нажали «Это не я» — {linking ? "привязка" : "вход"} отменён
            {linking ? "а" : ""}. Если это ошибка, начните заново.
          </p>
        </div>
      )}

      {phase === "expired" && (
        <div className="tg-bot-login-fallback">
          <p>Время ожидания вышло. Начните заново или войдите через Telegram в браузере.</p>
          <a className="btn secondary" href={fallbackHref} data-full-nav>
            {fallbackLabel}
          </a>
        </div>
      )}

      {phase === "error" && (
        <div className="tg-bot-login-fallback">
          <p>{error ?? "Что-то пошло не так."}</p>
          <a className="btn secondary" href={fallbackHref} data-full-nav>
            {fallbackLabel}
          </a>
        </div>
      )}

      {phase !== "claiming" && phase !== "done" && (
        <button type="button" className="tg-bot-login-cancel" onClick={onCancel}>
          {phase === "waiting" || phase === "starting" ? "Отмена" : "Назад"}
        </button>
      )}
    </div>
  );
}
