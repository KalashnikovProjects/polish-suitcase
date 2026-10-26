from typing import Literal
from zoneinfo import ZoneInfo

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", case_sensitive=False)

    agent_mode: Literal["demo", "openai"] = "demo"
    openai_api_key: SecretStr = SecretStr("")
    openai_model: str = "gpt-6-luna"
    openai_reasoning_effort: str = "low"
    backend_url: str = "http://localhost:8000"
    agent_service_token: SecretStr = SecretStr("")
    parser_url: str = "http://localhost:8001"
    parser_service_token: SecretStr = SecretStr("")
    rabbitmq_url: SecretStr = SecretStr("")
    agent_queue: str = "agent.tasks"
    sse_silence_seconds: float = Field(default=45, gt=0)
    http_timeout_seconds: float = Field(default=20, gt=0)
    llm_timeout_seconds: float = Field(default=60, gt=0)
    sync_timeout_seconds: float = Field(default=20, gt=0)
    max_llm_calls: int = Field(default=6, ge=1)
    max_search_calls: int = Field(default=12, ge=1)
    max_delivery_attempts: int = Field(default=3, ge=1)
    agent_max_concurrent_chats: int = Field(default=32, ge=1, le=256)
    retry_backoff_seconds: float = Field(default=2, ge=0)
    timezone: str = "Europe/Moscow"

    @model_validator(mode="after")
    def valid_timezone(self):
        ZoneInfo(self.timezone)
        return self

    def validate_runtime(self, *, worker: bool) -> None:
        required = []
        if self.agent_mode == "openai":
            required += ["openai_api_key", "parser_service_token"]
        if worker:
            required += ["rabbitmq_url", "agent_service_token"]
        missing = [key.upper() for key in required if not getattr(self, key).get_secret_value()]
        if missing:
            raise ValueError("Не заполнены настройки: " + ", ".join(missing))
