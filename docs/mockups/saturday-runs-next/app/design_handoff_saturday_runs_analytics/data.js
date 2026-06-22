/* Saturday Runs — realistic (fictional) mock dataset.
   Attached to window.SR. Plain JS, loaded before the React app. */
(function () {
  "use strict";

  // ---- helpers ----
  const mmss = (sec) => {
    const m = Math.floor(sec / 60);
    const s = Math.round(sec % 60);
    return `${m}:${String(s).padStart(2, "0")}`;
  };

  const PLATFORMS = {
    five_verst: { code: "five_verst", name: "5 вёрст", short: "5в", color: "var(--p-5verst)" },
    s95: { code: "s95", name: "S95", short: "S95", color: "var(--p-s95)" },
    parkrun: { code: "parkrun", name: "parkrun", short: "pr", color: "var(--p-parkrun)" },
  };

  // ============================================================
  // COMMUNITY (общая статистика)
  // ============================================================

  // Monthly community growth, 2019-01 .. 2026-05 (cumulative participants + finishes/mo)
  const growth = [];
  {
    let cumP = 1200;
    const start = new Date(2019, 0, 1);
    const months = 89; // through 2026-05
    for (let i = 0; i < months; i++) {
      const d = new Date(start.getFullYear(), start.getMonth() + i, 1);
      const y = d.getFullYear();
      const mo = d.getMonth(); // 0-11
      // seasonal: more in warm months, COVID gap mid-2020, steady growth
      const covid = y === 2020 && mo >= 2 && mo <= 7;
      const season = 0.7 + 0.5 * Math.sin(((mo - 3) / 12) * Math.PI * 2);
      const base = (140 + i * 7) * season;
      const newP = covid ? Math.round(base * 0.06) : Math.round(base * (0.9 + (i % 5) * 0.03));
      cumP += newP;
      const finishes = covid ? Math.round(base * 0.3) : Math.round(base * (6.5 + i * 0.06));
      growth.push({
        month: `${y}-${String(mo + 1).padStart(2, "0")}`,
        year: y,
        participants: cumP,
        finishes,
        covid,
      });
    }
  }

  const lastG = growth[growth.length - 1];

  const community = {
    totals: {
      participants: lastG.participants, // ~ shown via count-up
      finishes: 1284600,
      locations: 312,
      volunteer_slots: 198400,
      total_km: 6423000,
      avg_weekly: 9120,
    },
    totalsMeta: {
      participants_delta: "+3.2% / мес",
      finishes_delta: "+9 120 / нед",
      locations_delta: "+6 за 90 дней",
      total_km_delta: "≈ 160× вокруг Земли",
    },
    growth,
    // platform split (finishes)
    platforms: [
      { ...PLATFORMS.five_verst, finishes: 712400, participants: 31200, locations: 184, avg_time: 1602, avg_pace: 320, share: 55.5 },
      { ...PLATFORMS.s95, finishes: 388900, participants: 14800, locations: 96, avg_time: 1571, avg_pace: 314, share: 30.3 },
      { ...PLATFORMS.parkrun, finishes: 183300, participants: 9600, locations: 32, avg_time: 1648, avg_pace: 329, share: 14.2 },
    ],
    // top locations by finishes
    topLocations: [
      { name: "Измайловский парк", city: "Москва", platform: "five_verst", finishes: 41280, participants: 6840, region: "Москва" },
      { name: "Кузьминки", city: "Москва", platform: "five_verst", finishes: 33970, participants: 5410, region: "Москва" },
      { name: "Парк 300-летия", city: "Санкт-Петербург", platform: "s95", finishes: 31540, participants: 5020, region: "Санкт-Петербург" },
      { name: "Мещерское", city: "Москва", platform: "five_verst", finishes: 28110, participants: 4690, region: "Москва" },
      { name: "Сокольники", city: "Москва", platform: "parkrun", finishes: 26730, participants: 4470, region: "Москва" },
      { name: "Кудыкина гора", city: "Липецк", platform: "s95", finishes: 21090, participants: 3380, region: "Липецкая обл." },
      { name: "Победы", city: "Казань", platform: "s95", finishes: 19840, participants: 3110, region: "Татарстан" },
      { name: "Гагарина", city: "Екатеринбург", platform: "five_verst", finishes: 18260, participants: 2920, region: "Свердловская обл." },
      { name: "Дендрарий", city: "Сочи", platform: "parkrun", finishes: 15730, participants: 2510, region: "Краснодарский край" },
      { name: "Заречье", city: "Новосибирск", platform: "five_verst", finishes: 14410, participants: 2280, region: "Новосибирская обл." },
      { name: "Швейцария", city: "Нижний Новгород", platform: "s95", finishes: 13720, participants: 2190, region: "Нижегородская обл." },
      { name: "Юбилейный", city: "Краснодар", platform: "five_verst", finishes: 12350, participants: 1980, region: "Краснодарский край" },
    ],
    // course records (per location best ever) + absolute
    records: {
      absolute: [
        { scope: "Абсолютный рекорд", time: 880, who: "Д. Соколов", where: "Измайловский парк", platform: "five_verst", date: "2024-08-17" },
        { scope: "Женский рекорд", time: 968, who: "А. Романова", where: "Парк 300-летия", platform: "s95", date: "2023-09-09" },
        { scope: "Юниоры (до 18)", time: 945, who: "М. Орлов", where: "Сокольники", platform: "parkrun", date: "2025-05-31" },
        { scope: "Ветераны (60+)", time: 1118, who: "В. Кузьмин", where: "Кузьминки", platform: "five_verst", date: "2024-06-22" },
      ],
      courses: [
        { where: "Измайловский парк", city: "Москва", platform: "five_verst", time: 880, who: "Д. Соколов", date: "2024-08-17" },
        { where: "Парк 300-летия", city: "СПб", platform: "s95", time: 902, who: "И. Лебедев", date: "2025-03-15" },
        { where: "Сокольники", city: "Москва", platform: "parkrun", time: 911, who: "Т. Новак", date: "2024-10-12" },
        { where: "Кузьминки", city: "Москва", platform: "five_verst", time: 923, who: "Р. Беляев", date: "2025-04-19" },
        { where: "Мещерское", city: "Москва", platform: "five_verst", time: 934, who: "С. Громов", date: "2023-07-08" },
        { where: "Победы", city: "Казань", platform: "s95", time: 949, who: "А. Гарипов", date: "2024-09-28" },
      ],
    },
    // participant leaderboards
    leaderboards: {
      runs: [
        { name: "Сергей Поляков", city: "Москва", value: 521, sub: "9 лет · 18 локаций", hue: 28 },
        { name: "Ольга Денисова", city: "СПб", value: 498, sub: "8 лет · 24 локации", hue: 168 },
        { name: "Андрей Титов", city: "Казань", value: 476, sub: "две системы", hue: 288 },
        { name: "Марина Швец", city: "Сочи", value: 461, sub: "31 локация", hue: 90 },
        { name: "Денис Лазарев", city: "Москва", value: 444, sub: "5 вёрст", hue: 256 },
        { name: "Юлия Краева", city: "Екатеринбург", value: 430, sub: "три системы", hue: 12 },
        { name: "Павел Ершов", city: "Москва", value: 419, sub: "S95", hue: 200 },
      ],
      pr: [
        { name: "Тимур Новак", city: "Москва", value: 38, sub: "PR · 14:31 личник", hue: 288 },
        { name: "Дарья Соловьёва", city: "СПб", value: 35, sub: "PR · 16:02", hue: 168 },
        { name: "Игорь Лебедев", city: "СПб", value: 33, sub: "PR · 15:02", hue: 256 },
        { name: "Алексей Морозов", city: "Москва", value: 31, sub: "PR · 19:42 — это вы", hue: 78, you: true },
        { name: "Никита Фомин", city: "Казань", value: 29, sub: "PR · 17:18", hue: 28 },
        { name: "Елена Зорина", city: "Сочи", value: 27, sub: "PR · 18:44", hue: 90 },
        { name: "Артём Белов", city: "Москва", value: 25, sub: "PR · 16:55", hue: 200 },
      ],
      volunteering: [
        { name: "Галина Орлова", city: "Москва", value: 214, sub: "9 ролей · директор забега", hue: 256 },
        { name: "Виктор Кузьмин", city: "Москва", value: 187, sub: "ветеран-волонтёр", hue: 28 },
        { name: "Наталья Гусева", city: "Казань", value: 173, sub: "тайминг · сканер", hue: 168 },
        { name: "Олег Дроздов", city: "СПб", value: 158, sub: "фотограф", hue: 288 },
        { name: "Светлана Рыжова", city: "Сочи", value: 141, sub: "8 ролей", hue: 90 },
        { name: "Роман Беляев", city: "Москва", value: 129, sub: "маршал", hue: 200 },
        { name: "Алексей Морозов", city: "Москва", value: 38, sub: "это вы", hue: 78, you: true },
      ],
    },
    // geography: stylized normalized coords (x,y in 0..1) sized by finishes
    geo: [
      { name: "Москва", x: 0.40, y: 0.46, finishes: 248000, locs: 64 },
      { name: "Санкт-Петербург", x: 0.34, y: 0.30, finishes: 132000, locs: 28 },
      { name: "Казань", x: 0.52, y: 0.50, finishes: 71000, locs: 16 },
      { name: "Нижний Новгород", x: 0.47, y: 0.47, finishes: 54000, locs: 11 },
      { name: "Екатеринбург", x: 0.60, y: 0.44, finishes: 61000, locs: 14 },
      { name: "Новосибирск", x: 0.72, y: 0.56, finishes: 47000, locs: 12 },
      { name: "Сочи", x: 0.40, y: 0.74, finishes: 38000, locs: 7 },
      { name: "Краснодар", x: 0.38, y: 0.69, finishes: 42000, locs: 9 },
      { name: "Самара", x: 0.55, y: 0.55, finishes: 33000, locs: 8 },
      { name: "Воронеж", x: 0.42, y: 0.58, finishes: 29000, locs: 7 },
      { name: "Липецк", x: 0.43, y: 0.55, finishes: 31000, locs: 6 },
      { name: "Владивосток", x: 0.92, y: 0.66, finishes: 12000, locs: 4 },
      { name: "Калининград", x: 0.21, y: 0.40, finishes: 16000, locs: 5 },
      { name: "Уфа", x: 0.58, y: 0.52, finishes: 27000, locs: 6 },
      { name: "Ростов-на-Дону", x: 0.39, y: 0.64, finishes: 35000, locs: 8 },
    ],
  };

  // ============================================================
  // PERSONAL (Алексей Морозов)
  // ============================================================

  // pace trend (monthly avg pace sec/km, last 18 months) — improving overall
  const paceTrend = [];
  {
    const start = new Date(2024, 11, 1);
    let base = 286;
    for (let i = 0; i < 18; i++) {
      const d = new Date(start.getFullYear(), start.getMonth() + i, 1);
      base -= 1.1 + (i % 3 === 0 ? 2.2 : 0);
      const noise = Math.sin(i * 1.7) * 4;
      paceTrend.push({
        month: `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}`,
        pace: Math.round(base + noise),
      });
    }
  }

  // finish-time distribution across community (buckets in seconds), with "you" bucket
  const distribution = (() => {
    const buckets = [];
    // 16:00 (960) .. 36:00 (2160), 1-min buckets
    const centers = [];
    for (let t = 960; t <= 2160; t += 60) centers.push(t);
    // bell-ish around 22:30 (1350)
    centers.forEach((c) => {
      const z = (c - 1350) / 240;
      const count = Math.round(2600 * Math.exp(-0.5 * z * z) + 40);
      buckets.push({ center: c, count });
    });
    return { buckets, you: 1182, fasterThanPct: 78 }; // your best ~19:42
  })();

  // saturday heat calendar — 52 weeks back, value 0..3 (0 none,1 vol,2 run,3 run+vol/pr)
  const heat = (() => {
    const cells = [];
    for (let w = 0; w < 52; w++) {
      // active most saturdays, gaps in winter
      const winter = w >= 30 && w <= 40;
      const r = Math.random();
      let v = 0;
      if (winter) v = r < 0.45 ? 0 : r < 0.7 ? 2 : r < 0.85 ? 1 : 3;
      else v = r < 0.12 ? 0 : r < 0.6 ? 2 : r < 0.8 ? 3 : 1;
      cells.push(v);
    }
    return cells;
  })();

  const personal = {
    name: "Алексей Морозов",
    initials: "АМ",
    since: "2019-02-09",
    platforms: ["five_verst", "s95", "parkrun"],
    totals: {
      runs: 143,
      volunteering: 38,
      best: 1182, // 19:42
      avg: 1335, // 22:15
      avg_pace: 267, // 4:27 /km
      locations: 27,
      regions: 6,
      cities: 9,
      total_km: 715,
      pr_count: 31,
      days_since_first: 2677,
      saturday_streak: 11,
    },
    hero: {
      runs: 143,
      volunteering: 38,
      best: 1182,
      locations: 27,
    },
    paceTrend,
    distribution,
    heat,
    // run clubs (milestone clubs by total runs)
    clubs: { earned: [10, 25, 50, 100], next: 250, current: 143 },
    // personal records
    records: [
      { where: "Измайловский парк", platform: "five_verst", time: 1182, date: "2025-09-13", global: true },
      { where: "Кузьминки", platform: "five_verst", time: 1201, date: "2025-06-21", global: false },
      { where: "Сокольники", platform: "parkrun", time: 1224, date: "2024-10-19", global: false },
      { where: "Парк 300-летия", platform: "s95", time: 1238, date: "2025-03-08", global: false },
      { where: "Мещерское", platform: "five_verst", time: 1256, date: "2024-07-27", global: false },
    ],
    // percentiles vs community
    percentiles: [
      { label: "Лучший результат", meta: "19:42 · быстрее 78% бегунов", pct: 78 },
      { label: "Всего пробежек", meta: "143 · топ 9% сообщества", pct: 91 },
      { label: "Уникальные локации", meta: "27 · топ 14%", pct: 86 },
      { label: "Волонтёрство", meta: "38 · топ 6%", pct: 94 },
      { label: "Постоянство (субботы)", meta: "73% за год · топ 18%", pct: 82 },
    ],
    // activity by month last 12 (runs/vol)
    activity: (() => {
      const a = [];
      const start = new Date(2025, 5, 1);
      for (let i = 0; i < 12; i++) {
        const d = new Date(start.getFullYear(), start.getMonth() + i, 1);
        const runs = [3, 4, 2, 4, 3, 1, 0, 2, 3, 4, 3, 4][i];
        const vol = [1, 0, 1, 0, 1, 2, 1, 0, 1, 0, 1, 1][i];
        a.push({ month: `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}`, runs, vol });
      }
      return a;
    })(),
    topRole: { role: "Сканирование штрихкодов", count: 14 },
    topLocation: { name: "Измайловский парк", count: 41 },
  };

  // ---- detailed individual runs (for the Пробежки section) ----
  personal.runsList = (function () {
    const locs = [
      { name: "Измайловский парк", city: "Москва", platform: "five_verst" },
      { name: "Кузьминки", city: "Москва", platform: "five_verst" },
      { name: "Мещерское", city: "Москва", platform: "five_verst" },
      { name: "Сокольники", city: "Москва", platform: "parkrun" },
      { name: "Парк 300-летия", city: "Санкт-Петербург", platform: "s95" },
      { name: "Победы", city: "Казань", platform: "s95" },
      { name: "Гагарина", city: "Екатеринбург", platform: "five_verst" },
    ];
    const out = [];
    let d = new Date(2026, 4, 31); // last Saturday shown
    const baseTime = 1182; // improves toward recent; min = personal best 19:42
    for (let i = 0; i < 42; i++) {
      const loc = locs[(i * 3 + (i % 5)) % locs.length];
      const drift = i * 4.2; // older runs slower
      const noise = Math.round(Math.sin(i * 1.9) * 38 + (i % 4) * 14);
      const time = Math.round(baseTime + drift + noise);
      const pos = 6 + ((i * 7 + 3) % 58);
      out.push({
        date: new Date(d).toISOString().slice(0, 10),
        platform: loc.platform,
        location: loc.name,
        city: loc.city,
        position: pos,
        time,
        pace: Math.round(time / 5),
        event_number: 320 - i,
        is_pr: false,
        is_global_pr: false,
        is_test: i === 17,
      });
      d.setDate(d.getDate() - 7);
    }
    // mark PRs chronologically (asc)
    const asc = [...out].reverse();
    let best = Infinity;
    asc.forEach((r) => {
      if (r.time < best) { r.is_pr = true; best = r.time; }
    });
    let gb = Infinity, gbi = -1;
    out.forEach((r, idx) => { if (r.time < gb) { gb = r.time; gbi = idx; } });
    if (gbi >= 0) out[gbi].is_global_pr = true;
    return out;
  })();

  // ---- linked profiles (for the Профили section) ----
  const profiles = {
    links: [
      {
        code: "five_verst", name: "Алексей Морозов", externalId: "790096427",
        status: "linked", lastSync: "2026-05-31 08:42", runs: 96, vol: 22,
        url: "https://5verst.ru/userstats/790096427/", dataThrough: "2026-05-30",
        hint: "Ссылка https://5verst.ru/userstats/… или код участника.",
      },
      {
        code: "s95", name: "Алексей Морозов", externalId: "5207",
        status: "linked", lastSync: "2026-05-29 19:10", runs: 31, vol: 11,
        url: "https://s95.ru/athletes/5207/", dataThrough: "2026-05-24",
        hint: "Ссылка на профиль участника, например https://s95.ru/athletes/5207/",
      },
      {
        code: "parkrun", name: "Aleksey Morozov", externalId: "A7035519",
        status: "unlinked", lastSync: null, runs: 16, vol: 5,
        url: "https://www.parkrun.org.uk/parkrunner/7035519/", dataThrough: null,
        hint: "Ссылка parkrun.org.uk, штрихкод A7035519 или ID 7035519.",
      },
    ],
    auth: [
      { code: "telegram", name: "Telegram", connected: true, detail: "@aleksey_m" },
      { code: "vk", name: "VK", connected: false, detail: "не подключён" },
      { code: "yandex", name: "Яндекс", connected: false, detail: "не подключён" },
    ],
  };

  window.SR = { PLATFORMS, community, personal, profiles, fmt: { mmss } };
})();
