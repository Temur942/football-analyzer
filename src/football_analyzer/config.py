"""Типизированные настройки приложения.

Правила проекта:
- секреты только в .env.local / переменных окружения, в коде их нет;
- при старте на проде пустой BOT_TOKEN — это ошибка, а не «ну ладно».
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(PROJECT_ROOT / ".env.local", PROJECT_ROOT / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_env: str = Field(default="local", description="local | stage | prod")

    bot_token: str = Field(default="", description="токен Telegram-бота")
    bot_admin_ids: str = Field(default="", description="ID админов через запятую")

    # PostgreSQL на сервере (решение заказчика). Секрет: только .env.local или
    # EnvironmentFile systemd, в git не попадает.
    database_url: str = Field(default="")

    forecast_lead_hours: int = Field(default=5, ge=1, le=72)
    scheduler_tick_seconds: int = Field(default=60, ge=10, le=3600)

    weather_api_url: str = Field(default="https://api.open-meteo.com/v1/forecast")
    weather_api_key: str = Field(default="")
    http_timeout_seconds: int = Field(default=15, ge=1, le=120)

    log_level: str = Field(default="INFO")

    cost_prices: str = Field(default="config/prices.json")
    cost_ledger: str = Field(default="var/cost-ledger.sqlite3")
    budget_max_per_run_rub: float = Field(default=0.0, ge=0)
    budget_max_per_day_rub: float = Field(default=0.0, ge=0)

    @field_validator("app_env")
    @classmethod
    def _known_env(cls, value: str) -> str:
        allowed = {"local", "stage", "prod"}
        if value not in allowed:
            raise ValueError("app_env должен быть одним из %s" % sorted(allowed))
        return value

    @property
    def is_prod(self) -> bool:
        return self.app_env == "prod"

    @property
    def admin_ids(self) -> list[int]:
        result: list[int] = []
        for chunk in self.bot_admin_ids.replace(";", ",").split(","):
            chunk = chunk.strip()
            if chunk.isdigit():
                result.append(int(chunk))
        return result

    def resolve(self, relative: str) -> Path:
        """Путь из настроек → абсолютный путь (относительные считаются от корня)."""
        path = Path(relative)
        return path if path.is_absolute() else PROJECT_ROOT / path

    def require_database_url(self) -> str:
        if not self.database_url:
            raise RuntimeError(
                "DATABASE_URL не задан: без базы расписание некуда писать. "
                "Заполните .env.local (см. .env.example) или EnvironmentFile systemd."
            )
        return self.database_url

    def require_bot_token(self) -> str:
        if not self.bot_token:
            raise RuntimeError(
                "BOT_TOKEN не задан. Для %s это ошибка запуска: "
                "заполните .env.local (см. .env.example)." % self.app_env
            )
        return self.bot_token


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
