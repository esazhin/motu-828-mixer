import pytest

from _motu_mixer.__main__ import main
from _motu_mixer.config import Settings


def test_main_loads_settings_from_the_environment_and_serves_or_exits_on_bad_config():
    environ = {"MOTU_HTTP_PORT": "5058"}
    asked, served = [], []

    def load_settings(answer):
        def from_env(env):
            asked.append(env)
            if isinstance(answer, Exception):
                raise answer
            return answer
        return from_env

    def serve(app, **options):
        served.append(options)

    # the environment main is given is what the settings are read from
    main(environ, load_settings=load_settings(Settings()), serve=serve)
    assert asked == [environ]

    # the defaults are served on 0.0.0.0:8280
    assert served[-1]["host"] == "0.0.0.0"
    assert served[-1]["port"] == 8280

    # an overridden port and host are what the server binds
    main(environ, load_settings=load_settings(Settings(http_port=5058)), serve=serve)
    assert served[-1]["port"] == 5058
    main(environ, load_settings=load_settings(Settings(host="127.0.0.1")), serve=serve)
    assert served[-1]["host"] == "127.0.0.1"

    # an invalid configuration exits non-zero without serving
    served.clear()
    with pytest.raises(SystemExit) as exit_info:
        main({"MOTU_HTTP_PORT": "x"}, load_settings=load_settings(ValueError("bad port")), serve=serve)
    assert exit_info.value.code not in (0, None)
    assert served == []


def test_main_builds_the_app_from_the_wired_service_session_hub_and_static_page_and_serves_that_app():
    from pathlib import Path

    from _motu_mixer.application.mixer_service import MixerService
    from _motu_mixer.application.session import DeviceSession
    from _motu_mixer.presentation.ws_hub import WebSocketHub

    built, served = [], []
    app = object()

    def create_app(service, session, hub, static_dir):
        built.append({"service": service, "session": session, "hub": hub, "static_dir": static_dir})
        return app

    def serve(app, **options):
        served.append(app)

    main({}, create_app=create_app, serve=serve)

    # main hands create_app the service, session and hub it wired, and the page's static directory
    assert len(built) == 1
    assert isinstance(built[0]["service"], MixerService)
    assert isinstance(built[0]["session"], DeviceSession)
    assert isinstance(built[0]["hub"], WebSocketHub)
    static = Path(__file__).parent / "presentation" / "static"
    assert Path(built[0]["static_dir"]).resolve() == static.resolve()

    # the app that owns the session's start and stop is the one served, as returned
    assert served == [app]


def test_main_runs_uvicorn_with_the_app_on_the_configured_host_and_port_over_websockets(monkeypatch):
    import uvicorn

    runs = []
    app = object()

    def run(*args, **kwargs):
        runs.append((args, kwargs))

    monkeypatch.setattr(uvicorn, "run", run)

    # by default the app is served on 0.0.0.0:8280 with the websockets implementation
    main({}, create_app=lambda service, session, hub, static_dir: app)
    assert runs == [((app,), {"host": "0.0.0.0", "port": 8280, "ws": "websockets"})]

    # the configured host and port are what uvicorn is asked to bind
    runs.clear()
    main({"MOTU_HTTP_HOST": "127.0.0.1", "MOTU_HTTP_PORT": "5058"},
         create_app=lambda service, session, hub, static_dir: app)
    assert runs == [((app,), {"host": "127.0.0.1", "port": 5058, "ws": "websockets"})]


# --- the composition root, and how it is started -------------------------------------------------------------------

import ctypes
import importlib.util
import runpy
import threading
import time
import warnings
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

from fastapi.testclient import TestClient

from _motu_mixer.domain.values import Address, Gain
from _motu_mixer.infrastructure.capture import parse_amidi_hex
from _motu_mixer.infrastructure.test_alsa_rawmidi import (
    CAPTURE, ENABLE, IN_HANDLE, LISTING_2026_10_12, OUT_HANDLE, PROBE, PROBE_REPLY, amidi_runner, fake_libasound,
)
from _motu_mixer.protocol.sysex import encode_set

REPOSITORY = Path(__file__).resolve().parent.parent
NO_828_LISTING = "Dir Device    Name\nIO  hw:5,0,0  RD-2000 MIDI 1\n"
LINE_3_ON_ANA_3 = {"bus": 4, "input": 2}


def test_main_hands_the_environment_it_is_given_to_the_settings_loader():
    asked = []

    def from_env(environ):
        asked.append(environ)
        return Settings()

    environ = {"MOTU_PORT_NAME": "828 COM"}
    main(environ, load_settings=from_env, serve=lambda app, **options: None)
    assert asked == [environ]


def test_main_hands_create_app_its_wired_parts_and_the_static_directory():
    built = []
    main({}, create_app=lambda *parts: built.append(parts) or "app", serve=lambda app, **options: None)
    service, session, hub, static_dir = built[0]
    assert (type(service).__name__, type(session).__name__, type(hub).__name__) == (
        "MixerService", "DeviceSession", "WebSocketHub")
    assert (Path(static_dir) / "index.html").is_file()


def test_main_runs_the_server_with_the_built_app_and_the_configured_address(monkeypatch):
    import uvicorn

    runs = []
    monkeypatch.setattr(uvicorn, "run", lambda *args, **kwargs: runs.append((args, kwargs)))
    main({"MOTU_HTTP_PORT": "5099"}, create_app=lambda *parts: "the app")
    assert runs == [(("the app",), {"host": "0.0.0.0", "port": 5099, "ws": "websockets"})]


def test_started_as_a_module_from_the_repository_main_serves_with_the_process_environment(monkeypatch):
    import uvicorn

    runs = []
    monkeypatch.setattr(uvicorn, "run", lambda app, **options: runs.append(options))
    monkeypatch.setenv("MOTU_HTTP_PORT", "5058")
    monkeypatch.setenv("MOTU_HTTP_HOST", "127.0.0.1")

    # `python -m _motu_mixer` run from the repository finds this repository's package
    assert Path(importlib.util.find_spec("_motu_mixer").origin).parent.parent == REPOSITORY
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        runpy.run_module("_motu_mixer", run_name="__main__", alter_sys=True)
    assert runs == [{"host": "127.0.0.1", "port": 5058, "ws": "websockets"}]


def test_main_serves_a_valid_configuration_and_exits_non_zero_on_an_invalid_one():
    served = []
    main({"MOTU_HTTP_PORT": "5058"}, serve=lambda app, **options: served.append(options["port"]))
    assert served == [5058]

    with pytest.raises(SystemExit) as exit_info:
        main({"MOTU_HTTP_PORT": "x"}, serve=lambda app, **options: served.append(options["port"]))
    assert exit_info.value.code not in (0, None)
    assert served == [5058]


def unit_on_the_edge(answering):
    """libasound with an 828 behind it that answers the probe and the enable with the recorded conversation."""
    lib, calls = fake_libasound()
    recorded = parse_amidi_hex(CAPTURE.read_text())
    answers = {PROBE: PROBE_REPLY, ENABLE: recorded.removeprefix(PROBE_REPLY)} if answering else {}
    pending, lock = bytearray(), threading.Lock()
    write = lib.snd_rawmidi_write

    def snd_rawmidi_write(handle, data, size):
        written = write(handle, data, size)
        with lock:
            pending.extend(answers.get(calls[-1][2], b""))
        return written

    def snd_rawmidi_read(handle, buf, size):
        with lock:
            chunk = bytes(pending[:getattr(size, "value", size)])
            del pending[:len(chunk)]
        if not chunk:
            return -11
        calls.append(("read", getattr(handle, "value", handle)))
        ctypes.memmove(buf, chunk, len(chunk))
        return len(chunk)

    lib.snd_rawmidi_write, lib.snd_rawmidi_read = snd_rawmidi_write, snd_rawmidi_read
    return lib, calls


def eventually(check, timeout=5.0):
    deadline = time.monotonic() + timeout
    while not (result := check()):
        assert time.monotonic() < deadline, "timed out"
        time.sleep(0.02)
    return result


@contextmanager
def served_by_main(device, environ=None):
    """main's real object graph, with amidi, libasound and uvicorn doubled at the edge, serving through a test client."""
    lib, calls = unit_on_the_edge(answering=device == "answering")
    run_amidi, asked = amidi_runner((NO_828_LISTING if device == "absent" else LISTING_2026_10_12, 0))
    served = []
    main(environ or {}, serve=lambda app, **options: served.append((app, options)),
         run_amidi=run_amidi, load_lib=lambda: lib)
    app, options = served[0]
    with TestClient(app) as client:
        edge = SimpleNamespace(client=client, calls=calls, asked=asked, options=options)
        if device == "answering":
            eventually(lambda: status(edge) == "synced")
        yield edge


def status(edge):
    return edge.client.get("/api/state").json()["status"]


def change(edge, **action):
    return edge.client.post("/api/change", json=action)


def written(edge):
    return [call[2] for call in edge.calls if call[0] == "write"]


def api_reaches_view_model(edge):
    response = change(edge, kind="set_level", bus=16, input=44, db=0.0)
    assert (response.status_code, response.json()["kind"]) == (409, "index_range")


def api_reaches_service(edge):
    assert status(edge) == "no_device"


def api_reaches_session(edge):
    assert edge.client.post("/api/port/release").json() == {"status": "released"}


def api_reaches_hub(edge):
    with edge.client.websocket_connect("/ws") as ws:
        assert ws.receive_json()["type"] == "state"
        ws.send_json({"type": "change", "action": {"kind": "foo"}})
        assert ws.receive_json()["type"] == "refused"


def hub_reaches_view_model(edge):
    with edge.client.websocket_connect("/ws") as ws:
        assert "layout" in ws.receive_json()


def service_reaches_hub(edge):
    with edge.client.websocket_connect("/ws") as ws:
        ws.receive_json()
        edge.client.post("/api/port/release")
        assert ws.receive_json() == {"type": "status", "status": "released", "read_only_reason": None, "loops": []}


def service_reaches_sysex(edge):
    device = edge.client.get("/api/state").json()["device"]
    assert (device["model"], device["serial"], device["api"]) == ("828E", "828E000000", 9)


def service_reaches_state(edge):
    assert change(edge, kind="set_level", db=-6.0, **LINE_3_ON_ANA_3).status_code == 200
    assert edge.client.get("/api/state").json()["cells"]["fader_db"][178] == pytest.approx(-6.0, abs=0.1)


def service_reaches_preflight(edge):
    assert change(edge, kind="set_level", db=-6.0, **LINE_3_ON_ANA_3).json()["kind"] == "not_synced"


def service_reaches_actions(edge):
    assert change(edge, kind="set_input_link", input=5, on=True).json()["kind"] == "odd_link_index"


def service_reaches_loop_guard(edge):
    assert change(edge, kind="set_route", output_slots=[58, 59], sources=[58, 59]).json()["kind"] == "loop"


def service_reaches_scheduler(edge):
    assert len(change(edge, kind="set_level", db=-6.0, **LINE_3_ON_ANA_3).json()["sent"]) == 1


def scheduler_reaches_sysex(edge):
    sent = change(edge, kind="set_level", db=-6.0, **LINE_3_ON_ANA_3).json()["sent"]
    assert sent == [encode_set(Address(1016, 178), Gain.from_db(-6.0)).hex(" ")]


def scheduler_reaches_port(edge):
    frame = bytes.fromhex(change(edge, kind="set_mute", on=True, **LINE_3_ON_ANA_3).json()["sent"][0])
    eventually(lambda: frame in written(edge))


def scheduler_reaches_clock(edge):
    before = time.monotonic()
    change(edge, kind="set_mute", on=True, **LINE_3_ON_ANA_3)
    entry = eventually(lambda: edge.client.get("/api/diag/sent").json())[-1]
    assert before <= entry["t"] <= time.monotonic()


def session_reaches_locator_and_port(edge):
    eventually(lambda: ("open", b"hw:4,0,1", 0) in edge.calls)
    assert edge.asked[0][0] == ["amidi", "-l"]


def session_reaches_pump(edge):
    assert ENABLE in written(edge)


def session_reaches_service(edge):
    eventually(lambda: status(edge) in ("syncing", "no_reply"))


def session_reaches_sysex(edge):
    assert eventually(lambda: written(edge))[0] == PROBE


def session_reaches_clock(edge):
    eventually(lambda: status(edge) == "no_reply")


def pump_reaches_port(edge):
    assert ("read", IN_HANDLE) in edge.calls


def actions_reach_topology(edge):
    sent = change(edge, kind="set_level", bus=1, input=2, db=-6.0).json()["sent"]
    assert sent == [encode_set(Address(1016, 2), Gain.from_db(-6.0)).hex(" ")]


def loop_guard_reaches_topology(edge):
    path = change(edge, kind="set_route", output_slots=[58, 59], sources=[58, 59]).json()["path"]
    assert path[0].startswith("S/PDIF in")


def state_reaches_properties(edge):
    assert edge.client.get("/api/state").json()["cells"]["fader"][2] == 1.0


def preflight_reaches_state(edge):
    assert status(edge) == "synced"


def sysex_reaches_properties(edge):
    sent = change(edge, kind="set_mute", on=True, **LINE_3_ON_ANA_3).json()["sent"]
    assert sent[0].endswith("00 01 01 f7")


def view_model_reaches_topology(edge):
    tabs = edge.client.get("/api/state").json()["layout"]["tabs"]
    assert [tab["label"] for tab in tabs][:3] == ["Main 1-2", "Ana 1-2", "Ana 3"]


def main_reaches_config(edge):
    assert edge.options["port"] == 5058


def main_reaches_api(edge):
    response = edge.client.get("/")
    assert (response.status_code, response.headers["content-type"].split(";")[0]) == (200, "text/html")


def actions_reach_properties(edge):
    assert change(edge, kind="set_property", prop=5004, index=0, value=1).json()["kind"] == "not_writable_property"


@pytest.mark.parametrize("reaches, device, environ", [
    pytest.param(api_reaches_view_model, "absent", None, id="api-reaches-view-model"),
    pytest.param(api_reaches_service, "absent", None, id="api-reaches-service"),
    pytest.param(api_reaches_session, "absent", None, id="api-reaches-session"),
    pytest.param(api_reaches_hub, "absent", None, id="api-reaches-hub"),
    pytest.param(hub_reaches_view_model, "absent", None, id="hub-reaches-view-model"),
    pytest.param(service_reaches_hub, "absent", None, id="service-reaches-hub"),
    pytest.param(service_reaches_sysex, "answering", None, id="service-reaches-sysex"),
    pytest.param(service_reaches_state, "answering", None, id="service-reaches-state"),
    pytest.param(service_reaches_preflight, "absent", None, id="service-reaches-preflight"),
    pytest.param(service_reaches_actions, "answering", None, id="service-reaches-actions"),
    pytest.param(service_reaches_loop_guard, "answering", None, id="service-reaches-loop-guard"),
    pytest.param(service_reaches_scheduler, "answering", None, id="service-reaches-scheduler"),
    pytest.param(scheduler_reaches_sysex, "answering", None, id="scheduler-reaches-sysex"),
    pytest.param(scheduler_reaches_port, "answering", None, id="scheduler-reaches-port"),
    pytest.param(scheduler_reaches_clock, "answering", None, id="scheduler-reaches-clock"),
    pytest.param(session_reaches_locator_and_port, "silent", None, id="session-reaches-locator-and-port"),
    pytest.param(session_reaches_pump, "answering", None, id="session-reaches-pump"),
    pytest.param(session_reaches_service, "silent", None, id="session-reaches-service"),
    pytest.param(session_reaches_sysex, "silent", None, id="session-reaches-sysex"),
    pytest.param(session_reaches_clock, "silent", None, id="session-reaches-clock"),
    pytest.param(pump_reaches_port, "answering", None, id="pump-reaches-port"),
    pytest.param(actions_reach_topology, "answering", None, id="actions-reach-topology"),
    pytest.param(loop_guard_reaches_topology, "answering", None, id="loop-guard-reaches-topology"),
    pytest.param(state_reaches_properties, "answering", None, id="state-reaches-properties"),
    pytest.param(preflight_reaches_state, "answering", None, id="preflight-reaches-state"),
    pytest.param(sysex_reaches_properties, "answering", None, id="sysex-reaches-properties"),
    pytest.param(view_model_reaches_topology, "answering", None, id="view-model-reaches-topology"),
    pytest.param(main_reaches_config, "absent", {"MOTU_HTTP_PORT": "5058"}, id="main-reaches-config"),
    pytest.param(main_reaches_api, "absent", None, id="main-reaches-api"),
    pytest.param(actions_reach_properties, "answering", None, id="actions-reach-properties"),
])
def test_main_object_graph_is_wired(reaches, device, environ):
    with served_by_main(device, environ) as edge:
        reaches(edge)


def test_config_local_sets_the_service_and_the_environment_overrides_it(tmp_path, monkeypatch):
    import uvicorn
    runs = []
    monkeypatch.setattr(uvicorn, "run", lambda *a, **k: runs.append(k))
    local = tmp_path / "config.local"
    local.write_text("MOTU_HTTP_PORT=9001\nMOTU_HTTP_HOST=127.0.0.1\n")
    app = object()
    make = lambda service, session, hub, static_dir: app
    main({"MOTU_CONFIG": str(local)}, create_app=make)
    assert (runs[-1]["host"], runs[-1]["port"]) == ("127.0.0.1", 9001)
    main({"MOTU_CONFIG": str(local), "MOTU_HTTP_PORT": "9002"}, create_app=make)
    assert (runs[-1]["host"], runs[-1]["port"]) == ("127.0.0.1", 9002)
    # an explicit environment without MOTU_CONFIG never reads the repository's own config.local (tests stay hermetic)
    main({}, create_app=make)
    assert (runs[-1]["host"], runs[-1]["port"]) == ("0.0.0.0", 8280)
