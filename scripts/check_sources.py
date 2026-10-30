#!/usr/bin/env python3
"""check_sources.py — проверка источников расписания на живых данных.

Бесплатно: обращается только к открытому API ESPN
(site.api.espn.com), которое использует soccerdata. Никаких платных вызовов.

Зачем: список соревнований в config/competitions.json — это ГИПОТЕЗА о том,
какие ключи существуют. Ключ, которого нет в источнике, тихо вернёт пустой
список матчей, и бот будет молча ничего не отправлять. Поэтому каждый ключ
проверяется заранее, до деплоя.

Что делает: для каждого соревнования запрашивает scoreboard за окно дат и
печатает, сколько матчей вернулось.

Коды выхода: 0 — все ключи рабочие, 1 — есть нерабочие.
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "config" / "competitions.json"
ESPN_API = "http://site.api.espn.com/apis/site/v2/sports/soccer"
TIMEOUT = 20


def load_competitions(path: Path) -> list[dict]:
    data = json.loads(path.read_text(encoding="utf-8"))
    competitions = data.get("competitions")
    if not isinstance(competitions, list) or not competitions:
        raise SystemExit("ОШИБКА: в %s нет списка competitions" % path)
    return competitions


def fetch(url: str) -> dict:
    request = urllib.request.Request(url, headers={"User-Agent": "football-analyzer/0.1"})
    with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
        return json.loads(response.read().decode("utf-8"))


def calendar_dates(calendar: list, date_from: str, date_to: str) -> list[str]:
    """Даты матчей из календаря соревнования.

    ESPN отдаёт календарь в двух видах:
      * лига — список дат-строк: ["2026-08-21T07:00Z", ...];
      * кубок или сборные — один объект {label, startDate, endDate}, без
        перечня конкретных дат. В этом случае дат нет, и проверка перебирает
        дни окна по одному.
    """
    plain = [d for d in calendar if isinstance(d, str)]
    if not plain:
        return []
    dates = [d[:10].replace("-", "") for d in plain]
    in_window = [d for d in dates if date_from <= d <= date_to]
    return in_window or dates[-4:]


def iter_window(date_from: str, date_to: str) -> list[str]:
    """Все дни окна в формате YYYYMMDD."""
    start = datetime.strptime(date_from, "%Y%m%d").date()
    end = datetime.strptime(date_to, "%Y%m%d").date()
    days = []
    current = start
    while current <= end:
        days.append(current.strftime("%Y%m%d"))
        current += timedelta(days=1)
    return days


def check_slug(slug: str, date_from: str, date_to: str, max_dates: int = 5) -> tuple[str, int, str]:
    """Проверить соревнование так же, как это делает soccerdata.

    Сначала берём календарь соревнования, затем запрашиваем конкретные даты.
    Диапазон дат ESPN не принимает (HTTP 400) — только одиночные даты.
    Для кубков, где календаря нет, перебираем дни окна и выходим на первом
    найденном матче: цель проверки — «источник вообще отдаёт матчи».
    """
    calendar_url = "%s/%s/scoreboard?dates=%s0701" % (ESPN_API, slug, date_from[:4])
    try:
        calendar_data = fetch(calendar_url)
    except urllib.error.HTTPError as exc:
        return "HTTP %d" % exc.code, 0, calendar_url
    except Exception as exc:  # noqa: BLE001
        return "ошибка", 0, "%s (%s)" % (calendar_url, exc)

    leagues = calendar_data.get("leagues") or []
    if not leagues:
        return "нет соревнования", 0, calendar_url
    # Имя соревнования из ответа — доказательство, что слаг существует.
    league_name = leagues[0].get("name") or ""
    calendar = leagues[0].get("calendar") or []
    dates = calendar_dates(calendar, date_from, date_to)

    if dates:
        probes = dates[:max_dates]
        stop_on_first = False
    else:
        probes = iter_window(date_from, date_to)
        stop_on_first = True

    total = 0
    sample = ""
    for date in probes:
        url = "%s/%s/scoreboard?dates=%s" % (ESPN_API, slug, date)
        try:
            data = fetch(url)
        except Exception:  # noqa: BLE001
            continue
        events = data.get("events") or []
        total += len(events)
        if events and not sample:
            first = events[0]
            sample = "%s %s" % (str(first.get("date", ""))[:10], first.get("name", ""))
            if stop_on_first:
                break
    if total == 0:
        # Соревнование найдено (имя получено), но в окне матчей нет — это норма
        # для межсезонья и завершённых турниров. Отличаем от битого слага.
        return "пусто в окне", 0, league_name
    return "ok", total, sample


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Проверка ключей источников расписания")
    parser.add_argument("--days", type=int, default=14, help="окно проверки в днях")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    today = datetime.now(tz=timezone.utc).date()
    date_from = (today - timedelta(days=args.days)).strftime("%Y%m%d")
    date_to = (today + timedelta(days=args.days)).strftime("%Y%m%d")

    competitions = load_competitions(CONFIG)
    results = []
    for competition in competitions:
        slug = competition.get("espn")
        if not slug:
            results.append((competition, "нет ключа espn", 0, ""))
            continue
        status, count, detail = check_slug(slug, date_from, date_to)
        results.append((competition, status, count, detail))

    # Битый ключ — это ошибка HTTP или отсутствие соревнования в ответе.
    # «Пусто в окне» — не ошибка: соревнование найдено, но сейчас межсезонье.
    broken = [r for r in results if r[1] not in ("ok", "пусто в окне")]
    empty = [r for r in results if r[1] == "пусто в окне"]

    if args.json:
        print(
            json.dumps(
                [
                    {
                        "id": c.get("id"),
                        "espn": c.get("espn"),
                        "status": status,
                        "matches": count,
                        "sample": detail,
                    }
                    for c, status, count, detail in results
                ],
                ensure_ascii=False,
                indent=2,
            )
        )
        return 1 if broken else 0

    print("=" * 78)
    print("  ПРОВЕРКА ИСТОЧНИКОВ РАСПИСАНИЯ (ESPN, окно %s..%s)" % (date_from, date_to))
    print("=" * 78)
    for competition, status, count, detail in results:
        if status == "ok":
            mark = "  "
        elif status == "пусто в окне":
            mark = "~~"
        else:
            mark = "!!"
        print(
            "%s %-34s %-16s матчей: %-4s %s"
            % (
                mark,
                competition.get("name_ru", "?")[:34],
                competition.get("espn", "—"),
                count,
                detail[:34],
            )
        )
    print("=" * 78)
    if empty:
        print("  БЕЗ МАТЧЕЙ В ОКНЕ (%d) — ключ рабочий, просто сейчас нет игр:" % len(empty))
        for competition, _status, _count, detail in empty:
            print("    %-34s %s" % (competition.get("name_ru", "?")[:34], detail[:40]))
    if broken:
        print("  ПРОБЛЕМЫ (%d):" % len(broken))
        for competition, status, _count, detail in broken:
            print("    %-34s %s — %s" % (competition.get("name_ru", "?")[:34], status, detail[:40]))
        print("  Такие соревнования бот молча пропустит. Ключ надо исправить.")
        return 1
    with_matches = len(results) - len(empty)
    print(
        "  ИТОГ: ключей рабочих %d из %d (%d вернули матчи, %d пусты в окне)."
        % (len(results), len(results), with_matches, len(empty))
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
