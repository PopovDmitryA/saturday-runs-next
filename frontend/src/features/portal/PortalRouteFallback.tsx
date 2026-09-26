import { useState } from "react";
import { useOptionalUser } from "../../lib/useOptionalUser";
import { PortalHeader } from "./PortalHeader";
import { SiteSidebar } from "./SiteSidebar";
import { SectionSubnav } from "./nav/SectionSubnav";
import "./portal.css";
import "./portalSection.css";

/**
 * Пока подгружается код раздела (Suspense в App.tsx), на экране — «Загрузка…»
 * в каркасе сайта. Если у страницы, с которой человек уходит, была навигация
 * слева, каркас тот же: рельс, колонка раздела, куда идёт переход, и полоса
 * страниц над содержимым. Раньше заглушка была без рельса — на время
 * загрузки он пропадал, а в шапке вместо него появлялись ссылки разделов, и
 * через долю секунды (на медленной сети — через несколько секунд) всё
 * возвращалось: шапка и левая часть экрана мигали (V5).
 *
 * Метку has-site-rail на <html> ставит SiteSidebar, а снимает при уходе со
 * страницы уже после отрисовки новой — поэтому в момент первой отрисовки
 * заглушки по ней ещё видно, какой была прошлая страница. Первый заход на
 * сайт (метки нет) — просто шапка и «Загрузка…».
 */
export function PortalRouteFallback() {
  const [withRail] = useState(
    () => typeof document !== "undefined" && document.documentElement.classList.contains("has-site-rail"),
  );
  const user = useOptionalUser();
  if (!withRail) {
    return (
      <>
        <PortalHeader />
        <main className="app">
          <p className="muted">Загрузка…</p>
        </main>
      </>
    );
  }
  // active={null}: раздел узнаётся по адресу, на который идёт переход, — рельс
  // и колонка сразу показывают, куда человек попадёт.
  return (
    <div className="portal-section-page">
      <PortalHeader />
      <div className="portal-cab-layout">
        <SiteSidebar active={null} />
        <main className="portal-cab-main portal-section">
          <SectionSubnav active={null} user={user} />
          <p className="muted">Загрузка…</p>
        </main>
      </div>
    </div>
  );
}
