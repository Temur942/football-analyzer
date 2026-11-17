"""Картинка для превью ссылок (og:image).

Без неё ссылка на сайт в Telegram и ВК выглядит голой строкой, а с картинкой
занимает в разы больше места и кликается заметно чаще.
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

WIDTH, HEIGHT = 1200, 630
BOLD = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
REGULAR = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"

OUT = Path(__file__).resolve().parent.parent / "docs" / "landing" / "og.png"


def font(size: int, bold: bool = True) -> ImageFont.FreeTypeFont:
    try:
        return ImageFont.truetype(BOLD if bold else REGULAR, size)
    except OSError:
        return ImageFont.load_default()


def main() -> None:
    image = Image.new("RGB", (WIDTH, HEIGHT), (15, 23, 42))
    draw = ImageDraw.Draw(image, "RGBA")

    for y in range(HEIGHT):
        ratio = y / HEIGHT
        color = (
            int(12 + (15 - 12) * ratio),
            int(32 + (23 - 32) * ratio),
            int(26 + (42 - 26) * ratio),
        )
        draw.line([(0, y), (WIDTH, y)], fill=color)

    for x in range(0, WIDTH, 140):
        draw.rectangle([x, 0, x + 70, HEIGHT], fill=(255, 255, 255, 6))

    draw.text((70, 70), "FOOTBALL ANALYZER", font=font(30, False), fill=(188, 172, 146))
    draw.line([(70, 120), (WIDTH - 70, 120)], fill=(16, 185, 129, 140), width=3)

    draw.text((70, 175), "Вероятности", font=font(74), fill=(237, 230, 218))
    draw.text((70, 265), "футбольных матчей", font=font(74), fill=(237, 230, 218))

    draw.text(
        (70, 400),
        "Тоталы · исходы · счёт · углы · карточки",
        font=font(36, False),
        fill=(16, 185, 129),
    )
    draw.text(
        (70, 460),
        "За 5 часов до матча — в Telegram",
        font=font(32, False),
        fill=(188, 172, 146),
    )

    draw.text((70, HEIGHT - 70), "footballinho.ru", font=font(30), fill=(237, 230, 218))
    draw.text((WIDTH - 70, HEIGHT - 70), "18+", font=font(30), fill=(245, 158, 11), anchor="ra")

    image.save(OUT, "PNG", optimize=True)
    print("og.png:", OUT, OUT.stat().st_size // 1024, "КБ")


if __name__ == "__main__":
    main()
