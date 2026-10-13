"""DeviceSession: finds '828 COM' by name, opens it, probes and enables the unit, and notices when it goes away."""
import asyncio
import math

from _motu_mixer.domain.status import ConnectionStatus

from .ports import PortBusy, PortGone

LOCATE_INTERVAL = 1.0
PROBE_TIMEOUT = 1.0
SILENCE_LIMIT = 2.0
# The 828 sends its whole state once after enable and never repeats it unasked; bytes lost on the way (a rawmidi
# overrun) would leave the state incomplete forever. Not settled this long after enable -> ask again.
DUMP_RETRY = 3.0


class DeviceSession:
    def __init__(self, *, service, locator, new_port, pump, clock, port_name, probe_frame, enable_frame):
        self._service = service
        self._locator = locator
        self._new_port = new_port
        self._pump = pump
        self._clock = clock
        self._port_name = port_name
        self._probe_frame = probe_frame
        self._enable_frame = enable_frame
        self._wake = asyncio.Event()
        self._status = None
        self._port = None
        self._replied = False
        self._probed_at = 0.0
        self._enabled_at = 0.0
        self._settled = False
        self._last_bytes = 0.0
        self._next_attempt = -math.inf
        self._released = False
        self._stopping = False

    async def run(self):
        try:
            while not self._stopping:
                if self._released:
                    await self._wait(None)
                elif self._port is not None:
                    await self._watch()
                else:
                    remaining = self._next_attempt - self._clock.monotonic()
                    if remaining > 0:
                        await self._wait(remaining)
                    else:
                        self._connect()
        finally:
            self._teardown(None)

    def stop(self):
        self._stopping = True
        self._teardown(None)
        self._wake.set()

    async def release(self):
        self._released = True
        self._teardown(None)
        self._set_status(ConnectionStatus.RELEASED)
        self._wake.set()
        return ConnectionStatus.RELEASED

    async def acquire(self):
        if self._released:
            self._released = False
            self._connect()
            self._wake.set()
        return self._status

    def on_bytes(self, chunk):
        if self._port is None:
            return
        events = self._service.on_device_bytes(chunk)
        self._last_bytes = self._clock.monotonic()
        self._settled = events.settled
        if events.probe_replied and not self._replied:
            self._replied = True
            self._set_status(ConnectionStatus.SYNCING)
            self._enable()

    def on_port_gone(self):
        self._teardown(ConnectionStatus.DISCONNECTED)

    def _connect(self):
        self._next_attempt = self._clock.monotonic() + LOCATE_INTERVAL
        addr = self._locator.locate(self._port_name)
        if addr is None:
            if self._status is not ConnectionStatus.DISCONNECTED:
                self._set_status(ConnectionStatus.NO_DEVICE)
            return
        port = self._new_port()
        try:
            port.open(addr)
        except PortBusy:
            self._set_status(ConnectionStatus.BUSY)
            return
        except PortGone:
            self._set_status(ConnectionStatus.NO_DEVICE)
            return
        self._port = port
        self._replied = False
        self._settled = False
        self._service.begin_session(port)
        self._pump.start(port, self)
        self._set_status(ConnectionStatus.SYNCING)
        self._probe()

    async def _watch(self):
        """Waits out the probe reply, then the silence limit, while the port stays open."""
        while self._port is not None and not self._released and not self._stopping:
            now = self._clock.monotonic()
            if self._replied:
                if now - self._last_bytes >= SILENCE_LIMIT:
                    self._teardown(ConnectionStatus.DISCONNECTED)
                    return
                if not self._settled and now - self._enabled_at >= DUMP_RETRY:
                    self._enable()
                    if self._port is None:
                        return
                deadline = self._last_bytes + SILENCE_LIMIT
                if not self._settled:
                    deadline = min(deadline, self._enabled_at + DUMP_RETRY)
            else:
                if now - self._probed_at >= PROBE_TIMEOUT:
                    self._set_status(ConnectionStatus.NO_REPLY)
                    self._probe()
                deadline = self._probed_at + PROBE_TIMEOUT
            await self._wait(deadline - now)

    def _enable(self):
        self._enabled_at = self._clock.monotonic()
        self._send(self._enable_frame())

    def _probe(self):
        self._probed_at = self._clock.monotonic()
        self._send(self._probe_frame())

    def _send(self, frame):
        try:
            self._port.write(frame)
        except PortGone:
            self._teardown(ConnectionStatus.DISCONNECTED)

    def _teardown(self, status):
        if self._port is None:
            return
        port, self._port = self._port, None
        self._pump.stop()
        port.close()
        self._service.end_session()
        self._next_attempt = self._clock.monotonic() + LOCATE_INTERVAL
        if status is not None:
            self._set_status(status)
        self._wake.set()

    def _set_status(self, status):
        if status is not self._status:
            self._status = status
            self._service.set_connection_status(status, None)

    async def _wait(self, seconds):
        """Sleeps on the clock for the given seconds (forever when None), or until something wakes the session."""
        waits = {asyncio.ensure_future(self._wake.wait())}
        if seconds is not None:
            waits.add(asyncio.ensure_future(self._clock.sleep(seconds)))
        try:
            await asyncio.wait(waits, return_when=asyncio.FIRST_COMPLETED)
        finally:
            for waiting in waits:
                waiting.cancel()
        self._wake.clear()
