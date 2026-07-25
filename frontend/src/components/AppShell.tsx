import type { FormEvent, ReactNode } from "react";
import { useCallback, useEffect, useState } from "react";
import { logout, probeCurrentUser, updateDisplayName, type User } from "../lib/api";
import { SITE_HOME_HREF } from "../lib/siteBrand";
import { APP_NAV_ITEMS } from "../lib/siteNav";
import { userLabel } from "../lib/userLabel";
import { SiteHeader } from "./SiteHeader";

type AppShellProps = {
  title: string;
  children: ReactNode;
  activePath?: string;
};

export function AppShell({ title, children, activePath }: AppShellProps) {
  const path = activePath ?? (window.location.pathname.replace(/\/$/, "") || "/");
  const [user, setUser] = useState<User | null>(null);
  const [editingName, setEditingName] = useState(false);
  const [nameDraft, setNameDraft] = useState("");
  const [nameSaving, setNameSaving] = useState(false);
  const [nameError, setNameError] = useState<string | null>(null);

  const loadUser = useCallback(async () => {
    const probe = await probeCurrentUser();
    if (probe.state === "authenticated") {
      setUser(probe.user);
      setNameDraft(probe.user.display_name ?? "");
      return;
    }
    if (probe.state === "guest") {
      setUser(null);
    }
    // state === "unknown": проверить сессию не удалось (429/5xx/таймаут).
    // Прежнее состояние не трогаем — иначе живая сессия выглядит разлогиненной.
  }, []);

  useEffect(() => {
    void loadUser();
  }, [loadUser]);

  const handleLogout = async () => {
    await logout();
    window.location.href = "/login";
  };

  const handleSaveName = async (event: FormEvent) => {
    event.preventDefault();
    setNameSaving(true);
    setNameError(null);
    try {
      const updated = await updateDisplayName(nameDraft);
      setUser(updated);
      setNameDraft(updated.display_name ?? "");
      setEditingName(false);
    } catch (err) {
      setNameError(err instanceof Error ? err.message : "Не удалось сохранить имя");
    } finally {
      setNameSaving(false);
    }
  };

  // Дашборд — «домашняя» страница кабинета, отдельный скрытый заголовок ей не нужен.
  const showPageHeading = title !== "Личный кабинет";

  return (
    <div className="shell">
      <SiteHeader
        homeHref={SITE_HOME_HREF}
        navItems={APP_NAV_ITEMS}
        activePath={path}
        showAdminNav={user?.is_admin ?? false}
        actions={
          <button type="button" className="btn btn-ghost btn-sm" onClick={() => void handleLogout()}>
            Выйти
          </button>
        }
      />

      <div className="shell-content">
        {user && (
          <div className="shell-user">
            {!editingName ? (
              <div className="shell-user-row">
                <h1 className="shell-user-name">{userLabel(user)}</h1>
                <button
                  type="button"
                  className="btn-icon btn-icon-lg"
                  aria-label="Изменить имя"
                  title="Изменить имя"
                  onClick={() => {
                    setNameDraft(
                      user.display_name_customized === true && user.display_name
                        ? user.display_name
                        : "",
                    );
                    setEditingName(true);
                    setNameError(null);
                  }}
                >
                  ✎
                </button>
              </div>
            ) : (
              <form className="shell-user-form" onSubmit={(event) => void handleSaveName(event)}>
                <input
                  className="input shell-user-input"
                  value={nameDraft}
                  onChange={(event) => setNameDraft(event.target.value)}
                  maxLength={128}
                  placeholder={
                    user.telegram_username
                      ? `Пусто — будет @${user.telegram_username.replace(/^@/, "")}`
                      : "Пусто — имя из Telegram"
                  }
                  autoFocus
                />
                <button type="submit" className="btn btn-ghost btn-sm" disabled={nameSaving}>
                  {nameSaving ? "…" : "OK"}
                </button>
                <button
                  type="button"
                  className="btn btn-ghost btn-sm"
                  disabled={nameSaving}
                  onClick={() => {
                    setEditingName(false);
                    setNameError(null);
                  }}
                >
                  Отмена
                </button>
              </form>
            )}
            {nameError && <p className="shell-user-error">{nameError}</p>}
          </div>
        )}

        {showPageHeading && (
          <h2 className="page-heading visually-hidden">{title}</h2>
        )}

        <main className="shell-main">{children}</main>
      </div>
    </div>
  );
}
