import { useEffect, useRef, useState } from "react";

/**
 * Число, «докручивающееся» от нуля до значения при появлении на экране
 * (гипотеза Т3 варианта B: движение цепляет глаз — цифру дочитывают, а не
 * проскальзывают мимо).
 *
 * При enabled=false рендерит значение сразу — вариант A остаётся как был.
 * Анимация запускается один раз, когда элемент попал во вьюпорт; последующие
 * смены value (переключение «за всё время» / «последний год») показываются
 * мгновенно, чтобы не превращать каждую перерисовку в аттракцион. Страховка
 * setTimeout доводит число до финала, даже если rAF заморожен (свёрнутая
 * вкладка, встроенные превью).
 */
export function CountUpNumber({
  value,
  format,
  durationMs = 1200,
  enabled = true,
}: {
  value: number;
  format: (n: number) => string;
  durationMs?: number;
  enabled?: boolean;
}) {
  const ref = useRef<HTMLSpanElement | null>(null);
  const startedRef = useRef(false);
  const [display, setDisplay] = useState(enabled ? 0 : value);

  useEffect(() => {
    const el = ref.current;
    // Без анимации: выключено, уже отыграла, нет IO, вырожденный вьюпорт
    // (встроенные превью с высотой 0) или пользователь просил меньше движения.
    const reducedMotion =
      typeof window.matchMedia === "function" && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    if (
      !enabled ||
      startedRef.current ||
      el === null ||
      typeof IntersectionObserver === "undefined" ||
      window.innerHeight === 0 ||
      reducedMotion
    ) {
      setDisplay(value);
      return;
    }
    let raf = 0;
    let safety = 0;
    const observer = new IntersectionObserver(
      (entries) => {
        if (!entries.some((entry) => entry.isIntersecting) || startedRef.current) {
          return;
        }
        startedRef.current = true;
        observer.disconnect();
        const startTs = performance.now();
        const tick = (now: number) => {
          const t = Math.min(1, (now - startTs) / durationMs);
          const eased = 1 - Math.pow(1 - t, 3);
          setDisplay(Math.round(value * eased));
          if (t < 1) {
            raf = requestAnimationFrame(tick);
          }
        };
        raf = requestAnimationFrame(tick);
        safety = window.setTimeout(() => {
          cancelAnimationFrame(raf);
          setDisplay(value);
        }, durationMs + 500);
      },
      { threshold: 0.4 },
    );
    observer.observe(el);
    return () => {
      observer.disconnect();
      cancelAnimationFrame(raf);
      window.clearTimeout(safety);
    };
  }, [enabled, value, durationMs]);

  return <span ref={ref}>{format(display)}</span>;
}
