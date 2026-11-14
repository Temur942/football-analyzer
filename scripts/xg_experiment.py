"""Даёт ли xG выигрыш: сравнение на одном и том же периоде.

Берём матчи, где есть xG, делим сезон на «обучение» и «проверку», считаем
оценки силы дважды — по xG и по фактическим голам — и сравниваем Brier.
Одинаковые данные, разница только в источнике голов.
"""

from __future__ import annotations

import asyncio
import os

from football_analyzer.analysis.forecast import build_forecast
from football_analyzer.analysis.ratings import MatchRow, fit_rates
from football_analyzer.cli import to_match_row
from football_analyzer.storage import db


def brier(entries: list[tuple[float, float, float, tuple[float, float, float]]]) -> float:
    total = 0.0
    for home, draw, away, actual in entries:
        total += (home - actual[0]) ** 2 + (draw - actual[1]) ** 2 + (away - actual[2]) ** 2
    return total / len(entries) if entries else 0.0


async def main() -> None:
    pool = await db.connect(os.environ["DATABASE_URL"])
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT kickoff_utc, home_team, away_team, home_goals, away_goals,
                   home_goals_ht, away_goals_ht, home_corners, away_corners,
                   home_yellow, away_yellow, competition_id, home_xg, away_xg
              FROM matches
             WHERE home_xg IS NOT NULL AND home_goals IS NOT NULL
             ORDER BY kickoff_utc
            """
        )
    await pool.close()

    matches = [(row["competition_id"], to_match_row(row)) for row in rows]
    if len(matches) < 200:
        print("мало матчей с xG:", len(matches))
        return

    by_league: dict[str, list] = {}
    for competition_id, match in matches:
        by_league.setdefault(competition_id, []).append(match)

    rates_xg: dict[str, object] = {}
    rates_goals: dict[str, object] = {}
    test_matches: list[tuple[str, MatchRow]] = []
    for competition_id, league_matches in by_league.items():
        if len(league_matches) < 60:
            continue
        split = int(len(league_matches) * 0.6)
        train, test = league_matches[:split], league_matches[split:]
        rates_xg[competition_id] = fit_rates(train)
        rates_goals[competition_id] = fit_rates(
            [MatchRow(**{**match.__dict__, "home_xg": None, "away_xg": None}) for match in train]
        )
        test_matches.extend((competition_id, match) for match in test)

    entries_xg: list[tuple[float, float, float, tuple[float, float, float]]] = []
    entries_goals: list[tuple[float, float, float, tuple[float, float, float]]] = []
    for competition_id, match in test_matches:
        if match.home_goals is None or match.away_goals is None:
            continue
        actual = (
            (1.0, 0.0, 0.0)
            if match.home_goals > match.away_goals
            else (0.0, 1.0, 0.0)
            if match.home_goals == match.away_goals
            else (0.0, 0.0, 1.0)
        )
        for table, bucket in ((rates_xg, entries_xg), (rates_goals, entries_goals)):
            rates = table.get(competition_id)
            if rates is None:
                continue
            forecast = build_forecast(
                rates,
                home_team=match.home_team,
                away_team=match.away_team,
                kickoff_utc=match.kickoff_utc,
            )
            bucket.append((forecast.p_home, forecast.p_draw, forecast.p_away, actual))

    print("матчей с xG:", len(matches), "| в проверке:", len(test_matches))
    print("Brier 1X2 с xG:      %.4f" % brier(entries_xg))
    print("Brier 1X2 без xG:    %.4f" % brier(entries_goals))
    print("меньше — лучше")


if __name__ == "__main__":
    asyncio.run(main())
