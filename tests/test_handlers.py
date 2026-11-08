"""Проверки тарифов, разбора ссылок и текстов бота — без сети и базы."""

from football_analyzer.bot.handlers import (
    TARIFFS,
    TRIAL_DAYS,
    _match_from,
    _referrer_from,
    _source_from,
    order_text,
    subscription_text,
    tariff,
    tariffs_text,
)


def test_tariffs_have_cheap_entry() -> None:
    prices = dict(TARIFFS)
    assert prices[7] == 149
    assert prices[30] == 499
    assert prices[90] == 1199
    assert min(prices.values()) < 200


def test_tariff_lookup() -> None:
    assert tariff(7) == (7, 149)
    assert tariff(90) == (90, 1199)
    # Неизвестный срок не должен ломать экран — отдаём основной тариф.
    assert tariff(12345) == (30, 499)


def test_referral_link_parsing() -> None:
    assert _referrer_from("ref_42") == 42
    assert _referrer_from("abc") is None
    assert _referrer_from(None) is None
    # Битое число не должно ронять обработчик.
    assert _referrer_from("ref_abc") is None


def test_source_parsing() -> None:
    assert _source_from("ch_vk") == "vk"
    assert _source_from("ref_42") is None


def test_tariffs_text_lists_all_plans() -> None:
    text = tariffs_text()
    for _, price in TARIFFS:
        assert "%d ₽" % price in text
    assert "бесплатно" in text


def test_order_text_uses_selected_plan() -> None:
    text = order_text(7, 149)
    assert "7 дней" in text
    assert "149 ₽" in text


def test_subscription_text_without_subscription() -> None:
    assert "не активна" in subscription_text(None)


def test_trial_is_three_days() -> None:
    assert TRIAL_DAYS == 3


def test_match_deeplink_parsing() -> None:
    assert _match_from("match_12345") == 12345
    assert _match_from("ref_42") is None
    assert _match_from("match_abc") is None
    assert _match_from(None) is None
