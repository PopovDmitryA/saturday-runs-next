import { useEffect, useRef, useState, type ReactNode } from "react";
import { Snackbar } from "../../components/Snackbar";
import { useSnackbar, type SnackbarState } from "../../hooks/useSnackbar";
import { copyToClipboard } from "../../lib/clipboard";
import "./locationRate.css";

/** Адрес вкладки «Оценка» локации — его и раздают в чат локации. */
export function locationRatePath(slug: string): string {
  return `/locations/${encodeURIComponent(slug)}/rate`;
}

/**
 * Системное окно «Поделиться» — только на сенсорных экранах. На компьютере
 * оно есть в Chrome и Safari, но ссылку там вставляют в Telegram Desktop или
 * веб-версию чата: буфер обмена для этого удобнее системного окна.
 */
function canShareLink(): boolean {
  if (typeof navigator === "undefined" || typeof navigator.share !== "function") {
    return false;
  }
  try {
    return window.matchMedia("(pointer: coarse)").matches;
  } catch {
    return false;
  }
}

/** Выделить текст узла целиком; true — если в выделении и правда весь адрес. */
function selectNodeText(node: HTMLElement | null, expected: string): boolean {
  const selection = typeof window.getSelection === "function" ? window.getSelection() : null;
  if (!node || !selection) {
    return false;
  }
  try {
    selection.selectAllChildren(node);
  } catch {
    return false;
  }
  return selection.toString().trim() === expected;
}

type Notify = (state: Omit<SnackbarState, "open">) => void;

/**
 * Ссылка на вкладку «Оценка» для чата локации: сам адрес текстом (его можно
 * выделить и скопировать руками) и кнопка «Поделиться» — на телефоне
 * системное окно, на компьютере копирование. Видна всем, и гостю тоже:
 * позвать оценить может любой участник, не только оргкоманда (решение
 * Дмитрия 22.10.2026). Пояснение «зачем» пишет вызывающая страница — у
 * вкладки и оргкабинета оно своё.
 *
 * slug — основной слаг локации из ответа сервера, а не из адреса: по
 * второстепенному ссылка тоже откроется, но в чате должна жить одна.
 *
 * onNotify — снекбар страницы. У вкладки «Оценка» он свой («Оценка
 * сохранена»), и второй снекбар этого блока лёг бы на него в тот же угол
 * экрана (ревью 22.10.2026); без onNotify блок показывает свой.
 */
export function RateLinkShare({
  slug,
  locationName,
  onNotify,
}: {
  slug: string;
  locationName: string | null;
  onNotify?: Notify;
}) {
  const origin = window.location.origin;
  const tail = `${encodeURIComponent(slug)}/rate`;
  const url = `${origin}/locations/${tail}`;
  const [shareMode] = useState(canShareLink);
  const [copied, setCopied] = useState(false);
  const timerRef = useRef<number | null>(null);
  const urlRef = useRef<HTMLParagraphElement>(null);
  const { snackbar, showSnackbar, dismissSnackbar } = useSnackbar();
  const notify: Notify = onNotify ?? showSnackbar;

  useEffect(
    () => () => {
      if (timerRef.current !== null) window.clearTimeout(timerRef.current);
    },
    [],
  );

  const copy = async () => {
    if (!(await copyToClipboard(url))) {
      // Буфер закрыт (встроенный браузер без прав): выделяем адрес — дальше
      // человек скопирует его сам. «Выделена» говорим, только если выделение
      // и правда легло: на iPhone оно без жеста пользователя может не встать.
      const selected = selectNodeText(urlRef.current, url);
      notify({
        variant: "default",
        title: "Не получилось скопировать",
        message: selected
          ? "Ссылка выделена — скопируйте её вручную."
          : "Выделите ссылку и скопируйте её вручную.",
      });
      return;
    }
    setCopied(true);
    if (timerRef.current !== null) window.clearTimeout(timerRef.current);
    timerRef.current = window.setTimeout(() => setCopied(false), 2000);
    // Зачем ссылка, написано прямо над ней — здесь только факт и что дальше.
    notify({ variant: "default", title: "Ссылка скопирована", message: "Вставьте её в чат локации." });
  };

  const handleClick = async () => {
    if (shareMode) {
      try {
        // Имя локации у оргкабинета приезжает не сразу — без него текст общий.
        await navigator.share({
          title: locationName ? `Оцените старт: ${locationName}` : "Оцените свой старт",
          text: locationName
            ? `Были на старте «${locationName}»? Оцените его — по ссылке откроется ваш последний старт здесь.`
            : "Оцените свой старт — по ссылке откроется ваш последний старт на локации.",
          url,
        });
        return;
      } catch (error) {
        // Закрыл окно сам — молчим. Прочие отказы (нет прав, браузер не
        // принял данные) — копируем: ссылка нужнее канала.
        if (error instanceof DOMException && error.name === "AbortError") {
          return;
        }
      }
    }
    await copy();
  };

  // Обе подписи лежат в одной ячейке сетки, видна одна: ширина кнопки — по
  // длинной, и «Скопировано» не дёргает соседей.
  const faces = (idle: ReactNode, done: ReactNode) => (
    <span className="rate-share-faces">
      <span className={`rate-share-face${copied ? " is-off" : ""}`}>{idle}</span>
      <span className={`rate-share-face${copied ? "" : " is-off"}`} aria-hidden={!copied}>
        {done}
      </span>
    </span>
  );

  return (
    <div className="rate-share">
      {/* Адрес — текстом с переносом, а не полем ввода: в узкой карточке
          оргкабинета и на телефоне поле обрезало его многоточием раньше
          слага, и не было видно, на какую локацию ссылка (ревью 22.10.2026).
          Перенос — после «/locations/», чтобы слаг не рвался посередине. */}
      <p ref={urlRef} className="rate-share-url" onClick={() => selectNodeText(urlRef.current, url)}>
        {`${origin}/locations/`}
        <wbr />
        {tail}
      </p>
      <button type="button" className="btn primary rate-share-btn" onClick={() => void handleClick()}>
        {faces(shareMode ? "Поделиться" : "Скопировать ссылку", "Скопировано ✓")}
      </button>
      {/* Смену подписи на кнопке диктор не объявляет — объявит снекбар (role=status). */}
      {!onNotify && (
        <Snackbar open={snackbar.open} title={snackbar.title} variant={snackbar.variant} onDismiss={dismissSnackbar}>
          {snackbar.message}
        </Snackbar>
      )}
    </div>
  );
}
