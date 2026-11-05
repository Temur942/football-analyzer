#!/usr/bin/env python3
"""check_match_stats.py — есть ли в источнике углы и ЖК по завершённым матчам.

Бесплатно: открытый API ESPN, без ключа и без браузера.

Зачем: прогноз требует угловых и жёлтых карточек по каждой команде. FBref их
отдаёт только через Chrome/Selenium — на сервере с 1.8 ГБ RAM это не вариант.
Проверяем, что ESPN summary отдаёт `wonCorners` и `yellowCards` по командам.

Что делает: по каждому соревнованию находит последний завершённый матч и
смотрит, есть ли в boxscore нужные показатели.

Коды выхода: 0 — данные есть везде, где были матчи; 1 — где-то нет.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "config" / "competitions.json"
ESPN_API = "http://site.api.espn.com/apis/site/v2/sports/soccer"
TIMEOUT = 25
REQUIRED_STATS = ("wonCorners", "yellowCards")


def get(url: str) -> dict:
    request = urllib.request.Request(url, headers={"User-Agent": "football-analyzer/0.1"})
    with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
        return json.loads(response.read().decode("utf-8"))


def load_competitions() -> list[dict]:
    data = json.loads(CONFIG.read_text(encoding="utf-8"))
    return data.get("competitions") or []


def last_finished(slug: str, days: int) -> dict | None:
    """Последний завершённый матч за окно дней."""
    today = datetime.now(tz=timezone.utc).date()
    for back in range(days):
        day = (today - timedelta(days=back)).strftime("%Y%m%d")
        try:
            data = get("%s/%s/scoreboard?dates=%s" % (ESPN_API, slug, day))
        except Exception:  # noqa: BLE001
            continue
        for event in data.get("events") or []:
            state = ((event.get("status") or {}).get("type") or {}).get("state")
            if state == "post":
                return event
        time.sleep(0.2)
    return None


def check_event(slug: str, event: dict) -> tuple[bool, dict[str, int]]:
    """Есть ли нужные показатели у обеих команд."""
    summary = get("%s/%s/summary?event=%s" % (ESPN_API, slug, event["id"]))
    teams = (summary.get("boxscore") or {}).get("teams") or []
    values: dict[str, int] = {}
    complete = len(teams) == 2
    for team in teams:
        stats = {s.get("name"): s.get("displayValue") for s in (team.get("statistics") or [])}
        for name in REQUIRED_STATS:
            if name not in stats:
                complete = False
        side = (team.get("homeAway") or "?").lower()
        for name in REQUIRED_STATS:
            if name in stats:
                values["%s.%s" % (side, name)] = stats[name]
    return complete, values


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Проверка углов и ЖК в ESPN")
    parser.add_argument("--days", type=int, default=10, help="окно поиска завершённых матчей")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    competitions = load_competitions()
    results = []
    missing = 0
    for competition in competitions:
        slug = competition.get("espn")
        if not slug:
            continue
        event = last_finished(slug, args.days)
        if event is None:
            results.append((competition["name_ru"], "нет завершённых матчей в окне", {}))
            continue
        try:
            complete, values = check_event(slug, event)
        except Exception as exc:  # noqa: BLE001
            results.append((competition["name_ru"], "ошибка: %s" % exc, {}))
            missing += 1
            continue
        if not complete:
            missing += 1
        results.append(
            (competition["name_ru"], "углы + ЖК есть" if complete else "НЕТ части данных", values)
        )
        time.sleep(0.2)

    if args.json:
        print(
            json.dumps(
                [{"competition": n, "status": s, "values": v} for n, s, v in results],
                ensure_ascii=False,
                indent=2,
            )
        )
        return 1 if missing else 0

    print("=" * 96)
    print("  УГЛЫ И ЖК ПО ЗАВЕРШЁННЫМ МАТЧАМ (ESPN summary, бесплатно)")
    print("=" * 96)
    for name, status, values in results:
        mark = "  " if status == "углы + ЖК есть" else "!!"
        sample = " ".join("%s=%s" % (k, v) for k, v in list(values.items())[:4])
        print("%s %-34s %-24s %s" % (mark, name[:34], status[:24], sample[:34]))
    print("=" * 96)
    if missing:
        print("  ПРОБЛЕМЫ: %d соревнований без полных данных" % missing)
        return 1
    print("  ИТОГ: везде, где были завершённые матчи, углы и ЖК присутствуют.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
