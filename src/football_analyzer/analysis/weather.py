"""Погода на матч: геокодинг стадиона и прогноз Open-Meteo.

Почему так: у ESPN есть только город стадиона, координат нет. Open-Meteo даёт
и геокодинг, и прогноз — бесплатно и без ключа (подтверждено заказчиком).

Честное ограничение: коэффициенты влияния погоды — экспертные, а не
откалиброванные на данных. Их надо проверять бэктестом; если поправка
ухудшает качество, её значение должно остаться 1.0.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime

import aiohttp

log = logging.getLogger(__name__)

GEOCODE_API = "https://geocoding-api.open-meteo.com/v1/search"
FORECAST_API = "https://api.open-meteo.com/v1/forecast"
USER_AGENT = "football-analyzer/0.1"


@dataclass
class Weather:
    rain_mm: float
    wind_kmh: float
    temp_c: float
    note: str
    factor: float


async def geocode(
    session: aiohttp.ClientSession, city: str, country: str | None = None
) -> tuple[float, float] | None:
    """Координаты города. Кэшируется вызывающей стороной в таблице venues."""
    params = {"name": city, "count": "1", "language": "ru", "format": "json"}
    async with session.get(
        GEOCODE_API, params=params, headers={"User-Agent": USER_AGENT}
    ) as response:
        if response.status != 200:
            return None
        data = await response.json(content_type=None)
    results = data.get("results") or []
    if not results:
        return None
    best = results[0]
    try:
        return float(best["latitude"]), float(best["longitude"])
    except (KeyError, TypeError, ValueError):
        return None


async def forecast_at(
    session: aiohttp.ClientSession, lat: float, lon: float, when: datetime
) -> Weather | None:
    """Погода на час начала матча."""
    params = {
        "latitude": "%.4f" % lat,
        "longitude": "%.4f" % lon,
        "hourly": "precipitation,wind_speed_10m,temperature_2m",
        "timezone": "UTC",
        "forecast_days": "7",
    }
    async with session.get(
        FORECAST_API, params=params, headers={"User-Agent": USER_AGENT}
    ) as response:
        if response.status != 200:
            return None
        data = await response.json(content_type=None)

    hourly = data.get("hourly") or {}
    times = hourly.get("time") or []
    if not times:
        return None
    target = when.astimezone(UTC).strftime("%Y-%m-%dT%H:00")
    if target not in times:
        return None
    index = times.index(target)

    def value(name: str) -> float:
        series = hourly.get(name) or []
        if index < len(series) and series[index] is not None:
            return float(series[index])
        return 0.0

    rain = value("precipitation")
    wind = value("wind_speed_10m")
    temp = value("temperature_2m")

    factor = 1.0
    parts = []
    if rain >= 2.0:
        factor *= 0.96
        parts.append("дождь %.1f мм" % rain)
    elif rain > 0.1:
        factor *= 0.98
        parts.append("небольшой дождь %.1f мм" % rain)
    if wind >= 40:
        factor *= 0.97
        parts.append("сильный ветер %.0f км/ч" % wind)
    elif wind >= 28:
        factor *= 0.985
        parts.append("ветер %.0f км/ч" % wind)
    if temp >= 32:
        parts.append("жара %.0f°C" % temp)

    factor = min(max(factor, 0.92), 1.0)
    note = (
        ", ".join(parts)
        if parts
        else "без заметного влияния (%.0f°C, ветер %.0f км/ч)"
        % (
            temp,
            wind,
        )
    )
    return Weather(rain_mm=rain, wind_kmh=wind, temp_c=temp, note=note, factor=factor)
