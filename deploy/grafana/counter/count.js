/*
 * Счётчик посещений legacy-Grafana (grafana.run5k.run) на время переезда
 * дашбордов на run5k.run. Задача одна: понять, ходит ли туда кто-то живой,
 * на какие дашборды и есть ли кто-то прямо сейчас.
 *
 * Как попадает на страницу: host nginx через sub_filter вставляет
 * <script src="/__count.js"> перед </body> в HTML Grafana (см.
 * deploy/nginx/grafana.run5k.run.conf). Сама Grafana не трогается.
 *
 * Как считает: шлёт запрос на /__count/hit, который nginx пишет в
 * /var/log/nginx/grafana_hits.log и отдаёт 204. Никакого бэкенда.
 * Скрипт исполняют только настоящие браузеры — поэтому в этом логе,
 * в отличие от access.log, нет сканеров и ботов.
 */
(function () {
  "use strict";
  try {
    var STORE_KEY = "gf_visitor_id";
    var HEARTBEAT_MS = 60000; // «кто сейчас на сайте» — раз в минуту, пока вкладка видима

    // Идентификатор браузера: считаем людей, а не IP (мобильные IP пляшут,
    // а за NAT'ом наоборот слипаются).
    var vid = null;
    try {
      vid = localStorage.getItem(STORE_KEY);
      if (!vid) {
        vid = Date.now().toString(36) + Math.random().toString(36).slice(2, 8);
        localStorage.setItem(STORE_KEY, vid);
      }
    } catch (e) {
      vid = "nostore"; // приватный режим — считаем такие визиты общей кучей
    }

    var lastPath = null;

    function hit(kind) {
      var path = location.pathname + location.search;
      if (kind === "nav" && path === lastPath) return; // Grafana дёргает history и без смены URL
      lastPath = path;
      var url =
        "/__count/hit?v=" + encodeURIComponent(vid) +
        "&p=" + encodeURIComponent(path) +
        "&t=" + kind;
      try {
        if (navigator.sendBeacon) navigator.sendBeacon(url);
        else new Image().src = url + "&_=" + Date.now();
      } catch (e) {
        /* счётчик не имеет права ломать страницу */
      }
    }

    hit("load");

    // Grafana — SPA: переход между дашбордами меняет URL через history API,
    // без единого запроса к серверу за HTML. Без этой обёртки в лог попадёт
    // только первый открытый дашборд за визит.
    ["pushState", "replaceState"].forEach(function (name) {
      var orig = history[name];
      if (typeof orig !== "function") return;
      history[name] = function () {
        var out = orig.apply(this, arguments);
        setTimeout(function () { hit("nav"); }, 0);
        return out;
      };
    });
    window.addEventListener("popstate", function () { hit("nav"); });

    // Пульс: показывает, что вкладка открыта и человек ещё здесь.
    setInterval(function () {
      if (!document.hidden) hit("ping");
    }, HEARTBEAT_MS);
  } catch (e) {
    /* молча: счётчик — не повод уронить дашборд */
  }
})();
