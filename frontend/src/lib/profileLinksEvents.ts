import { useEffect, useRef } from "react";

/**
 * «Привязки беговых систем изменились» — сигнал на всё приложение.
 *
 * Профиль теперь привязывают не только на своих страницах: окно «Это вы?» из
 * поиска и окно после входа живут поверх любой страницы. Без общего сигнала
 * плитки онбординга и список профилей в кабинете показывали бы старое, пока
 * человек не обновит страницу.
 */
export const PROFILE_LINKS_CHANGED_EVENT = "profile-links:changed";

export function notifyProfileLinksChanged(): void {
  window.dispatchEvent(new CustomEvent(PROFILE_LINKS_CHANGED_EVENT));
}

/** Перечитать привязки, когда их поменяли где-то ещё на странице. */
export function useProfileLinksChanged(handler: () => void): void {
  // Обработчик держим в ref: инлайн-функция не переподписывает слушателя на
  // каждом рендере.
  const handlerRef = useRef(handler);
  useEffect(() => {
    handlerRef.current = handler;
  });
  useEffect(() => {
    const onChanged = () => handlerRef.current();
    window.addEventListener(PROFILE_LINKS_CHANGED_EVENT, onChanged);
    return () => window.removeEventListener(PROFILE_LINKS_CHANGED_EVENT, onChanged);
  }, []);
}
