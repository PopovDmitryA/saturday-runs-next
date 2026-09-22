# Общие настройки проксирования /api/* в FastAPI. Подключается include'ом из
# location'ов в default.conf: у /api/ и /api/auth/ разные limit_req, а всё
# остальное обязано совпадать. Расширение .inc, а не .conf: nginx.conf образа
# включает conf.d/*.conf сам, и такой файл подхватился бы на уровне http, где
# proxy_pass запрещён.

# Загрузка аватарок: дефолтный 1m резал multipart ещё на nginx (413).
# Бэкенд сам ограничивает 10 МБ и сжимает до 256×256.
client_max_body_size 12m;
# Через переменную — чтобы resolver перечитывал api:8000 после пересоздания
# контейнера (иначе устаревший IP → 502).
set $api_upstream http://api:8000;
proxy_pass $api_upstream;
proxy_set_header Host $host;
# После realip (см. начало default.conf) $remote_addr — настоящий клиент;
# приложение верит именно X-Real-IP (app/core/client_ip.py).
proxy_set_header X-Real-IP $remote_addr;
proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
proxy_set_header X-Forwarded-Proto $scheme;
proxy_read_timeout 300s;
proxy_connect_timeout 60s;
proxy_send_timeout 300s;
