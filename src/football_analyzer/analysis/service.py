"""Общая сборка моделей: используется и командой predict, и рассылкой.

Держим в одном месте, чтобы прогноз в консоли и прогноз, который уходит
подписчику, считались одинаково. Разные цифры в двух местах — худший вид бага.
"""

from __future__ import annotations

import asyncpg

from football_analyzer.analysis.bridge import CrossRates, build_cross_model
from football_analyzer.analysis.ratings import MatchRow
from football_analyzer.storage import db

CUP_PREFIXES = ("uefa-", "int-")


def to_match_row(row: object) -> MatchRow:
    return MatchRow(
        kickoff_utc=row["kickoff_utc"],  # type: ignore[index]
        home_team=row["home_team"],  # type: ignore[index]
        away_team=row["away_team"],  # type: ignore[index]
        home_goals=row["home_goals"],  # type: ignore[index]
        away_goals=row["away_goals"],  # type: ignore[index]
        home_goals_ht=row["home_goals_ht"],  # type: ignore[index]
        away_goals_ht=row["away_goals_ht"],  # type: ignore[index]
        home_corners=row["home_corners"],  # type: ignore[index]
        away_corners=row["away_corners"],  # type: ignore[index]
        home_yellow=row["home_yellow"],  # type: ignore[index]
        away_yellow=row["away_yellow"],  # type: ignore[index]
    )


async def fit_all_models(pool: asyncpg.Pool) -> CrossRates:
    """Оценки по лигам + сшивка для еврокубков — по всему, что есть в базе."""
    competitions = await db.competition_ids(pool)
    league_ids = [c for c in competitions if not c.startswith(CUP_PREFIXES)]
    cup_ids = [c for c in competitions if c.startswith(CUP_PREFIXES)]

    league_matches = {
        competition_id: [
            to_match_row(row) for row in await db.finished_matches(pool, competition_id)
        ]
        for competition_id in league_ids
    }
    cup_matches = [
        to_match_row(row)
        for competition_id in cup_ids
        for row in await db.finished_matches(pool, competition_id)
    ]
    return build_cross_model(league_matches, cup_matches)
