/**
 * Описание под заголовком страницы на телефоне: две строки с многоточием, тап
 * раскрывает его целиком, второй тап сворачивает (идея Б, решение Дмитрия
 * 26.09.2026).
 *
 * На первом экране телефона описание в три-четыре строки отодвигало таблицу
 * за край: на /ratings/fastest при 390px не было видно ни одной строки
 * рейтинга, в «Пробежках» кабинета первая строка начиналась на 590px из 740
 * (ревью, mob-6). Описание нужно один раз — тому, кто пришёл впервые; ему
 * хватит двух строк и тапа, остальным оно не мешает.
 *
 * Сворачивает CSS (siteNavMobile.css, те же селекторы), здесь — тап и
 * клавиатура. Кнопкой описание становится, только когда текст и правда
 * обрезан (метка data-lead-clamped): тогда у него курсор-рука, место в обходе
 * Tab, role=button и aria-expanded, Enter и пробел раскрывают его так же, как
 * тап. Короткие описания («Москва · Россия») остаются обычным текстом —
 * раньше рука висела и над ними, а тап ничего не делал (проверка 26.09.2026).
 *
 * Метки — data-атрибуты на самом абзаце: React их не трогает, а страница
 * перерисовывается заново при каждом переходе.
 */
import { useEffect } from "react";

/** Описания под заголовком: кабинет, рейтинги, локации и организатор. */
export const PAGE_LEAD_SELECTOR = [
  ".portal-cab-pagehead-sub",
  ".lb-header > .lb-description",
  ".loc-header > p.muted:not(.loc-header-breadcrumb)",
  ".loc-header > .loc-header-lead",
  ".loc-header > .protocol-subtitle",
].join(", ");

const PHONE_QUERY = "(max-width: 900px)";
/** Внутри описания бывают ссылки — им клики и клавиши оставляем. */
const INTERACTIVE = "a, button, input, select, textarea, label, summary";

function isOpen(lead: HTMLElement): boolean {
  return lead.dataset.leadOpen != null;
}

/** Сделать описание кнопкой-раскрывашкой или вернуть обычным текстом. */
function syncLead(lead: HTMLElement, phone: boolean): void {
  // Раскрытое описание не обрезано, но кнопкой остаётся — чтобы свернуть.
  const clamped = phone && (isOpen(lead) || lead.scrollHeight > lead.clientHeight + 1);
  if (clamped) {
    if (lead.dataset.leadClamped == null) {
      lead.dataset.leadClamped = "1";
      lead.tabIndex = 0;
      // Со ссылками внутри описание кнопкой не называем: кнопка со ссылками
      // внутри сбивает экранных дикторов. Клавиши при этом работают.
      if (!lead.querySelector(INTERACTIVE)) lead.setAttribute("role", "button");
    }
    lead.setAttribute("aria-expanded", isOpen(lead) ? "true" : "false");
    return;
  }
  if (lead.dataset.leadClamped != null) {
    delete lead.dataset.leadClamped;
    lead.removeAttribute("tabindex");
    lead.removeAttribute("role");
    lead.removeAttribute("aria-expanded");
  }
  if (!phone) delete lead.dataset.leadOpen;
}

function toggleLead(lead: HTMLElement): void {
  if (isOpen(lead)) {
    delete lead.dataset.leadOpen;
  } else if (lead.dataset.leadClamped != null) {
    lead.dataset.leadOpen = "1";
  }
  if (lead.dataset.leadClamped != null) {
    lead.setAttribute("aria-expanded", isOpen(lead) ? "true" : "false");
  }
}

/**
 * Слушатели ставятся один раз на документ, сколько бы каркасов ни позвали
 * хук: два обработчика клика раскрывали бы и тут же сворачивали описание.
 */
let users = 0;
let teardown: (() => void) | null = null;

export function usePageLeadToggle(): void {
  useEffect(() => {
    users += 1;
    if (users === 1) teardown = install();
    return () => {
      users -= 1;
      if (users === 0) {
        teardown?.();
        teardown = null;
      }
    };
  }, []);
}

function install(): () => void {
  const media = window.matchMedia(PHONE_QUERY);

  // Обрезан ли текст, видно только после отрисовки, а описания приезжают
  // вместе с данными страницы. Пересчитываем по изменениям разметки и
  // ширины — не чаще раза в кадр; поиск — по пяти селекторам, это дёшево.
  let frame = 0;
  const scan = () => {
    frame = 0;
    const phone = media.matches;
    for (const lead of document.querySelectorAll<HTMLElement>(PAGE_LEAD_SELECTOR)) {
      syncLead(lead, phone);
    }
  };
  const schedule = () => {
    if (!frame) frame = requestAnimationFrame(scan);
  };
  schedule();
  const observer = new MutationObserver(schedule);
  observer.observe(document.body, { childList: true, subtree: true, characterData: true });
  window.addEventListener("resize", schedule);
  media.addEventListener("change", schedule);

  const leadOf = (event: Event): HTMLElement | null => {
    if (!media.matches || event.defaultPrevented) return null;
    const target = event.target as Element | null;
    // Ссылки и кнопки внутри описания работают как обычно.
    if (!target || target.closest(INTERACTIVE)) return null;
    return target.closest<HTMLElement>(PAGE_LEAD_SELECTOR);
  };

  const onClick = (event: MouseEvent) => {
    const lead = leadOf(event);
    if (!lead) return;
    // Разметка могла смениться после последнего кадра — сверяемся сейчас.
    syncLead(lead, true);
    toggleLead(lead);
  };

  const onKey = (event: KeyboardEvent) => {
    if (event.key !== "Enter" && event.key !== " ") return;
    const lead = leadOf(event);
    // Только с самого описания: Enter на ссылке внутри — переход по ссылке.
    if (!lead || event.target !== lead || lead.dataset.leadClamped == null) return;
    event.preventDefault();
    toggleLead(lead);
  };

  document.addEventListener("click", onClick);
  document.addEventListener("keydown", onKey);
  return () => {
    cancelAnimationFrame(frame);
    observer.disconnect();
    window.removeEventListener("resize", schedule);
    media.removeEventListener("change", schedule);
    document.removeEventListener("click", onClick);
    document.removeEventListener("keydown", onKey);
  };
}
