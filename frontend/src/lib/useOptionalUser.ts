import { useEffect, useState } from "react";
import { ApiError, getCurrentUser, type User } from "./api";

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

export function useOptionalUser(options?: { skipCache?: boolean }): User | null | undefined {
  // skipCache — не доверять кэшу на старте. Нужно там, где по ответу
  // принимается решение о редиректе: сразу после входа кэш ещё помнит
  // «аноним», и страница успела бы отправить залогиненного обратно на /login.
  const skipCache = options?.skipCache ?? false;
  const [user, setUser] = useState<User | null | undefined>(skipCache ? undefined : readCachedUser);

  useEffect(() => {
    let cancelled = false;
    getCurrentUser()
      .then((current) => {
        writeCachedUser(current);
        if (!cancelled) setUser(current);
      })
      .catch((err: unknown) => {
        // «Аноним» кэшируем ТОЛЬКО на настоящий 401. Обрывы сети и абортнутые
        // запросы (клик по ссылке, пока /auth/me ещё летит — обычное дело в
        // MPA) НЕ должны затирать кэш: именно это заставляло следующую
        // страницу мигать кнопкой «Войти» перед залогиненным.
        const isRealAnon = err instanceof ApiError && err.status === 401;
        if (isRealAnon) {
          writeCachedUser(null);
          if (!cancelled) setUser(null);
        } else if (!cancelled) {
          // Ошибка сети: остаёмся на кэшированном состоянии; если кэша не
          // было — считаем анонимом (но кэш не пишем, следующая страница
          // попробует снова).
          setUser((current) => (current === undefined ? null : current));
        }
      });
    return () => {
      cancelled = true;
    };
  }, []);

  return user;
}
