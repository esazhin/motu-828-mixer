import configparser
import importlib.util
import runpy
import shlex
import warnings
from pathlib import Path

import pytest

UNIT = Path(__file__).resolve().parent.parent / "deploy" / "motu-mixer.service"


def _unit():
    parser = configparser.ConfigParser(strict=False, interpolation=None)
    parser.optionxform = str
    parser.read_string(UNIT.read_text())
    return parser


def test_unit_starts_the_mixer_module_with_the_repository_venv_python_from_the_repository():
    service = _unit()["Service"]
    working_directory = service["WorkingDirectory"].strip()
    command = shlex.split(service["ExecStart"])

    # the service is started as `python -m _motu_mixer`, nothing more
    interpreter, arguments = command[0], command[1:]
    assert arguments == ["-m", "_motu_mixer"]

    # the interpreter is the repository's .venv python, and the repository is the working directory
    venv_suffix = "/.venv/bin/python"
    assert interpreter.endswith(venv_suffix)
    repository = interpreter[: -len(venv_suffix)]
    assert working_directory
    assert repository.endswith(working_directory.rstrip("/"))


REPOSITORY = UNIT.parent.parent
INSTALL_COMMAND = "systemctl --user daemon-reload && systemctl --user enable --now motu-mixer"


def _installed_unit(repository):
    parser = configparser.ConfigParser(strict=False, interpolation=None)
    parser.optionxform = str
    parser.read_string(UNIT.read_text().replace("<repo>", str(repository)))
    return parser


def test_unit_restarts_after_a_failure_and_starts_with_the_login_session():
    unit = _unit()
    assert {"Unit", "Service", "Install"} <= set(unit.sections())
    service = unit["Service"]
    assert shlex.split(service["ExecStart"])[-2:] == ["-m", "_motu_mixer"]
    assert service["WorkingDirectory"].strip()
    assert service["Restart"].strip() == "on-failure"
    assert service["RestartSec"].strip() == "2"
    assert unit["Install"]["WantedBy"].strip() == "default.target"


def test_installing_with_the_repository_path_runs_its_venv_python_from_the_repository():
    # the documented install names this file and the unit enabled by its file name
    assert INSTALL_COMMAND in UNIT.read_text()
    assert UNIT.stem == INSTALL_COMMAND.split()[-1]

    # replacing <repo> with a real path (this one has spaces) gives that repository's venv python and directory
    service = _installed_unit(REPOSITORY)["Service"]
    assert shlex.split(service["ExecStart"]) == [f"{REPOSITORY}/.venv/bin/python", "-m", "_motu_mixer"]
    assert service["WorkingDirectory"].strip() == str(REPOSITORY)
    assert "<repo>" not in UNIT.read_text().replace("<repo>", str(REPOSITORY))


@pytest.mark.parametrize("start", ["unit-starts-main"])
def test_unit_start_command_reaches_the_entry_point(start, monkeypatch):
    import uvicorn

    service = _installed_unit(REPOSITORY)["Service"]
    working_directory = service["WorkingDirectory"].strip()
    module = shlex.split(service["ExecStart"])[-1]
    served = []
    monkeypatch.setattr(uvicorn, "run", lambda app, **options: served.append(app))
    for name in ("MOTU_PORT_NAME", "MOTU_HTTP_HOST", "MOTU_HTTP_PORT"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.chdir(working_directory)
    monkeypatch.syspath_prepend(working_directory)

    assert Path(importlib.util.find_spec(module).origin).parent == REPOSITORY / module
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        runpy.run_module(module, run_name="__main__", alter_sys=True)
    assert len(served) == 1
