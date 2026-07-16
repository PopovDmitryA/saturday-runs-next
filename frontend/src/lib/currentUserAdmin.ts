import { useSyncExternalStore } from "react";

/**
 * Флаг «текущий пользователь — админ» для ссылок на раздел «Локации».
 *
 * Раздел пока admin-only, поэтому ссылки на /locations/* по всему сайту
 * (таблицы пробежек/волонтёрств, попапы карты, каталог) должны быть обычным
 * текстом для всех остальных. Глобального контекста пользователя в приложении
 * нет: RequireAuth отдаёт user через render-prop, и часть страниц его
 * отбрасывает, а demo и публичный профиль работают вообще без сессии.
 * Поэтому маленький модульный стор: RequireAuth публикует сюда значение при
 * успешной проверке сессии, ссылки читают. Без сессии (demo, публичный
 * профиль) остаётся false — ровно то, что нужно.
 */
let isAdmin = false;
const listeners = new Set<() => void>();

export function setCurrentUserIsAdmin(value: boolean): void {
  if (isAdmin === value) {
    return;
  }
  isAdmin = value;
  for (const listener of listeners) {
    listener();
  }
}

export function getCurrentUserIsAdmin(): boolean {
  return isAdmin;
}

function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

export function useCurrentUserIsAdmin(): boolean {
  return useSyncExternalStore(subscribe, getCurrentUserIsAdmin, () => false);
}
