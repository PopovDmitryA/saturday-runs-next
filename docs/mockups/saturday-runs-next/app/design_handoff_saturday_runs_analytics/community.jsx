/* Saturday Runs — Community (общая статистика) section */
const { useState: useStateC, useMemo: useMemoC } = React;

function PlatformChip({ code }) {
  const p = window.SR.PLATFORMS[code];
  if (!p) return null;
  return (
    <span className="chip">
      <span className={"pdot p-" + code}></span>
      {p.name}
    </span>
  );
}

function monthShort(m) {
  const [y, mo] = m.split("-");
  const names = ["янв", "фев", "мар", "апр", "май", "июн", "июл", "авг", "сен", "окт", "ноя", "дек"];
  return `${names[+mo - 1]} ${y.slice(2)}`;
}

function CommunityHero({ active }) {
  const { totals, totalsMeta } = window.SR.community;
  const items = [
    { val: totals.participants, label: "участников сообщества", delta: totalsMeta.participants_delta, unit: "" },
    { val: totals.finishes, label: "финишей записано", delta: totalsMeta.finishes_delta, unit: "" },
    { val: totals.locations, label: "локаций в 3 системах", delta: totalsMeta.locations_delta, unit: "" },
    { val: Math.round(totals.total_km / 1000), label: "тысяч километров пройдено", delta: totalsMeta.total_km_delta, unit: "K км" },
  ];
  return (
    <div className="hero rise in">
      <span className="eyebrow hero-eyebrow">Saturday Runs · сводка сообщества</span>
      <h1 className="hero-title">Каждую субботу страна выходит на <em>5 километров</em>.</h1>
      <p className="hero-lead">
        Живая картина по трём системам — <b style={{ color: "var(--hero-ink)" }}>5 вёрст</b>, <b style={{ color: "var(--hero-ink)" }}>S95</b> и <b style={{ color: "var(--hero-ink)" }}>parkrun</b>.
        Один протокол, один масштаб, общий результат.
      </p>
      <div className="hero-stats">
        {items.map((it, i) => (
          <div className="hstat" key={i}>
            <div className="hstat-val tabular">
              <CountUp value={it.val} start={active} dur={1500 + i * 150} />
              {it.unit && <span className="unit">{it.unit}</span>}
            </div>
            <div className="hstat-label">{it.label}</div>
            <div className="hstat-delta">↗ {it.delta}</div>
          </div>
        ))}
      </div>
    </div>
  );
}

function GrowthCard() {
  const { growth } = window.SR.community;
  const [metric, setMetric] = useStateC("participants");
  const data = growth.map((g) => ({ label: g.month, value: g[metric], raw: g }));
  return (
    <div className="card">
      <div className="card-head">
        <div>
          <div className="card-title">Рост сообщества</div>
          <div className="card-note">2019 → 2026 · помесячно</div>
        </div>
        <div className="mini-toggle">
          <button className={metric === "participants" ? "on" : ""} onClick={() => setMetric("participants")}>Участники</button>
          <button className={metric === "finishes" ? "on" : ""} onClick={() => setMetric("finishes")}>Финиши/мес</button>
        </div>
      </div>
      <AreaLine
        data={data}
        height={250}
        color={metric === "participants" ? "var(--brand)" : "var(--run)"}
        formatLabel={(d) => monthShort(d.label)}
        formatVal={(v) => v.toLocaleString("ru-RU") + (metric === "participants" ? " чел." : " финишей")}
      />
      <div className="row wrapf" style={{ gap: 14, marginTop: 4, fontSize: 12, color: "var(--muted)" }}>
        <span className="row" style={{ gap: 6 }}><span style={{ width: 18, height: 3, background: "var(--p-5verst)", borderRadius: 2, opacity: 0.5 }}></span>пауза 2020 (COVID) видна провалом</span>
      </div>
    </div>
  );
}

function PlatformSplitCard() {
  const { platforms } = window.SR.community;
  const segs = platforms.map((p) => ({ name: p.name, value: p.finishes, color: p.color }));
  return (
    <div className="card" style={{ height: "100%", display: "flex", flexDirection: "column" }}>
      <div className="card-head">
        <div className="card-title">Доля платформ</div>
        <div className="card-note">по финишам</div>
      </div>
      <div style={{ flex: 1, display: "flex", alignItems: "center" }}>
        <Donut segments={segs} />
      </div>
    </div>
  );
}

function PlatformCompareCard() {
  const { platforms } = window.SR.community;
  const { mmss } = window.SR.fmt;
  const maxF = Math.max(...platforms.map((p) => p.finishes));
  return (
    <div className="card">
      <div className="card-head">
        <div className="card-title">Сравнение платформ</div>
        <div className="card-note">средние по системе</div>
      </div>
      <div className="grid" style={{ gap: 10 }}>
        {platforms.map((p) => (
          <div key={p.code} style={{ display: "grid", gridTemplateColumns: "120px 1fr", gap: 14, alignItems: "center" }}>
            <div className="row" style={{ gap: 8 }}>
              <span className={"pdot p-" + p.code}></span>
              <b style={{ fontSize: "0.9rem" }}>{p.name}</b>
            </div>
            <div>
              <div className="rank-bar-track" style={{ height: 9 }}>
                <div className="rank-bar-fill in" style={{ background: p.color, transform: `scaleX(${p.finishes / maxF})` }}></div>
              </div>
              <div className="row between" style={{ marginTop: 6, fontSize: 12, color: "var(--muted)" }}>
                <span><b className="tabular" style={{ color: "var(--ink)" }}>{p.finishes.toLocaleString("ru-RU")}</b> финишей · {p.locations} локаций</span>
                <span className="mono">⌀ {mmss(p.avg_time)} · {mmss(p.avg_pace)}/км</span>
              </div>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

function TopLocationsCard() {
  const { topLocations } = window.SR.community;
  const [metric, setMetric] = useStateC("finishes");
  const sorted = useMemoC(
    () => [...topLocations].sort((a, b) => b[metric] - a[metric]).slice(0, 12),
    [metric]
  );
  const items = sorted.map((l) => ({
    name: l.name,
    sub: (
      <>
        <span className={"pdot p-" + l.platform} style={{ width: 7, height: 7 }}></span>
        {l.city === l.region ? l.city : `${l.city} · ${l.region}`}
      </>
    ),
    value: l[metric],
    color: window.SR.PLATFORMS[l.platform].color,
  }));
  return (
    <div className="card">
      <div className="card-head">
        <div>
          <div className="card-title">Топ локаций</div>
          <div className="card-note">самые многолюдные площадки</div>
        </div>
        <div className="mini-toggle">
          <button className={metric === "finishes" ? "on" : ""} onClick={() => setMetric("finishes")}>Финиши</button>
          <button className={metric === "participants" ? "on" : ""} onClick={() => setMetric("participants")}>Участники</button>
        </div>
      </div>
      <RankedBars items={items} valueSuffix={metric === "finishes" ? "финишей" : "чел."} />
    </div>
  );
}

function RecordsCard() {
  const { records } = window.SR.community;
  const { mmss } = window.SR.fmt;
  const [tab, setTab] = useStateC("absolute");
  const rows = tab === "absolute" ? records.absolute : records.courses;
  return (
    <div className="card">
      <div className="card-head">
        <div className="card-title">Рекорды</div>
        <div className="lb-tabs">
          <button className={"lb-tab" + (tab === "absolute" ? " on" : "")} onClick={() => setTab("absolute")}>Абсолютные</button>
          <button className={"lb-tab" + (tab === "courses" ? " on" : "")} onClick={() => setTab("courses")}>Рекорды трасс</button>
        </div>
      </div>
      <table className="rec-table">
        <thead>
          <tr>
            <th>{tab === "absolute" ? "Категория" : "Локация"}</th>
            <th>Атлет</th>
            <th className="num">Время</th>
            <th className="num">Когда</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r, i) => (
            <tr key={i}>
              <td>
                <div style={{ fontWeight: 600 }}>{tab === "absolute" ? r.scope : r.where}</div>
                <div className="row" style={{ gap: 6, marginTop: 3 }}>
                  <span className={"pdot p-" + r.platform} style={{ width: 7, height: 7 }}></span>
                  <span style={{ fontSize: 12, color: "var(--muted)" }}>{tab === "absolute" ? r.where : r.city}</span>
                </div>
              </td>
              <td style={{ fontSize: "0.85rem" }}>{r.who}</td>
              <td className="num"><span className="rec-time" style={{ color: i === 0 ? "var(--gold-ink)" : "var(--ink)" }}>{mmss(r.time)}</span></td>
              <td className="num mono" style={{ fontSize: 12, color: "var(--muted)" }}>{r.date.split("-").reverse().slice(0, 2).join(".")}.{r.date.slice(2, 4)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function LeaderboardCard() {
  const { leaderboards } = window.SR.community;
  const [tab, setTab] = useStateC("runs");
  const cfg = {
    runs: { label: "По пробежкам", suffix: "пробежек", color: "var(--run)" },
    pr: { label: "По PR", suffix: "PR", color: "var(--gold)" },
    volunteering: { label: "По волонтёрству", suffix: "раз", color: "var(--vol)" },
  };
  const data = leaderboards[tab];
  return (
    <div className="card">
      <div className="card-head">
        <div className="card-title">Лидерборд участников</div>
        <div className="lb-tabs">
          {Object.keys(cfg).map((k) => (
            <button key={k} className={"lb-tab" + (tab === k ? " on" : "")} onClick={() => setTab(k)}>{cfg[k].label}</button>
          ))}
        </div>
      </div>
      <div className="rank-list">
        {data.map((it, i) => (
          <div key={i} className="rank-row" style={{ gridTemplateColumns: "26px 36px 1fr auto", ...(it.you ? { background: "color-mix(in oklch, var(--gold) 12%, transparent)", borderRadius: 10 } : {}) }}>
            <div className="rank-num" style={i === 0 ? { color: "var(--gold-ink)", fontWeight: 700 } : null}>{i + 1}</div>
            <div className="avatar" style={{ background: `oklch(0.6 0.17 ${it.hue})` }}>{it.name.split(" ").map((w) => w[0]).join("")}</div>
            <div className="rank-main" style={{ display: "flex", flexDirection: "column", justifyContent: "center" }}>
              <div className="rank-name">{it.name}{it.you && <span style={{ color: "var(--gold-ink)", fontWeight: 700 }}> · вы</span>}</div>
              <div className="rank-sub">{it.city} · {it.sub}</div>
            </div>
            <div className="rank-val" style={{ color: it.you ? "var(--gold-ink)" : "var(--ink)" }}>
              {it.value}
              <small>{cfg[tab].suffix}</small>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

function GeoCard() {
  const { geo } = window.SR.community;
  const [hover, setHover] = useStateC(null);
  const maxF = Math.max(...geo.map((g) => g.finishes));
  return (
    <div className="card">
      <div className="card-head">
        <div>
          <div className="card-title">География</div>
          <div className="card-note">размер точки = число финишей · {geo.length} городов</div>
        </div>
        <span className="chip"><span className="pdot" style={{ background: "var(--brand)" }}></span>активные регионы</span>
      </div>
      <div className="geo">
        <svg viewBox="0 0 100 56" preserveAspectRatio="none" style={{ position: "absolute", inset: 0, width: "100%", height: "100%" }}>
          {[14, 28, 42].map((y) => <line key={"h" + y} className="geo-grid-line" x1="0" x2="100" y1={y} y2={y} />)}
          {[25, 50, 75].map((x) => <line key={"v" + x} className="geo-grid-line" x1={x} x2={x} y1="0" y2="56" />)}
        </svg>
        {geo.map((g, i) => {
          const size = 10 + (g.finishes / maxF) * 38;
          return (
            <div
              key={i}
              className="geo-dot"
              onMouseEnter={() => setHover(i)}
              onMouseLeave={() => setHover(null)}
              style={{
                left: `${g.x * 100}%`,
                top: `${g.y * 100}%`,
                width: size,
                height: size,
                background: `color-mix(in oklch, var(--brand) ${40 + (g.finishes / maxF) * 50}%, transparent)`,
                border: "1.5px solid var(--brand)",
                zIndex: hover === i ? 5 : 1,
              }}
            ></div>
          );
        })}
        {hover != null && (
          <div className="tooltip show" style={{ left: `${geo[hover].x * 100}%`, top: `${geo[hover].y * 100}%` }}>
            <div className="tt-title">{geo[hover].name}</div>
            <div className="tt-row"><b>{geo[hover].finishes.toLocaleString("ru-RU")}</b> финишей</div>
            <div className="tt-row" style={{ color: "var(--hero-muted)" }}>{geo[hover].locs} локаций</div>
          </div>
        )}
      </div>
    </div>
  );
}

function CommunityView({ active }) {
  return (
    <div className="grid" style={{ gap: "var(--gap)" }}>
      <CommunityHero active={active} />

      <div className="grid cols-12" style={{ marginTop: 8 }}>
        <div className="span-8"><GrowthCard /></div>
        <div className="span-4"><PlatformSplitCard /></div>
      </div>

      <div className="grid cols-12">
        <div className="span-7"><TopLocationsCard /></div>
        <div className="span-5" style={{ display: "grid", gap: "var(--gap)", alignContent: "start" }}>
          <PlatformCompareCard />
          <LeaderboardCard />
          <RecordsCard />
        </div>
      </div>

      <GeoCard />
    </div>
  );
}

Object.assign(window, { CommunityView, PlatformChip, monthShort });
