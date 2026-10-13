import asyncio

from _motu_mixer.application.ports import ChangeOutcome, Snapshot
from _motu_mixer.domain.state import MixerState
from _motu_mixer.domain.status import ConnectionStatus, LoopPath, PathNode, Refusal, RefusalKind
from _motu_mixer.domain.values import Address, Delta, Gain, MixCell, OutputSlot, Report, Source
from _motu_mixer.presentation.ws_hub import WebSocketHub

SPDIF_LOOP = LoopPath((PathNode(Source(58), "S/PDIF in L"), PathNode(OutputSlot(58), "S/PDIF out L")))


class FakeSocket:
    """Records every dict sent to it."""

    def __init__(self):
        self.sent = []

    async def send_json(self, message):
        self.sent.append(message)


async def settle():
    for _ in range(5):
        await asyncio.sleep(0)


def test_hub_sends_the_messages_the_view_model_builds_and_nothing_for_an_undisplayed_delta():
    snapshot = Snapshot(state=MixerState(), status=ConnectionStatus.SYNCED, read_only_detail=None, loops=frozenset())
    fader = (Delta(Address(1016, 2), b"\x01\x00\x00\x00", b"\x00\x80\x4d\xce"),)
    meters = (Delta(Address(6000, 0), b"\x00", b"\x01"),)
    refused = ChangeOutcome.Refused(Refusal(RefusalKind.INDEX_RANGE, "bus 16 does not exist"))

    class ViewModelDouble:
        """Answers a typed dict per builder, and None for the undisplayed delta; records each call."""

        def __init__(self):
            self.calls = []

        def state_message(self, snapshot):
            self.calls.append(("state", snapshot))
            return {"type": "state", "status": "synced"}

        def delta_message(self, deltas):
            self.calls.append(("delta", deltas))
            return None if deltas == meters else {"type": "delta", "changes": [{"prop": "fader", "index": 2, "value": -6.0}]}

        def status_message(self, status, detail, loops):
            self.calls.append(("status", status, detail, loops))
            return {"type": "status", "status": "synced", "read_only_reason": None, "loops": [["S/PDIF in L", "S/PDIF out L"]]}

        def outcome_json(self, outcome):
            self.calls.append(("outcome", outcome))
            return {"type": "refused", "kind": "index_range", "reason": "bus 16 does not exist", "path": None}

    async def scenario():
        view_model = ViewModelDouble()
        hub = WebSocketHub(snapshot_provider=lambda: snapshot, view_model=view_model)
        phone = FakeSocket()

        # a new client gets the state message built from the current snapshot
        client_id = await hub.register(phone)
        await settle()
        assert view_model.calls == [("state", snapshot)]
        assert phone.sent == [{"type": "state", "status": "synced"}]

        # a displayed delta is sent as built; an undisplayed one sends nothing
        hub.publish_delta(fader)
        hub.publish_delta(meters)
        await settle()
        assert view_model.calls[1:] == [("delta", fader), ("delta", meters)]
        assert phone.sent[1:] == [{"type": "delta", "changes": [{"prop": "fader", "index": 2, "value": -6.0}]}]

        # status and full state are sent as built
        hub.publish_status(ConnectionStatus.SYNCED, None, frozenset({SPDIF_LOOP}))
        hub.publish_state(snapshot)
        await settle()
        assert view_model.calls[3:] == [
            ("status", ConnectionStatus.SYNCED, None, frozenset({SPDIF_LOOP})),
            ("state", snapshot),
        ]
        assert phone.sent[2:] == [
            {"type": "status", "status": "synced", "read_only_reason": None, "loops": [["S/PDIF in L", "S/PDIF out L"]]},
            {"type": "state", "status": "synced"},
        ]

        # a reply is the serialized outcome
        await hub.reply(client_id, refused)
        await settle()
        assert view_model.calls[5:] == [("outcome", refused)]
        assert phone.sent[4:] == [{"type": "refused", "kind": "index_range", "reason": "bus 16 does not exist", "path": None}]

        # every message sent carries its type
        assert all("type" in message for message in phone.sent)

    asyncio.run(scenario())


class EchoViewModel:
    """Builds each message as its type plus the thing it was built from."""

    def state_message(self, snapshot):
        return {"type": "state", "snapshot": snapshot}

    def delta_message(self, deltas):
        return {"type": "delta", "deltas": deltas}

    def status_message(self, status, detail, loops):
        return {"type": "status", "status": status, "read_only_reason": detail, "loops": loops}

    def outcome_json(self, outcome):
        return {"type": "accepted" if isinstance(outcome, ChangeOutcome.Accepted) else "refused", "outcome": outcome}

    def meters_message(self, levels):
        return {"type": "meters", "levels": levels}


class BrokenSocket:
    """Fails every send, as a socket whose page has gone away."""

    def __init__(self):
        self.attempts = 0

    async def send_json(self, message):
        self.attempts += 1
        raise ConnectionError("socket closed")


class StuckSocket:
    """Never finishes a send, as a phone that stopped reading."""

    async def send_json(self, message):
        await asyncio.Event().wait()


LINE_3_MAIN_TO_MINUS_6 = (Delta(Address(1016, 2), b"\x01\x00\x00\x00", b"\x00\x80\x4d\xce"),)
SYNCED = Snapshot(state=MixerState(), status=ConnectionStatus.SYNCED, read_only_detail=None, loops=frozenset())


def test_register_sends_state_first_replies_reach_only_their_client_and_a_failing_socket_is_dropped():
    accepted = ChangeOutcome.Accepted(sent=(b"\xf0\xf7",))
    refused = ChangeOutcome.Refused(Refusal(RefusalKind.SLOT_NOT_WRITABLE, "slot 86 cannot be changed here"))

    async def scenario():
        hub = WebSocketHub(snapshot_provider=lambda: SYNCED, view_model=EchoViewModel())
        phone, pc, broken = FakeSocket(), FakeSocket(), BrokenSocket()

        # each registration returns its own id and the state is the first thing its socket gets
        phone_id = await hub.register(phone)
        pc_id = await hub.register(pc)
        await settle()
        assert isinstance(phone_id, str) and isinstance(pc_id, str) and phone_id != pc_id
        assert phone.sent == [{"type": "state", "snapshot": SYNCED}]
        assert pc.sent == [{"type": "state", "snapshot": SYNCED}]

        # a reply reaches only the client it names
        await hub.reply(phone_id, accepted)
        await settle()
        assert phone.sent[1:] == [{"type": "accepted", "outcome": accepted}]
        assert pc.sent[1:] == []
        await hub.reply(pc_id, refused)
        await settle()
        assert pc.sent[1:] == [{"type": "refused", "outcome": refused}]
        assert phone.sent[2:] == []

        # a socket that fails to send is dropped without raising, and the others still receive
        broken_id = await hub.register(broken)
        await settle()
        assert broken.attempts == 1
        await hub.reply(broken_id, accepted)
        hub.publish_delta(LINE_3_MAIN_TO_MINUS_6)
        await settle()
        assert broken.attempts == 1
        assert phone.sent[-1] == pc.sent[-1] == {"type": "delta", "deltas": LINE_3_MAIN_TO_MINUS_6}
        hub.unregister(broken_id)

        # an unregistered client receives nothing more, and unregistering twice is harmless
        hub.unregister(pc_id)
        hub.unregister(pc_id)
        hub.publish_status(ConnectionStatus.SYNCED, None, frozenset())
        await hub.reply(pc_id, accepted)
        await settle()
        assert pc.sent[-1] == {"type": "delta", "deltas": LINE_3_MAIN_TO_MINUS_6}
        assert phone.sent[-1] == {"type": "status", "status": ConnectionStatus.SYNCED, "read_only_reason": None, "loops": frozenset()}

    asyncio.run(scenario())


def test_publishing_returns_at_once_never_raises_and_reaches_every_client_past_a_stuck_one():
    async def scenario():
        hub = WebSocketHub(snapshot_provider=lambda: SYNCED, view_model=EchoViewModel())

        # with nobody connected, publishing is a no-op
        hub.publish_delta(LINE_3_MAIN_TO_MINUS_6)
        hub.publish_state(SYNCED)
        hub.publish_status(ConnectionStatus.NO_DEVICE, None, frozenset())

        stuck, broken, phone, pc = StuckSocket(), BrokenSocket(), FakeSocket(), FakeSocket()
        for socket in (stuck, broken, phone, pc):
            await hub.register(socket)
        await settle()

        # each publish returns without awaiting any client, and the stuck and broken ones hold nobody up
        hub.publish_delta(LINE_3_MAIN_TO_MINUS_6)
        hub.publish_state(SYNCED)
        hub.publish_status(ConnectionStatus.READ_ONLY, "API version 10, expected 9", frozenset({SPDIF_LOOP}))
        await settle()
        expected = [
            {"type": "state", "snapshot": SYNCED},
            {"type": "delta", "deltas": LINE_3_MAIN_TO_MINUS_6},
            {"type": "state", "snapshot": SYNCED},
            {"type": "status", "status": ConnectionStatus.READ_ONLY, "read_only_reason": "API version 10, expected 9",
             "loops": frozenset({SPDIF_LOOP})},
        ]
        assert phone.sent == expected
        assert pc.sent == expected

    asyncio.run(scenario())


def test_both_open_browsers_receive_a_change_made_on_the_unit_at_once():
    async def scenario():
        hub = WebSocketHub(snapshot_provider=lambda: SYNCED, view_model=EchoViewModel())
        phone, pc = FakeSocket(), FakeSocket()
        await hub.register(phone)
        await hub.register(pc)
        await settle()

        # Line 3 on Main 1-2 changed to −6 dB on the unit: one turn of the loop puts it on both sockets
        hub.publish_delta(LINE_3_MAIN_TO_MINUS_6)
        await asyncio.sleep(0)
        assert phone.sent[1:] == [{"type": "delta", "deltas": LINE_3_MAIN_TO_MINUS_6}]
        assert pc.sent[1:] == [{"type": "delta", "deltas": LINE_3_MAIN_TO_MINUS_6}]

    asyncio.run(scenario())


def test_a_page_opened_after_an_hour_with_no_browsers_starts_from_the_current_state():
    at_start = Snapshot(state=MixerState(), status=ConnectionStatus.SYNCING, read_only_detail=None, loops=frozenset())
    line_3_at_minus_10 = MixerState()
    line_3_at_minus_10.apply(Report(Address(1016, 2), Gain.from_db(-10.0).to_824().to_bytes(4, "big", signed=True)))
    an_hour_later = Snapshot(state=line_3_at_minus_10, status=ConnectionStatus.SYNCED, read_only_detail=None, loops=frozenset())
    current = [at_start]

    async def scenario():
        hub = WebSocketHub(snapshot_provider=lambda: current[0], view_model=EchoViewModel())

        # nobody listens while the unit changes; then the page opens
        hub.publish_delta(LINE_3_MAIN_TO_MINUS_6)
        current[0] = an_hour_later
        phone = FakeSocket()
        await hub.register(phone)
        await settle()
        assert phone.sent == [{"type": "state", "snapshot": an_hour_later}]
        assert round(phone.sent[0]["snapshot"].state.gain(MixCell(0, 2)).db, 1) == -10.0

    asyncio.run(scenario())


def test_meters_reach_every_reading_client_and_a_stuck_client_holds_only_a_few():
    from _motu_mixer.presentation.ws_hub import METER_BACKLOG

    async def scenario():
        hub = WebSocketHub(snapshot_provider=lambda: SYNCED, view_model=EchoViewModel())
        stuck, phone = StuckSocket(), FakeSocket()
        stuck_id = await hub.register(stuck)
        await hub.register(phone)
        await settle()
        for n in range(200):                          # ~12 s of meter frames
            hub.publish_meters(n)
            await settle()
        hub.publish_delta(LINE_3_MAIN_TO_MINUS_6)     # a state change is never dropped, even for the stuck client
        await settle()
        meters = [m["levels"] for m in phone.sent if m["type"] == "meters"]
        assert meters == list(range(200))
        assert phone.sent[-1] == {"type": "delta", "deltas": LINE_3_MAIN_TO_MINUS_6}
        stuck_queue = hub._clients[stuck_id][0]
        assert stuck_queue.qsize() <= METER_BACKLOG + 1
        assert any(m.get("type") == "delta" for m in list(stuck_queue._queue))

    asyncio.run(scenario())
