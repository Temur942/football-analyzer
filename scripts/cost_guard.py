#!/usr/bin/env python3
"""cost_guard.py — учёт платных операций: план ДО запуска, факт ПОСЛЕ.

Зачем этот файл. На соседнем проекте заказчика на одну единицу продукта ушло
30+ оплаченных генераций при 4 сохранённых результатах: сначала запускали,
потом смотрели счёт. Здесь порядок обратный — сначала цифры и явное «да»,
потом запуск.

Важно: скрипт НИЧЕГО не запускает и не тратит денег. Он только считает и
пишет факты в реестр. Поэтому его можно запускать свободно.

Команды
-------
  plan   <operation> <count> [--note TEXT]
        Сколько будет стоить: цена × количество, проверка лимитов бюджета.

  record <operation> <count> --key KEY [--status ok|failed|timeout] [--note TEXT]
        Записать факт в реестр. --key обязателен: это защита от повторной
        покупки того же самого (идемпотентность). Повторный ключ = отказ.

  report [--since YYYY-MM-DD] [--until YYYY-MM-DD]
        Сверка план/факт по операциям и статусам.

  prices [<operation>]
        Показать таблицу цен или одну операцию.

  keys   [--operation OP]
        Что уже оплачено — проверяется ПЕРЕД повторным запуском.

Коды выхода
-----------
  0 — всё в порядке
  1 — ошибка вызова (аргументы, нет файла, битый JSON)
  2 — отказ: цены нет, превышен лимит или ключ уже использован

Только стандартная библиотека: скрипт обязан работать там, где нет venv.
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PRICES = ROOT / "config" / "prices.json"
DEFAULT_LEDGER = ROOT / "var" / "cost-ledger.sqlite3"

EXIT_OK = 0
EXIT_USAGE = 1
EXIT_REFUSE = 2

STATUSES = ("ok", "failed", "timeout", "pending")

SCHEMA = """
CREATE TABLE IF NOT EXISTS api_calls (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    ts         TEXT    NOT NULL,
    operation  TEXT    NOT NULL,
    units      INTEGER NOT NULL,
    unit_price REAL,
    amount     REAL    NOT NULL,
    status     TEXT    NOT NULL DEFAULT 'ok',
    idem_key   TEXT    UNIQUE,
    note       TEXT
);
CREATE INDEX IF NOT EXISTS idx_api_calls_operation ON api_calls(operation, ts);
"""


class CostError(Exception):
    """Ошибка, из-за которой считать или писать нельзя."""


# --------------------------------------------------------------------------- #
# Пути и файлы
# --------------------------------------------------------------------------- #


def prices_path() -> Path:
    return Path(os.environ.get("COST_PRICES") or DEFAULT_PRICES)


def ledger_path() -> Path:
    return Path(os.environ.get("COST_LEDGER") or DEFAULT_LEDGER)


def load_prices(path: Path) -> Dict[str, Any]:
    if not path.is_file():
        raise CostError("не найден файл цен: %s" % path)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise CostError("битый JSON в %s: %s" % (path, exc)) from exc
    if not isinstance(data, dict):
        raise CostError("в %s ожидался объект JSON" % path)
    return data


def get_operation(prices: Dict[str, Any], name: str) -> Tuple[Dict[str, Any], float]:
    """Вернуть (описание операции, цена за единицу). Нет цены — отказ."""
    operations = prices.get("operations") or {}
    operation = operations.get(name)
    if operation is None:
        raise CostError(
            "операция '%s' не описана в %s — цену знать неоткуда.\n"
            "Платное запускать НЕЛЬЗЯ. Добавьте операцию с реальной ценой "
            "и подтверждением заказчика." % (name, prices_path())
        )
    if not isinstance(operation, dict):
        raise CostError("описание операции '%s' должно быть объектом" % name)
    price = operation.get("price")
    if price is None:
        raise CostError(
            "у операции '%s' цена не задана (price: null).\n"
            "Считать нечего — сначала узнайте цену у заказчика." % name
        )
    if isinstance(price, bool) or not isinstance(price, (int, float)) or price < 0:
        raise CostError("у операции '%s' некорректная цена: %r" % (name, price))
    return operation, float(price)


# --------------------------------------------------------------------------- #
# Реестр
# --------------------------------------------------------------------------- #


def connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.executescript(SCHEMA)
    return conn


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def spent_since(conn: sqlite3.Connection, since_iso: str) -> float:
    row = conn.execute(
        "SELECT COALESCE(SUM(amount), 0) FROM api_calls WHERE ts >= ? AND status != 'failed'",
        (since_iso,),
    ).fetchone()
    return float(row[0] or 0.0)


def day_start_iso() -> str:
    now = datetime.now(timezone.utc)
    return now.replace(hour=0, minute=0, second=0, microsecond=0).isoformat(timespec="seconds")


def money(value: float, currency: str = "RUB") -> str:
    sign = "₽" if currency == "RUB" else currency
    return "%.2f %s" % (value, sign)


# --------------------------------------------------------------------------- #
# Команды
# --------------------------------------------------------------------------- #


def cmd_plan(args: argparse.Namespace) -> int:
    prices = load_prices(prices_path())
    currency = prices.get("currency", "RUB")
    operation, price = get_operation(prices, args.operation)
    count = args.count
    total = price * count

    conn = connect(ledger_path())
    today = spent_since(conn, day_start_iso())
    budget = prices.get("budget") or {}
    per_run = budget.get("max_per_run_rub")
    per_day = budget.get("max_per_day_rub")

    print("=" * 66)
    print("  ПЛАН ПЛАТНОЙ ОПЕРАЦИИ (ничего не запущено)")
    print("=" * 66)
    print("  Операция:        %s" % args.operation)
    print("  Что это:         %s" % (operation.get("note") or "—"))
    print("  Единица:         %s" % operation.get("unit", "1 единица"))
    print("  Количество:      %d" % count)
    print("  Цена за единицу: %s" % money(price, currency))
    print("  " + "-" * 62)
    print("  ИТОГО К ОПЛАТЕ:  %s" % money(total, currency))
    print("  Уже потрачено сегодня: %s" % money(today, currency))
    print(
        "  Лимит на запуск: %s" % (money(per_run, currency) if per_run is not None else "не задан")
    )
    print(
        "  Лимит на сутки:  %s" % (money(per_day, currency) if per_day is not None else "не задан")
    )

    problems: List[str] = []
    if per_run is not None and total > float(per_run):
        problems.append(
            "сумма запуска %s больше лимита на запуск %s"
            % (money(total, currency), money(float(per_run), currency))
        )
    if per_day is not None and today + total > float(per_day):
        problems.append(
            "с учётом сегодняшних трат выйдет %s — больше суточного лимита %s"
            % (money(today + total, currency), money(float(per_day), currency))
        )

    print("  " + "-" * 62)
    if problems:
        for problem in problems:
            print("  ОТКАЗ: %s" % problem)
        print("=" * 66)
        return EXIT_REFUSE
    if total == 0:
        print("  Операция бесплатна — оплата не требуется.")
    print("  Дальше: спросить у заказчика явное «да» и только потом запускать.")
    print("=" * 66)
    return EXIT_OK


def cmd_record(args: argparse.Namespace) -> int:
    prices = load_prices(prices_path())
    currency = prices.get("currency", "RUB")
    operation, price = get_operation(prices, args.operation)
    count = args.count
    amount = price * count
    if args.status == "failed":
        amount = 0.0

    conn = connect(ledger_path())
    try:
        conn.execute(
            "INSERT INTO api_calls"
            " (ts, operation, units, unit_price, amount, status, idem_key, note)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (now_iso(), args.operation, count, price, amount, args.status, args.key, args.note),
        )
        conn.commit()
    except sqlite3.IntegrityError:
        print("ОТКАЗ: ключ '%s' уже есть в реестре." % args.key)
        print("Это значит, что операция уже была оплачена. Повторная покупка того же")
        print("самого запрещена — сначала разберитесь, почему результата нет.")
        return EXIT_REFUSE

    row = conn.execute(
        "SELECT id, ts, operation, units, amount, status FROM api_calls WHERE idem_key = ?",
        (args.key,),
    ).fetchone()
    print("=" * 66)
    print("  ЗАПИСЬ В РЕЕСТР (это и есть доказательство, а не лог)")
    print("=" * 66)
    print("  id:        %s" % row[0])
    print("  Время:     %s" % row[1])
    print("  Операция:  %s" % row[2])
    print("  Единиц:    %s" % row[3])
    print("  Сумма:     %s (%s)" % (money(float(row[4]), currency), args.status))
    print("  Описание:  %s" % (operation.get("note") or "—"))
    print("  Реестр:    %s" % ledger_path())
    print("=" * 66)
    return EXIT_OK


def cmd_report(args: argparse.Namespace) -> int:
    prices = load_prices(prices_path())
    currency = prices.get("currency", "RUB")
    conn = connect(ledger_path())

    where: List[str] = []
    params: List[Any] = []
    if args.since:
        where.append("ts >= ?")
        params.append(args.since + "T00:00:00+00:00")
    if args.until:
        where.append("ts <= ?")
        params.append(args.until + "T23:59:59+00:00")
    clause = (" WHERE " + " AND ".join(where)) if where else ""

    rows = conn.execute(
        "SELECT operation, status, COUNT(*), SUM(units), SUM(amount)"
        " FROM api_calls" + clause + " GROUP BY operation, status ORDER BY operation, status",
        params,
    ).fetchall()

    print("=" * 66)
    print("  РЕЕСТР ПЛАТНЫХ ОПЕРАЦИЙ")
    print("=" * 66)
    if not rows:
        print("  Записей нет. Если платное уже запускали — это аномалия.")
    total = 0.0
    for operation, status, calls, units, amount in rows:
        amount = float(amount or 0.0)
        if status != "failed":
            total += amount
        print(
            "  %-28s %-8s вызовов: %-4s единиц: %-5s сумма: %s"
            % (operation, status, calls, units, money(amount, currency))
        )
    print("  " + "-" * 62)
    print("  К ОПЛАТЕ ВСЕГО: %s" % money(total, currency))
    print("  Реестр: %s" % ledger_path())
    print("=" * 66)
    return EXIT_OK


def cmd_prices(args: argparse.Namespace) -> int:
    prices = load_prices(prices_path())
    currency = prices.get("currency", "RUB")
    operations = prices.get("operations") or {}
    print("=" * 66)
    print("  ТАБЛИЦА ЦЕН  (%s)" % prices_path())
    print("=" * 66)
    if args.operation:
        operation, price = get_operation(prices, args.operation)
        print(
            "  %s → %s за %s"
            % (args.operation, money(price, currency), operation.get("unit", "единицу"))
        )
        print("  %s" % (operation.get("note") or "—"))
    elif not operations:
        print("  Платных операций нет. Это соответствует замыслу проекта:")
        print("  прогноз считается локально, источники данных бесплатные.")
    else:
        for name, operation in sorted(operations.items()):
            price = operation.get("price")
            shown = (
                money(float(price), currency)
                if isinstance(price, (int, float))
                else "ЦЕНА НЕ ЗАДАНА"
            )
            print("  %-28s %-16s %s" % (name, shown, operation.get("unit", "")))
    print("=" * 66)
    return EXIT_OK


def cmd_keys(args: argparse.Namespace) -> int:
    conn = connect(ledger_path())
    sql = "SELECT idem_key, ts, operation, status FROM api_calls WHERE idem_key IS NOT NULL"
    params: List[Any] = []
    if args.operation:
        sql += " AND operation = ?"
        params.append(args.operation)
    sql += " ORDER BY ts DESC LIMIT 200"
    rows = conn.execute(sql, params).fetchall()
    print("=" * 66)
    print("  ЧТО УЖЕ ОПЛАЧЕНО (проверять ПЕРЕД повторным запуском)")
    print("=" * 66)
    if not rows:
        print("  Пусто.")
    for key, ts, operation, status in rows:
        print("  %s  %-24s %-8s %s" % (ts, operation, status, key))
    print("=" * 66)
    return EXIT_OK


# --------------------------------------------------------------------------- #
# Точка входа
# --------------------------------------------------------------------------- #


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="cost_guard.py",
        description="Учёт платных операций: сначала план, потом «да», потом факт.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_plan = sub.add_parser("plan", help="сколько будет стоить (ничего не тратит)")
    p_plan.add_argument("operation")
    p_plan.add_argument("count", type=int)
    p_plan.add_argument("--note", default=None)
    p_plan.set_defaults(func=cmd_plan)

    p_record = sub.add_parser("record", help="записать факт в реестр")
    p_record.add_argument("operation")
    p_record.add_argument("count", type=int)
    p_record.add_argument("--key", required=True, help="ключ идемпотентности, уникальный")
    p_record.add_argument("--status", choices=STATUSES, default="ok")
    p_record.add_argument("--note", default=None)
    p_record.set_defaults(func=cmd_record)

    p_report = sub.add_parser("report", help="сверка план/факт")
    p_report.add_argument("--since", default=None, help="YYYY-MM-DD")
    p_report.add_argument("--until", default=None, help="YYYY-MM-DD")
    p_report.set_defaults(func=cmd_report)

    p_prices = sub.add_parser("prices", help="таблица цен")
    p_prices.add_argument("operation", nargs="?", default=None)
    p_prices.set_defaults(func=cmd_prices)

    p_keys = sub.add_parser("keys", help="что уже оплачено")
    p_keys.add_argument("--operation", default=None)
    p_keys.set_defaults(func=cmd_keys)

    return parser


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if getattr(args, "count", 0) is not None and getattr(args, "count", 1) < 0:
        print("ОШИБКА: количество не может быть отрицательным", file=sys.stderr)
        return EXIT_USAGE
    try:
        return args.func(args)
    except CostError as exc:
        print("ОТКАЗ: %s" % exc, file=sys.stderr)
        return EXIT_REFUSE
    except KeyboardInterrupt:
        return EXIT_USAGE


if __name__ == "__main__":
    sys.exit(main())
