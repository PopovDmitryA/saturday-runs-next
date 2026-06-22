/* Saturday Runs — app shell: nav, transitions, theme, tweaks */
const { useState: useStateA, useEffect: useEffectA, useRef: useRefA, useLayoutEffect } = React;

const TWEAK_DEFAULTS = /*EDITMODE-BEGIN*/{
  "theme": "light",
  "accent": "#2563eb",
  "density": "regular",
  "personalHero": "dark",
  "viewport": "Десктоп"
}/*EDITMODE-END*/;

const ACCENTS = {
  "#2563eb": { name: "Брендовый синий", brand: "oklch(0.55 0.20 256)", ink: "oklch(0.45 0.20 256)", darkBrand: "oklch(0.68 0.17 256)", darkInk: "oklch(0.78 0.15 256)" },
  "#0e9488": { name: "Бирюзовый", brand: "oklch(0.58 0.13 185)", ink: "oklch(0.48 0.12 185)", darkBrand: "oklch(0.70 0.12 185)", darkInk: "oklch(0.80 0.11 185)" },
  "#7c3aed": { name: "Фиолетовый", brand: "oklch(0.55 0.20 290)", ink: "oklch(0.46 0.19 290)", darkBrand: "oklch(0.70 0.17 290)", darkInk: "oklch(0.80 0.15 290)" },
  "#e11d48": { name: "Малиновый", brand: "oklch(0.58 0.20 18)", ink: "oklch(0.49 0.19 18)", darkBrand: "oklch(0.70 0.18 18)", darkInk: "oklch(0.80 0.16 18)" },
};

const Icon = ({ name }) => {
  const p = {
    community: <><circle cx="9" cy="8" r="3"/><circle cx="17" cy="10" r="2.3"/><path d="M3 19c0-3 2.7-5 6-5s6 2 6 5"/><path d="M15.5 14c2.3.4 3.9 2 3.9 4"/></>,
    personal: <><path d="M4 19V9"/><path d="M10 19V5"/><path d="M16 19v-7"/><path d="M22 19H2"/></>,
    runs: <><path d="M13 4l-2 5h4l-3 11"/><circle cx="17" cy="5" r="1.6"/></>,
    profiles: <><path d="M9 12a4 4 0 0 1 4-4h3a4 4 0 0 1 0 8h-1"/><path d="M15 12a4 4 0 0 1-4 4H8a4 4 0 0 1 0-8h1"/></>,
    about: <><circle cx="12" cy="12" r="9"/><path d="M12 11v5"/><circle cx="12" cy="7.5" r="0.6" fill="currentColor"/></>,
  }[name];
  return <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.9" strokeLinecap="round" strokeLinejoin="round" width="18" height="18">{p}</svg>;
};

const TABS = [
  { id: "community", label: "Сообщество", short: "Сообщество" },
  { id: "personal", label: "Мой профиль", short: "Профиль" },
  { id: "runs", label: "Пробежки", short: "Пробежки" },
  { id: "profiles", label: "Привязки", short: "Привязки" },
  { id: "about", label: "О проекте", short: "О нас" },
];

function NavTabs({ tab, setTab }) {
  const ref = useRefA(null);
  const [pill, setPill] = useStateA({ left: 4, width: 0 });
  const measure = () => {
    const el = ref.current; if (!el) return;
    const btn = el.querySelector(`button[data-tab="${tab}"]`);
    if (btn) setPill({ left: btn.offsetLeft, width: btn.offsetWidth });
  };
  useLayoutEffect(measure, [tab]);
  useEffectA(() => {
    window.addEventListener("resize", measure);
    return () => window.removeEventListener("resize", measure);
  }, [tab]);
  return (
    <div className="navtabs" ref={ref}>
      <span className="seg-pill" style={{ left: pill.left, width: pill.width }}></span>
      {TABS.map((t) => (
        <button key={t.id} data-tab={t.id} className={"navtab" + (tab === t.id ? " on" : "")} onClick={() => setTab(t.id)}>{t.label}</button>
      ))}
    </div>
  );
}

function BottomNav({ tab, setTab }) {
  return (
    <nav className="bottomnav">
      {TABS.map((t) => (
        <button key={t.id} className={tab === t.id ? "on" : ""} onClick={() => setTab(t.id)}>
          <Icon name={t.id} />
          {t.short}
        </button>
      ))}
    </nav>
  );
}

function ThemeToggle({ theme, onToggle }) {
  return (
    <button className="theme-toggle" onClick={onToggle} title={theme === "dark" ? "Светлая тема" : "Тёмная тема"} aria-label="Переключить тему">
      {theme === "dark" ? (
        <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" strokeWidth="1.9" strokeLinecap="round"><circle cx="12" cy="12" r="4.5"/><path d="M12 2v2M12 20v2M4.2 4.2l1.4 1.4M18.4 18.4l1.4 1.4M2 12h2M20 12h2M4.2 19.8l1.4-1.4M18.4 5.6l1.4-1.4"/></svg>
      ) : (
        <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" strokeWidth="1.9" strokeLinecap="round" strokeLinejoin="round"><path d="M20 14.5A8 8 0 0 1 9.5 4 7 7 0 1 0 20 14.5z"/></svg>
      )}
    </button>
  );
}

function initialTab() {
  const h = (location.hash || "").replace(/[#/]/g, "").split(/[&?]/)[0];
  return TABS.some((t) => t.id === h) ? h : "community";
}

function App() {
  const [t, setTweak] = useTweaks(TWEAK_DEFAULTS);
  const [tab, setTab] = useStateA(initialTab());
  const [active, setActive] = useStateA(true);
  const firstRun = useRefA(true);

  useEffectA(() => {
    const root = document.documentElement;
    root.setAttribute("data-theme", t.theme);
    root.setAttribute("data-density", t.density);
    const a = ACCENTS[t.accent] || ACCENTS["#2563eb"];
    if (t.theme === "dark") {
      root.style.setProperty("--brand", a.darkBrand);
      root.style.setProperty("--brand-ink", a.darkInk);
      root.style.setProperty("--vol", a.darkBrand);
    } else {
      root.style.setProperty("--brand", a.brand);
      root.style.setProperty("--brand-ink", a.ink);
      root.style.setProperty("--vol", a.brand);
    }
  }, [t.theme, t.density, t.accent]);

  useEffectA(() => {
    if (firstRun.current) { firstRun.current = false; setActive(true); return; }
    setActive(false);
    const id = setTimeout(() => setActive(true), 80);
    window.scrollTo({ top: 0, behavior: "smooth" });
    return () => clearTimeout(id);
  }, [tab]);

  const view = () => {
    switch (tab) {
      case "community": return <CommunityView active={active} />;
      case "personal": return <PersonalView active={active} heroStyle={t.personalHero} />;
      case "runs": return <RunsView />;
      case "profiles": return <ProfilesView />;
      case "about": return <AboutView />;
      default: return null;
    }
  };

  const mobile = t.viewport === "Телефон";
  return (
    <div className={"shell" + (mobile ? " vm" : "")}>
      <header className="topbar">
        <div className="wrap topbar-inner">
          <div className="brand" onClick={() => setTab("community")}>
            <div className="brand-mark"><span></span></div>
            <div>
              <div className="brand-name">Saturday <b>Runs</b></div>
              <div className="brand-sub">аналитика · 5 вёрст · s95 · parkrun</div>
            </div>
          </div>
          <NavTabs tab={tab} setTab={setTab} />
          <ThemeToggle theme={t.theme} onToggle={() => setTweak("theme", t.theme === "dark" ? "light" : "dark")} />
        </div>
      </header>

      <main className="wrap section section-pad-top">
        <div key={tab} style={{ animation: "viewIn 0.5s var(--ease)" }}>
          {view()}
        </div>

        <footer className="foot">
          <div className="row between wrapf" style={{ gap: 14 }}>
            <div>
              <b style={{ color: "var(--ink)", fontFamily: "var(--font-display)" }}>Saturday Runs</b> — неофициальный единый профиль парковых пробежек.
              <br />Данные на макете вымышленные. Источники: 5 вёрст · S95 · parkrun.
            </div>
            <div className="row" style={{ gap: 10 }}>
              <PlatformChip code="five_verst" />
              <PlatformChip code="s95" />
              <PlatformChip code="parkrun" />
            </div>
          </div>
        </footer>
      </main>

      <BottomNav tab={tab} setTab={setTab} />

      <TweaksPanel>
        <TweakSection label="Тема" />
        <TweakRadio label="Оформление" value={t.theme} options={["light", "dark"]} onChange={(v) => setTweak("theme", v)} />
        <TweakColor label="Акцент" value={t.accent} options={Object.keys(ACCENTS)} onChange={(v) => setTweak("accent", v)} />
        <TweakSection label="Плотность и подача" />
        <TweakRadio label="Просмотр" value={t.viewport} options={["Десктоп", "Телефон"]} onChange={(v) => setTweak("viewport", v)} />
        <TweakRadio label="Плотность" value={t.density} options={["compact", "regular", "comfy"]} onChange={(v) => setTweak("density", v)} />
        <TweakRadio label="Герой профиля" value={t.personalHero} options={["dark", "compact"]} onChange={(v) => setTweak("personalHero", v)} />
      </TweaksPanel>
    </div>
  );
}

ReactDOM.createRoot(document.getElementById("root")).render(<App />);
