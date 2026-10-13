"""Composition root: reads the settings, wires the mixer service to '828 COM' and serves the page."""
import os
import subprocess
from pathlib import Path

import uvicorn

from _motu_mixer.application.mixer_service import MixerService
from _motu_mixer.application.session import DeviceSession
from _motu_mixer.application.write_scheduler import WriteScheduler
from _motu_mixer.config import LOCAL_CONFIG, Settings, read_local_config
from _motu_mixer.infrastructure.alsa_rawmidi import AlsaRawMidiPort, AmidiPortLocator, load_libasound
from _motu_mixer.infrastructure.clock import AsyncioClock
from _motu_mixer.infrastructure.device_pump import ThreadDevicePump
from _motu_mixer.presentation.api import create_app as _create_app
from _motu_mixer.presentation.ws_hub import WebSocketHub
from _motu_mixer.protocol.sysex import enable_frame, probe_frame

STATIC_DIR = Path(__file__).resolve().parent / "presentation" / "static"


def main(environ=None, *, load_settings=Settings.from_env, create_app=_create_app, serve=None,
         run_amidi=subprocess.run, load_lib=load_libasound):
    """Serves the mixer until terminated; an invalid configuration exits non-zero."""
    try:
        env = os.environ if environ is None else environ
        # config.local is read for the real process (or a MOTU_CONFIG file); an explicit environ stays hermetic
        local = env.get("MOTU_CONFIG") or (LOCAL_CONFIG if environ is None else None)
        settings = load_settings({**read_local_config(local), **env} if local else env)
    except ValueError as error:
        raise SystemExit(f"motu-mixer: invalid configuration: {error}")
    clock = AsyncioClock()
    scheduler = WriteScheduler(clock)
    hub = WebSocketHub(lambda: service.snapshot())
    service = MixerService(hub, scheduler)
    session = DeviceSession(
        service=service, locator=AmidiPortLocator(runner=run_amidi),
        new_port=lambda: AlsaRawMidiPort(lib=load_lib()), pump=ThreadDevicePump(), clock=clock,
        port_name=settings.port_name, probe_frame=probe_frame, enable_frame=enable_frame,
    )
    app = create_app(service, session, hub, STATIC_DIR)
    (serve or uvicorn.run)(app, host=settings.host, port=settings.http_port, ws="websockets")


if __name__ == "__main__":
    main()
