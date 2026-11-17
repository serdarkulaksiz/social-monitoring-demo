"""Standard-library logging configuration with secret masking."""

from __future__ import annotations

import logging
import sys


class SecretMaskingFilter(logging.Filter):
    """Replaces known secret values in log messages with '***'."""

    def __init__(self, secrets: list[str]) -> None:
        super().__init__()
        self._secrets = [secret for secret in secrets if secret]

    def filter(self, record: logging.LogRecord) -> bool:
        if not self._secrets:
            return True
        message = record.getMessage()
        masked = message
        for secret in self._secrets:
            masked = masked.replace(secret, "***")
        if masked != message:
            record.msg, record.args = masked, None
        return True


def configure_logging(level: str, secrets: list[str]) -> None:
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    handler.addFilter(SecretMaskingFilter(secrets))
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level)
    # Request logs could contain query strings; keep them at warning level by default.
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
