"""Модели данных, общие для источника, хранилища и бота."""

from __future__ import annotations

from datetime import UTC, datetime

from pydantic import BaseModel, Field


def season_of(moment: datetime, season_start_month: int = 7) -> str:
    """Сезон для даты: с июля по июнь следующего года.

    Европейские чемпионаты играют по схеме «август — май», поэтому
    «2026-2027» начинается в июле 2026 и заканчивается в июне 2027.
    """
    if moment.month >= season_start_month:
        return "%d-%d" % (moment.year, moment.year + 1)
    return "%d-%d" % (moment.year - 1, moment.year)


class Competition(BaseModel):
    """Соревнование из config/competitions.json."""

    id: str
    name_ru: str
    country: str
    tier: int = 1
    espn: str | None = None
    fbref: str | None = None
    season_start: str = "Aug"
    season_end: str = "May"
    is_active: bool = True


class Fixture(BaseModel):
    """Матч в расписании.

    Отдельная сущность от прогноза: расписание обновляется часто, прогноз
    считается один раз и хранится с привязкой к матчу.
    """

    competition_id: str
    season: str
    kickoff_utc: datetime
    home_team: str
    away_team: str
    source: str = "ESPN"
    source_game_id: str | None = None
    status: str = "scheduled"
    dedupe_key: str = Field(default="")

    def model_post_init(self, __context: object) -> None:  # noqa: D105
        if not self.dedupe_key:
            stamp = self.kickoff_utc.astimezone(UTC).strftime("%Y-%m-%dT%H:%M")
            self.dedupe_key = "%s|%s|%s|%s|%s" % (
                self.competition_id,
                self.season,
                stamp,
                self.home_team,
                self.away_team,
            )
