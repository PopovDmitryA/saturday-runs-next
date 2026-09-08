import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError } from "../lib/api";
import { readCached, writeCached } from "../lib/dataCache";

export type CachedResource<T> = {
  data: T | null;
  /** Идёт первая загрузка — показать нечего. При ответе из кэша всегда false. */
  loading: boolean;
  /** Идёт запрос, пока на экране стоит ответ из кэша или прежний срез. */
  refreshing: boolean;
  error: string | null;
  notFound: boolean;
  forbidden: boolean;
  setData: React.Dispatch<React.SetStateAction<T | null>>;
  reload: () => void;
};

type Options<T> = {
  /** Текст ошибки, когда сервер не объяснил. */
  errorText?: string;
  /** Что сделать со свежим ответом (мета-теги, подсказки) — не при показе из кэша. */
  onLoaded?: (payload: T) => void;
  /** Что сделать, когда запрос закончился любым исходом (метрика просмотра). */
  onSettled?: () => void;
};

/**
 * Загрузка ответа API с памятью на «назад» (см. lib/dataCache).
 *
 * Страница сначала рисуется из ответа не старше пяти минут, а свежий подъезжает
 * следом и молча его заменяет — так на возврате нет ни скелета, ни пустого
 * документа, к которому нельзя восстановить прокрутку. Ключ — адрес запроса с
 * параметрами: другой срез — другая запись кэша. Пустой ключ выключает кэш.
 *
 * Ошибки: 404 и 403 отдаются флагами, остальное — текстом. Если на экране уже
 * стоит ответ из кэша, сетевая ошибка молча проглатывается: ошибка вместо
 * готовой таблицы была бы шагом назад.
 */
export function useCachedResource<T>(
  key: string | null,
  load: () => Promise<T>,
  deps: readonly unknown[],
  options: Options<T> = {},
): CachedResource<T> {
  const restored = key ? readCached<T>(key) : undefined;
  const [data, setData] = useState<T | null>(restored ?? null);
  const [loading, setLoading] = useState(restored === undefined);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notFound, setNotFound] = useState(false);
  const [forbidden, setForbidden] = useState(false);
  const [version, setVersion] = useState(0);
  const optionsRef = useRef(options);
  optionsRef.current = options;
  const loadRef = useRef(load);
  loadRef.current = load;

  useEffect(() => {
    let cancelled = false;
    const cached = key ? readCached<T>(key) : undefined;
    if (cached !== undefined) {
      setData(cached);
      setLoading(false);
      setRefreshing(true);
    } else {
      setLoading(true);
    }
    setError(null);
    setNotFound(false);
    setForbidden(false);
    loadRef
      .current()
      .then((payload) => {
        if (cancelled) {
          return;
        }
        setData(payload);
        if (key) {
          writeCached(key, payload);
        }
        optionsRef.current.onLoaded?.(payload);
      })
      .catch((err: unknown) => {
        if (cancelled) {
          return;
        }
        if (err instanceof ApiError && err.status === 404) {
          setNotFound(true);
        } else if (err instanceof ApiError && err.status === 403) {
          setForbidden(true);
        } else if (cached === undefined) {
          setError(
            err instanceof Error && err.message
              ? err.message
              : (optionsRef.current.errorText ?? "Не удалось загрузить данные"),
          );
        }
      })
      .finally(() => {
        if (cancelled) {
          return;
        }
        setLoading(false);
        setRefreshing(false);
        optionsRef.current.onSettled?.();
      });
    return () => {
      cancelled = true;
    };
    // deps — то, от чего зависит запрос; сам колбэк читается через ref.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key, version, ...deps]);

  const reload = useCallback(() => setVersion((value) => value + 1), []);

  return { data, loading, refreshing, error, notFound, forbidden, setData, reload };
}
