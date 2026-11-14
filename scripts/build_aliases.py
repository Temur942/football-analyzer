"""Построить соответствие названий команд между источником и нашей базой.

Автоматически: нормализация + сравнение похожести внутри одной лиги.
Результат складываем в team_aliases, чтобы не гадать каждый раз.
"""

from __future__ import annotations

import asyncio
import difflib
import os

from football_analyzer.storage import db
from football_analyzer.teams_norm import normalize

THRESHOLD = 0.82


def similarity(first: str, second: str) -> float:
    left, right = normalize(first), normalize(second)
    if left == right:
        return 1.0
    # Одно название — часть другого («Celtic» ⊂ «Celtic Glasgow»).
    left_tokens, right_tokens = set(left.split()), set(right.split())
    if left_tokens and right_tokens:
        shorter, longer = (
            (left_tokens, right_tokens)
            if len(left_tokens) <= len(right_tokens)
            else (right_tokens, left_tokens)
        )
        if shorter <= longer and len(" ".join(shorter)) >= 4:
            return 0.92
    return difflib.SequenceMatcher(None, left, right).ratio()


async def main() -> None:
    pool = await db.connect(os.environ["DATABASE_URL"])
    await db.apply_schema(pool)

    async with pool.acquire() as conn:
        ours = await conn.fetch(
            "SELECT DISTINCT competition_id, home_team AS name FROM matches"
            " UNION SELECT DISTINCT competition_id, away_team FROM matches"
        )
        theirs = await conn.fetch(
            "SELECT DISTINCT competition, home_team AS name FROM market_odds"
            " UNION SELECT DISTINCT competition, away_team FROM market_odds"
        )

    our_by_competition: dict[str, set[str]] = {}
    for row in ours:
        our_by_competition.setdefault(row["competition_id"], set()).add(row["name"])

    the_by_competition: dict[str, set[str]] = {}
    for row in theirs:
        the_by_competition.setdefault(row["competition"], set()).add(row["name"])

    rows: list[tuple] = []
    matched = total = 0
    for competition, names in the_by_competition.items():
        our_names = our_by_competition.get(competition, set())
        if not our_names:
            continue
        for name in names:
            total += 1
            best_name, best_score = None, 0.0
            for candidate in our_names:
                score = similarity(name, candidate)
                if score > best_score:
                    best_name, best_score = candidate, score
            if best_name is not None and best_score >= THRESHOLD:
                rows.append((competition, name, best_name, round(best_score, 3)))
                matched += 1

    async with pool.acquire() as conn:
        await conn.executemany(
            "INSERT INTO team_aliases (competition_id, source_name, our_name, score)"
            " VALUES ($1, $2, $3, $4)"
            " ON CONFLICT (competition_id, source_name) DO UPDATE SET"
            " our_name = EXCLUDED.our_name, score = EXCLUDED.score",
            rows,
        )
        result = await conn.fetchval("SELECT count(*) FROM team_aliases")

    print("названий у источника:", total)
    print("сопоставлено автоматически:", matched, "(%.0f%%)" % (100 * matched / max(1, total)))
    print("в таблице соответствий:", result)
    await pool.close()


if __name__ == "__main__":
    asyncio.run(main())
