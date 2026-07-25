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
  prevMap,
}: {
  title: string;
  subtitle: string;
  bots: Bot[];
  showUptime: boolean;
  showFlag: boolean;
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
                  <td className="hq-num hq-collected">
                    {fmt(bot.collected_total)}
                    <Delta value={bot.collected_total - (prevMap.get(id) ?? bot.collected_total)} />
                  </td>
                </tr>
              );
            })}
            {shown.length === 0 && (
              <tr>
                <td colSpan={showUptime ? 6 : 5} className="hq-empty">
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

type Athlete = {
  athlete_id: number;
  name: string | null;
  total_runs: number;
  status: string;
  parsed_at: string;
  volunteer: number;
  location: string | null;
  country_name: string | null;
  iso2: string | null;
};

function flagFromIso2(iso2: string | null): string {
  if (!iso2 || iso2.length !== 2) return "";
  const A = 0x1f1e6;
  const c = iso2.toLowerCase();
  return String.fromCodePoint(A + c.charCodeAt(0) - 97, A + c.charCodeAt(1) - 97);
}

const ATHLETE_STATUS: Record<string, { label: string; cls: string }> = {
  ok: { label: "✓ валидна", cls: "ok" },
  registered_empty: { label: "пусто", cls: "empty" },
  not_found: { label: "нет", cls: "empty" },
  unclassified: { label: "ревью", cls: "review" },
};

function AthletesTab({ token }: { token: string }) {
  const [rows, setRows] = useState<Athlete[] | null>(null);
  const [error, setError] = useState(false);
  useEffect(() => {
    let alive = true;
    const load = async () => {
      try {
        const res = await fetch(`/api/sweep-hq/athletes?token=${encodeURIComponent(token)}`, {
          credentials: "same-origin",
        });
        if (!alive) return;
        if (!res.ok) {
          setError(true);
          return;
        }
        setError(false);
        setRows(((await res.json()) as { athletes: Athlete[] }).athletes);
      } catch {
        if (alive) setError(true);
      }
    };
    load();
    // Опрос выравниваем по стенным часам (границы кратны 3 мин), чтобы отсчёт
    // не сбрасывался при перезагрузке страницы — данные на сервере живые.
    let interval: number | undefined;
    const msToBoundary = REFRESH_MS - (Date.now() % REFRESH_MS);
    const timer = window.setTimeout(() => {
      load();
      interval = window.setInterval(load, REFRESH_MS);
    }, msToBoundary);
    return () => {
      alive = false;
      window.clearTimeout(timer);
      if (interval) window.clearInterval(interval);
    };
  }, [token]);

  if (error) return <p className="hq-muted hq-pad">Не удалось загрузить.</p>;
  if (!rows) return <p className="hq-muted hq-pad">Загрузка…</p>;

  return (
    <section className="hq-card">
      <header className="hq-card__head">
        <h2>🏃 Последние 100 обработанных</h2>
        <span className="hq-card__sub">свежие сверху · обновление раз в 3 мин</span>
      </header>
      <div className="hq-tablewrap">
        <table className="hq-table hq-table--ath">
          <thead>
            <tr>
              <th>ID</th>
              <th>Результат</th>
              <th>ФИО</th>
              <th className="hq-num">Пробежек</th>
              <th className="hq-num">Волонтёрств</th>
              <th>Частая локация</th>
              <th className="hq-num">Обработан (МСК)</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((a) => {
              const st = ATHLETE_STATUS[a.status] ?? { label: a.status, cls: "empty" };
              return (
                <tr key={a.athlete_id}>
                  <td className="hq-mono">
                    <a
                      href={`https://www.parkrun.org.uk/parkrunner/${a.athlete_id}/`}
                      target="_blank"
                      rel="noreferrer"
                      className="hq-idlink"
                    >
                      {a.athlete_id}
                    </a>
                  </td>
                  <td>
                    <span className={`hq-astatus hq-astatus--${st.cls}`}>{st.label}</span>
                  </td>
                  <td>{a.name ?? "—"}</td>
                  <td className="hq-num">{a.total_runs || "—"}</td>
                  <td className="hq-num">{a.volunteer || "—"}</td>
                  <td>
                    {a.location ? (
                      <span className="hq-loc">
                        {a.iso2 && <span className="hq-loc__flag">{flagFromIso2(a.iso2)}</span>}
                        <span>{a.location}</span>
                        {a.country_name && <span className="hq-loc__cn">{a.country_name}</span>}
                      </span>
                    ) : (
                      "—"
                    )}
                  </td>
                  <td className="hq-num hq-mono hq-muted">{fmtMoscowDateTime(a.parsed_at)}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </section>
  );
}

function Calculator({ remaining }: { remaining: number }) {
  const [rate, setRate] = useState<string>("10000");
  const [unit, setUnit] = useState<"hour" | "day">("day");
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
  const [tab, setTab] = useState<"fleet" | "athletes">("fleet");
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
      setPrev(curRef.current);
      curRef.current = next;
      setData(next);
      setUpdatedAt(new Date());
      saveSnap(token, next);
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
  const prevVpnMap = collectedMap(prev?.vpn);
  const prevFreeMap = collectedMap(prev?.free.top);
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
            className={tab === "athletes" ? "hq-tab hq-tab--on" : "hq-tab"}
            onClick={() => setTab("athletes")}
          >
            🏃 Атлеты
          </button>
        </nav>

        {tab === "athletes" ? (
          <AthletesTab token={token} />
        ) : (
          <div className="hq-fleet">
            <BotTable
              title="🛡️ Приватные VPN"
            subtitle={`${vpnWorking} в работе · ${data.vpn.length} всего`}
            bots={data.vpn}
            showUptime
            showFlag
            prevMap={prevVpnMap}
          />
          <BotTable
            title="🌐 Бесплатные прокси"
            subtitle={`${freeSum.active} живых · ${freeSum.cooldown} в отлёжке · собрали ${fmt(
              freeSum.collected,
            )}`}
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
