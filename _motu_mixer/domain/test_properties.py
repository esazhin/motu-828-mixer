from _motu_mixer.domain.properties import GATE_ARRAYS, PROPERTY_TABLE, PropertySpec, ValueType, spec


def test_mixer_fader_is_writable_and_device_safety_properties_are_not():
    assert spec(1016) == PropertySpec(ValueType.F824, 704, True)
    assert not spec(5004).writable
    assert not spec(5000).writable
    assert spec(6000) is None


def test_phantom_power_cannot_be_written():
    assert spec(5004) is not None and spec(5004).writable is False


def test_output_trim_cannot_be_written():
    assert spec(5000) is not None and spec(5000).writable is False


def test_table_gives_capture_lengths_and_gate_arrays():
    lengths = {prop: spec(prop).length for prop in (
        1000, 1001, 1016, 1017, 1018, 1019, 1027, 1028, 5006, 5010, 5020, 5025, 21, 8, 3, 2)}
    assert lengths == {
        1000: 44, 1001: 16, 1016: 704, 1017: 704, 1018: 704, 1019: 704, 1027: 16, 1028: 16,
        5006: 2, 5010: 128, 5020: 128, 5025: 1, 21: 1, 8: 1, 3: 1, 2: 1,
    }
    assert all(isinstance(spec(prop), PropertySpec) for prop in GATE_ARRAYS)
    assert set(GATE_ARRAYS) == {1000, 1001, 1016, 1019, 5006, 5010, 5020, 5025}
    assert spec(6000) is None and spec(4242) is None


def test_table_gives_the_wire_type_of_each_property():
    assert spec(1016).value_type is ValueType.F824
    assert spec(1019).value_type is ValueType.BYTE
    assert spec(5010).value_type is ValueType.BYTE
    assert spec(21).value_type is ValueType.STRING
    assert spec(6000) is None


def test_only_mixer_routing_and_eq_properties_are_writable():
    # EQ (input 1002-1006, output 1022-1026) added 2026-10-12;
    # phantom power, preamp gain, pad and output trim stay out.
    writable = {int(prop) for prop, entry in PROPERTY_TABLE.items() if entry.writable}
    assert writable == {1000, 1001, 1016, 1017, 1018, 1019, 1027, 1028, 5010,
                        1002, 1003, 1004, 1005, 1006, 1022, 1023, 1024, 1025, 1026}
    assert not ({5000, 5001, 5003, 5004} & writable)
    assert spec(1004) == PropertySpec(ValueType.I32, 40, True) and spec(1025) == PropertySpec(ValueType.F824, 42, True)
    assert spec(1002).value_type is ValueType.BYTE and spec(1026).value_type is ValueType.F824
    for locked in (5004, 5001, 5003, 5000):
        assert spec(locked).writable is False
    assert spec(6000) is None
