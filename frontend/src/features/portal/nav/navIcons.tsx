/**
 * Иконки навигации сайта — инлайн-SVG в stroke-стиле (как в шапке портала),
 * рисуются в квадрате 24×24 и масштабируются контейнером.
 *
 * Иконки есть у разделов (рельс, нижняя панель телефона) и у каждой страницы
 * раздела: колонка, «Меню», переключатель на телефоне и выдача поиска рисуют
 * одну и ту же картинку, так страница узнаётся в любом из этих мест.
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

// ---------- иконки подразделов (колонка, «Меню», переключатель, поиск) ----------
// Пункт колонки без иконки читался сплошной стеной текста (замечание Дмитрия
// 23.09.2026) — у каждой страницы своя картинка того, что на ней лежит.

export const SETTINGS_ICON = icon(
  <>
    <circle cx="12" cy="12" r="3" />
    <path d="M12 2.8v2.4M12 18.8v2.4M4.9 4.9l1.7 1.7M17.4 17.4l1.7 1.7M2.8 12h2.4M18.8 12h2.4M4.9 19.1l1.7-1.7M17.4 6.6l1.7-1.7" />
  </>,
);

export const GRID_ICON = CABINET_ICONS.dashboard;

// Свод по пробежке: планшет со списком.
export const REPORT_ICON = icon(
  <>
    <path d="M9 4.5h6M8 6.5h8a1 1 0 0 1 1 1V20a.5.5 0 0 1-.5.5h-9A.5.5 0 0 1 7 20V7.5a1 1 0 0 1 1-1Z" />
    <path d="M9.8 11h.01M12 11h2.5M9.8 14.5h.01M12 14.5h2.5" />
  </>,
);

// Пост в канал: рупор.
export const POST_ICON = icon(
  <>
    <path d="M4 10v4a1 1 0 0 0 1 1h2l6 4V5L7 9H5a1 1 0 0 0-1 1Z" />
    <path d="M17 9a4 4 0 0 1 0 6M19.5 6.5a7.5 7.5 0 0 1 0 11" />
  </>,
);

// Протокол / журнал протоколов: лист с загнутым углом.
export const PROTOCOL_ICON = icon(
  <>
    <path d="M6 3.5h9L19 7.5V20a.5.5 0 0 1-.5.5h-12A.5.5 0 0 1 6 20V4a.5.5 0 0 1 .5-.5Z" />
    <path d="M14.5 3.5V8H19" />
    <path d="M9 12.5h6M9 16h4" />
  </>,
);

// Скорость выгрузки протоколов: часы с галочкой.
export const PROTOCOL_SPEED_ICON = icon(
  <>
    <circle cx="11" cy="12" r="7.5" />
    <path d="M11 8v4l2.5 1.5" />
    <path d="m16.5 18 1.8 1.8 3.2-3.2" />
  </>,
);

// Календарь юбилеев: календарь со звездой.
export const MILESTONES_ICON = icon(
  <>
    <rect x="3.5" y="5" width="17" height="15.5" rx="2" />
    <path d="M3.5 9.5h17M8 3v4M16 3v4" />
    <path d="m12 12.2.9 1.8 2 .3-1.45 1.4.35 2-1.8-.95-1.8.95.35-2-1.45-1.4 2-.3Z" />
  </>,
);

// Новички: человечек с плюсом.
export const NEWCOMERS_ICON = icon(
  <>
    <circle cx="10" cy="8" r="3.4" />
    <path d="M3.5 20a6.6 6.6 0 0 1 13 0" />
    <path d="M19 8v6M16 11h6" />
  </>,
);

// Долгая пауза: песочные часы.
export const PAUSE_ICON = icon(
  <>
    <path d="M6.5 3.5h11M6.5 20.5h11" />
    <path d="M7.5 3.5c0 4 4.5 5.5 4.5 8.5s-4.5 4.5-4.5 8.5M16.5 3.5c0 4-4.5 5.5-4.5 8.5s4.5 4.5 4.5 8.5" />
  </>,
);

// Портрет участника: круговая диаграмма.
export const AUDIENCE_ICON = icon(
  <>
    <path d="M12 3.5a8.5 8.5 0 1 0 8.5 8.5H12Z" />
    <path d="M15 3.9A8.5 8.5 0 0 1 20.1 9H15Z" />
  </>,
);

// Волонтёрская скамейка: скамейка.
export const BENCH_ICON = icon(
  <>
    <path d="M4 10h16M4 14h16" />
    <path d="M6 14v5M18 14v5M6 10V7M18 10V7" />
  </>,
);

// Команда и нагрузка: трое.
export const TEAM_ICON = icon(
  <>
    <circle cx="12" cy="7.5" r="2.8" />
    <path d="M7 19.5a5 5 0 0 1 10 0" />
    <circle cx="5.2" cy="10" r="2" />
    <circle cx="18.8" cy="10" r="2" />
    <path d="M2 18a3.6 3.6 0 0 1 4.3-3.2M22 18a3.6 3.6 0 0 0-4.3-3.2" />
  </>,
);

// Посещаемость: столбики.
export const ATTENDANCE_ICON = icon(
  <>
    <path d="M4 20.5h16" />
    <path d="M6.5 17v-5M10.5 17V7M14.5 17v-8M18.5 17v-3" />
  </>,
);

// Мы и соседи: весы.
export const BENCHMARK_ICON = icon(
  <>
    <path d="M12 4v16M8 20h8M5 7h14" />
    <path d="m5 7-2.5 6a2.5 2.5 0 0 0 5 0Z" />
    <path d="m19 7-2.5 6a2.5 2.5 0 0 0 5 0Z" />
  </>,
);

// Единый протокол: список с точками.
export const UNIFIED_PROTOCOL_ICON = icon(
  <>
    <path d="M9 6.5h11M9 12h11M9 17.5h11" />
    <circle cx="5" cy="6.5" r="1.3" />
    <circle cx="5" cy="12" r="1.3" />
    <circle cx="5" cy="17.5" r="1.3" />
  </>,
);

// Каталог локаций: несколько меток.
export const CATALOG_ICON = icon(
  <>
    <path d="M8 13.5s-4.5-3.6-4.5-7a4.5 4.5 0 0 1 9 0c0 3.4-4.5 7-4.5 7Z" />
    <circle cx="8" cy="6.5" r="1.5" />
    <path d="M16.5 21s-4-3.2-4-6.2a4 4 0 0 1 8 0c0 3-4 6.2-4 6.2Z" />
    <circle cx="16.5" cy="14.8" r="1.3" />
  </>,
);

// Главная страница локации: прицел-метка.
export const PLACE_ICON = icon(
  <>
    <circle cx="12" cy="12" r="3.2" />
    <circle cx="12" cy="12" r="8" />
    <path d="M12 2.5v2M12 19.5v2M2.5 12h2M19.5 12h2" />
  </>,
);

export const REGULARS_ICON = icon(
  <>
    <circle cx="9.5" cy="8.5" r="3" />
    <path d="M3.5 19.5c.7-3.1 3.1-4.9 6-4.9s5.3 1.8 6 4.9" />
    <circle cx="17.5" cy="7.5" r="2.2" />
    <path d="M16 13.6c2.3-.4 4.1 1.2 4.5 3.6" />
  </>,
);

export const PODIUM_ICON = icon(
  <>
    <path d="M9.5 11.5h5V20h-5z" />
    <path d="M4 15h5.5v5H4zM14.5 13.5H20V20h-5.5z" />
    <path d="M12 4l1.1 2.3 2.4.3-1.8 1.7.5 2.4L12 9.6 9.8 10.7l.5-2.4L8.5 6.6l2.4-.3z" />
  </>,
);

export const WEATHER_ICON = icon(
  <path d="M7 18.5a4 4 0 0 1-.6-7.95A5.5 5.5 0 0 1 17 9.2a4.7 4.7 0 0 1 .5 9.3Z" />,
);

// Рейтинг пробежек — та же линия, что у «Пробежек» в кабинете.
export const RUNS_RATING_ICON = CABINET_ICONS.runs;

// Первые места: медаль.
export const MEDAL_ICON = icon(
  <>
    <path d="M8 3h8l-2.5 6h-3Z" />
    <circle cx="12" cy="15" r="5.5" />
    <path d="M12 12.5v5" />
  </>,
);

export const BOLT_ICON = icon(<path d="M13 3 5 13.5h6l-1 7.5 8-10.5h-6Z" />);

export const VOLUNTEER_ICON = CABINET_ICONS.volunteering;

// Волонтёрство на разных локациях: сердце с меткой.
export const VOLUNTEER_PLACES_ICON = icon(
  <>
    <path d="M10 18c-3.8-2.8-6.5-5.1-6.5-8.1C3.5 7.7 5.1 6 7.1 6c1.2 0 2.3.6 2.9 1.6.6-1 1.7-1.6 2.9-1.6 2 0 3.6 1.7 3.6 3.9" />
    <path d="M18 21s-3.5-2.8-3.5-5.5a3.5 3.5 0 0 1 7 0C21.5 18.2 18 21 18 21Z" />
  </>,
);

// Разнообразие ролей: четыре фигуры.
export const ROLES_ICON = icon(
  <>
    <rect x="4" y="4" width="6.5" height="6.5" rx="1.2" />
    <circle cx="16.75" cy="7.25" r="3.25" />
    <path d="m7.25 13.5 3.5 6.5h-7Z" />
    <path d="m16.75 13.5 3.25 3.25-3.25 3.25-3.25-3.25Z" />
  </>,
);

// Уникальные локации (туризм): глобус.
export const GLOBE_ICON = icon(
  <>
    <circle cx="12" cy="12" r="8.5" />
    <path d="M3.5 12h17M12 3.5c2.4 2.4 3.5 5.2 3.5 8.5s-1.1 6.1-3.5 8.5c-2.4-2.4-3.5-5.2-3.5-8.5s1.1-6.1 3.5-8.5Z" />
  </>,
);

// Открытия локаций: флаг.
export const FLAG_ICON = icon(
  <>
    <path d="M5.5 21V4" />
    <path d="M5.5 4.5h11l-2 4 2 4h-11" />
  </>,
);

// Локации с первым местом: корона.
export const CROWN_ICON = icon(
  <>
    <path d="M4 8.5 7.5 12 12 5.5 16.5 12 20 8.5 18.5 18h-13Z" />
    <path d="M5.5 21h13" />
  </>,
);

// Дальность от дома: домик и маршрут.
export const DISTANCE_ICON = icon(
  <>
    <path d="M3.5 10.5 7.5 7l4 3.5V15h-8Z" />
    <circle cx="18" cy="17.5" r="2" />
    <path d="M7.5 15v2.5a2 2 0 0 0 2 2H16" strokeDasharray="1.5 2" />
  </>,
);

// Рекорды локаций: награда со звездой.
export const RECORD_ICON = icon(
  <>
    <circle cx="12" cy="9.5" r="5.5" />
    <path d="m12 7 .8 1.6 1.7.25-1.25 1.2.3 1.7-1.55-.8-1.55.8.3-1.7-1.25-1.2 1.7-.25Z" />
    <path d="m8.5 14-1.5 7 5-2.5 5 2.5-1.5-7" />
  </>,
);

// Регионы: сложенная карта.
export const REGIONS_ICON = CABINET_ICONS.map;

export const INFO_ICON = icon(
  <>
    <circle cx="12" cy="12" r="8.5" />
    <path d="M12 11v5.5M12 7.8v.01" />
  </>,
);

// Блог: газета.
export const BLOG_ICON = icon(
  <>
    <path d="M4 5.5h12.5v13a2 2 0 0 0 2 2H6a2 2 0 0 1-2-2Z" />
    <path d="M16.5 9.5H20v9a2 2 0 0 1-2 2" />
    <path d="M7.5 9h5.5M7.5 12.5h5.5M7.5 16h3.5" />
  </>,
);

// Обновления: искры.
export const SPARKLES_ICON = icon(
  <>
    <path d="M10 4.5 11.6 9 16 10.5 11.6 12 10 16.5 8.4 12 4 10.5 8.4 9Z" />
    <path d="M17.5 14.5 18.3 16.7 20.5 17.5 18.3 18.3 17.5 20.5 16.7 18.3 14.5 17.5 16.7 16.7Z" />
  </>,
);

// Бэклог идей: лампочка.
export const IDEA_ICON = icon(
  <>
    <path d="M9 17.5h6M10 20.5h4" />
    <path d="M12 3.5a6 6 0 0 0-3.6 10.8c.6.5 1 1.3 1 2.2v1h5.2v-1c0-.9.4-1.7 1-2.2A6 6 0 0 0 12 3.5Z" />
  </>,
);

// Админка: щит.
export const SHIELD_ICON = icon(<path d="M12 3.5 5 6v5.5c0 4.3 3 7.6 7 9 4-1.4 7-4.7 7-9V6Z" />);
