"""Сборка страниц лендинга из юридических документов (markdown в HTML).

Один источник правды: тексты лежат в docs/legal, здесь только оформление.
"""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LEGAL = ROOT / "docs" / "legal"
OUT = ROOT / "docs" / "landing"

PAGES = {
    "offer.md": ("Публичная оферта", "offer.html"),
    "terms.md": ("Пользовательское соглашение", "terms.html"),
    "privacy.md": ("Политика обработки персональных данных", "privacy.html"),
}

TEMPLATE = """<!doctype html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title} — Football Analyzer</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Onest:wght@400;500;700&family=Roboto+Mono:wght@400&display=swap" rel="stylesheet">
<link rel="stylesheet" href="style.css">
</head>
<body>
<header class="nav">
  <a class="brand" href="/">⚽ Football Analyzer</a>
  <nav>
    <a href="/#prices">Тарифы</a>
    <a href="/#accuracy">Точность</a>
    <a class="cta" href="https://t.me/footballizator_bot">Открыть бота</a>
  </nav>
</header>
<main class="legal">
<h1>{title}</h1>
{body}
<p class="muted">Редакция 1.0. Актуальная версия — в боте, раздел «Документы».</p>
</main>
<footer>
  <p>18+. Материалы — аналитика и вероятности, а не призыв к ставкам.</p>
  <p>Самозанятый Хидиров Темур Орифжонович · ИНН 021904477296 · khidrov@yandex.ru</p>
</footer>
</body>
</html>
"""


def inline(text: str) -> str:
    text = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    text = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", text)
    return text


def convert(markdown: str) -> str:
    html: list[str] = []
    in_list = False
    for raw in markdown.splitlines():
        line = raw.rstrip()
        if not line or line.startswith("**Редакция"):
            if in_list:
                html.append("</ul>")
                in_list = False
            continue
        if line.startswith("# "):
            continue  # заголовок страницы уже есть
        if line.startswith("## "):
            if in_list:
                html.append("</ul>")
                in_list = False
            html.append("<h2>%s</h2>" % inline(line[3:]))
        elif line.startswith("### "):
            html.append("<h3>%s</h3>" % inline(line[4:]))
        elif line.startswith("- "):
            if not in_list:
                html.append("<ul>")
                in_list = True
            html.append("<li>%s</li>" % inline(line[2:]))
        elif line.startswith("|"):
            if in_list:
                html.append("</ul>")
                in_list = False
            cells = [c.strip() for c in line.strip("|").split("|")]
            if all(set(c) <= {"-", ":", " "} and c for c in cells):
                continue
            html.append("<p class='table-row'>%s</p>" % " · ".join(inline(c) for c in cells))
        else:
            if in_list:
                html.append("</ul>")
                in_list = False
            html.append("<p>%s</p>" % inline(line))
    if in_list:
        html.append("</ul>")
    return "\n".join(html)


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    for source, (title, target) in PAGES.items():
        body = convert((LEGAL / source).read_text(encoding="utf-8"))
        (OUT / target).write_text(TEMPLATE.format(title=title, body=body), encoding="utf-8")
        print("собрано:", target)

    accuracy = OUT / "accuracy.json"
    if not accuracy.is_file():
        print("внимание: accuracy.json отсутствует — цифры на лендинге будут пустыми")


if __name__ == "__main__":
    main()
