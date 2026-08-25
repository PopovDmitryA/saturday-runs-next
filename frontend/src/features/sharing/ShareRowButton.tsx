// Компактная кнопка «Поделиться» для строк таблиц (пробежки, волонтёрства):
// иконка рядом со звездой оценки, открывает шторку с сюжетом этой строки.

import { useOptionalShareSheet } from "./ShareSheetContext";
import type { ShareEntryPoint, ShareSubject } from "./types";

export function ShareRowIcon() {
  return (
    <svg
      viewBox="0 0 24 24"
      width="16"
      height="16"
      aria-hidden="true"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      <circle cx="18" cy="5" r="3" />
      <circle cx="6" cy="12" r="3" />
      <circle cx="18" cy="19" r="3" />
      <line x1="8.6" y1="13.5" x2="15.4" y2="17.5" />
      <line x1="15.4" y1="6.5" x2="8.6" y2="10.5" />
    </svg>
  );
}

export function ShareRowButton({
  subject,
  entry,
}: {
  /** null — сюжет для строки не собрался, кнопка не рисуется. */
  subject: ShareSubject | null;
  entry: ShareEntryPoint;
}) {
  const sheet = useOptionalShareSheet();
  if (sheet === null || subject === null) {
    return null;
  }
  return (
    <button
      type="button"
      className="s2-row-share"
      title="Поделиться"
      aria-label="Поделиться"
      onClick={() => sheet.open({ subject, entry })}
    >
      <ShareRowIcon />
    </button>
  );
}
