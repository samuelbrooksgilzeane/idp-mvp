import re
from enum import Enum
from pathlib import Path
from typing import Any, Self

from pydantic import PositiveInt, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

SIMPLE_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,127}$")


class IdpMode(str, Enum):
    MOCK = "mock"
    DATABRICKS = "databricks"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="IDP_",
        extra="ignore",
    )

    mode: IdpMode = IdpMode.MOCK
    catalog: str | None = None
    project_schema: str | None = None
    table_prefix: str | None = None
    source_volume_name: str | None = None
    artifacts_volume_name: str | None = None
    warehouse_id: str | None = None
    parse_job_id: PositiveInt | None = None
    validation_endpoint: str | None = None
    app_name: str = "IDP MVP"
    local_data_dir: Path = Path(".local/idp")
    max_upload_bytes: PositiveInt = 25 * 1024 * 1024
    max_upload_files: PositiveInt = 10

    @field_validator(
        "catalog",
        "project_schema",
        "table_prefix",
        "source_volume_name",
        "artifacts_volume_name",
        mode="before",
    )
    @classmethod
    def validate_identifier(cls, value: Any) -> Any:
        if value is None or value == "":
            return None
        if not isinstance(value, str) or SIMPLE_IDENTIFIER.fullmatch(value) is None:
            raise ValueError(
                "must be a single identifier using only ASCII letters, numbers, and underscores"
            )
        return value

    @field_validator("warehouse_id", "validation_endpoint", mode="before")
    @classmethod
    def normalize_optional_text(cls, value: Any) -> Any:
        if isinstance(value, str):
            value = value.strip()
            if value == "":
                return None
        return value

    @model_validator(mode="after")
    def require_databricks_configuration(self) -> Self:
        if self.mode is not IdpMode.DATABRICKS:
            return self

        required = {
            "IDP_CATALOG": self.catalog,
            "IDP_PROJECT_SCHEMA": self.project_schema,
            "IDP_TABLE_PREFIX": self.table_prefix,
            "IDP_SOURCE_VOLUME_NAME": self.source_volume_name,
            "IDP_ARTIFACTS_VOLUME_NAME": self.artifacts_volume_name,
            "IDP_WAREHOUSE_ID": self.warehouse_id,
            "IDP_PARSE_JOB_ID": self.parse_job_id,
            "IDP_VALIDATION_ENDPOINT": self.validation_endpoint,
        }
        missing = [name for name, value in required.items() if not value]
        if missing:
            raise ValueError(
                "IDP_MODE=databricks requires configuration: " + ", ".join(missing)
            )
        return self
