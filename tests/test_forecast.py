"""Проверки текста прогноза: время, скрытые линии, разбивка сообщений."""

from datetime import UTC, datetime

from football_analyzer.analysis.forecast import build_forecast, render_payload, render_text
from football_analyzer.analysis.ratings import Rates
from football_analyzer.bot.docs import split_text


def rates(**overrides: object) -> Rates:
    base = dict(
        attack={"А": 1.1, "Б": 0.95},
        defence={"А": 0.95, "Б": 1.05},
        attack_corners={"А": 1.0, "Б": 1.0},
        defence_corners={"А": 1.0, "Б": 1.0},
        attack_yellow={"А": 1.0, "Б": 1.0},
        defence_yellow={"А": 1.0, "Б": 1.0},
        rho=-0.05,
        half_share=0.43,
    )
    base.update(overrides)
    return Rates(**base)  # type: ignore[arg-type]


def build(competition_id: str = "eng-premier-league") -> str:
    forecast = build_forecast(
        rates(),
        competition_id=competition_id,
        home_team="А",
        away_team="Б",
        kickoff_utc=datetime(2026, 11, 8, 14, 0, tzinfo=UTC),
    )
    return render_text(forecast, "Тестовая лига")


def test_time_is_moscow() -> None:
    """14:00 UTC — это 17:00 по Москве; пользователь не должен видеть UTC."""
    assert "17:00 МСК" in build()


def test_league_shows_all_lines() -> None:
    text = build("eng-premier-league")
    corners = text.split("Угловые")[1].split("Жёлтые")[0]
    assert "8,5" in corners


def test_cup_hides_weak_lines() -> None:
    """В еврокубках слабые линии не показываем: модель их почти не различает."""
    text = build("uefa-champions-league")
    corners = text.split("Угловые")[1].split("Жёлтые")[0]
    cards = text.split("Жёлтые")[1]
    assert "8,5" not in corners
    assert "3,5" not in cards
    assert "9,5" in corners


def test_every_block_has_percentages() -> None:
    text = build()
    for block in ("тотал матча", "первого тайма", "угловые", "жёлтые карточки"):
        assert block in text.lower()
    assert "%" in text


def test_split_text_respects_limit() -> None:
    text = "\n\n".join("Абзац номер %d. " % index + "текст " * 40 for index in range(30))
    chunks = split_text(text, limit=1000)
    assert chunks
    assert all(len(chunk) <= 1000 for chunk in chunks)
    import re

    # Пробелы и переводы строк при сборке частей меняются — сравниваем текст.
    assert re.sub(r"\s+", "", "".join(chunks)) == re.sub(r"\s+", "", text)


def test_render_payload_keeps_total_lines_but_hides_weak_market_lines() -> None:
    """Фильтр скрытых линий не должен задевать тотал: 3,5 голов — нормальная линия."""
    payload = {
        "competition_id": "uefa-champions-league",
        "home_team": "А",
        "away_team": "Б",
        "kickoff_utc": "2026-11-08T14:00:00+00:00",
        "p_home": 0.5,
        "p_draw": 0.26,
        "p_away": 0.24,
        "total": {"1.5": 0.74, "2.5": 0.48, "3.5": 0.27},
        "first_half": {"0.5": 0.67, "1.5": 0.31},
        "corners": {"8.5": 0.55, "9.5": 0.48, "10.5": 0.35},
        "yellow": {"3.5": 0.45, "4.5": 0.41, "5.5": 0.25},
    }
    text = render_payload(payload, "Лига чемпионов")
    lines = [line for line in text.splitlines() if line.startswith("ТБ")]
    totals, corners, cards = lines[0], lines[2], lines[3]

    assert "3,5" in totals
    assert "8,5" not in corners
    assert "9,5" in corners
    assert "3,5" not in cards
    assert "4,5" in cards
