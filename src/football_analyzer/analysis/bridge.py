"""Сшивка лиг: сравнимые оценки силы для еврокубков.

Проблема: оценка «атака = 1.3» в Сегунде и в АПЛ означает разное. Прямо
сравнивать команды из разных лиг нельзя — их средние разные.

Решение: у каждой лиги есть коэффициент силы s. Сила команды в общем масштабе:
атака × s(лига). Коэффициенты подбираются по матчам еврокубков, где команды из
разных лиг встречаются напрямую, — то есть по данным, а не по репутации лиги.

Как считаем: максимизируем пуассоновское правдоподобие голов в еврокубковых
матчах по log s, с регуляризацией (тянет к «сила всех лиг одинакова»). Общий
масштаб неопределим, поэтому средний log s принудительно равен нулю.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from datetime import UTC, datetime

from scipy.optimize import minimize

from football_analyzer.analysis.ratings import MatchRow, Rates, fit_rates

log = logging.getLogger(__name__)


@dataclass
class CrossRates:
    """Модель, которая умеет считать и лиговые, и межлиговые матчи.

    Интерфейс совпадает с Rates (goals_lambda / corners_lambda / yellow_lambda),
    поэтому подходит в build_forecast без изменений.
    """

    league_rates: dict[str, Rates] = field(default_factory=dict)
    team_league: dict[str, str] = field(default_factory=dict)
    strength: dict[str, float] = field(default_factory=dict)
    mu_home: float = 1.4
    mu_away: float = 1.1
    rho: float = -0.05
    half_share: float = 0.45
    league_corners_home: dict[str, float] = field(default_factory=dict)
    league_corners_away: dict[str, float] = field(default_factory=dict)
    league_yellow_home: dict[str, float] = field(default_factory=dict)
    league_yellow_away: dict[str, float] = field(default_factory=dict)

    def _league(self, team: str) -> str | None:
        return self.team_league.get(team)

    def _team_rate(self, team: str, field_name: str) -> float:
        league = self._league(team)
        if league is None:
            return 1.0
        rates = self.league_rates.get(league)
        if rates is None:
            return 1.0
        return getattr(rates, field_name).get(team, 1.0)

    def goals_lambda(self, home_team: str, away_team: str) -> tuple[float, float]:
        home_league = self._league(home_team)
        away_league = self._league(away_team)
        scale_home = self.strength.get(home_league, 1.0) if home_league else 1.0
        scale_away = self.strength.get(away_league, 1.0) if away_league else 1.0
        lam_home = (
            self.mu_home
            * scale_home
            * self._team_rate(home_team, "attack")
            * scale_away
            * self._team_rate(away_team, "defence")
        )
        lam_away = (
            self.mu_away
            * scale_away
            * self._team_rate(away_team, "attack")
            * scale_home
            * self._team_rate(home_team, "defence")
        )
        return max(lam_home, 0.05), max(lam_away, 0.05)

    def _league_base(self, table: dict[str, float], team: str, fallback: float) -> float:
        league = self._league(team)
        return table.get(league, fallback) if league else fallback

    def corners_lambda(self, home_team: str, away_team: str) -> tuple[float, float]:
        # Углы — свойство игры команды; межлиговую силу к ним не применяем
        # (проверять отдельно), но средние берём по своей лиге.
        base_home = self._league_base(self.league_corners_home, home_team, 5.0)
        base_away = self._league_base(self.league_corners_away, away_team, 4.5)
        lam_home = (
            base_home
            * self._team_rate(home_team, "attack_corners")
            * self._team_rate(away_team, "defence_corners")
        )
        lam_away = (
            base_away
            * self._team_rate(away_team, "attack_corners")
            * self._team_rate(home_team, "defence_corners")
        )
        return max(lam_home, 0.2), max(lam_away, 0.2)

    def yellow_lambda(self, home_team: str, away_team: str) -> tuple[float, float]:
        base_home = self._league_base(self.league_yellow_home, home_team, 2.0)
        base_away = self._league_base(self.league_yellow_away, away_team, 2.2)
        lam_home = (
            base_home
            * self._team_rate(home_team, "attack_yellow")
            * self._team_rate(away_team, "defence_yellow")
        )
        lam_away = (
            base_away
            * self._team_rate(away_team, "attack_yellow")
            * self._team_rate(home_team, "defence_yellow")
        )
        return max(lam_home, 0.2), max(lam_away, 0.2)


def team_leagues(league_matches: dict[str, list[MatchRow]]) -> dict[str, str]:
    """Команда → лига по её последнему матчу в лиге."""
    latest: dict[str, tuple[datetime, str]] = {}
    for competition_id, matches in league_matches.items():
        for match in matches:
            for team in (match.home_team, match.away_team):
                current = latest.get(team)
                if current is None or match.kickoff_utc > current[0]:
                    latest[team] = (match.kickoff_utc, competition_id)
    return {team: competition_id for team, (_, competition_id) in latest.items()}


def _strength_objective(
    log_strength: list[float],
    preparation: list[tuple[float, float]],
    league_index: list[int],
    home_goals: list[int],
    away_goals: list[int],
    regularization: float,
) -> float:
    """Отрицательное лог-правдоподобие голов в еврокубках."""
    total = 0.0
    for index, (pred_home, pred_away) in enumerate(preparation):
        scale_home = math.exp(log_strength[league_index[index * 2]])
        scale_away = math.exp(log_strength[league_index[index * 2 + 1]])
        lam_home = pred_home * scale_home * scale_away
        lam_away = pred_away * scale_home * scale_away
        total += lam_home - home_goals[index] * math.log(lam_home)
        total += lam_away - away_goals[index] * math.log(lam_away)
    total += regularization * sum(value * value for value in log_strength)
    return total


def fit_league_strength(
    cross_matches: list[MatchRow],
    league_rates: dict[str, Rates],
    team_league: dict[str, str],
    *,
    regularization: float = 8.0,
) -> dict[str, float]:
    """Подобрать коэффициенты силы лиг по матчам еврокубков."""
    named: set[str] = set()
    for match in cross_matches:
        for team in (match.home_team, match.away_team):
            league = team_league.get(team)
            if league:
                named.add(league)
    leagues = sorted(named)
    if len(leagues) < 2:
        return {}

    index: dict[str, int] = {league: position for position, league in enumerate(leagues)}
    preparation: list[tuple[float, float]] = []
    league_index: list[int] = []
    home_goals: list[int] = []
    away_goals: list[int] = []

    for match in cross_matches:
        home_league = team_league.get(match.home_team)
        away_league = team_league.get(match.away_team)
        if home_league is None or away_league is None:
            continue
        home_rates = league_rates.get(home_league)
        away_rates = league_rates.get(away_league)
        if home_rates is None or away_rates is None:
            continue
        # Прогноз без силы лиг: средние по лигам уже учтены их собственными
        # базовыми значениями, поэтому берём отношения к среднему.
        pred_home = (
            home_rates.league_home_goals
            * home_rates.attack.get(match.home_team, 1.0)
            * away_rates.defence.get(match.away_team, 1.0)
        )
        pred_away = (
            away_rates.league_away_goals
            * away_rates.attack.get(match.away_team, 1.0)
            * home_rates.defence.get(match.home_team, 1.0)
        )
        if pred_home <= 0 or pred_away <= 0:
            continue
        preparation.append((pred_home, pred_away))
        league_index.append(index[home_league])
        league_index.append(index[away_league])
        home_goals.append(match.home_goals)
        away_goals.append(match.away_goals)

    if len(preparation) < 30:
        log.warning("мало матчей для сшивки лиг: %d", len(preparation))
        return {}

    start = [0.0] * len(leagues)
    result = minimize(
        _strength_objective,
        start,
        args=(preparation, league_index, home_goals, away_goals, regularization),
        method="L-BFGS-B",
    )
    values = list(result.x)
    # Общий масштаб неопределим: центрируем средний log по нулю.
    mean = sum(values) / len(values)
    values = [value - mean for value in values]
    return {league: math.exp(value) for league, value in zip(leagues, values, strict=True)}


def build_cross_model(
    league_matches: dict[str, list[MatchRow]],
    cross_matches: list[MatchRow],
    *,
    now: datetime | None = None,
    half_life_days: float = 180.0,
) -> CrossRates:
    """Полная модель: оценки по лигам + сила лиг + общие средние."""
    now = now or datetime.now(tz=UTC)
    league_rates = {
        competition_id: fit_rates(matches, now=now, half_life_days=half_life_days)
        for competition_id, matches in league_matches.items()
        if len(matches) >= 100
    }
    team_league = team_leagues(league_matches)

    all_matches = [match for matches in league_matches.values() for match in matches]
    total = len(all_matches) or 1
    mu_home = sum(match.home_goals for match in all_matches) / total
    mu_away = sum(match.away_goals for match in all_matches) / total

    model = CrossRates(
        league_rates=league_rates,
        team_league=team_league,
        strength=fit_league_strength(cross_matches, league_rates, team_league),
        mu_home=max(mu_home, 0.05),
        mu_away=max(mu_away, 0.05),
        league_corners_home={cid: rates.league_home_corners for cid, rates in league_rates.items()},
        league_corners_away={cid: rates.league_away_corners for cid, rates in league_rates.items()},
        league_yellow_home={cid: rates.league_home_yellow for cid, rates in league_rates.items()},
        league_yellow_away={cid: rates.league_away_yellow for cid, rates in league_rates.items()},
    )
    if league_rates:
        first = next(iter(league_rates.values()))
        model.rho = first.rho
        model.half_share = first.half_share

    if model.strength:
        ordered = sorted(model.strength.items(), key=lambda item: item[1], reverse=True)
        log.info(
            "сила лиг: %s",
            ", ".join("%s %.2f" % (league, value) for league, value in ordered),
        )
    return model
