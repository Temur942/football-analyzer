"""Загрузка исторических коэффициентов с football-data.co.uk.

Складываем только то, что нужно для сравнения модели с рынком: исход и тотал.
Имена команд остаются как в источнике — сопоставление с нашими делается
отдельным проходом, чтобы не терять данные из-за разного написания.
"""

from __future__ import annotations

import asyncio
import csv
import datetime
import io
import os

import aiohttp

from football_analyzer.storage import db

BASE = "https://football-data.co.uk/mmz4281"
# Код лиги в источнике → наш id соревнования
LEAGUES = {
    "E0": "eng-premier-league",
    "E1": "eng-championship",
    "SP1": "esp-la-liga",
    "SP2": "esp-segunda",
    "I1": "ita-serie-a",
    "I2": "ita-serie-b",
    "D1": "ger-bundesliga",
    "D2": "ger-2-bundesliga",
    "F1": "fra-ligue-1",
    "F2": "fra-ligue-2",
    "P1": "por-primeira-liga",
    "N1": "ned-eredivisie",
    "T1": "tur-super-lig",
}
SEASONS = ("2425", "2526", "2627")


def number(value: str) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def parse_row(row: dict[str, str], competition: str, season: str) -> tuple | None:
    date = row.get("Date") or ""
    if not date:
        return None
    day, month, year = date.split("/")
    iso_date = datetime.date(int(year), int(month), int(day))
    return (
        competition,
        season,
        iso_date,
        row.get("HomeTeam"),
        row.get("AwayTeam"),
        int(row["FTHG"]) if row.get("FTHG") else None,
        int(row["FTAG"]) if row.get("FTAG") else None,
        number(row.get("B365H")),
        number(row.get("B365D")),
        number(row.get("B365A")),
        number(row.get("AvgH")),
        number(row.get("AvgD")),
        number(row.get("AvgA")),
        number(row.get("MaxH")),
        number(row.get("MaxD")),
        number(row.get("MaxA")),
        number(row.get("B365>2.5")) or number(row.get("Avg>2.5")),
        number(row.get("B365<2.5")) or number(row.get("Avg<2.5")),
        number(row.get("HxG")),
        number(row.get("AxG")),
    )


async def main() -> None:
    pool = await db.connect(os.environ["DATABASE_URL"])
    await db.apply_schema(pool)
    total = 0
    async with aiohttp.ClientSession() as session:
        for season in SEASONS:
            for code, competition in LEAGUES.items():
                url = "%s/%s/%s.csv" % (BASE, season, code)
                try:
                    async with session.get(
                        url, timeout=aiohttp.ClientTimeout(total=40)
                    ) as response:
                        if response.status != 200:
                            continue
                        raw = await response.text(encoding="utf-8-sig", errors="replace")
                except Exception as exc:  # noqa: BLE001
                    print("ошибка загрузки", url, exc)
                    continue
                rows = []
                for row in csv.DictReader(io.StringIO(raw)):
                    parsed = parse_row(row, competition, season)
                    if parsed is not None:
                        rows.append(parsed)
                if not rows:
                    continue
                async with pool.acquire() as conn:
                    await conn.executemany(
                        """
                        INSERT INTO market_odds (
                            competition, season, match_date, home_team, away_team,
                            home_goals, away_goals, odds_home, odds_draw, odds_away,
                            avg_home, avg_draw, avg_away, max_home, max_draw, max_away,
                            over25, under25, home_xg, away_xg
                        ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,
                                  $11,$12,$13,$14,$15,$16,$17,$18,$19,$20)
                        ON CONFLICT (competition, match_date, home_team, away_team)
                        DO UPDATE SET home_xg = EXCLUDED.home_xg, away_xg = EXCLUDED.away_xg
                        """,
                        rows,
                    )
                total += len(rows)
                print("%s %s: %d" % (season, code, len(rows)))
    await pool.close()
    print("итого строк:", total)


if __name__ == "__main__":
    asyncio.run(main())
