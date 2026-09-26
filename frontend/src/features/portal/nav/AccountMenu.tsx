/**
 * Меню аккаунта под именем в шапке: кабинет, настройки, админка, выход.
 *
 * Настройки — не страница кабинета, а настройки аккаунта и всего сайта
 * (правка Дмитрия 25.09.2026), поэтому из колонки кабинета они уехали
 * сюда — туда же, где их ищут на большинстве сайтов: под своим именем.
 * Раньше имя в шапке просто вело в кабинет, а «Выйти» висело внизу колонки.
 *
 * Устроено как раскрывашка, а не role="menu": кнопка с aria-expanded и
 * обычный список ссылок. role="menu" обещает диктору управление стрелками,
 * которого не было (a11y-2). При открытии фокус встаёт на первый пункт,
 * Esc закрывает и возвращает фокус на кнопку, уход фокуса наружу — закрывает.
 */
import { useEffect, useRef, useState } from "react";
import type { User } from "../../../lib/api";
import { userLabel } from "../../../lib/userLabel";
import { LogoutButton } from "../SiteSidebar";
import { CHEVRON_DOWN_ICON, ME_ICON, SETTINGS_ICON, SHIELD_ICON } from "./navIcons";
import { CABINET_LABEL, cabinetHref } from "./siteNav";

function initials(label: string): string {
  const parts = label.replace(/^@/, "").trim().split(/\s+/).filter(Boolean);
  return ((parts[0]?.[0] ?? "") + (parts[1]?.[0] ?? "")).toUpperCase() || "?";
}

const POPOVER_ID = "account-menu-popover";

export function AccountMenu({ user }: { user: User }) {
  const [open, setOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);
  const buttonRef = useRef<HTMLButtonElement>(null);
  const popoverRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    popoverRef.current?.querySelector<HTMLElement>("a, button")?.focus({ preventScroll: true });
    const onPointer = (event: PointerEvent) => {
      if (!rootRef.current?.contains(event.target as Node)) setOpen(false);
    };
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        setOpen(false);
        buttonRef.current?.focus();
      }
    };
    const onFocus = (event: FocusEvent) => {
      if (!rootRef.current?.contains(event.target as Node)) setOpen(false);
    };
    document.addEventListener("pointerdown", onPointer);
    document.addEventListener("keydown", onKey);
    document.addEventListener("focusin", onFocus);
    return () => {
      document.removeEventListener("pointerdown", onPointer);
      document.removeEventListener("keydown", onKey);
      document.removeEventListener("focusin", onFocus);
    };
  }, [open]);

  const label = userLabel(user);
  return (
    <div className="account-menu" ref={rootRef}>
      <button
        ref={buttonRef}
        type="button"
        className="portal-header-user account-menu-button"
        aria-expanded={open}
        aria-controls={POPOVER_ID}
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
        <div className="account-menu-popover" id={POPOVER_ID} ref={popoverRef}>
          <a className="account-menu-item" href={cabinetHref(user, "dashboard")}>
            <span className="account-menu-icon">{ME_ICON}</span>
            {CABINET_LABEL}
          </a>
          <a className="account-menu-item" href={cabinetHref(user, "settings")}>
            <span className="account-menu-icon">{SETTINGS_ICON}</span>
            Настройки
          </a>
          {user.is_admin && (
            <a className="account-menu-item account-menu-item-admin" href="/admin/users">
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
