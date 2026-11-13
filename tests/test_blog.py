"""Проверки формата статей блога: разбор файла, ссылки, жирный текст."""

from pathlib import Path

from football_analyzer.blog import inline, load_posts, parse_post, render_body

SAMPLE = """# Тотал 2,5: что это значит

Дата: 2026-11-13
Slug: total-25
Краткое: разбор линии

---

Абзац со **жирным** и [ссылкой](poisson-model.html).

## Подзаголовок

- первый пункт
- второй пункт

=== TELEGRAM ===

Пост в канал.
"""


def test_parse_post(tmp_path: Path) -> None:
    path = tmp_path / "total-25.md"
    path.write_text(SAMPLE, encoding="utf-8")

    post = parse_post(path)

    assert post.title == "Тотал 2,5: что это значит"
    assert post.slug == "total-25"
    assert post.date == "2026-11-13"
    assert post.summary == "разбор линии"
    assert post.telegram == "Пост в канал."
    assert "TELEGRAM" not in post.body


def test_render_body_keeps_structure() -> None:
    html = render_body("Первый абзац.\n\n## Раздел\n\n- один\n- два")

    assert "<p>Первый абзац.</p>" in html
    assert "<h2>Раздел</h2>" in html
    assert "<ul>" in html and "<li>один</li>" in html


def test_link_becomes_anchor() -> None:
    link = inline("см. [модель](poisson-model.html)")
    assert link == 'см. <a href="poisson-model.html">модель</a>'


def test_only_safe_links_become_anchors() -> None:
    """javascript: и подобное в статью не должно попадать как ссылка."""
    assert "<a" not in inline("[жми](javascript:alert(1))")
    assert '<a href="/prices.html">тарифы</a>' in inline("[тарифы](/prices.html)")


def test_bold_survives_link() -> None:
    assert inline("**[бот](/prices.html)**") == '<strong><a href="/prices.html">бот</a></strong>'


def test_load_posts_sorted_newest_first(tmp_path: Path) -> None:
    (tmp_path / "old.md").write_text(
        "# Старое\nДата: 2026-11-01\nSlug: old\n\n---\n\nТекст.", encoding="utf-8"
    )
    (tmp_path / "new.md").write_text(
        "# Новое\nДата: 2026-11-13\nSlug: new\n\n---\n\nТекст.", encoding="utf-8"
    )

    assert [post.slug for post in load_posts(tmp_path)] == ["new", "old"]
