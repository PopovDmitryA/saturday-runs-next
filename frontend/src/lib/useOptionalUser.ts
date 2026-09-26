import { useEffect, useState } from "react";
import { probeCurrentUser, type User } from "./api";
import { dropCached } from "./dataCache";

/**
 * Текущий пользователь БЕЗ гейта — для публичных страниц (локации, рейтинги),
 * которые залогиненному показывают личные блоки, а анониму — призыв войти.
 * undefined — сессия ещё проверяется, null — аноним.
 *
 * Ответ кэшируется в sessionStorage. Переходы по сайту идут без перезагрузки
 * (pushState, hooks/useAppPath), но страница на каждом переходе собирается
 * заново (ключ записи истории, hooks/useEntryKey), а первый заход и F5 —
 * вообще с нуля. Без кэша шапка с сайдбаром на долю секунды показывали
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
  // Ответы API на «назад» тоже личные: после выхода их нельзя показать тому,
  // кто войдёт следующим в этой же вкладке.
  dropCached("");
}

export function useOptionalUser(options?: {
  skipCache?: boolean;
  unknownAsPending?: boolean;
}): User | null | undefined {
  // skipCache — не доверять кэшу на старте. Нужно там, где по ответу
  // принимается решение о редиректе: сразу после входа кэш ещё помнит
  // «аноним», и страница успела бы отправить залогиненного обратно на /login.
  const skipCache = options?.skipCache ?? false;
  // unknownAsPending — «спросить не удалось» (429/5xx/таймаут) без кэша не
  // превращать в гостя, а оставить undefined. Нужно навигации (шапка, рельс,
  // нижняя панель): при undefined роль организатора берётся из памяти
  // браузера, и сбой /auth/me не перестраивает рельс в «Войти» без
  // «Оргкабинета» и не стирает запомненную локацию (V3). Страницам это не
  // подходит: там undefined — «Загрузка…», и сбой сети повесил бы её навсегда.
  const unknownAsPending = options?.unknownAsPending ?? false;
  const [user, setUser] = useState<User | null | undefined>(skipCache ? undefined : readCachedUser);

  useEffect(() => {
    let cancelled = false;
    // probeCurrentUser (из main) уже умеет отличать «точно гость» от «спросить
    // не удалось»: 429/5xx/таймаут он повторяет и в крайнем случае отдаёт
    // unknown. Гостя кэшируем только при явном ответе сервера — иначе
    // следующая страница мигала бы кнопкой «Войти» перед залогиненным.
    void probeCurrentUser().then((probe) => {
      if (probe.state === "authenticated") {
        writeCachedUser(probe.user);
        if (!cancelled) setUser(probe.user);
        return;
      }
      if (probe.state === "guest") {
        writeCachedUser(null);
        if (!cancelled) setUser(null);
        return;
      }
      // unknown — остаёмся на кэше; без кэша считаем гостем (или, по
      // unknownAsPending, «ещё неизвестно»), но не запоминаем.
      if (!cancelled && !unknownAsPending) {
        setUser((current) => (current === undefined ? null : current));
      }
    });
    return () => {
      cancelled = true;
    };
  }, [unknownAsPending]);

  return user;
}
