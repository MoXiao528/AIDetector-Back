import pytest
from pydantic import ValidationError

from app.core.config import Settings


def test_settings_reject_weak_secret_in_production():
    with pytest.raises(ValidationError):
        Settings(
            environment="production",
            secret_key="change-me",
            postgres_password="StrongDbPass!23",
            detect_service_url="https://detect.internal.example.com",
        )


def test_settings_reject_local_detect_service_in_production():
    with pytest.raises(ValidationError):
        Settings(
            environment="production",
            secret_key="a" * 32,
            postgres_password="StrongDbPass!23",
            detect_service_url="http://host.docker.internal:9000",
        )


def test_settings_reject_local_detect_service_detect_url_in_production():
    with pytest.raises(ValidationError):
        Settings(
            environment="production",
            secret_key="a" * 32,
            postgres_password="StrongDbPass!23",
            detect_service_url="https://detect.internal.example.com",
            detect_service_detect_url="http://localhost:9000/detect",
        )


def test_settings_accept_safe_production_values():
    settings = Settings(
        environment="production",
        secret_key="a" * 32,
        postgres_password="StrongDbPass!23",
        detect_service_url="https://detect.internal.example.com",
        detect_service_detect_url="https://umcat.cis.um.edu.mo/api/aidetect.php",
    )

    assert settings.environment == "production"
