/**
 * Меню аккаунта под именем в шапке: кабинет, настройки, админка, выход.
 *
 * Настройки — не страница кабинета, а настройки аккаунта и всего сайта
 * (правка Дмитрия 25.09.2026), поэтому из колонки «Мой кабинет» они уехали
 * сюда — туда же, где их ищут на большинстве сайтов: под своим именем.
 * Раньше имя в шапке просто вело в кабинет, а «Выйти» висело внизу колонки.
 */
import { useEffect, useRef, useState } from "react";
import type { User } from "../../../lib/api";
import { cabinetTabHref, PORTAL_CABINET_SETTINGS_HREF } from "../../../lib/portalRoutes";
import { userLabel } from "../../../lib/userLabel";
import { LogoutButton } from "../SiteSidebar";
import { CABINET_ICONS, CHEVRON_DOWN_ICON, SETTINGS_ICON, SHIELD_ICON } from "./navIcons";

function initials(label: string): string {
  const parts = label.replace(/^@/, "").trim().split(/\s+/).filter(Boolean);
  return ((parts[0]?.[0] ?? "") + (parts[1]?.[0] ?? "")).toUpperCase() || "?";
}

export function AccountMenu({ user }: { user: User }) {
  const [open, setOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);
  const buttonRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    if (!open) return;
    const onPointer = (event: PointerEvent) => {
      if (!rootRef.current?.contains(event.target as Node)) setOpen(false);
    };
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        setOpen(false);
        buttonRef.current?.focus();
      }
    };
    document.addEventListener("pointerdown", onPointer);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("pointerdown", onPointer);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  const label = userLabel(user);
  return (
    <div className="account-menu" ref={rootRef}>
      <button
        ref={buttonRef}
        type="button"
        className="portal-header-user account-menu-button"
        aria-haspopup="menu"
        aria-expanded={open}
        title={`${label} — аккаунт`}
        aria-label={`Аккаунт: ${label}`}
        onClick={() => setOpen((value) => !value)}
      >
        {user.avatar_url ? (
          <img className="portal-header-user-avatar" src={user.avatar_url} alt="" />
        ) : (
          // Без аватарки на узком экране осталась бы одна стрелка — инициалы
          // держат кнопку узнаваемой, когда имя спрятано.
          <span className="account-menu-initials" aria-hidden="true">
            {initials(label)}
          </span>
        )}
        <span className="account-menu-name">{label}</span>
        <span className="account-menu-chevron">{CHEVRON_DOWN_ICON}</span>
      </button>
      {open && (
        <div className="account-menu-popover" role="menu" aria-label="Аккаунт">
          <a role="menuitem" className="account-menu-item" href={cabinetTabHref(user, "dashboard")}>
            <span className="account-menu-icon">{CABINET_ICONS.dashboard}</span>
            Мой кабинет
          </a>
          <a role="menuitem" className="account-menu-item" href={PORTAL_CABINET_SETTINGS_HREF}>
            <span className="account-menu-icon">{SETTINGS_ICON}</span>
            Настройки
          </a>
          {user.is_admin && (
            <a role="menuitem" className="account-menu-item account-menu-item-admin" href="/admin/users">
              <span className="account-menu-icon">{SHIELD_ICON}</span>
              Админка
            </a>
          )}
          <div className="account-menu-sep" />
          <LogoutButton className="account-menu-item account-menu-logout" />
        </div>
      )}
    </div>
  );
}
