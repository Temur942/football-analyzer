"""Сборка страниц блога из docs/blog в docs/landing/blog.

Страницы попадают в тот же каталог, который отдаёт nginx (docs/landing),
поэтому статьи открываются по адресу https://footballinho.ru/blog/<slug>.html
без отдельной настройки сервера.
"""

import html
import json
from pathlib import Path

from football_analyzer.blog import Post, load_posts, render_body

ROOT = Path(__file__).resolve().parent.parent
BLOG_DIR = ROOT / "docs" / "blog"
OUT_DIR = ROOT / "docs" / "landing" / "blog"
SITE = "https://footballinho.ru"
BOT_URL = "https://t.me/footballizator_bot?start=ch_site"

HEAD = """<!doctype html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title} — FOOTBALLINHO</title>
<meta name="description" content="{summary}">
{robots}
<link rel="canonical" href="{canonical}">
<meta property="og:type" content="article">
<meta property="og:locale" content="ru_RU">
<meta property="og:site_name" content="FOOTBALLINHO">
<meta property="og:url" content="{canonical}">
<meta property="og:image" content="https://footballinho.ru/og.png">
<meta property="og:image:width" content="1200">
<meta property="og:image:height" content="630">
<meta name="twitter:card" content="summary_large_image">
<meta property="og:title" content="{title}">
<meta property="og:description" content="{summary}">
<meta property="article:published_time" content="{date_iso}">
<link rel="stylesheet" href="../fonts/onest.css">
<meta name="yandex-verification" content="2d7c2dde8b71028d">
<link rel="stylesheet" href="../style.css">
<script type="application/ld+json">{jsonld}</script>
</head>
<body>
<header class="nav">
  <a class="brand" href="/">FOOTBALLINHO</a>
  <nav>
    <a href="/blog/">Статьи</a>
    <a href="/about.html">О сервисе</a>
    <a href="/#prices">Тарифы</a>
    <a class="cta" href="{bot}">Открыть бота</a>
  </nav>
</header>
<main>
<article class="post">
<p class="muted">{date}</p>
<h1>{title}</h1>
{body}
<div class="card">
  <h3>Полная раскладка — в боте</h3>
  <p>Углы, карточки, голы каждой команды и вероятный счёт по 20 соревнованиям.
     Прогноз приходит за 5 часов до матча.</p>
  <p><a class="button" href="{bot}">Открыть бота</a></p>
  <p class="muted"><a href="/match-center/">Все матчи и вероятности — в матч-центре</a> ·
  <a href="/guide/">Разборы: xG, тоталы, форма</a> ·
  <a href="/table/">Таблицы</a> · <a href="/schedule/">Расписание</a> ·
  <a href="/results/">Результаты</a></p>
</div>
</article>
</main>
<footer>
  <p>18+. Материалы — аналитика и вероятности, а не призыв к ставкам.</p>
  <p>Самозанятый Хидиров Темур Орифжонович · ИНН 021904477296 · khidrov@yandex.ru</p>
  <p>
    <a href="/offer.html">Оферта</a> ·
    <a href="/terms.html">Соглашение</a> ·
    <a href="/privacy.html">Политика ПД</a> ·
    <a href="/consent.html">Согласие</a> ·
    <a href="/rights.html">Права</a> ·
    <a href="/cookies.html">Cookie</a> ·
    <a href="#" data-cookie-settings>Настройки cookie</a>
  </p>
</footer>
</body>
</html>
"""

INDEX_HEAD = """<!doctype html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Статьи — FOOTBALLINHO</title>
<meta name="description" content="Разборы матчей: вероятности голов, углов и карточек.">
<link rel="canonical" href="{canonical}">
<link rel="stylesheet" href="../fonts/onest.css">
<link rel="stylesheet" href="../style.css">
</head>
<body>
<header class="nav">
  <a class="brand" href="/">FOOTBALLINHO</a>
  <nav>
    <a href="/about.html">О сервисе</a>
    <a href="/#prices">Тарифы</a>
    <a class="cta" href="{bot}">Открыть бота</a>
  </nav>
</header>
<main>
<section class="hero">
  <h1>Статьи</h1>
  <p>Разборы матчей: что модель считает вероятным и почему.</p>
</section>
"""

INDEX_ITEM = """<div class="card">
  <p class="muted">{date}</p>
  <h3><a href="{slug}.html">{title}</a></h3>
  <p>{summary}</p>
</div>
"""

INDEX_TAIL = """</main>
<footer>
  <p>18+. Материалы — аналитика и вероятности, а не призыв к ставкам.</p>
  <p>Самозанятый Хидиров Темур Орифжонович · ИНН 021904477296 · khidrov@yandex.ru</p>
  <p>
    <a href="/offer.html">Оферта</a> ·
    <a href="/terms.html">Соглашение</a> ·
    <a href="/privacy.html">Политика ПД</a> ·
    <a href="/consent.html">Согласие</a> ·
    <a href="/rights.html">Права</a> ·
    <a href="/cookies.html">Cookie</a> ·
    <a href="#" data-cookie-settings>Настройки cookie</a>
  </p>
</footer>
</body>
</html>
"""


def ru_date(value: str) -> str:
    """2026-11-13 → 13.11.2026. Не ISO оставляем как есть."""
    parts = value.split("-")
    if len(parts) == 3 and all(part.isdigit() for part in parts):
        return "%s.%s.%s" % (parts[2], parts[1], parts[0])
    return value


def article_jsonld(post: Post, canonical: str) -> str:
    """Разметка статьи: поиск понимает дату, раздел и автора."""
    return json.dumps(
        {
            "@context": "https://schema.org",
            "@type": "Article",
            "headline": post.title,
            "description": post.summary,
            "datePublished": post.date,
            "dateModified": post.date,
            "inLanguage": "ru-RU",
            "mainEntityOfPage": canonical,
            "author": {"@type": "Organization", "name": "FOOTBALLINHO"},
            "publisher": {"@type": "Organization", "name": "FOOTBALLINHO", "url": SITE + "/"},
        },
        ensure_ascii=False,
    )


def is_auto_preview(slug: str) -> bool:
    """Авто-превью дня: тонкие страницы, Google их не индексирует.

    Мы их оставляем для канала и как архив, но закрываем от поиска:
    материал дублирует данные страниц матчей.
    """
    return "-day-" in slug


def render_post(post: Post) -> str:
    auto_preview = is_auto_preview(post.slug)
    canonical = "%s/blog/%s.html" % (SITE, post.slug)
    return HEAD.format(
        title=html.escape(post.title),
        summary=html.escape(post.summary),
        date=html.escape(ru_date(post.date)),
        date_iso=html.escape(post.date),
        canonical=canonical,
        jsonld=article_jsonld(post, canonical),
        robots=('<meta name="robots" content="noindex, follow">' if auto_preview else ""),
        body=render_body(post.body),
        bot=BOT_URL,
    )


def build_rss(posts) -> None:
    """Лента статей: поисковики быстрее замечают новые материалы."""
    from datetime import datetime, timezone
    from datetime import time as dt_time
    from email.utils import format_datetime

    items = []
    for post in posts[:20]:
        try:
            published = datetime.combine(
                datetime.strptime(post.date, "%Y-%m-%d").date(),
                dt_time(10, 0),
                tzinfo=timezone.utc,
            )
        except (ValueError, AttributeError):
            published = datetime.now(timezone.utc)
        items.append(
            "    <item>\n"
            "      <title>%s</title>\n"
            "      <link>https://footballinho.ru/blog/%s.html</link>\n"
            "      <guid>https://footballinho.ru/blog/%s.html</guid>\n"
            "      <pubDate>%s</pubDate>\n"
            "      <description>%s</description>\n"
            "    </item>"
            % (
                _xml(post.title),
                post.slug,
                post.slug,
                format_datetime(published),
                _xml(post.summary),
            )
        )
    feed = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<rss version="2.0">\n  <channel>\n'
        "    <title>FOOTBALLINHO — статьи</title>\n"
        "    <link>https://footballinho.ru/blog/</link>\n"
        "    <description>Разборы футбольной аналитики и вероятностей</description>\n"
        "    <language>ru</language>\n" + "\n".join(items) + "\n  </channel>\n</rss>\n"
    )
    (ROOT / "docs" / "landing" / "rss.xml").write_text(feed, encoding="utf-8")


def _xml(text: str) -> str:
    return (
        text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")
    )


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    posts = load_posts(BLOG_DIR)
    build_rss(posts)
    for post in posts:
        (OUT_DIR / ("%s.html" % post.slug)).write_text(render_post(post), encoding="utf-8")
    items = "".join(
        INDEX_ITEM.format(
            date=html.escape(ru_date(post.date)),
            slug=post.slug,
            title=html.escape(post.title),
            summary=html.escape(post.summary),
        )
        for post in posts
    )
    (OUT_DIR / "index.html").write_text(
        INDEX_HEAD.format(canonical=SITE + "/blog/", bot=BOT_URL) + items + INDEX_TAIL,
        encoding="utf-8",
    )
    print("статей собрано:", len(posts))


if __name__ == "__main__":
    main()
