import { useCallback, useEffect, useRef, useState, type FormEvent } from "react";
import { flushSync } from "react-dom";
import { markAccountDeleted } from "../../components/AccountDeletedNotice";
import { ConfirmModal } from "../../components/ConfirmModal";
import { ApiError } from "../../lib/api";
import { forgetPersonalBrowserState } from "../../lib/browserState";
import { pluralizeRu } from "../../lib/format";
import { useOptionalUser } from "../../lib/useOptionalUser";
import {
  DELETE_CONFIRMATION_WORD,
  deleteAccount,
  deleteConfirmationMatches,
  getAccountDeletionPreview,
  getDeletionStatus,
  makeDeletionAttemptId,
  type AccountDeletionPreview,
} from "./accountDeletionApi";
import "./DeleteAccountSection.css";

/**
 * «Удаление профиля» — последний раздел настроек.
 *
 * Что уходит и что остаётся, решено 12.10.2026 (подробно —
 * backend/app/services/account_deletion_service.py): личное удаляется,
 * протоколы систем остаются, карточки и комментарии бэклога остаются с
 * подписью «Профиль удалён». Подтверждение — словом «удалить», набранным
 * руками: случайный клик профиль не удалит. Администратору раздел не
 * показываем — сервер ему всё равно откажет.
 */
// Якорь для ссылок со страницы «О проекте»: /settings#delete-account.
export const DELETE_ACCOUNT_ANCHOR = "delete-account";

const PROTOCOL_SYSTEMS = "5 вёрст, С95, parkrun и RunPark";

/**
 * Запрос на удаление упал — но профиль, может быть, уже удалён.
 *
 * Сервер отвечает сразу после commit (файлы и письмо админу — фоном), но
 * связь может оборваться и после commit: телефон ушёл из сети, сработал наш
 * таймаут, nginx отдал 502 на перезапуске API. Профиля уже нет, а человек
 * видел бы ошибку после удаления, которое не отменить, и 401 на повторе.
 *
 * По одному 401 не понять, удалила ли профиль наша прошлая попытка или
 * сессия кончилась иначе (вышли на другой вкладке) — поэтому спрашиваем
 * сервер по id попытки: он запомнил его после коммита. Отказы, которые
 * сервер даёт до удаления (неверное слово, админ, лимит), — не повод.
 */
async function deletedDespiteError(error: unknown, attemptId: string): Promise<boolean> {
  if (!(error instanceof ApiError)) {
    return false;
  }
  const unclear = error.status === 0 || error.status >= 500 || error.status === 401 || error.status === 404;
  if (!unclear) {
    return false;
  }
  try {
    return (await getDeletionStatus(attemptId)).deleted;
  } catch {
    return false;
  }
}

function DeletionPreviewDetails({ preview }: { preview: AccountDeletionPreview }) {
  const removed: string[] = [
    preview.auth_providers.length
      ? `профиль на сайте${preview.has_avatar ? " с аватаркой" : ""} и способы входа: ${preview.auth_providers.join(", ")}`
      : `профиль на сайте${preview.has_avatar ? " с аватаркой" : ""}`,
  ];
  if (preview.platforms.length) {
    removed.push(`привязки к системам: ${preview.platforms.join(", ")}`);
  }
  if (preview.location_reviews) {
    const photos = preview.review_photos
      ? ` вместе с ${pluralizeRu(preview.review_photos, ["фото", "фото", "фото"])}`
      : "";
    removed.push(`${pluralizeRu(preview.location_reviews, ["отзыв", "отзыва", "отзывов"])} о локациях${photos}`);
  }
  if (preview.goals) {
    removed.push(`цели на год: ${preview.goals}`);
  }
  if (preview.run_plans) {
    removed.push(`планы стартов и отметки «Не бегаю»: ${preview.run_plans}`);
  }
  if (preview.run_plan_series) {
    // Остановленные тоже уходят: в них история прошлых суббот.
    removed.push(
      preview.run_plan_series === 1
        ? "постоянный план «каждую субботу»"
        : `постоянные планы «каждую субботу», с остановленными: ${preview.run_plan_series}`,
    );
  }
  if (preview.run_tracks) {
    removed.push(`${pluralizeRu(preview.run_tracks, ["трек", "трека", "треков"])} пробежек`);
  }
  if (preview.is_organizer || preview.organizer_grants) {
    removed.push("доступ к кабинету организатора");
  }
  if (preview.backlog_votes) {
    removed.push(`${pluralizeRu(preview.backlog_votes, ["голос", "голоса", "голосов"])} в бэклоге`);
  }
  removed.push("настройки и уведомления");

  const kept: string[] = [
    `результаты в протоколах ${PROTOCOL_SYSTEMS} — под именем из протокола, без ссылки на профиль`,
  ];
  if (preview.backlog_cards || preview.backlog_comments) {
    const parts = [
      preview.backlog_cards ? pluralizeRu(preview.backlog_cards, ["карточка", "карточки", "карточек"]) : null,
      preview.backlog_comments
        ? pluralizeRu(preview.backlog_comments, ["комментарий", "комментария", "комментариев"])
        : null,
    ].filter(Boolean);
    kept.push(`${parts.join(" и ")} в бэклоге — с подписью «Профиль удалён»`);
  }

  return (
    <>
      <p className="delete-account-modal-heading">Удалится навсегда</p>
      <ul className="delete-account-list">
        {removed.map((item) => (
          <li key={item}>{item}</li>
        ))}
      </ul>
      <p className="delete-account-modal-heading">Останется</p>
      <ul className="delete-account-list">
        {kept.map((item) => (
          <li key={item}>{item}</li>
        ))}
      </ul>
    </>
  );
}

export function DeleteAccountSection() {
  const user = useOptionalUser();
  const [open, setOpen] = useState(false);
  const [preview, setPreview] = useState<AccountDeletionPreview | null>(null);
  const [previewLoading, setPreviewLoading] = useState(false);
  const [previewFailed, setPreviewFailed] = useState(false);
  const [confirmation, setConfirmation] = useState("");
  const [deleting, setDeleting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  // id попытки удаления — см. deletedDespiteError. Живёт дольше окна:
  // закрыли, открыли и повторили — это та же история.
  const attemptIdRef = useRef<string | null>(null);
  if (attemptIdRef.current === null) {
    attemptIdRef.current = makeDeletionAttemptId();
  }

  const visible = Boolean(user && !user.is_admin);

  // Пришли по /settings#delete-account (ссылка со страницы «О проекте»):
  // раздел рисуется, когда известен пользователь, и штатная прокрутка к
  // якорю до него не достаёт. Доезжаем сами. Раздел последний, а разделы
  // выше дорисовываются после своих запросов и сдвигают его вниз, — поэтому
  // ещё пару секунд доводим к нему снова, пока человек сам не взялся листать.
  const scrolledRef = useRef(false);
  useEffect(() => {
    if (!visible || scrolledRef.current || window.location.hash !== `#${DELETE_ACCOUNT_ANCHOR}`) {
      return;
    }
    const section = document.getElementById(DELETE_ACCOUNT_ANCHOR);
    if (!section) {
      return;
    }
    scrolledRef.current = true;
    const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    section.scrollIntoView({ behavior: reduceMotion ? "auto" : "smooth", block: "start" });
    const container = section.parentElement;
    if (!container || typeof ResizeObserver === "undefined") {
      return;
    }
    let first = true;
    const observer = new ResizeObserver(() => {
      // Первый вызов приходит сразу при подписке — это не сдвиг раскладки.
      if (first) {
        first = false;
        return;
      }
      section.scrollIntoView({ behavior: "auto", block: "start" });
    });
    const userEvents = ["wheel", "touchstart", "keydown"] as const;
    const stop = () => {
      observer.disconnect();
      window.clearTimeout(timer);
      for (const name of userEvents) {
        window.removeEventListener(name, stop);
      }
    };
    const timer = window.setTimeout(stop, 2500);
    observer.observe(container);
    for (const name of userEvents) {
      window.addEventListener(name, stop, { passive: true });
    }
    return stop;
  }, [visible]);

  const loadPreview = useCallback(async () => {
    setPreviewLoading(true);
    setPreviewFailed(false);
    try {
      setPreview(await getAccountDeletionPreview());
    } catch {
      // Без цифр окно всё равно честное: общий текст ниже говорит то же.
      setPreviewFailed(true);
    } finally {
      setPreviewLoading(false);
    }
  }, []);

  const openModal = () => {
    setConfirmation("");
    setError(null);
    setOpen(true);
    void loadPreview();
  };

  const closeModal = useCallback(() => {
    if (!deleting) {
      setOpen(false);
    }
  }, [deleting]);

  const canConfirm = deleteConfirmationMatches(confirmation);

  const performDelete = async () => {
    if (!canConfirm || deleting) {
      return;
    }
    setDeleting(true);
    setError(null);
    const attemptId = attemptIdRef.current ?? makeDeletionAttemptId();
    try {
      await deleteAccount(confirmation, attemptId);
    } catch (err) {
      // Пока спрашиваем исход, кнопка так и крутится: он ещё не ясен.
      if (!(await deletedDespiteError(err, attemptId))) {
        // flushSync: поле ещё disabled, а focus() на нём ничего не делает —
        // сперва дорисовываем снятый disabled, потом возвращаем фокус, чтобы
        // Enter повторил попытку без мыши.
        flushSync(() => {
          setError(err instanceof Error ? err.message : "Не удалось удалить профиль");
          setDeleting(false);
        });
        inputRef.current?.focus();
        return;
      }
    }
    // Сессию сервер уже погасил (а куки стёр, если ответ дошёл). Здесь —
    // память браузера о человеке и метка для прощального сообщения на главной
    // (AccountDeletedNotice). replace, а не переход: «Назад» не должен вести
    // в настройки удалённого.
    forgetPersonalBrowserState();
    markAccountDeleted();
    window.location.replace("/");
  };

  const handleSubmit = (event: FormEvent) => {
    event.preventDefault();
    void performDelete();
  };

  if (!visible) {
    return null;
  }

  return (
    <section className="card delete-account-card" id={DELETE_ACCOUNT_ANCHOR}>
      <h2 className="section-title">Удаление профиля</h2>
      <p className="muted settings-lead">
        Удалятся профиль на сайте, способы входа, привязки к беговым системам, настройки,
        уведомления, цели, планы стартов, отзывы о локациях и треки. Результаты в протоколах{" "}
        {PROTOCOL_SYSTEMS}{" "}
        останутся — они принадлежат системам, а не сайту. Восстановить профиль нельзя, но завести
        новый можно в любой момент.
      </p>
      <div className="delete-account-actions">
        <button type="button" className="btn btn-ghost btn-danger btn-sm" onClick={openModal}>
          Удалить профиль…
        </button>
      </div>

      <ConfirmModal
        open={open}
        title="Удалить профиль навсегда?"
        variant="danger"
        confirmLabel="Удалить навсегда"
        cancelLabel="Оставить"
        confirmLoading={deleting}
        confirmDisabled={!canConfirm}
        onCancel={closeModal}
        onConfirm={() => void performDelete()}
      >
        <form className="delete-account-modal" onSubmit={handleSubmit} noValidate>
          {previewLoading && <p className="muted">Собираем, что удалится…</p>}
          {!previewLoading && preview && <DeletionPreviewDetails preview={preview} />}
          {!previewLoading && !preview && previewFailed && (
            <p>
              Удалятся профиль, способы входа, привязки, настройки, цели, планы, отзывы и треки. Результаты в
              протоколах {PROTOCOL_SYSTEMS} останутся.
            </p>
          )}
          <p className="muted delete-account-cache-note">
            Имя ещё несколько часов может быть видно в рейтингах и на страницах локаций — пока не
            обновятся их сохранённые копии.
          </p>
          <label className="delete-account-confirm-label" htmlFor="delete-account-confirm">
            Чтобы подтвердить, введите слово <strong>{DELETE_CONFIRMATION_WORD}</strong>
          </label>
          <input
            ref={inputRef}
            id="delete-account-confirm"
            className="delete-account-confirm-input"
            type="text"
            value={confirmation}
            onChange={(event) => setConfirmation(event.target.value)}
            disabled={deleting}
            autoFocus
            autoComplete="off"
            autoCapitalize="none"
            autoCorrect="off"
            spellCheck={false}
            enterKeyHint="done"
            aria-invalid={error ? true : undefined}
            aria-describedby={error ? "delete-account-error" : undefined}
          />
          {error && (
            <p className="error-text" id="delete-account-error" role="alert">
              {error}
            </p>
          )}
        </form>
      </ConfirmModal>
    </section>
  );
}
