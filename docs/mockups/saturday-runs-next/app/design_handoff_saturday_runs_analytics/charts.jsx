/* Saturday Runs — reusable chart + animation primitives.
   Exports to window. Loaded as a Babel script. */
const { useState, useEffect, useRef, useCallback } = React;

/* ---- reveal on scroll ---- */
const STILL = typeof window !== "undefined" && window.__SR_STILL;

function useInView(opts) {
  const ref = useRef(null);
  const [seen, setSeen] = useState(STILL);
  useEffect(() => {
    if (STILL) { setSeen(true); return; }
    const el = ref.current;
    if (!el) return;
    const io = new IntersectionObserver(
      (entries) => {
        entries.forEach((e) => {
          if (e.isIntersecting) {
            setSeen(true);
            io.disconnect();
          }
        });
      },
      { threshold: opts?.threshold ?? 0.2, rootMargin: opts?.rootMargin ?? "0px 0px -8% 0px" }
    );
    io.observe(el);
    // Fallback: ensure charts draw even if IO never fires (offscreen/throttled envs)
    const fallback = setTimeout(() => setSeen(true), 500);
    return () => { io.disconnect(); clearTimeout(fallback); };
  }, []);
  return [ref, seen];
}

/* ---- count-up number ---- */
function CountUp({ value, dur = 1400, format, className, start }) {
  const [n, setN] = useState(STILL ? value : 0);
  const raf = useRef(0);
  useEffect(() => {
    if (STILL) { setN(value); return; }
    if (!start) {
      setN(0);
      return;
    }
    const t0 = performance.now();
    const from = 0;
    const ease = (t) => 1 - Math.pow(1 - t, 3);
    const tick = (now) => {
      const p = Math.min(1, (now - t0) / dur);
      setN(from + (value - from) * ease(p));
      if (p < 1) raf.current = requestAnimationFrame(tick);
    };
    raf.current = requestAnimationFrame(tick);
    // Fallback: guarantee final value even if rAF is throttled (offscreen capture)
    const done = setTimeout(() => setN(value), dur + 250);
    return () => { cancelAnimationFrame(raf.current); clearTimeout(done); };
  }, [start, value, dur]);
  const display = format ? format(n) : Math.round(n).toLocaleString("ru-RU");
  return <span className={className}>{display}</span>;
}

/* ---- generic SVG hover tooltip ---- */
function useTooltip() {
  const [tip, setTip] = useState(null); // {x,y,node}
  const Tip = ({ container }) =>
    tip ? (
      <div className={"tooltip show"} style={{ left: tip.x, top: tip.y }}>
        {tip.node}
      </div>
    ) : null;
  return { tip, setTip, Tip };
}

/* ============================================================
   Area + line chart (community growth / pace trend)
   props: data [{label, value}], color, fill, formatVal, formatLabel, height, yInvert
   ============================================================ */
function AreaLine({ data, color = "var(--brand)", fill = true, height = 220, formatVal, formatLabel, yInvert = false, baselineLabel }) {
  const [ref, seen] = useInView();
  const [hover, setHover] = useState(null);
  const W = 1000;
  const H = 320;
  const padL = 8, padR = 8, padT = 18, padB = 28;
  const vals = data.map((d) => d.value);
  const min = Math.min(...vals);
  const max = Math.max(...vals);
  const span = max - min || 1;
  const innerW = W - padL - padR;
  const innerH = H - padT - padB;
  const x = (i) => padL + (i / (data.length - 1)) * innerW;
  const y = (v) => {
    const t = (v - min) / span; // 0..1
    const tt = yInvert ? t : 1 - t;
    return padT + tt * innerH;
  };
  const pts = data.map((d, i) => [x(i), y(d.value)]);
  const linePath = pts.map((p, i) => `${i === 0 ? "M" : "L"}${p[0].toFixed(1)},${p[1].toFixed(1)}`).join(" ");
  const areaPath = `${linePath} L${x(data.length - 1).toFixed(1)},${H - padB} L${padL},${H - padB} Z`;
  const len = 2600;
  const gid = "g" + Math.random().toString(36).slice(2, 7);

  // x labels: ~6 ticks
  const tickEvery = Math.ceil(data.length / 6);

  return (
    <div className={"chart" + (seen ? " draw" : "")} ref={ref} style={{ position: "relative" }}>
      <svg viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none" style={{ height }}>
        <defs>
          <linearGradient id={gid} x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor={color} stopOpacity="0.28" />
            <stop offset="100%" stopColor={color} stopOpacity="0.02" />
          </linearGradient>
        </defs>
        {/* gridlines */}
        {[0, 0.25, 0.5, 0.75, 1].map((g) => (
          <line key={g} className="grid-line" x1={padL} x2={W - padR} y1={padT + g * innerH} y2={padT + g * innerH} opacity={g === 1 ? 1 : 0.5} />
        ))}
        {fill && <path className="area-path" d={areaPath} fill={`url(#${gid})`} style={{ opacity: seen ? 1 : 0, transitionDelay: "0.5s" }} />}
        <path className="line-path" d={linePath} stroke={color} style={{ "--len": len }} />
        {/* hover hit + markers */}
        {pts.map((p, i) => (
          <g key={i}>
            <rect
              x={x(i) - innerW / data.length / 2}
              y={0}
              width={innerW / data.length}
              height={H}
              fill="transparent"
              onMouseEnter={() => setHover(i)}
              onMouseLeave={() => setHover(null)}
              style={{ cursor: "pointer" }}
            />
            {hover === i && (
              <>
                <line x1={p[0]} x2={p[0]} y1={padT} y2={H - padB} stroke={color} strokeWidth="1" opacity="0.4" />
                <circle cx={p[0]} cy={p[1]} r="5" fill={color} stroke="var(--surface)" strokeWidth="2.5" />
              </>
            )}
          </g>
        ))}
        {data.map((d, i) =>
          i % tickEvery === 0 || i === data.length - 1 ? (
            <text key={i} className="axis-label" x={x(i)} y={H - 8} textAnchor={i === 0 ? "start" : i === data.length - 1 ? "end" : "middle"}>
              {formatLabel ? formatLabel(d, i) : d.label}
            </text>
          ) : null
        )}
      </svg>
      {hover != null && (
        <div
          className="tooltip show"
          style={{ left: `${(x(hover) / W) * 100}%`, top: `${(y(data[hover].value) / H) * 100}%` }}
        >
          <div className="tt-title">{formatLabel ? formatLabel(data[hover], hover) : data[hover].label}</div>
          <div className="tt-row">
            <span className="sw" style={{ background: color }}></span>
            <b>{formatVal ? formatVal(data[hover].value) : data[hover].value.toLocaleString("ru-RU")}</b>
          </div>
        </div>
      )}
    </div>
  );
}

/* ============================================================
   Histogram (distribution) with "you" marker
   props: buckets [{center,count}], youCenter, color, formatX, height
   ============================================================ */
function Histogram({ buckets, youCenter, formatX, height = 170, fasterThanPct }) {
  const [ref, seen] = useInView();
  const [hover, setHover] = useState(null);
  const max = Math.max(...buckets.map((b) => b.count));
  // find "you" bucket index (closest center)
  let youIdx = 0;
  let bestD = Infinity;
  buckets.forEach((b, i) => {
    const d = Math.abs(b.center - youCenter);
    if (d < bestD) { bestD = d; youIdx = i; }
  });
  const band = 1; // highlight a band around the mode? no — just you
  return (
    <div ref={ref} style={{ position: "relative" }}>
      <div className={"histo" + (seen ? " draw" : "")} style={{ height }}>
        {buckets.map((b, i) => {
          const h = (b.count / max) * 100;
          const isYou = i === youIdx;
          return (
            <div
              key={i}
              className={"histo-bar" + (isYou ? " you" : "")}
              style={{ height: `${h}%`, transitionDelay: `${i * 18}ms` }}
              onMouseEnter={() => setHover(i)}
              onMouseLeave={() => setHover(null)}
            >
              {isYou && <span className="you-flag">ВЫ · {formatX ? formatX(youCenter) : youCenter}</span>}
            </div>
          );
        })}
      </div>
      {/* x axis */}
      <div className="row between" style={{ marginTop: 8, fontFamily: "var(--font-mono)", fontSize: 10, color: "var(--faint)" }}>
        <span>{formatX ? formatX(buckets[0].center) : buckets[0].center}</span>
        <span>{formatX ? formatX(buckets[Math.floor(buckets.length / 2)].center) : ""}</span>
        <span>{formatX ? formatX(buckets[buckets.length - 1].center) : buckets[buckets.length - 1].center}</span>
      </div>
      {hover != null && (
        <div className="tooltip show" style={{ left: `${((hover + 0.5) / buckets.length) * 100}%`, top: 0 }}>
          <div className="tt-title">{formatX ? formatX(buckets[hover].center) : buckets[hover].center}</div>
          <div className="tt-row"><b>{buckets[hover].count.toLocaleString("ru-RU")}</b> финишей</div>
        </div>
      )}
    </div>
  );
}

/* ============================================================
   Ranked bars
   props: items [{name, sub, value, valueLabel, color, top, you}], max, accent
   ============================================================ */
function RankedBars({ items, valueSuffix, color = "var(--brand)", showRank = true }) {
  const [ref, seen] = useInView();
  const max = Math.max(...items.map((i) => i.value));
  return (
    <div className="rank-list" ref={ref}>
      {items.map((it, i) => {
        const c = it.color || color;
        return (
          <div key={i} className={"rank-row" + (i === 0 ? " top" : "") + (it.you ? " you-row" : "")} style={it.you ? { background: "color-mix(in oklch, var(--gold) 12%, transparent)" } : null}>
            {showRank && <div className="rank-num">{i + 1}</div>}
            <div className="rank-main">
              <div className="rank-name">{it.name}{it.you && <span style={{ color: "var(--gold-ink)", fontWeight: 700 }}> · вы</span>}</div>
              {it.sub && <div className="rank-sub">{it.sub}</div>}
              <div className="rank-bar-track">
                <div className="rank-bar-fill" style={{ background: it.you ? "var(--gold)" : c, transform: seen ? `scaleX(${it.value / max})` : "scaleX(0)", transitionDelay: `${i * 60}ms` }}></div>
              </div>
            </div>
            <div className="rank-val">
              {it.value.toLocaleString("ru-RU")}
              {valueSuffix && <small>{valueSuffix}</small>}
            </div>
          </div>
        );
      })}
    </div>
  );
}

/* ============================================================
   Donut (platform split)
   props: segments [{name, value, color}], total, size
   ============================================================ */
function Donut({ segments, size = 168, thickness = 26 }) {
  const [ref, seen] = useInView();
  const total = segments.reduce((s, x) => s + x.value, 0);
  const r = (size - thickness) / 2;
  const c = 2 * Math.PI * r;
  let acc = 0;
  return (
    <div className="donut-wrap" ref={ref}>
      <svg width={size} height={size} viewBox={`0 0 ${size} ${size}`} style={{ flexShrink: 0 }}>
        <circle cx={size / 2} cy={size / 2} r={r} fill="none" stroke="var(--surface-2)" strokeWidth={thickness} />
        {segments.map((s, i) => {
          const frac = s.value / total;
          const dash = frac * c;
          const off = acc;
          acc += dash;
          return (
            <circle
              key={i}
              cx={size / 2}
              cy={size / 2}
              r={r}
              fill="none"
              stroke={s.color}
              strokeWidth={thickness}
              strokeDasharray={`${seen ? dash : 0} ${c}`}
              strokeDashoffset={-off}
              transform={`rotate(-90 ${size / 2} ${size / 2})`}
              style={{ transition: `stroke-dasharray 1s var(--ease) ${i * 0.18}s` }}
              strokeLinecap="butt"
            />
          );
        })}
        <text x={size / 2} y={size / 2 - 4} textAnchor="middle" style={{ fontFamily: "var(--font-display)", fontWeight: 800, fontSize: 26, fill: "var(--ink)" }} className="tabular">
          {total >= 1000 ? (total / 1000).toFixed(0) + "K" : total}
        </text>
        <text x={size / 2} y={size / 2 + 16} textAnchor="middle" style={{ fontFamily: "var(--font-mono)", fontSize: 10, fill: "var(--muted)", letterSpacing: "0.08em" }}>
          ФИНИШЕЙ
        </text>
      </svg>
      <div className="donut-legend">
        {segments.map((s, i) => (
          <div className="donut-leg-row" key={i}>
            <span className="sw" style={{ background: s.color }}></span>
            <span className="donut-leg-name">{s.name}</span>
            <span className="donut-leg-val tabular">{s.value.toLocaleString("ru-RU")}</span>
            <span className="donut-leg-pct">{((s.value / total) * 100).toFixed(1)}%</span>
          </div>
        ))}
      </div>
    </div>
  );
}

/* ============================================================
   Heat calendar (Saturdays) — single row of weeks
   props: cells [0..3], formatWeek
   ============================================================ */
function HeatCalendar({ cells }) {
  const [ref, seen] = useInView();
  const colors = ["var(--surface-2)", "var(--vol)", "var(--run)", "var(--gold)"];
  const labels = ["нет", "волонтёрство", "пробежка", "пробежка + PR"];
  const [hover, setHover] = useState(null);
  return (
    <div ref={ref}>
      <div style={{ display: "grid", gridAutoFlow: "column", gridTemplateRows: "1fr", gap: 4, position: "relative" }}>
        {cells.map((v, i) => (
          <div
            key={i}
            title={`${52 - i} нед. назад · ${labels[v]}`}
            onMouseEnter={() => setHover(i)}
            onMouseLeave={() => setHover(null)}
            style={{
              aspectRatio: "1",
              borderRadius: 4,
              background: colors[v],
              opacity: seen ? (v === 0 ? 0.7 : 1) : 0,
              transform: seen ? "scale(1)" : "scale(0.4)",
              transition: `opacity 0.4s ${i * 9}ms, transform 0.4s var(--ease) ${i * 9}ms`,
              cursor: "pointer",
              outline: hover === i ? "2px solid var(--brand)" : "none",
            }}
          ></div>
        ))}
      </div>
      <div className="row between" style={{ marginTop: 10 }}>
        <span style={{ fontFamily: "var(--font-mono)", fontSize: 10, color: "var(--faint)" }}>52 недели назад</span>
        <div className="heat-legend">
          {labels.map((l, i) => (
            <span key={i} className="row" style={{ gap: 4 }}>
              <span className="sw" style={{ background: colors[i] }}></span>
              <span style={{ fontSize: 11 }}>{l}</span>
            </span>
          ))}
        </div>
        <span style={{ fontFamily: "var(--font-mono)", fontSize: 10, color: "var(--faint)" }}>сегодня</span>
      </div>
    </div>
  );
}

/* ============================================================
   Stacked mini-bars (activity by month: runs/vol)
   ============================================================ */
function MiniBars({ data, formatLabel }) {
  const [ref, seen] = useInView();
  const [hover, setHover] = useState(null);
  const max = Math.max(...data.map((d) => d.runs + d.vol), 1);
  return (
    <div ref={ref} style={{ position: "relative" }}>
      <div style={{ display: "grid", gridTemplateColumns: `repeat(${data.length}, 1fr)`, gap: 6, alignItems: "end", height: 130 }}>
        {data.map((d, i) => {
          const total = d.runs + d.vol;
          const hh = (total / max) * 100;
          return (
            <div key={i} style={{ display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "flex-end", height: "100%" }}
                 onMouseEnter={() => setHover(i)} onMouseLeave={() => setHover(null)}>
              <div style={{ width: "100%", maxWidth: 22, height: `${hh}%`, display: "flex", flexDirection: "column", justifyContent: "flex-end", minHeight: total ? 3 : 0 }}>
                {d.vol > 0 && <span style={{ flex: d.vol, background: "var(--vol)", borderRadius: "4px 4px 0 0", transform: seen ? "scaleY(1)" : "scaleY(0)", transformOrigin: "bottom", transition: `transform 0.6s var(--ease) ${i * 35}ms` }}></span>}
                {d.runs > 0 && <span style={{ flex: d.runs, background: "var(--run)", borderRadius: d.vol > 0 ? 0 : "4px 4px 0 0", transform: seen ? "scaleY(1)" : "scaleY(0)", transformOrigin: "bottom", transition: `transform 0.6s var(--ease) ${i * 35}ms` }}></span>}
              </div>
              <span style={{ marginTop: 6, fontFamily: "var(--font-mono)", fontSize: 9, color: "var(--faint)" }}>{formatLabel ? formatLabel(d, i) : ""}</span>
            </div>
          );
        })}
      </div>
      {hover != null && (
        <div className="tooltip show" style={{ left: `${((hover + 0.5) / data.length) * 100}%`, top: 0 }}>
          <div className="tt-title">{formatLabel ? formatLabel(data[hover], hover) : ""}</div>
          <div className="tt-row"><span className="sw" style={{ background: "var(--run)" }}></span>{data[hover].runs} пробежек</div>
          <div className="tt-row"><span className="sw" style={{ background: "var(--vol)" }}></span>{data[hover].vol} волонтёрств</div>
        </div>
      )}
    </div>
  );
}

Object.assign(window, { useInView, CountUp, AreaLine, Histogram, RankedBars, Donut, HeatCalendar, MiniBars });
