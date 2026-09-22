# Домены run5k.run

**Текущая схема (с 04.09.2026):**

| Домен | Назначение | Конфиг host-nginx |
|-------|------------|-------------------|
| `https://run5k.run` | Сайт (Docker nginx → 127.0.0.1:8080) | `deploy/nginx/run5k.run.conf` |
| `https://app.run5k.run` | Старый адрес ЛК: 301 → run5k.run | `deploy/nginx/app.run5k.run.conf` |
| `https://grafana.run5k.run` | **Заглушка** «дашборды переехали на run5k.run» | `deploy/nginx/grafana.run5k.run.farewell.conf` + `deploy/grafana/farewell/index.html` |

Host-nginx деплоем не перезапускается: правки конфигов применяются руками
(`cp` в `/etc/nginx/sites-available/…` и `nginx -s reload`).

## grafana.run5k.run — закрыт

Legacy-статистика в Grafana закрыта 04.09.2026: дашборды перенесены в разделы
сайта, домен отдаёт прощальную страницу со ссылками на них. Заглушку ставит
`scripts/install_grafana_farewell.sh` (sudo; снимок прежнего конфига кладётся
рядом — им же и откатываться). Сама Grafana на 127.0.0.1:9000 и сборщики
старой БД гасятся `scripts/stop_legacy_grafana.sh` — он отказывается работать,
пока не стоит заглушка, иначе домен отдавал бы 502.

Счётчик `/__count/hit` на заглушке оставлен намеренно: по нему видно, кто ещё
ходит по старым адресам (отчёт — `scripts/archive/grafana/grafana_usage_report.py`).

**Редиректа на grafana.run5k.run нет — и не нужно его возвращать.** Закладки
со времён, когда Grafana жила на apex-домене (`run5k.run/d/<uid>/…`,
`/dashboard/…`, `/public/…`), раньше уходили 301-м на поддомен. Теперь такой
запрос идёт в приложение: известный uid дашборда открывает свою страницу сайта
(`LEGACY_DASHBOARD_ROUTES` в `frontend/src/lib/siteBrand.ts`), незнакомый —
объяснение «старая статистика закрыта». Вернуть редирект = снова увести людей
на закрытый домен, а с него — обратно на сайт (кольцо).

## OAuth (ручная настройка в кабинетах)

| Провайдер | Redirect URI |
|-----------|--------------|
| VK ID | `https://run5k.run/oauth/vk/callback` |
| Yandex ID | `https://run5k.run/oauth/yandex/callback` |

## Smoke tests

```bash
curl -sI https://run5k.run/health
curl -sI https://app.run5k.run/            # Location: https://run5k.run/...
curl -sI https://grafana.run5k.run/        # 200, заглушка
curl -sI https://run5k.run/d/anything      # 200, разбирает приложение (не 301)
```

## История: переезд июня 2026

Apex-домен отдали личному кабинету, `app.run5k.run` увели редиректом, Grafana
временно переехала на `grafana.run5k.run` с собственным TLS и systemd-override
(`deploy/grafana/systemd-override.conf`). Скрипты того переезда
(`deploy_run5k_domains.sh`, `remote_switch_domains.sh`) и выпуска TLS для
Grafana лежат в `scripts/archive/domains/` и `scripts/archive/grafana/`;
рабочий конфиг Grafana-эпохи — `deploy/nginx/grafana.run5k.run.conf`
(+ `.http.conf` для certbot) — оставлен для истории и отката.
