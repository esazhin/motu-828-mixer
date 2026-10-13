from _motu_mixer.application.mixer_service import MixerService
from _motu_mixer.domain.loop_guard import Accepted
from _motu_mixer.domain.preflight import GateOpen
from _motu_mixer.domain.values import Address, Delta, Gain, Report, Write

LINE_3_MAIN_1 = Address(1016, 2)
ADAT_B_8_REVERB_1 = Address(1016, 643)
REPORTS = {
    b"line 3 at 0 dB": Report(LINE_3_MAIN_1, bytes.fromhex("01000000")),
    b"line 3 at 0 dB again": Report(LINE_3_MAIN_1, bytes.fromhex("01000000")),
    b"line 3 at -10 dB": Report(LINE_3_MAIN_1, bytes.fromhex("0050f44e")),
}


class StateDouble:
    """Stands in for MixerState: canned answers for apply, with_writes and raw reads; records each call."""

    def __init__(self):
        self.apply_answers = []
        self.after = None
        self.raws = {}
        self.applied = []
        self.with_writes_asked = []

    def apply(self, report):
        self.applied.append(report)
        return self.apply_answers.pop(0) if self.apply_answers else None

    def with_writes(self, writes):
        self.with_writes_asked.append(writes)
        return self.after

    def raw(self, address):
        return self.raws.get(address)


class Splitter:
    def feed(self, chunk):
        return [chunk]


class Publisher:
    def __init__(self):
        self.deltas, self.states, self.statuses = [], [], []

    def publish_delta(self, deltas):
        self.deltas.append(tuple(deltas))

    def publish_state(self, snapshot):
        self.states.append(snapshot)

    def publish_status(self, status, detail, loops):
        self.statuses.append((status, detail, loops))


class Scheduler:
    def attach(self, port):
        pass

    def detach(self):
        pass

    def submit(self, writes):
        return (b"frame",)

    def sent_log(self, n):
        return ()


class Guard:
    def __init__(self):
        self.checked = []

    def check(self, before, after, writes):
        self.checked.append((before, after, writes))
        return Accepted()

    def paths(self, state):
        return frozenset()


def test_service_keeps_one_fresh_state_per_session_and_publishes_its_deltas():
    created = []

    def new_state():
        created.append(StateDouble())
        return created[-1]

    publisher, guard = Publisher(), Guard()
    write = Write(ADAT_B_8_REVERB_1, Gain.from_db(-6.0))
    planned = (write,)
    service = MixerService(
        publisher, Scheduler(),
        new_state=new_state, new_splitter=Splitter, decode=REPORTS.__getitem__,
        write_gate=lambda state: GateOpen(), plan=lambda action, state: planned, guard=guard,
    )

    service.begin_session("port")
    first = created[-1]
    changed = Delta(LINE_3_MAIN_1, None, bytes.fromhex("01000000"))
    first.apply_answers = [changed, None]

    # every report reaches apply; a Delta is published, None publishes nothing
    service.on_device_bytes(b"line 3 at 0 dB")
    service.on_device_bytes(b"line 3 at 0 dB again")
    assert first.applied == [REPORTS[b"line 3 at 0 dB"], REPORTS[b"line 3 at 0 dB again"]]
    assert publisher.deltas == [(changed,)]

    # the guard judges the with_writes copy; the accepted copy becomes the live state,
    # and a cell not yet received on the original reads None, so its delta has no old value
    after = StateDouble()
    after.raws = {ADAT_B_8_REVERB_1: bytes.fromhex("00804dce")}
    first.after = after
    service.request_change("set ADAT B 8 on Reverb 1 to -6 dB", "phone")
    assert first.with_writes_asked == [planned]
    assert guard.checked == [(first, after, planned)]
    assert publisher.deltas[-1] == (Delta(ADAT_B_8_REVERB_1, None, bytes.fromhex("00804dce")),)

    service.on_device_bytes(b"line 3 at -10 dB")
    assert after.applied == [REPORTS[b"line 3 at -10 dB"]]
    assert first.applied == [REPORTS[b"line 3 at 0 dB"], REPORTS[b"line 3 at 0 dB again"]]

    # a new session starts from a fresh state
    service.end_session()
    service.begin_session("port")
    second = created[-1]
    assert second is not first and second is not after
    service.on_device_bytes(b"line 3 at 0 dB")
    assert second.applied == [REPORTS[b"line 3 at 0 dB"]]
    assert after.applied == [REPORTS[b"line 3 at -10 dB"]]


def test_service_asks_the_write_gate_before_planning_and_after_identity_or_optical_reports():
    from _motu_mixer.domain.status import ConnectionStatus, Refusal, RefusalKind

    asked = []
    gate_answers = [GateOpen()]
    state = StateDouble()
    state.after = state
    state.raws = {ADAT_B_8_REVERB_1: bytes.fromhex("00804dce")}
    planned = (Write(ADAT_B_8_REVERB_1, Gain.from_db(-6.0)),)
    reports = {
        b"api version 10": Report(Address(8, 0), bytes.fromhex("000a")),
        b"optical mode toslink": Report(Address(5006, 0), bytes.fromhex("01")),
    }

    def gate(seen):
        asked.append(("gate", seen))
        return gate_answers[-1]

    def plan(action, seen):
        asked.append(("plan", seen))
        return planned

    class RecordingScheduler(Scheduler):
        def submit(self, writes):
            asked.append(("submit", writes))
            return (b"frame",)

    publisher = Publisher()
    service = MixerService(
        publisher, RecordingScheduler(),
        new_state=lambda: state, new_splitter=Splitter, decode=reports.__getitem__,
        write_gate=gate, plan=plan, guard=Guard(),
    )
    service.begin_session("port")
    service.set_connection_status(ConnectionStatus.SYNCING, None)

    # an open gate is asked about the live state before the plan, and the change goes out
    service.request_change("set ADAT B 8 on Reverb 1 to -6 dB", "phone")
    assert asked[:3] == [("gate", state), ("plan", state), ("submit", planned)]

    # a closed gate refuses the change: no plan, nothing submitted
    for refusal in (Refusal(RefusalKind.NOT_SYNCED, "syncing"),
                    Refusal(RefusalKind.READ_ONLY, "API version 10, expected 9")):
        asked.clear()
        gate_answers.append(refusal)
        outcome = service.request_change("set ADAT B 8 on Reverb 1 to -6 dB", "phone")
        assert outcome.refusal == refusal
        assert asked == [("gate", state)]

    # an applied identity report is judged by the gate, and a read-only answer reaches the status
    asked.clear()
    state.apply_answers = [Delta(Address(8, 0), bytes.fromhex("0009"), bytes.fromhex("000a"))]
    service.on_device_bytes(b"api version 10")
    assert ("gate", state) in asked
    status, detail, _loops = publisher.statuses[-1]
    assert (status, detail) == (ConnectionStatus.READ_ONLY, "API version 10, expected 9")

    # so is an applied optical report
    asked.clear()
    state.apply_answers = [Delta(Address(5006, 0), bytes.fromhex("00"), bytes.fromhex("01"))]
    service.on_device_bytes(b"optical mode toslink")
    assert ("gate", state) in asked


def test_service_feeds_chunks_to_one_splitter_per_session_and_acts_on_each_decoded_frame():
    from _motu_mixer.protocol.sysex import Ignored, ProbeReply

    line_3_at_0_db = Report(LINE_3_MAIN_1, bytes.fromhex("01000000"))
    splitters = []

    class RecordingSplitter:
        """Stands in for SysexSplitter: records every chunk fed and yields the canned frames for it."""

        frames_for = {
            b"\xf0\x00\x00\x3b\x00\x01": [],
            b"\x41\xf7": [b"probe reply"],
            b"cut short": [],
            b"report then foreign": [b"line 3 report", b"foreign frame"],
        }

        def __init__(self):
            self.fed = []
            splitters.append(self)

        def feed(self, chunk):
            self.fed.append(chunk)
            return list(self.frames_for[chunk])

    decoded = []
    answers = {b"probe reply": ProbeReply(), b"line 3 report": line_3_at_0_db, b"foreign frame": Ignored()}

    def decode(frame):
        decoded.append(frame)
        return answers[frame]

    state = StateDouble()
    publisher = Publisher()
    service = MixerService(
        publisher, Scheduler(),
        new_state=lambda: state, new_splitter=RecordingSplitter, decode=decode,
        write_gate=lambda seen: GateOpen(), plan=lambda action, seen: (), guard=Guard(),
    )
    service.begin_session("port")
    assert len(splitters) == 1

    # a probe reply split across two chunks: the first yields no frame, the second yields it
    first = service.on_device_bytes(b"\xf0\x00\x00\x3b\x00\x01")
    assert decoded == [] and not first.probe_replied
    second = service.on_device_bytes(b"\x41\xf7")
    assert decoded == [b"probe reply"] and second.probe_replied
    assert state.applied == []

    # a truncated frame the splitter dropped yields nothing to decode
    assert not service.on_device_bytes(b"cut short").probe_replied
    assert decoded == [b"probe reply"]

    # frames are decoded in the order yielded: the Report is applied, Ignored changes nothing
    changed = Delta(LINE_3_MAIN_1, None, bytes.fromhex("01000000"))
    state.apply_answers = [changed]
    events = service.on_device_bytes(b"report then foreign")
    assert decoded == [b"probe reply", b"line 3 report", b"foreign frame"]
    assert state.applied == [line_3_at_0_db]
    assert publisher.deltas == [(changed,)]
    assert not events.probe_replied

    # chunks reached the one splitter of this session, in arrival order
    assert splitters[0].fed == [
        b"\xf0\x00\x00\x3b\x00\x01", b"\x41\xf7", b"cut short", b"report then foreign",
    ]

    # a new session feeds a fresh splitter
    service.end_session()
    service.begin_session("port")
    assert len(splitters) == 2
    service.on_device_bytes(b"cut short")
    assert splitters[1].fed == [b"cut short"]
    assert len(splitters[0].fed) == 4


def test_service_plans_only_through_an_open_gate_and_passes_planned_writes_to_the_guard():
    from _motu_mixer.domain.status import Refusal, RefusalKind

    planned_asked = []
    plan_answers = []
    gate_answers = [GateOpen()]
    submitted = []
    state = StateDouble()
    after = StateDouble()
    after.raws = {ADAT_B_8_REVERB_1: bytes.fromhex("00804dce")}
    state.after = after
    writes = (Write(ADAT_B_8_REVERB_1, Gain.from_db(-6.0)),)

    def plan(action, seen):
        planned_asked.append((action, seen))
        return plan_answers[-1]

    class RecordingScheduler(Scheduler):
        def submit(self, planned):
            submitted.append(planned)
            return (b"frame",)

    guard = Guard()
    service = MixerService(
        Publisher(), RecordingScheduler(),
        new_state=lambda: state, new_splitter=Splitter, decode=REPORTS.__getitem__,
        write_gate=lambda seen: gate_answers[-1], plan=plan, guard=guard,
    )
    service.begin_session("port")

    # a closed gate: plan is never asked
    gate_answers.append(Refusal(RefusalKind.NOT_SYNCED, "syncing"))
    plan_answers.append(writes)
    service.request_change("set ADAT B 8 on Reverb 1 to -6 dB", "phone")
    assert planned_asked == []
    gate_answers.append(GateOpen())

    # each refusal kind plan gives is returned as Refused, with no guard check and nothing submitted
    for refusal in (
        Refusal(RefusalKind.ODD_LINK_INDEX, "only even indexes can be linked"),
        Refusal(RefusalKind.SLOT_NOT_WRITABLE, "slot 86 cannot be changed here"),
        Refusal(RefusalKind.NOT_WRITABLE_PROPERTY, "property 5004 cannot be changed here"),
        Refusal(RefusalKind.INDEX_RANGE, "bus 16 does not exist"),
    ):
        plan_answers.append(refusal)
        outcome = service.request_change("a refused action", "pc")
        assert outcome.refusal == refusal
        assert planned_asked[-1] == ("a refused action", state)
    assert guard.checked == []
    assert submitted == []

    # planned writes go on to the guard, judged on the with_writes copy, then out to the scheduler
    plan_answers.append(writes)
    outcome = service.request_change("set ADAT B 8 on Reverb 1 to -6 dB", "phone")
    assert planned_asked[-1] == ("set ADAT B 8 on Reverb 1 to -6 dB", state)
    assert state.with_writes_asked == [writes]
    assert guard.checked == [(state, after, writes)]
    assert submitted == [writes]
    assert outcome.sent == (b"frame",)


def test_service_submits_only_what_the_loop_guard_accepts_and_publishes_the_loops_it_finds():
    from _motu_mixer.domain.status import ConnectionStatus, LoopPath, PathNode, Refusal, RefusalKind
    from _motu_mixer.domain.values import BusIndex, OutputSlot, Source

    through_ana_1_2 = LoopPath((
        PathNode(Source(58), "S/PDIF in"), PathNode(BusIndex(2), "Ana 1-2"), PathNode(OutputSlot(58), "S/PDIF out"),
    ))
    spdif_l_ana_1 = Address(1016, 2 * 44 + 10)

    class LoopGuardDouble:
        """Stands in for the loop guard: canned check and paths answers; records each call."""

        def __init__(self):
            self.check_answers = []
            self.paths_answers = []
            self.checked = []
            self.paths_asked = []

        def check(self, before, after, writes):
            self.checked.append((before, after, writes))
            return self.check_answers.pop(0)

        def paths(self, state):
            self.paths_asked.append(state)
            return self.paths_answers.pop(0) if self.paths_answers else frozenset()

    reports = {
        b"line 3 at 0 dB": Report(LINE_3_MAIN_1, bytes.fromhex("01000000")),
        b"spdif l on ana 1 at -6 dB": Report(spdif_l_ana_1, bytes.fromhex("00804dce")),
        b"spdif l on ana 1 at -inf": Report(spdif_l_ana_1, bytes.fromhex("00000000")),
    }
    submitted = []

    class RecordingScheduler(Scheduler):
        def submit(self, writes):
            submitted.append(writes)
            return (b"frame",)

    state = StateDouble()
    publisher, guard = Publisher(), LoopGuardDouble()
    service = MixerService(
        publisher, RecordingScheduler(),
        new_state=lambda: state, new_splitter=Splitter, decode=reports.__getitem__,
        write_gate=lambda seen: GateOpen(), plan=lambda action, seen: planned, guard=guard,
    )
    service.begin_session("port")
    service.set_connection_status(ConnectionStatus.SYNCED, None)

    # after an applied report the guard is asked for the paths of the live state; none means no loop status
    state.apply_answers = [Delta(LINE_3_MAIN_1, None, bytes.fromhex("01000000"))]
    guard.paths_answers = [frozenset()]
    statuses_before = len(publisher.statuses)
    service.on_device_bytes(b"line 3 at 0 dB")
    assert guard.paths_asked == [state]
    assert len(publisher.statuses) == statuses_before

    # a report that creates a loop: the status is published with that loop
    state.apply_answers = [Delta(spdif_l_ana_1, bytes.fromhex("00000000"), bytes.fromhex("00804dce"))]
    guard.paths_answers = [frozenset({through_ana_1_2})]
    service.on_device_bytes(b"spdif l on ana 1 at -6 dB")
    assert guard.paths_asked == [state, state]
    status, detail, loops = publisher.statuses[-1]
    assert (status, detail) == (ConnectionStatus.SYNCED, None)
    assert set(loops) == {through_ana_1_2}

    # the loop goes away: the status is published again with no loops
    state.apply_answers = [Delta(spdif_l_ana_1, bytes.fromhex("00804dce"), bytes.fromhex("00000000"))]
    guard.paths_answers = [frozenset()]
    service.on_device_bytes(b"spdif l on ana 1 at -inf")
    assert guard.paths_asked == [state, state, state]
    status, detail, loops = publisher.statuses[-1]
    assert status == ConnectionStatus.SYNCED and set(loops) == set()

    # a change the guard refuses with a loop path: Refused with that refusal, nothing submitted or published
    after = StateDouble()
    after.raws = {spdif_l_ana_1: bytes.fromhex("00804dce")}
    state.after = after
    planned = (Write(spdif_l_ana_1, Gain.from_db(-6.0)),)
    refusal = Refusal(RefusalKind.LOOP, "S/PDIF in → Ana 1-2 → S/PDIF out", through_ana_1_2)
    guard.check_answers = [refusal]
    deltas_before = list(publisher.deltas)
    outcome = service.request_change("raise S/PDIF L/R on Ana 1-2 to -6 dB", "phone")
    assert guard.checked == [(state, after, planned)]
    assert outcome.refusal == refusal
    assert submitted == []
    assert publisher.deltas == deltas_before

    # a change the guard accepts goes out to the scheduler
    guard.check_answers = [Accepted()]
    outcome = service.request_change("raise S/PDIF L/R on Ana 1-2 to -6 dB", "phone")
    assert guard.checked[-1] == (state, after, planned)
    assert submitted == [planned]
    assert outcome.sent == (b"frame",)


def test_service_drives_the_write_scheduler_per_session_and_only_with_accepted_writes():
    from _motu_mixer.domain.status import LoopPath, PathNode, Refusal, RefusalKind
    from _motu_mixer.domain.values import BusIndex, OutputSlot, SentFrame, Source

    class SchedulerDouble:
        """Stands in for the write scheduler: canned frames and sent log; records each call in order."""

        def __init__(self):
            self.calls = []
            self.frames = (bytes.fromhex("f000003b0001000a0378020300040005004d4ef7"),)
            self.log = (SentFrame(1.0, b"\xf0\x01\xf7"), SentFrame(2.0, b"\xf0\x02\xf7"))

        def attach(self, port):
            self.calls.append(("attach", port))

        def detach(self):
            self.calls.append(("detach",))

        def submit(self, writes):
            self.calls.append(("submit", writes))
            return self.frames

        def sent_log(self, n):
            self.calls.append(("sent_log", n))
            return self.log[-n:]

    class GuardDouble(Guard):
        def __init__(self):
            super().__init__()
            self.answers = []

        def check(self, before, after, writes):
            super().check(before, after, writes)
            return self.answers.pop(0)

    state = StateDouble()
    state.after = state
    state.raws = {ADAT_B_8_REVERB_1: bytes.fromhex("00804dce")}
    planned = (Write(ADAT_B_8_REVERB_1, Gain.from_db(-6.0)),)
    scheduler, guard = SchedulerDouble(), GuardDouble()
    service = MixerService(
        Publisher(), scheduler,
        new_state=lambda: state, new_splitter=Splitter, decode=REPORTS.__getitem__,
        write_gate=lambda seen: GateOpen(), plan=lambda action, seen: planned, guard=guard,
    )

    # begin_session attaches the scheduler to that session's port
    service.begin_session("first port")
    assert scheduler.calls == [("attach", "first port")]

    # a change the guard refuses is never submitted
    loop = LoopPath((PathNode(Source(58), "S/PDIF in"), PathNode(BusIndex(2), "Ana 1-2"),
                     PathNode(OutputSlot(58), "S/PDIF out")))
    guard.answers = [Refusal(RefusalKind.LOOP, "S/PDIF in → Ana 1-2 → S/PDIF out", loop)]
    service.request_change("raise S/PDIF L/R on Ana 1-2 to -6 dB", "phone")
    assert scheduler.calls == [("attach", "first port")]

    # an accepted change submits exactly the planned writes, and the frames submit answers are what is sent
    guard.answers = [Accepted()]
    outcome = service.request_change("set ADAT B 8 on Reverb 1 to -6 dB", "phone")
    assert scheduler.calls[-1] == ("submit", planned)
    assert outcome.sent == scheduler.frames

    # the sent log is the scheduler's, asked with the caller's n
    assert tuple(service.sent_log(1)) == scheduler.log[-1:]
    assert scheduler.calls[-1] == ("sent_log", 1)

    # end_session detaches; a new session attaches the new port and re-sends nothing the old one dropped
    service.end_session()
    assert scheduler.calls[-1] == ("detach",)
    service.begin_session("second port")
    assert scheduler.calls[-1] == ("attach", "second port")
    assert [call for call in scheduler.calls if call[0] == "submit"] == [("submit", planned)]


def test_service_publishes_deltas_full_state_on_links_or_sync_and_status_on_status_or_loop_changes():
    from _motu_mixer.application.ports import Snapshot
    from _motu_mixer.domain.status import ConnectionStatus, LoopPath, PathNode, Refusal, RefusalKind
    from _motu_mixer.domain.values import BusIndex, OutputSlot, Source

    class CopyableState(StateDouble):
        def copy(self):
            return self

    class PathsGuard(Guard):
        def __init__(self):
            super().__init__()
            self.loops = frozenset()

        def paths(self, state):
            return self.loops

    through_ana_1_2 = LoopPath((
        PathNode(Source(58), "S/PDIF in"), PathNode(BusIndex(2), "Ana 1-2"), PathNode(OutputSlot(58), "S/PDIF out"),
    ))
    spdif_l_ana_1 = Address(1016, 2 * 44 + 10)
    model = Address(21, 0)
    reports = {
        b"model 828E": Report(model, b"828E\x00"),
        b"line 3 at 0 dB": Report(LINE_3_MAIN_1, bytes.fromhex("01000000")),
        b"line 3 at -10 dB": Report(LINE_3_MAIN_1, bytes.fromhex("0050f44e")),
        b"line 5-6 linked": Report(Address(1000, 4), b"\x01"),
        b"ana 3-4 linked": Report(Address(1001, 4), b"\x01"),
        b"spdif l on ana 1 at -6 dB": Report(spdif_l_ana_1, bytes.fromhex("00804dce")),
    }
    created = []

    def new_state():
        created.append(CopyableState())
        return created[-1]

    gate_answers = [Refusal(RefusalKind.NOT_SYNCED, "syncing")]
    planned = (Write(ADAT_B_8_REVERB_1, Gain.from_db(-6.0)),)
    publisher, guard = Publisher(), PathsGuard()
    service = MixerService(
        publisher, Scheduler(),
        new_state=new_state, new_splitter=Splitter, decode=reports.__getitem__,
        write_gate=lambda seen: gate_answers[-1], plan=lambda action, seen: planned, guard=guard,
    )
    service.begin_session("port")
    state = created[-1]

    # a status set by the session is published
    service.set_connection_status(ConnectionStatus.SYNCING, None)
    assert publisher.statuses[-1][0] == ConnectionStatus.SYNCING

    # the gate opens on an identity report: the session is synced, and the full state and the status go out
    gate_answers.append(GateOpen())
    states_before = len(publisher.states)
    state.apply_answers = [Delta(model, None, b"828E\x00")]
    service.on_device_bytes(b"model 828E")
    assert len(publisher.states) == states_before + 1
    assert isinstance(publisher.states[-1], Snapshot)
    assert publisher.states[-1].status == ConnectionStatus.SYNCED
    assert publisher.statuses[-1][0] == ConnectionStatus.SYNCED

    # a fader report publishes its delta, and neither the full state nor a status
    states_before, statuses_before = len(publisher.states), len(publisher.statuses)
    fader = Delta(LINE_3_MAIN_1, bytes.fromhex("01000000"), bytes.fromhex("0050f44e"))
    state.apply_answers = [fader]
    service.on_device_bytes(b"line 3 at -10 dB")
    assert publisher.deltas[-1] == (fader,)
    assert len(publisher.states) == states_before
    assert len(publisher.statuses) == statuses_before

    # an input link or a bus link report publishes the full state
    for chunk, address in ((b"line 5-6 linked", Address(1000, 4)), (b"ana 3-4 linked", Address(1001, 4))):
        states_before = len(publisher.states)
        state.apply_answers = [Delta(address, b"\x00", b"\x01")]
        service.on_device_bytes(chunk)
        assert len(publisher.states) == states_before + 1
        assert isinstance(publisher.states[-1], Snapshot)

    # a report that creates a loop publishes the status with that loop
    guard.loops = frozenset({through_ana_1_2})
    state.apply_answers = [Delta(spdif_l_ana_1, bytes.fromhex("00000000"), bytes.fromhex("00804dce"))]
    service.on_device_bytes(b"spdif l on ana 1 at -6 dB")
    status, detail, loops = publisher.statuses[-1]
    assert (status, detail) == (ConnectionStatus.SYNCED, None)
    assert set(loops) == {through_ana_1_2}

    # a later report leaving the loop set unchanged publishes no status
    statuses_before = len(publisher.statuses)
    state.apply_answers = [Delta(LINE_3_MAIN_1, bytes.fromhex("0050f44e"), bytes.fromhex("01000000"))]
    service.on_device_bytes(b"line 3 at 0 dB")
    assert len(publisher.statuses) == statuses_before

    # an accepted change publishes its delta at once
    after = CopyableState()
    after.raws = {ADAT_B_8_REVERB_1: bytes.fromhex("00804dce")}
    state.after = after
    outcome = service.request_change("set ADAT B 8 on Reverb 1 to -6 dB", "phone")
    assert outcome.sent == (b"frame",)
    assert publisher.deltas[-1] == (Delta(ADAT_B_8_REVERB_1, None, bytes.fromhex("00804dce")),)

    # a disconnect is a status change, and is published
    service.end_session()
    service.set_connection_status(ConnectionStatus.DISCONNECTED, None)
    assert publisher.statuses[-1][0] == ConnectionStatus.DISCONNECTED

    # a session the gate judges read-only publishes the full state and the read-only status
    service.begin_session("port")
    service.set_connection_status(ConnectionStatus.SYNCING, None)
    gate_answers.append(Refusal(RefusalKind.READ_ONLY, "API version 10, expected 9"))
    guard.loops = frozenset()
    states_before = len(publisher.states)
    created[-1].apply_answers = [Delta(model, None, b"828E\x00")]
    service.on_device_bytes(b"model 828E")
    assert len(publisher.states) == states_before + 1
    assert publisher.states[-1].status == ConnectionStatus.READ_ONLY
    assert publisher.statuses[-1][:2] == (ConnectionStatus.READ_ONLY, "API version 10, expected 9")


LINE_3_ANA_3 = Address(1016, 4 * 44 + 2)


class SubmitLog(Scheduler):
    def __init__(self):
        self.submitted = []

    def submit(self, writes):
        self.submitted.append(writes)
        return tuple(b"frame for %d" % len(self.submitted) for _ in writes)


def level_planner(action, state):
    """Plans ('level', db) as one write of that gain to Line 3 on Ana 3."""
    return (Write(LINE_3_ANA_3, Gain.from_db(action[1])),)


def synced_service(publisher, scheduler):
    from _motu_mixer.domain.state import MixerState
    from _motu_mixer.domain.status import ConnectionStatus

    service = MixerService(
        publisher, scheduler,
        new_state=MixerState, new_splitter=Splitter, decode=REPORTS.__getitem__,
        write_gate=lambda state: GateOpen(), plan=level_planner, guard=Guard(),
    )
    service.begin_session("port")
    service.set_connection_status(ConnectionStatus.SYNCED, None)
    return service


def published_db(delta):
    return round(Gain.from_824(int.from_bytes(delta.new, "big", signed=True)).db, 1)


def test_two_clients_setting_one_cell_in_turn_end_at_the_later_value():
    publisher, scheduler = Publisher(), SubmitLog()
    service = synced_service(publisher, scheduler)

    service.request_change(("level", -6.0), "phone")
    service.request_change(("level", -3.0), "pc")

    assert [round(w[0].value.db, 1) for w in scheduler.submitted] == [-6.0, -3.0]
    assert [published_db(d[0]) for d in publisher.deltas] == [-6.0, -3.0]
    assert published_db(Delta(LINE_3_ANA_3, None, service.snapshot().state.raw(LINE_3_ANA_3))) == -3.0


def test_a_change_the_unit_does_not_echo_is_published_at_once_and_stays():
    publisher, scheduler = Publisher(), SubmitLog()
    service = synced_service(publisher, scheduler)

    service.request_change(("level", -6.0), "phone")
    assert len(publisher.deltas) == 1
    assert publisher.deltas[0][0].address == LINE_3_ANA_3
    assert published_db(publisher.deltas[0][0]) == -6.0

    # the unit goes on reporting other cells, never echoing the write
    service.on_device_bytes(b"line 3 at 0 dB")
    service.on_device_bytes(b"line 3 at -10 dB")
    assert all(d.address != LINE_3_ANA_3 for deltas in publisher.deltas[1:] for d in deltas)
    assert published_db(Delta(LINE_3_ANA_3, None, service.snapshot().state.raw(LINE_3_ANA_3))) == -6.0


def test_service_answers_each_change_with_accepted_or_refused_and_serves_its_snapshot_and_sent_log():
    from _motu_mixer.application.ports import ChangeOutcome, Snapshot
    from _motu_mixer.domain.status import ConnectionStatus, LoopPath, PathNode, Refusal, RefusalKind
    from _motu_mixer.domain.values import BusIndex, OutputSlot, SentFrame, Source

    calls = []
    gate_answers, plan_answers, check_answers = [GateOpen()], [], []
    loop = LoopPath((PathNode(Source(58), "S/PDIF in"), PathNode(BusIndex(2), "Ana 1-2"),
                     PathNode(OutputSlot(58), "S/PDIF out")))
    state = StateDouble()
    after = StateDouble()
    after.raws = {LINE_3_ANA_3: bytes.fromhex("00804dce")}
    state.after = after
    writes = (Write(LINE_3_ANA_3, Gain.from_db(-6.0)),)

    def gate(seen):
        calls.append(("gate", seen))
        return gate_answers.pop(0)

    def plan(action, seen):
        calls.append(("plan", action, seen))
        return plan_answers.pop(0)

    class CheckingGuard(Guard):
        def check(self, before, after, planned):
            calls.append(("check", before, after, planned))
            return check_answers.pop(0)

        def paths(self, seen):
            return frozenset({loop})

    class LoggingScheduler(Scheduler):
        log = tuple(SentFrame(float(t), b"\xf0%d\xf7" % t) for t in range(5))

        def submit(self, planned):
            calls.append(("submit", planned))
            return (b"\xf0frame\xf7",)

        def sent_log(self, n):
            return self.log[-n:]

    publisher = Publisher()
    service = MixerService(
        publisher, LoggingScheduler(),
        new_state=lambda: state, new_splitter=Splitter, decode=REPORTS.__getitem__,
        write_gate=gate, plan=plan, guard=CheckingGuard(),
    )
    service.begin_session("port")
    service.set_connection_status(ConnectionStatus.READ_ONLY, "API version 10, expected 9")

    # accepted: gate, plan, guard on the with_writes copy, then submit; the frames are the answer
    plan_answers.append(writes)
    check_answers.append(Accepted())
    outcome = service.request_change("set Line 3 on Ana 3 to -6 dB", None)
    assert outcome == ChangeOutcome.Accepted((b"\xf0frame\xf7",))
    assert calls == [("gate", state), ("plan", "set Line 3 on Ana 3 to -6 dB", state),
                     ("check", state, after, writes), ("submit", writes)]

    # each refusal comes back as Refused, never raised, with nothing submitted
    refusals = (
        Refusal(RefusalKind.READ_ONLY, "API version 10, expected 9"),
        Refusal(RefusalKind.ODD_LINK_INDEX, "only even indexes can be linked"),
        Refusal(RefusalKind.LOOP, "S/PDIF in → Ana 1-2 → S/PDIF out", loop),
    )
    gate_answers.extend([refusals[0], GateOpen(), GateOpen()])
    plan_answers.extend([refusals[1], writes])
    check_answers.append(refusals[2])
    calls.clear()
    outcomes = [service.request_change("a change", "pc") for _ in refusals]
    assert outcomes == [ChangeOutcome.Refused(r) for r in refusals]
    assert not any(call[0] == "submit" for call in calls)

    # the snapshot carries the state, status, read-only detail and loops; the sent log holds at most n frames
    snapshot = service.snapshot()
    assert isinstance(snapshot, Snapshot)
    assert (snapshot.status, snapshot.read_only_detail) == (ConnectionStatus.READ_ONLY, "API version 10, expected 9")
    assert snapshot.loops == frozenset({loop})
    assert service.sent_log(2) == LoggingScheduler.log[-2:]
    assert len(service.sent_log(50)) == 5


def test_service_starts_fresh_per_session_reports_probe_replies_and_publishes_each_status():
    from _motu_mixer.application.ports import DeviceEvents
    from _motu_mixer.domain.state import MixerState
    from _motu_mixer.domain.status import ConnectionStatus, Refusal, RefusalKind
    from _motu_mixer.protocol.sysex import ProbeReply

    calls = []
    gate_answers = [Refusal(RefusalKind.NOT_SYNCED, "syncing")]
    frames = {
        b"probe reply": ProbeReply(),
        b"line 3 at 0 dB": Report(LINE_3_MAIN_1, bytes.fromhex("01000000")),
        b"model 828E": Report(Address(21, 0), b"828E\x00"),
    }

    class SessionScheduler(Scheduler):
        def attach(self, port):
            calls.append(("attach", port))

        def detach(self):
            calls.append(("detach",))

    publisher = Publisher()
    service = MixerService(
        publisher, SessionScheduler(),
        new_state=MixerState, new_splitter=Splitter, decode=frames.__getitem__,
        write_gate=lambda state: gate_answers[-1], plan=level_planner, guard=Guard(),
    )

    # begin_session attaches the scheduler; the probe reply is reported
    service.begin_session("first port")
    assert calls == [("attach", "first port")]
    assert service.on_device_bytes(b"probe reply") == DeviceEvents(probe_replied=True)
    assert service.on_device_bytes(b"line 3 at 0 dB") == DeviceEvents(probe_replied=False)
    assert service.snapshot().state.raw(LINE_3_MAIN_1) == bytes.fromhex("01000000")

    # each status the session sets is published
    service.set_connection_status(ConnectionStatus.SYNCING, None)
    assert publisher.statuses[-1][:2] == (ConnectionStatus.SYNCING, None)

    # once the gate opens the published status is SYNCED, and the bytes' events say the unit is settled
    gate_answers.append(GateOpen())
    assert service.on_device_bytes(b"model 828E") == DeviceEvents(probe_replied=False, settled=True)
    assert publisher.statuses[-1][:2] == (ConnectionStatus.SYNCED, None)

    # end_session detaches; the next session starts from a fresh state
    service.end_session()
    assert calls[-1] == ("detach",)
    service.set_connection_status(ConnectionStatus.DISCONNECTED, None)
    assert publisher.statuses[-1][:2] == (ConnectionStatus.DISCONNECTED, None)
    service.begin_session("second port")
    assert calls[-1] == ("attach", "second port")
    assert service.snapshot().state.raw(LINE_3_MAIN_1) is None

    # a gate that judges the unit read-only makes the published status READ_ONLY with its detail
    service.set_connection_status(ConnectionStatus.SYNCING, None)
    gate_answers.append(Refusal(RefusalKind.READ_ONLY, "model 828X, expected 828E"))
    assert service.on_device_bytes(b"model 828E") == DeviceEvents(probe_replied=False, settled=True)
    assert publisher.statuses[-1][:2] == (ConnectionStatus.READ_ONLY, "model 828X, expected 828E")
    assert publisher.states[-1].status == ConnectionStatus.READ_ONLY


def test_service_splits_decodes_and_applies_device_bytes_and_publishes_what_changed():
    from _motu_mixer.protocol.sysex import Ignored, ProbeReply

    fed = []

    class TwoFrameSplitter:
        def feed(self, chunk):
            fed.append(chunk)
            return [b"line 3 at 0 dB", b"foreign", b"probe reply"]

    answers = {b"line 3 at 0 dB": REPORTS[b"line 3 at 0 dB"], b"foreign": Ignored(), b"probe reply": ProbeReply()}
    state = StateDouble()
    changed = Delta(LINE_3_MAIN_1, None, bytes.fromhex("01000000"))
    state.apply_answers = [changed, None]
    publisher = Publisher()
    service = MixerService(
        publisher, Scheduler(),
        new_state=lambda: state, new_splitter=TwoFrameSplitter, decode=answers.__getitem__,
        write_gate=lambda seen: GateOpen(), plan=level_planner, guard=Guard(),
    )
    service.begin_session("port")

    assert service.on_device_bytes(b"chunk").probe_replied
    assert fed == [b"chunk"]
    assert state.applied == [REPORTS[b"line 3 at 0 dB"]]
    assert publisher.deltas == [(changed,)]

    # the same report again changes nothing, so nothing is published
    service.on_device_bytes(b"chunk")
    assert publisher.deltas == [(changed,)]


def test_meter_frames_are_published_as_levels_and_never_enter_the_state():
    from _motu_mixer.domain.meters import MeterLevels, meter_levels
    from _motu_mixer.domain.state import MixerState
    from _motu_mixer.domain.status import ConnectionStatus

    class MeterPublisher(Publisher):
        def __init__(self):
            super().__init__()
            self.meters = []

        def publish_meters(self, levels):
            self.meters.append(levels)

    first, second = bytes([200] * 128), bytes([100] * 128)
    frames = {
        b"meters 1": Report(Address(6000, 0), first),
        b"meters 2": Report(Address(6000, 0), second),
        b"line 3 at 0 dB": Report(LINE_3_MAIN_1, bytes.fromhex("01000000")),
    }
    publisher = MeterPublisher()
    service = MixerService(
        publisher, Scheduler(),
        new_state=MixerState, new_splitter=Splitter, decode=frames.__getitem__,
        write_gate=lambda state: GateOpen(), plan=level_planner, guard=Guard(),
    )
    service.begin_session("port")
    service.set_connection_status(ConnectionStatus.SYNCING, None)

    service.on_device_bytes(b"meters 1")
    service.on_device_bytes(b"meters 2")
    assert publisher.meters == [meter_levels(first), meter_levels(second)]
    assert isinstance(publisher.meters[0], MeterLevels)
    assert publisher.deltas == []                                     # a meter frame is not a state change
    assert service.snapshot().state.raw(Address(6000, 0)) is None

    # ordinary reports still flow into the state as before
    service.on_device_bytes(b"line 3 at 0 dB")
    assert service.snapshot().state.raw(LINE_3_MAIN_1) == bytes.fromhex("01000000")


def test_an_eq_change_runs_through_the_real_planner_state_and_guard_and_reaches_the_scheduler():
    # Integration over the real domain pieces (planner, state, S/PDIF guard, write gate); only the port is a fake.
    # It catches what per-piece tests against doubles could not: the EQ gain float crashing MixerState._encode.
    from _motu_mixer.application.ports import ChangeOutcome
    from _motu_mixer.domain.actions import SetProperty
    from _motu_mixer.domain.state import MixerState

    class RecordingScheduler(Scheduler):
        def __init__(self):
            self.submitted = []

        def submit(self, writes):
            self.submitted.append(tuple(writes))
            return (b"frame",)

    scheduler = RecordingScheduler()
    service = MixerService(Publisher(), scheduler, write_gate=lambda state: GateOpen())
    service.begin_session("port")
    for action, expected in [(SetProperty(1025, 3, -7.5), -7.5), (SetProperty(1026, 3, 2.25), 2.25),
                             (SetProperty(1024, 3, 500), 500), (SetProperty(1023, 3, True), 1)]:
        outcome = service.request_change(action, None)
        assert isinstance(outcome, ChangeOutcome.Accepted), outcome
        assert scheduler.submitted[-1] == (Write(Address(action.prop, action.index), expected),)
    assert isinstance(service.request_change(SetProperty(1025, 3, -30), None), ChangeOutcome.Refused)
