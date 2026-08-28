import pytest
from pydantic import ValidationError

from idp_app.core.config import IdpMode, Settings


def test_valid_databricks_configuration_is_accepted() -> None:
    settings = Settings(
        _env_file=None,
        mode=IdpMode.DATABRICKS,
        catalog="shared_catalog",
        project_schema="idp_project",
        table_prefix="idp_dev",
        source_volume_name="source_documents",
        artifacts_volume_name="idp_artifacts",
        warehouse_id="abc123",
        validation_endpoint="idp-validation-endpoint",
    )

    assert settings.table_prefix == "idp_dev"


@pytest.mark.parametrize(
    "invalid_identifier",
    [
        "catalog.schema",
        "quoted'name",
        'quoted"name',
        "path/name",
        "has whitespace",
        "table;DROP_TABLE",
        "name--comment",
    ],
)
def test_invalid_simple_identifiers_are_rejected(invalid_identifier: str) -> None:
    with pytest.raises(ValidationError, match="must be a single identifier"):
        Settings(_env_file=None, catalog=invalid_identifier)


def test_mock_mode_needs_no_databricks_configuration() -> None:
    settings = Settings(_env_file=None)

    assert settings.mode is IdpMode.MOCK
    assert settings.catalog is None


def test_databricks_mode_reports_all_missing_configuration() -> None:
    with pytest.raises(ValidationError) as error:
        Settings(_env_file=None, mode=IdpMode.DATABRICKS)

    message = str(error.value)
    assert "IDP_MODE=databricks requires configuration" in message
    assert "IDP_CATALOG" in message
    assert "IDP_VALIDATION_ENDPOINT" in message


def test_idp_mode_environment_variable_activates_databricks_validation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("IDP_MODE", "databricks")

    with pytest.raises(ValidationError, match="IDP_MODE=databricks requires configuration"):
        Settings(_env_file=None)
