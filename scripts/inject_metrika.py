"""Вставка счётчика Метрики во все HTML лендинга.

Отдельный проход, а не правка шаблонов: страницы генерируются тремя разными
скриптами, и один постпроцессор надёжнее, чем три копии счётчика.
"""

from __future__ import annotations

from pathlib import Path

from football_analyzer.metrika import SNIPPET

ROOT = Path(__file__).resolve().parent.parent
LANDING = ROOT / "docs" / "landing"


def main() -> None:
    injected = 0
    for path in LANDING.rglob("*.html"):
        text = path.read_text(encoding="utf-8")
        if "mc.yandex.ru/metrika/tag.js" in text:
            continue
        if "</body>" not in text:
            continue
        text = text.replace("</body>", SNIPPET + "</body>", 1)
        path.write_text(text, encoding="utf-8")
        injected += 1
    print("счётчик добавлен на страниц:", injected)


if __name__ == "__main__":
    main()
