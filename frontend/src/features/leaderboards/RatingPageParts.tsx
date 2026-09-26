import { useId, useState, type ReactNode } from "react";
import { RATINGS_HUB_HREF } from "../portal/nav/siteNav";
import { ratingName, type RatingKey, type RatingName } from "./ratingNames";

/**
 * Хлебные крошки страницы рейтинга: «← Все рейтинги / Группа · Рейтинг».
 * Слова — из дерева навигации (см. ratingNames.ts), поэтому крошка всегда
 * называет рейтинг так же, как пункт меню, по которому сюда пришли.
 */
export function RatingBreadcrumb({ ratingKey, fallback }: { ratingKey: RatingKey; fallback?: RatingName }) {
  const name = ratingName(ratingKey, fallback);
  return (
    <nav className="lb-breadcrumb" aria-label="Хлебные крошки">
      <a href={RATINGS_HUB_HREF}>← Все рейтинги</a>
      <span aria-hidden> / </span>
      <span>{name.group ? `${name.group} · ${name.label}` : name.label}</span>
    </nav>
  );
}

function FunnelIcon() {
  return (
    <svg
      className="lb-filters-toggle-icon"
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
      <path d="M3 5h18l-7 8.5V19l-4 2v-7.5z" />
    </svg>
  );
}

/**
 * Панель фильтров рейтинга, которая на телефоне свёрнута в одну кнопку
 * «Фильтры · N» (идея Б ревью навигации 25.09.2026).
 *
 * На 360×740 открытая панель («Вид», «Система», «Колонки», «Поиск») занимала
 * целый экран, и первую строку таблицы человек видел только после прокрутки,
 * хотя пришёл за таблицей. N — сколько фильтров отличаются от значений по
 * умолчанию: по ней видно, что таблица уже отфильтрована, не раскрывая панель.
 *
 * На компьютере кнопки нет, и панель стоит как раньше — места там хватает.
 * Переключение — чистым CSS (медиазапрос в leaderboards.css), без замера окна
 * в JS: страница не моргает раскрытой панелью до первого замера, а поле поиска
 * и прочие контролы не перемонтируются при повороте телефона.
 */
export function RatingFilters({ activeCount, children }: { activeCount: number; children: ReactNode }) {
  const [open, setOpen] = useState(false);
  const bodyId = useId();
  return (
    <div className={`lb-filters-disclosure${open ? " lb-filters-open" : ""}`}>
      <button
        type="button"
        className="lb-filters-toggle"
        aria-expanded={open}
        aria-controls={bodyId}
        onClick={() => setOpen((value) => !value)}
      >
        <FunnelIcon />
        <span className="lb-filters-toggle-label">
          Фильтры
          {activeCount > 0 && (
            <span className="lb-filters-toggle-count" aria-hidden="true">
              {" "}· {activeCount}
            </span>
          )}
        </span>
        {activeCount > 0 && (
          <span className="visually-hidden">
            {activeCount === 1 ? "(изменён 1 фильтр)" : `(изменено фильтров: ${activeCount})`}
          </span>
        )}
        <span className="lb-filters-toggle-chevron" aria-hidden="true">
          ▾
        </span>
      </button>
      <div id={bodyId} className="lb-filters-body">
        {children}
      </div>
    </div>
  );
}
