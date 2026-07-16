import { useEffect, useState, type ReactNode } from "react";
import { ApiError, getCurrentUser, type User } from "../lib/api";
import { setCurrentUserIsAdmin } from "../lib/currentUserAdmin";

type RequireAdminProps = {
  children: ReactNode | ((user: User) => ReactNode);
};

export function RequireAdmin({ children }: RequireAdminProps) {
  const [user, setUser] = useState<User | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    getCurrentUser()
      .then((current) => {
        setCurrentUserIsAdmin(current.is_admin);
        if (!current.is_admin) {
          window.location.href = "/dashboard";
          return;
        }
        setUser(current);
      })
      .catch((err: unknown) => {
        if (err instanceof ApiError && err.status === 401) {
          window.location.href = "/login";
          return;
        }
        window.location.href = "/login";
      })
      .finally(() => setLoading(false));
  }, []);

  if (loading) {
    return (
      <main className="app">
        <p className="muted">Загрузка…</p>
      </main>
    );
  }

  if (!user) {
    return (
      <main className="app">
        <p className="muted">Нет доступа…</p>
      </main>
    );
  }

  if (typeof children === "function") {
    return <>{children(user)}</>;
  }

  return <>{children}</>;
}
