import re
import shlex
import tomllib
from dataclasses import fields
from pathlib import Path
from urllib.parse import urlsplit

from fastapi.testclient import TestClient

from _motu_mixer.config import Settings
from _motu_mixer.domain.status import ConnectionStatus
from _motu_mixer.presentation.api import create_app

REPOSITORY = Path(__file__).resolve().parent.parent
README = (REPOSITORY / "README.md").read_text()
UNIT = REPOSITORY / "deploy" / "motu-mixer.service"


def _commands():
    """Every command line in the README's shell blocks, with trailing comments removed."""
    blocks = re.findall(r"```sh\n(.*?)```", README, re.S)
    lines = (line.split("  #")[0].strip() for block in blocks for line in block.splitlines())
    return [line for line in lines if line and not line.startswith("#")]


def test_readme_installs_with_uv_and_starts_the_service():
    commands = _commands()
    assert "uv venv .venv" in commands
    assert "uv pip install -e '.[dev]'" in commands
    assert "dev" in tomllib.loads((REPOSITORY / "pyproject.toml").read_text())["project"]["optional-dependencies"]
    assert not [c for c in commands if c.startswith("sudo") and ("uv" in c.split() or "pip" in c.split())]

    # the venv the README creates is the one the unit runs, and the run command is the unit's module
    assert '"<repo>/.venv/bin/python" -m _motu_mixer' in UNIT.read_text()
    assert "python -m _motu_mixer" in commands
    assert (REPOSITORY / "_motu_mixer" / "__main__.py").is_file()


def test_readme_enables_the_user_unit_exactly_as_the_unit_file_says():
    commands = _commands()
    documented = next(line for line in UNIT.read_text().splitlines() if "systemctl --user" in line).lstrip("# ")
    assert documented in commands

    install = next(c for c in commands if c.startswith("sed ") and str(UNIT.relative_to(REPOSITORY)) in c)
    target = shlex.split(install)[-1]
    assert target == f"~/.config/systemd/user/{UNIT.name}"
    assert UNIT.stem == documented.split()[-1]
    assert "s|<repo>|$PWD|g" in shlex.split(install)


def test_readme_releases_and_takes_back_the_port_through_the_service_endpoints():
    class SessionDouble:
        """Answers release with RELEASED and acquire with SYNCING; records each call."""

        def __init__(self):
            self.calls = []

        async def release(self):
            self.calls.append("release")
            return ConnectionStatus.RELEASED

        async def acquire(self):
            self.calls.append("acquire")
            return ConnectionStatus.SYNCING

    session = SessionDouble()
    client = TestClient(create_app(None, session, None, REPOSITORY / "_motu_mixer" / "presentation" / "static"))
    curls = [shlex.split(c) for c in _commands() if c.startswith("curl -X POST")]
    paths = [urlsplit(args[-1]) for args in curls]
    assert all(url.port == Settings().http_port for url in paths)

    answers = [client.post(url.path) for url in paths]
    assert session.calls == ["release", "acquire"]
    assert [a.status_code for a in answers] == [200, 200]
    assert answers[0].json() == {"status": "released"}
    assert '{"status": "released"}' in README


def test_readme_names_the_test_commands_as_the_pyproject_configures_them():
    commands = _commands()
    options = tomllib.loads((REPOSITORY / "pyproject.toml").read_text())["tool"]["pytest"]["ini_options"]
    assert "python -m pytest" in commands
    assert "node --test _motu_mixer/presentation/static/" in commands
    assert "MOTU_LIVE=1 python -m pytest -m live" in commands

    # the live command selects the configured marker and sets the variable its description names
    assert options["markers"] == ["live: needs the real 828 (MOTU_LIVE=1)"]
    assert options["addopts"] == "-m 'not live'"
    assert options["testpaths"] == ["_motu_mixer"]
    assert list((REPOSITORY / "_motu_mixer" / "presentation" / "static").glob("*.test.mjs"))


def test_readme_lists_every_setting_with_its_default():
    names = {"port_name": "MOTU_PORT_NAME", "host": "MOTU_HTTP_HOST", "http_port": "MOTU_HTTP_PORT"}
    defaults = Settings()
    assert {f.name for f in fields(Settings)} == set(names)
    for field, variable in names.items():
        assert f"| `{variable}` | `{getattr(defaults, field)}` |" in README
    assert Settings.from_env({"MOTU_HTTP_PORT": "5058"}).http_port == 5058
    assert "MOTU_HTTP_PORT=5058 python -m _motu_mixer" in README
