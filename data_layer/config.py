"""Централизованная конфигурация сервиса data_layer.

Все переменные окружения читаются здесь. Остальной код
обращается к Settings, а не к os.getenv напрямую.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field


@dataclass
class Settings:
    """Конфигурация сервиса, собранная из переменных окружения."""

    # Сервис
    parser_token: str = "dev-token-change-me-in-production"
    parser_port: int = 8080

    # Режим данных:
    #   synthetic      — только синтетические фикстуры
    #   sandbox        — LiteAPI; режим ответа определяет sandbox/live
    #   live_transport — совместимый alias режима с обоими реальными API
    #   live           — LiteAPI; без синтетического fallback
    data_mode: str = "live"

    # Ключи LiteAPI (жильё) и Яндекс Расписаний (дорога)
    yandex_rasp_api_key: str | None = None
    liteapi_key: str | None = None
    ostrovok_api_key: str | None = None
    guest_nationality: str = "RU"

    # HTTP-настройки для внешних адаптеров
    http_timeout_seconds: float = 10.0
    http_max_retries: int = 3
    http_retry_backoff_seconds: float = 1.0

    def __post_init__(self):
        if self.data_mode not in {"synthetic", "sandbox", "live", "live_transport"}:
            raise ValueError("Unsupported DATA_MODE")

    @classmethod
    def from_env(cls) -> "Settings":
        """Создаёт Settings из переменных окружения."""
        settings = cls(
            parser_token=os.getenv(
                "PARSER_SERVICE_TOKEN",
                "dev-token-change-me-in-production",
            ),
            parser_port=int(os.getenv("PARSER_PORT", "8080")),
            data_mode=os.getenv("DATA_MODE", "live").strip().lower(),
            yandex_rasp_api_key=os.getenv("YANDEX_RASP_API_KEY") or None,
            liteapi_key=os.getenv("LITEAPI_KEY") or None,
            ostrovok_api_key=os.getenv("OSTROVOK_API_KEY") or None,
            guest_nationality=os.getenv("GUEST_NATIONALITY", "RU"),
            http_timeout_seconds=float(
                os.getenv("HTTP_TIMEOUT_SECONDS", "10.0")
            ),
            http_max_retries=int(os.getenv("HTTP_MAX_RETRIES", "3")),
            http_retry_backoff_seconds=float(
                os.getenv("HTTP_RETRY_BACKOFF_SECONDS", "1.0")
            ),
        )
        settings.validate_runtime()
        return settings

    def validate_runtime(self) -> None:
        """Refuse an unconfigured external service instead of silently losing providers."""
        if self.is_synthetic_only:
            return
        required = {
            "LITEAPI_KEY": self.liteapi_key,
            "YANDEX_RASP_API_KEY": self.yandex_rasp_api_key,
        }
        missing = [name for name, value in required.items() if not value or not value.strip()]
        if missing:
            raise ValueError("Не заполнены настройки внешних источников: " + ", ".join(missing))

    @property
    def is_synthetic_only(self) -> bool:
        """True, если используется только синтетика."""
        return self.data_mode == "synthetic"

    @property
    def allow_real_transport(self) -> bool:
        """True, если разрешены реальные транспортные источники."""
        return self.data_mode in ("live", "sandbox", "live_transport")

    @property
    def allow_real_hotels(self) -> bool:
        """True, если разрешены реальные отельные источники."""
        return self.data_mode in ("live", "sandbox", "live_transport")
