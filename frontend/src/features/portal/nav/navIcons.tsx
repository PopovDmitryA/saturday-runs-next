/**
 * Иконки навигации сайта — инлайн-SVG в stroke-стиле (как в шапке портала),
 * рисуются в квадрате 24×24 и масштабируются контейнером.
 *
 * Иконки есть только у разделов (рельс, нижняя панель телефона) и у вкладок
 * кабинета: подразделы в колонке и чипы — текстовые, иначе колонка из 14
 * рейтингов превращалась в ряд одинаковых значков.
 */
import type { ReactNode } from "react";

export function icon(paths: ReactNode) {
  return (
    <svg
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.8"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      {paths}
    </svg>
  );
}

export const ME_ICON = icon(
  <>
    <circle cx="12" cy="8" r="3.6" />
    <path d="M5 20c.8-3.6 3.6-5.7 7-5.7s6.2 2.1 7 5.7" />
  </>,
);

// Кабинет организатора — планшет оргкоманды.
export const ORGANIZER_ICON = icon(
  <>
    <path d="M9 4.5h6M8 6.5h8a1 1 0 0 1 1 1V20a.5.5 0 0 1-.5.5h-9A.5.5 0 0 1 7 20V7.5a1 1 0 0 1 1-1Z" />
    <path d="M10 11h4M10 14.5h4" />
  </>,
);

// «Результаты»: секундомер — свежие результаты стартов.
export const RESULTS_ICON = icon(
  <>
    <circle cx="12" cy="13" r="7" />
    <path d="M12 9.5V13l2.5 2" />
    <path d="M10 3h4M12 3v3" />
  </>,
);

export const LOCATIONS_ICON = icon(
  <>
    <path d="M12 21s-7-5.6-7-11a7 7 0 0 1 14 0c0 5.4-7 11-7 11Z" />
    <circle cx="12" cy="10" r="2.6" />
  </>,
);

export const RATINGS_ICON = icon(
  <>
    <path d="M7.5 4h9v4.5a4.5 4.5 0 0 1-9 0V4Z" />
    <path d="M7.5 5.5H5a3 3 0 0 0 2.8 3.9M16.5 5.5H19a3 3 0 0 1-2.8 3.9" />
    <path d="M12 13v3.5M8.5 21h7M10 21l.7-4.5h2.6l.7 4.5" />
  </>,
);

// «О проекте»: блог, обновления, бэклог — раскрытая книжка.
export const PROJECT_ICON = icon(
  <>
    <path d="M12 6.5c-1.8-1.3-4.3-2-7.5-2v13c3.2 0 5.7.7 7.5 2 1.8-1.3 4.3-2 7.5-2v-13c-3.2 0-5.7.7-7.5 2Z" />
    <path d="M12 6.5v13" />
  </>,
);

export const SEARCH_ICON = icon(
  <>
    <circle cx="11" cy="11" r="6.5" />
    <path d="m20 20-4.2-4.2" />
  </>,
);

export const MENU_ICON = icon(<path d="M4 7h16M4 12h16M4 17h16" />);

export const CLOSE_ICON = icon(<path d="M6 6l12 12M18 6 6 18" />);

export const CHEVRON_RIGHT_ICON = icon(<path d="M9 6l6 6-6 6" />);
export const CHEVRON_LEFT_ICON = icon(<path d="M15 6l-6 6 6 6" />);
export const CHEVRON_DOWN_ICON = icon(<path d="M6 9l6 6 6-6" />);

// Вкладки личного кабинета (как были в сайдбаре — узнаваемость важнее новизны).
export const CABINET_ICONS = {
  dashboard: icon(
    <>
      <rect x="3" y="3" width="7.5" height="9" rx="1.6" />
      <rect x="13.5" y="3" width="7.5" height="5.5" rx="1.6" />
      <rect x="13.5" y="12" width="7.5" height="9" rx="1.6" />
      <rect x="3" y="15.5" width="7.5" height="5.5" rx="1.6" />
    </>,
  ),
  runs: icon(<polyline points="3,17 8,12 11,15 16,8 18.5,10.5 21,5" />),
  volunteering: icon(
    <path d="M12 20.5c-4.6-3.4-8-6.3-8-9.9C4 7.9 6 6 8.4 6c1.5 0 2.8.8 3.6 2 .8-1.2 2.1-2 3.6-2C18 6 20 7.9 20 10.6c0 3.6-3.4 6.5-8 9.9Z" />,
  ),
  meetings: icon(
    <>
      <circle cx="8.5" cy="8.5" r="3.2" />
      <path d="M2.8 20c.6-3 2.9-4.8 5.7-4.8s5.1 1.8 5.7 4.8" />
      <circle cx="16.8" cy="9.8" r="2.6" />
      <path d="M15.2 15.6c.5-.2 1-.3 1.6-.3 2.4 0 4.3 1.5 4.9 4" />
    </>,
  ),
  achievements: icon(
    <>
      <circle cx="12" cy="9" r="5.2" />
      <path d="M8.8 13.4 7 21l5-2.6L17 21l-1.8-7.6" />
    </>,
  ),
  history: icon(
    <>
      <circle cx="12" cy="12" r="8.5" />
      <polyline points="12,7 12,12 15.5,14" />
    </>,
  ),
  map: icon(
    <>
      <path d="M9 4 3.5 6v14L9 18l6 2 5.5-2V4L15 6 9 4Z" />
      <path d="M9 4v14M15 6v14" />
    </>,
  ),
  share: icon(
    <>
      <circle cx="6" cy="12" r="2.6" />
      <circle cx="17.5" cy="5.5" r="2.6" />
      <circle cx="17.5" cy="18.5" r="2.6" />
      <path d="M8.4 10.7 15.1 6.8M8.4 13.3l6.7 3.9" />
    </>,
  ),
} as const;

// Чужой профиль без аватарки: человечек с галочкой.
export const PROFILE_ICON = icon(
  <>
    <circle cx="12" cy="8" r="3.4" />
    <path d="M5.5 20a6.8 6.8 0 0 1 13 0" />
    <path d="M17.5 3.5 19 5l2.5-2.5" />
  </>,
);
