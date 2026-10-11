"""Per-workspace settings files (databricks_etl/targets/<name>.yml), checked by make check."""

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
TEMPLATE = ROOT / "databricks_etl/targets/example.yml.template"


def load_validator():
    spec = importlib.util.spec_from_file_location(
        "validate_configuration", ROOT / "scripts/validate_configuration.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


validator = load_validator()


def settings(tmp_path: Path, name: str = "us", **replacements: str) -> Path:
    text = TEMPLATE.read_text(encoding="utf-8").replace("  example:", f"  {name}:")
    for old, new in replacements.items():
        assert old in text, old
        text = text.replace(old, new)
    path = tmp_path / f"{name}.yml"
    path.write_text(text, encoding="utf-8")
    return path


def test_a_filled_in_copy_of_the_template_is_valid(tmp_path: Path) -> None:
    validator.validate_target_file(settings(tmp_path))
    validator.validate_target_file(TEMPLATE)


@pytest.mark.parametrize(
    ("replacements", "message"),
    [
        ({'chat_mode: knowledge_assistant': "chat_mode: off"}, "must be text"),
        ({'ka_sync_enabled: "true"': "ka_sync_enabled: true"}, "must be text"),
        ({"chat_mode: knowledge_assistant": "chat_mode: auto"}, "chat_mode must be"),
        ({"mode: production": "mode: development"}, "mode: production"),
        ({"      root_path:": "      # root_path:"}, "root_path"),
        ({"table_prefix: idp ": "table_prefix: idp-x "}, "simple identifier"),
        ({"      warehouse_id: YOUR_WAREHOUSE_ID\n": ""}, "missing \\['warehouse_id'\\]"),
        ({"      app_name: idp-mvp\n": "      genie_space: x\n"}, "unknown variables"),
        ({"parse_concurrency: 3": "parse_concurrency: 5"}, "combined budget"),
    ],
)
def test_settings_mistakes_fail_with_the_reason(tmp_path: Path, replacements, message) -> None:
    with pytest.raises(ValueError, match=message):
        validator.validate_target_file(settings(tmp_path, **replacements))


@pytest.mark.parametrize("name", ["dev", "prod", "Us"])
def test_reserved_names_are_refused(tmp_path: Path, name: str) -> None:
    with pytest.raises(ValueError):
        validator.validate_target_file(settings(tmp_path, name=name))


def test_target_must_be_named_after_its_file(tmp_path: Path) -> None:
    path = settings(tmp_path)
    path.rename(tmp_path / "eu.yml")
    with pytest.raises(ValueError, match="named after the file"):
        validator.validate_target_file(tmp_path / "eu.yml")
