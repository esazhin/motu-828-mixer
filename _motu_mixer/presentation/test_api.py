import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

from fastapi.testclient import TestClient

from _motu_mixer.application.ports import ChangeOutcome, Snapshot
from _motu_mixer.domain.actions import SetLevel, SetRouteSlot
from _motu_mixer.domain.state import MixerState
from _motu_mixer.domain.status import ConnectionStatus, LoopPath, PathNode, Refusal, RefusalKind
from _motu_mixer.domain.values import Address, Delta, OutputSlot, SentFrame, Source
from _motu_mixer.presentation.api import create_app
from _motu_mixer.presentation.ws_hub import WebSocketHub

STATIC_DIR = Path(__file__).parent / "static"
MINUS_6_DB_FRAME = bytes.fromhex("f0 00 00 3b 00 01 00 0a 03 78 02 03 00 04 00 05 00 4d 4e f7")
MUTE_FRAME = bytes.fromhex("f0 00 00 3b 00 01 00 0a 03 7b 02 03 00 01 01 f7")
SPDIF_LOOP = LoopPath((PathNode(Source(58), "S/PDIF in L"), PathNode(OutputSlot(58), "S/PDIF out L")))


def test_api_hands_each_parsed_change_to_the_mixer_service_and_serializes_its_answers():
    class MixerServiceDouble:
        """Answers Accepted, then Refused, then a loop refusal; a canned snapshot and sent log; records each call."""

        def __init__(self):
            self.changes = []
            self.snapshots = 0
            self.sent_logs = []
            self.outcomes = [
                ChangeOutcome.Accepted(sent=(MINUS_6_DB_FRAME,)),
                ChangeOutcome.Refused(Refusal(RefusalKind.SLOT_NOT_WRITABLE, "slot 86 cannot be changed here")),
                ChangeOutcome.Refused(Refusal(RefusalKind.LOOP, "S/PDIF in L → S/PDIF out L", SPDIF_LOOP)),
            ]

        def request_change(self, action, client_id):
            self.changes.append((action, client_id))
            return self.outcomes.pop(0)

        def snapshot(self):
            self.snapshots += 1
            return Snapshot(state=MixerState(), status=ConnectionStatus.SYNCED, read_only_detail=None, loops=frozenset())

        def sent_log(self, n):
            self.sent_logs.append(n)
            return (SentFrame(t=10.0, frame=MINUS_6_DB_FRAME), SentFrame(t=10.5, frame=MUTE_FRAME))[-n:]

    service = MixerServiceDouble()
    client = TestClient(create_app(service=service, session=SimpleNamespace(), hub=SimpleNamespace(), static_dir=STATIC_DIR))

    # an accepted change answers 200 with the frames sent, as hex
    accepted = client.post("/api/change", json={"kind": "set_level", "bus": 4, "input": 2, "db": -6.0})
    assert accepted.status_code == 200
    assert accepted.json()["accepted"] is True
    assert [bytes.fromhex(frame) for frame in accepted.json()["sent"]] == [MINUS_6_DB_FRAME]

    # a refused change answers 409 with its kind and reason, and no path
    refused = client.post("/api/change", json={"kind": "set_route_slot", "slot": 86, "source": 32})
    assert refused.status_code == 409
    body = refused.json()
    assert body["accepted"] is False
    assert body["kind"] == "slot_not_writable"
    assert "slot 86 cannot be changed here" in body["reason"]
    assert body["path"] is None

    # a loop refusal carries the path's hop labels
    looped = client.post("/api/change", json={"kind": "set_route_slot", "slot": 58, "source": 58})
    assert looped.status_code == 409
    assert looped.json()["kind"] == "loop"
    assert looped.json()["path"] == ["S/PDIF in L", "S/PDIF out L"]

    # every parsed action reached the service, from no particular client
    assert service.changes == [
        (SetLevel(bus=4, input=2, db=-6.0), None),
        (SetRouteSlot(slot=86, source=32), None),
        (SetRouteSlot(slot=58, source=58), None),
    ]

    # the snapshot is serialized as the state
    state = client.get("/api/state")
    assert state.status_code == 200
    assert service.snapshots >= 1
    assert state.json()["status"] == "synced"
    assert state.json()["read_only_reason"] is None
    assert state.json()["loops"] == []

    # the sent log is asked for n entries and served newest last, at most n of them
    sent = client.get("/api/diag/sent", params={"n": 1})
    assert sent.status_code == 200
    assert service.sent_logs == [1]
    entries = sent.json()
    assert len(entries) == 1
    assert entries[0]["t"] == 10.5
    assert bytes.fromhex(entries[0]["hex"]) == MUTE_FRAME

    sent = client.get("/api/diag/sent", params={"n": 50})
    assert service.sent_logs == [1, 50]
    assert [bytes.fromhex(entry["hex"]) for entry in sent.json()] == [MINUS_6_DB_FRAME, MUTE_FRAME]


def test_port_endpoints_release_and_take_back_the_port_through_the_session_and_return_its_status():
    class DeviceSessionDouble:
        """Answers RELEASED to release() and each reachable status to acquire() in turn; records each call."""

        def __init__(self):
            self.calls = []
            self.acquired = [ConnectionStatus.SYNCING, ConnectionStatus.BUSY, ConnectionStatus.NO_DEVICE, ConnectionStatus.NO_REPLY]

        async def release(self):
            self.calls.append("release")
            return ConnectionStatus.RELEASED

        async def acquire(self):
            self.calls.append("acquire")
            return self.acquired.pop(0)

    session = DeviceSessionDouble()
    client = TestClient(create_app(service=SimpleNamespace(), session=session, hub=SimpleNamespace(), static_dir=STATIC_DIR))

    # releasing asks the session once and nothing else: no take-back until it is asked for
    released = client.post("/api/port/release")
    assert released.status_code == 200
    assert released.json() == {"status": "released"}
    assert session.calls == ["release"]

    # taking back asks the session and returns whichever status it reached
    reached = [client.post("/api/port/acquire") for _ in range(4)]
    assert [response.status_code for response in reached] == [200] * 4
    assert [response.json() for response in reached] == [
        {"status": "syncing"}, {"status": "busy"}, {"status": "no_device"}, {"status": "no_reply"},
    ]
    assert session.calls == ["release", "acquire", "acquire", "acquire", "acquire"]


def test_api_parses_every_change_and_serializes_snapshot_and_outcomes_through_the_view_model():
    index_refusal = Refusal(RefusalKind.INDEX_RANGE, "bus 16, input 44 does not exist")
    malformed_refusal = Refusal(RefusalKind.MALFORMED_REQUEST, "unknown kind 'foo'")
    snapshot = Snapshot(state=MixerState(), status=ConnectionStatus.SYNCED, read_only_detail=None, loops=frozenset())
    state_json = {"status": "synced", "read_only_reason": None, "layout": {"tabs": [], "strips": []}, "loops": []}
    accepted_json = {"accepted": True, "sent": [MINUS_6_DB_FRAME.hex(" ")]}

    class ViewModelDouble:
        """Answers a typed action, an index refusal or a malformed refusal per body; canned state and outcome dicts."""

        def __init__(self):
            self.parsed = []
            self.snapshots = []
            self.outcomes = []

        def parse_action(self, obj):
            self.parsed.append(obj)
            answers = {
                ("set_level", 4, 2): SetLevel(bus=4, input=2, db=-6.0),
                ("set_level", 16, 44): index_refusal,
                ("foo", None, None): malformed_refusal,
            }
            return answers[(obj["kind"], obj.get("bus"), obj.get("input"))]

        def snapshot_json(self, snapshot):
            self.snapshots.append(snapshot)
            return state_json

        def outcome_json(self, outcome):
            self.outcomes.append(outcome)
            if isinstance(outcome, ChangeOutcome.Accepted):
                return accepted_json
            return {"accepted": False, "kind": str(outcome.refusal.kind), "reason": outcome.refusal.detail, "path": None}

    class MixerServiceDouble:
        """Accepts every change with the −6 dB frame and serves one snapshot; records each call."""

        def __init__(self):
            self.changes = []

        def request_change(self, action, client_id):
            self.changes.append((action, client_id))
            return ChangeOutcome.Accepted(sent=(MINUS_6_DB_FRAME,))

        def snapshot(self):
            return snapshot

    class HubDouble:
        """Names the socket 'phone' and answers each reply with one frame on it; records each reply."""

        def __init__(self):
            self.replies = []

        async def register(self, ws):
            self.socket = ws
            return "phone"

        def unregister(self, client_id):
            pass

        async def reply(self, client_id, outcome):
            self.replies.append((client_id, outcome))
            await self.socket.send_json({"type": "replied"})

    view_model, service, hub = ViewModelDouble(), MixerServiceDouble(), HubDouble()
    client = TestClient(create_app(service=service, session=SimpleNamespace(), hub=hub, static_dir=STATIC_DIR, view_model=view_model))

    level = {"kind": "set_level", "bus": 4, "input": 2, "db": -6.0}
    out_of_range = {"kind": "set_level", "bus": 16, "input": 44, "db": -6.0}
    unknown = {"kind": "foo"}

    # each decoded body reaches parse_action; a typed action goes on to the service and its outcome is serialized
    accepted = client.post("/api/change", json=level)
    assert accepted.status_code == 200
    assert accepted.json() == accepted_json

    # a refusal from parse_action answers 409 with the serialized refusal and never reaches the service
    for body, refusal in ((out_of_range, index_refusal), (unknown, malformed_refusal)):
        refused = client.post("/api/change", json=body)
        assert refused.status_code == 409
        assert refused.json() == {"accepted": False, "kind": str(refusal.kind), "reason": refusal.detail, "path": None}

    assert view_model.parsed == [level, out_of_range, unknown]
    assert service.changes == [(SetLevel(bus=4, input=2, db=-6.0), None)]
    assert view_model.outcomes == [
        ChangeOutcome.Accepted(sent=(MINUS_6_DB_FRAME,)),
        ChangeOutcome.Refused(index_refusal),
        ChangeOutcome.Refused(malformed_refusal),
    ]

    # the service's snapshot is handed to snapshot_json and its dict, layout included, is the state served
    state = client.get("/api/state")
    assert state.status_code == 200
    assert view_model.snapshots == [snapshot]
    assert state.json() == state_json

    # each WebSocket action object reaches parse_action too; a refusal is replied without asking the service
    with client.websocket_connect("/ws") as ws:
        ws.send_json({"type": "change", "action": level})
        assert ws.receive_json() == {"type": "replied"}
        ws.send_json({"type": "change", "action": out_of_range})
        assert ws.receive_json() == {"type": "replied"}

    assert view_model.parsed[3:] == [level, out_of_range]
    assert service.changes[1:] == [(SetLevel(bus=4, input=2, db=-6.0), "phone")]
    assert hub.replies == [
        ("phone", ChangeOutcome.Accepted(sent=(MINUS_6_DB_FRAME,))),
        ("phone", ChangeOutcome.Refused(index_refusal)),
    ]


def test_ws_connections_are_registered_replied_to_and_unregistered_through_the_hub():
    level = {"kind": "set_level", "bus": 4, "input": 2, "db": -6.0}
    accepted = ChangeOutcome.Accepted(sent=(MINUS_6_DB_FRAME,))
    refused = ChangeOutcome.Refused(Refusal(RefusalKind.SLOT_NOT_WRITABLE, "slot 86 cannot be changed here"))

    class MixerServiceDouble:
        """Accepts every change except the last one asked for, which it refuses."""

        def __init__(self):
            self.outcomes = [accepted, refused, accepted, accepted, accepted, accepted]

        def request_change(self, action, client_id):
            return self.outcomes.pop(0)

    class HubDouble:
        """Names sockets phone, pc, tablet; sends 'state' on register and the outcome's type on reply; the tablet's send fails."""

        def __init__(self):
            self.calls = []
            self.sockets = {}
            self.names = ["phone", "pc", "tablet"]

        async def register(self, ws):
            client_id = self.names.pop(0)
            self.calls.append(("register", client_id))
            self.sockets[client_id] = ws
            await ws.send_json({"type": "state"})
            return client_id

        def unregister(self, client_id):
            self.calls.append(("unregister", client_id))

        async def reply(self, client_id, outcome):
            self.calls.append(("reply", client_id, outcome))
            if client_id == "tablet":
                self.sockets.pop(client_id, None)
                return
            kind = "accepted" if isinstance(outcome, ChangeOutcome.Accepted) else "refused"
            await self.sockets[client_id].send_json({"type": kind})

    hub = HubDouble()
    client = TestClient(create_app(service=MixerServiceDouble(), session=SimpleNamespace(), hub=hub, static_dir=STATIC_DIR))

    # a connection is registered and its first frame is the state; each change is replied to that client
    with client.websocket_connect("/ws") as phone:
        assert phone.receive_json() == {"type": "state"}
        phone.send_json({"type": "change", "action": level})
        assert phone.receive_json() == {"type": "accepted"}
        phone.send_json({"type": "change", "action": {"kind": "set_route_slot", "slot": 86, "source": 32}})
        assert phone.receive_json() == {"type": "refused"}

    assert hub.calls == [
        ("register", "phone"),
        ("reply", "phone", accepted),
        ("reply", "phone", refused),
        ("unregister", "phone"),
    ]

    # with two clients open, a reply goes to the client that asked, by the id register returned
    with client.websocket_connect("/ws") as pc, client.websocket_connect("/ws") as tablet:
        assert pc.receive_json() == {"type": "state"}
        assert tablet.receive_json() == {"type": "state"}
        pc.send_json({"type": "change", "action": level})
        assert pc.receive_json() == {"type": "accepted"}

        # a client whose send fails is dropped by the hub without disturbing the api: later changes still get replies
        tablet.send_json({"type": "change", "action": level})
        tablet.send_json({"type": "change", "action": level})
        pc.send_json({"type": "change", "action": level})
        assert pc.receive_json() == {"type": "accepted"}

    assert hub.calls[4:6] == [("register", "pc"), ("register", "tablet")]
    assert ("reply", "pc", accepted) in hub.calls[6:]
    assert [call for call in hub.calls if call[:2] == ("reply", "tablet")] == [("reply", "tablet", accepted)] * 2
    assert [call for call in hub.calls if call[0] == "reply" and call[1] == "pc"] == [("reply", "pc", accepted)] * 2
    assert sorted(call for call in hub.calls if call[0] == "unregister") == [
        ("unregister", "pc"), ("unregister", "phone"), ("unregister", "tablet"),
    ]


def static_assets(tmp_path):
    for name in ("index.html", "app.js", "mixer_client.js", "eq_math.js", "app.test.mjs"):
        (tmp_path / name).write_text(f"// {name}\n")
    return tmp_path


def test_the_page_and_its_modules_are_served_and_no_other_static_file(tmp_path):
    client = TestClient(create_app(service=SimpleNamespace(), session=SimpleNamespace(), hub=SimpleNamespace(),
                                   static_dir=static_assets(tmp_path)))

    page = client.get("/")
    assert page.status_code == 200
    assert page.headers["content-type"].startswith("text/html")
    assert page.text == "// index.html\n"

    for name in ("app.js", "mixer_client.js", "eq_math.js"):
        module = client.get(f"/{name}")
        assert module.status_code == 200
        assert module.headers["content-type"].startswith("application/javascript")
        assert module.text == f"// {name}\n"

    assert client.get("/app.test.mjs").status_code == 404
    assert client.get("/static/app.js").status_code == 404
    assert client.get("/index.html").status_code == 404


def test_a_posted_change_answers_200_with_its_frames_or_409_with_the_refusal_and_never_422():
    bodies = [
        {"kind": "set_level", "bus": 4, "input": 2, "db": -6.0},
        {"kind": "set_pan", "bus": 0, "input": 2, "pan": 0.5},
        {"kind": "set_mute", "bus": 14, "input": 27, "on": True},
        {"kind": "set_solo", "bus": 4, "input": 2, "on": True},
        {"kind": "set_input_link", "input": 4, "on": True},
        {"kind": "set_bus_link", "bus": 4, "on": True},
        {"kind": "set_master_level", "bus": 4, "db": -3.0},
        {"kind": "set_master_mute", "bus": 0, "on": True},
        {"kind": "set_route", "output_slots": [43, 40], "sources": [76, 77]},
        {"kind": "set_route_slot", "slot": 86, "source": 32},
        {"kind": "set_property", "prop": 5004, "index": 0, "value": 1},
    ]
    loop_refusal = Refusal(RefusalKind.LOOP, "S/PDIF in L → S/PDIF out L", SPDIF_LOOP)

    class MixerServiceDouble:
        """Refuses the S/PDIF route as a loop and accepts everything else with the −6 dB frame; records each action."""

        def __init__(self):
            self.actions = []

        def request_change(self, action, client_id):
            self.actions.append(action)
            if action == SetRouteSlot(slot=58, source=58):
                return ChangeOutcome.Refused(loop_refusal)
            return ChangeOutcome.Accepted(sent=(MINUS_6_DB_FRAME,))

    service = MixerServiceDouble()
    client = TestClient(create_app(service=service, session=SimpleNamespace(), hub=SimpleNamespace(), static_dir=STATIC_DIR))

    # every one of the action kinds is taken, parsed into its typed action and answered with the frames as hex
    for body in bodies:
        response = client.post("/api/change", json=body)
        assert response.status_code == 200, body
        assert response.json()["accepted"] is True
        assert response.json()["sent"] == [MINUS_6_DB_FRAME.hex(" ")]
    assert [type(action).__name__ for action in service.actions] == [
        "SetLevel", "SetPan", "SetMute", "SetSolo", "SetInputLink", "SetBusLink",
        "SetMasterLevel", "SetMasterMute", "SetRoute", "SetRouteSlot", "SetProperty",
    ]

    # a refusal from the service answers 409 with the path's labels
    looped = client.post("/api/change", json={"kind": "set_route_slot", "slot": 58, "source": 58})
    assert looped.status_code == 409
    assert looped.json()["accepted"] is False
    assert looped.json()["kind"] == "loop"
    assert "S/PDIF in L → S/PDIF out L" in looped.json()["reason"]
    assert looped.json()["path"] == ["S/PDIF in L", "S/PDIF out L"]

    # out-of-range and malformed bodies, even ones that are not JSON objects, answer 409 and never reach the service
    asked = len(service.actions)
    for content, kind in (
        (b'{"kind": "set_level", "bus": 16, "input": 44, "db": -6.0}', "index_range"),
        (b'{"kind": "foo"}', "malformed_request"),
        (b'{"kind": "set_level", "bus": 4, "input": 2}', "malformed_request"),
        (b'["set_level"]', "malformed_request"),
        (b"not json", "malformed_request"),
        (b"", "malformed_request"),
    ):
        response = client.post("/api/change", content=content, headers={"content-type": "application/json"})
        assert response.status_code == 409, content
        assert response.json()["accepted"] is False
        assert response.json()["kind"] == kind
        assert response.json()["path"] is None
        assert response.json()["reason"]
    assert len(service.actions) == asked


def test_state_port_hand_over_and_sent_log_answer_from_the_service_and_the_session():
    snapshot = Snapshot(state=MixerState(), status=ConnectionStatus.READ_ONLY, read_only_detail="API version 10, expected 9",
                        loops=frozenset({SPDIF_LOOP}))
    frames = tuple(SentFrame(t=float(t), frame=bytes([0xF0, t, 0xF7])) for t in range(5))

    class MixerServiceDouble:
        def snapshot(self):
            return snapshot

        def sent_log(self, n):
            return frames[-n:]

    class DeviceSessionDouble:
        async def release(self):
            return ConnectionStatus.RELEASED

        async def acquire(self):
            return ConnectionStatus.SYNCING

    client = TestClient(create_app(service=MixerServiceDouble(), session=DeviceSessionDouble(), hub=SimpleNamespace(),
                                   static_dir=STATIC_DIR))

    state = client.get("/api/state")
    assert state.status_code == 200
    body = state.json()
    assert {"status", "read_only_reason", "device", "layout", "cells", "bus_master", "routing", "links", "loops"} <= set(body)
    assert body["status"] == "read_only"
    assert body["read_only_reason"] == "API version 10, expected 9"
    assert body["loops"] == [["S/PDIF in L", "S/PDIF out L"]]

    assert client.post("/api/port/release").json() == {"status": "released"}
    acquired = client.post("/api/port/acquire")
    assert acquired.status_code == 200
    assert acquired.json() == {"status": "syncing"}

    for n in (1, 3, 50):
        sent = client.get("/api/diag/sent", params={"n": n})
        assert sent.status_code == 200
        entries = sent.json()
        assert len(entries) == min(n, len(frames))
        assert entries[-1] == {"t": 4.0, "hex": "f0 04 f7"}
        assert [entry["t"] for entry in entries] == sorted(entry["t"] for entry in entries)


def test_each_socket_starts_with_the_state_then_gets_published_messages_and_its_own_replies():
    snapshot = Snapshot(state=MixerState(), status=ConnectionStatus.SYNCED, read_only_detail=None, loops=frozenset())
    refused = Refusal(RefusalKind.SLOT_NOT_WRITABLE, "slot 86 cannot be changed here")

    class MixerServiceDouble:
        """Refuses the slot-86 route and accepts everything else with the −6 dB frame."""

        def request_change(self, action, client_id):
            if action == SetRouteSlot(slot=86, source=32):
                return ChangeOutcome.Refused(refused)
            return ChangeOutcome.Accepted(sent=(MINUS_6_DB_FRAME,))

        def snapshot(self):
            return snapshot

    class DeviceSessionDouble:
        """run() waits until stop() is called."""

        def __init__(self):
            self.stopped = asyncio.Event()

        async def run(self):
            await self.stopped.wait()

        def stop(self):
            self.stopped.set()

    hub = WebSocketHub(snapshot_provider=MixerServiceDouble().snapshot)
    app = create_app(service=MixerServiceDouble(), session=DeviceSessionDouble(), hub=hub, static_dir=STATIC_DIR)
    fader = (Delta(Address(1016, 2), b"\x01\x00\x00\x00", b"\x00\x80\x4d\xce"),)

    with TestClient(app) as client:
        with client.websocket_connect("/ws") as phone, client.websocket_connect("/ws") as pc:
            for ws in (phone, pc):
                first = ws.receive_json()
                assert first["type"] == "state"
                assert first["status"] == "synced"
                assert "layout" in first

            # each change gets its reply on the socket that sent it
            phone.send_text(json.dumps({"type": "change", "action": {"kind": "set_level", "bus": 4, "input": 2, "db": -6.0}}))
            assert phone.receive_json() == {"type": "accepted", "accepted": True, "sent": [MINUS_6_DB_FRAME.hex(" ")]}
            pc.send_text(json.dumps({"type": "change", "action": {"kind": "set_route_slot", "slot": 86, "source": 32}}))
            reply = pc.receive_json()
            assert reply["type"] == "refused"
            assert reply["kind"] == "slot_not_writable"
            assert reply["path"] is None
            pc.send_text(json.dumps({"type": "change", "action": {"kind": "set_level", "bus": 16, "input": 44, "db": 0}}))
            assert pc.receive_json()["kind"] == "index_range"

            # what the service publishes reaches every socket as delta, status and a fresh state
            client.portal.call(hub.publish_delta, fader)
            client.portal.call(hub.publish_status, ConnectionStatus.READ_ONLY, "model 828X, expected 828E", frozenset())
            client.portal.call(hub.publish_state, snapshot)
            for ws in (phone, pc):
                delta = ws.receive_json()
                assert delta["type"] == "delta"
                assert delta["changes"][0]["prop"] == "fader"
                assert delta["changes"][0]["index"] == 2
                assert ws.receive_json() == {"type": "status", "status": "read_only",
                                             "read_only_reason": "model 828X, expected 828E", "loops": []}
                assert ws.receive_json()["type"] == "state"

        # after a socket closes, a new connection starts with the state again
        with client.websocket_connect("/ws") as woken:
            assert woken.receive_json()["type"] == "state"


def test_the_app_runs_the_session_while_it_is_up_and_stops_it_on_shutdown():
    class DeviceSessionDouble:
        """run() waits until stop() is called; records both."""

        def __init__(self):
            self.calls = []
            self.stopped = asyncio.Event()

        async def run(self):
            self.calls.append("run")
            await self.stopped.wait()
            self.calls.append("run ended")

        def stop(self):
            self.calls.append("stop")
            self.stopped.set()

    session = DeviceSessionDouble()
    app = create_app(service=SimpleNamespace(), session=session, hub=SimpleNamespace(), static_dir=STATIC_DIR)
    assert session.calls == []

    with TestClient(app) as client:
        client.portal.call(asyncio.sleep, 0)
        assert session.calls == ["run"]

    assert session.calls == ["run", "stop", "run ended"]


def test_every_module_the_page_imports_is_served():
    # 2026-10-12: eq_math.js was imported by mixer_client.js but not served -> the whole page died on 'connecting'.
    import re
    from _motu_mixer.presentation.api import _ASSETS
    served = {path.lstrip("/") for path in _ASSETS}
    static = STATIC_DIR
    pending, seen = ["index.html"], set()
    while pending:
        name = pending.pop()
        if name in seen:
            continue
        seen.add(name)
        text = (static / name).read_text()
        for imported in re.findall(r"""(?:from|import)\s+['"]\./([\w.]+\.js)['"]""", text):
            assert imported in served, f"{name} imports {imported}, which the API does not serve"
            pending.append(imported)
    assert {"app.js", "mixer_client.js", "eq_math.js"} <= seen
