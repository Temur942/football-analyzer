"""Баннер cookie и счётчик, который грузится только после согласия.

Проход по всем страницам лендинга: убирает старый счётчик (он грузился сразу),
ставит новый — с проверкой согласия, добавляет баннер и скрипт.

Файл подтверждения Google (google*.html) не трогаем: там должен остаться
ровно текст подтверждения.
"""

from __future__ import annotations

import re
from pathlib import Path

from football_analyzer.metrika import SNIPPET

ROOT = Path(__file__).resolve().parent.parent
LANDING = ROOT / "docs" / "landing"

BANNER = """<div class="fa-cookie" id="fa-cookie-banner" hidden>
  <p>Мы используем cookie для работы сайта и аналитики.
     <a href="/cookies.html">Подробнее</a> · <a href="/privacy.html">Политика данных</a></p>
  <div class="fa-cookie-actions">
    <button type="button" data-consent="necessary">Только необходимые</button>
    <button type="button" data-consent="accepted">Принять все</button>
  </div>
</div>
"""

OLD_BLOCK = re.compile(r"<!-- Yandex\.Metrika counter.*?<!-- /Yandex\.Metrika counter -->\s*", re.S)


def main() -> None:
    processed = 0
    for path in LANDING.rglob("*.html"):
        if path.name.startswith("google"):
            continue
        text = path.read_text(encoding="utf-8")
        text = OLD_BLOCK.sub("", text)

        if "</body>" not in text:
            continue

        if "__faLoadMetrica" not in text:
            text = text.replace("</body>", SNIPPET + "</body>", 1)
        if "fa-cookie-banner" not in text:
            text = text.replace("</body>", BANNER + "</body>", 1)
        if "cookie-consent.js" not in text:
            text = text.replace("</body>", '<script src="/cookie-consent.js"></script>\n</body>', 1)
        path.write_text(text, encoding="utf-8")
        processed += 1
    print("страниц обработано:", processed)


if __name__ == "__main__":
    main()
