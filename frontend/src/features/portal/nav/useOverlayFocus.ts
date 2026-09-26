/**
 * Фокус в окнах навигации телефона: «Меню», список страниц раздела,
 * выпадающий список полосы.
 *
 * Ревью 25.09.2026 (a11y-2): после нажатия «Меню» фокус оставался на кнопке,
 * Tab уходил в шапку за затемнением, а диктор читал пункты шторки последними —
 * в DOM они стоят раньше кнопки. Теперь при открытии фокус переходит внутрь
 * окна, Tab ходит по кругу внутри него (и кнопок, которые окно закрывают),
 * Esc закрывает, а после закрытия фокус возвращается на кнопку, которой окно
 * открыли.
 */
import { useEffect, useRef, type RefObject } from "react";

const FOCUSABLE =
  'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';

function focusables(containers: (HTMLElement | null)[]): HTMLElement[] {
  const out: HTMLElement[] = [];
  for (const container of containers) {
    if (!container) continue;
    for (const element of container.querySelectorAll<HTMLElement>(FOCUSABLE)) {
      // Скрытые (display:none, свёрнутые группы) фокус не получают.
      if (element.offsetParent !== null || element.getClientRects().length > 0) {
        out.push(element);
      }
    }
  }
  return out;
}

export function useOverlayFocus({
  open,
  containers,
  initial,
  trigger,
  onEscape,
}: {
  open: boolean;
  /** Где ходит Tab: само окно и, если надо, кнопки вне его (нижняя панель). */
  containers: RefObject<HTMLElement | null>[];
  /** Куда поставить фокус при открытии; по умолчанию — первый пункт окна. */
  initial?: () => HTMLElement | null;
  /** Куда вернуть фокус после закрытия; по умолчанию — туда, где он был. */
  trigger?: RefObject<HTMLElement | null>;
  onEscape: () => void;
}): void {
  const escapeRef = useRef(onEscape);
  const initialRef = useRef(initial);
  const containersRef = useRef(containers);
  useEffect(() => {
    escapeRef.current = onEscape;
    initialRef.current = initial;
    containersRef.current = containers;
  });

  useEffect(() => {
    if (!open) return;
    const previous = document.activeElement as HTMLElement | null;
    const boxes = () => containersRef.current.map((ref) => ref.current);

    // Окно дорисовывается в этом же коммите — ставим фокус кадром позже, чтобы
    // раскрытые группы и прокрутка списка уже были на месте.
    const frame = requestAnimationFrame(() => {
      const target = initialRef.current?.() ?? focusables([boxes()[0]])[0] ?? null;
      target?.focus({ preventScroll: false });
    });

    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        event.preventDefault();
        escapeRef.current();
        return;
      }
      if (event.key !== "Tab") return;
      const items = focusables(boxes());
      if (items.length === 0) return;
      // Порядок обхода ведём сами — по контейнерам, а внутри них по разметке:
      // окно и кнопки панели лежат в разных местах DOM, и браузерный Tab
      // между ними ушёл бы в адресную строку.
      event.preventDefault();
      const index = items.indexOf(document.activeElement as HTMLElement);
      const step = event.shiftKey ? -1 : 1;
      const next = index === -1 ? (event.shiftKey ? items.length - 1 : 0) : (index + step + items.length) % items.length;
      items[next].focus();
    };
    document.addEventListener("keydown", onKey);

    return () => {
      cancelAnimationFrame(frame);
      document.removeEventListener("keydown", onKey);
      // Возвращаем фокус, только если он остался «ничьим» (окно исчезло вместе
      // с ним) или внутри окна. Ушли на другую страницу — кнопки уже нет.
      const back = trigger?.current ?? previous;
      const now = document.activeElement;
      const orphaned = now == null || now === document.body || boxes().some((box) => box?.contains(now));
      if (back && back.isConnected && orphaned) {
        back.focus({ preventScroll: true });
      }
    };
    // trigger — ref, его содержимое читаем при закрытии.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);
}
