import pytest
from pydantic import ValidationError

from app.core.config import Settings

SERVICE_TOKEN = "test-repre-guard-service-token-1234567890"


def test_settings_reject_weak_secret_in_production():
    with pytest.raises(ValidationError):
        Settings(
            environment="production",
            secret_key="change-me",
            postgres_password="StrongDbPass!23",
            detect_service_url="https://detect.internal.example.com",
            repre_guard_service_token=SERVICE_TOKEN,
        )


def test_settings_reject_local_detect_service_in_production():
    with pytest.raises(ValidationError):
        Settings(
            environment="production",
            secret_key="a" * 32,
            postgres_password="StrongDbPass!23",
            detect_service_url="http://host.docker.internal:9000",
            repre_guard_service_token=SERVICE_TOKEN,
        )


def test_settings_reject_local_detect_service_detect_url_in_production():
    with pytest.raises(ValidationError):
        Settings(
            environment="production",
            secret_key="a" * 32,
            postgres_password="StrongDbPass!23",
            detect_service_url="https://detect.internal.example.com",
            detect_service_detect_url="http://localhost:9000/detect",
            repre_guard_service_token=SERVICE_TOKEN,
        )


@pytest.mark.parametrize(
    "detect_url",
    [
        "http://detect.internal.example.com",
        "ftp://detect.internal.example.com",
    ],
)
def test_settings_reject_remote_non_https_detect_service_in_production(detect_url):
    with pytest.raises(ValidationError):
        Settings(
            environment="production",
            secret_key="a" * 32,
            postgres_password="StrongDbPass!23",
            detect_service_url=detect_url,
            repre_guard_service_token=SERVICE_TOKEN,
        )


def test_settings_reject_cross_origin_health_service_in_production():
    with pytest.raises(ValidationError):
        Settings(
            environment="production",
            secret_key="a" * 32,
            postgres_password="StrongDbPass!23",
            detect_service_url="https://detect.internal.example.com",
            detect_service_health_url="https://health.internal.example.com/health",
            repre_guard_service_token=SERVICE_TOKEN,
        )


@pytest.mark.parametrize(
    "detect_url",
    [
        "https://umcat.cis.um.edu.mo/api/aidetect.php",
        "https://umcat.cis.um.edu.mo/api/aidetect.php/",
    ],
)
def test_settings_reject_legacy_php_detect_service_in_production(detect_url):
    with pytest.raises(ValidationError):
        Settings(
            environment="production",
            secret_key="a" * 32,
            postgres_password="StrongDbPass!23",
            detect_service_detect_url=detect_url,
            repre_guard_service_token=SERVICE_TOKEN,
        )


@pytest.mark.parametrize("service_token", ["", "too-short", "令牌" * 16])
def test_settings_reject_missing_short_or_non_ascii_service_token(service_token):
    with pytest.raises(ValidationError):
        Settings(repre_guard_service_token=service_token)


def test_settings_accept_local_http_detect_service_in_development():
    settings = Settings(
        environment="development",
        detect_service_url="http://host.docker.internal:9000",
        repre_guard_service_token=SERVICE_TOKEN,
    )

    assert settings.detect_service_url == "http://host.docker.internal:9000"


def test_settings_accept_legacy_php_detect_service_only_in_development():
    settings = Settings(
        environment="development",
        detect_service_detect_url="https://umcat.cis.um.edu.mo/api/aidetect.php",
        repre_guard_service_token=SERVICE_TOKEN,
    )

    assert settings.detect_service_detect_url == "https://umcat.cis.um.edu.mo/api/aidetect.php"


def test_settings_masks_service_token_in_repr():
    settings = Settings(repre_guard_service_token=SERVICE_TOKEN)

    assert SERVICE_TOKEN not in repr(settings)


def test_settings_does_not_echo_rejected_service_token():
    rejected_token = "sensitive-invalid-service-token-value-令牌"

    with pytest.raises(ValidationError) as exc_info:
        Settings(repre_guard_service_token=rejected_token)

    assert rejected_token not in str(exc_info.value)


def test_settings_accept_safe_production_values():
    settings = Settings(
        environment="production",
        secret_key="a" * 32,
        postgres_password="StrongDbPass!23",
        detect_service_url="https://detect.internal.example.com",
        detect_service_detect_url="https://detect.internal.example.com/detect",
        detect_service_health_url="https://detect.internal.example.com/health",
        repre_guard_service_token=SERVICE_TOKEN,
    )

    assert settings.environment == "production"


def test_evidence_settings_default_to_off():
    settings = Settings(_env_file=None, repre_guard_service_token=SERVICE_TOKEN)

    assert settings.detect_evidence_mode == "off"
    assert settings.detect_evidence_bundle_path == ""
    assert settings.detect_evidence_bundle_sha256 == ""
    assert settings.detect_evidence_timeout_seconds == "12"


def test_invalid_optional_evidence_config_does_not_block_settings():
    settings = Settings(
        _env_file=None,
        repre_guard_service_token=SERVICE_TOKEN,
        detect_evidence_mode="invalid-mode",
        detect_evidence_bundle_path="missing.bundle",
        detect_evidence_bundle_sha256="invalid-sha",
        detect_evidence_timeout_seconds="invalid-timeout",
    )

    assert settings.detect_evidence_mode == "invalid-mode"
    assert settings.detect_evidence_bundle_sha256 == "invalid-sha"
    assert settings.detect_evidence_timeout_seconds == "invalid-timeout"
