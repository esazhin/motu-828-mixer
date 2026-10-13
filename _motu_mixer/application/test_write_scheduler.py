import asyncio

from _motu_mixer.application.write_scheduler import WriteScheduler
from _motu_mixer.domain.values import Address, Gain, Write

ADAT_B_8_REVERB_1_FADER = Address(1016, 643)
ADAT_B_8_REVERB_1_MUTE = Address(1019, 643)


class ManualClock:
    """Stands in for the clock: time moves only when sleep is awaited."""

    def __init__(self):
        self.now = 0.0

    def monotonic(self):
        return self.now

    async def sleep(self, seconds):
        self.now += seconds
        await asyncio.sleep(0)


class Port:
    def __init__(self):
        self.written = []

    def write(self, frame):
        self.written.append(frame)


async def until(condition):
    for _ in range(1000):
        if condition():
            return
        await asyncio.sleep(0)


def test_scheduler_writes_exactly_the_frames_the_encoder_returns():
    asked = []
    canned = {
        ADAT_B_8_REVERB_1_FADER: bytes.fromhex("f0 00 00 3b 00 01 00 0a 03 78 02 03 00 04 00 05 00 4d 4e f7"),
        ADAT_B_8_REVERB_1_MUTE: bytes.fromhex("f0 00 00 3b 00 01 00 0a 03 7b 02 03 00 01 01 f7"),
    }

    def encode(address, value):
        asked.append((address, value))
        return canned[address]

    fader = Write(ADAT_B_8_REVERB_1_FADER, Gain.from_db(-6.0))
    mute = Write(ADAT_B_8_REVERB_1_MUTE, True)

    async def scenario():
        port = Port()
        scheduler = WriteScheduler(ManualClock(), encode=encode)
        scheduler.attach(port)
        sent = scheduler.submit((fader, mute))
        await until(lambda: len(port.written) >= 2)
        return sent, port.written

    sent, written = asyncio.run(scenario())

    # each write is encoded from its own address and value
    assert asked == [(fader.address, fader.value), (mute.address, mute.value)]
    # the encoder's frames are returned at once and reach the port byte for byte, in order
    assert sent == (canned[ADAT_B_8_REVERB_1_FADER], canned[ADAT_B_8_REVERB_1_MUTE])
    assert written == [canned[ADAT_B_8_REVERB_1_FADER], canned[ADAT_B_8_REVERB_1_MUTE]]


def test_scheduler_writes_one_frame_per_call_and_drops_pending_when_the_port_is_gone():
    from _motu_mixer.application.ports import PortGone

    line_3_ana_3 = Address(1016, 178)
    frames = {
        ADAT_B_8_REVERB_1_FADER: bytes.fromhex("f0 00 00 3b 00 01 00 0a 03 78 02 03 00 04 00 05 00 4d 4e f7"),
        ADAT_B_8_REVERB_1_MUTE: bytes.fromhex("f0 00 00 3b 00 01 00 0a 03 7b 02 03 00 01 01 f7"),
        line_3_ana_3: bytes.fromhex("f0 00 00 3b 00 01 00 0a 03 78 00 32 00 04 00 01 00 00 00 f7"),
    }

    class VanishingPort:
        """Stands in for the control port: records each write; raises PortGone from the given call on."""

        def __init__(self, gone_from_call=None):
            self.gone_from_call = gone_from_call
            self.calls = []

        def write(self, frame):
            self.calls.append(frame)
            if self.gone_from_call is not None and len(self.calls) >= self.gone_from_call:
                raise PortGone()

    def encode(address, value):
        return frames[address]

    async def settle():
        for _ in range(200):
            await asyncio.sleep(0)

    async def scenario():
        gone, fresh = VanishingPort(gone_from_call=2), VanishingPort()
        scheduler = WriteScheduler(ManualClock(), encode=encode)
        scheduler.attach(gone)
        scheduler.submit((
            Write(ADAT_B_8_REVERB_1_FADER, Gain.from_db(-6.0)),
            Write(ADAT_B_8_REVERB_1_MUTE, True),
            Write(line_3_ana_3, Gain.from_db(0.0)),
        ))
        await settle()
        gone_calls_after_failure = list(gone.calls)

        # after the port is gone nothing more is sent, even for new submissions
        scheduler.submit((Write(line_3_ana_3, Gain.from_db(-3.0)),))
        await settle()
        gone_calls_later = list(gone.calls)

        # a new attach sends only what is submitted from then on; the dropped frame never goes out
        scheduler.attach(fresh)
        await settle()
        fresh_before_submit = list(fresh.calls)
        scheduler.submit((Write(ADAT_B_8_REVERB_1_MUTE, True),))
        await until(lambda: len(fresh.calls) >= 1)
        await settle()
        return gone_calls_after_failure, gone_calls_later, fresh_before_submit, fresh.calls

    after_failure, later, fresh_before, fresh_calls = asyncio.run(scenario())

    # one complete frame per write call, in submit order; the failing second call ends the sending
    assert after_failure == [frames[ADAT_B_8_REVERB_1_FADER], frames[ADAT_B_8_REVERB_1_MUTE]]
    assert all(frame[0] == 0xF0 and frame[-1] == 0xF7 for frame in after_failure)
    assert later == after_failure
    assert fresh_before == []
    assert fresh_calls == [frames[ADAT_B_8_REVERB_1_MUTE]]


def test_scheduler_paces_one_cell_by_the_clock_and_always_sends_the_final_value():
    line_3_ana_3 = Address(1016, 178)
    dbs = [-60.0 + i * 48.0 / 59 for i in range(59)] + [-12.0]
    frames = {Gain.from_db(db): bytes([0xF0, i, 0xF7]) for i, db in enumerate(dbs)}

    class RecordingClock(ManualClock):
        """The manual clock, also recording every monotonic read and every sleep asked for."""

        def __init__(self):
            super().__init__()
            self.reads = []
            self.sleeps = []

        def monotonic(self):
            self.reads.append(self.now)
            return super().monotonic()

        async def sleep(self, seconds):
            self.sleeps.append(seconds)
            await super().sleep(seconds)

    clock = RecordingClock()

    class TimedPort:
        def __init__(self):
            self.written = []

        def write(self, frame):
            self.written.append((clock.now, frame))

    async def scenario():
        port = TimedPort()
        scheduler = WriteScheduler(clock, encode=lambda address, value: frames[value])
        scheduler.attach(port)
        # a drag through 60 positions in one second of clock time, ending at -12 dB
        for db in dbs:
            scheduler.submit((Write(line_3_ana_3, Gain.from_db(db)),))
            clock.now += 1.0 / 60
            for _ in range(5):
                await asyncio.sleep(0)
        await until(lambda: port.written and port.written[-1][1] == frames[Gain.from_db(-12.0)])
        for _ in range(200):
            await asyncio.sleep(0)
        return port.written

    written = asyncio.run(scenario())

    # the scheduler reads the clock for its rate window and waits on the clock between sends
    assert clock.reads
    assert any(seconds > 0 for seconds in clock.sleeps)
    # time as the clock tells it: no one-second window holds more than 50 frames for the cell
    times = [t for t, _ in written]
    assert times == sorted(times)
    assert all(sum(1 for u in times if t <= u < t + 1.0) <= 50 for t in times)
    # the final value is always sent, and it is the last thing written
    assert written[-1][1] == frames[Gain.from_db(-12.0)]
    sent_order = [frame[1] for _, frame in written]
    assert sent_order == sorted(sent_order)


class Encoder:
    """Stands in for the codec: answers a frame naming the address and a running number; records each call."""

    def __init__(self):
        self.asked = []

    def __call__(self, address, value):
        self.asked.append((address, value))
        return bytes([0xF0, address.index % 128, len(self.asked) % 128, 0xF7])


async def let_the_loop_run():
    for _ in range(200):
        await asyncio.sleep(0)


def test_a_drag_of_one_cell_goes_out_at_most_50_times_a_second_ending_on_its_last_value():
    line_3_ana_3 = Address(1016, 178)
    dbs = [-60.0 + i * 48.0 / 59 for i in range(59)] + [-12.0]
    clock, encoder = ManualClock(), Encoder()

    async def scenario():
        port = Port()
        scheduler = WriteScheduler(clock, encode=encoder)
        scheduler.attach(port)
        last = None
        for db in dbs:
            (last,) = scheduler.submit((Write(line_3_ana_3, Gain.from_db(db)),))
            clock.now += 1.0 / 60
            await asyncio.sleep(0)
        await let_the_loop_run()
        return port.written, last

    written, last = asyncio.run(scenario())

    assert encoder.asked[-1] == (line_3_ana_3, Gain.from_db(-12.0))
    assert 1 < len(written) <= 50
    assert written[-1] == last


def test_a_port_lost_mid_send_drops_pending_frames_and_never_sends_them_again():
    from _motu_mixer.application.ports import PortGone

    line_3_main_1, line_3_ana_3, line_4_ana_3 = Address(1016, 2), Address(1016, 178), Address(1016, 179)

    class Unplugged(Port):
        def write(self, frame):
            super().write(frame)
            if len(self.written) == 2:
                raise PortGone()

    async def scenario():
        lost, replugged = Unplugged(), Port()
        scheduler = WriteScheduler(ManualClock(), encode=Encoder())
        scheduler.attach(lost)
        frames = scheduler.submit((Write(line_3_main_1, Gain.from_db(-6.0)),
                                   Write(line_3_ana_3, Gain.from_db(-6.0)),
                                   Write(line_4_ana_3, Gain.from_db(-6.0))))
        await let_the_loop_run()
        scheduler.detach()
        scheduler.attach(replugged)
        await let_the_loop_run()
        return frames, lost.written, replugged.written

    frames, lost_written, replugged_written = asyncio.run(scenario())

    assert lost_written == list(frames[:2])
    assert replugged_written == []


def test_each_write_is_encoded_once_and_its_frame_is_both_returned_and_written():
    encoder = Encoder()
    writes = (Write(ADAT_B_8_REVERB_1_FADER, Gain.from_db(-6.0)), Write(ADAT_B_8_REVERB_1_MUTE, True))

    async def scenario():
        port = Port()
        scheduler = WriteScheduler(ManualClock(), encode=encoder)
        scheduler.attach(port)
        frames = scheduler.submit(writes)
        await let_the_loop_run()
        return frames, port.written

    frames, written = asyncio.run(scenario())

    assert encoder.asked == [(w.address, w.value) for w in writes]
    assert list(frames) == written


def test_scheduler_follows_attach_and_detach_and_answers_submit_and_sent_log():
    first, second, third = Address(1016, 0), Address(1016, 1), Address(1016, 2)

    async def scenario():
        clock, port = ManualClock(), Port()
        scheduler = WriteScheduler(clock, encode=Encoder())
        scheduler.attach(port)

        # submit answers the encoded frames before anything is sent
        (a1,) = scheduler.submit((Write(first, Gain.from_db(-6.0)),))
        b1, a2 = scheduler.submit((Write(second, Gain.from_db(-6.0)), Write(first, Gain.from_db(-3.0))))
        assert port.written == []
        await let_the_loop_run()
        # submit order kept across addresses, and only the latest value of an address goes out
        assert port.written == [a2, b1]

        # detach drops what is still pending
        scheduler.submit((Write(third, Gain.from_db(0.0)),))
        scheduler.detach()
        await let_the_loop_run()
        assert port.written == [a2, b1]

        # the log holds the last 500 frames sent, and sent_log(n) answers the newest n, newest last
        scheduler.attach(port)
        for i in range(600):
            scheduler.submit((Write(Address(1016, i), Gain.from_db(0.0)),))
        await let_the_loop_run()
        clock.now += 1.0
        await let_the_loop_run()
        log = scheduler.sent_log(1000)
        assert len(log) == 500
        assert [entry.frame for entry in log] == port.written[-500:]
        assert scheduler.sent_log(3) == log[-3:]
        assert [entry.t for entry in log] == sorted(entry.t for entry in log)

    asyncio.run(scenario())
