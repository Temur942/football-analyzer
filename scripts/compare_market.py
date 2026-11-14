"""Сравнение модели с рынком: у кого вероятность точнее.

Берём матчи, где есть закрывающие коэффициенты, обучаем модель на прошлом и
считаем Brier для модели и для рынка (после снятия маржи). Это ответ на вопрос
«есть ли у модели преимущество», а не рекламная цифра.
"""

from __future__ import annotations

import asyncio
import os
from datetime import UTC, datetime

from football_analyzer.analysis.forecast import build_forecast
from football_analyzer.analysis.service import CUP_PREFIXES, fit_all_models
from football_analyzer.storage import db
from football_analyzer.teams_norm import normalize

CUTOFF = datetime(2026, 6, 1, tzinfo=UTC)


async def main() -> None:
    pool = await db.connect(os.environ["DATABASE_URL"])
    competitions = await db.competition_ids(pool)
    league_ids = [c for c in competitions if not c.startswith(CUP_PREFIXES)]

    our_matches: list[dict] = []
    for competition_id in league_ids:
        for row in await db.finished_matches(pool, competition_id):
            our_matches.append(
                {
                    "competition": competition_id,
                    "date": row["kickoff_utc"].date(),
                    "key_home": normalize(row["home_team"]),
                    "key_away": normalize(row["away_team"]),
                    "home": row["home_team"],
                    "away": row["away_team"],
                    "kickoff": row["kickoff_utc"],
                    "goals": (
                        (int(row["home_goals"]), int(row["away_goals"]))
                        if row["home_goals"] is not None and row["away_goals"] is not None
                        else None
                    ),
                }
            )

    async with pool.acquire() as conn:
        odds = await conn.fetch(
            "SELECT competition, match_date, home_team, away_team,"
            " avg_home, avg_draw, avg_away"
            " FROM market_odds WHERE avg_home IS NOT NULL"
        )

    index: dict[tuple, dict] = {}
    for match in our_matches:
        index[(match["competition"], match["date"], match["key_home"], match["key_away"])] = match

    matched: list[tuple[dict, object]] = []
    for row in odds:
        key = (
            row["competition"],
            row["match_date"],
            normalize(row["home_team"]),
            normalize(row["away_team"]),
        )
        match = index.get(key)
        if match is not None:
            matched.append((match, row))

    print("матчей с коэффициентами:", len(odds))
    print("сопоставлено с нашей базой:", len(matched))
    if matched:
        print("доля сопоставления: %.0f%%" % (100 * len(matched) / max(1, len(odds))))

    model = await fit_all_models(pool)
    await pool.close()

    model_brier = market_brier = 0.0
    count = 0
    for match, row in matched:
        if match["kickoff"] < CUTOFF:
            continue
        rates = model.league_rates.get(match["competition"])
        if rates is None:
            continue
        try:
            forecast = build_forecast(
                rates,
                competition_id=match["competition"],
                home_team=match["home"],
                away_team=match["away"],
                kickoff_utc=match["kickoff"],
            )
        except Exception:  # noqa: BLE001
            continue
        goals = match["goals"]
        if goals is None:
            continue
        home_goals, away_goals = goals
        outcome = 1.0 if home_goals > away_goals else 0.0
        implied_home = 1 / float(row["avg_home"])
        implied_not_home = 1 / float(row["avg_draw"]) + 1 / float(row["avg_away"])
        market_home = implied_home / (implied_home + implied_not_home)
        model_brier += (forecast.p_home - outcome) ** 2
        market_brier += (market_home - outcome) ** 2
        count += 1

    if count:
        print("матчей в проверке:", count)
        print(
            "Brier модель: %.4f | Brier рынок: %.4f (меньше — лучше)"
            % (model_brier / count, market_brier / count)
        )
    else:
        print("нет матчей после отсечки — проверка не получилась")


if __name__ == "__main__":
    asyncio.run(main())
