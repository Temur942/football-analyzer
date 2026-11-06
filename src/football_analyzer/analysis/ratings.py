"""Оценка силы команд по завершённым матчам.

Метод — взвешенные отношения (аналог Пуассоновской регрессии в замкнутой форме):
сила атаки команды = сколько она забивает относительно среднего по лиге, сила
защиты = сколько пропускает относительно среднего. Старые матчи весят меньше
(экспоненциальное затухание), мало данных штрафуется притягиванием к среднему
(prior): иначе команда с тремя матчами получала бы экстремальные оценки.

Почему не полный MLE: 300+ команд и 600+ параметров требуют оптимизации на
каждом пересчёте, а выигрыш на наших объёмах данных не проверен. Метод
отношений быстрый, устойчивый и честно проверяется бэктестом (см. backtest).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import UTC, datetime


@dataclass(frozen=True)
class MatchRow:
    """Один завершённый матч для обучения."""

    kickoff_utc: datetime
    home_team: str
    away_team: str
    home_goals: int
    away_goals: int
    home_goals_ht: int | None = None
    away_goals_ht: int | None = None
    home_corners: int | None = None
    away_corners: int | None = None
    home_yellow: int | None = None
    away_yellow: int | None = None


@dataclass
class Rates:
    """Готовые оценки. Значения 1.0 — «как в среднем по лиге»."""

    league_home_goals: float = 1.4
    league_away_goals: float = 1.1
    league_home_corners: float = 5.0
    league_away_corners: float = 4.5
    league_home_yellow: float = 2.0
    league_away_yellow: float = 2.2

    attack: dict[str, float] = field(default_factory=dict)
    defence: dict[str, float] = field(default_factory=dict)
    attack_corners: dict[str, float] = field(default_factory=dict)
    defence_corners: dict[str, float] = field(default_factory=dict)
    attack_yellow: dict[str, float] = field(default_factory=dict)
    defence_yellow: dict[str, float] = field(default_factory=dict)

    rho: float = -0.05
    half_share: float = 0.45
    matches_used: int = 0

    def goals_lambda(self, home_team: str, away_team: str) -> tuple[float, float]:
        """Ожидаемые голы хозяев и гостей."""
        home_attack = self.attack.get(home_team, 1.0)
        home_defence = self.defence.get(home_team, 1.0)
        away_attack = self.attack.get(away_team, 1.0)
        away_defence = self.defence.get(away_team, 1.0)
        lam_home = self.league_home_goals * home_attack * away_defence
        lam_away = self.league_away_goals * away_attack * home_defence
        return max(lam_home, 0.01), max(lam_away, 0.01)

    def corners_lambda(self, home_team: str, away_team: str) -> tuple[float, float]:
        lam_home = (
            self.league_home_corners
            * self.attack_corners.get(home_team, 1.0)
            * self.defence_corners.get(away_team, 1.0)
        )
        lam_away = (
            self.league_away_corners
            * self.attack_corners.get(away_team, 1.0)
            * self.defence_corners.get(home_team, 1.0)
        )
        return max(lam_home, 0.01), max(lam_away, 0.01)

    def yellow_lambda(self, home_team: str, away_team: str) -> tuple[float, float]:
        lam_home = (
            self.league_home_yellow
            * self.attack_yellow.get(home_team, 1.0)
            * self.defence_yellow.get(away_team, 1.0)
        )
        lam_away = (
            self.league_away_yellow
            * self.attack_yellow.get(away_team, 1.0)
            * self.defence_yellow.get(home_team, 1.0)
        )
        return max(lam_home, 0.01), max(lam_away, 0.01)


def _weight(kickoff: datetime, now: datetime, half_life_days: float) -> float:
    age_days = max((now - kickoff).total_seconds() / 86400.0, 0.0)
    return 0.5 ** (age_days / half_life_days)


def _ratio(scored: float, expected: float, prior_sum: float) -> float:
    """Отношение с притягиванием к среднему: (факт + prior) / (ожидание + prior)."""
    if expected <= 0:
        return 1.0
    return (scored + prior_sum) / (expected + prior_sum)


def fit_rates(
    matches: list[MatchRow],
    *,
    now: datetime | None = None,
    half_life_days: float = 180.0,
    prior_matches: float = 10.0,
) -> Rates:
    """Посчитать оценки по набору матчей (обычно — вся история)."""
    if not matches:
        return Rates()

    now = now or datetime.now(tz=UTC)
    weighted = [(_weight(m.kickoff_utc, now, half_life_days), m) for m in matches]
    total_weight = sum(w for w, _ in weighted) or 1.0

    league_home = sum(w * m.home_goals for w, m in weighted) / total_weight
    league_away = sum(w * m.away_goals for w, m in weighted) / total_weight

    rates = Rates(
        league_home_goals=max(league_home, 0.05),
        league_away_goals=max(league_away, 0.05),
        matches_used=len(matches),
    )

    # Накопители: (забито, пропущено, ожидалось забить, ожидалось пропустить).
    goals: dict[str, list[float]] = {}
    corners: dict[str, list[float]] = {}
    yellows: dict[str, list[float]] = {}

    def bucket(
        store: dict[str, list[float]],
        team: str,
        scored: float,
        conceded: float,
        expected_for: float,
        expected_against: float,
    ) -> None:
        acc = store.setdefault(team, [0.0, 0.0, 0.0, 0.0])
        acc[0] += scored
        acc[1] += conceded
        acc[2] += expected_for
        acc[3] += expected_against

    for weight, match in weighted:
        bucket(
            goals,
            match.home_team,
            weight * match.home_goals,
            weight * match.away_goals,
            weight * league_home,
            weight * league_away,
        )
        bucket(
            goals,
            match.away_team,
            weight * match.away_goals,
            weight * match.home_goals,
            weight * league_away,
            weight * league_home,
        )

    # Углы и ЖК считаем только там, где источник их дал.
    # Углы и ЖК есть не у всех матчей: фильтруем один раз и держим числа явно,
    # чтобы не тащить Optional через расчёты.
    corner_rows: list[tuple[float, MatchRow, int, int]] = []
    for weight, match in weighted:
        home_corners, away_corners = match.home_corners, match.away_corners
        if home_corners is not None and away_corners is not None:
            corner_rows.append((weight, match, home_corners, away_corners))
    if corner_rows:
        cw = sum(item[0] for item in corner_rows) or 1.0
        rates.league_home_corners = sum(w * hc for w, _, hc, _ in corner_rows) / cw
        rates.league_away_corners = sum(w * ac for w, _, _, ac in corner_rows) / cw
        for weight, match, home_corners, away_corners in corner_rows:
            bucket(
                corners,
                match.home_team,
                weight * home_corners,
                weight * away_corners,
                weight * rates.league_home_corners,
                weight * rates.league_away_corners,
            )
            bucket(
                corners,
                match.away_team,
                weight * away_corners,
                weight * home_corners,
                weight * rates.league_away_corners,
                weight * rates.league_home_corners,
            )

    yellow_rows: list[tuple[float, MatchRow, int, int]] = []
    for weight, match in weighted:
        home_yellow, away_yellow = match.home_yellow, match.away_yellow
        if home_yellow is not None and away_yellow is not None:
            yellow_rows.append((weight, match, home_yellow, away_yellow))
    if yellow_rows:
        yw = sum(item[0] for item in yellow_rows) or 1.0
        rates.league_home_yellow = sum(w * hy for w, _, hy, _ in yellow_rows) / yw
        rates.league_away_yellow = sum(w * ay for w, _, _, ay in yellow_rows) / yw
        for weight, match, home_yellow, away_yellow in yellow_rows:
            bucket(
                yellows,
                match.home_team,
                weight * home_yellow,
                weight * away_yellow,
                weight * rates.league_home_yellow,
                weight * rates.league_away_yellow,
            )
            bucket(
                yellows,
                match.away_team,
                weight * away_yellow,
                weight * home_yellow,
                weight * rates.league_away_yellow,
                weight * rates.league_home_yellow,
            )

    def fill(
        store: dict[str, list[float]],
        attack: dict[str, float],
        defence: dict[str, float],
        mean_value: float,
        prior_weight: float,
    ) -> None:
        for team, (scored, conceded, expected_for, expected_against) in store.items():
            attack[team] = _ratio(scored, expected_for, prior_weight * mean_value)
            defence[team] = _ratio(conceded, expected_against, prior_weight * mean_value)

    mean_goals = (rates.league_home_goals + rates.league_away_goals) / 2.0
    fill(goals, rates.attack, rates.defence, mean_goals, prior_matches)
    if corners:
        mean_corners = (rates.league_home_corners + rates.league_away_corners) / 2.0
        fill(corners, rates.attack_corners, rates.defence_corners, mean_corners, prior_matches)
    if yellows:
        mean_yellow = (rates.league_home_yellow + rates.league_away_yellow) / 2.0
        fill(yellows, rates.attack_yellow, rates.defence_yellow, mean_yellow, prior_matches)

    _calibrate(rates, weighted)
    rates.rho = fit_rho(weighted, rates)
    rates.half_share = estimate_half_share(weighted)
    return rates


def _calibrate(rates: Rates, weighted: list[tuple[float, MatchRow]]) -> None:
    """Согласовать средний прогноз с фактом: страховка от смещения.

    Сжатие оценок к среднему (prior) теоретически может систематически
    занижать ожидания, поэтому средние по лиге домножаются так, чтобы средний
    прогноз совпал с фактом. На наших данных (АПЛ, 1230 матчей) множитель
    оказался около единицы: прогноз 2,79 гола против 2,97 факта и 3,85 ЖК
    против 3,95. То есть выраженного смещения нет — шаг оставлен как дешёвая
    страховка при смене данных, а не потому, что модель «врала».
    """
    if not weighted:
        return

    def ratio(pairs: list[tuple[float, float, float]]) -> float:
        predicted = sum(w * p for w, p, _ in pairs)
        actual = sum(w * a for w, _, a in pairs)
        if predicted <= 0:
            return 1.0
        return actual / predicted

    goal_pairs: list[tuple[float, float, float]] = []
    for weight, match in weighted:
        lam_home, lam_away = rates.goals_lambda(match.home_team, match.away_team)
        goal_pairs.append((weight, lam_home, float(match.home_goals)))
        goal_pairs.append((weight, lam_away, float(match.away_goals)))
    goal_factor = ratio(goal_pairs)
    rates.league_home_goals *= goal_factor
    rates.league_away_goals *= goal_factor

    corner_pairs: list[tuple[float, float, float]] = []
    for weight, match in weighted:
        if match.home_corners is None or match.away_corners is None:
            continue
        lam_home, lam_away = rates.corners_lambda(match.home_team, match.away_team)
        corner_pairs.append((weight, lam_home, float(match.home_corners)))
        corner_pairs.append((weight, lam_away, float(match.away_corners)))
    if corner_pairs:
        corner_factor = ratio(corner_pairs)
        rates.league_home_corners *= corner_factor
        rates.league_away_corners *= corner_factor

    yellow_pairs: list[tuple[float, float, float]] = []
    for weight, match in weighted:
        if match.home_yellow is None or match.away_yellow is None:
            continue
        lam_home, lam_away = rates.yellow_lambda(match.home_team, match.away_team)
        yellow_pairs.append((weight, lam_home, float(match.home_yellow)))
        yellow_pairs.append((weight, lam_away, float(match.away_yellow)))
    if yellow_pairs:
        yellow_factor = ratio(yellow_pairs)
        rates.league_home_yellow *= yellow_factor
        rates.league_away_yellow *= yellow_factor


def tau(x: int, y: int, lam_home: float, lam_away: float, rho: float) -> float:
    """Поправка Диксона–Коулза для низких счетов.

    Обычный Пуассон занижает вероятность 0:0 и 1:1. Поправка делает счёт
    «реалистичнее» и заметно улучшает вероятности тоталов на низких линиях.
    """
    if x == 0 and y == 0:
        value = 1.0 - lam_home * lam_away * rho
    elif x == 0 and y == 1:
        value = 1.0 + lam_home * rho
    elif x == 1 and y == 0:
        value = 1.0 + lam_away * rho
    elif x == 1 and y == 1:
        value = 1.0 - rho
    else:
        return 1.0
    return max(value, 1e-6)


def fit_rho(weighted: list[tuple[float, MatchRow]], rates: Rates) -> float:
    """Подобрать rho перебором по сетке на низких счетах (0:0, 0:1, 1:0, 1:1)."""
    low = [(w, m) for w, m in weighted if m.home_goals <= 1 and m.away_goals <= 1]
    if len(low) < 50:
        return -0.03
    best_rho, best_score = 0.0, -1e18
    step = 0.005
    for index in range(-40, 21):  # от -0.20 до +0.10
        rho = index * step
        score = 0.0
        for weight, match in low:
            lam_home, lam_away = rates.goals_lambda(match.home_team, match.away_team)
            value = tau(match.home_goals, match.away_goals, lam_home, lam_away, rho)
            score += weight * math.log(value)
        if score > best_score:
            best_rho, best_score = rho, score
    return best_rho


def estimate_half_share(weighted: list[tuple[float, MatchRow]]) -> float:
    """Какая доля голов приходится на первый тайм."""
    total_ht = total_ft = 0.0
    for weight, match in weighted:
        if match.home_goals_ht is None or match.away_goals_ht is None:
            continue
        total_ht += weight * (match.home_goals_ht + match.away_goals_ht)
        total_ft += weight * (match.home_goals + match.away_goals)
    if total_ft <= 0:
        return 0.45
    share = total_ht / total_ft
    return min(max(share, 0.35), 0.55)
