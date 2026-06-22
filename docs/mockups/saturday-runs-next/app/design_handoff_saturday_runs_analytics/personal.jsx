/* Saturday Runs — Personal (Мой профиль) section */
const { useState: useStateP } = React;

function PersonalHero({ active, heroStyle }) {
  const p = window.SR.personal;
  const { mmss } = window.SR.fmt;
  const sinceYears = Math.floor(p.totals.days_since_first / 365);
  const stats = [
    { val: p.hero.runs, label: "пробежек", fmt: null, unit: "" },
    { val: p.hero.volunteering, label: "волонтёрств", fmt: null, unit: "" },
    { val: p.hero.best, label: "личный рекорд", fmt: (n) => mmss(n), unit: "" },
    { val: p.hero.locations, label: "локаций", fmt: null, unit: "" },
  ];

  if (heroStyle === "compact") {
    return (
      <div className="card rise in" style={{ display: "flex", alignItems: "center", gap: 20, flexWrap: "wrap" }}>
        <div className="avatar" style={{ width: 58, height: 58, fontSize: "1.3rem", background: "linear-gradient(135deg, var(--brand), var(--p-parkrun))" }}>{p.initials}</div>
        <div>
          <div className="card-title" style={{ fontSize: "1.3rem" }}>{p.name}</div>
          <div className="card-note">в субботних пробежках с {p.since.split("-").reverse().join(".")} · {sinceYears} лет · 3 системы</div>
        </div>
        <div style={{ display: "flex", gap: 26, marginLeft: "auto", flexWrap: "wrap" }}>
          {stats.map((s, i) => (
            <div key={i}>
              <div className="tile-val tabular" style={{ fontSize: "1.5rem" }}>{s.fmt ? <CountUp value={s.val} start={active} format={s.fmt} /> : <CountUp value={s.val} start={active} />}</div>
              <div className="tile-label">{s.label}</div>
            </div>
          ))}
        </div>
      </div>
    );
  }

  // default: dark hero
  return (
    <div className="hero rise in">
      <div className="row" style={{ gap: 16, alignItems: "center" }}>
        <div className="avatar" style={{ width: 56, height: 56, fontSize: "1.3rem", background: "linear-gradient(135deg, var(--hero-accent), var(--p-parkrun))" }}>{p.initials}</div>
        <div>
          <span className="eyebrow hero-eyebrow" style={{ marginBottom: 0 }}>Личный кабинет</span>
          <h1 className="hero-title" style={{ margin: "4px 0 0", fontSize: "clamp(1.5rem, 3.5vw, 2.3rem)" }}>{p.name}</h1>
        </div>
      </div>
      <p className="hero-lead" style={{ marginTop: 12 }}>
        В субботних пробежках с {p.since.split("-").reverse().join(".")} · <b style={{ color: "var(--hero-ink)" }}>{sinceYears} лет</b> · профили в трёх системах объединены в один путь.
      </p>
      <div className="hero-stats">
        {stats.map((s, i) => (
          <div className="hstat" key={i}>
            <div className="hstat-val tabular">
              {s.fmt ? <CountUp value={s.val} start={active} format={s.fmt} dur={1400 + i * 120} /> : <CountUp value={s.val} start={active} dur={1400 + i * 120} />}
            </div>
            <div className="hstat-label">{s.label}</div>
          </div>
        ))}
      </div>
    </div>
  );
}

function PercentileCard() {
  const { percentiles } = window.SR.personal;
  const [ref, seen] = useInView();
  return (
    <div className="card" ref={ref}>
      <div className="card-head">
        <div>
          <div className="card-title">Я в сравнении с сообществом</div>
          <div className="card-note">перцентиль среди всех участников</div>
        </div>
        <span className="chip tag-best"><span className="pdot p-gold"></span>лучше среднего</span>
      </div>
      <div>
        {percentiles.map((p, i) => (
          <div key={i} className="pct-row">
            <div>
              <div className="pct-label">{p.label}</div>
              <div className="pct-meta">{p.meta}</div>
            </div>
            <div className="pct-val tabular">{p.pct}%</div>
            <div className="pct-track">
              <div className="pct-fill" style={{ transform: seen ? `scaleX(${p.pct / 100})` : "scaleX(0)", transitionDelay: `${i * 90}ms` }}></div>
              <div className="pct-marker" style={{ left: `${p.pct}%` }}></div>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

function DistributionCard() {
  const { distribution } = window.SR.personal;
  const { mmss } = window.SR.fmt;
  return (
    <div className="card">
      <div className="card-head">
        <div>
          <div className="card-title">Где мой результат в поле</div>
          <div className="card-note">распределение всех финишей сообщества</div>
        </div>
      </div>
      <Histogram
        buckets={distribution.buckets}
        youCenter={distribution.you}
        formatX={(c) => mmss(c)}
        height={180}
      />
      <div className="row wrapf" style={{ gap: 16, marginTop: 14, fontSize: 13, color: "var(--muted)" }}>
        <span className="row" style={{ gap: 6 }}><span className="sw" style={{ width: 11, height: 11, borderRadius: 3, background: "var(--gold)" }}></span>ваш лучший — {mmss(distribution.you)}</span>
        <span>быстрее <b style={{ color: "var(--ink)" }}>{distribution.fasterThanPct}%</b> финишей</span>
      </div>
    </div>
  );
}

function PaceCard() {
  const { paceTrend } = window.SR.personal;
  const { mmss } = window.SR.fmt;
  // invert: lower pace (faster) plotted higher
  const data = paceTrend.map((d) => ({ label: d.month, value: d.pace }));
  const first = paceTrend[0].pace;
  const last = paceTrend[paceTrend.length - 1].pace;
  const gained = first - last;
  return (
    <div className="card">
      <div className="card-head">
        <div>
          <div className="card-title">Динамика темпа</div>
          <div className="card-note">средний темп по месяцам · выше = быстрее</div>
        </div>
        <span className="chip" style={{ color: "var(--run-ink)" }}><span className="pdot p-run"></span>−{mmss(gained)}/км за 18 мес</span>
      </div>
      <AreaLine
        data={data}
        color="var(--run)"
        height={210}
        yInvert={true}
        formatLabel={(d) => monthShort(d.label)}
        formatVal={(v) => mmss(v) + " /км"}
      />
    </div>
  );
}

function ClubsCard() {
  const { clubs } = window.SR.personal;
  const all = [10, 25, 50, 100, 250, 500];
  const pctToNext = Math.min(100, Math.round((clubs.current / clubs.next) * 100));
  return (
    <div className="card">
      <div className="card-head">
        <div>
          <div className="card-title">Беговые клубы</div>
          <div className="card-note">майлстоуны по числу пробежек</div>
        </div>
        <span className="card-note tabular">{clubs.current} / {clubs.next}</span>
      </div>
      <div className="clubs">
        {all.map((c) => {
          const earned = clubs.earned.includes(c);
          const next = c === clubs.next;
          return (
            <div key={c} className={"club" + (earned ? " earned" : "") + (next ? " next" : "")}>
              <div className="club-badge">{c}</div>
              <div className="club-name">{earned ? "получен" : next ? "цель" : "—"}</div>
            </div>
          );
        })}
      </div>
      <div style={{ marginTop: 6 }}>
        <div className="rank-bar-track" style={{ height: 9 }}>
          <div className="rank-bar-fill in" style={{ background: "var(--gold)", transform: `scaleX(${pctToNext / 100})` }}></div>
        </div>
        <div className="card-note" style={{ marginTop: 7 }}>До клуба «{clubs.next}» осталось <b style={{ color: "var(--ink)" }}>{clubs.next - clubs.current}</b> пробежек</div>
      </div>
    </div>
  );
}

function PersonalRecordsCard() {
  const { records } = window.SR.personal;
  const { mmss } = window.SR.fmt;
  return (
    <div className="card">
      <div className="card-head">
        <div className="card-title">Личные рекорды</div>
        <div className="card-note">лучшее время на локацию</div>
      </div>
      <table className="rec-table">
        <thead>
          <tr><th>Локация</th><th className="num">Время</th><th className="num">Дата</th></tr>
        </thead>
        <tbody>
          {records.map((r, i) => (
            <tr key={i}>
              <td>
                <div className="row" style={{ gap: 8 }}>
                  <span className={"pdot p-" + r.platform} style={{ width: 8, height: 8 }}></span>
                  <span style={{ fontWeight: 600 }}>{r.where}</span>
                  {r.global && <span className="chip tag-best" style={{ padding: "2px 7px" }}>глоб. PR</span>}
                </div>
              </td>
              <td className="num"><span className="rec-time" style={{ color: r.global ? "var(--gold-ink)" : "var(--ink)" }}>{mmss(r.time)}</span></td>
              <td className="num mono" style={{ fontSize: 12, color: "var(--muted)" }}>{r.date.split("-").reverse().join(".")}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function HeatCard() {
  const { heat } = window.SR.personal;
  const active = heat.filter((v) => v > 0).length;
  return (
    <div className="card">
      <div className="card-head">
        <div>
          <div className="card-title">Постоянство по субботам</div>
          <div className="card-note">последние 52 субботы · активных {active} из 52 ({Math.round((active / 52) * 100)}%)</div>
        </div>
      </div>
      <HeatCalendar cells={heat} />
    </div>
  );
}

function ActivityCard() {
  const { activity } = window.SR.personal;
  return (
    <div className="card">
      <div className="card-head">
        <div className="card-title">Активность за год</div>
        <div className="row" style={{ gap: 14, fontSize: 12, color: "var(--muted)" }}>
          <span className="row" style={{ gap: 5 }}><span className="sw" style={{ width: 10, height: 10, borderRadius: 2, background: "var(--run)" }}></span>пробежки</span>
          <span className="row" style={{ gap: 5 }}><span className="sw" style={{ width: 10, height: 10, borderRadius: 2, background: "var(--vol)" }}></span>волонтёрство</span>
        </div>
      </div>
      <MiniBars data={activity} formatLabel={(d) => monthShort(d.month).split(" ")[0]} />
    </div>
  );
}

function HighlightTiles({ active }) {
  const p = window.SR.personal;
  const { mmss } = window.SR.fmt;
  const tiles = [
    { val: p.totals.total_km, label: "км пройдено", cls: "run", foot: "≈ 5 км × " + p.totals.runs, fmt: null },
    { val: p.totals.pr_count, label: "личных рекордов", cls: "gold", foot: "31% пробежек — PR", fmt: null },
    { val: p.totals.saturday_streak, label: "суббот подряд", cls: "", foot: "текущая серия", fmt: null },
    { val: p.totals.avg, label: "среднее время", cls: "run", foot: "темп " + mmss(p.totals.avg_pace) + "/км", fmt: (n) => mmss(n) },
    { val: p.totals.regions, label: "регионов", cls: "", foot: p.totals.cities + " городов", fmt: null },
    { val: p.topLocation.count, label: "раз в Измайлово", cls: "", foot: "частая локация", fmt: null },
  ];
  return (
    <div className="grid" style={{ gridTemplateColumns: "repeat(auto-fit, minmax(130px, 1fr))" }}>
      {tiles.map((t, i) => (
        <div key={i} className={"tile " + t.cls}>
          <div className="tile-accent"></div>
          <div className="tile-val tabular">{t.fmt ? <CountUp value={t.val} start={active} format={t.fmt} /> : <CountUp value={t.val} start={active} />}{t.unit}</div>
          <div className="tile-label">{t.label}</div>
          <div className="tile-foot">{t.foot}</div>
        </div>
      ))}
    </div>
  );
}

function PersonalView({ active, heroStyle }) {
  return (
    <div className="grid" style={{ gap: "var(--gap)" }}>
      <PersonalHero active={active} heroStyle={heroStyle} />
      <div style={{ marginTop: 4 }}><HighlightTiles active={active} /></div>

      <div className="grid cols-12" style={{ marginTop: 4 }}>
        <div className="span-7"><DistributionCard /></div>
        <div className="span-5"><PercentileCard /></div>
      </div>

      <div className="grid cols-12">
        <div className="span-7"><PaceCard /></div>
        <div className="span-5"><ClubsCard /></div>
      </div>

      <div className="grid cols-12">
        <div className="span-8"><HeatCard /></div>
        <div className="span-4"><ActivityCard /></div>
      </div>

      <PersonalRecordsCard />
    </div>
  );
}

Object.assign(window, { PersonalView });
