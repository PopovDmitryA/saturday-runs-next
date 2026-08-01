import { useEffect, useState } from "react";
import "./sweepWorld.css";

// ПУБЛИЧНАЯ страница /world. Отличается от закрытого /hq тем, что здесь нет и
// не должно появиться: имён атлетов, адресов прокси, счётчиков капч, статусов
// воркеров. Только обезличенный прогресс — см. комментарий в sweep_hq.py.

const REFRESH_MS = 120_000;

type WorldData = {
  progress: {
    done: number;
    total: number;
    remaining: number;
    pct: number;
    runs: number;
    profiles: number;
  };
  rate_1h: number;
  rate_24h: number;
  forecast: { days: number | null; date: string | null };
  hours: { hour: string; collected: number }[];
};

const fmt = (n: number) => n.toLocaleString("ru-RU");

function fmtDate(iso: string | null): string {
  if (!iso) return "—";
  return new Date(iso).toLocaleDateString("ru-RU", {
    day: "numeric",
    month: "long",
    year: "numeric",
  });
}

function fmtHour(iso: string): string {
  return new Date(iso).toLocaleString("ru-RU", {
    timeZone: "Europe/Moscow",
    day: "2-digit",
    month: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function RateChart({ points }: { points: WorldData["hours"] }) {
  const [hover, setHover] = useState<number | null>(null);
  const width = 760;
  const height = 200;
  const plot = { left: 48, right: 748, top: 14, bottom: 168 };

  if (points.length < 2) {
    return <p className="world-muted">Данных для графика пока мало.</p>;
  }

  const max = Math.max(1, ...points.map((p) => p.collected));
  const last = points.length - 1;
  const stepX = (plot.right - plot.left) / last;
  const x = (i: number) => plot.left + i * stepX;
  const y = (v: number) => plot.bottom - (v / max) * (plot.bottom - plot.top);
  const grid = [0.5, 1].map((s) => Math.round(max * s));
  const labelEvery = Math.max(1, Math.ceil(points.length / 6));
  const area =
    `${x(0)},${plot.bottom} ` +
    points.map((p, i) => `${x(i).toFixed(1)},${y(p.collected).toFixed(1)}`).join(" ") +
    ` ${x(last)},${plot.bottom}`;
  const line = points
    .map((p, i) => `${x(i).toFixed(1)},${y(p.collected).toFixed(1)}`)
    .join(" ");

  const move = (e: React.MouseEvent<SVGSVGElement>) => {
    const rect = e.currentTarget.getBoundingClientRect();
    const px = (e.clientX - rect.left) * (width / rect.width);
    setHover(Math.min(last, Math.max(0, Math.round((px - plot.left) / stepX))));
  };
  const hp = hover != null ? points[hover] : null;

  return (
    <div className="world-chart">
      <div className="world-chart__tip">
        {hp ? (
          <>
            {fmtHour(hp.hour)} — <b>{fmt(hp.collected)}</b> профилей
          </>
        ) : (
          "Наведите на график, чтобы увидеть конкретный час"
        )}
      </div>
      <svg
        viewBox={`0 0 ${width} ${height}`}
        role="img"
        aria-label="Скорость сбора по часам за последние двое суток"
        onMouseMove={move}
        onMouseLeave={() => setHover(null)}
      >
        {grid.map((v) => (
          <g key={v}>
            <line x1={plot.left} x2={plot.right} y1={y(v)} y2={y(v)} className="world-grid" />
            <text x={plot.left - 8} y={y(v) + 4} textAnchor="end">
              {fmt(v)}
            </text>
          </g>
        ))}
        <polygon points={area} className="world-area" />
        <polyline points={line} className="world-line" />
        {hover != null && (
          <>
            <line x1={x(hover)} x2={x(hover)} y1={plot.top} y2={plot.bottom} className="world-cursor" />
            <circle cx={x(hover)} cy={y(points[hover].collected)} r="4.5" className="world-dot" />
          </>
        )}
        {points.map(
          (p, i) =>
            (i % labelEvery === 0 || i === last) && (
              <text key={p.hour} x={x(i)} y={height - 4} textAnchor="middle">
                {fmtHour(p.hour)}
              </text>
            ),
        )}
      </svg>
    </div>
  );
}

export function SweepWorldPage() {
  const [data, setData] = useState<WorldData | null>(null);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    let alive = true;
    const load = () => {
      fetch("/api/sweep-hq/public", { credentials: "same-origin" })
        .then((r) => (r.ok ? r.json() : Promise.reject(new Error(String(r.status)))))
        .then((d: WorldData) => {
          if (alive) {
            setData(d);
            setFailed(false);
          }
        })
        .catch(() => {
          if (alive) setFailed(true);
        });
    };
    load();
    const t = window.setInterval(load, REFRESH_MS);
    return () => {
      alive = false;
      window.clearInterval(t);
    };
  }, []);

  if (failed && !data) {
    return (
      <div className="world-root world-root--center">
        <p className="world-muted">Табло сейчас недоступно. Загляните чуть позже.</p>
      </div>
    );
  }
  if (!data) {
    return (
      <div className="world-root world-root--center">
        <p className="world-muted">Загружаем табло…</p>
      </div>
    );
  }

  const p = data.progress;

  return (
    <div className="world-root">
      <div className="world-inner">
        <header className="world-hero">
          <p className="world-eyebrow">Открытый проект · run5k.run</p>
          <h1>Перепись parkrun-бегунов мира</h1>
          <p className="world-lede">
            Я собираю статистику по всем участникам parkrun на планете, чтобы построить
            честную картину движения: сколько людей бегает, где и как это менялось
            за двадцать лет. Это табло показывает, как идёт сбор — прямо сейчас.
          </p>

          <div className="world-progress">
            <div className="world-progress__bar">
              <div
                className="world-progress__fill"
                style={{ width: `${Math.max(0.5, p.pct)}%` }}
              />
            </div>
            <div className="world-progress__legend">
              <span>
                <b>{fmt(p.done)}</b> из {fmt(p.total)} профилей
              </span>
              <span className="world-progress__pct">{p.pct.toFixed(2)}%</span>
            </div>
          </div>
        </header>

        <section className="world-stats">
          <div className="world-stat">
            <b>{fmt(p.profiles)}</b>
            <span>бегунов найдено</span>
          </div>
          <div className="world-stat">
            <b>{fmt(p.runs)}</b>
            <span>их забегов в базе</span>
          </div>
          <div className="world-stat">
            <b>{fmt(data.rate_1h)}</b>
            <span>профилей за час</span>
          </div>
          <div className="world-stat">
            <b>{fmt(data.rate_24h)}</b>
            <span>профилей за сутки</span>
          </div>
          <div className="world-stat world-stat--soft">
            <b>
              {data.forecast.days != null
                ? `${Math.ceil(data.forecast.days).toLocaleString("ru-RU")} дн`
                : "—"}
            </b>
            <span>до конца сбора</span>
          </div>
          <div className="world-stat world-stat--soft">
            <b className="world-stat__date">{fmtDate(data.forecast.date)}</b>
            <span>ожидаемый финиш</span>
          </div>
        </section>

        <section className="world-block">
          <h2>Как шёл сбор последние двое суток</h2>
          <RateChart points={data.hours} />
        </section>

        <section className="world-block world-about">
          <h2>Что здесь происходит</h2>
          <p>
            У parkrun больше двадцати лет истории и тысячи площадок в двадцати странах.
            Общей открытой статистики по участникам не существует — есть только
            отдельные страницы каждого бегуна.
          </p>
          <p>
            Я обхожу их по порядку и собираю обезличенные цифры: сколько у человека
            забегов, в каких странах он бегал, как часто волонтёрил. Имён и личных
            данных на этом табло нет и не будет — здесь только общий прогресс.
          </p>
          <p>
            Когда сбор закончится, из этого получатся открытые исследования о
            движении: как оно росло, где держится крепче всего и что общего у людей,
            которые бегают по субботам десятилетиями.
          </p>
          <p className="world-muted world-about__foot">
            Табло обновляется каждые пару минут. Проект некоммерческий, его веду я
            один — <a href="/">run5k.run</a>.
          </p>
        </section>
      </div>
    </div>
  );
}
