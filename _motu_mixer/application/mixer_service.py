"""MixerService: the one owner of the unit's MixerState, between the device session, the planner and the clients."""
from _motu_mixer.domain.actions import plan as plan_action
from _motu_mixer.domain.loop_guard import SpdifLoopGuard
from _motu_mixer.domain.preflight import GateOpen, write_gate as gate_writes
from _motu_mixer.domain.meters import METERS_PROP, meter_levels
from _motu_mixer.domain.properties import PropertyId as P
from _motu_mixer.domain.state import MixerState
from _motu_mixer.domain.status import ConnectionStatus, Refusal, RefusalKind
from _motu_mixer.domain.values import Delta, Report
from _motu_mixer.protocol.sysex import ProbeReply, SysexSplitter, decode as decode_frame

from .ports import ChangeOutcome, DeviceEvents, Snapshot

_LINKS = (P.INPUT_LINK, P.BUS_LINK)
_GATED = (P.MODEL_ID, P.API_VERSION, P.DEV_NAME, P.DEV_UID, P.OPTICAL_MODE, P.OPTICAL_EXPANDER)
_JUDGED = (ConnectionStatus.SYNCING, ConnectionStatus.SYNCED, ConnectionStatus.READ_ONLY)
_SETTLED = (ConnectionStatus.SYNCED, ConnectionStatus.READ_ONLY)


class MixerService:
    def __init__(
        self, publisher, scheduler, *, new_state=MixerState, new_splitter=SysexSplitter, decode=decode_frame,
        write_gate=gate_writes, plan=plan_action, guard=None,
    ):
        self._publisher = publisher
        self._scheduler = scheduler
        self._new_state = new_state
        self._new_splitter = new_splitter
        self._decode = decode
        self._write_gate = write_gate
        self._plan = plan
        self._guard = guard if guard is not None else SpdifLoopGuard()
        self._state = new_state()
        self._splitter = None
        self._status = ConnectionStatus.NO_DEVICE
        self._detail = None
        self._loops = frozenset()

    def begin_session(self, port):
        self._state = self._new_state()
        self._splitter = self._new_splitter()
        self._loops = frozenset()
        self._scheduler.attach(port)

    def end_session(self):
        self._scheduler.detach()
        self._state = self._new_state()
        self._splitter = None
        self._loops = frozenset()

    def set_connection_status(self, status, detail):
        self._status, self._detail = status, detail
        self._publisher.publish_status(status, detail, self._loops)

    def on_device_bytes(self, chunk) -> DeviceEvents:
        probe_replied = False
        deltas = []
        meters = None
        for frame in self._splitter.feed(chunk):
            message = self._decode(frame)
            if isinstance(message, ProbeReply):
                probe_replied = True
            elif isinstance(message, Report) and message.address.prop == METERS_PROP:
                meters = message.raw          # live levels, ~17/s: published, never part of the mixer state
            elif isinstance(message, Report):
                delta = self._state.apply(message)
                if delta is not None:
                    deltas.append(delta)
        if deltas:
            self._after_change(deltas, judge=any(d.address.prop in _GATED for d in deltas))
        if meters is not None:
            self._publisher.publish_meters(meter_levels(meters))
        return DeviceEvents(probe_replied=probe_replied, settled=self._status in _SETTLED)

    def request_change(self, action, client_id):
        gate = self._write_gate(self._state)
        if isinstance(gate, Refusal):
            return ChangeOutcome.Refused(gate)
        writes = self._plan(action, self._state)
        if isinstance(writes, Refusal):
            return ChangeOutcome.Refused(writes)
        before = self._state
        after = before.with_writes(writes)
        verdict = self._guard.check(before, after, writes)
        if isinstance(verdict, Refusal):
            return ChangeOutcome.Refused(verdict)
        sent = self._scheduler.submit(writes)
        self._state = after
        addresses = dict.fromkeys(write.address for write in writes)
        deltas = [
            Delta(address, before.raw(address), after.raw(address))
            for address in addresses if before.raw(address) != after.raw(address)
        ]
        if deltas:
            self._after_change(deltas, judge=False)
        return ChangeOutcome.Accepted(sent)

    def snapshot(self) -> Snapshot:
        return Snapshot(self._state.with_writes(()), self._status, self._detail, self._loops)

    def sent_log(self, n):
        return self._scheduler.sent_log(n)

    def _after_change(self, deltas, judge):
        self._publisher.publish_delta(tuple(deltas))
        status, detail = self._next_status(judge)
        loops = self._next_loops(status)
        publish_state = any(d.address.prop in _LINKS for d in deltas) or self._settles(status, detail)
        changed = (status, detail, loops) != (self._status, self._detail, self._loops)
        self._status, self._detail, self._loops = status, detail, loops
        self._publish(publish_state, changed)

    def _next_status(self, judge):
        rejudge = judge or self._status is ConnectionStatus.SYNCING
        if rejudge and self._status in _JUDGED:
            return self._judge()
        return self._status, self._detail

    def _next_loops(self, status):
        """The loops of the live state; not looked for while the full report is still arriving."""
        if status is ConnectionStatus.SYNCING:
            return self._loops
        return self._guard.paths(self._state)

    def _settles(self, status, detail):
        return status in _SETTLED and (status, detail) != (self._status, self._detail)

    def _publish(self, state, status):
        if state:
            self._publisher.publish_state(self.snapshot())
        if status:
            self._publisher.publish_status(self._status, self._detail, self._loops)

    def _judge(self):
        gate = self._write_gate(self._state)
        if isinstance(gate, GateOpen):
            return ConnectionStatus.SYNCED, None
        if gate.kind is RefusalKind.READ_ONLY:
            return ConnectionStatus.READ_ONLY, gate.detail
        return self._status, self._detail
