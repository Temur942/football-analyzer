"""Блог: разбор простого формата статей и сборка HTML.

Формат файла docs/blog/*.md:

    # Заголовок
    Дата: 2026-11-12
    Slug: national-teams-12-11
    Краткое: одна строка для списка

    ---

    Текст статьи в markdown (абзацы, ## подзаголовки, списки).

    === TELEGRAM ===

    Текст для канала (плоский, без markdown-таблиц).

Один файл — два вывода: страница на сайте и пост в канале. Так они никогда
не разъедутся, а править нужно в одном месте.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Post:
    title: str
    date: str
    slug: str
    summary: str
    body: str
    telegram: str = ""
    fields: dict[str, str] = field(default_factory=dict)


def parse_post(path: Path) -> Post:
    raw = path.read_text(encoding="utf-8")
    article, _, telegram = raw.partition("=== TELEGRAM ===")
    header, _, body = article.partition("\n---\n")

    lines = [line.strip() for line in header.splitlines() if line.strip()]
    title = lines[0].lstrip("# ").strip() if lines else path.stem
    fields: dict[str, str] = {}
    for line in lines[1:]:
        key, _, value = line.partition(":")
        fields[key.strip().lower()] = value.strip()

    return Post(
        title=title,
        date=fields.get("дата", ""),
        slug=fields.get("slug", path.stem),
        summary=fields.get("краткое", ""),
        body=body.strip(),
        telegram=telegram.strip(),
        fields=fields,
    )


def load_posts(directory: Path) -> list[Post]:
    posts = [parse_post(path) for path in sorted(directory.glob("*.md"))]
    return sorted(posts, key=lambda post: (post.date, post.slug), reverse=True)


LINK = re.compile(r"\[([^\]]+)\]\(([^)\s]+)\)")
BOLD = re.compile(r"\*\*(.+?)\*\*")
SAFE_HREF = re.compile(r"^(https?://|/|#|[\w.-]+\.html$|[\w-]+/$)")


def _link(match: re.Match[str]) -> str:
    """[текст](адрес) — но только для внутренних страниц и http(s).

    Внутренние ссылки между статьями и на лендинг нужны для поиска: без них
    статья остаётся островом, на который не ведёт ни одна страница.
    """
    text, href = match.group(1), match.group(2).replace('"', "%22")
    if not SAFE_HREF.match(href):
        return match.group(0)
    return '<a href="%s">%s</a>' % (href, text)


def inline(text: str) -> str:
    text = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    text = LINK.sub(_link, text)
    return BOLD.sub(r"<strong>\1</strong>", text)


def render_body(markdown: str) -> str:
    html: list[str] = []
    in_list = False
    for line in markdown.splitlines():
        stripped = line.strip()
        if not stripped:
            if in_list:
                html.append("</ul>")
                in_list = False
            continue
        if stripped.startswith("## "):
            if in_list:
                html.append("</ul>")
                in_list = False
            html.append("<h2>%s</h2>" % inline(stripped[3:]))
        elif stripped.startswith("- "):
            if not in_list:
                html.append("<ul>")
                in_list = True
            html.append("<li>%s</li>" % inline(stripped[2:]))
        else:
            if in_list:
                html.append("</ul>")
                in_list = False
            html.append("<p>%s</p>" % inline(stripped))
    if in_list:
        html.append("</ul>")
    return "\n".join(html)
