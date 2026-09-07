import { useEffect, useState } from "react";
import { currentEntryKey, onEntryChange } from "../lib/historyEntry";

/**
 * Ключ текущей записи истории (см. lib/historyEntry).
 *
 * Нужен как React-ключ страницам, которые остаются тем же компонентом при
 * переходе (рейтинг → другой рейтинг, локация → другая локация): без него
 * React переиспользует экземпляр, и на новую страницу утекает состояние
 * прежней, а на возврат «назад» — наоборот, не приезжает снимок записи.
 * С ключом страница на каждом переходе собирается заново — из адреса и снимка.
 */
export function useEntryKey(): string {
  const [key, setKey] = useState(currentEntryKey);
  useEffect(() => onEntryChange(({ to }) => setKey(to)), []);
  return key;
}
