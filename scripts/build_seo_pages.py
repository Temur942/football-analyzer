"""Страницы под поисковые запросы: АПЛ, углы, чтение вероятностей.

Задача — не дублировать матч-центр, а дать ответ на вопрос, который люди
вводят в поиск: «как считать углы», «что значит вероятность 54%»,
«прогнозы на АПЛ». Уникальный текст + живые цифры по ближайшим матчам.
"""

# HTML-страницы: длинные строки намеренно.
# ruff: noqa: E501

from __future__ import annotations

import asyncio
import html
import os
from pathlib import Path

from football_analyzer.analysis.forecast import MSK, build_forecast
from football_analyzer.analysis.service import CUP_PREFIXES, fit_all_models
from football_analyzer.storage import db
from football_analyzer.teams_ru import to_russian

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "docs" / "landing"
BASE_URL = "https://footballinho.ru"
BOT_URL = "https://t.me/footballizator_bot?start=ch_site"

PAGE = """<!doctype html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<meta name="description" content="{description}">
<meta name="yandex-verification" content="2d7c2dde8b71028d">
<link rel="canonical" href="{canonical}">
<link rel="stylesheet" href="/fonts/onest.css">
<link rel="stylesheet" href="/style.css">
<script type="application/ld+json">{jsonld}</script>
</head>
<body>
<header class="nav">
  <a class="brand" href="/">⚽ Football Analyzer</a>
  <nav>
    <a href="/match-center/">Матч-центр</a>
    <a href="/blog/">Статьи</a>
    <a href="/about.html">О сервисе</a>
    <a class="cta" href="{bot}">Открыть бота</a>
  </nav>
</header>
<main>
{content}
<div class="card" style="margin-top:32px">
  <h3>Полная раскладка — в боте</h3>
  <p>Здесь главное. В боте: индивидуальные тоталы команд, все линии углов
     и карточек, вероятный счёт и доставка за 5 часов до матча.</p>
  <p><a class="button" href="{bot}">Открыть бота</a></p>
</div>
</main>
<footer>
  <p>18+. Материалы — аналитика и вероятности, а не призыв к ставкам.</p>
  <p>Самозанятый Хидиров Темур Орифжонович · ИНН 021904477296 · khidrov@yandex.ru</p>
  <p><a href="https://t.me/footballinho_ru">Канал в Telegram</a></p>
</footer>
</body>
</html>
"""


def line_text(probability: float, label: str) -> str:
    if probability >= 1 - probability:
        return "ТБ %s — %.0f%%" % (label, probability * 100)
    return "ТМ %s — %.0f%%" % (label, (1 - probability) * 100)


def write_page(relative: str, *, title: str, description: str, content: str) -> Path:
    path = OUT / relative / "index.html"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        PAGE.format(
            title=html.escape(title),
            description=html.escape(description)[:300],
            canonical="%s/%s/" % (BASE_URL, relative),
            jsonld='{"@context":"https://schema.org","@type":"Article"}',
            content=content,
            bot=BOT_URL,
        ),
        encoding="utf-8",
    )
    return path


async def build() -> None:
    pool = await db.connect(os.environ["DATABASE_URL"])
    rows = await db.upcoming_matches(pool, 14)
    model = await fit_all_models(pool)
    await pool.close()

    prepared = []
    for match in rows:
        competition = match["competition_id"]
        rates = (
            model if competition.startswith(CUP_PREFIXES) else model.league_rates.get(competition)
        )
        if rates is None:
            continue
        try:
            forecast = build_forecast(
                rates,
                competition_id=competition,
                home_team=match["home_team"],
                away_team=match["away_team"],
                kickoff_utc=match["kickoff_utc"],
            )
        except Exception:  # noqa: BLE001
            continue
        prepared.append((match, forecast))

    # 1. АПЛ: живая таблица + объяснение
    epl = [(m, f) for m, f in prepared if m["competition_id"] == "eng-premier-league"]
    epl_rows = []
    for match, forecast in epl[:12]:
        kickoff = match["kickoff_utc"].astimezone(MSK)
        epl_rows.append(
            '<tr><td>%s</td><td><a href="/match/%d/">%s — %s</a></td>'
            "<td>П1 %.0f%% · Х %.0f%% · П2 %.0f%%</td><td>%s</td><td>%s</td></tr>"
            % (
                kickoff.strftime("%d.%m %H:%M"),
                match["id"],
                html.escape(to_russian(match["home_team"])),
                html.escape(to_russian(match["away_team"])),
                forecast.p_home * 100,
                forecast.p_draw * 100,
                forecast.p_away * 100,
                line_text(forecast.total[2.5], "2,5"),
                line_text(forecast.corners[9.5], "9,5"),
            )
        )
    epl_table = (
        "<table><thead><tr><th>Время</th><th>Матч</th><th>Исход</th>"
        "<th>Тотал</th><th>Углы</th></tr></thead><tbody>%s</tbody></table>" % "".join(epl_rows)
        if epl_rows
        else "<p class='muted'>Ближайших матчей АПЛ в расписании нет.</p>"
    )
    write_page(
        "predictions/premier-league",
        title="Прогнозы на АПЛ: вероятности матчей по математике | Football Analyzer",
        description="Вероятности исходов, тоталов и углов на матчи Английской "
        "премьер-лиги. Расчёт по трём сезонам истории, обновляется каждый час. 18+",
        content="""
<h1>Прогнозы на АПЛ по математике</h1>
<p>Мы не угадываем счёт и не опираемся на «мнение эксперта». Каждый матч
Английской премьер-лиги считается по одной модели: сила атаки и защиты команд
относительно среднего по лиге, распределение Пуассона с поправкой на низкие
счета, отдельная поправка на домашнее преимущество.</p>
<p>Старые матчи весят меньше: модель смотрит на последние полгода, а не на
результаты двухлетней давности. Поэтому смена тренера или состава отражается
в цифрах быстрее, чем в таблице.</p>
<h2 class="section-title">Ближайшие матчи АПЛ</h2>
__TABLE__
<h2 class="section-title">Что означают проценты</h2>
<p>«П1 60%» — это не «Арсенал обязательно победит». Это значит: в похожих
матчах по модели хозяева выигрывали примерно в шести случаях из десяти.
Проигрыш при 60% — нормальное событие, а не ошибка прогноза.</p>
<p>Полная раскладка (голы каждой команды, все линии углов и карточек,
вероятный счёт) приходит в боте за 5 часов до матча.</p>
<p><a href="/guide/probabilities/">Как читать вероятности →</a></p>
""".replace("__TABLE__", epl_table),
    )

    # 2. Углы
    write_page(
        "guide/corners",
        title="Углы в футболе: как считать вероятности | Football Analyzer",
        description="Как прогнозировать количество угловых в футбольном матче: "
        "база по лиге, сила команд, темп. Примеры расчёта и типичные ошибки. 18+",
        content="""
<h1>Углы в футболе: как считается вероятность</h1>
<p>Угловые — рынок, который любят недооценивать: кажется, что это лотерея.
На самом деле у углов есть устойчивая структура, и её видно в данных.</p>

<h2 class="section-title">Откуда берётся база</h2>
<p>В каждой лиге своё среднее число углов. В АПЛ это около 10 на матч,
в Чемпионшипе — около 10,5, в некоторых вторых дивизионах — 9. Если считать
«в среднем по футболу», ошибка будет уже на старте.</p>

<h2 class="section-title">Что двигает цифру</h2>
<ul>
  <li><strong>Стиль команды.</strong> Команды, играющие через фланги и навесы, зарабатывают больше углов.</li>
  <li><strong>Темп матча.</strong> Аутсайдер, отбивающийся у своей штрафной, отдаёт мяч — углы растут у соперника.</li>
  <li><strong>Счёт.</strong> Проигрывающая команда чаще идёт вперёд. Но прогноз считается до матча, поэтому этот фактор недоступен.</li>
</ul>

<h2 class="section-title">Как считаем мы</h2>
<p>Углы моделируются отдельно от голов: своя средняя по лиге, своя сила команд
по заработанным и допущенным углам, своё затухание по времени. Для еврокубков
база берётся по турниру: команды из разных чемпионатов нельзя сравнивать
по средней лиги напрямую.</p>
<p>Честное ограничение: линии около среднего (8,5–9,5) предсказываются плохо.
Проверка на отложенных матчах показала, что сложные поправки (калибровка,
отрицательное биномиальное, темп) результат не улучшают — поэтому мы не
показываем их как «точные».</p>

<h2 class="section-title">Типичная ошибка</h2>
<p>Смотреть на одну игру и делать вывод. Углы — сильно случайная величина:
при ожидании 10 реально бывает и 4, и 17. Работает только серия и сравнение
с вероятностью, а не отдельный матч.</p>
<p><a href="/match-center/">Ближайшие матчи и вероятности углов →</a></p>
""",
    )

    # 3. Как читать вероятности
    write_page(
        "guide/probabilities",
        title="Как читать вероятности в футбольных прогнозах | Football Analyzer",
        description="Что значит вероятность 54% в прогнозе на футбол, чем она "
        "отличается от обещания и как проверять точность модели. 18+",
        content="""
<h1>Как читать вероятности</h1>
<p>Главная путаница в футбольных прогнозах: процент воспринимают как обещание.
«54% на тотал больше 2,5» — это не «будет больше». Это оценка частоты.</p>

<h2 class="section-title">Три правила</h2>
<ol>
  <li><strong>Вероятность — это не гарантия.</strong> Событие с 70% случается примерно в семи случаях из десяти. Три из десяти — норма.</li>
  <li><strong>Смотрите на серию.</strong> Один матч ничего не доказывает. Проверять модель нужно на десятках матчей.</li>
  <li><strong>Сравнивайте с ценой.</strong> Вероятность 54% при коэффициенте 1,7 — это одно, при 1,9 — другое. Мы коэффициенты не считаем и не советуем, что ставить.</li>
</ol>

<h2 class="section-title">Пример</h2>
<p>Модель даёт: тотал больше 2,5 — 54%, углы больше 9,5 — 62%, жёлтые меньше 4,5 — 71%.
Это значит, что из ста похожих матчей модель ждёт примерно 54 с тремя голами,
62 с десятью углами и 71 с четырьмя карточками.</p>
<p>Если в конкретном матче вышло иначе — с моделью всё в порядке. Проверка
идёт на выборке, а не на одном результате.</p>

<h2 class="section-title">Как проверяем мы</h2>
<p>Модель обучается на прошлом и проверяется на матчах, которых не видела.
Результат публикуется каждую неделю, включая неудачные отрезки: показать
только выигрышные прогнозы — это обман, а не статистика.</p>
<p>Отдельно и честно: на исторических коэффициентах рынок точнее нашей модели
примерно на 3%. Мы не обещаем обыграть букмекера.</p>
<p><a href="/about.html">Как устроен сервис →</a> · <a href="/match-center/">Матч-центр →</a></p>
""",
    )

    print("страницы: predictions/premier-league, guide/corners, guide/probabilities")


if __name__ == "__main__":
    asyncio.run(build())
