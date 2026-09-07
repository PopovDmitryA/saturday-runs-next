import { useEffect, useRef, useState } from "react";
import { readEntryValue, writeEntryValue } from "../lib/entryMemory";

/**
 * useState, переживающий уход со страницы и возврат по «назад».
 *
 * Значение привязано к записи истории (см. lib/historyEntry), а не к адресу:
 * вернувшись назад, страница получает своё прежнее состояние, а открыв тот же
 * адрес заново — чистое. Годится для того, чему не место в адресе: сколько
 * строк догружено, раскрыт ли спойлер, что выбрано в фильтре столбца.
 *
 * Значение обязано быть JSON-совместимым (множества кладите массивами) —
 * иначе оно не переживёт перезагрузку документа.
 */
export function useRestorableState<T>(
  name: string,
  initial: T | (() => T),
): [T, React.Dispatch<React.SetStateAction<T>>] {
  const [value, setValue] = useState<T>(() => {
    const saved = readEntryValue<T>(name);
    if (saved !== undefined) {
      return saved;
    }
    return typeof initial === "function" ? (initial as () => T)() : initial;
  });

  // Первый прогон эффекта ничего нового не сохраняет — значение либо только что
  // прочитано из снимка, либо это дефолт, который и так восстановится.
  const first = useRef(true);
  useEffect(() => {
    if (first.current) {
      first.current = false;
      return;
    }
    writeEntryValue(name, value);
  }, [name, value]);

  return [value, setValue];
}
