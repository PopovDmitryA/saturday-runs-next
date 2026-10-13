import { useEffect, useState, type ReactNode } from "react";
import { ApiError, getCurrentUser, type User } from "../lib/api";
import { withLoginReturn } from "../lib/loginReturn";

type RequireAuthProps = {
  children: (user: User) => ReactNode;
  // Куда уводить анонима. Страницы портального редизайна передают
  // PORTAL_LOGIN_HREF, чтобы не «протекать» в старый /login.
  loginHref?: string;
};

export function RequireAuth({ children, loginHref = "/login" }: RequireAuthProps) {
  const [user, setUser] = useState<User | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    getCurrentUser()
      .then(setUser)
      .catch((err: unknown) => {
        if (err instanceof ApiError && err.status === 401) {
          // После входа — обратно сюда (?next=, lib/loginReturn). replace, а
          // не переход: иначе «Назад» со входа вернул бы на эту же страницу,
          // и она снова отправила бы на вход.
          window.location.replace(withLoginReturn(loginHref));
          return;
        }
        setError(err instanceof Error ? err.message : "Не удалось проверить сессию");
      })
      .finally(() => setLoading(false));
  }, [loginHref]);

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
            <a className="btn secondary" href={withLoginReturn(loginHref)}>
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
