# deeptraversal

**Self-service key provisioning для AmneziaWG.** Выдача ключей, IP-пул, отзыв доступа —
на вашем собственном сервере. Телеграм-бот здесь просто один из фронтендов (CLI уже есть,
HTTP API — в roadmap), вся логика живёт рядом с VPN и правит его конфиг напрямую.

No panel API, no third-party infrastructure: works on the same box as AmneziaWG, native
install or inside Amnezia's Docker container.

Вместо «зайди по SSH → `awg genkey` → придумай IP → допиши peer → собери .conf → скинь
файлом» клиент жмёт кнопку в Телеграме и получает готовый `.conf`, который импортируется в
приложение в два тапа.

```
пользователь            бот (этот репозиторий)                  сервер
───────────             ─────────────────────────                ─────────────
/start  ─────────────▶  заявка в SQLite
                        уведомление админу ──▶ [Выдать]/[Отказать]
"Выдать" ────────────▶  X25519 keypair + PSK + свободный IP из пула
                        правка awg0.conf (managed-блок) + awg syncconf
   ◀──── .conf файлом   рендер клиентского конфига
```

## Что умеет

- выдача конфига по кнопке, с одобрением админа или сразу (`REQUIRE_APPROVAL=false`);
- **две формы выдачи**: ссылка `vpn://` (импорт одним тапом, как в самом AmneziaVPN) и файл
  `.conf` (fallback для старых версий и для `amneziawg`-клиентов); переключается
  `SEND_VPN_LINK` / `SEND_CONF_FILE`;
- пул адресов: IP выдаётся и переиспользуется автоматически, дубликатов не бывает;
- один ключ на пользователя (настраивается), перевыпуск = отзыв старого;
- `/revoke` — пользователь сам закрывает себе доступ, `awg syncconf` применяется сразу;
- админ-команды: `/status`, `/pending`, `/approve`, `/reject`, `/ban`, `/unban`,
  `/revoke <tg_id>`;
- аудит всех выдач и отзывов в таблицу `audit`;
- параметры обфускации (`Jc`, `Jmin`, `Jmax`, `S1`, `S2`, `H1…H4`, `I1…I5`, `J1…J3`,
  `Itime`) **копируются из вашего серверного конфига** в клиентский — иначе AmneziaWG-сервер
  молча дропает handshake. Это главная причина, по которой «конфиг из интернета» не работает;
- `BACKEND=mock` — полный прогон логики без единого изменения в системе (демо, разработка,
  CI).

Чего **не** умеет (см. Roadmap): ссылки `vpn://` (только `.conf`), IPv6-адреса клиентам,
учёт трафика по пользователям, биллинг.

## Требования

- сервер с AmneziaWG (`awg`, `awg-quick`, интерфейс вида `awg0`);
- Python 3.8+ (проверено на 3.9 и 3.14), единственная зависимость — `requests`;
- токен бота от [@BotFather](https://t.me/BotFather).

## Быстрый старт (без риска, mock-режим)

```bash
git clone <this repo> deeptraversal && cd deeptraversal
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
cp .env.example .env          # вписать BOT_TOKEN, ADMIN_IDS, ENDPOINT_HOST
sed -i 's/^BACKEND=.*/BACKEND=mock/' .env

.venv/bin/python -m awgbot --check        # проверить настройки
.venv/bin/python -m awgbot                # запустить бота
```

В mock-режиме бот ведёт себя как обычно, но вместо правки `/etc/amnezia/...` пишет список
peer'ов в `mock_awg_state.json`. Удобно посмотреть, как выглядит выданный конфиг:

```bash
.venv/bin/python -m awgbot --list
.venv/bin/python -m awgbot --export <your_tg_id>   # напечатать конфиг
```

## Быстрый старт, если Amnezia уже стоит

Одна команда на сервере (root нужен только для установки, спрашивает **только** токен бота и
ваш Telegram ID — остальное находит само):

```bash
sudo bash -c "$(curl -fsSL https://raw.githubusercontent.com/Wanshengg/deeptraversal/main/setup.sh)"
```

или из клонированного репозитория:

```bash
git clone https://github.com/Wanshengg/deeptraversal && cd deeptraversal
sudo ./setup.sh
```

Скрипт сам: находит контейнер `amnezia-awg`/`amnezia-awg2` или нативный `awg`, читает интерфейс,
порт, подсеть и параметры обфускации из конфига, определяет публичный IP, ставит зависимости и
venv в `/opt/deeptraversal`, генерирует `.env` (0600), регистрирует systemd-юнит, прогоняет
`--check` и запускает бота, печатает имя бота из логов. Повторный запуск безопасен — обновляет код и
сохраняет ваши токен/ID/адрес.

Полезные флаги: `--dry-run` (только показать, что найдено), `--no-start`, `--host`, `--port`,
`--dest`, `--service`, `--token`, `--admin` (для неинтерактивной установки).

## Установка на сервер (руками, если нужен контроль)

```bash
sudo ./deploy/install.sh                 # код в /opt/deeptraversal, venv, systemd-юнит
sudo nano /opt/deeptraversal/.env           # BOT_TOKEN, ADMIN_IDS, ENDPOINT_HOST, AWG_CONF
sudo /opt/deeptraversal/.venv/bin/python -m awgbot --env /opt/deeptraversal/.env --check
sudo systemctl enable --now awgbot
sudo journalctl -u awgbot -f
```

### Если Amnezia стоит в Docker

Очень частый случай (и то, что делает установщик AmneziaVPN на VPS): `awg` и конфиг живут
внутри контейнера `amnezia-awg`/`amnezia-awg2`, на хосте их нет вообще. Тогда:

```bash
AWG_DOCKER=amnezia-awg2                 # имя контейнера: docker ps
AWG_CONF=/opt/amnezia/awg/awg0.conf     # путь ВНУТРИ контейнера, не на хосте
```

Все операции (чтение/запись конфига, `awg syncconf`, `awg show`) идут через
`docker exec -i <контейнер>`; бот по-прежнему ставится на хост и требует только `docker`.
Проверить, что режим угадан правильно:

```bash
docker exec amnezia-awg2 awg show awg0 | head -5
python -m awgbot --check        # покажет тот же public key и те же obf-параметры
```

Ограничение: конфиг внутри контейнера не переживает `docker rm` контейнера (переустановка
Amnezia снесёт и пиров бота). У Amnezia это штатное поведение — держите `export/` и базу
в бэкапе.

Что нужно в `.env` (полный список — в `.env.example`):

| Переменная | Смысл |
|---|---|
| `BOT_TOKEN` | токен от BotFather |
| `TELEGRAM_API_BASE` | по умолчанию `https://api.telegram.org`; менять только для своего Bot API server |
| `ADMIN_IDS` | ваш Telegram ID (узнать: `/id` в боте), можно несколько через запятую |
| `AWG_CONF` | путь к серверному конфигу, обычно `/etc/amnezia/amneziawg/awg0.conf` |
| `AWG_CLIENTS_TABLE` | путь к `clientsTable` (список пользователей для приложения AmneziaVPN); пусто = рядом с конфигом, отсутствие файла не ошибка |
| `AWG_DOCKER` | имя контейнера, если Amnezia стоит в Docker (`amnezia-awg2`); тогда `AWG_CONF` — путь **внутри** контейнера, обычно `/opt/amnezia/awg/awg0.conf` |
| `AWG_IFACE` | имя интерфейса, `awg0` |
| `ENDPOINT_HOST` / `ENDPOINT_PORT` | публичный адрес и `ListenPort` сервера |
| `SUBNET` / `SERVER_IP` | подсеть клиентов и адрес сервера в ней |
| `SERVER_PUBLIC_KEY` | можно оставить пустым — выведется из `PrivateKey` конфига |
| `DNS`, `ALLOWED_IPS`, `PERSISTENT_KEEPALIVE`, `MTU` | попадут в клиентский конфиг как есть |
| `MAX_PEERS_PER_USER` | сколько ключей можно держать одному человеку (1 по умолчанию) |
| `REQUIRE_APPROVAL` | `true` — ключ только после нажатия админом; `false` — сразу |
| `SEND_VPN_LINK` / `SEND_CONF_FILE` | что приходит в чат: ссылка `vpn://` и/или файл `.conf` (по умолчанию оба) |
| `DB_PATH` | SQLite рядом с ботом, создаётся с правами 0600 |

## Как это работает внутри

1. **Peer'ы бота живут в отдельном блоке серверного конфига.** Бот не перегенерирует файл
   целиком и не трогает руками добавленных пиров — он заменяет только регион между
   `# >>> awgbot managed peers` и `# <<< awgbot managed peers`. Перед каждой записью
   делается `.awgbot.bak`.
2. **Применение — `awg syncconf`.** Из конфига вырезаются ключи, которые понимает только
   `awg-quick` (`Address`, `DNS`, `MTU`, `PostUp`, `PostDown`, `SaveConfig`, …), результат
   скармливается `awg syncconf awg0 <tmp>`; это идемпотентная сверка живого интерфейса с
   файлом. Живой интерфейс обновляется **до** записи файла: если `syncconf` упал, на диске
   остаётся конфиг, соответствующий реально работающему состоянию.
3. **Ключи** генерируются чистым Python (X25519 по RFC 7748) — без `wg genkey`, без
   криптобиблиотек, легко проверяемо: в тестах есть векторы RFC 7748 и сверка с
   `cryptography` на случайных входах.
4. **Состояние** — SQLite: `users`, `requests`, `peers`, `audit`. IP-пул вычисляется из
   `peers` (освободившиеся адреса переиспользуются), плюс адреса пиров, которые уже лежат в
   серверном конфиге до бота — они никогда не выдаются повторно.
5. **Docker**: если Amnezia стоит контейнером (`amnezia-awg2`), все операции идут через
   `docker exec -i`, а `AWG_CONF` — путь внутри контейнера. На хосте не нужны ни `awg`,
   ни сам конфиг.
6. **Экран «Пользователи» в приложении AmneziaVPN** читает не конфиг, а файл
   `clientsTable` рядом с ним (`/opt/amnezia/awg/clientsTable`): JSON-массив
   `{"clientId": <public key>, "userData": {"clientName", "allowedIps", "creationDate"}}`.
   Бот сам добавляет туда запись при выдаче и удаляет при отзыве, поэтому его ключи видны
   в приложении с человеческим именем устройства — а трафик и последний handshake
   приложение подтягивает из `awg show` при обновлении страницы. Есть разовая миграция для
   уже выданных ключей: `python -m awgbot --sync-clients-table`.

## Команды

Пользователь:

```
/start   — получить ключ
/key     — прислать конфиг ещё раз
/revoke  — отозвать ключ (доступ закрывается сразу)
/id      — свой Telegram ID
/help    — справка
```

Админ: `/status`, `/pending`, `/approve <id>`, `/reject <id>`, `/ban <tg_id>`,
`/unban <tg_id>`, `/revoke <tg_id>`, `/prune` плюс кнопки под уведомлением о заявке.

CLI: `--check`, `--list`, `--export <tg_id>` (добавьте `--link`, чтобы напечатать только
`vpn://`), `--revoke <tg_id>`, `--prune [--force]`, `--sync-clients-table`, `--mock`.

`--prune` нужен, когда ключи удаляли **мимо бота** (например, в самом приложении AmneziaVPN):
бот сверяет базу с живым интерфейсом (`awg show`), отзывает записи, которых там уже нет, и
убирает их из `clientsTable`. Ручные пиры вне базы не трогает, только сообщает их число.
Если интерфейс вернул 0 пиров, а в базе они есть, prune отказывается работать (похоже на
упавший `awg`) — тогда нужен явный `--force`. В Телеграме то же самое: `/prune`, `/prune force`.

## Импорт в приложение

Бот присылает **две вещи** — ссылку `vpn://` и файл `.conf`:

- ссылка: AmneziaVPN → «+» → «Импорт из буфера обмена» (тап по строке в Telegram копирует её
  целиком). Так же работает «Открыть в AmneziaVPN», если клиент подхватывает схему;
- файл: AmneziaVPN → «+» → «Импорт из файла» → выбрать `.conf`.

Конфиг содержит `[Interface]` с параметрами обфускации и `[Peer]` с endpoint'ом, так что
туннель поднимается сразу после импорта.

### Формат ссылки `vpn://`

Разобран по исходникам клиента (`importController.cpp`, `protocols_defs.h`,
`exportController.cpp`) и совпадает с тем, что генерирует сам AmneziaVPN:

```
vpn:// base64url( qCompress( JSON-конверт ) )        # без '=' на конце
qCompress(data) = 4 байта BE длины + zlib-поток       # Qt QByteArray::qCompress
```

Конверт и вложенный `last_config` (сериализуется **строкой**, то есть JSON в JSON):

```json
{"containers":[{"container":"amnezia-awg","awg":{
    "last_config":"{...}","isThirdPartyConfig":true,
    "port":"51820","transport_proto":"udp"}}],
 "defaultContainer":"amnezia-awg","hostName":"vpn.example.com",
 "description":"iphone","dns1":"1.1.1.1"}
```

В `last_config` лежит и сырой INI (`config`, для отображения), и структурные поля, которые
читает путь подключения: `client_priv_key`, `client_ip`, `server_pub_key`, `psk_key`,
`allowed_ips` (**массив**, не строка), `mtu`, `persistent_keep_alive`, `port` (число),
`isObfuscationEnabled` и все параметры обфускации под их INI-именами (`Jc`, `Jmin`, `Jmax`,
`S1`…`S4`, `H1`…`H4`, `I1`…`I5`) — дословно, включая диапазоны вида `2345678-2345799` и
CPS-теги в `I1`. Если отдать только `config`, приложение падает на подключении (error 1000):
именно поэтому «просто закинуть .conf в JSON» не работает.

Посмотреть ссылку без бота: `python -m awgbot --export <tg_id> --link`.

## Безопасность

Модель угроз: внешний атакующий. Границы, которые бот реально соблюдает:

- сетевых портов не слушает — только исходящий long-polling к `api.telegram.org`;
- управление — по `ADMIN_IDS`, всё остальное игнорируется (двойная проверка и на кнопках,
  и на командах);
- выдача только через одобрение админом (по умолчанию), кулдаун на повторные заявки;
- имена устройств санитизируются (никаких `../` в именах файлов);
- `awgbot.db` и экспортированные конфиги — `0600`.

Что нужно понимать:

- **в базе лежат приватные ключи клиентов** (иначе `/key` не смог бы переслать конфиг).
  Компрометация сервера = компрометация всех выданных ключей; это цена удобства. Кто хочет
  иначе — удаляйте ключ после выдачи и перевыпускайте по запросу;
- бот работает от root (нужен `CAP_NET_ADMIN` и запись в `/etc`), поэтому `.env` с токеном
  тоже должен быть `0600`;
- токен бота = полный контроль над ботом. Утёк — `/revoke` в BotFather и новый токен.

## Разработка

```bash
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/python -m pytest -q      # 81 тест: крипта, IPAM, конфиг, vpn://, backend, бот
```

Тесты не требуют ни AmneziaWG, ни Telegram: `AmneziaWG`-бэкенд проверяется через
подставной `runner`, бот — через фейковый Telegram-клиент.

Структура:

```
awgbot/
  curve25519.py   X25519 (RFC 7748), ~50 строк
  keys.py         генерация ключей и base64-формат WireGuard
  ipam.py         пул адресов
  clientconf.py   парсинг серверного конфига, рендер клиентского, strip для syncconf
  vpnlink.py      сборка/разбор ссылки vpn:// (qCompress + конверт Amnezia)
  awg.py          бэкенды: AmneziaWG (real) и Mock
  provision.py    выдача/отзыв ключей — вся бизнес-логика
  store.py        SQLite
  telegram.py     тонкий клиент Bot API
  bot.py          роутер апдейтов, тексты, кнопки
  __main__.py     CLI и long-polling
deploy/           install.sh + systemd unit
tests/            pytest
```

## Подводные камни

- **Не редактируйте интерфейс через приложение Amnezia после подключения бота.** GUI
  Amnezia пересобирает `awg0.conf` целиком и стирает managed-блок — пиры из базы останутся,
  но из конфига исчезнут. Восстановить: `systemctl restart awgbot` не поможет, нужно
  `python -m awgbot --revoke <id>` … либо просто выдать ключ заново (бот перезапишет блок при
  следующей выдаче/отзыве). Ручные правки самого файла вне блока безопасны.
- Интерфейс (`awg-quick@awg0` или контейнер) должен быть уже поднят: `syncconf` работает
  только с живым интерфейсом.
- `H1…H4` в новых версиях AmneziaWG могут быть диапазонами (`H2 = 2345678-2345799`) — бот
  копирует их как есть, не пытаясь разобрать.
- Клиентский `.conf` — это AmneziaWG, а не классический WireGuard: обычный WG-клиент его не
  поднимет (лишние ключи `Jc/H1/...` он просто проигнорирует, но сервер дропнет handshake).

## Roadmap

- [ ] QR-код в ответе бота (`qrencode`) — ссылку `vpn://` клиент умеет читать камерой;
- [ ] IPv6 для клиентов, несколько подсетей;
- [ ] статистика handshake'ов из `awg show` (`/who`), автоотзыв «мёртвых» ключей;
- [ ] лимит трафика через `iptables`/`tc` на peer;
- [ ] контейнер в Docker для серверов, где AmneziaWG уже в контейнере.

## Лицензия

MIT.
