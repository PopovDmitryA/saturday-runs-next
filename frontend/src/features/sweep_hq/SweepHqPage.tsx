import { useEffect, useMemo, useRef, useState } from "react";
import "./sweepHq.css";

const REFRESH_MS = 180_000; // раз в 3 минуты

type Bot = {
  name?: string;
  proxy?: string;
  account?: string;
  status: "working" | "cooldown" | "off";
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
    runs: number;
  };
  rate_24h: number;
  rate_1h: number;
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
  const m = name.match(/^([a-z]{2})/i);
  if (!m) return "🏳️";
  const code = m[1].toLowerCase();
  if (PREFIX_FLAG[code]) return PREFIX_FLAG[code];
  const A = 0x1f1e6;
  return String.fromCodePoint(A + code.charCodeAt(0) - 97, A + code.charCodeAt(1) - 97);
}

const STATUS_META: Record<Bot["status"], { dot: string; label: string }> = {
  working: { dot: "🟢", label: "работает" },
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

function fmtCountdown(s: number): string {
  const m = Math.floor(s / 60);
  const sec = s % 60;
  return `${m}:${String(sec).padStart(2, "0")}`;
}

function Delta({ value }: { value: number }) {
  if (!value || value <= 0) return null;
  return <span className="hq-delta">↑{fmt(value)}</span>;
}

function collectedMap(bots?: Bot[]): Map<string, number> {
  const m = new Map<string, number>();
  bots?.forEach((b, i) => m.set(b.name ?? b.proxy ?? String(i), b.collected_total));
  return m;
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
              <th>Статус</th>
              <th className="hq-num">Задержка</th>
              {showUptime && <th className="hq-num">В работе</th>}
              <th className="hq-num">Атлетов</th>
            </tr>
          </thead>
          <tbody>
            {bots.map((bot, i) => {
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
            {bots.length === 0 && (
              <tr>
                <td colSpan={showUptime ? 6 : 5} className="hq-empty">
                  пока пусто — прогрев
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
  const [perDay, setPerDay] = useState<string>("10000");
  const result = useMemo(() => {
    const r = Number(perDay.replace(/\s/g, ""));
    if (!r || r <= 0) return null;
    const days = remaining / r;
    const date = new Date(Date.now() + days * 86400_000);
    return { days, date };
  }, [perDay, remaining]);
  return (
    <div className="hq-calc">
      <div className="hq-calc__row">
        <label htmlFor="hq-rate">Калькулятор темпа:</label>
        <div className="hq-calc__input">
          <input
            id="hq-rate"
            inputMode="numeric"
            value={perDay}
            onChange={(e) => setPerDay(e.target.value)}
          />
          <span>атлетов / день</span>
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
  const [prev, setPrev] = useState<SweepData | null>(null);
  const [updatedAt, setUpdatedAt] = useState<Date | null>(null);
  const [error, setError] = useState<number | null>(null);
  const [tab, setTab] = useState<"fleet" | "athletes">("fleet");
  const [now, setNow] = useState<number>(() => Date.now());
  const curRef = useRef<SweepData | null>(null);

  useEffect(() => {
    const id = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(id);
  }, []);

  useEffect(() => {
    let alive = true;
    const load = async () => {
      try {
        const res = await fetch(`/api/sweep-hq?token=${encodeURIComponent(token)}`, {
          credentials: "same-origin",
        });
        if (!alive) return;
        if (!res.ok) {
          setError(res.status);
          return;
        }
        const next = (await res.json()) as SweepData;
        setError(null);
        setPrev(curRef.current);
        curRef.current = next;
        setData(next);
        setUpdatedAt(new Date());
      } catch {
        if (alive) setError(0);
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
  // Отсчёт до следующей 3-минутной границы стенных часов — не зависит от момента
  // загрузки страницы, поэтому перезагрузка его не сбрасывает.
  const secondsLeft = Math.ceil((REFRESH_MS - (now % REFRESH_MS)) / 1000);
  const daysDelta =
    prev && prev.forecast.days != null && data.forecast.days != null
      ? data.forecast.days - prev.forecast.days
      : null;

  return (
    <div className="hq-root">
      <div className="hq-inner">
        <div className="hq-topbar">
          <span className="hq-topbar__upd">
            🕐 обновлено {updatedAt ? fmtMoscow(updatedAt) : "…"}
          </span>
          <span className="hq-topbar__next">
            следующее через <b className="hq-topbar__timer">{fmtCountdown(secondsLeft)}</b>
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

          <div className="hq-progress">
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
            <div className="hq-stat">
              <span className="hq-stat__num">{fmt(p.collected)}</span>
              <span className="hq-stat__cap">атлетов собрано</span>
            </div>
            <div className="hq-stat">
              <span className="hq-stat__num">{fmt(p.runs)}</span>
              <span className="hq-stat__cap">забегов</span>
            </div>
            <div className="hq-stat hq-stat--accent">
              <span className="hq-stat__num">{fmt(data.rate_1h)}</span>
              <span className="hq-stat__cap">за час</span>
            </div>
            <div className="hq-stat hq-stat--accent">
              <span className="hq-stat__num">{fmt(data.rate_24h)}</span>
              <span className="hq-stat__cap">за сутки</span>
            </div>
            <div className="hq-stat">
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
            <div className="hq-stat hq-stat--date">
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
