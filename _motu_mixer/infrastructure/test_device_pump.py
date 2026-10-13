import asyncio
import threading
import time
from pathlib import Path

from _motu_mixer.application.ports import PortGone
from _motu_mixer.infrastructure.device_pump import ThreadDevicePump


def test_pump_reads_from_one_thread_skips_idle_reads_and_ends_when_the_port_is_gone():
    class ScriptedPort:
        """Answers read() from a script: idle, chunks, then the port vanishes."""

        def __init__(self, answers):
            self.answers = list(answers)
            self.read_threads = []

        def read(self):
            self.read_threads.append(threading.get_ident())
            answer = self.answers.pop(0) if self.answers else PortGone()
            if isinstance(answer, Exception):
                raise answer
            return answer

    port = ScriptedPort([b"", b"\xf0\x00\x00", b"", b"", b"\x3b\x00\x01\x41\xf7", PortGone()])

    async def scenario():
        loop_thread = threading.get_ident()
        gone = asyncio.Event()
        received = []

        class Sink:
            def on_bytes(self, chunk):
                received.append((chunk, threading.get_ident()))

            def on_port_gone(self):
                received.append(("gone", threading.get_ident()))
                gone.set()

        pump = ThreadDevicePump()
        pump.start(port, Sink())
        await asyncio.wait_for(gone.wait(), timeout=2.0)
        # give a stray read or callback the chance to show up
        await asyncio.sleep(0.1)
        pump.stop()
        return loop_thread, received

    loop_thread, received = asyncio.run(scenario())

    assert [entry for entry, _ in received] == [b"\xf0\x00\x00", b"\x3b\x00\x01\x41\xf7", "gone"]
    assert all(thread == loop_thread for _, thread in received)
    # every read came from one thread that is not the event loop's, and none after the port was gone
    assert len(port.read_threads) == 6
    assert len(set(port.read_threads)) == 1
    assert port.read_threads[0] != loop_thread


CAPTURE = Path(__file__).resolve().parents[2] / "fixtures" / "828-property-dump-2026-10-12.hex"


class ChunkedPort:
    """Serves the data in fixed-size chunks, then idles; raises PortGone once the data is out if asked to."""

    def __init__(self, data, size, gone_at_end=False):
        self.chunks = [data[i:i + size] for i in range(0, len(data), size)]
        self.gone_at_end = gone_at_end
        self.reads = 0

    def read(self):
        self.reads += 1
        if self.chunks:
            return self.chunks.pop(0)
        if self.gone_at_end:
            raise PortGone()
        return b""


class RecordingSink:
    def __init__(self):
        self.chunks = []
        self.gone = 0
        self.threads = set()

    def on_bytes(self, chunk):
        self.threads.add(threading.get_ident())
        self.chunks.append(chunk)

    def on_port_gone(self):
        self.threads.add(threading.get_ident())
        self.gone += 1


async def wait_for(condition, timeout):
    deadline = time.monotonic() + timeout
    while not condition() and time.monotonic() < deadline:
        await asyncio.sleep(0.005)


def test_pump_delivers_the_whole_capture_in_order_on_the_event_loop():
    capture = bytes.fromhex(CAPTURE.read_text())
    port, sink = ChunkedPort(capture, 2048), RecordingSink()

    async def scenario():
        pump = ThreadDevicePump()
        started = time.monotonic()
        pump.start(port, sink)
        await wait_for(lambda: sum(map(len, sink.chunks)) >= len(capture), timeout=2.0)
        took = time.monotonic() - started
        pump.stop()
        return threading.get_ident(), took

    loop_thread, took = asyncio.run(scenario())

    assert b"".join(sink.chunks) == capture
    assert all(sink.chunks)
    assert sink.threads == {loop_thread}
    assert took < 0.1
    assert sink.gone == 0


def test_pump_reports_the_port_gone_exactly_once():
    port, sink = ChunkedPort(b"\xf0\x00\x00\x3b\x00\x01\x41\xf7", 4, gone_at_end=True), RecordingSink()

    async def scenario():
        pump = ThreadDevicePump()
        pump.start(port, sink)
        await wait_for(lambda: sink.gone, timeout=2.0)
        await asyncio.sleep(0.05)
        pump.stop()

    asyncio.run(scenario())

    assert b"".join(sink.chunks) == b"\xf0\x00\x00\x3b\x00\x01\x41\xf7"
    assert sink.gone == 1
    # reading stopped with the PortGone: two chunks, then the one failing read
    assert port.reads == 3


def test_pump_makes_no_callback_after_stop_even_for_chunks_already_read():
    port, sink = ChunkedPort(bytes(range(256)) * 64, 16, gone_at_end=True), RecordingSink()

    async def scenario():
        pump = ThreadDevicePump()
        pump.start(port, sink)
        # hold the event loop while the thread reads and queues its chunks and the PortGone
        time.sleep(0.05)
        pump.stop()
        reads_at_stop = port.reads
        await asyncio.sleep(0.05)
        return reads_at_stop

    reads_at_stop = asyncio.run(scenario())

    assert port.reads > 0
    assert sink.chunks == []
    assert sink.gone == 0
    assert port.reads == reads_at_stop
