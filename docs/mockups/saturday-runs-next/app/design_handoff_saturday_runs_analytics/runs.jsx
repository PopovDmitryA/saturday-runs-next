/* Saturday Runs — Пробежки (detailed runs dashboard) */
const { useState: useStateR, useMemo: useMemoR } = React;

function RunsView() {
  const { mmss } = window.SR.fmt;
  const all = window.SR.personal.runsList;
  const PLAT = window.SR.PLATFORMS;

  const [platFilter, setPlatFilter] = useStateR([]); // [] = all
  const [query, setQuery] = useStateR("");
  const [sort, setSort] = useStateR({ key: "date", dir: "desc" });
  const [showTest, setShowTest] = useStateR(false);
  const [openRow, setOpenRow] = useStateR(null);

  const toggleP = (code) =>
    setPlatFilter((cur) => (cur.includes(code) ? cur.filter((c) => c !== code) : [...cur, code]));

  const filtered = useMemoR(() => {
    let rows = all.filter((r) => (showTest ? true : !r.is_test));
    if (platFilter.length) rows = rows.filter((r) => platFilter.includes(r.platform));
    if (query.trim()) {
      const q = query.trim().toLowerCase();
      rows = rows.filter((r) => r.location.toLowerCase().includes(q) || r.city.toLowerCase().includes(q));
    }
    const dir = sort.dir === "asc" ? 1 : -1;
    rows = [...rows].sort((a, b) => {
      let av, bv;
      if (sort.key === "date") { av = a.date; bv = b.date; }
      else if (sort.key === "position") { av = a.position; bv = b.position; }
      else if (sort.key === "time") { av = a.time; bv = b.time; }
      else { av = a.pace; bv = b.pace; }
      return av < bv ? -dir : av > bv ? dir : 0;
    });
    return rows;
  }, [all, platFilter, query, sort, showTest]);

  const setSortKey = (key) =>
    setSort((cur) => (cur.key === key ? { key, dir: cur.dir === "asc" ? "desc" : "asc" } : { key, dir: key === "date" ? "desc" : "asc" }));

  // summary over filtered
  const summary = useMemoR(() => {
    const times = filtered.map((r) => r.time);
    const best = times.length ? Math.min(...times) : null;
    const avg = times.length ? Math.round(times.reduce((s, x) => s + x, 0) / times.length) : null;
    const year = filtered.filter((r) => r.date.startsWith("2026")).length;
    const prs = filtered.filter((r) => r.is_pr).length;
    return { count: filtered.length, best, avg, year, prs };
  }, [filtered]);

  const Arrow = ({ k }) => (sort.key === k ? <span style={{ fontSize: "0.7em" }}>{sort.dir === "asc" ? " ▲" : " ▼"}</span> : null);
  const dmy = (d) => d.split("-").reverse().join(".");

  return (
    <div className="grid" style={{ gap: "var(--gap)" }}>
      <div>
        <span className="eyebrow">Детальная история · бег</span>
        <h1 className="h-section">Все пробежки</h1>
        <p className="sub">Каждый финиш из трёх систем в одной таблице. Фильтруйте по системе и локации, сортируйте по времени, темпу или месту.</p>
      </div>

      <div className="grid" style={{ gridTemplateColumns: "repeat(auto-fit, minmax(130px, 1fr))" }}>
        {[
          { v: summary.count, l: "пробежек показано", cls: "run", f: null },
          { v: summary.best, l: "лучшее время", cls: "gold", f: mmss },
          { v: summary.avg, l: "среднее время", cls: "run", f: mmss },
          { v: summary.prs, l: "из них PR", cls: "gold", f: null },
          { v: summary.year, l: "в этом году", cls: "", f: null },
        ].map((t, i) => (
          <div key={i} className={"tile " + t.cls}>
            <div className="tile-accent"></div>
            <div className="tile-val tabular">{t.v == null ? "—" : t.f ? t.f(t.v) : t.v}</div>
            <div className="tile-label">{t.l}</div>
          </div>
        ))}
      </div>

      <div className="card" style={{ padding: 0, overflow: "hidden" }}>
        {/* filter bar */}
        <div className="runs-filterbar">
          <div className="row wrapf" style={{ gap: 8 }}>
            {Object.keys(PLAT).map((code) => {
              const on = platFilter.includes(code);
              return (
                <button key={code} className={"filter-chip" + (on ? " on" : "")} onClick={() => toggleP(code)}>
                  <span className={"pdot p-" + code}></span>
                  {PLAT[code].name}
                </button>
              );
            })}
            {platFilter.length > 0 && (
              <button className="filter-chip clear" onClick={() => setPlatFilter([])}>сбросить</button>
            )}
          </div>
          <div className="row" style={{ gap: 10, marginLeft: "auto" }}>
            <label className="row" style={{ gap: 7, fontSize: 12.5, color: "var(--muted)", cursor: "pointer" }}>
              <input type="checkbox" checked={showTest} onChange={(e) => setShowTest(e.target.checked)} />
              тестовые
            </label>
            <div className="search-wrap">
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2"><circle cx="11" cy="11" r="7" /><path d="m21 21-4.3-4.3" /></svg>
              <input className="search-input" placeholder="Поиск локации…" value={query} onChange={(e) => setQuery(e.target.value)} />
            </div>
          </div>
        </div>

        <div className="runs-table-wrap">
          <table className="runs-table">
            <thead>
              <tr>
                <th className="sortable" onClick={() => setSortKey("date")}>Дата<Arrow k="date" /></th>
                <th>Система</th>
                <th>Локация</th>
                <th className="num sortable" onClick={() => setSortKey("position")}>Место<Arrow k="position" /></th>
                <th className="num sortable" onClick={() => setSortKey("time")}>Время<Arrow k="time" /></th>
                <th className="num sortable" onClick={() => setSortKey("pace")}>Темп<Arrow k="pace" /></th>
              </tr>
            </thead>
            <tbody>
              {filtered.length === 0 ? (
                <tr><td colSpan={6} style={{ textAlign: "center", padding: 28, color: "var(--muted)" }}>Нет строк по фильтрам</td></tr>
              ) : (
                filtered.map((r, i) => (
                  <tr key={i} className={r.is_global_pr ? "row-gpr" : ""} onClick={() => setOpenRow(openRow === i ? null : i)}>
                    <td className="mono nowrap">
                      {dmy(r.date)}
                      {r.is_global_pr && <span className="badge badge-gpr">глоб. PR</span>}
                      {r.is_pr && !r.is_global_pr && <span className="badge badge-pr">PR</span>}
                      {r.is_test && <span className="badge">тест</span>}
                    </td>
                    <td><span className="row" style={{ gap: 7 }}><span className={"pdot p-" + r.platform}></span><span style={{ fontSize: "0.84rem" }}>{PLAT[r.platform].name}</span></span></td>
                    <td>{r.location}<span className="muted-txt" style={{ fontSize: "0.78rem" }}> · {r.city}</span></td>
                    <td className="num tabular">{r.position}</td>
                    <td className="num"><span className="rec-time" style={{ color: r.is_global_pr ? "var(--gold-ink)" : "var(--ink)" }}>{mmss(r.time)}</span></td>
                    <td className="num mono">{mmss(r.pace)}<span className="muted-txt" style={{ fontSize: "0.7rem" }}>/км</span></td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>
        <div className="runs-foot">
          <span>Показано <b style={{ color: "var(--ink)" }}>{filtered.length}</b> из {all.filter((r) => showTest || !r.is_test).length}</span>
          <span className="mono muted-txt">5 км · обновлено 31.05.2026</span>
        </div>
      </div>
    </div>
  );
}

Object.assign(window, { RunsView });
