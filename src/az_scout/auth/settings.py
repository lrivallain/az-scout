"""Auth settings loaded from environment variables."""

import logging
from enum import StrEnum
from typing import Literal

from pydantic import model_validator
from pydantic_settings import BaseSettings

logger = logging.getLogger(__name__)


class AuthMode(StrEnum):
    """Supported authentication modes."""

    entra = "entra"
    mock = "mock"


class AuthSettings(BaseSettings):
    """Configuration for az-scout authentication.

    Values are read from environment variables (case-insensitive) and
    optionally from a ``.env`` file in the working directory.
    """

    auth_mode: Literal["entra", "mock"] = "entra"

    auth_tenant_id: str = ""
    auth_client_id: str = ""
    auth_client_secret: str = ""
    auth_api_scope: str = ""

    host: str = "0.0.0.0"
    port: int = 8000

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8", "extra": "ignore"}

    @model_validator(mode="after")
    def _validate_entra_vars(self) -> "AuthSettings":
        if self.auth_mode == "entra":
            missing: list[str] = []
            if not self.auth_tenant_id:
                missing.append("AUTH_TENANT_ID")
            if not self.auth_client_id:
                missing.append("AUTH_CLIENT_ID")
            if missing:
                raise ValueError(
                    f"AUTH_MODE=entra requires {', '.join(missing)} to be set. "
                    "Set AUTH_MODE=mock for local development without Entra ID."
                )
        return self


settings = AuthSettings()
