"""Генератор постов-превью: разбор группы матчей на дату.

Пример:
  python scripts/make_previews.py --date 2026-11-12 --competition "сборных" --groups 4
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from datetime import datetime
from pathlib import Path

from football_analyzer.analysis.forecast import MSK, build_forecast
from football_analyzer.analysis.service import CUP_PREFIXES, fit_all_models
from football_analyzer.storage import db

ROOT = Path(__file__).resolve().parent.parent
BLOG_DIR = ROOT / "docs" / "blog"


def top_lines(forecast) -> list[str]:
    """Две самые вероятные строки прогноза для превью."""
    candidates: list[tuple[str, float]] = [
        ("П1", forecast.p_home),
        ("Ничья", forecast.p_draw),
        ("П2", forecast.p_away),
    ]
    for line, probability in forecast.total.items():
        candidates.append(("ТБ %s" % str(line).replace(".", ","), probability))
        candidates.append(("ТМ %s" % str(line).replace(".", ","), 1 - probability))
    for line, probability in forecast.corners.items():
        candidates.append(("углы ТБ %s" % str(line).replace(".", ","), probability))
    for line, probability in forecast.yellow.items():
        candidates.append(("ЖК ТБ %s" % str(line).replace(".", ","), probability))
    best = sorted(candidates, key=lambda item: -item[1])[:2]
    return ["%s — %.0f%%" % (name, probability * 100) for name, probability in best]


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", required=True, help="YYYY-MM-DD (МСК)")
    parser.add_argument("--competition", default="", help="подстрока названия соревнования")
    parser.add_argument("--groups", type=int, default=4, help="на сколько постов разбить")
    parser.add_argument("--per-group", type=int, default=4)
    parser.add_argument(
        "--slug-prefix", default="", help="часть слага, чтобы группы не перезаписывали друг друга"
    )
    args = parser.parse_args()

    target = datetime.strptime(args.date, "%Y-%m-%d").replace(tzinfo=MSK)
    pool = await db.connect(os.environ["DATABASE_URL"])
    rows = await db.upcoming_matches(pool, 14)
    model = await fit_all_models(pool)
    await pool.close()

    selected = [
        row
        for row in rows
        if row["kickoff_utc"].astimezone(MSK).date() == target.date()
        and args.competition.lower() in str(row["name_ru"]).lower()
    ]
    selected.sort(key=lambda row: row["kickoff_utc"])
    if not selected:
        print("нет матчей под условия", file=sys.stderr)
        return

    per_group = max(1, min(args.per_group, len(selected) // args.groups or 1))
    chunks = [selected[index : index + per_group] for index in range(0, len(selected), per_group)]
    chunks = chunks[: args.groups]

    BLOG_DIR.mkdir(parents=True, exist_ok=True)
    for number, chunk in enumerate(chunks, 1):
        lines: list[str] = []
        telegram: list[str] = []
        for row in chunk:
            competition = row["competition_id"]
            rates = (
                model
                if competition.startswith(CUP_PREFIXES)
                else model.league_rates.get(competition)
            )
            if rates is None:
                continue
            forecast = build_forecast(
                rates,
                competition_id=competition,
                home_team=row["home_team"],
                away_team=row["away_team"],
                kickoff_utc=row["kickoff_utc"],
            )
            kickoff = row["kickoff_utc"].astimezone(MSK).strftime("%d.%m %H:%M")
            marks = ", ".join(top_lines(forecast))
            lines.append("## %s — %s" % (row["home_team"], row["away_team"]))
            lines.append("%s МСК · %s" % (kickoff, row["name_ru"]))
            lines.append("")
            lines.append("Наиболее вероятное: **%s**." % marks)
            lines.append("")
            telegram.append(
                "▫️ %s. %s — %s: %s" % (kickoff, row["home_team"], row["away_team"], marks)
            )

        if not lines:
            continue
        slug = "%s-%s-group-%d" % (args.date, args.slug_prefix or "matches", number)
        title = "Матчи сборных %s: группа %d" % (
            target.strftime("%d.%m"),
            number,
        )
        body = (
            "Разбор %d матчей на %s по нашей модели. Ниже — только те события, "
            "которые модель считает наиболее вероятными; полная раскладка "
            "(углы, карточки, голы команд, счёт) — в боте.\n\n%s"
            % (len(chunk), target.strftime("%d.%m.%Y"), "\n".join(lines))
        )
        tg = (
            "⚽ %s\n\n%s\n\nПолная раскладка по каждому матчу — в боте: "
            "@footballizator_bot\n\n18+. Вероятности, не гарантии." % (title, "\n".join(telegram))
        )
        (BLOG_DIR / ("%s.md" % slug)).write_text(
            "# %s\nДата: %s\nSlug: %s\nКраткое: разбор %d матчей на %s\n\n---\n\n%s\n\n"
            "=== TELEGRAM ===\n\n%s\n"
            % (title, args.date, slug, len(chunk), target.strftime("%d.%m"), body, tg),
            encoding="utf-8",
        )
        print("создан пост:", slug)


if __name__ == "__main__":
    asyncio.run(main())
