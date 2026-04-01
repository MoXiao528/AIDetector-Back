from functools import lru_cache
from typing import List
from urllib.parse import urlparse

from pydantic import AnyHttpUrl, Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


LOCAL_DETECT_HOSTS = {"localhost", "127.0.0.1", "host.docker.internal"}
WEAK_DB_PASSWORDS = {"postgres", "123456", "password", "changeme"}
DEVELOPMENT_ENVIRONMENTS = {"development", "dev", "local", "test"}


class Settings(BaseSettings):
    detect_service_url: str = "http://host.docker.internal:9000"
    detect_service_detect_url: str | None = None
    detect_service_health_url: str | None = None
    detect_service_timeout: int = 10
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", case_sensitive=False)

    app_name: str = Field(default="AIDetector API")
    environment: str = Field(default="development")
    secret_key: str = Field(default="change-me", min_length=8)

    backend_cors_origins: List[AnyHttpUrl] | List[str] = Field(
        default_factory=lambda: [
            "http://localhost:3000",
            "http://localhost:5173",
            "http://127.0.0.1:3000",
            "http://127.0.0.1:5173",
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

        detect_urls = [
            ("DETECT_SERVICE_URL", self.detect_service_url),
            ("DETECT_SERVICE_DETECT_URL", self.detect_service_detect_url),
            ("DETECT_SERVICE_HEALTH_URL", self.detect_service_health_url),
        ]
        for field_name, url in detect_urls:
            if not url:
                continue
            detect_host = (urlparse(url).hostname or "").strip().lower()
            if detect_host in LOCAL_DETECT_HOSTS:
                raise ValueError(f"Production-like environments cannot use a local {field_name}.")

        postgres_password = str(self.postgres_password or "").strip().lower()
        if postgres_password in WEAK_DB_PASSWORDS:
            raise ValueError("Production-like environments require a non-default POSTGRES_PASSWORD.")

        return self


@lru_cache

def get_settings() -> Settings:
    return Settings()
