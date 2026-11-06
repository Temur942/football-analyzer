"""Распределение счёта и вероятности по линиям.

Счёт считается двумерным Пуассоном с поправкой Диксона–Коулза на низкие счета.
Углы и ЖК — одномерный Пуассон по сумме ожидаемых значений обеих команд
(сумма независимых пуассоновских величин — снова пуассон).
"""

from __future__ import annotations

import math

from football_analyzer.analysis.ratings import tau


def poisson_pmf(k: int, lam: float) -> float:
    return math.exp(-lam) * lam**k / math.factorial(k)


def poisson_over(lam: float, line: float) -> float:
    """P(X > line) для пуассоновской величины."""
    threshold = math.floor(line)
    return max(0.0, 1.0 - sum(poisson_pmf(k, lam) for k in range(threshold + 1)))


def score_matrix(
    lam_home: float, lam_away: float, rho: float, max_goals: int = 10
) -> list[list[float]]:
    """Матрица вероятностей счетов [i][j] с поправкой на низкие счета."""
    matrix = [
        [
            poisson_pmf(i, lam_home) * poisson_pmf(j, lam_away) * tau(i, j, lam_home, lam_away, rho)
            for j in range(max_goals + 1)
        ]
        for i in range(max_goals + 1)
    ]
    total = sum(sum(row) for row in matrix)
    if total <= 0:
        return [[0.0] * (max_goals + 1) for _ in range(max_goals + 1)]
    return [[value / total for value in row] for row in matrix]


def outcome_probs(matrix: list[list[float]]) -> tuple[float, float, float]:
    """Вероятности (победа хозяев, ничья, победа гостей)."""
    home = sum(matrix[i][j] for i in range(len(matrix)) for j in range(len(matrix)) if i > j)
    draw = sum(matrix[i][i] for i in range(len(matrix)))
    away = max(0.0, 1.0 - home - draw)
    return home, draw, away


def total_over(matrix: list[list[float]], line: float) -> float:
    """Вероятность, что сумма голов больше линии."""
    return sum(matrix[i][j] for i in range(len(matrix)) for j in range(len(matrix)) if i + j > line)


def team_over(matrix: list[list[float]], side: str, line: float) -> float:
    """Вероятность, что команда забьёт больше линии."""
    index = 0 if side == "home" else 1
    result = 0.0
    for i in range(len(matrix)):
        for j in range(len(matrix)):
            goals = i if index == 0 else j
            if goals > line:
                result += matrix[i][j]
    return result


def top_scores(matrix: list[list[float]], limit: int = 4) -> list[tuple[int, int, float]]:
    """Самые вероятные счета."""
    cells = [(i, j, matrix[i][j]) for i in range(len(matrix)) for j in range(len(matrix))]
    cells.sort(key=lambda item: item[2], reverse=True)
    return cells[:limit]
