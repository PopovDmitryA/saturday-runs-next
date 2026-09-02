# Юниты домашнего сервера

Домашний сервер (`saturday-run`, 192.168.1.26) исполняет код сайта против
продовой базы, поэтому обязан совпадать с продом по схеме. Подробности и
причина — в `AGENTS.md`, раздел «Деплой».

| Юнит | Что делает |
|---|---|
| `pm-home-sync.timer` | раз в 5 минут сверяет `.deployed_sha` с продом и подтягивается |
| `pm-site-queue.timer` | разбор очереди `profile_fetch_pending` пачками по 50 |

Установка:

```bash
sudo install -m644 deploy/home/pm-home-sync.{service,timer} /etc/systemd/system/
sudo systemctl daemon-reload && sudo systemctl enable --now pm-home-sync.timer
```

`home_sync.sh` останавливает и запускает таймер очереди через `sudo systemctl`,
поэтому пользователю `dmitry` нужно разрешить это без пароля:

```
dmitry ALL=(root) NOPASSWD: /usr/bin/systemctl stop pm-site-queue.timer, /usr/bin/systemctl start pm-site-queue.timer
```

Права намеренно узкие: только два конкретных действия над одним таймером.
