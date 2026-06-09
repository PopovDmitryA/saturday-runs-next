# Переключение доменов run5k.run

**Целевая схема (июнь 2026):**

| Домен | Назначение |
|-------|------------|
| `https://run5k.run` | Новый ЛК (Docker → 127.0.0.1:8080) |
| `https://grafana.run5k.run` | Legacy Grafana (127.0.0.1:9000) |
| `https://app.run5k.run` | 301 → run5k.run |

## 1. DNS

Добавить A/AAAA записи на IP сервера (`195.58.34.112`):

- `run5k.run` — уже есть
- `www.run5k.run` — опционально (редirect на apex)
- **`grafana.run5k.run`** — новая запись

## 2. Деплой на сервере

```bash
cd /opt/saturday-runs-next
bash scripts/deploy_run5k_domains.sh
```

Скрипт требует **sudo** (nginx, certbot, grafana-server). Если sudo запрашивает пароль — выполнить интерактивно по SSH.

### Вручную (если скрипт недоступен)

```bash
# Host nginx
sudo cp deploy/nginx/run5k.run.conf /etc/nginx/sites-available/run5k.run
sudo cp deploy/nginx/app.run5k.run.conf /etc/nginx/sites-available/app.run5k.run
sudo ln -sf /etc/nginx/sites-available/run5k.run /etc/nginx/sites-enabled/
sudo ln -sf /etc/nginx/sites-available/app.run5k.run /etc/nginx/sites-enabled/

# Grafana TLS (после DNS grafana.run5k.run)
sudo cp deploy/nginx/grafana.run5k.run.http.conf /etc/nginx/sites-available/grafana.run5k.run
sudo ln -sf /etc/nginx/sites-available/grafana.run5k.run /etc/nginx/sites-enabled/
sudo nginx -t && sudo systemctl reload nginx
sudo certbot --nginx -d grafana.run5k.run --redirect
sudo cp deploy/nginx/grafana.run5k.run.conf /etc/nginx/sites-available/grafana.run5k.run
sudo nginx -t && sudo systemctl reload nginx

# Grafana public URL
sudo mkdir -p /etc/systemd/system/grafana-server.service.d
sudo cp deploy/grafana/systemd-override.conf /etc/systemd/system/grafana-server.service.d/run5k-subdomain.conf
sudo systemctl daemon-reload && sudo systemctl restart grafana-server

# .env (если ещё app.run5k.run)
sed -i 's|https://app.run5k.run|https://run5k.run|g' .env
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --force-recreate api vk-bot
```

## 3. OAuth (ручная настройка в кабинетах)

| Провайдер | Redirect URI |
|-----------|--------------|
| VK ID | `https://run5k.run/oauth/vk/callback` |
| Yandex ID | `https://run5k.run/oauth/yandex/callback` |

## 4. Smoke tests

```bash
curl -sI https://run5k.run/health
curl -sI https://grafana.run5k.run/api/health
curl -sI https://app.run5k.run/   # Location: https://run5k.run/...
```

## 5. Файлы в репозитории

- `deploy/nginx/run5k.run.conf`
- `deploy/nginx/grafana.run5k.run.conf` (+ `.http.conf` для certbot bootstrap)
- `deploy/nginx/app.run5k.run.conf`
- `deploy/grafana/systemd-override.conf`
- `frontend/src/lib/siteBrand.ts` — ссылка «Прежняя версия» → grafana.run5k.run
