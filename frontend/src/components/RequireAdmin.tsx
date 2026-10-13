import { useEffect, useState, type ReactNode } from "react";
import { ApiError, getCurrentUser, type User } from "../lib/api";
import { goToLogin } from "../lib/loginReturn";

type RequireAdminProps = {
  children: ReactNode | ((user: User) => ReactNode);
};

export function RequireAdmin({ children }: RequireAdminProps) {
  const [user, setUser] = useState<User | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    getCurrentUser()
      .then((current) => {
        if (!current.is_admin) {
          window.location.href = "/dashboard";
          return;
        }
        setUser(current);
      })
      .catch((err: unknown) => {
        if (err instanceof ApiError && err.status === 401) {
          // После входа — обратно на эту страницу админки; replace — чтобы
          // «Назад» со входа не вернул сюда и не отправил на вход снова.
          goToLogin({ replace: true });
          return;
        }
        // Сбой проверки, а не «не вошли»: без возврата, иначе вход, увидев
        // живую сессию, отправил бы обратно сюда — и по кругу, пока сбоит.
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
