"""Сравнение модели с рынком: 1X2 и тотал 2,5.

Модель обучается только на матчах ДО отсечки, проверяется на матчах после —
иначе сравнение бессмысленно (утечка данных). Рынок — средние закрывающие
коэффициенты с убранной маржой.

Сопоставление матчей идёт через таблицу team_aliases (её строит
scripts/build_aliases.py), поэтому названия команд не мешают.
"""

from __future__ import annotations

import asyncio
import os
from datetime import UTC, datetime

from football_analyzer.analysis.bridge import build_cross_model
from football_analyzer.analysis.forecast import build_forecast
from football_analyzer.analysis.service import CUP_PREFIXES
from football_analyzer.cli import to_match_row
from football_analyzer.storage import db

CUTOFF = datetime(2026, 6, 1, tzinfo=UTC)


def implied_triplet(
    home: float | None, draw: float | None, away: float | None
) -> tuple[float, float, float] | None:
    """Вероятности рынка из коэффициентов (снимаем маржу делением на сумму)."""
    if not home or not draw or not away:
        return None
    values = [1 / float(home), 1 / float(draw), 1 / float(away)]
    total = sum(values)
    return (values[0] / total, values[1] / total, values[2] / total)


def implied_over(over_odds: float | None, under_odds: float | None) -> float | None:
    if not over_odds or not under_odds:
        return None
    over = 1 / float(over_odds)
    under = 1 / float(under_odds)
    return over / (over + under)


async def main() -> None:
    pool = await db.connect(os.environ["DATABASE_URL"])
    competitions = await db.competition_ids(pool)
    league_ids = [c for c in competitions if not c.startswith(CUP_PREFIXES)]

    league_matches: dict[str, list] = {}
    for competition_id in league_ids:
        rows = await db.finished_matches(pool, competition_id)
        league_matches[competition_id] = [db_row_to_match(row, competition_id) for row in rows]

    train = {
        competition_id: [m for m in matches if m["kickoff"] < CUTOFF]
        for competition_id, matches in league_matches.items()
    }
    model = build_cross_model(
        {
            competition_id: [m["raw"] for m in matches if m["kickoff"] < CUTOFF]
            for competition_id, matches in train.items()
        },
        [],
        now=CUTOFF,
    )

    async with pool.acquire() as conn:
        odds = await conn.fetch(
            """
            SELECT o.competition, o.match_date, o.avg_home, o.avg_draw, o.avg_away,
                   o.over25, o.under25,
                   home_alias.our_name AS home_team, away_alias.our_name AS away_team
              FROM market_odds o
              JOIN team_aliases home_alias
                ON home_alias.competition_id = o.competition
               AND home_alias.source_name = o.home_team
              JOIN team_aliases away_alias
                ON away_alias.competition_id = o.competition
               AND away_alias.source_name = o.away_team
            """
        )
        total_odds = await conn.fetchval("SELECT count(*) FROM market_odds")
    await pool.close()

    index = {}
    for competition_id, matches in league_matches.items():
        for match in matches:
            index[(competition_id, match["date"], match["home"], match["away"])] = match

    matched = 0
    evaluated = 0
    model_1x2 = market_1x2 = 0.0
    model_total = market_total = 0.0
    total_count = 0

    for row in odds:
        key = (row["competition"], row["match_date"], row["home_team"], row["away_team"])
        match = index.get(key)
        if match is None:
            continue
        matched += 1
        if match["kickoff"] < CUTOFF or match["goals"] is None:
            continue
        rates = model.league_rates.get(row["competition"])
        if rates is None:
            continue
        try:
            forecast = build_forecast(
                rates,
                competition_id=row["competition"],
                home_team=match["home"],
                away_team=match["away"],
                kickoff_utc=match["kickoff"],
            )
        except Exception:  # noqa: BLE001
            continue
        evaluated += 1

        market = implied_triplet(row["avg_home"], row["avg_draw"], row["avg_away"])
        home_goals, away_goals = match["goals"]
        actual = (
            (1.0, 0.0, 0.0)
            if home_goals > away_goals
            else (0.0, 1.0, 0.0)
            if home_goals == away_goals
            else (0.0, 0.0, 1.0)
        )
        model_probs = (forecast.p_home, forecast.p_draw, forecast.p_away)
        model_1x2 += sum((p - y) ** 2 for p, y in zip(model_probs, actual, strict=True))
        if market is not None:
            market_1x2 += sum((p - y) ** 2 for p, y in zip(market, actual, strict=True))

        over = implied_over(row["over25"], row["under25"])
        if over is not None:
            actual_over = 1.0 if home_goals + away_goals > 2.5 else 0.0
            model_total += (forecast.total[2.5] - actual_over) ** 2
            market_total += (over - actual_over) ** 2
            total_count += 1

    print("строк с коэффициентами:", total_odds)
    print("сопоставлено матчей:", matched)
    print("в проверке после отсечки:", evaluated)
    if evaluated:
        print(
            "1X2 Brier: модель %.4f | рынок %.4f" % (model_1x2 / evaluated, market_1x2 / evaluated)
        )
    if total_count:
        print(
            "Тотал 2,5 Brier: модель %.4f | рынок %.4f (матчей %d)"
            % (model_total / total_count, market_total / total_count, total_count)
        )
    print("меньше — лучше")


def db_row_to_match(row, competition_id: str) -> dict:
    return {
        "raw": to_match_row(row),
        "competition": competition_id,
        "date": row["kickoff_utc"].date(),
        "home": row["home_team"],
        "away": row["away_team"],
        "kickoff": row["kickoff_utc"],
        "goals": (
            (int(row["home_goals"]), int(row["away_goals"]))
            if row["home_goals"] is not None and row["away_goals"] is not None
            else None
        ),
    }


if __name__ == "__main__":
    asyncio.run(main())
