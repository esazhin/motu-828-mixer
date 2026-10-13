import dataclasses
from pathlib import Path

from _motu_mixer.domain.protocol_bounds import OPEN_ITEMS

PROTOCOL_SPEC = Path(__file__).resolve().parents[2] / "PROTOCOL-SPEC.md"


def test_open_items_are_echo_and_the_unrun_write_checks():
    assert [item.name for item in OPEN_ITEMS] == ["echo", "W2", "W3", "W5"]
    for item in OPEN_ITEMS:
        assert item.behaviour and item.settled_by


def test_open_items_declare_behaviour_without_switches():
    for item in OPEN_ITEMS:
        values = [getattr(item, f.name) for f in dataclasses.fields(item)]
        assert all(isinstance(value, str) for value in values)
    assert type(OPEN_ITEMS[0]).__dataclass_params__.frozen


def test_protocol_spec_records_the_settled_items():
    spec = PROTOCOL_SPEC.read_text()
    assert "### 4.5 Stereo semantics: one cell per action [CODE]" in spec
    assert "patch slots **76–119 feed mixer inputs 0–43**" in spec
    assert "| ~~W4~~ | dropped" in spec
