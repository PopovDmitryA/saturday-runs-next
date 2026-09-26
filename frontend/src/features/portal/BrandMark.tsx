/**
 * Логотип run5k.run: имя с цветным «.run» и пульс-линия. Один компонент на
 * шапку и подвал — раньше подвал рисовал логотип своим серым текстом, и он
 * выглядел как чужой (замечание Дмитрия 26.09.2026).
 */
export function BrandMark() {
  return (
    <span className="portal-brand-row">
      <span className="portal-brand-name">
        run5k<span className="portal-brand-tld">.run</span>
      </span>
      <svg className="portal-brand-pulse" viewBox="0 0 34 14" fill="none" aria-hidden="true">
        <polyline
          points="1,12 8,10 14,11 20,6 26,7 32,2"
          strokeWidth="2"
          strokeLinecap="round"
          strokeLinejoin="round"
        />
        <circle cx="32" cy="2" r="2.4" />
      </svg>
    </span>
  );
}
