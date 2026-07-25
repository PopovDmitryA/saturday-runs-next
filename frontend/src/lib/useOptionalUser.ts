import { useEffect, useState } from "react";
import { getCurrentUser, type User } from "./api";

/**
 * Текущий пользователь БЕЗ гейта — для публичных страниц (локации, рейтинги),
 * которые залогиненному показывают личные блоки, а анониму — призыв войти.
 * undefined — сессия ещё проверяется, null — аноним.
 *
 * Ответ кэшируется в sessionStorage: сайт — MPA (каждый переход — полная
 * перезагрузка), и без кэша шапка с сайдбаром на долю секунды показывали
 * «Войти» залогиненному, пока /auth/me летел по сети. С кэшем стартуем с
 * последнего известного состояния и молча обновляем его свежим ответом.
 */
const CACHE_KEY = "sr:cachedAuthUser";
const CACHE_ANON = "anon";

function readCachedUser(): User | null | undefined {
  try {
    const raw = sessionStorage.getItem(CACHE_KEY);
    if (raw === CACHE_ANON) {
      return null;
    }
    if (raw) {
      return JSON.parse(raw) as User;
    }
  } catch {
    // повреждённый кэш игнорируем
  }
  return undefined;
}

function writeCachedUser(user: User | null): void {
  try {
    sessionStorage.setItem(CACHE_KEY, user === null ? CACHE_ANON : JSON.stringify(user));
  } catch {
    // sessionStorage недоступен — просто не кэшируем
  }
}

/** Сбросить кэш (обязательно при выходе — иначе шапка «помнит» ник). */
export function clearCachedUser(): void {
  try {
    sessionStorage.removeItem(CACHE_KEY);
  } catch {
    // ignore
  }
}

export function useOptionalUser(): User | null | undefined {
  const [user, setUser] = useState<User | null | undefined>(readCachedUser);

  useEffect(() => {
    let cancelled = false;
    getCurrentUser()
      .then((current) => {
        writeCachedUser(current);
        if (!cancelled) setUser(current);
      })
      .catch(() => {
        writeCachedUser(null);
        if (!cancelled) setUser(null);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  return user;
}
