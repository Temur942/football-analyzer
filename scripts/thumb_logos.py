"""Уменьшить эмблемы до 40 px: они показываются размером 18 px.

Исходные файлы ESPN — 500×500 и 30–130 КБ каждый. На странице матч-центра
их сотни, и это главная причина тяжёлых страниц. 40 px хватает для экранов
с двойной плотностью (2×18≈36).
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
LOGO_DIR = ROOT / "docs" / "landing" / "logos"
TARGET = 40


def main() -> None:
    shrunk = skipped = 0
    before = after = 0
    for path in sorted(LOGO_DIR.glob("*.png")):
        before += path.stat().st_size
        try:
            with Image.open(path) as image:
                if max(image.size) <= TARGET + 8:
                    skipped += 1
                    after += path.stat().st_size
                    continue
                image = image.convert("RGBA")
                image.thumbnail((TARGET, TARGET), Image.LANCZOS)
                image.save(path, "PNG", optimize=True)
                shrunk += 1
        except Exception:  # noqa: BLE001
            skipped += 1
        after += path.stat().st_size
    print(
        "сжато: %d, пропущено: %d, было %.1f МБ → стало %.1f МБ"
        % (shrunk, skipped, before / 1048576, after / 1048576)
    )


if __name__ == "__main__":
    main()
