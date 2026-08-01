import { StatHintTooltip } from "../StatHintTooltip";

/**
 * Точечный «?» рядом с сокращённым заголовком колонки: ховер/фокус на десктопе,
 * тап на мобильном — всплывает расшифровка. Постоянного места не занимает.
 */
export function HeaderHint({ text }: { text: string }) {
  return (
    <StatHintTooltip text={text} className="th-hint">
      <span aria-hidden="true">?</span>
    </StatHintTooltip>
  );
}
