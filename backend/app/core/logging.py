import logging
from typing import Optional


class SensitiveDataFilter(logging.Filter):
    """过滤可能包含敏感字段的日志。"""

    SENSITIVE_KEYS = {"password", "secret", "token", "api_key"}

    def filter(self, record: logging.LogRecord) -> bool:  # type: ignore[override]
        for key in list(record.__dict__.keys()):
            if key.lower() in self.SENSITIVE_KEYS and isinstance(record.__dict__[key], str):
                record.__dict__[key] = "***"
        return True


def configure_logging(level: int = logging.INFO, logger_name: Optional[str] = None) -> logging.Logger:
    logger = logging.getLogger(logger_name)
    if not logger.handlers:
        handler = logging.StreamHandler()
        formatter = logging.Formatter(
            fmt="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
        handler.setFormatter(formatter)
        handler.addFilter(SensitiveDataFilter())
        logger.addHandler(handler)
    logger.setLevel(level)
    return logger
