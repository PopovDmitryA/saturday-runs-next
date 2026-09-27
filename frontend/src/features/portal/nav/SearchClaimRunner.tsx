/**
 * После входа — «Это вы? — Это я, привязать» с тем человеком, на которого гость
 * нажал в поиске перед входом (намерение — nav/searchClaim.ts).
 *
 * Живёт на уровне App, как TeaserClaimRunner: провайдер возвращает человека
 * куда угодно — в кабинет, на /welcome. Но в отличие от тизера молча не
 * привязывает: за общим компьютером намерение мог оставить другой человек, так
 * что без «Это я» привязки нет. Намерение гасится только ответом человека или
 * окончательным отказом сервера; 401 и сбой сети — спросим на следующей
 * загрузке страницы.
 */
import { useCallback, useEffect, useRef, useState, useSyncExternalStore } from "react";
import { createPortal } from "react-dom";
import { Snackbar } from "../../../components/Snackbar";
import {
  ApiError,
  declineSearchClaim,
  linkBySearchToken,
  openSearchClaim,
  type SearchClaimPerson,
} from "../../../lib/api";
import { lockBodyScroll } from "../../../lib/bodyScrollLock";
import { platformCodeLabel } from "../../../lib/format";
import { notifyProfileLinksChanged } from "../../../lib/profileLinksEvents";
import { clearCachedUser, useOptionalUser } from "../../../lib/useOptionalUser";
import { openFindSelfSearch } from "./findSelfSearch";
import { ProtocolClaimCard } from "./ProtocolClaimCard";
import {
  CLAIM_ALREADY_YOURS_TEXT,
  CLAIM_LINKED_TEXT,
  CLAIM_LINKED_TITLE,
  CLAIM_TAKEN_HINT,
  CLAIM_TAKEN_TEXT,
  claimPlatformLinkedText,
  forgetSearchClaim,
  peekSearchClaim,
} from "./searchClaim";
import { useOverlayFocus } from "./useOverlayFocus";

/**
 * Где не спрашиваем: на /login с живой сессией страница сама уводит в кабинет,
 * на /oauth/* и /auth/telegram/* вход ещё не закончен, /render/* — картинки
 * для превью ссылок.
 */
function isServicePath(path: string): boolean {
  return (
    path === "/login" || path.startsWith("/oauth/") || path.startsWith("/auth/telegram/") || path.startsWith("/render/")
  );
}

/** Метка на <html>, пока открыто окно навигации или поиск (useOverlayHistory). */
const OVERLAY_OPEN_CLASS = "site-overlay-open";

function subscribeOverlayOpen(onChange: () => void): () => void {
  const observer = new MutationObserver(onChange);
  observer.observe(document.documentElement, { attributes: true, attributeFilter: ["class"] });
  return () => observer.disconnect();
}

function overlayOpenNow(): boolean {
  return document.documentElement.classList.contains(OVERLAY_OPEN_CLASS);
}

type Prompt =
  | {
      kind: "ask";
      token: string;
      card: SearchClaimPerson;
      linking: boolean;
      error: string | null;
      /** Ошибку можно повторить (сеть, перегрузка). */
      retry: boolean;
      /** Сессия кончилась — намерение не гасим: спросим после следующего входа. */
      keep: boolean;
    }
  | { kind: "info"; card: SearchClaimPerson; viewer: "platform_linked" | "taken" }
  | { kind: "expired"; message: string; card: SearchClaimPerson };

type Notice = { title: string; text: string };

export function SearchClaimRunner({ path }: { path: string }) {
  // Свой запрос сессии, без кэша: сразу после входа кэш вкладки ещё помнит
  // «гость» (вход по почте и через Telegram его не сбрасывает).
  const user = useOptionalUser({ skipCache: true });
  const userId = user ? user.id : null;
  const blocked = isServicePath(path);
  const [prompt, setPrompt] = useState<Prompt | null>(null);
  const [notice, setNotice] = useState<Notice | null>(null);
  // Спрашиваем один раз на загрузку страницы для этого пользователя.
  const startedForRef = useRef<string | null>(null);
  const panelRef = useRef<HTMLDivElement>(null);
  const titleRef = useRef<HTMLHeadingElement>(null);

  useEffect(() => {
    if (userId === null || blocked || startedForRef.current === userId) return;
    const pending = peekSearchClaim();
    if (!pending) return;
    startedForRef.current = userId;
    // Этот же запрос засчитывает на сервере этап воронки «вошли».
    openSearchClaim(pending.token)
      .then(({ viewer_state, ...card }) => {
        if (startedForRef.current !== userId) return;
        if (viewer_state === "can_link") {
          setPrompt({ kind: "ask", token: pending.token, card, linking: false, error: null, retry: false, keep: false });
        } else if (viewer_state === "already_yours") {
          forgetSearchClaim();
          setNotice({ title: "Уже привязан", text: CLAIM_ALREADY_YOURS_TEXT });
        } else if (viewer_state === "platform_linked" || viewer_state === "taken") {
          setPrompt({ kind: "info", card, viewer: viewer_state });
        }
        // «guest» — сессия кончилась между загрузкой и запросом: намерение
        // дождётся следующего входа.
      })
      .catch((error: unknown) => {
        if (startedForRef.current !== userId) return;
        if (error instanceof ApiError && [400, 404, 410].includes(error.status)) {
          setPrompt({ kind: "expired", message: error.message, card: pending.snapshot });
        }
        // 401, сеть, перегрузка — молча: спросим на следующей загрузке.
      });
  }, [userId, blocked]);

  const linking = prompt?.kind === "ask" && prompt.linking;

  const close = useCallback((forget = true) => {
    if (forget) forgetSearchClaim();
    setPrompt(null);
  }, []);

  // Тап мимо окна и Esc — «Не сейчас»: намерение гасим, чтобы окно не
  // всплывало на каждой странице.
  const dismiss = useCallback(() => {
    if (linking) return;
    // Поверх окна открыт поиск или «Меню» — Esc принадлежит им.
    if (document.documentElement.classList.contains(OVERLAY_OPEN_CLASS)) return;
    close(!(prompt?.kind === "ask" && prompt.keep));
  }, [close, linking, prompt]);

  useEffect(() => {
    if (!prompt) return;
    return lockBodyScroll();
  }, [prompt]);

  // Поверх окна открыли поиск или «Меню» (у них z-index выше): Tab и первый
  // фокус — их. Держи окно свою ловушку, обе ловушки на document тянули бы
  // фокус друг у друга, а ответ сервера, пришедший при открытом поиске,
  // увёл бы фокус из поля ввода под затемнение.
  const overlayOpen = useSyncExternalStore(subscribeOverlayOpen, overlayOpenNow);

  useOverlayFocus({
    open: prompt !== null && !overlayOpen,
    contentKey: prompt?.kind ?? null,
    containers: [panelRef],
    initial: () => titleRef.current,
    onEscape: dismiss,
  });

  const confirm = () => {
    if (!prompt || prompt.kind !== "ask" || prompt.linking) return;
    const current = prompt;
    setPrompt({ ...current, linking: true, error: null });
    linkBySearchToken(current.token)
      .then((result) => {
        forgetSearchClaim();
        setPrompt(null);
        if (result.status === "already_linked") {
          setNotice({ title: "Уже привязан", text: CLAIM_ALREADY_YOURS_TEXT });
          return;
        }
        // Имя на сайте и снимки кабинета пересчитаются от новой привязки,
        // плитки онбординга и список профилей в кабинете перечитаются.
        clearCachedUser();
        notifyProfileLinksChanged();
        setNotice({ title: CLAIM_LINKED_TITLE, text: CLAIM_LINKED_TEXT });
      })
      .catch((error: unknown) => {
        const status = error instanceof ApiError ? error.status : 0;
        if (status === 401) {
          setPrompt({
            ...current,
            linking: false,
            error: "Сессия закончилась — войдите снова, и мы предложим привязку ещё раз.",
            retry: false,
            keep: true,
          });
          return;
        }
        setPrompt({
          ...current,
          linking: false,
          error: error instanceof ApiError ? error.message : "Не получилось привязать — попробуйте ещё раз.",
          retry: status === 0 || status === 429 || status >= 500,
          keep: false,
        });
      });
  };

  const decline = () => {
    if (!prompt || prompt.kind !== "ask" || prompt.linking) return;
    declineSearchClaim(prompt.token);
    close();
  };

  const hideNotice = useCallback(() => setNotice(null), []);

  const findAgain = (name: string) => {
    close();
    openFindSelfSearch(name);
  };

  const title = "Это вы?";
  let body: React.ReactNode = null;
  let actions: React.ReactNode = null;
  if (prompt?.kind === "ask") {
    body = (
      <>
        <p>Вы нашли себя в протоколах — привязать этот профиль к аккаунту?</p>
        <ProtocolClaimCard person={prompt.card} />
        {prompt.error && (
          <p className="site-search-claim-modal-error" role="alert">
            {prompt.error}
          </p>
        )}
      </>
    );
    actions =
      prompt.error && !prompt.retry ? (
        <button type="button" className="btn primary modal-btn" onClick={() => close(!prompt.keep)}>
          Закрыть
        </button>
      ) : (
        <>
          <button type="button" className="btn secondary modal-btn" disabled={prompt.linking} onClick={decline}>
            Это не я
          </button>
          <button type="button" className="btn primary modal-btn" disabled={prompt.linking} onClick={confirm}>
            {prompt.linking ? "Привязка…" : prompt.error ? "Повторить" : "Это я — привязать"}
          </button>
        </>
      );
  } else if (prompt?.kind === "info") {
    body = (
      <>
        <ProtocolClaimCard person={prompt.card} />
        {prompt.viewer === "platform_linked" ? (
          <p>{claimPlatformLinkedText(platformCodeLabel(prompt.card.platform_code))}</p>
        ) : (
          <>
            <p>{CLAIM_TAKEN_TEXT}</p>
            <p className="muted">{CLAIM_TAKEN_HINT}</p>
          </>
        )}
      </>
    );
    actions = (
      <button type="button" className="btn primary modal-btn" onClick={() => close()}>
        Понятно
      </button>
    );
  } else if (prompt?.kind === "expired") {
    // Заголовок тот же «Это вы?», а карточка — из снимка: 400, 404 и 410
    // отличаются только причиной, её и говорит текст сервера.
    body = (
      <>
        <ProtocolClaimCard person={prompt.card} />
        <p className="site-search-claim-modal-error" role="alert">
          {prompt.message}
        </p>
      </>
    );
    const name = prompt.card.display_name;
    actions = (
      <>
        <button type="button" className="btn secondary modal-btn" onClick={() => close()}>
          Закрыть
        </button>
        <button type="button" className="btn primary modal-btn" onClick={() => findAgain(name)}>
          Найти себя ещё раз
        </button>
      </>
    );
  }

  return (
    <>
      {prompt &&
        createPortal(
          <div className="modal-overlay" onClick={dismiss} role="presentation">
            <div
              ref={panelRef}
              className="modal-panel site-search-claim-modal"
              role="dialog"
              aria-modal="true"
              aria-labelledby="search-claim-modal-title"
              onClick={(event) => event.stopPropagation()}
            >
              <h2 id="search-claim-modal-title" ref={titleRef} tabIndex={-1} className="modal-title">
                {title}
              </h2>
              <div className="modal-body">{body}</div>
              <div className="modal-actions">{actions}</div>
            </div>
          </div>,
          document.body,
        )}
      <Snackbar open={notice !== null} title={notice?.title} onDismiss={hideNotice}>
        {notice?.text}
      </Snackbar>
    </>
  );
}
