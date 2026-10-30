// Подгружается в node контейнера сборки фронта (NODE_OPTIONS=--require).
// OpenSSL 3.5 в node:22-alpine предлагает постквантовый X25519MLKEM768:
// ClientHello вырастает больше 1,5 КБ, и с домашнего сервера рукопожатие с
// Cloudflare (registry.npmjs.org) зависает — TCP есть, TLS нет. С 30.10.2026
// из-за этого вис npm ci в deploy_home.sh. Классические кривые дают обычный
// ClientHello; на сборку и на сайт это не влияет.
require("tls").DEFAULT_ECDH_CURVE = "X25519:prime256v1";
