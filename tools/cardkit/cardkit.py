"""cardkit — карточки футбольных прогнозов без дизайнера.

Берёт матч и вероятности, возвращает PNG 1080×1350: команды, время,
полоски вероятностей, подпись канала. Без внешних картинок и стоковых фото —
всё рисуется кодом, поэтому лицензионных рисков нет.

Зависимости: pillow. Шрифты: DejaVu (Linux) или Arial (macOS/Windows).

Пример:
    python cardkit.py --home "Зенит" --away "Спартак" \
        --kickoff "23.11, 19:30 МСК" --out card.png \
        --line "Тотал 2,5:0.54" --line "Углы 9,5:0.62" --line "ЖК 4,5:0.71"
"""

from __future__ import annotations

import argparse
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

WIDTH, HEIGHT = 1080, 1350
FONT_CANDIDATES = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
    "/Library/Fonts/Arial Bold.ttf",
    "C:/Windows/Fonts/arialbd.ttf",
]
BG_TOP, BG_BOTTOM = (12, 32, 26), (15, 23, 42)
GREEN, AMBER = (16, 185, 129), (245, 158, 11)
TEXT, MUTED = (237, 230, 218), (188, 172, 146)


def font(size: int, bold: bool = True) -> ImageFont.FreeTypeFont:
    for path in FONT_CANDIDATES:
        if "Bold" in path or "bd" in path or bold:
            try:
                return ImageFont.truetype(path, size)
            except OSError:
                continue
    return ImageFont.load_default()


def fit(draw: ImageDraw.ImageDraw, text: str, width: int, start: int) -> ImageFont.FreeTypeFont:
    size = start
    while size > 24:
        candidate = font(size)
        if draw.textlength(text, font=candidate) <= width:
            return candidate
        size -= 4
    return font(24)


def background() -> Image.Image:
    image = Image.new("RGB", (WIDTH, HEIGHT), BG_BOTTOM)
    draw = ImageDraw.Draw(image, "RGBA")
    for y in range(HEIGHT):
        ratio = y / HEIGHT
        color = tuple(int(BG_TOP[i] + (BG_BOTTOM[i] - BG_TOP[i]) * ratio) for i in range(3))
        draw.line([(0, y), (WIDTH, y)], fill=color)
    for x in range(0, WIDTH, 120):
        draw.rectangle([x, 0, x + 60, HEIGHT], fill=(255, 255, 255, 6))
    return image


def render_card(
    *,
    home: str,
    away: str,
    kickoff: str,
    lines: list[tuple[str, float]],
    out_path: Path,
    competition: str = "",
    footer: str = "@footballizator_bot",
) -> Path:
    image = background()
    draw = ImageDraw.Draw(image, "RGBA")

    if competition:
        draw.text((60, 70), competition, font=font(34, False), fill=MUTED)
    draw.text((60, 120), kickoff, font=font(38), fill=TEXT)
    draw.line([(60, 190), (WIDTH - 60, 190)], fill=(16, 185, 129, 120), width=3)

    draw.text((WIDTH // 2, 330), home, font=fit(draw, home, WIDTH - 140, 88), fill=TEXT, anchor="mm")
    draw.text((WIDTH // 2, 430), "vs", font=font(44, False), fill=MUTED, anchor="mm")
    draw.text((WIDTH // 2, 530), away, font=fit(draw, away, WIDTH - 140, 88), fill=TEXT, anchor="mm")

    top = 700
    for index, (label, probability) in enumerate(lines[:4]):
        y = top + index * 150
        draw.text((60, y), label, font=font(38), fill=TEXT)
        percent = int(round(probability * 100))
        draw.text((WIDTH - 60, y), "%d%%" % percent, font=font(44), fill=GREEN, anchor="ra")
        bar_y = y + 62
        draw.rounded_rectangle([60, bar_y, WIDTH - 60, bar_y + 22], radius=11, fill=(255, 255, 255, 26))
        share = max(0.02, min(probability, 1.0))
        draw.rounded_rectangle(
            [60, bar_y, 60 + int((WIDTH - 120) * share), bar_y + 22], radius=11, fill=GREEN
        )

    draw.text((60, HEIGHT - 120), footer, font=font(34, False), fill=MUTED)
    draw.text((WIDTH - 60, HEIGHT - 120), "18+", font=font(34), fill=AMBER, anchor="ra")

    out_path.parent.mkdir(parents=True, exist_ok=True)
    image.save(out_path, "PNG", optimize=True)
    return out_path


def parse_line(value: str) -> tuple[str, float]:
    label, _, raw = value.rpartition(":")
    return label.strip() or "Линия", float(raw)


def main() -> None:
    parser = argparse.ArgumentParser(description="Карточка прогноза в PNG")
    parser.add_argument("--home", required=True)
    parser.add_argument("--away", required=True)
    parser.add_argument("--kickoff", required=True)
    parser.add_argument("--competition", default="")
    parser.add_argument("--line", action="append", default=[], help="подпись:вероятность, например 'Тотал 2,5:0.54'")
    parser.add_argument("--out", default="card.png")
    parser.add_argument("--footer", default="@footballizator_bot")
    args = parser.parse_args()

    path = render_card(
        home=args.home,
        away=args.away,
        kickoff=args.kickoff,
        lines=[parse_line(item) for item in args.line],
        out_path=Path(args.out),
        competition=args.competition,
        footer=args.footer,
    )
    print("готово:", path)


if __name__ == "__main__":
    main()
