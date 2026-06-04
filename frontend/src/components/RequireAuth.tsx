import { useEffect, useState, type ReactNode } from "react";
import { ApiError, getCurrentUser, type User } from "../lib/api";

type RequireAuthProps = {
  children: (user: User) => ReactNode;
};

export function RequireAuth({ children }: RequireAuthProps) {
  const [user, setUser] = useState<User | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    getCurrentUser()
      .then(setUser)
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
        <p className="muted">Перенаправление на вход…</p>
      </main>
    );
  }

  return <>{children(user)}</>;
}
