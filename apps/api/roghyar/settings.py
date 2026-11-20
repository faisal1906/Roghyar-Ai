from typing import Literal

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

LOCAL_PASSWORD = "local-development-only"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="ROGHYAR_", env_file=".env", extra="ignore", hide_input_in_errors=True
    )

    environment: Literal["local", "test", "production"] = "local"
    db_host: str = Field(default="127.0.0.1", min_length=1, max_length=253)
    db_port: int = Field(default=5432, ge=1, le=65535)
    db_name: str = Field(default="roghyar", min_length=1, max_length=63)
    db_user: str = Field(default="roghyar", min_length=1, max_length=63)
    db_password: SecretStr = SecretStr(LOCAL_PASSWORD)
    session_signing_key: SecretStr | None = None
    field_encryption_key: SecretStr | None = None
    phone_lookup_key: SecretStr | None = None
    synthetic_fixtures: bool = False
    clinical_guidance_enabled: bool = False
    appointment_booking_enabled: bool = False

    @model_validator(mode="after")
    def check_environment(self) -> "Settings":
        password = self.db_password.get_secret_value()
        if not password:
            raise ValueError("A database password is required")
        if self.clinical_guidance_enabled or self.appointment_booking_enabled:
            raise ValueError("Clinical guidance and booking require implementation and approval")
        if self.environment == "production":
            if self.synthetic_fixtures:
                raise ValueError("Synthetic fixtures are forbidden in production")
            if password == LOCAL_PASSWORD:
                raise ValueError("Production requires a dedicated database password")
        return self
