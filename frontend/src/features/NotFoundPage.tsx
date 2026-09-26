import { useOptionalUser } from "../lib/useOptionalUser";
import { PORTAL_LOGIN_HREF } from "../lib/portalRoutes";
import { isLegacyGrafanaPath, LEGACY_SITE_LABEL, SITE_PUBLIC_HOME_HREF } from "../lib/siteBrand";
import { PortalSectionShell } from "./portal/PortalSectionShell";
import { SEARCH_ICON } from "./portal/nav/navIcons";
import { cabinetHref, RATING_GROUPS } from "./portal/nav/siteNav";
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
  locations: "локации",
  organizer: "оргкабинет",
  about: "о проекте",
  blog: "блог",
  updates: "обновления",
  backlog: "бэклог",
};

// Адреса «раздел/сущность/страница»: сущность (slug локации, хендл) — главное
// слово для поиска, её нельзя терять, даже если дальше в адресе опечатка.
const ENTITY_PREFIXES = new Set(["locations", "users", "organizer"]);

// Служебные начала адресов: сами по себе поиску не помогают.
const PREFIX_SEGMENTS = new Set(["locations", "users", "ratings", "organizer", "new", "d", "admin"]);

/** Расстояние Левенштейна — для опечаток в коротких частях адреса. */
function editDistance(a: string, b: string): number {
  if (Math.abs(a.length - b.length) > 2) return 3;
  let previous = Array.from({ length: b.length + 1 }, (_, index) => index);
  for (let i = 1; i <= a.length; i += 1) {
    const current = [i];
    for (let j = 1; j <= b.length; j += 1) {
      current[j] = Math.min(previous[j] + 1, current[j - 1] + 1, previous[j - 1] + (a[i - 1] === b[j - 1] ? 0 : 1));
    }
    previous = current;
  }
  return previous[b.length];
}

/** Похоже ли на известное слово: одна опечатка в коротком, две в длинном. */
function isTypoOf(segment: string, known: string): boolean {
  return editDistance(segment, known) <= (known.length >= 6 ? 2 : 1);
}

function humanize(segment: string): string {
  return segment.replace(/[-_.]+/g, " ").trim();
}

/** Слово известной части адреса, в том числе с опечаткой (weathr → погода). */
function knownWord(segment: string): string | null {
  if (SEGMENT_WORDS[segment]) return SEGMENT_WORDS[segment];
  const typo = Object.keys(SEGMENT_WORDS).find((key) => isTypoOf(segment, key));
  return typo ? SEGMENT_WORDS[typo] : null;
}

/**
 * /ratings/{что-то}: рейтинг с похожим адресом — его название (fastestt →
 * «самые быстрые», поиск найдёт страницу по имени), иначе все рейтинги.
 */
function ratingWords(segment: string): string {
  for (const group of RATING_GROUPS) {
    for (const item of group.items) {
      const slug = item.href.split("/").pop() ?? "";
      if (slug && (slug === segment || isTypoOf(segment, slug))) return item.label.toLowerCase();
    }
  }
  return "рейтинги";
}

/**
 * Слова для поиска из адреса страницы, которой нет. Под /locations/, /users/
 * и /organizer/ главное — сущность (опечатка в названии локации, ник) плюс
 * страница, если её удалось узнать: «sokolnki погода» (раньше при опечатке
 * в странице терялось и название локации, V6). Под /ratings/ — похожий
 * рейтинг. В остальных адресах — последняя часть, а если она известная
 * страница, то и часть перед ней. Пусто — подставлять нечего.
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
    .filter((segment) => !/^\d+$/.test(segment));
  const [head, entity, page] = segments;
  let query = "";
  if (head && entity && ENTITY_PREFIXES.has(head)) {
    const pageWord = page ? knownWord(page) : null;
    query = pageWord ? `${humanize(entity)} ${pageWord}` : humanize(entity);
  } else if (head === "ratings" && entity) {
    query = ratingWords(entity);
  } else {
    // Служебное начало в конце адреса оставляем, только если у него есть
    // слово: «/new/12345» искать нечего.
    const rest = segments.filter(
      (segment, index, all) => !(PREFIX_SEGMENTS.has(segment) && (index < all.length - 1 || !SEGMENT_WORDS[segment])),
    );
    const last = rest.at(-1);
    if (!last) return "";
    const lastWord = knownWord(last);
    const beforeLast = rest.at(-2);
    query = lastWord && beforeLast ? `${humanize(beforeLast)} ${lastWord}` : (lastWord ?? humanize(last));
  }
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
