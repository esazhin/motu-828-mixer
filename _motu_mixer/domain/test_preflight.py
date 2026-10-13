from _motu_mixer.domain.preflight import GateOpen, write_gate
from _motu_mixer.domain.properties import GATE_ARRAYS, PropertyId, spec
from _motu_mixer.domain.state import MixerState
from _motu_mixer.domain.status import Refusal, RefusalKind
from _motu_mixer.domain.values import Address, DeviceIdentity, OpticalConfig, OpticalMode

VERIFIED = DeviceIdentity("828E", "828E000000", "1.1.8+2458f7dace00", 9)
ADAT = OpticalConfig(OpticalMode.ADAT, OpticalMode.ADAT, 0)


class StateReads:
    """Stands in for MixerState: answers the three reads the gate makes and records each one asked."""

    def __init__(self, identity, optical, complete):
        self._identity, self._optical, self._complete = identity, optical, complete
        self.asked = []

    def identity(self):
        self.asked.append("identity")
        return self._identity

    def optical_config(self):
        self.asked.append("optical_config")
        return self._optical

    def arrays_complete(self):
        self.asked.append("arrays_complete")
        return self._complete


def _refusal(result):
    assert isinstance(result, Refusal)
    return result.kind, result.detail


def test_write_gate_opens_only_for_a_complete_verified_unit():
    complete = StateReads(VERIFIED, ADAT, True)
    assert isinstance(write_gate(complete), GateOpen)
    assert set(complete.asked) == {"identity", "optical_config", "arrays_complete"}

    # identity or optical props not all arrived, or arrays still filling: not synced yet
    for state in (StateReads(None, ADAT, True), StateReads(VERIFIED, None, True),
                  StateReads(VERIFIED, ADAT, False)):
        assert _refusal(write_gate(state))[0] is RefusalKind.NOT_SYNCED

    # a synced unit that differs from the verified one: read-only, naming the value
    other_model = DeviceIdentity("828X", "828E000000", "1.1.8+2458f7dace00", 9)
    assert _refusal(write_gate(StateReads(other_model, ADAT, True))) == (
        RefusalKind.READ_ONLY, "model 828X, expected 828E")
    newer_api = DeviceIdentity("828E", "828E000000", "1.2.0", 10)
    assert _refusal(write_gate(StateReads(newer_api, ADAT, True))) == (
        RefusalKind.READ_ONLY, "API version 10, expected 9")
    for optical in (OpticalConfig(OpticalMode.TOSLINK, OpticalMode.ADAT, 0),
                    OpticalConfig(OpticalMode.ADAT, OpticalMode.TOSLINK, 0),
                    OpticalConfig(OpticalMode.ADAT, OpticalMode.ADAT, 1)):
        assert _refusal(write_gate(StateReads(VERIFIED, optical, True))) == (
            RefusalKind.READ_ONLY, "unverified optical configuration")


def test_newer_api_version_makes_the_unit_read_only():
    newer_api = DeviceIdentity("828E", "828E000000", "1.2.0", 10)
    assert _refusal(write_gate(StateReads(newer_api, ADAT, True))) == (
        RefusalKind.READ_ONLY, "API version 10, expected 9")


def test_other_model_makes_the_unit_read_only():
    other_model = DeviceIdentity("828X", "828X0001AA", "1.1.8+2458f7dace00", 9)
    assert _refusal(write_gate(StateReads(other_model, ADAT, True))) == (
        RefusalKind.READ_ONLY, "model 828X, expected 828E")


def test_model_not_yet_reported_means_not_synced():
    assert _refusal(write_gate(StateReads(None, ADAT, False)))[0] is RefusalKind.NOT_SYNCED


def test_gate_reads_identity_optical_config_and_completeness_from_the_state():
    for state, kind in ((StateReads(VERIFIED, ADAT, True), None),
                        (StateReads(VERIFIED, ADAT, False), RefusalKind.NOT_SYNCED),
                        (StateReads(VERIFIED, OpticalConfig(OpticalMode.TOSLINK, OpticalMode.TOSLINK, 0), True),
                         RefusalKind.READ_ONLY)):
        result = write_gate(state)
        assert (None if isinstance(result, GateOpen) else result.kind) is kind
        assert "identity" in state.asked and "arrays_complete" in state.asked


def _reported_state(api=9, model="828E", optical_mode=0, expander=0, leave_out=()):
    raw = {Address(prop, i): (b"\x01" if prop != PropertyId.OPTICAL_MODE else bytes([optical_mode]))
           for prop in GATE_ARRAYS for i in range(spec(prop).length)}
    raw[Address(PropertyId.OPTICAL_EXPANDER, 0)] = bytes([expander])
    raw[Address(PropertyId.MODEL_ID, 0)] = model.encode() + b"\0"
    raw[Address(PropertyId.API_VERSION, 0)] = api.to_bytes(2, "big")
    raw[Address(PropertyId.DEV_NAME, 0)] = b"828\0"
    raw[Address(PropertyId.DEV_UID, 0)] = b"828E000000\0"
    for address in leave_out:
        del raw[address]
    return MixerState(raw=raw)


def test_gate_answers_the_service_for_a_real_mixer_state():
    assert isinstance(write_gate(_reported_state()), GateOpen)
    for leave_out in ((Address(PropertyId.MODEL_ID, 0),), (Address(PropertyId.PATCH, 127),)):
        assert _refusal(write_gate(_reported_state(leave_out=leave_out)))[0] is RefusalKind.NOT_SYNCED
    assert _refusal(write_gate(_reported_state(model="828X"))) == (
        RefusalKind.READ_ONLY, "model 828X, expected 828E")
    assert _refusal(write_gate(_reported_state(api=10))) == (
        RefusalKind.READ_ONLY, "API version 10, expected 9")
    assert _refusal(write_gate(_reported_state(optical_mode=1)))[0] is RefusalKind.READ_ONLY
    assert _refusal(write_gate(_reported_state(expander=1)))[0] is RefusalKind.READ_ONLY
