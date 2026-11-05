# football-analyzer

Telegram-бот: круглосуточно следит за расписанием футбольных матчей и присылает
математические прогнозы за 5 часов до начала игры.

Прогноз считается **локально** (scipy, распределение Пуассона). Платных LLM API
в проекте нет.

## Документы

- **[AGENTS.md](AGENTS.md)** — главный документ: стек, окружения, деплой,
  правила работы с деньгами, известные ловушки, открытые вопросы. Читать первым.

## Требования

- Python **3.11+**
- Токен Telegram-бота (@BotFather)

## Установка (когда будет Python 3.11+)

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env.local     # и заполнить BOT_TOKEN
```

## Проверка окружения (бесплатно, без сети)

```bash
python3 scripts/check_env.py
```

## Учёт платных операций

```bash
python3 scripts/cost_guard.py prices                       # таблица цен
python3 scripts/cost_guard.py plan <operation> <count>     # сколько будет стоить
python3 scripts/cost_guard.py record <operation> <count> --key <id>
python3 scripts/cost_guard.py report --since 2026-10-01
```

Правило: платное запускается только после явного «да» заказчика, и сначала
называются количество запросов и сумма в рублях. Если цена операции не описана
в `config/prices.json`, скрипт отказывается считать — это защита, а не баг.

## Расписание матчей (работает)

```bash
python -m football_analyzer.cli competitions                  # 20 соревнований
python -m football_analyzer.cli fetch-schedule --days 3       # окно вперёд
python -m football_analyzer.cli fetch-schedule --back 4 --days 5   # с серединой недели
python -m football_analyzer.cli fetch-schedule --days 3 --json
python scripts/check_sources.py                               # проверка ключей источников
```

Источник — открытый API ESPN (бесплатно, без ключа, без браузера). Запросов
за проход немного: скрипт сначала читает календарь соревнования и ходит только
по игровым дням. Проверено 30.10.2026: 132 матча за 3 дня (83 запроса) и
193 матча за окно с серединой недели (127 запросов), 0 ошибок.

## Стейдж на сервере

VDS FirstVDS (`168.113.176.166`, Ubuntu 24.04), каталог `/var/www/football-analyzer-stage`.

```bash
# код и зависимости (идемпотентно, можно запускать сколько угодно раз)
ssh -i ~/.ssh/football_analyzer_deploy root@168.113.176.166 'bash /root/provision.sh'

# что происходит
ssh ... 'systemctl list-timers football-analyzer-sync.timer --no-pager'
ssh ... 'journalctl -u football-analyzer-sync -n 30 --no-pager'

# данные в базе (доказательство, а не лог)
ssh ... 'sudo -u postgres psql -d football -c "select count(*) from matches;"'
ssh ... 'sudo -u postgres psql -d football -c "select id, status, rows_seen, rows_new, rows_updated from ingest_runs order by id desc limit 5;"'

# прогнать сбор руками
ssh ... 'systemctl start football-analyzer-sync.service'
```

Сбор расписания идёт **сам раз в час** (systemd-таймер), бот пока не
включён — нужен `BOT_TOKEN` от @BotFather в `/etc/football-analyzer/stage.env`.

## Статус

Готово и проверено: получение расписания, запись в PostgreSQL, автосбор по
таймеру на сервере. Не начато: математика прогноза (`analysis/`), отправка
за 5 часов (`scheduler/`), бот (`main.py` — ждёт токен), подписки.

Что дальше и какие вопросы открыты — в [AGENTS.md](AGENTS.md).
