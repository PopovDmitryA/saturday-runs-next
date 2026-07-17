import { useEffect, useState, type ReactNode } from "react";
import { ApiError, getCurrentUser, type User } from "../lib/api";

type RequireAuthProps = {
  children: (user: User) => ReactNode;
};

export function RequireAuth({ children }: RequireAuthProps) {
  const [user, setUser] = useState<User | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    getCurrentUser()
      .then(setUser)
      .catch((err: unknown) => {
        if (err instanceof ApiError && err.status === 401) {
          window.location.href = "/login";
          return;
        }
        setError(err instanceof Error ? err.message : "Не удалось проверить сессию");
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

  if (error) {
    return (
      <main className="app">
        <div className="card error">
          <p>{error}</p>
          <p>
            <a className="btn secondary" href="/login">
              Перейти ко входу
            </a>
          </p>
        </div>
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
