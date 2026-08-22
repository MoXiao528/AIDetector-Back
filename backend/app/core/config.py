from functools import lru_cache
from typing import List
from urllib.parse import urlparse

from pydantic import AnyHttpUrl, Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


LOCAL_DETECT_HOSTS = {"localhost", "127.0.0.1", "::1", "host.docker.internal"}
WEAK_DB_PASSWORDS = {"postgres", "123456", "password", "changeme"}
DEVELOPMENT_ENVIRONMENTS = {"development", "dev", "local", "test"}


class Settings(BaseSettings):
    detect_service_url: str = "http://127.0.0.1:9000"
    detect_service_detect_url: str | None = None
    detect_service_health_url: str | None = None
    detect_service_timeout: int = 60
    repre_guard_service_token: SecretStr
    detect_segment_concurrency: int = Field(default=4, ge=1, le=16)
    detect_request_timeout: int = Field(default=120, ge=1, le=900)
    detect_tokenizer_model: str = "WUJUNCHAO/DetectRL-X-XLM-RoBERTa-Detector-All"
    detect_max_input_tokens: int = Field(default=512, ge=16, le=4096)
    detect_short_segment_visible_chars: int = Field(default=40, ge=1, le=200)
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        hide_input_in_errors=True,
    )

    app_name: str = Field(default="AIDetector API")
    environment: str = Field(default="development")
    api_host_bind: str = Field(default="127.0.0.1")
    api_host_port: int = Field(default=8020)
    secret_key: str = Field(default="change-me", min_length=8)

    backend_cors_origins: List[AnyHttpUrl] | List[str] = Field(
        default_factory=lambda: [
            "http://localhost:3000",
            "http://localhost:5173",
            "http://localhost:5179",
            "http://localhost:5300",
            "http://127.0.0.1:3000",
            "http://127.0.0.1:5173",
            "http://127.0.0.1:5179",
            "http://127.0.0.1:5300",
        ]
    )

    postgres_host: str = Field(default="db")
    postgres_port: int = Field(default=5432)
    postgres_user: str = Field(default="postgres")
    postgres_password: str = Field(default="postgres")
    postgres_db: str = Field(default="aidetector")
    access_token_expire_minutes: int = Field(default=60, ge=1, le=1440)

    @field_validator("backend_cors_origins", mode="before")
    @classmethod
    def split_cors_origins(cls, value: List[str] | str) -> List[str]:
        if isinstance(value, str):
            return [origin.strip() for origin in value.split(",") if origin.strip()]
        return value

    @field_validator("detect_service_url", "detect_service_detect_url", "detect_service_health_url", mode="before")
    @classmethod
    def normalize_detect_urls(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = str(value).strip()
        return normalized or None

    @field_validator("repre_guard_service_token", mode="before")
    @classmethod
    def validate_repre_guard_service_token(cls, value: SecretStr | str | None) -> str:
        token = value.get_secret_value() if isinstance(value, SecretStr) else str(value or "")
        token = token.strip()
        if len(token) < 32 or any(not 0x21 <= ord(char) <= 0x7E for char in token):
            raise ValueError("REPRE_GUARD_SERVICE_TOKEN must contain at least 32 printable ASCII characters.")
        return token

    @property
    def database_url(self) -> str:
        return (
            f"postgresql+psycopg2://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}?client_encoding=utf8"
        )

    @model_validator(mode="after")
    def validate_production_safety(self) -> "Settings":
        environment = str(self.environment or "").strip().lower()
        if environment in DEVELOPMENT_ENVIRONMENTS:
            return self

        secret_key = str(self.secret_key or "").strip()
        if len(secret_key) < 32 or secret_key.lower() == "change-me":
            raise ValueError("Production-like environments require a strong SECRET_KEY with at least 32 characters.")

        base_url = self.detect_service_url.rstrip("/")
        base_path = (urlparse(base_url).path or "").rstrip("/").lower()
        base_is_direct = base_path.endswith("/detect") or base_path.endswith(".php")
        detect_url = self.detect_service_detect_url or (base_url if base_is_direct else f"{base_url}/detect")
        health_url = self.detect_service_health_url
        if not health_url and not self.detect_service_detect_url and not base_is_direct:
            health_url = f"{base_url}/health"

        parsed_detect_url = urlparse(detect_url)
        if parsed_detect_url.path.rstrip("/").lower().endswith(".php"):
            raise ValueError("Production-like environments cannot use a legacy PHP detect endpoint.")

        parsed_urls = [("detect", parsed_detect_url)]
        if health_url:
            parsed_urls.append(("health", urlparse(health_url)))
        for endpoint_name, parsed_url in parsed_urls:
            detect_host = (parsed_url.hostname or "").strip().lower()
            if parsed_url.scheme.lower() != "https" or not detect_host:
                raise ValueError(f"Production-like {endpoint_name} endpoint must use HTTPS.")
            if detect_host in LOCAL_DETECT_HOSTS:
                raise ValueError(f"Production-like environments cannot use a local {endpoint_name} endpoint.")

        if len(parsed_urls) == 2:
            detect_origin = (parsed_detect_url.scheme.lower(), parsed_detect_url.hostname, parsed_detect_url.port or 443)
            parsed_health_url = parsed_urls[1][1]
            health_origin = (parsed_health_url.scheme.lower(), parsed_health_url.hostname, parsed_health_url.port or 443)
            if detect_origin != health_origin:
                raise ValueError("Production-like detect and health endpoints must use the same origin.")

        postgres_password = str(self.postgres_password or "").strip().lower()
        if postgres_password in WEAK_DB_PASSWORDS:
            raise ValueError("Production-like environments require a non-default POSTGRES_PASSWORD.")

        return self


@lru_cache

def get_settings() -> Settings:
    return Settings()
