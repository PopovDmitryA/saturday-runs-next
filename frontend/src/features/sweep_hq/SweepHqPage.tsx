import { useEffect, useMemo, useRef, useState } from "react";
import "./sweepHq.css";

const REFRESH_MS = 180_000; // раз в 3 минуты
// Прошлый снимок держим в localStorage, чтобы дельты (стрелки прироста, динамика
// скорости) не обнулялись при перезагрузке страницы. Берём его как «прошлый»
// только если он свежий (~в пределах одного цикла), иначе дельта была бы враньём.
const SNAP_FRESH_MS = 240_000;
const snapKey = (token: string) => `sweep-hq-snap:${token}`;

type Bot = {
  name?: string;
  proxy?: string;
  account?: string;
  status: "working" | "queued" | "cooldown" | "off";
  cooldown_hours: number;
  collected_total: number;
  active_seconds?: number;
  delay_sec: number;
  ban_level: number;
  last_ok_at: string | null;
  captcha_total?: number;
  captcha_solved?: number;
  last_captcha_at?: string | null;
};

type SweepData = {
  progress: {
    done: number;
    total: number;
    remaining: number;
    pct: number;
    collected: number;
    in_processing: number;
    runs: number;
  };
  rate_24h: number;
  rate_1h: number;
  parse_rate_24h: number;
  parse_rate_1h: number;
  forecast: { days: number | null; date: string | null };
  // Время расчёта снимка на бэкенде. null — посчитано на месте (снимка ещё нет).
  snapshot_at?: string | null;
  vpn: Bot[];
  free: {
    summary: { total: number; active: number; cooldown: number; collected: number };
    top: Bot[];
  };
};

const fmt = (n: number) => n.toLocaleString("ru-RU");

function fmtDuration(sec: number): string {
  if (!sec) return "—";
  const h = Math.floor(sec / 3600);
  const m = Math.floor((sec % 3600) / 60);
  if (h >= 24) {
    const d = Math.floor(h / 24);
    return `${d}д ${h % 24}ч`;
  }
  return h ? `${h}ч ${m}м` : `${m}м`;
}

function fmtDate(iso: string | null): string {
  if (!iso) return "—";
  const d = new Date(iso);
  return d.toLocaleDateString("ru-RU", { day: "2-digit", month: "long", year: "numeric" });
}

// Имя VPN-выхода начинается с 2-буквенного кода страны (de, at, fi, pl2, deh2…).
const PREFIX_FLAG: Record<string, string> = {
  de: "🇩🇪", at: "🇦🇹", it: "🇮🇹", lt: "🇱🇹", tr: "🇹🇷", us: "🇺🇸",
  pl: "🇵🇱", nl: "🇳🇱", fi: "🇫🇮", ee: "🇪🇪", se: "🇸🇪", gf: "🌐",
};
function flagFor(name: string): string {
  if (name.toLowerCase().startsWith("mac")) return "💻";
  const m = name.match(/^([a-z]{2})/i);
  if (!m) return "🏳️";
  const code = m[1].toLowerCase();
  if (PREFIX_FLAG[code]) return PREFIX_FLAG[code];
  const A = 0x1f1e6;
  return String.fromCodePoint(A + code.charCodeAt(0) - 97, A + code.charCodeAt(1) - 97);
}

const STATUS_META: Record<Bot["status"], { dot: string; label: string }> = {
  working: { dot: "🟢", label: "работает" },
  queued: { dot: "🔵", label: "в очереди" },
  cooldown: { dot: "🟡", label: "отлёжка" },
  off: { dot: "🔴", label: "выключен" },
};

function StatusCell({ bot }: { bot: Bot }) {
  const meta = STATUS_META[bot.status];
  return (
    <span className={`hq-status hq-status--${bot.status}`}>
      <span className="hq-status__dot">{meta.dot}</span>
      {meta.label}
      {bot.status === "cooldown" && bot.cooldown_hours > 0 && (
        <span className="hq-status__cd"> {bot.cooldown_hours.toFixed(1)}ч</span>
      )}
    </span>
  );
}

function fmtMoscow(d: Date): string {
  return (
    d.toLocaleTimeString("ru-RU", {
      timeZone: "Europe/Moscow",
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit",
    }) + " МСК"
  );
}

function fmtMoscowDateTime(iso: string | null): string {
  if (!iso) return "—";
  return new Date(iso).toLocaleString("ru-RU", {
    timeZone: "Europe/Moscow",
    day: "2-digit",
    month: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });
}

function fmtCountdown(s: number): string {
  const m = Math.floor(s / 60);
  const sec = s % 60;
  return `${m}:${String(sec).padStart(2, "0")}`;
}

function Delta({ value }: { value: number }) {
  if (!value || value <= 0) return null;
  return <span className="hq-delta">↑{fmt(value)}</span>;
}

// Знаковая дельта темпа: ускорились — зелёная ↑, замедлились — жёлтая ↓.
function RateDelta({ value }: { value: number }) {
  if (!value) return null;
  const up = value > 0;
  return (
    <span className={up ? "hq-fdelta hq-fdelta--good" : "hq-fdelta hq-fdelta--bad"}>
      {up ? "↑" : "↓"} {fmt(Math.abs(value))}
    </span>
  );
}

function collectedMap(bots?: Bot[]): Map<string, number> {
  const m = new Map<string, number>();
  bots?.forEach((b, i) => m.set(b.name ?? b.proxy ?? String(i), b.collected_total));
  return m;
}

function loadSnap(token: string): SweepData | null {
  try {
    const raw = localStorage.getItem(snapKey(token));
    if (!raw) return null;
    const parsed = JSON.parse(raw) as { at: number; data: SweepData };
    if (Date.now() - parsed.at > SNAP_FRESH_MS) return null;
    return parsed.data;
  } catch {
    return null;
  }
}

function saveSnap(token: string, data: SweepData): void {
  try {
    localStorage.setItem(snapKey(token), JSON.stringify({ at: Date.now(), data }));
  } catch {
    /* приватный режим / переполнение — не критично */
  }
}

const MEDALS = ["🥇", "🥈", "🥉"];

function BotTable({
  title,
  subtitle,
  bots,
  showUptime,
  showFlag,
  showCaptcha = false,
  prevMap,
}: {
  title: string;
  subtitle: React.ReactNode;
  bots: Bot[];
  showUptime: boolean;
  showFlag: boolean;
  showCaptcha?: boolean;
  prevMap: Map<string, number>;
}) {
  const [workingOnly, setWorkingOnly] = useState(false);
  const shown = workingOnly ? bots.filter((b) => b.status === "working") : bots;
  return (
    <section className="hq-card">
      <header className="hq-card__head">
        <h2>{title}</h2>
        <span className="hq-card__sub">{subtitle}</span>
      </header>
      <div className="hq-tablewrap">
        <table className="hq-table">
          <thead>
            <tr>
              <th className="hq-th-rank">#</th>
              <th>Бот</th>
              <th
                className="hq-th-sort"
                onClick={() => setWorkingOnly((v) => !v)}
                title="Показать только работающих"
              >
                Статус {workingOnly ? "🟢✓" : "▾"}
              </th>
              <th className="hq-num">Задержка</th>
              {showUptime && <th className="hq-num">В работе</th>}
              {showCaptcha && <th className="hq-num">Капчи</th>}
              <th className="hq-num">Атлетов</th>
            </tr>
          </thead>
          <tbody>
            {shown.map((bot, i) => {
              const id = bot.name ?? bot.proxy ?? String(i);
              return (
                <tr key={id} className={i < 3 && bot.collected_total > 0 ? "hq-row--top" : ""}>
                  <td className="hq-th-rank">
                    {bot.collected_total > 0 && i < 3 ? MEDALS[i] : i + 1}
                  </td>
                  <td className="hq-bot">
                    {showFlag && <span className="hq-bot__flag">{flagFor(id)}</span>}
                    <span className="hq-bot__name">{id}</span>
                    {bot.account && bot.account !== "free" && (
                      <span className="hq-bot__acc">{bot.account}</span>
                    )}
                  </td>
                  <td>
                    <StatusCell bot={bot} />
                  </td>
                  <td className="hq-num hq-muted">{bot.delay_sec}с</td>
                  {showUptime && <td className="hq-num">{fmtDuration(bot.active_seconds ?? 0)}</td>}
                  {showCaptcha && (
                    <td className="hq-num">
                      {bot.captcha_total ? (
                        <span
                          className="hq-hint hq-captcha"
                          data-hint={`Решено ${bot.captcha_solved ?? 0} из ${bot.captcha_total}. Последняя: ${fmtMoscowDateTime(bot.last_captcha_at ?? null)}`}
                        >
                          {bot.captcha_solved ?? 0}/{bot.captcha_total}
                        </span>
                      ) : (
                        <span className="hq-muted">—</span>
                      )}
                    </td>
                  )}
                  <td className="hq-num hq-collected">
                    {fmt(bot.collected_total)}
                    <Delta value={bot.collected_total - (prevMap.get(id) ?? bot.collected_total)} />
                  </td>
                </tr>
              );
            })}
            {shown.length === 0 && (
              <tr>
                <td colSpan={(showUptime ? 6 : 5) + (showCaptcha ? 1 : 0)} className="hq-empty">
                  {workingOnly ? "сейчас никто не работает" : "пока пусто — прогрев"}
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </section>
  );
}

type RateHour = { hour: string; collected: number };

function fmtHourLabel(iso: string): string {
  return new Date(iso).toLocaleString("ru-RU", {
    timeZone: "Europe/Moscow",
    day: "2-digit",
    month: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
}

/** Метка часа покороче — для подписей оси, где важен только день и час. */
function fmtHourShort(iso: string): string {
  return new Date(iso).toLocaleString("ru-RU", {
    timeZone: "Europe/Moscow",
    day: "2-digit",
    month: "2-digit",
    hour: "2-digit",
  });
}

/** Круглый потолок оси: 8 700 -> 9 000, 27 400 -> 30 000. Сетка с такими
 *  числами читается быстрее, чем с 8 743.
 *
 *  Шагов нарочно много: с грубой шкалой (1-2-5-10) максимум в 27 тысяч задирал
 *  потолок до пятидесяти, и график ужимался в нижнюю половину поля. */
function niceCeil(v: number): number {
  if (v <= 0) return 1;
  const pow = Math.pow(10, Math.floor(Math.log10(v)));
  const n = v / pow;
  const step = [1, 1.2, 1.5, 2, 2.5, 3, 4, 5, 6, 8, 10].find((c) => n <= c) ?? 10;
  return step * pow;
}

/** Монотонная кубическая интерполяция (Фрич–Карлсон).
 *
 *  Обычный сплайн на всплесках вылетает за пределы данных и рисует провалы
 *  ниже нуля, которых не было. Монотонный по построению не выходит за
 *  соседние точки, поэтому сглаживание не выдумывает значений.
 */
function monotonePath(xs: number[], ys: number[]): string {
  const n = xs.length;
  if (n < 2) return "";
  const dx: number[] = [];
  const slope: number[] = [];
  for (let i = 0; i < n - 1; i += 1) {
    dx[i] = xs[i + 1] - xs[i];
    slope[i] = (ys[i + 1] - ys[i]) / dx[i];
  }
  const t: number[] = new Array(n);
  t[0] = slope[0];
  t[n - 1] = slope[n - 2];
  for (let i = 1; i < n - 1; i += 1) {
    if (slope[i - 1] * slope[i] <= 0) {
      t[i] = 0;
    } else {
      const w1 = 2 * dx[i] + dx[i - 1];
      const w2 = dx[i] + 2 * dx[i - 1];
      t[i] = (w1 + w2) / (w1 / slope[i - 1] + w2 / slope[i]);
    }
  }
  let d = `M${xs[0].toFixed(1)},${ys[0].toFixed(1)}`;
  for (let i = 0; i < n - 1; i += 1) {
    const h = dx[i] / 3;
    d += ` C${(xs[i] + h).toFixed(1)},${(ys[i] + t[i] * h).toFixed(1)}`;
    d += ` ${(xs[i + 1] - h).toFixed(1)},${(ys[i + 1] - t[i + 1] * h).toFixed(1)}`;
    d += ` ${xs[i + 1].toFixed(1)},${ys[i + 1].toFixed(1)}`;
  }
  return d;
}

function RateChart({ points }: { points: RateHour[] }) {
  const [hoverIdx, setHoverIdx] = useState<number | null>(null);
  const width = 760;
  const height = 260;
  const plot = { left: 58, right: 744, top: 18, bottom: 214 };

  const geom = useMemo(() => {
    if (points.length < 2) return null;
    const values = points.map((p) => p.collected);
    const top = niceCeil(Math.max(1, ...values));
    const last = points.length - 1;
    const stepX = (plot.right - plot.left) / last;
    const xs = points.map((_, i) => plot.left + i * stepX);
    const ys = values.map((v) => plot.bottom - (v / top) * (plot.bottom - plot.top));
    // Больше двух сотен точек — они и так сливаются в сплошную линию,
    // сглаживать нечего, а строка пути выросла бы в разы.
    const line = points.length > 200
      ? `M${xs.map((x, i) => `${x.toFixed(1)},${ys[i].toFixed(1)}`).join(" L")}`
      : monotonePath(xs, ys);
    const area = `${line} L${xs[last].toFixed(1)},${plot.bottom} L${xs[0].toFixed(1)},${plot.bottom} Z`;
    const avg = values.reduce((a, b) => a + b, 0) / values.length;
    const peak = values.indexOf(Math.max(...values));
    return { top, last, stepX, xs, ys, line, area, avg, peak, values };
  }, [points]);

  if (!geom) {
    return <p className="hq-muted hq-pad">Пока недостаточно данных для графика.</p>;
  }

  const { top, last, stepX, xs, ys, line, area, avg, peak } = geom;
  const avgLabel = `среднее ${fmt(Math.round(avg))}`;
  const yFor = (v: number) => plot.bottom - (v / top) * (plot.bottom - plot.top);
  const grid = [0, 0.25, 0.5, 0.75, 1].map((s) => top * s);
  const labelEvery = Math.max(1, Math.ceil(points.length / 7));
  // Последний час подписываем всегда, а очередную регулярную подпись рядом с ним
  // пропускаем — иначе они налезают друг на друга у правого края.
  const showLabel = (i: number) =>
    i === last || (i % labelEvery === 0 && last - i > labelEvery * 0.55);

  const handleMove = (e: React.MouseEvent<SVGSVGElement>) => {
    const rect = e.currentTarget.getBoundingClientRect();
    const px = (e.clientX - rect.left) * (width / rect.width);
    setHoverIdx(Math.min(last, Math.max(0, Math.round((px - plot.left) / stepX))));
  };

  const hp = hoverIdx != null ? points[hoverIdx] : null;
  // Подсказку прижимаем к краям поля, иначе на первых и последних часах
  // она уезжала бы за границу картинки.
  const tipW = 168;
  const tipX = hoverIdx == null ? 0 : Math.min(plot.right - tipW, Math.max(plot.left, xs[hoverIdx] - tipW / 2));

  return (
    <div className="hq-ratechart">
      <svg
        viewBox={`0 0 ${width} ${height}`}
        role="img"
        aria-label="Скорость сбора по часам"
        onMouseMove={handleMove}
        onMouseLeave={() => setHoverIdx(null)}
      >
        <defs>
          <linearGradient id="hq-rc-fill" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor="#38bdf8" stopOpacity="0.38" />
            <stop offset="60%" stopColor="#38bdf8" stopOpacity="0.10" />
            <stop offset="100%" stopColor="#38bdf8" stopOpacity="0" />
          </linearGradient>
          <linearGradient id="hq-rc-line" x1="0" y1="0" x2="1" y2="0">
            <stop offset="0%" stopColor="#818cf8" />
            <stop offset="55%" stopColor="#38bdf8" />
            <stop offset="100%" stopColor="#34d399" />
          </linearGradient>
          <filter id="hq-rc-glow" x="-20%" y="-40%" width="140%" height="180%">
            <feGaussianBlur stdDeviation="4" result="b" />
            <feMerge>
              <feMergeNode in="b" />
              <feMergeNode in="SourceGraphic" />
            </feMerge>
          </filter>
        </defs>

        {grid.map((v) => (
          <g key={v} className="hq-rc-grid">
            <line
              x1={plot.left}
              x2={plot.right}
              y1={yFor(v)}
              y2={yFor(v)}
              strokeDasharray={v === 0 ? undefined : "2 6"}
            />
            <text x={plot.left - 10} y={yFor(v) + 4} textAnchor="end">
              {fmt(Math.round(v))}
            </text>
          </g>
        ))}

        <path d={area} fill="url(#hq-rc-fill)" />
        <path
          d={line}
          fill="none"
          stroke="url(#hq-rc-line)"
          strokeWidth="2.4"
          strokeLinejoin="round"
          strokeLinecap="round"
          filter="url(#hq-rc-glow)"
        />

        <g className="hq-rc-avg">
          <line x1={plot.left} x2={plot.right} y1={yFor(avg)} y2={yFor(avg)} strokeDasharray="6 5" />
          {/* Подпись на подложке и у левого края: на самой линии её перекрывал
              график, а у правого края — финальный всплеск. */}
          <g transform={`translate(${plot.left + 8}, ${Math.max(plot.top + 14, yFor(avg) - 8)})`}>
            <rect x="0" y="-11" width={avgLabel.length * 5.4 + 14} height="15" rx="7.5" />
            <text x="7" y="0">{avgLabel}</text>
          </g>
        </g>

        {points.length <= 60 &&
          points.map((p, i) => (
            <circle key={p.hour} className="hq-rc-dot" cx={xs[i]} cy={ys[i]} r={2.4} />
          ))}
        <circle className="hq-rc-peak" cx={xs[peak]} cy={ys[peak]} r={4} />

        {points.map(
          (p, i) =>
            showLabel(i) && (
              <text key={p.hour} className="hq-rc-xlab" x={xs[i]} y={height - 8} textAnchor="middle">
                {fmtHourShort(p.hour)}
              </text>
            ),
        )}

        {hoverIdx != null && hp && (
          <g className="hq-rc-hover">
            <line x1={xs[hoverIdx]} x2={xs[hoverIdx]} y1={plot.top} y2={plot.bottom} />
            <circle cx={xs[hoverIdx]} cy={ys[hoverIdx]} r={5} />
            <g transform={`translate(${tipX}, ${plot.top})`}>
              <rect width={tipW} height="42" rx="9" />
              <text x="12" y="17">{fmtHourLabel(hp.hour)}</text>
              <text x="12" y="33" className="hq-rc-tipval">
                {fmt(hp.collected)} атлетов
              </text>
            </g>
          </g>
        )}
      </svg>
    </div>
  );
}

function RateHistoryTab({ token }: { token: string }) {
  const [rows, setRows] = useState<RateHour[] | null>(null);
  const [error, setError] = useState(false);
  const [hours, setHours] = useState(48);
  useEffect(() => {
    let alive = true;
    fetch(`/api/sweep-hq/rate-history?token=${encodeURIComponent(token)}&hours=${hours}`, {
      credentials: "same-origin",
    })
      .then((res) => {
        if (!alive) return;
        if (!res.ok) {
          setError(true);
          return;
        }
        setError(false);
        return res.json();
      })
      .then((d: { hours: RateHour[] } | undefined) => {
        if (!alive || !d) return;
        // Текущий час ещё не закончился — его столбец всегда занижен (частичные
        // данные), на графике это выглядит как обвал темпа. Прячем его целиком.
        const curHourStart = Math.floor(Date.now() / 3_600_000) * 3_600_000;
        setRows(d.hours.filter((p) => new Date(p.hour).getTime() < curHourStart));
      })
      .catch(() => {
        if (alive) setError(true);
      });
    return () => {
      alive = false;
    };
  }, [token, hours]);

  return (
    <section className="hq-card">
      <header className="hq-card__head">
        <h2>📈 Динамика скорости сбора</h2>
        <div className="hq-calc__unit">
          {[24, 48, 24 * 7, 0].map((h) => (
            <button
              key={h}
              className={hours === h ? "hq-ubtn hq-ubtn--on" : "hq-ubtn"}
              onClick={() => setHours(h)}
            >
              {h === 0 ? "весь период" : h < 24 * 7 ? `${h}ч` : "7д"}
            </button>
          ))}
        </div>
      </header>
      {error && <p className="hq-muted hq-pad">Не удалось загрузить.</p>}
      {!error && !rows && <p className="hq-muted hq-pad">Загрузка…</p>}
      {!error && rows && <RateChart points={rows} />}
    </section>
  );
}

function Calculator({ remaining }: { remaining: number }) {
  const [rate, setRate] = useState<string>("10000");
  const [unit, setUnit] = useState<"hour" | "day">("hour");
  const result = useMemo(() => {
    const r = Number(rate.replace(/\s/g, ""));
    if (!r || r <= 0) return null;
    // прогноз всегда в днях+дате; при «в час» темп домножаем на 24
    const perDay = unit === "hour" ? r * 24 : r;
    const days = remaining / perDay;
    const date = new Date(Date.now() + days * 86400_000);
    return { days, date };
  }, [rate, unit, remaining]);
  return (
    <div className="hq-calc">
      <div className="hq-calc__row">
        <label htmlFor="hq-rate">Калькулятор темпа:</label>
        <div className="hq-calc__unit">
          <button
            className={unit === "hour" ? "hq-ubtn hq-ubtn--on" : "hq-ubtn"}
            onClick={() => setUnit("hour")}
          >
            в час
          </button>
          <button
            className={unit === "day" ? "hq-ubtn hq-ubtn--on" : "hq-ubtn"}
            onClick={() => setUnit("day")}
          >
            в день
          </button>
        </div>
        <div className="hq-calc__input">
          <input
            id="hq-rate"
            inputMode="numeric"
            value={rate}
            onChange={(e) => setRate(e.target.value)}
          />
          <span>атлетов / {unit === "hour" ? "час" : "день"}</span>
        </div>
      </div>
      {result ? (
        <p className="hq-calc__out">
          При таком темпе — <b>{Math.ceil(result.days).toLocaleString("ru-RU")} дней</b>, финиш{" "}
          <b>{fmtDate(result.date.toISOString())}</b>
        </p>
      ) : (
        <p className="hq-calc__out hq-muted">введи число больше нуля</p>
      )}
    </div>
  );
}

export function SweepHqPage({ token }: { token: string }) {
  const [data, setData] = useState<SweepData | null>(null);
  const [prev, setPrev] = useState<SweepData | null>(() => loadSnap(token));
  const [updatedAt, setUpdatedAt] = useState<Date | null>(null);
  const [error, setError] = useState<number | null>(null);
  const [tab, setTab] = useState<"fleet" | "rate">("fleet");
  const [now, setNow] = useState<number>(() => Date.now());
  const curRef = useRef<SweepData | null>(loadSnap(token));

  useEffect(() => {
    let alive = true;
    const doFetch = async (): Promise<SweepData | null> => {
      try {
        const res = await fetch(`/api/sweep-hq?token=${encodeURIComponent(token)}`, {
          credentials: "same-origin",
        });
        if (!alive) return null;
        if (!res.ok) {
          setError(res.status);
          return null;
        }
        setError(null);
        return (await res.json()) as SweepData;
      } catch {
        if (alive) setError(0);
        return null;
      }
    };
    const apply = (next: SweepData) => {
      // Данные считает снимок на бэкенде раз в 3 минуты. Если он не обновился
      // (расписание встало, база недоступна), числа придут те же — и дельты
      // показали бы честный ноль, неотличимый от «за 3 минуты ничего не собрали».
      // Поэтому prev двигаем только когда снимок действительно сменился.
      const same =
        next.snapshot_at != null && next.snapshot_at === curRef.current?.snapshot_at;
      if (!same) {
        setPrev(curRef.current);
        saveSnap(token, next);
      }
      curRef.current = next;
      setData(next);
      setUpdatedAt(new Date());
    };
    // немедленная загрузка при входе
    doFetch().then((d) => {
      if (d) apply(d);
    });

    // Тикер раз в секунду: обновляет часы (для анимаций/отсчёта), ПРЕФЕТЧит данные
    // за ~5с до 3-мин границы и ПОКАЗЫВАЕТ их ровно на границе (в 0:00), а не через
    // пару секунд после. Раньше времени показанные данные не раскрываем.
    let prefetchedFor = -1;
    let pending: { at: number; data: SweepData } | null = null;
    let applied = Math.floor(Date.now() / REFRESH_MS);
    const tick = window.setInterval(() => {
      if (!alive) return;
      const nowMs = Date.now();
      setNow(nowMs);
      const bIndex = Math.floor(nowMs / REFRESH_MS);
      const nextIndex = bIndex + 1;
      if (nextIndex * REFRESH_MS - nowMs <= 5000 && prefetchedFor !== nextIndex) {
        prefetchedFor = nextIndex;
        doFetch().then((d) => {
          if (d) pending = { at: nextIndex, data: d };
        });
      }
      if (bIndex > applied && pending && pending.at <= bIndex) {
        apply(pending.data);
        pending = null;
        applied = bIndex;
      }
    }, 1000);
    return () => {
      alive = false;
      window.clearInterval(tick);
    };
  }, [token]);

  if (error === 404) {
    return (
      <div className="hq-root hq-center">
        <p className="hq-muted">Страница не найдена.</p>
      </div>
    );
  }
  if (!data) {
    return (
      <div className="hq-root hq-center">
        <p className="hq-muted">Загрузка табло…</p>
      </div>
    );
  }

  const p = data.progress;
  const freeSum = data.free.summary;
  const vpnWorking = data.vpn.filter((b) => b.status === "working").length;
  // Снимок пересчитывается раз в 3 минуты; допускаем задержку в два цикла,
  // дальше это уже поломка, и о ней лучше сказать, чем показывать нули дельт.
  const snapAgeMs = data?.snapshot_at ? now - new Date(data.snapshot_at).getTime() : null;
  const staleMinutes =
    snapAgeMs != null && snapAgeMs > 2 * REFRESH_MS ? Math.floor(snapAgeMs / 60_000) : null;

  const prevVpnMap = collectedMap(prev?.vpn);
  const prevFreeMap = collectedMap(prev?.free.top);
  // Сумма собранного флотом целиком + дельта за интервал опроса — виден вклад
  // платных/бесплатных за последние 3 минуты, не только общий счётчик.
  const vpnCollectedSum = data.vpn.reduce((s, b) => s + b.collected_total, 0);
  const prevVpnCollectedSum = prev ? prev.vpn.reduce((s, b) => s + b.collected_total, 0) : null;
  const vpnCollectedDelta = prevVpnCollectedSum != null ? vpnCollectedSum - prevVpnCollectedSum : 0;
  const prevFreeCollected = prev ? prev.free.summary.collected : null;
  const freeCollectedDelta = prevFreeCollected != null ? freeSum.collected - prevFreeCollected : 0;
  const collectedDelta = prev ? p.collected - prev.progress.collected : 0;
  const rate1hDelta = prev ? data.rate_1h - prev.rate_1h : 0;
  const rate24hDelta = prev ? data.rate_24h - prev.rate_24h : 0;
  const parseRate1hDelta = prev ? data.parse_rate_1h - prev.parse_rate_1h : 0;
  const runsDelta = prev ? p.runs - prev.progress.runs : 0;
  // Отсчёт до следующей 3-минутной границы стенных часов — не зависит от момента
  // загрузки страницы, поэтому перезагрузка его не сбрасывает.
  const secondsLeft = Math.ceil((REFRESH_MS - (now % REFRESH_MS)) / 1000);
  const imminent = secondsLeft > 0 && secondsLeft <= 10;
  const daysDelta =
    prev && prev.forecast.days != null && data.forecast.days != null
      ? data.forecast.days - prev.forecast.days
      : null;

  return (
    <div className={imminent ? "hq-root hq-hot" : "hq-root"}>
      <div className="hq-inner">
        <div className={imminent ? "hq-topbar hq-topbar--hot" : "hq-topbar"}>
          <span className="hq-topbar__upd">
            🕐 обновлено {updatedAt ? fmtMoscow(updatedAt) : "…"}
            {staleMinutes != null && (
              <b className="hq-topbar__stale" title="Снимок на бэкенде не обновляется">
                {" "}
                · данные {staleMinutes} мин назад
              </b>
            )}
          </span>
          <span className="hq-topbar__next">
            {imminent ? "⚡ обновление через " : "следующее через "}
            <b className={imminent ? "hq-topbar__timer hq-topbar__timer--hot" : "hq-topbar__timer"}>
              {fmtCountdown(secondsLeft)}
            </b>
          </span>
        </div>

        <header className="hq-hero">
          <div className="hq-hero__title">
            <span className="hq-hero__emoji">🌍</span>
            <div>
              <h1>Мировой обход parkrun</h1>
              <p className="hq-muted">сбор атлетов со всей планеты · живое табло</p>
            </div>
          </div>

          <div
            className="hq-progress hq-hint"
            data-hint="Обработано + собрано ÷ всего ID в очереди (диапазон 751355…7 500 000). «Обработано» = страница уже прошла через любой из статусов результата, «собрано» входит сюда же."
          >
            <div className="hq-progress__bar">
              <div className="hq-progress__fill" style={{ width: `${Math.max(0.4, p.pct)}%` }} />
              <span className="hq-progress__label">
                {fmt(p.done)} / {fmt(p.total)} · {p.pct.toFixed(2)}%
              </span>
            </div>
            {collectedDelta > 0 && (
              <div className="hq-progress__delta">↑ {fmt(collectedDelta)} за 3 мин</div>
            )}
          </div>

          <div className="hq-stats">
            <div
              className="hq-stat hq-hint"
              data-hint="Страницы, которые мы уже скачали: распарсенные в БД плюс лежащие в папке файлы, ждущие обработки. «N в обработке» — скачаны в папку (статус collected), но ещё не распарсены."
            >
              <span className="hq-stat__num">{fmt(p.collected)}</span>
              <span className="hq-stat__cap">
                атлетов собрано
                {p.in_processing > 0 && (
                  <span className="hq-inproc"> ({fmt(p.in_processing)} в обработке)</span>
                )}
              </span>
            </div>
            <div
              className="hq-stat hq-hint"
              data-hint="Сумма всех строк в таблице runs — только из уже распарсенных атлетов. Файлы, ждущие обработки, сюда пока не входят: появятся после парсинга."
            >
              <span className="hq-stat__num">{fmt(p.runs)}</span>
              <span className="hq-stat__cap">забегов</span>
              <RateDelta value={runsDelta} />
            </div>
            <div
              className="hq-stat hq-stat--accent hq-hint"
              data-hint="Сколько страниц скачано за последний час (по времени фетча, fetched_at). Считает оба движка — бесплатные прокси (только качают в папку) и приватные VPN (качают и парсят на лету)."
            >
              <span className="hq-stat__num">{fmt(data.rate_1h)}</span>
              <span className="hq-stat__cap">сбор / час</span>
              <RateDelta value={rate1hDelta} />
            </div>
            <div
              className="hq-stat hq-stat--accent hq-hint"
              data-hint="То же самое, что «сбор / час», но за последние 24 часа. Именно этот темп используется для прогноза срока до финиша."
            >
              <span className="hq-stat__num">{fmt(data.rate_24h)}</span>
              <span className="hq-stat__cap">сбор / сутки</span>
              <RateDelta value={rate24hDelta} />
            </div>
            <div
              className="hq-stat hq-hint"
              data-hint="Сколько атлетов распарсено в БД за последний час (по parsed_at). Её кормит VPN-движок (парсит на лету) и офлайн-парсер, когда он запущен. Растёт, пока идёт парсинг, — а не падает."
            >
              <span className="hq-stat__num">{fmt(data.parse_rate_1h)}</span>
              <span className="hq-stat__cap">обработка / час</span>
              <RateDelta value={parseRate1hDelta} />
            </div>
            <div
              className="hq-stat hq-hint"
              data-hint="Осталось в очереди ÷ темп сбора за сутки. Грубая оценка при текущей скорости; растёт число VPN/прокси — срок падает."
            >
              <span className="hq-stat__num">
                {data.forecast.days != null
                  ? `${Math.ceil(data.forecast.days).toLocaleString("ru-RU")} дн`
                  : "—"}
              </span>
              <span className="hq-stat__cap">до финиша</span>
              {daysDelta != null && Math.abs(daysDelta) >= 0.5 && (
                <span
                  className={
                    daysDelta < 0 ? "hq-fdelta hq-fdelta--good" : "hq-fdelta hq-fdelta--bad"
                  }
                >
                  {daysDelta < 0 ? "↓" : "↑"} {fmt(Math.abs(Math.round(daysDelta)))} дн
                </span>
              )}
            </div>
            <div
              className="hq-stat hq-stat--date hq-hint"
              data-hint="Сегодняшняя дата + число дней «до финиша». Календарная проекция того же прогноза."
            >
              <span className="hq-stat__num">{fmtDate(data.forecast.date)}</span>
              <span className="hq-stat__cap">прогноз финиша</span>
            </div>
          </div>

          <Calculator remaining={p.remaining} />
        </header>

        <nav className="hq-tabs">
          <button
            className={tab === "fleet" ? "hq-tab hq-tab--on" : "hq-tab"}
            onClick={() => setTab("fleet")}
          >
            🤖 Флот ботов
          </button>
          <button
            className={tab === "rate" ? "hq-tab hq-tab--on" : "hq-tab"}
            onClick={() => setTab("rate")}
          >
            📈 Динамика
          </button>
        </nav>

        {tab === "rate" ? (
          <RateHistoryTab token={token} />
        ) : (
          <div className="hq-fleet">
            <BotTable
              title="🛡️ Приватные VPN"
            subtitle={
              <>
                {vpnWorking} в работе · {data.vpn.length} всего · собрали {fmt(vpnCollectedSum)}
                <RateDelta value={vpnCollectedDelta} />
              </>
            }
            bots={data.vpn}
            showUptime
            showFlag
            showCaptcha
            prevMap={prevVpnMap}
          />
          <BotTable
            title="🌐 Бесплатные прокси"
            subtitle={
              <>
                {freeSum.active} живых · {freeSum.cooldown} в отлёжке · собрали{" "}
                {fmt(freeSum.collected)}
                <RateDelta value={freeCollectedDelta} />
              </>
            }
            bots={data.free.top}
            showUptime
            showFlag={false}
            prevMap={prevFreeMap}
            />
          </div>
        )}

        <footer className="hq-foot hq-muted">данные обновляются раз в 3 минуты</footer>
      </div>
    </div>
  );
}
