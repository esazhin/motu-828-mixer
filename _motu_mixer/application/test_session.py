import asyncio
import inspect
from types import SimpleNamespace

from _motu_mixer.application.ports import PortBusy, PortGone
from _motu_mixer.application.session import DeviceSession
from _motu_mixer.domain.status import ConnectionStatus
from _motu_mixer.domain.values import PortAddress

PROBE = bytes.fromhex("f0 00 00 3b 00 01 01 f7")
ENABLE = bytes.fromhex("f0 00 00 3b 00 01 02 f7")
COM_PORT = PortAddress("hw:4,0,1")


class ManualClock:
    """Stands in for the clock: time stands still until the test advances it."""

    def __init__(self):
        self.now = 0.0
        self.sleepers = []

    def monotonic(self):
        return self.now

    async def sleep(self, seconds):
        wake = asyncio.get_running_loop().create_future()
        self.sleepers.append((self.now + seconds, wake))
        await wake

    def advance(self, seconds):
        self.now += seconds
        due = [s for s in self.sleepers if s[0] <= self.now]
        self.sleepers = [s for s in self.sleepers if s[0] > self.now]
        for _, wake in due:
            if not wake.done():
                wake.set_result(None)


async def settle(clock, condition, budget, step=0.05):
    """Yields to the session, moving the clock in small steps, until the condition holds or the budget is spent."""
    spent = 0.0
    while True:
        for _ in range(20):
            if condition():
                return
            await asyncio.sleep(0)
        if spent >= budget:
            return
        clock.advance(step)
        spent += step


class Locator:
    def locate(self, name):
        return COM_PORT


class Port:
    def __init__(self):
        self.written = []

    def open(self, addr):
        pass

    def write(self, frame):
        self.written.append(frame)

    def close(self):
        pass


class Pump:
    def __init__(self):
        self.sink = None

    def start(self, port, sink):
        self.sink = sink

    def stop(self):
        pass


class Service:
    # like the real service: settled once the whole state has arrived (status SYNCED), until the next session
    settled = False

    def begin_session(self, port):
        self.settled = False

    def end_session(self):
        pass

    def on_device_bytes(self, chunk):
        if chunk == b"dump done":
            self.settled = True
        return SimpleNamespace(probe_replied=chunk == b"probe reply", settled=self.settled)

    def set_connection_status(self, status, detail):
        pass


def test_session_writes_the_probe_and_enable_frames_it_is_given():
    asked = []

    def probe_frame():
        asked.append("probe_frame")
        return PROBE

    def enable_frame():
        asked.append("enable_frame")
        return ENABLE

    port, pump, clock = Port(), Pump(), ManualClock()
    session = DeviceSession(
        service=Service(), locator=Locator(), new_port=lambda: port, pump=pump, clock=clock,
        port_name="828 COM", probe_frame=probe_frame, enable_frame=enable_frame,
    )

    async def scenario():
        running = asyncio.create_task(session.run())
        try:
            await settle(clock, lambda: port.written, budget=1.0)
            probe_written = list(port.written)
            # the unit answers the probe: the enable frame follows
            pump.sink.on_bytes(b"probe reply")
            await settle(clock, lambda: len(port.written) >= 2, budget=0.5)
            return probe_written, list(port.written)
        finally:
            running.cancel()
            await asyncio.gather(running, return_exceptions=True)

    probe_written, written = asyncio.run(scenario())

    assert {"probe_frame", "enable_frame"} <= set(asked)
    assert probe_written == [PROBE]
    assert written == [PROBE, ENABLE]


def test_session_times_the_locate_retry_the_probe_reply_and_the_silence_limit_on_its_clock():
    class RecordingClock(ManualClock):
        """The manual clock, also recording what the session asks of it."""

        def __init__(self):
            super().__init__()
            self.asked = []

        def monotonic(self):
            self.asked.append("monotonic")
            return super().monotonic()

        async def sleep(self, seconds):
            self.asked.append(("sleep", seconds))
            await super().sleep(seconds)

    class StatusService(Service):
        def __init__(self):
            self.statuses = []

        def set_connection_status(self, status, detail):
            self.statuses.append(status)

    class AbsentOnceLocator:
        def __init__(self):
            self.calls = 0

        def locate(self, name):
            self.calls += 1
            return None if self.calls == 1 else COM_PORT

    async def yield_to_session():
        for _ in range(50):
            await asyncio.sleep(0)

    async def run_until(clock, moment):
        # steps of 1/8 s are exact in binary, so the clock lands on every whole and half second
        await yield_to_session()
        while clock.now < moment:
            clock.advance(min(0.125, moment - clock.now))
            await yield_to_session()

    async def with_session(service, locator, clock, scenario):
        port, pump = Port(), Pump()
        session = DeviceSession(
            service=service, locator=locator, new_port=lambda: port, pump=pump, clock=clock,
            port_name="828 COM", probe_frame=lambda: PROBE, enable_frame=lambda: ENABLE,
        )
        running = asyncio.create_task(session.run())
        try:
            return await scenario(port, pump)
        finally:
            running.cancel()
            await asyncio.gather(running, return_exceptions=True)

    # no '828 COM' listed at first, then a unit that never answers the probe
    retry_clock, retry_service, locator = RecordingClock(), StatusService(), AbsentOnceLocator()

    async def unanswered(port, pump):
        seen = {}
        await run_until(retry_clock, 0.875)
        seen["locates before 1 s"] = locator.calls
        await run_until(retry_clock, 1.0)
        seen["locates at 1 s"] = locator.calls
        seen["written at 1 s"] = list(port.written)
        await run_until(retry_clock, 1.875)
        seen["statuses 0.875 s after the probe"] = list(retry_service.statuses)
        await run_until(retry_clock, 2.125)
        seen["statuses 1.125 s after the probe"] = list(retry_service.statuses)
        return seen

    seen = asyncio.run(with_session(retry_service, locator, retry_clock, unanswered))

    assert seen["locates before 1 s"] == 1
    assert seen["locates at 1 s"] == 2
    assert seen["written at 1 s"] == [PROBE]
    assert ConnectionStatus.NO_DEVICE in seen["statuses 0.875 s after the probe"]
    assert ConnectionStatus.NO_REPLY not in seen["statuses 0.875 s after the probe"]
    assert ConnectionStatus.NO_REPLY in seen["statuses 1.125 s after the probe"]
    assert "monotonic" in retry_clock.asked
    assert any(call[0] == "sleep" and call[1] > 0 for call in retry_clock.asked if call != "monotonic")

    # an answering unit that goes quiet after its last bytes
    quiet_clock, quiet_service = RecordingClock(), StatusService()

    async def goes_quiet(port, pump):
        seen = {}
        await run_until(quiet_clock, 0.5)
        pump.sink.on_bytes(b"probe reply")
        await run_until(quiet_clock, 2.0)
        seen["written"] = list(port.written)
        # bytes 1.5 s after the last ones keep the session alive
        pump.sink.on_bytes(b"dump")
        await run_until(quiet_clock, 3.875)
        seen["statuses after 1.875 s of silence"] = list(quiet_service.statuses)
        await run_until(quiet_clock, 4.25)
        seen["statuses after 2.25 s of silence"] = list(quiet_service.statuses)
        return seen

    seen = asyncio.run(with_session(quiet_service, Locator(), quiet_clock, goes_quiet))

    assert seen["written"] == [PROBE, ENABLE]
    assert ConnectionStatus.DISCONNECTED not in seen["statuses after 1.875 s of silence"]
    assert ConnectionStatus.NO_REPLY not in seen["statuses after 1.875 s of silence"]
    assert ConnectionStatus.DISCONNECTED in seen["statuses after 2.25 s of silence"]
    assert "monotonic" in quiet_clock.asked


async def advance_to(clock, moment):
    """Moves the clock in steps of 1/8 s up to the moment, yielding to the session after each step."""
    for _ in range(50):
        await asyncio.sleep(0)
    while clock.now < moment:
        clock.advance(min(0.125, moment - clock.now))
        for _ in range(50):
            await asyncio.sleep(0)


def test_session_starts_the_pump_on_open_forwards_its_bytes_and_stops_it_before_closing_when_the_port_is_gone():
    log = []
    statuses = []

    class LoggingPort(Port):
        def open(self, addr):
            log.append("open")

        def close(self):
            log.append("close")

    class RecordingPump:
        def __init__(self):
            self.sink = None

        def start(self, port, sink):
            self.sink = sink
            log.append(("start", port, sink))

        def stop(self):
            log.append("stop")

    class RecordingService(Service):
        def begin_session(self, port):
            log.append("begin")

        def end_session(self):
            log.append("end")

        def on_device_bytes(self, chunk):
            log.append(("bytes", chunk))
            return super().on_device_bytes(chunk)

        def set_connection_status(self, status, detail):
            statuses.append(status)

    port, pump, clock = LoggingPort(), RecordingPump(), ManualClock()
    session = DeviceSession(
        service=RecordingService(), locator=Locator(), new_port=lambda: port, pump=pump, clock=clock,
        port_name="828 COM", probe_frame=lambda: PROBE, enable_frame=lambda: ENABLE,
    )

    def started():
        return any(isinstance(entry, tuple) and entry[0] == "start" for entry in log)

    async def scenario():
        running = asyncio.create_task(session.run())
        try:
            seen = {}
            await settle(clock, started, budget=1.0)
            seen["log at start"] = list(log)

            # every chunk the pump hands over reaches the service, in order
            chunks = [b"probe reply", b"first", b"second", b"third"]
            for chunk in chunks:
                pump.sink.on_bytes(chunk)
            await advance_to(clock, clock.now)
            seen["forwarded"] = [entry[1] for entry in log if isinstance(entry, tuple) and entry[0] == "bytes"]

            # bytes arriving 1.5 s later restart the 2 s silence limit
            t0 = clock.now
            await advance_to(clock, t0 + 1.5)
            pump.sink.on_bytes(b"later")
            await advance_to(clock, t0 + 3.375)
            seen["statuses before the port is gone"] = list(statuses)

            # the pump reports the port gone
            gone_at = len(log)
            pump.sink.on_port_gone()
            await settle(clock, lambda: "close" in log[gone_at:], budget=0.5)
            seen["log after gone"] = log[gone_at:]
            seen["statuses after gone"] = list(statuses)
            return seen
        finally:
            running.cancel()
            await asyncio.gather(running, return_exceptions=True)

    seen = asyncio.run(scenario())

    at_start = seen["log at start"]
    start_entry = next(entry for entry in at_start if isinstance(entry, tuple) and entry[0] == "start")
    assert start_entry[1] is port
    assert start_entry[2] is session
    assert at_start.index("open") < at_start.index(start_entry)

    assert seen["forwarded"] == [b"probe reply", b"first", b"second", b"third"]
    assert ConnectionStatus.DISCONNECTED not in seen["statuses before the port is gone"]

    after_gone = seen["log after gone"]
    assert "end" in after_gone
    assert "stop" in after_gone and "close" in after_gone
    assert after_gone.index("stop") < after_gone.index("close")
    assert ConnectionStatus.DISCONNECTED in seen["statuses after gone"]


def test_session_locates_by_name_each_second_retries_a_busy_or_vanished_port_and_closes_it_on_release_disconnect_and_shutdown():
    clock = ManualClock()

    class ScriptedLocator:
        """Answers None for the first calls it is given, then the '828 COM' address."""

        def __init__(self, absent_calls):
            self.absent_calls = absent_calls
            self.asked = []

        def locate(self, name):
            self.asked.append(name)
            return None if len(self.asked) <= self.absent_calls else COM_PORT

    class ScriptedPort:
        """Fails open() with the given exceptions in turn, then opens; records every call."""

        def __init__(self, failures):
            self.failures = list(failures)
            self.log = []

        def open(self, addr):
            self.log.append(("open", addr))
            if self.failures:
                raise self.failures.pop(0)

        def write(self, frame):
            self.log.append(("write", frame))

        def close(self):
            self.log.append("close")

    class StatusService(Service):
        def __init__(self):
            self.statuses = []

        def set_connection_status(self, status, detail):
            self.statuses.append(status)

    async def call(fn):
        result = fn()
        if inspect.isawaitable(result):
            task = asyncio.ensure_future(result)
            await settle(clock, task.done, budget=1.0)
            return task.result()
        return result

    async def until(condition, deadline):
        while not condition() and clock.now < deadline:
            await advance_to(clock, clock.now + 0.125)

    def new_session(locator, port, pump, service):
        return DeviceSession(
            service=service, locator=locator, new_port=lambda: port, pump=pump, clock=clock,
            port_name="828 COM", probe_frame=lambda: PROBE, enable_frame=lambda: ENABLE,
        )

    # absent for two locates, then busy, then vanished, then it opens
    locator, port, pump, service = ScriptedLocator(absent_calls=2), ScriptedPort([PortBusy(), PortGone()]), Pump(), StatusService()
    session = new_session(locator, port, pump, service)

    async def lifecycle():
        seen = {}
        running = asyncio.create_task(session.run())
        try:
            await advance_to(clock, 0.875)
            seen["locates before 1 s"] = list(locator.asked)
            seen["statuses before 1 s"] = list(service.statuses)
            await advance_to(clock, 1.0)
            seen["locates at 1 s"] = list(locator.asked)

            await until(lambda: ("write", PROBE) in port.log, deadline=6.0)
            seen["opened at"] = clock.now
            seen["log until the probe"] = list(port.log)
            seen["statuses until open"] = list(service.statuses)
            locates_when_open = len(locator.asked)

            pump.sink.on_bytes(b"probe reply")
            for _ in range(4):
                await advance_to(clock, clock.now + 0.5)
                pump.sink.on_bytes(b"dump")
            seen["locates while open"] = len(locator.asked) - locates_when_open
            seen["writes while open"] = [entry[1] for entry in port.log if isinstance(entry, tuple) and entry[0] == "write"]

            # the port vanishes underneath the session
            mark = len(port.log)
            pump.sink.on_port_gone()
            await settle(clock, lambda: "close" in port.log[mark:], budget=0.5)
            seen["log after disconnect"] = port.log[mark:]
            seen["statuses after disconnect"] = list(service.statuses)

            # it comes back, and is then released
            await until(lambda: ("write", PROBE) in port.log[mark:], deadline=clock.now + 2.5)
            seen["log after reconnect"] = port.log[mark:]
            pump.sink.on_bytes(b"probe reply")
            await advance_to(clock, clock.now + 0.125)
            mark = len(port.log)
            seen["release status"] = await call(session.release)
            seen["log after release"] = port.log[mark:]
            return seen
        finally:
            running.cancel()
            await asyncio.gather(running, return_exceptions=True)

    seen = asyncio.run(lifecycle())

    assert seen["locates before 1 s"] == ["828 COM"]
    assert ConnectionStatus.NO_DEVICE in seen["statuses before 1 s"]
    assert seen["locates at 1 s"] == ["828 COM", "828 COM"]
    assert set(locator.asked) == {"828 COM"}

    # busy, then vanished, then opened: three opens of the located address and no write before the last
    opens = [entry for entry in seen["log until the probe"] if entry != "close"]
    assert opens == [("open", COM_PORT)] * 3 + [("write", PROBE)]
    assert ConnectionStatus.BUSY in seen["statuses until open"]
    assert seen["opened at"] <= 5.0

    assert seen["locates while open"] == 0
    assert seen["writes while open"] == [PROBE, ENABLE]

    assert "close" in seen["log after disconnect"]
    assert ConnectionStatus.DISCONNECTED in seen["statuses after disconnect"]
    assert ("open", COM_PORT) in seen["log after reconnect"]
    assert ("write", PROBE) in seen["log after reconnect"]

    assert seen["release status"] == ConnectionStatus.RELEASED
    assert "close" in seen["log after release"]

    # shutdown closes an open port
    port, pump = ScriptedPort([]), Pump()
    session = new_session(ScriptedLocator(absent_calls=0), port, pump, StatusService())

    async def shutdown():
        running = asyncio.create_task(session.run())
        try:
            await until(lambda: ("write", PROBE) in port.log, deadline=clock.now + 1.0)
            mark = len(port.log)
            await call(session.stop)
            await settle(clock, lambda: "close" in port.log[mark:], budget=0.5)
            return port.log[mark:]
        finally:
            running.cancel()
            await asyncio.gather(running, return_exceptions=True)

    assert "close" in asyncio.run(shutdown())


def test_session_drives_the_mixer_service_through_open_probe_reply_gone_and_release():
    from _motu_mixer.application.ports import DeviceEvents

    clock = ManualClock()
    log = []

    class LoggingPort(Port):
        def open(self, addr):
            log.append("open")

        def write(self, frame):
            log.append(("write", frame))

    class RecordingService:
        """Answers each chunk with whether it was the probe reply, and records every call."""

        def begin_session(self, port):
            log.append(("begin_session", port))

        def end_session(self):
            log.append("end_session")

        def on_device_bytes(self, chunk):
            log.append(("on_device_bytes", chunk))
            return DeviceEvents(probe_replied=chunk == b"probe reply")

        def set_connection_status(self, status, detail):
            log.append(("status", status))

    def statuses(entries):
        return [entry[1] for entry in entries if isinstance(entry, tuple) and entry[0] == "status"]

    def in_order(seen, expected):
        remaining = iter(seen)
        return all(any(item == wanted for item in remaining) for wanted in expected)

    async def call(fn):
        result = fn()
        if inspect.isawaitable(result):
            task = asyncio.ensure_future(result)
            await settle(clock, task.done, budget=1.0)
            return task.result()
        return result

    def new_session(port, pump):
        return DeviceSession(
            service=RecordingService(), locator=Locator(), new_port=lambda: port, pump=pump, clock=clock,
            port_name="828 COM", probe_frame=lambda: PROBE, enable_frame=lambda: ENABLE,
        )

    # a unit whose bytes never include the probe reply: no enable, and NO_REPLY once 1 s has passed
    port, pump = LoggingPort(), Pump()
    session = new_session(port, pump)

    async def unanswered():
        running = asyncio.create_task(session.run())
        try:
            seen = {}
            await settle(clock, lambda: ("write", PROBE) in log, budget=1.0)
            probed_at = clock.now
            seen["log until the probe"] = list(log)
            pump.sink.on_bytes(b"noise")
            await advance_to(clock, probed_at + 0.875)
            seen["log before 1 s"] = list(log)
            await advance_to(clock, probed_at + 1.25)
            seen["log after 1 s"] = list(log)
            return seen
        finally:
            running.cancel()
            await asyncio.gather(running, return_exceptions=True)

    seen = asyncio.run(unanswered())

    until_probe = seen["log until the probe"]
    assert until_probe.index("open") < until_probe.index(("begin_session", port)) < until_probe.index(("write", PROBE))
    assert ("on_device_bytes", b"noise") in seen["log before 1 s"]
    assert ConnectionStatus.NO_REPLY not in statuses(seen["log before 1 s"])
    assert ConnectionStatus.NO_REPLY in statuses(seen["log after 1 s"])
    assert ("write", ENABLE) not in seen["log after 1 s"]

    # an answering unit: enable follows the reply, every chunk is handed over, and each ending ends the session
    log.clear()
    port, pump = LoggingPort(), Pump()
    session = new_session(port, pump)

    async def answered():
        running = asyncio.create_task(session.run())
        try:
            seen = {}
            await settle(clock, lambda: ("write", PROBE) in log, budget=1.0)
            for chunk in (b"probe reply", b"dump 1", b"dump 2"):
                pump.sink.on_bytes(chunk)
            await advance_to(clock, clock.now + 0.125)
            seen["log while synced"] = list(log)

            mark = len(log)
            pump.sink.on_port_gone()
            await settle(clock, lambda: "end_session" in log[mark:], budget=0.5)
            seen["log after gone"] = log[mark:]

            mark = len(log)
            await settle(clock, lambda: ("write", PROBE) in log[mark:], budget=2.5)
            pump.sink.on_bytes(b"probe reply")
            await advance_to(clock, clock.now + 0.125)
            seen["log after reconnect"] = log[mark:]

            mark = len(log)
            seen["release status"] = await call(session.release)
            await advance_to(clock, clock.now + 0.125)
            seen["log after release"] = log[mark:]
            return seen
        finally:
            running.cancel()
            await asyncio.gather(running, return_exceptions=True)

    seen = asyncio.run(answered())

    synced = seen["log while synced"]
    assert synced.index(("begin_session", port)) < synced.index(("write", PROBE))
    assert synced.index(("on_device_bytes", b"probe reply")) < synced.index(("write", ENABLE))
    chunks = [entry[1] for entry in synced if isinstance(entry, tuple) and entry[0] == "on_device_bytes"]
    assert chunks == [b"probe reply", b"dump 1", b"dump 2"]
    assert ConnectionStatus.SYNCING in statuses(synced)

    assert "end_session" in seen["log after gone"]
    assert ConnectionStatus.DISCONNECTED in statuses(seen["log after gone"])

    reconnect = seen["log after reconnect"]
    assert reconnect.index(("begin_session", port)) < reconnect.index(("write", PROBE)) < reconnect.index(("write", ENABLE))

    assert seen["release status"] == ConnectionStatus.RELEASED
    assert "end_session" in seen["log after release"]
    assert ConnectionStatus.RELEASED in statuses(seen["log after release"])

    # transitions are reported in the order they happen, and SYNCED / READ_ONLY are left to the service
    every_status = statuses(synced + seen["log after gone"] + reconnect + seen["log after release"])
    assert in_order(every_status, [ConnectionStatus.SYNCING, ConnectionStatus.DISCONNECTED,
                                   ConnectionStatus.SYNCING, ConnectionStatus.RELEASED])
    assert ConnectionStatus.SYNCED not in every_status
    assert ConnectionStatus.READ_ONLY not in every_status


class UnitOnPort:
    """A port and locator in one: the unit can be unplugged, replugged or held by another program; records every call."""

    def __init__(self):
        self.present = True
        self.held = False
        self.log = []
        self.locates = 0

    def locate(self, name):
        self.locates += 1
        return COM_PORT if self.present else None

    def open(self, addr):
        self.log.append("open")
        if self.held:
            raise PortBusy()

    def write(self, frame):
        self.log.append(frame)

    def close(self):
        self.log.append("close")


class SessionLog(Service):
    """Records the session's calls to the mixer service, in order."""

    def __init__(self):
        self.log = []

    def begin_session(self, port):
        self.log.append("begin")

    def end_session(self):
        self.log.append("end")

    def on_device_bytes(self, chunk):
        self.log.append(chunk)
        return super().on_device_bytes(chunk)

    def set_connection_status(self, status, detail):
        self.log.append(status)

    def statuses(self):
        return [entry for entry in self.log if isinstance(entry, ConnectionStatus)]


def session_on(unit, service, pump, clock):
    return DeviceSession(
        service=service, locator=unit, new_port=lambda: unit, pump=pump, clock=clock,
        port_name="828 COM", probe_frame=lambda: PROBE, enable_frame=lambda: ENABLE,
    )


def drive(session, scenario):
    async def run():
        running = asyncio.create_task(session.run())
        try:
            return await scenario()
        finally:
            running.cancel()
            await asyncio.gather(running, return_exceptions=True)

    return asyncio.run(run())


def test_a_unit_that_never_answers_the_probe_shows_no_reply_after_one_second_and_is_probed_again():
    unit, service, pump, clock = UnitOnPort(), SessionLog(), Pump(), ManualClock()
    session = session_on(unit, service, pump, clock)

    async def scenario():
        seen = {}
        await advance_to(clock, 0.875)
        seen["statuses before 1 s"] = service.statuses()
        await advance_to(clock, 1.125)
        seen["statuses after 1 s"] = service.statuses()
        await advance_to(clock, 2.125)
        seen["port after 2 s"] = list(unit.log)
        return seen

    seen = drive(session, scenario)

    assert seen["statuses before 1 s"] == [ConnectionStatus.SYNCING]
    assert seen["statuses after 1 s"] == [ConnectionStatus.SYNCING, ConnectionStatus.NO_REPLY]
    # still open, probed again each second, never enabled
    assert seen["port after 2 s"] == ["open", PROBE, PROBE, PROBE]


def test_with_no_port_of_that_name_listed_the_session_shows_no_device_and_looks_again_each_second():
    unit, service, pump, clock = UnitOnPort(), SessionLog(), Pump(), ManualClock()
    unit.present = False
    session = session_on(unit, service, pump, clock)

    async def scenario():
        await advance_to(clock, 3.0)
        return unit.locates

    assert drive(session, scenario) == 4
    assert service.statuses() == [ConnectionStatus.NO_DEVICE]
    assert unit.log == []


def test_every_session_starts_fresh_and_takes_the_units_full_report_after_enabling_it():
    unit, service, pump, clock = UnitOnPort(), SessionLog(), Pump(), ManualClock()
    session = session_on(unit, service, pump, clock)

    async def scenario():
        await advance_to(clock, 0.125)
        pump.sink.on_bytes(b"probe reply")
        pump.sink.on_bytes(b"dump")
        await advance_to(clock, 0.25)

    drive(session, scenario)

    assert unit.log[:3] == ["open", PROBE, ENABLE]
    assert service.log[:4] == ["begin", ConnectionStatus.SYNCING, b"probe reply", b"dump"]


def test_an_unplug_shows_disconnected_within_two_seconds_and_a_replug_resyncs_a_fresh_session():
    unit, service, pump, clock = UnitOnPort(), SessionLog(), Pump(), ManualClock()
    session = session_on(unit, service, pump, clock)

    async def scenario():
        seen = {}
        await advance_to(clock, 0.125)
        pump.sink.on_bytes(b"probe reply")

        # the pump reports the port gone
        unit.present = False
        pump.sink.on_port_gone()
        await advance_to(clock, 0.25)
        seen["statuses at the unplug"] = service.statuses()
        seen["port at the unplug"] = list(unit.log)
        await advance_to(clock, 3.0)
        seen["status while unplugged"] = service.statuses()[-1]

        mark = len(service.log)
        unit.present = True
        replugged = clock.now
        await advance_to(clock, replugged + 1.0)
        pump.sink.on_bytes(b"probe reply")
        pump.sink.on_bytes(b"dump after replug")
        # from here the unit goes quiet without the port vanishing
        quiet_from = clock.now
        await advance_to(clock, replugged + 1.125)
        seen["after the replug"] = service.log[mark:]
        seen["port after the replug"] = list(unit.log)

        await advance_to(clock, quiet_from + 1.875)
        seen["statuses after 1.875 s of quiet"] = service.statuses()
        await advance_to(clock, quiet_from + 2.0)
        seen["statuses after 2 s of quiet"] = service.statuses()
        return seen

    seen = drive(session, scenario)

    assert seen["statuses at the unplug"][-1] == ConnectionStatus.DISCONNECTED
    assert seen["port at the unplug"][-1] == "close"
    assert seen["status while unplugged"] == ConnectionStatus.DISCONNECTED

    after = seen["after the replug"]
    assert after[:2] == ["begin", ConnectionStatus.SYNCING]
    assert after.index(b"probe reply") < after.index(b"dump after replug")
    assert seen["port after the replug"][-3:] == ["open", PROBE, ENABLE]

    assert ConnectionStatus.DISCONNECTED not in seen["statuses after 1.875 s of quiet"][-1:]
    assert seen["statuses after 2 s of quiet"][-1] == ConnectionStatus.DISCONNECTED
    assert service.log.count("end") == 2


def test_three_unplugs_within_ten_seconds_end_synced_within_five_seconds_of_the_last_replug():
    unit, service, pump, clock = UnitOnPort(), SessionLog(), Pump(), ManualClock()
    session = session_on(unit, service, pump, clock)

    async def scenario():
        await advance_to(clock, 0.125)
        pump.sink.on_bytes(b"probe reply")
        for _ in range(3):
            unit.present = False
            pump.sink.on_port_gone()
            await advance_to(clock, clock.now + 1.5)
            unit.present = True
            replugged = clock.now
            await settle(clock, lambda: unit.log[-1] == PROBE, budget=5.0)
            pump.sink.on_bytes(b"probe reply")
            await advance_to(clock, clock.now + 0.125)
        return clock.now - replugged, list(unit.log), list(service.log)

    took, port_log, service_log = drive(session, scenario)

    assert took <= 5.0
    assert clock.now <= 10.0
    assert port_log[-3:] == ["open", PROBE, ENABLE]
    assert service_log.count("begin") == 4
    assert service_log.count("end") == 3
    assert [entry for entry in service_log if isinstance(entry, ConnectionStatus)][-1] == ConnectionStatus.SYNCING


def test_a_port_held_by_another_program_shows_busy_and_syncs_soon_after_it_is_let_go():
    unit, service, pump, clock = UnitOnPort(), SessionLog(), Pump(), ManualClock()
    unit.held = True
    session = session_on(unit, service, pump, clock)

    async def scenario():
        await advance_to(clock, 30.0)
        unit.held = False
        await advance_to(clock, 31.0)
        return list(unit.log)

    port_log = drive(session, scenario)

    assert port_log.count("open") == 32
    assert service.statuses() == [ConnectionStatus.BUSY, ConnectionStatus.SYNCING]
    assert port_log[-2:] == ["open", PROBE]


def test_release_closes_the_port_and_keeps_it_closed_until_acquire_returns_the_status_reached():
    unit, service, pump, clock = UnitOnPort(), SessionLog(), Pump(), ManualClock()
    session = session_on(unit, service, pump, clock)

    async def scenario():
        seen = {}
        await advance_to(clock, 0.125)
        pump.sink.on_bytes(b"probe reply")

        released_at = clock.now
        seen["release"] = await session.release()
        seen["released in"] = clock.now - released_at
        seen["port after release"] = unit.log[-1]
        locates, opens = unit.locates, unit.log.count("open")
        await advance_to(clock, clock.now + 3.0)
        seen["locates while released"] = unit.locates - locates
        seen["opens while released"] = unit.log.count("open") - opens

        seen["acquire"] = await session.acquire()
        await advance_to(clock, clock.now + 0.125)
        seen["port after acquire"] = unit.log[-2:]

        await session.release()
        unit.held = True
        seen["acquire while held"] = await session.acquire()
        await advance_to(clock, clock.now + 1.0)
        seen["opens a second later"] = unit.log[-1]

        await session.release()
        unit.held, unit.present = False, False
        seen["acquire while unplugged"] = await session.acquire()
        return seen

    seen = drive(session, scenario)

    assert seen["release"] == ConnectionStatus.RELEASED
    assert seen["released in"] <= 1.0
    assert seen["port after release"] == "close"
    assert "end" in service.log and ConnectionStatus.RELEASED in service.statuses()
    assert seen["locates while released"] == 0
    assert seen["opens while released"] == 0

    assert seen["acquire"] == ConnectionStatus.SYNCING
    assert seen["port after acquire"] == ["open", PROBE]
    assert seen["acquire while held"] == ConnectionStatus.BUSY
    assert seen["opens a second later"] == "open"
    assert seen["acquire while unplugged"] == ConnectionStatus.NO_DEVICE


def test_session_asks_again_for_the_full_dump_until_the_unit_is_settled():
    """The 828 sends its whole state once after enable and never repeats it unasked. If bytes are lost (a rawmidi
    overrun on the real unit, 2026-10-12) the state never completes, so the session re-sends enable every 3 s
    while the unit is not settled, and stops once it is."""
    clock, port, pump = ManualClock(), Port(), Pump()
    session = DeviceSession(
        service=Service(), locator=Locator(), new_port=lambda: port, pump=pump, clock=clock,
        port_name="828 COM", probe_frame=lambda: PROBE, enable_frame=lambda: ENABLE,
    )

    async def scenario():
        running = asyncio.create_task(session.run())
        seen = {}
        try:
            await advance_to(clock, 0.5)
            pump.sink.on_bytes(b"probe reply")              # enable goes out at t = 0.5
            for t in (1.5, 2.5):                             # the stream keeps flowing, never settled
                await advance_to(clock, t)
                pump.sink.on_bytes(b"partial dump")
            await advance_to(clock, 3.375)
            seen["before 3 s"] = port.written.count(ENABLE)
            pump.sink.on_bytes(b"partial dump")
            await advance_to(clock, 3.625)
            seen["after 3 s"] = port.written.count(ENABLE)
            for t in (4.5, 5.5, 6.5):
                await advance_to(clock, t)
                pump.sink.on_bytes(b"partial dump")
            await advance_to(clock, 6.75)
            seen["after 6 s"] = port.written.count(ENABLE)
            pump.sink.on_bytes(b"dump done")
            for t in (7.5, 8.5, 9.5, 10.5, 11.5, 12.5):
                await advance_to(clock, t)
                pump.sink.on_bytes(b"stream")
            seen["once settled"] = port.written.count(ENABLE)
        finally:
            running.cancel()
            await asyncio.gather(running, return_exceptions=True)
        return seen

    seen = asyncio.run(scenario())
    assert seen == {"before 3 s": 1, "after 3 s": 2, "after 6 s": 3, "once settled": 3}
