"""Бренд: фавикон и картинка для превью ссылок.

Мяч из логотипа убрали — оставляем строгий знак «F» на тёмном фоне.
Шрифты подбираем из системных: на сервере DejaVu, на macOS Arial/Helvetica.
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "docs" / "landing"

FONTS = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
    "/System/Library/Fonts/Helvetica.ttc",
    "/Library/Fonts/Arial Bold.ttf",
]

BG = (17, 12, 12)
ACCENT_TOP = (229, 50, 15)
ACCENT_BOTTOM = (233, 115, 24)
TEXT = (243, 243, 243)


def font(size: int) -> ImageFont.FreeTypeFont:
    for path in FONTS:
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            continue
    return ImageFont.load_default()


def rounded_icon(size: int) -> Image.Image:
    image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle([0, 0, size - 1, size - 1], radius=int(size * 0.22), fill=BG)

    # Лёгкий градиентный акцент в верхней части
    for y in range(int(size * 0.55)):
        ratio = y / max(1, int(size * 0.55))
        color = tuple(
            int(ACCENT_TOP[i] + (ACCENT_BOTTOM[i] - ACCENT_TOP[i]) * ratio) for i in range(3)
        )
        draw.line(
            [(int(size * 0.18), y + int(size * 0.12)), (int(size * 0.82), y + int(size * 0.12))],
            fill=color + (26,),
            width=1,
        )

    letter = font(int(size * 0.62))
    text = "F"
    box = draw.textbbox((0, 0), text, font=letter)
    width = box[2] - box[0]
    height = box[3] - box[1]
    draw.text(
        ((size - width) / 2 - box[0], (size - height) / 2 - box[1]),
        text,
        font=letter,
        fill=TEXT,
    )
    return image


def make_og() -> None:
    width, height = 1200, 630
    image = Image.new("RGB", (width, height), (15, 23, 42))
    draw = ImageDraw.Draw(image, "RGBA")
    for y in range(height):
        ratio = y / height
        color = (
            int(12 + (15 - 12) * ratio),
            int(32 + (23 - 32) * ratio),
            int(26 + (42 - 26) * ratio),
        )
        draw.line([(0, y), (width, y)], fill=color)
    for x in range(0, width, 140):
        draw.rectangle([x, 0, x + 70, height], fill=(255, 255, 255, 6))

    mark = rounded_icon(96)
    image.paste(mark, (70, 64), mark)
    draw.text((186, 82), "FOOTBALLINHO", font=font(44), fill=TEXT)
    draw.line([(70, 190), (width - 70, 190)], fill=(16, 185, 129, 140), width=3)

    draw.text((70, 240), "Вероятности", font=font(74), fill=TEXT)
    draw.text((70, 330), "футбольных матчей", font=font(74), fill=TEXT)
    draw.text(
        (70, 450),
        "Тоталы · исходы · счёт · углы · карточки",
        font=font(36),
        fill=(16, 185, 129),
    )
    draw.text(
        (70, 505),
        "За 5 часов до матча — в Telegram",
        font=font(30),
        fill=(188, 172, 146),
    )
    draw.text((70, height - 66), "footballinho.ru", font=font(28), fill=TEXT)
    draw.text((width - 70, height - 66), "18+", font=font(28), fill=(245, 158, 11), anchor="ra")

    image.save(OUT / "og.png", "PNG", optimize=True)
    print("og.png обновлён")


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)

    icon = rounded_icon(256)
    icon.save(
        OUT / "favicon.ico", sizes=[(16, 16), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]
    )
    rounded_icon(180).save(OUT / "apple-touch-icon.png", "PNG")
    rounded_icon(192).save(OUT / "favicon-192.png", "PNG")
    rounded_icon(32).save(OUT / "favicon-32.png", "PNG")
    make_og()
    print("фавикон: favicon.ico, apple-touch-icon.png, favicon-192.png, favicon-32.png")


if __name__ == "__main__":
    main()
