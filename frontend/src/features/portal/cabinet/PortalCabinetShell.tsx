import { useCallback, useEffect, useRef, type ReactNode } from "react";
import { NotificationsPromptModal } from "../../../components/NotificationsPromptModal";
import type { User } from "../../../lib/api";
import { PortalFooter } from "../PortalFooter";
import { PortalHeader } from "../PortalHeader";
import { CabinetUserCard, SiteSidebar, type CabinetTabKey } from "../SiteSidebar";
import { SectionSubnav } from "../nav/SectionSubnav";
import "../portal.css";
import "./cabinet.css";

// Обратная совместимость: раньше эти сущности жили здесь.
export { userLabel, type CabinetTabKey } from "../SiteSidebar";

type PortalCabinetShellProps = {
  active: CabinetTabKey;
  user: User;
  // Заголовок страницы; на дашборде шапку рисует сам контент (герой).
  title?: string;
  sub?: string;
  // Подменить адреса разделов. Нужно превью на демо-данных: там навигация
  // ведёт на ?tab=…, иначе клик уходит на страницу под RequireAuth и без
  // сессии выбрасывает на вход.
  hrefForTab?: (key: CabinetTabKey, defaultHref: string) => string;
  // Превью: служебные пункты и «Выйти» скрыты — они уводят из демо-режима.
  hideSecondaryNav?: boolean;
  children: ReactNode;
};

// Модалки (DetailModal и т.п.) центрируются на весь viewport и не знают про
// сайдбар кабинета — из-за этого при сворачивании/разворачивании сайдбара
// модалка визуально "гуляла" относительно видимого контента. Кабинет пишет
// сюда фактические левый и правый края .portal-cab-main — ОБА, не только
// левый: на широких мониторах .portal-cab-layout сам центрируется с полями
// (max-width 1440), так что правый край контента тоже не совпадает с краем
// окна, и модалка вылезала за карточку с другой стороны, если считать одну
// только левую поправку. .modal-overlay в index.css добавляет оба значения
// к своим паддингам, так что центрирование считается строго между краями
// контентной колонки. На остальных страницах сайта переменные не
// выставляются — там модалки ведут себя как раньше.
const MODAL_CENTER_OFFSET_LEFT_VAR = "--modal-center-offset-left";
const MODAL_CENTER_OFFSET_RIGHT_VAR = "--modal-center-offset-right";

export function PortalCabinetShell({
  active,
  user,
  title,
  sub,
  hrefForTab,
  hideSecondaryNav = false,
  children,
}: PortalCabinetShellProps) {
  const mainRef = useRef<HTMLElement>(null);

  const measureModalOffset = () => {
    if (mainRef.current) {
      const rect = mainRef.current.getBoundingClientRect();
      const root = document.documentElement;
      root.style.setProperty(MODAL_CENTER_OFFSET_LEFT_VAR, `${rect.left}px`);
      root.style.setProperty(MODAL_CENTER_OFFSET_RIGHT_VAR, `${window.innerWidth - rect.right}px`);
    }
  };

  // Сайдбар сообщает о сворачивании после коммита DOM — пересчитываем офсет
  // модалок сразу (надёжнее ResizeObserver, который не срабатывал на смену
  // ширины из-за соседнего flex-элемента — см. историю в git).
  const handleCollapsedChange = useCallback(() => {
    measureModalOffset();
  }, []);

  // Пересчёт при ресайзе окна (адаптивный брейкпоинт сайдбара на 900px,
  // смена центрирующих полей страницы) и уборка переменной при
  // размонтировании кабинета — чтобы не протекала на остальные страницы.
  useEffect(() => {
    measureModalOffset();
    window.addEventListener("resize", measureModalOffset);
    // Ширина колонки меняется и без ресайза окна (догрузка данных, свёрнутый
    // сайдбар, смена вкладки). Без наблюдателя переменные оставались от
    // первого замера, и модалка-таблица открывалась узкой полосой.
    const observer = new ResizeObserver(() => measureModalOffset());
    if (mainRef.current) {
      observer.observe(mainRef.current);
    }
    return () => {
      observer.disconnect();
      window.removeEventListener("resize", measureModalOffset);
      const root = document.documentElement;
      root.style.removeProperty(MODAL_CENTER_OFFSET_LEFT_VAR);
      root.style.removeProperty(MODAL_CENTER_OFFSET_RIGHT_VAR);
    };
  }, []);

  return (
    <div className="portal-cab">
      <NotificationsPromptModal />
      {/* Та же шапка, что и на главной портала — с этого экрана вы уже
          авторизованы, так что навигация (Локации/Рейтинги/О проекте) и
          переход в кабинет по клику на ник работают идентично. */}
      <PortalHeader />

      <div className="portal-cab-layout">
        <SiteSidebar
          active={active}
          user={user}
          hrefForTab={hrefForTab}
          hideSecondaryNav={hideSecondaryNav}
          onCollapsedChange={handleCollapsedChange}
        />

        <main className="portal-cab-main" ref={mainRef}>
          {/* Страницы кабинета на телефоне — липкой полосой из общего дерева
              навигации, первой в колонке: она прилегает к шапке сайта. Своя
              нижняя панель кабинета ушла 23.09.2026 — панель одна на весь
              сайт, её рисует шапка. */}
          <SectionSubnav active={active} user={user} hrefForTab={hrefForTab} />
          {/* Телефон: сайдбар скрыт, вместе с ним пропадала и карточка
              участника — карандаш правки имени был доступен только с
              компьютера. Здесь та же карточка, видна только на узких
              экранах (см. .portal-cab-user-mobile). */}
          <div className="portal-cab-user-mobile">
            <CabinetUserCard initialUser={user} />
          </div>
          {title && (
            <div className="portal-cab-pagehead">
              <h1>{title}</h1>
              {sub && <p className="portal-cab-pagehead-sub">{sub}</p>}
            </div>
          )}
          {children}
        </main>
      </div>

      <PortalFooter />
    </div>
  );
}
