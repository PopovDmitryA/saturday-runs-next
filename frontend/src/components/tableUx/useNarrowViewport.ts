import { useEffect, useState } from "react";

// Единый брейкпоинт «узкой» таблицы — тот же, что в CSS (@media max-width: 820px),
// ниже него включаются горизонтальный скролл и режим «Кратко | Полно».
export const NARROW_TABLE_QUERY = "(max-width: 820px)";

export function useNarrowViewport(): boolean {
  const [narrow, setNarrow] = useState(
    () => typeof window !== "undefined" && window.matchMedia(NARROW_TABLE_QUERY).matches,
  );

  useEffect(() => {
    const mql = window.matchMedia(NARROW_TABLE_QUERY);
    const onChange = (event: MediaQueryListEvent) => setNarrow(event.matches);
    mql.addEventListener("change", onChange);
    return () => mql.removeEventListener("change", onChange);
  }, []);

  return narrow;
}
