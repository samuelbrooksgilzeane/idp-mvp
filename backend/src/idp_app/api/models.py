from typing import Literal

from pydantic import BaseModel

from idp_app.core.config import IdpMode


class ConfigurationPresence(BaseModel):
    catalog: bool
    project_schema: bool
    table_prefix: bool
    source_volume_name: bool
    artifacts_volume_name: bool
    warehouse_id: bool
    validation_endpoint: bool


class HealthResponse(BaseModel):
    status: Literal["ok"]
    mode: IdpMode
    application_name: str
    configuration: ConfigurationPresence
