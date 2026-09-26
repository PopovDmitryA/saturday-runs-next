import { useOptionalUser } from "../lib/useOptionalUser";
import { PORTAL_LOGIN_HREF } from "../lib/portalRoutes";
import { isLegacyGrafanaPath, LEGACY_SITE_LABEL, SITE_PUBLIC_HOME_HREF } from "../lib/siteBrand";
import { PortalSectionShell } from "./portal/PortalSectionShell";
import { SEARCH_ICON } from "./portal/nav/navIcons";
import { cabinetHref } from "./portal/nav/siteNav";
import { openSiteSearch } from "./portal/nav/siteSearchBus";
import "./NotFoundPage.css";

// Английские части адресов сайта → слова, которыми их ищут: /locations/sokolniki/weather
// превращается в «sokolniki погода» (локацию поиск найдёт и по slug).
const SEGMENT_WORDS: Record<string, string> = {
  weather: "погода",
  events: "протоколы",
  protocol: "протокол",
  tops: "топ",
  participants: "состав",
  runs: "пробежки",
  volunteering: "волонтёрство",
  achievements: "достижения",
  maps: "карта",
  map: "карта",
  history: "история",
  meetings: "встречи",
  "co-runners": "встречи",
  settings: "настройки",
  share: "поделиться",
  results: "итоги",
  ratings: "рейтинги",
  organizer: "оргкабинет",
};

// Разделы-префиксы: если после них в адресе что-то есть, само слово раздела
// поиску не помогает (/locations/sokolnki — ищем «sokolnki», а не «локации»).
const PREFIX_SEGMENTS = new Set(["locations", "users", "ratings", "organizer", "new", "d", "admin"]);

/**
 * Слова для поиска из адреса страницы, которой нет. Берём последнюю часть
 * адреса (обычно это опечатка в названии локации или ник), а если она —
 * известная страница локации, то и часть перед ней: «sokolniki погода».
 * Пусто — подставлять нечего.
 */
function searchWordsFromPath(path: string): string {
  const segments = path
    .split("/")
    .filter(Boolean)
    .map((segment) => {
      try {
        return decodeURIComponent(segment).toLowerCase();
      } catch {
        return segment.toLowerCase();
      }
    })
    .filter((segment, index, all) => !(PREFIX_SEGMENTS.has(segment) && index < all.length - 1))
    .filter((segment) => !/^\d+$/.test(segment));
  const words = (segment: string) => SEGMENT_WORDS[segment] ?? segment.replace(/[-_.]+/g, " ").trim();
  const last = segments.at(-1);
  if (!last) return "";
  const beforeLast = segments.at(-2);
  const query = SEGMENT_WORDS[last] && beforeLast ? `${words(beforeLast)} ${words(last)}` : words(last);
  return query.slice(0, 60);
}

/**
 * Страница 404. На неё приходят как раз потерявшиеся — по старой ссылке из
 * канала, с опечаткой, по удалённому профилю, — поэтому она в том же каркасе,
 * что весь сайт: шапка с поиском и разделами, нижняя панель на телефоне. И
 * сразу предлагает поиск по словам из адреса (a11y-9, code-5). Раньше тут
 * была старая шапка без навигации, а «В личный кабинет» вела на /dashboard.
 */
export function NotFoundPage() {
  const path = window.location.pathname;
  const user = useOptionalUser();
  // Адрес из эпохи Grafana на этом домене: объясняем, что старый сайт закрыт,
  // а не отправляем человека обратно на заглушку (см. legacyGrafanaTarget).
  const isLegacy = isLegacyGrafanaPath(path);
  const words = searchWordsFromPath(path);

  return (
    <PortalSectionShell>
      <section className="card not-found">
        {isLegacy ? (
          <>
            <h1 className="section-title">Старая статистика закрыта</h1>
            <p className="muted">
              <code>{path}</code> — адрес дашборда с прежнего сайта ({LEGACY_SITE_LABEL}). Он
              закрыт, всё переехало сюда, но прямого аналога именно у этого дашборда пока нет.
            </p>
            <div className="not-found-actions">
              <a href="/ratings" className="btn primary">
                Рейтинги
              </a>
              <a href="/locations" className="btn secondary">
                Локации
              </a>
              <a href={SITE_PUBLIC_HOME_HREF} className="btn secondary">
                На главную
              </a>
            </div>
            <p className="muted">
              Не хватает данных, которые были на старом дашборде? Напишите автору —{" "}
              <a href="https://t.me/Popov_Dmitry">@Popov_Dmitry</a>.
            </p>
          </>
        ) : (
          <>
            <h1 className="section-title">Страница не найдена</h1>
            <p className="muted">
              Адреса <code>{path}</code> на сайте нет: ссылка могла устареть или в ней опечатка.
              Попробуйте найти нужное поиском — по названию страницы, локации или по имени.
            </p>
            <div className="not-found-actions">
              <button type="button" className="btn primary not-found-search" onClick={() => openSiteSearch(words)}>
                <span className="not-found-search-icon">{SEARCH_ICON}</span>
                {words ? `Найти «${words}»` : "Найти на сайте"}
              </button>
              <a href={SITE_PUBLIC_HOME_HREF} className="btn secondary">
                На главную
              </a>
              {/* Пока сессия проверяется, ссылку не рисуем: гостю она вела бы
                  не туда. */}
              {user !== undefined && (
                <a href={user ? cabinetHref(user, "dashboard") : PORTAL_LOGIN_HREF} className="btn secondary">
                  {user ? "В кабинет" : "Войти"}
                </a>
              )}
            </div>
          </>
        )}
      </section>
    </PortalSectionShell>
  );
}
