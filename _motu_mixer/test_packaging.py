import importlib
import tomllib
from pathlib import Path

PYPROJECT = Path(__file__).resolve().parent.parent / "pyproject.toml"


def _project():
    return tomllib.loads(PYPROJECT.read_text())


def test_project_declares_runtime_and_dev_dependencies():
    project = _project()["project"]
    assert project["name"] == "motu-mixer"
    assert project["requires-python"] == ">=3.14"
    assert set(project["dependencies"]) == {"fastapi", "uvicorn", "websockets"}
    assert set(project["optional-dependencies"]["dev"]) == {"pytest", "httpx"}


def test_pytest_collects_from_package_and_deselects_live(pytestconfig):
    options = _project()["tool"]["pytest"]["ini_options"]
    assert options["testpaths"] == ["_motu_mixer"]
    assert options["markers"] == ["live: needs the real 828 (MOTU_LIVE=1)"]
    assert options["addopts"] == "-m 'not live'"
    assert pytestconfig.getoption("markexpr") == "not live"


def test_layer_packages_import():
    for layer in ("domain", "protocol", "application", "infrastructure", "presentation"):
        package = importlib.import_module(f"_motu_mixer.{layer}")
        assert package.__file__.endswith("__init__.py")
