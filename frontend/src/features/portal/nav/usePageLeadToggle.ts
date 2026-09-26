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
 * Сворачивает CSS (siteNavMobile.css, те же селекторы), здесь — только тап.
 * Отметка о раскрытии — data-атрибут на самом абзаце: React его не трогает,
 * а страница перерисовывается заново при каждом переходе.
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

export function usePageLeadToggle(): void {
  useEffect(() => {
    const media = window.matchMedia(PHONE_QUERY);
    const onClick = (event: MouseEvent) => {
      if (!media.matches || event.defaultPrevented) return;
      const target = event.target as Element | null;
      // Ссылки и кнопки внутри описания работают как обычно.
      if (!target || target.closest("a, button, input, select, textarea, label, summary")) return;
      const lead = target.closest<HTMLElement>(PAGE_LEAD_SELECTOR);
      if (!lead) return;
      if (lead.dataset.leadOpen) {
        delete lead.dataset.leadOpen;
        return;
      }
      // Раскрываем, только если текст и правда обрезан.
      if (lead.scrollHeight > lead.clientHeight + 1) {
        lead.dataset.leadOpen = "1";
      }
    };
    document.addEventListener("click", onClick);
    return () => document.removeEventListener("click", onClick);
  }, []);
}
