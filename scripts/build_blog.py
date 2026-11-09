"""Сборка страниц блога из docs/blog в docs/landing/blog."""

import html
from pathlib import Path

from football_analyzer.blog import Post, load_posts, render_body

ROOT = Path(__file__).resolve().parent.parent
BLOG_DIR = ROOT / "docs" / "blog"
OUT_DIR = ROOT / "docs" / "landing" / "blog"

HEAD = """<!doctype html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title} — Football Analyzer</title>
<meta name="description" content="{summary}">
<meta property="og:title" content="{title}">
<meta property="og:description" content="{summary}">
<link rel="stylesheet" href="../fonts/onest.css">
<link rel="stylesheet" href="../style.css">
</head>
<body>
<header class="nav">
  <a class="brand" href="/">⚽ Football Analyzer</a>
  <nav>
    <a href="/blog/">Статьи</a>
    <a href="/#prices">Тарифы</a>
    <a class="cta" href="https://t.me/footballizator_bot">Открыть бота</a>
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
  <p><a class="button" href="https://t.me/footballizator_bot">Открыть бота</a></p>
</div>
</article>
</main>
<footer>
  <p>18+. Материалы — аналитика и вероятности, а не призыв к ставкам.</p>
  <p>Самозанятый Хидиров Темур Орифжонович · ИНН 021904477296 · khidrov@yandex.ru</p>
</footer>
</body>
</html>
"""

INDEX_HEAD = """<!doctype html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Статьи — Football Analyzer</title>
<meta name="description" content="Разборы футбольных матчей: вероятности голов, углов и карточек по модели.">
<link rel="stylesheet" href="../fonts/onest.css">
<link rel="stylesheet" href="../style.css">
</head>
<body>
<header class="nav">
  <a class="brand" href="/">⚽ Football Analyzer</a>
  <nav>
    <a href="/#prices">Тарифы</a>
    <a class="cta" href="https://t.me/footballizator_bot">Открыть бота</a>
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
</footer>
</body>
</html>
"""


def render_post(post: Post) -> str:
    return HEAD.format(
        title=html.escape(post.title),
        summary=html.escape(post.summary),
        date=html.escape(post.date),
        body=render_body(post.body),
    )


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    posts = load_posts(BLOG_DIR)
    for post in posts:
        (OUT_DIR / ("%s.html" % post.slug)).write_text(render_post(post), encoding="utf-8")
    items = "".join(
        INDEX_ITEM.format(
            date=html.escape(post.date),
            slug=post.slug,
            title=html.escape(post.title),
            summary=html.escape(post.summary),
        )
        for post in posts
    )
    (OUT_DIR / "index.html").write_text(INDEX_HEAD + items + INDEX_TAIL, encoding="utf-8")
    print("статей собрано:", len(posts))


if __name__ == "__main__":
    main()
