"""Сборка прогноза и вывод в процентах.

Формат сообщения — по требованию заказчика: не «прогнозы», а математически
посчитанные проценты по линиям (тотал, тотал 1-го тайма, исход, счёт,
индивидуальные тоталы, углы, ЖК).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from zoneinfo import ZoneInfo

from football_analyzer.analysis.poisson import (
    outcome_probs,
    poisson_over,
    score_matrix,
    team_over,
    top_scores,
    total_over,
)
from football_analyzer.analysis.ratings import Rates

MSK = ZoneInfo("Europe/Moscow")

# В еврокубках модель почти не различает эти линии (Brier ≈ 0.25, как у
# «всегда 50%» по бэктесту 08.11.2026) — показывать их нельзя: это продажа
# шума как знания.
CUP_PREFIXES = ("uefa-", "int-")
HIDDEN_CUP_LINES = {"corners": 8.5, "yellow": 3.5}

GOAL_LINES = (1.5, 2.5, 3.5)
HALF_LINES = (0.5, 1.5)
TEAM_LINES = (0.5, 1.5)
CORNER_LINES = (8.5, 9.5, 10.5)
YELLOW_LINES = (3.5, 4.5, 5.5)


@dataclass
class Forecast:
    """Числа прогноза. Отдельно от текста: текст можно менять свободно."""

    competition_id: str
    home_team: str
    away_team: str
    kickoff_utc: datetime
    lam_home: float
    lam_away: float
    lam_corners_home: float
    lam_corners_away: float
    lam_yellow_home: float
    lam_yellow_away: float
    p_home: float
    p_draw: float
    p_away: float
    total: dict[float, float] = field(default_factory=dict)
    first_half: dict[float, float] = field(default_factory=dict)
    home_goals: dict[float, float] = field(default_factory=dict)
    away_goals: dict[float, float] = field(default_factory=dict)
    corners: dict[float, float] = field(default_factory=dict)
    yellow: dict[float, float] = field(default_factory=dict)
    scores: list[tuple[int, int, float]] = field(default_factory=list)
    weather_note: str | None = None


def build_forecast(
    rates: Rates,
    *,
    competition_id: str,
    home_team: str,
    away_team: str,
    kickoff_utc: datetime,
    weather_factor: float = 1.0,
    weather_note: str | None = None,
) -> Forecast:
    """Посчитать все вероятности для одного матча.

    weather_factor — множитель ожидаемых голов (1.0 = без поправки).
    Применяется к обеим командам одинаково: погода влияет на игру целиком,
    а не на одну сторону.
    """
    lam_home, lam_away = rates.goals_lambda(home_team, away_team)
    lam_home *= weather_factor
    lam_away *= weather_factor

    matrix = score_matrix(lam_home, lam_away, rates.rho)
    p_home, p_draw, p_away = outcome_probs(matrix)

    lam_corner_home, lam_corner_away = rates.corners_lambda(home_team, away_team)
    lam_yellow_home, lam_yellow_away = rates.yellow_lambda(home_team, away_team)
    lam_first_half = (lam_home + lam_away) * rates.half_share

    return Forecast(
        competition_id=competition_id,
        home_team=home_team,
        away_team=away_team,
        kickoff_utc=kickoff_utc,
        lam_home=lam_home,
        lam_away=lam_away,
        lam_corners_home=lam_corner_home,
        lam_corners_away=lam_corner_away,
        lam_yellow_home=lam_yellow_home,
        lam_yellow_away=lam_yellow_away,
        p_home=p_home,
        p_draw=p_draw,
        p_away=p_away,
        total={line: total_over(matrix, line) for line in GOAL_LINES},
        first_half={line: poisson_over(lam_first_half, line) for line in HALF_LINES},
        home_goals={line: team_over(matrix, "home", line) for line in TEAM_LINES},
        away_goals={line: team_over(matrix, "away", line) for line in TEAM_LINES},
        corners={
            line: poisson_over(lam_corner_home + lam_corner_away, line) for line in CORNER_LINES
        },
        yellow={
            line: poisson_over(lam_yellow_home + lam_yellow_away, line) for line in YELLOW_LINES
        },
        scores=top_scores(matrix, 3),
        weather_note=weather_note,
    )


def _pct(value: float) -> str:
    return "%d%%" % round(value * 100)


def _line(value: float) -> str:
    """1.5 -> «1,5» (русская запятая)."""
    return ("%.1f" % value).replace(".", ",")


def _lines(values: dict[float, float], prefix: str = "ТБ") -> str:
    return " · ".join(
        "%s %s — <b>%s</b>" % (prefix, _line(line), _pct(p)) for line, p in values.items()
    )


def render_text(forecast: Forecast, competition_name: str | None = None) -> str:
    """Текст прогноза для Telegram.

    Время — московское: аудитория русскоязычная, UTC в сообщениях только путал.
    """
    title = forecast.home_team + " — " + forecast.away_team
    kickoff = forecast.kickoff_utc.astimezone(MSK)

    is_cup = forecast.competition_id.startswith(CUP_PREFIXES)
    corners = {
        line: value
        for line, value in forecast.corners.items()
        if not (is_cup and line == HIDDEN_CUP_LINES["corners"])
    }
    yellow = {
        line: value
        for line, value in forecast.yellow.items()
        if not (is_cup and line == HIDDEN_CUP_LINES["yellow"])
    }

    scores = " · ".join(
        "<b>%d:%d</b> %s" % (home, away, _pct(probability))
        for home, away, probability in forecast.scores
    )

    blocks = [
        "⚽ <b>%s</b>" % title,
        "🏆 %s" % (competition_name or forecast.competition_id),
        "🕐 %s МСК" % kickoff.strftime("%d.%m, %H:%M"),
        "",
        "🎯 <b>Исход</b>",
        "▫️ Хозяева — <b>%s</b>" % _pct(forecast.p_home),
        "▫️ Ничья — <b>%s</b>" % _pct(forecast.p_draw),
        "▫️ Гости — <b>%s</b>" % _pct(forecast.p_away),
        "",
        "🥅 <b>Тотал матча</b>",
        _lines(forecast.total),
        "",
        "⏱ <b>Тотал первого тайма</b>",
        _lines(forecast.first_half),
        "",
        "🔮 <b>Вероятные счета</b>",
        scores,
        "",
        "👥 <b>Голы команд</b>",
        "▫️ Хозяева: " + _lines(forecast.home_goals),
        "▫️ Гости: " + _lines(forecast.away_goals),
        "",
        "🚩 <b>Угловые</b>",
        _lines(corners),
        "",
        "🟨 <b>Жёлтые карточки</b>",
        _lines(yellow),
    ]
    if forecast.weather_note:
        blocks += ["", "🌦 <b>Погода:</b> " + forecast.weather_note]
    blocks += ["", "<i>Вероятности, а не гарантии. 18+</i>"]
    return "\n".join(blocks)


def render_payload(payload: dict, competition_name: str | None = None) -> str:
    """Собрать текст прогноза из сохранённого JSON (история «Мои прогнозы»).

    Нужно, чтобы показать ранее отправленный прогноз без пересчёта модели:
    цифры должны быть те же, что человек получил, даже если модель обновилась.
    """
    kickoff = datetime.fromisoformat(payload["kickoff_utc"]).astimezone(MSK)
    competition_id = str(payload.get("competition_id", ""))
    is_cup = competition_id.startswith(CUP_PREFIXES)

    def pct(value: float) -> str:
        return "%d%%" % round(float(value) * 100)

    def line_text(values: dict, hidden: float | None = None) -> str:
        """Строка линий. hidden — линия, скрытая для еврокубков."""
        parts = []
        for line, value in values.items():
            number = float(line)
            if is_cup and hidden is not None and number == hidden:
                continue
            parts.append("ТБ %s — <b>%s</b>" % (line.replace(".", ","), pct(value)))
        return " · ".join(parts)

    corners = dict(payload.get("corners", {}))
    yellow = dict(payload.get("yellow", {}))

    blocks = [
        "⚽ <b>%s — %s</b>" % (payload["home_team"], payload["away_team"]),
        "🏆 %s" % (competition_name or competition_id),
        "🕐 %s МСК" % kickoff.strftime("%d.%m, %H:%M"),
        "",
        "🎯 <b>Исход</b>",
        "▫️ Хозяева — <b>%s</b>" % pct(payload["p_home"]),
        "▫️ Ничья — <b>%s</b>" % pct(payload["p_draw"]),
        "▫️ Гости — <b>%s</b>" % pct(payload["p_away"]),
        "",
        "🥅 <b>Тотал матча</b>",
        line_text(payload.get("total", {})),
        "",
        "⏱ <b>Тотал первого тайма</b>",
        line_text(payload.get("first_half", {})),
    ]
    if corners:
        blocks += ["", "🚩 <b>Угловые</b>", line_text(corners, HIDDEN_CUP_LINES["corners"])]
    if yellow:
        blocks += ["", "🟨 <b>Жёлтые карточки</b>", line_text(yellow, HIDDEN_CUP_LINES["yellow"])]
    blocks += ["", "<i>Вероятности, а не гарантии. 18+</i>"]
    return "\n".join(blocks)
