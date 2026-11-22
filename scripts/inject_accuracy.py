"""Вписать точность в HTML главной, чтобы она была видна без JavaScript.

Зачем: раньше таблица заполнялась скриптом из accuracy.json. Пока скрипт не
выполнится, в HTML стояло «Загрузка…» — и поисковик видел пустоту. Теперь
цифры лежат прямо в разметке, а скрипт не нужен.
"""

from __future__ import annotations

import html
import json
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
INDEX = ROOT / "docs" / "landing" / "index.html"
DATA = ROOT / "docs" / "landing" / "accuracy.json"

ROWS_START = "<!-- ACCURACY:ROWS START -->"
ROWS_END = "<!-- ACCURACY:ROWS END -->"
UPDATED_START = "<!-- ACCURACY:UPDATED START -->"
UPDATED_END = "<!-- ACCURACY:UPDATED END -->"


def human(name: str) -> str:
    return name


def main() -> None:
    if not DATA.exists():
        print("accuracy.json нет — таблица остаётся прежней")
        return
    data = json.loads(DATA.read_text(encoding="utf-8"))
    markets = data.get("markets") or {}
    if not markets:
        print("в accuracy.json нет рынков")
        return

    rows = []
    for name, values in markets.items():
        rate = float(values.get("rate") or 0) * 100
        rows.append(
            "<tr><td>%s</td><td>%s</td><td>%s</td><td>%.1f%%</td></tr>"
            % (html.escape(human(name)), values.get("n", ""), values.get("hits", ""), rate)
        )

    confident = (data.get("confident") or {}).get("исход")
    if confident:
        rate = float(confident.get("rate") or 0) * 100
        rows.append(
            "<tr><td><strong>исход (уверенные)</strong></td><td>%s</td><td>%s</td>"
            "<td><strong>%.1f%%</strong></td></tr>"
            % (confident.get("n", ""), confident.get("hits", ""), rate)
        )

    generated = data.get("generated_at") or ""
    try:
        moment = datetime.fromisoformat(generated)
        stamp = moment.strftime("%d.%m.%Y, %H:%M")
    except ValueError:
        stamp = ""
    window = data.get("window_days", 30)
    updated = "Окно: %s дней. Обновлено: %s." % (window, stamp) if stamp else ""

    text = INDEX.read_text(encoding="utf-8")
    if ROWS_START not in text:
        print("маркеров нет — пропускаю")
        return

    text = (
        text.split(ROWS_START)[0]
        + ROWS_START
        + "\n    "
        + "\n    ".join(rows)
        + "\n    "
        + ROWS_END
        + text.split(ROWS_END, 1)[1]
    )
    text = (
        text.split(UPDATED_START)[0]
        + UPDATED_START
        + updated
        + UPDATED_END
        + text.split(UPDATED_END, 1)[1]
    )
    INDEX.write_text(text, encoding="utf-8")
    print("точность вписана: %d строк, %s" % (len(rows), updated))


if __name__ == "__main__":
    main()
