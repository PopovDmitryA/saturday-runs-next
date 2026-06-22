/* Saturday Runs — О проекте */
function AboutView() {
  const PLAT = window.SR.PLATFORMS;
  const contacts = [
    { title: "Личный Telegram", label: "@Popov_Dmitry", desc: "Написать автору напрямую" },
    { title: "Канал", label: "@popov_way", desc: "О субботних пробежках, жизни и цифрах" },
    { title: "Чат", label: "@popov_talk", desc: "Вопросы и обратная связь по сайту" },
  ];
  const authorProfiles = [
    { code: "five_verst", sub: "Дмитрий ПОПОВ · ID 790103773" },
    { code: "s95", sub: "Дмитрий ПОПОВ · athlete 5207" },
    { code: "parkrun", sub: "Дмитрий ПОПОВ · A7035519" },
  ];
  return (
    <div className="grid" style={{ gap: "var(--gap)" }}>
      <div className="hero rise in">
        <span className="eyebrow hero-eyebrow">О проекте</span>
        <h1 className="hero-title" style={{ fontSize: "clamp(1.6rem, 3.8vw, 2.5rem)" }}>Единая статистика субботних пробежек</h1>
        <p className="hero-lead">
          Личный кабинет для участников <b style={{ color: "var(--hero-ink)" }}>5 вёрст</b>, <b style={{ color: "var(--hero-ink)" }}>S95</b> и <b style={{ color: "var(--hero-ink)" }}>parkrun</b>:
          пробежки, волонтёрство, карта локаций и аналитика в одном месте — без переключения между сайтами.
        </p>
        <blockquote className="about-quote">
          «Когда путь уже виден целиком, первая пробежка в новой системе перестаёт казаться „с нуля“.»
          <footer>— Дмитрий ПОПОВ, автор проекта</footer>
        </blockquote>
      </div>

      <div className="grid cols-12">
        <div className="span-7">
          <div className="card" style={{ borderColor: "color-mix(in oklch, var(--gold) 45%, var(--border))", background: "color-mix(in oklch, var(--gold) 7%, var(--surface))", height: "100%" }}>
            <div className="row" style={{ gap: 12, alignItems: "flex-start" }}>
              <span className="disc-icon">!</span>
              <div>
                <div className="card-title" style={{ marginBottom: 8 }}>Неофициальный проект</div>
                <p className="sub" style={{ maxWidth: "none", marginBottom: 8 }}>
                  Сайт <b>не является официальным ресурсом</b> 5 вёрст, S95, parkrun или другой беговой системы.
                  Мы не аффилированы с организаторами стартов.
                </p>
                <p className="sub" style={{ maxWidth: "none", margin: 0 }}>
                  Все данные получены из <b>открытых публичных источников</b> и не принадлежат автору. В ЛК отображается ваша
                  статистика после входа и привязки профилей.
                </p>
              </div>
            </div>
          </div>
        </div>
        <div className="span-5">
          <div className="card" style={{ height: "100%" }}>
            <div className="row" style={{ gap: 14, alignItems: "center", marginBottom: 6 }}>
              <span className="avatar" style={{ width: 50, height: 50, fontSize: "1.05rem", background: "linear-gradient(135deg, var(--brand), var(--p-parkrun))" }}>DP</span>
              <div>
                <div className="card-title">Дмитрий ПОПОВ</div>
                <div className="card-note">автор проекта</div>
              </div>
            </div>
            <p className="sub" style={{ maxWidth: "none", margin: 0 }}>
              «Я такой же участник парковых пробежек — просто хотел видеть всю статистику по всем системам в одном месте.»
            </p>
          </div>
        </div>
      </div>

      <div className="card">
        <div className="card-head"><div className="card-title">Данные и приватность</div></div>
        <div className="grid cols-3">
          {[
            { t: "Что обрабатываем", d: "Учётка входа (Telegram/VK/Яндекс) и публичные данные профилей, которые вы добровольно привязали." },
            { t: "Зачем", d: "Сводная статистика, история активности, карта локаций и аналитика — в одном кабинете." },
            { t: "Отзыв согласия", d: "Можно отвязать профили и прекратить использование в любой момент. Согласие — добровольное действие при входе." },
          ].map((x, i) => (
            <div key={i} className="privacy-cell">
              <div className="privacy-num mono">0{i + 1}</div>
              <div style={{ fontWeight: 600, fontSize: "0.92rem", marginBottom: 4 }}>{x.t}</div>
              <div className="card-note" style={{ lineHeight: 1.5 }}>{x.d}</div>
            </div>
          ))}
        </div>
      </div>

      <div className="grid cols-12">
        <div className="span-7">
          <div className="card" style={{ height: "100%" }}>
            <div className="card-head"><div className="card-title">Контакты</div></div>
            <div className="grid" style={{ gap: 9 }}>
              {contacts.map((c) => (
                <a key={c.label} className="contact-row" href="#" onClick={(e) => e.preventDefault()}>
                  <span className="tg-logo">✈</span>
                  <div style={{ minWidth: 0 }}>
                    <div style={{ fontWeight: 600, fontSize: "0.9rem" }}>{c.title} <span className="mono muted-txt" style={{ fontSize: "0.76rem" }}>{c.label}</span></div>
                    <div className="card-note">{c.desc}</div>
                  </div>
                  <span className="muted-txt" style={{ marginLeft: "auto" }}>↗</span>
                </a>
              ))}
            </div>
          </div>
        </div>
        <div className="span-5">
          <div className="card" style={{ height: "100%" }}>
            <div className="card-head"><div className="card-title">Профили автора</div></div>
            <div className="grid" style={{ gap: 9 }}>
              {authorProfiles.map((p) => (
                <a key={p.code} className="contact-row" href="#" onClick={(e) => e.preventDefault()}>
                  <span className={"plogo p-" + p.code} style={{ width: 36, height: 36, fontSize: "0.7rem" }}>{PLAT[p.code].short}</span>
                  <div>
                    <div style={{ fontWeight: 600, fontSize: "0.9rem" }}>{PLAT[p.code].name}</div>
                    <div className="card-note">{p.sub}</div>
                  </div>
                  <span className="muted-txt" style={{ marginLeft: "auto" }}>↗</span>
                </a>
              ))}
            </div>
          </div>
        </div>
      </div>

      <a className="legacy-link" href="#" onClick={(e) => e.preventDefault()}>
        <span className="mono">grafana.run5k.run</span>
        <span>Прежняя версия — рейтинги и дашборды на Grafana</span>
        <span style={{ marginLeft: "auto" }}>↗</span>
      </a>
    </div>
  );
}

Object.assign(window, { AboutView });
