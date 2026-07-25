import { useEffect, useState } from "react";
import { getCurrentUser, type User } from "./api";

/**
 * Текущий пользователь БЕЗ гейта — для публичных страниц (локации, рейтинги),
 * которые залогиненному показывают личные блоки, а анониму — призыв войти.
 * undefined — сессия ещё проверяется, null — аноним.
 */
export function useOptionalUser(): User | null | undefined {
  const [user, setUser] = useState<User | null | undefined>(undefined);

  useEffect(() => {
    let cancelled = false;
    getCurrentUser()
      .then((current) => {
        if (!cancelled) setUser(current);
      })
      .catch(() => {
        if (!cancelled) setUser(null);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  return user;
}
