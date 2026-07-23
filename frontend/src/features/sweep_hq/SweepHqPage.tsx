import { useEffect, useMemo, useState } from "react";
import "./sweepHq.css";

type Bot = {
  name?: string;
  proxy?: string;
  account?: string;
  status: "working" | "cooldown" | "off";
  cooldown_hours: number;
  collected_total: number;
  active_seconds?: number;
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

const MEDALS = ["🥇", "🥈", "🥉"];

function BotTable({
  title,
  subtitle,
  bots,
  showUptime,
}: {
  title: string;
  subtitle: string;
  bots: Bot[];
  showUptime: boolean;
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
                    <span className="hq-bot__name">{id}</span>
                    {bot.account && bot.account !== "free" && (
                      <span className="hq-bot__acc">{bot.account}</span>
                    )}
                  </td>
                  <td>
                    <StatusCell bot={bot} />
                  </td>
                  {showUptime && <td className="hq-num">{fmtDuration(bot.active_seconds ?? 0)}</td>}
                  <td className="hq-num hq-collected">{fmt(bot.collected_total)}</td>
                </tr>
              );
            })}
            {bots.length === 0 && (
              <tr>
                <td colSpan={showUptime ? 5 : 4} className="hq-empty">
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
  const [error, setError] = useState<number | null>(null);

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
        setError(null);
        setData((await res.json()) as SweepData);
      } catch {
        if (alive) setError(0);
      }
    };
    load();
    const id = window.setInterval(load, 30_000);
    return () => {
      alive = false;
      window.clearInterval(id);
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

  return (
    <div className="hq-root">
      <div className="hq-inner">
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
              <span className="hq-stat__num">{fmt(data.rate_24h)}</span>
              <span className="hq-stat__cap">за 24 часа</span>
            </div>
            <div className="hq-stat">
              <span className="hq-stat__num">
                {data.forecast.days != null
                  ? `${Math.ceil(data.forecast.days).toLocaleString("ru-RU")} дн`
                  : "—"}
              </span>
              <span className="hq-stat__cap">до финиша</span>
            </div>
            <div className="hq-stat hq-stat--date">
              <span className="hq-stat__num">{fmtDate(data.forecast.date)}</span>
              <span className="hq-stat__cap">прогноз финиша</span>
            </div>
          </div>

          <Calculator remaining={p.remaining} />
        </header>

        <div className="hq-fleet">
          <BotTable
            title="🛡️ Приватные VPN"
            subtitle={`${vpnWorking} в работе · ${data.vpn.length} всего`}
            bots={data.vpn}
            showUptime
          />
          <BotTable
            title="🌐 Бесплатные прокси"
            subtitle={`${freeSum.active} живых · ${freeSum.cooldown} в отлёжке · собрали ${fmt(
              freeSum.collected,
            )}`}
            bots={data.free.top}
            showUptime={false}
          />
        </div>

        <footer className="hq-foot hq-muted">обновляется каждые 30 секунд</footer>
      </div>
    </div>
  );
}
