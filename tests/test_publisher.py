"""Проверки текстов для канала: пост-прогноз и пост точности."""

from datetime import UTC, datetime

from football_analyzer.analysis.forecast import build_forecast
from football_analyzer.analysis.ratings import Rates
from football_analyzer.scheduler.publisher import accuracy_post, post_text


def make_forecast():  # noqa: ANN201
    rates = Rates(
        attack={"А": 1.1, "Б": 0.95},
        defence={"А": 0.95, "Б": 1.05},
        attack_corners={"А": 1.0, "Б": 1.0},
        defence_corners={"А": 1.0, "Б": 1.0},
        attack_yellow={"А": 1.0, "Б": 1.0},
        defence_yellow={"А": 1.0, "Б": 1.0},
        rho=-0.05,
        half_share=0.43,
    )
    return build_forecast(
        rates,
        competition_id="eng-premier-league",
        home_team="А",
        away_team="Б",
        kickoff_utc=datetime(2026, 11, 8, 14, 0, tzinfo=UTC),
    )


def test_post_contains_all_outcomes() -> None:
    """Обрезка текста уже теряла «Ничья» и «Гости» — проверяем, что все три на месте."""
    text = post_text(make_forecast(), "Тестовая лига")
    assert "П1" in text and "Х" in text and "П2" in text
    assert "Тотал" in text
    assert "Счёт" in text


def test_post_has_moscow_time_and_link() -> None:
    text = post_text(make_forecast(), "Тестовая лига")
    assert "МСК" in text
    assert "@footballizator_bot" in text


def test_post_marks_18_plus() -> None:
    text = post_text(make_forecast(), "Тестовая лига")
    assert "18+" in text


def test_accuracy_post_formats_percentages() -> None:
    report = {
        "window_days": 30,
        "markets": {
            "исход": {"n": 100, "hits": 54, "rate": 0.54},
            "тотал 2,5": {"n": 100, "hits": 55, "rate": 0.55},
        },
        "confident": {"исход": {"n": 10, "hits": 7, "rate": 0.7}},
    }
    text = accuracy_post(report)
    assert "54.0%" in text or "54,0%" in text
    assert "70.0%" in text or "70,0%" in text
    assert "30 дней" in text


def test_accuracy_post_without_confident() -> None:
    """Пустые данные не должны ломать пост."""
    text = accuracy_post({"window_days": 30, "markets": {}, "confident": {}})
    assert "30 дней" in text
