import { useEffect, useLayoutEffect, useRef, useState, type CSSProperties, type ReactNode } from "react";
import { createPortal } from "react-dom";

const VIEWPORT_GUTTER = 8;
/** Отступ подсказки от триггера. */
const ANCHOR_GAP = 8;
/** Ближе к краю плашки хвостик не двигаем: там скругление угла (как в TapTooltipBubble). */
const ARROW_EDGE_INSET = 14;

/** Где триггер на экране: центр по горизонтали, верх и низ. */
type Anchor = { centre: number; top: number; bottom: number };
type Placement = { x: number; y: number; below: boolean; arrow: number };

type StatHintTooltipProps = {
  text?: string;
  content?: ReactNode;
  children: ReactNode;
  className?: string;
  /**
   * Подпись триггера-значка («i») для экранных читалок: с ней триггер —
   * кнопка с этим именем, а не безымянный фокус на span.
   */
  label?: string;
};

export function StatHintTooltip({ text, content, children, className, label }: StatHintTooltipProps) {
  const triggerRef = useRef<HTMLSpanElement>(null);
  const tooltipRef = useRef<HTMLSpanElement>(null);
  const [visible, setVisible] = useState(false);
  const [anchor, setAnchor] = useState<Anchor | null>(null);
  const [placement, setPlacement] = useState<Placement | null>(null);
  const tooltipBody = content ?? text;

  if (!tooltipBody) {
    return <>{children}</>;
  }

  const updatePosition = () => {
    const trigger = triggerRef.current;
    if (!trigger) {
      return;
    }
    const rect = trigger.getBoundingClientRect();
    setAnchor({ centre: rect.left + rect.width / 2, top: rect.top, bottom: rect.bottom });
  };

  const show = () => {
    updatePosition();
    setVisible(true);
  };

  const hide = () => {
    setVisible(false);
  };

  // Уход курсора не закрывает подсказку, открытую тапом или кликом: триггер
  // тогда в фокусе, и закроют её blur или тап мимо. Иначе на телефоне за тапом
  // следовал синтетический mouseleave, и «i» мигала, не успев показаться.
  const hideOnLeave = () => {
    if (document.activeElement !== triggerRef.current) {
      hide();
    }
  };

  // Тултип центрируется по триггеру (transform: translateX(-50%)), поэтому у
  // правого края экрана он вылезал за границу — у колонки PR подсказка
  // обрезалась. После рендера меряем реальный размер и вжимаем в экран: по
  // горизонтали — сдвигом (хвостик остаётся над триггером, а не посреди
  // плашки, иначе он показывал на «?» соседней колонки), по вертикали —
  // разворотом вниз, если над триггером плашка целиком не помещается
  // (заголовок таблицы под шапкой сайта срезал начало подсказки «Гостей»).
  useLayoutEffect(() => {
    const tip = tooltipRef.current;
    if (!visible || !tip || !anchor) {
      setPlacement(null);
      return;
    }
    const width = tip.offsetWidth;
    const height = tip.offsetHeight;
    const half = width / 2;
    const min = VIEWPORT_GUTTER + half;
    const max = window.innerWidth - VIEWPORT_GUTTER - half;
    const x = Math.min(Math.max(anchor.centre, min), Math.max(min, max));
    const roomAbove = anchor.top - ANCHOR_GAP - height >= VIEWPORT_GUTTER;
    const roomBelow = anchor.bottom + ANCHOR_GAP + height <= window.innerHeight - VIEWPORT_GUTTER;
    const below = !roomAbove && roomBelow;
    setPlacement({
      x,
      y: below ? anchor.bottom + ANCHOR_GAP : anchor.top - ANCHOR_GAP,
      below,
      arrow: Math.min(
        Math.max(anchor.centre - (x - half), ARROW_EDGE_INSET),
        Math.max(ARROW_EDGE_INSET, width - ARROW_EDGE_INSET),
      ),
    });
  }, [visible, anchor]);

  useEffect(() => {
    if (!visible) {
      return;
    }
    const handleReposition = () => updatePosition();
    // Тап мимо триггера закрывает тултип (десктопный mouseleave на таче не сработает).
    const handleOutside = (event: MouseEvent | TouchEvent) => {
      const trigger = triggerRef.current;
      if (trigger && event.target instanceof Node && !trigger.contains(event.target)) {
        setVisible(false);
      }
    };
    window.addEventListener("scroll", handleReposition, true);
    window.addEventListener("resize", handleReposition);
    document.addEventListener("click", handleOutside);
    return () => {
      window.removeEventListener("scroll", handleReposition, true);
      window.removeEventListener("resize", handleReposition);
      document.removeEventListener("click", handleOutside);
    };
  }, [visible]);

  return (
    <>
      <span
        ref={triggerRef}
        className={className ?? "stat-hint-tooltip-trigger"}
        onMouseEnter={show}
        onMouseLeave={hideOnLeave}
        // Тап на тачскринах: mouseenter там не живёт, показываем по клику;
        // закрытие — тапом мимо (см. handleOutside). stopPropagation глушит
        // клик-сортировку на th, чтобы тап по «?» не менял сортировку.
        onClick={(event) => {
          event.stopPropagation();
          show();
        }}
        onFocus={show}
        onBlur={hide}
        onKeyDown={(event) => {
          if (event.key === "Escape") {
            hide();
          }
        }}
        tabIndex={0}
        role={label ? "button" : undefined}
        aria-label={label}
        aria-describedby={visible ? "stat-hint-tooltip" : undefined}
      >
        {children}
      </span>
      {visible &&
        createPortal(
          <span
            ref={tooltipRef}
            id="stat-hint-tooltip"
            role="tooltip"
            // Разворот вниз и хвостик — те же правила, что у тач-подсказки
            // (.tap-tooltip-below, --tap-tooltip-arrow в index.css). Пока
            // размер не измерен, плашка невидима — без мигания в углу экрана.
            className={`unique-locations-count-tooltip stat-hint-tooltip${placement?.below ? " tap-tooltip-below" : ""}`}
            style={
              placement
                ? ({ left: placement.x, top: placement.y, "--tap-tooltip-arrow": `${placement.arrow}px` } as CSSProperties)
                : { left: 0, top: 0, visibility: "hidden" }
            }
          >
            {tooltipBody}
          </span>,
          document.body,
        )}
    </>
  );
}
