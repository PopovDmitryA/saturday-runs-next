/**
 * Ленивые разделы сайта (code-splitting).
 *
 * До 13.09.2026 фронт собирался в один бандл ~1,5 МБ (433 КБ gzip): любой
 * заход, в том числе с телефона по мобильной сети, тянул код админки, кабинета
 * организатора, карт (leaflet) и генератора постеров (html-to-image), а каждый
 * деплой менял хэш единственного файла и сбрасывал кэш целиком. Теперь разделы
 * грузятся по первому обращению, а вендоры лежат отдельными чанками
 * (manualChunks в vite.config.ts), чтобы релиз не инвалидировал react/leaflet.
 *
 * Грабля деплоя: у давно открытой вкладки старый index.html, при переходе она
 * просит чанк со старым хэшем, а nginx уже отдаёт новую сборку → 404 на чанке
 * и белый экран. Лечится одной перезагрузкой: свежий index.html знает свежие
 * хэши. Перезагружаем не больше одного раза на адрес (флаг в sessionStorage),
 * чтобы настоящая сетевая ошибка не крутила страницу по кругу — второй провал
 * уходит в LazyErrorBoundary с кнопкой «Обновить».
 */
import { Component, lazy, type ComponentType, type ErrorInfo, type LazyExoticComponent, type ReactNode } from "react";

const RELOAD_FLAG = "lazy-chunk-reloaded-for";

function readReloadFlag(): string | null {
  try {
    return window.sessionStorage.getItem(RELOAD_FLAG);
  } catch {
    return null;
  }
}

function writeReloadFlag(value: string | null) {
  try {
    if (value === null) {
      window.sessionStorage.removeItem(RELOAD_FLAG);
    } else {
      window.sessionStorage.setItem(RELOAD_FLAG, value);
    }
  } catch {
    // Приватный режим без storage — просто не защищаемся от повторной перезагрузки.
  }
}

/**
 * Загрузка модуля с одной страховочной перезагрузкой страницы, если чанк не
 * пришёл (устаревший index.html после деплоя). Во второй раз ошибка
 * пробрасывается наверх — до ближайшего error boundary.
 */
export async function loadWithReload<M>(load: () => Promise<M>): Promise<M> {
  try {
    const mod = await load();
    if (readReloadFlag() !== null) {
      writeReloadFlag(null);
    }
    return mod;
  } catch (error) {
    const here = window.location.href;
    if (readReloadFlag() !== here) {
      writeReloadFlag(here);
      window.location.reload();
      // Страница уже перезагружается — держим Suspense-фолбэк, не бросаем.
      return new Promise<never>(() => {});
    }
    throw error;
  }
}

/**
 * React.lazy для модулей с именованными экспортами (в проекте default-экспорта
 * у страниц нет): `lazyPage(() => import("./X"), (m) => m.XPage)`.
 */
export function lazyPage<M, P>(
  load: () => Promise<M>,
  pick: (mod: M) => ComponentType<P>,
): LazyExoticComponent<ComponentType<P>> {
  return lazy(() => loadWithReload(load).then((mod) => ({ default: pick(mod) })));
}

/** Единый фолбэк Suspense — тот же «Загрузка…», что рисуют сами страницы. */
export function RouteFallback() {
  return (
    <main className="app">
      <p className="muted">Загрузка…</p>
    </main>
  );
}

type BoundaryState = { failed: boolean };

/**
 * Ловит провал загрузки чанка (и заодно любую ошибку рендера страницы):
 * вместо белого экрана — карточка с кнопкой «Обновить». Ключуется в App по
 * записи истории, поэтому переход на другой адрес сбрасывает состояние.
 */
export class LazyErrorBoundary extends Component<{ children: ReactNode }, BoundaryState> {
  state: BoundaryState = { failed: false };

  static getDerivedStateFromError(): BoundaryState {
    return { failed: true };
  }

  componentDidCatch(error: unknown, info: ErrorInfo) {
    console.error("Страница не отрисовалась", error, info.componentStack);
  }

  render() {
    if (!this.state.failed) {
      return this.props.children;
    }
    return (
      <main className="app">
        <div className="card error">
          <p>Не удалось загрузить страницу. Проверьте связь и попробуйте ещё раз.</p>
          <p>
            <button type="button" className="btn secondary" onClick={() => window.location.reload()}>
              Обновить
            </button>
          </p>
        </div>
      </main>
    );
  }
}
