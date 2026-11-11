"""Карточка прогноза: своя картинка вместо стокового фото.

Почему генерируем, а не берём сток: у бесплатных фото (Pexels, Pixabay)
лицензия позволяет коммерческое использование, но на кадрах матчей часто
видны клубные эмблемы и лица игроков — это уже чужие права. Своя карточка
этих рисков не имеет, выглядит одинаково во всех постах и содержит цифры.
"""

from __future__ import annotations

import logging
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

log = logging.getLogger(__name__)

WIDTH, HEIGHT = 1080, 1350
FONT_BOLD = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
FONT_REGULAR = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"

BG_TOP = (12, 32, 26)
BG_BOTTOM = (15, 23, 42)
GREEN = (16, 185, 129)
AMBER = (245, 158, 11)
TEXT = (237, 230, 218)
MUTED = (188, 172, 146)


def _font(size: int, bold: bool = True) -> ImageFont.FreeTypeFont:
    path = FONT_BOLD if bold else FONT_REGULAR
    try:
        return ImageFont.truetype(path, size)
    except OSError:
        return ImageFont.load_default()


def _fit_text(
    draw: ImageDraw.ImageDraw, text: str, max_width: int, start: int
) -> ImageFont.FreeTypeFont:
    size = start
    while size > 24:
        font = _font(size)
        if draw.textlength(text, font=font) <= max_width:
            return font
        size -= 4
    return _font(24)


def _gradient() -> Image.Image:
    image = Image.new("RGB", (WIDTH, HEIGHT), BG_BOTTOM)
    draw = ImageDraw.Draw(image, "RGBA")
    for y in range(HEIGHT):
        ratio = y / HEIGHT
        color = tuple(
            int(BG_TOP[index] + (BG_BOTTOM[index] - BG_TOP[index]) * ratio) for index in range(3)
        )
        draw.line([(0, y), (WIDTH, y)], fill=color)
    # Полосы поля: очень мягкие, чтобы не мешать тексту
    for x in range(0, WIDTH, 120):
        draw.rectangle([x, 0, x + 60, HEIGHT], fill=(255, 255, 255, 6))
    return image


def render_forecast_card(
    *,
    competition: str,
    home: str,
    away: str,
    kickoff: str,
    rows: list[tuple[str, float]],
    out_path: Path,
    footer: str = "@footballizator_bot",
) -> Path:
    """Нарисовать карточку прогноза и сохранить PNG."""
    image = _gradient()
    draw = ImageDraw.Draw(image, "RGBA")

    draw.text((60, 70), competition, font=_font(34, False), fill=MUTED)
    draw.text((60, 120), kickoff, font=_font(38), fill=TEXT)
    draw.line([(60, 190), (WIDTH - 60, 190)], fill=(16, 185, 129, 120), width=3)

    home_font = _fit_text(draw, home, WIDTH - 140, 88)
    draw.text((WIDTH // 2, 330), home, font=home_font, fill=TEXT, anchor="mm")
    draw.text((WIDTH // 2, 430), "vs", font=_font(44, False), fill=MUTED, anchor="mm")
    away_font = _fit_text(draw, away, WIDTH - 140, 88)
    draw.text((WIDTH // 2, 530), away, font=away_font, fill=TEXT, anchor="mm")

    top = 700
    for index, (label, probability) in enumerate(rows):
        y = top + index * 150
        draw.text((60, y), label, font=_font(38), fill=TEXT)
        percent = int(round(probability * 100))
        draw.text((WIDTH - 60, y), "%d%%" % percent, font=_font(44), fill=GREEN, anchor="ra")
        bar_y = y + 62
        draw.rounded_rectangle(
            [60, bar_y, WIDTH - 60, bar_y + 22], radius=11, fill=(255, 255, 255, 26)
        )
        share = max(0.02, min(probability, 1.0))
        fill_width = int((WIDTH - 120) * share)
        draw.rounded_rectangle([60, bar_y, 60 + fill_width, bar_y + 22], radius=11, fill=GREEN)

    draw.text((60, HEIGHT - 120), footer, font=_font(34, False), fill=MUTED)
    draw.text((WIDTH - 60, HEIGHT - 120), "18+", font=_font(34), fill=AMBER, anchor="ra")

    out_path.parent.mkdir(parents=True, exist_ok=True)
    image.save(out_path, "PNG", optimize=True)
    return out_path


def _wrap(
    draw: ImageDraw.ImageDraw, text: str, font: ImageFont.FreeTypeFont, width: int
) -> list[str]:
    words = text.split()
    lines: list[str] = []
    current = ""
    for word in words:
        candidate = (current + " " + word).strip()
        if draw.textlength(candidate, font=font) <= width:
            current = candidate
        else:
            if current:
                lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines


def render_text_card(
    *,
    title: str,
    label: str,
    out_path: Path,
    footer: str = "@footballizator_bot",
) -> Path:
    """Обложка для текстового поста: заголовок на футбольном фоне."""
    image = _gradient()
    draw = ImageDraw.Draw(image, "RGBA")

    draw.text((80, 120), label.upper(), font=_font(30, False), fill=GREEN)
    draw.line([(80, 190), (WIDTH - 80, 190)], fill=(16, 185, 129, 120), width=3)

    font = _font(76)
    lines = _wrap(draw, title, font, WIDTH - 160)
    while len(lines) > 5 and font.size > 44:
        font = _font(font.size - 6)
        lines = _wrap(draw, title, font, WIDTH - 160)

    line_height = int(font.size * 1.25)
    block_height = line_height * len(lines)
    y = (HEIGHT - block_height) // 2
    for line in lines:
        draw.text((80, y), line, font=font, fill=TEXT)
        y += line_height

    draw.text((80, HEIGHT - 120), footer, font=_font(34, False), fill=MUTED)
    draw.text((WIDTH - 80, HEIGHT - 120), "18+", font=_font(34), fill=AMBER, anchor="ra")

    out_path.parent.mkdir(parents=True, exist_ok=True)
    image.save(out_path, "PNG", optimize=True)
    return out_path
