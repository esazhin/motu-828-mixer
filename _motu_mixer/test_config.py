import pytest

from _motu_mixer.config import Settings


def test_settings_default_to_the_828_com_port_served_on_every_interface_at_8280():
    settings = Settings.from_env({"PATH": "/usr/bin"})
    assert settings == Settings("828 COM", "0.0.0.0", 8280)


def test_settings_take_each_override_from_the_environment():
    assert Settings.from_env({"MOTU_HTTP_PORT": "5058"}).http_port == 5058
    assert Settings.from_env({"MOTU_HTTP_HOST": "192.0.2.10"}).host == "192.0.2.10"
    assert Settings.from_env({"MOTU_PORT_NAME": "828 MIDI"}).port_name == "828 MIDI"
    assert Settings.from_env(
        {"MOTU_PORT_NAME": "828 MIDI", "MOTU_HTTP_HOST": "127.0.0.1", "MOTU_HTTP_PORT": "5058"}
    ) == Settings("828 MIDI", "127.0.0.1", 5058)


@pytest.mark.parametrize("port", ["abc", "70000", "0"])
def test_settings_reject_a_port_that_is_not_an_integer_or_out_of_range(port):
    with pytest.raises(ValueError):
        Settings.from_env({"MOTU_HTTP_PORT": port})


def test_a_local_config_file_is_read_as_key_value_lines(tmp_path):
    from _motu_mixer.config import read_local_config
    path = tmp_path / "config.local"
    path.write_text('# my machine\n\nMOTU_HTTP_PORT=9001\nMOTU_PORT_NAME="828 COM"\n MOTU_OPEN_URL = http://mixer-host:9001/ \nnot a line\n')
    assert read_local_config(path) == {
        "MOTU_HTTP_PORT": "9001", "MOTU_PORT_NAME": "828 COM", "MOTU_OPEN_URL": "http://mixer-host:9001/",
    }
    assert read_local_config(tmp_path / "missing") == {}
    assert read_local_config(None) == {}
