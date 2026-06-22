/* Saturday Runs — Профили (linking) */
const { useState: useStatePr } = React;

function LinkedCard({ link }) {
  const PLAT = window.SR.PLATFORMS;
  const p = PLAT[link.code];
  const [open, setOpen] = useStatePr(false);
  const [syncing, setSyncing] = useStatePr(false);
  const dmy = (d) => (d ? d.split("-").reverse().join(".") : "—");

  const doSync = () => {
    setSyncing(true);
    setTimeout(() => setSyncing(false), 2200);
  };

  if (link.status !== "linked") {
    // unlinked → add form (demo)
    return (
      <div className="card profile-card profile-card-unlinked">
        <div className="profile-card-head">
          <div className="row" style={{ gap: 12 }}>
            <span className={"plogo p-" + link.code}>{p.short}</span>
            <div>
              <div className="card-title">{p.name}</div>
              <div className="card-note">профиль не привязан</div>
            </div>
          </div>
          <span className="chip" style={{ color: "var(--muted)" }}>не привязан</span>
        </div>
        <p className="card-note" style={{ lineHeight: 1.5 }}>{link.hint}</p>
        <div className="profile-input-row">
          <input className="search-input profile-url" placeholder={"Ссылка или код · напр. " + link.externalId} defaultValue="" />
          <button className="btn-solid">Предпросмотр</button>
        </div>
        <div className="profile-hint-row">
          <span className="mono muted-txt">↳ найдём профиль в базе или загрузим с сайта</span>
        </div>
      </div>
    );
  }

  return (
    <div className="card profile-card">
      <div className="profile-card-head">
        <div className="row" style={{ gap: 12 }}>
          <span className={"plogo p-" + link.code}>{p.short}</span>
          <div>
            <div className="card-title">{p.name}</div>
            <div className="card-note">{link.name} · ID {link.externalId}</div>
          </div>
        </div>
        <span className="chip" style={{ color: "var(--run-ink)", borderColor: "color-mix(in oklch, var(--run) 40%, transparent)", background: "color-mix(in oklch, var(--run) 12%, transparent)" }}>
          <span className="pdot p-run"></span>привязан
        </span>
      </div>

      <div className="profile-stats">
        <div><span className="profile-stat-v tabular">{link.runs}</span><span className="profile-stat-l">пробежек</span></div>
        <div><span className="profile-stat-v tabular">{link.vol}</span><span className="profile-stat-l">волонтёрств</span></div>
        <div><span className="profile-stat-v mono" style={{ fontSize: "0.92rem" }}>{dmy(link.dataThrough)}</span><span className="profile-stat-l">данные по</span></div>
      </div>

      <div className="profile-sync-row">
        <span className="mono muted-txt" style={{ fontSize: "0.74rem" }}>
          {syncing ? "обновление…" : "синхронизировано " + (link.lastSync ? link.lastSync.replace(/-/g, ".") : "—")}
        </span>
        <div className="row" style={{ gap: 8 }}>
          <button className={"icon-btn" + (syncing ? " spin" : "")} title="Обновить данные" onClick={doSync} disabled={syncing}>
            <svg width="15" height="15" viewBox="0 0 24 24" fill="currentColor"><path d="M17.65 6.35A7.96 7.96 0 0 0 12 4c-4.42 0-7.99 3.58-7.99 8s3.57 8 7.99 8c3.73 0 6.84-2.55 7.73-6h-2.08a5.99 5.99 0 0 1-5.65 4 6 6 0 1 1 4.22-10.22L13 11h7V4z" /></svg>
          </button>
          <a className="text-link" href={link.url} target="_blank" rel="noreferrer">открыть ↗</a>
          <button className="text-link danger" onClick={() => setOpen(true)}>отвязать</button>
        </div>
      </div>

      {open && (
        <div className="profile-confirm">
          <p style={{ margin: 0, fontSize: "0.86rem" }}>Отвязать <b>{p.name}</b>? Пробежки и волонтёрства исчезнут из ЛК (в общей базе сохранятся).</p>
          <div className="row" style={{ gap: 8, marginTop: 10 }}>
            <button className="btn-solid danger" onClick={() => setOpen(false)}>Отвязать</button>
            <button className="btn-ghost-sm" onClick={() => setOpen(false)}>Оставить</button>
          </div>
        </div>
      )}
    </div>
  );
}

function ProfilesView() {
  const { links, auth } = window.SR.profiles;
  const linkedCount = links.filter((l) => l.status === "linked").length;
  return (
    <div className="grid" style={{ gap: "var(--gap)" }}>
      <div>
        <span className="eyebrow">Настройки · источники данных</span>
        <h1 className="h-section">Профили и привязки</h1>
        <p className="sub">Привяжите профили беговых систем один раз — данные подтянутся из публичных страниц и обновляются по запросу. Привязано {linkedCount} из 3.</p>
      </div>

      <div className="grid cols-3">
        {links.map((l) => <LinkedCard key={l.code} link={l} />)}
      </div>

      <div className="card">
        <div className="card-head">
          <div>
            <div className="card-title">Способы входа</div>
            <div className="card-note">привяжите несколько — входите любым</div>
          </div>
        </div>
        <div className="grid cols-3">
          {auth.map((a) => (
            <div key={a.code} className={"auth-row" + (a.connected ? " on" : "")}>
              <div className="row" style={{ gap: 10 }}>
                <span className={"auth-logo auth-" + a.code}>{a.name[0]}</span>
                <div>
                  <div style={{ fontWeight: 600, fontSize: "0.9rem" }}>{a.name}</div>
                  <div className="card-note">{a.detail}</div>
                </div>
              </div>
              {a.connected
                ? <span className="chip" style={{ color: "var(--run-ink)" }}><span className="pdot p-run"></span>подключён</span>
                : <button className="btn-ghost-sm">Подключить</button>}
            </div>
          ))}
        </div>
      </div>

      <div className="banner-soft">
        <b>Не чаще раза в 30 минут.</b> Чтобы не нагружать сайты систем, ручное обновление каждой платформы ограничено. Плановая синхронизация идёт в фоне.
      </div>
    </div>
  );
}

Object.assign(window, { ProfilesView });
