"""Reads the control port on its own thread and hands each chunk to the sink on the event loop."""
import asyncio
import threading
import time

from _motu_mixer.application.ports import BytesSink, ControlPort, PortGone

IDLE_WAIT = 0.001


class ThreadDevicePump:
    def __init__(self):
        self._thread = None
        self._stopped = threading.Event()

    def start(self, port: ControlPort, sink: BytesSink) -> None:
        loop = asyncio.get_running_loop()
        stopped = self._stopped = threading.Event()

        def deliver(callback, *args):
            if not stopped.is_set():
                callback(*args)

        def pump():
            while not stopped.is_set():
                try:
                    chunk = port.read()
                except PortGone:
                    loop.call_soon_threadsafe(deliver, sink.on_port_gone)
                    return
                if chunk:
                    loop.call_soon_threadsafe(deliver, sink.on_bytes, chunk)
                else:
                    time.sleep(IDLE_WAIT)

        self._thread = threading.Thread(target=pump, name="motu-device-pump", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stopped.set()
        if self._thread is not None:
            self._thread.join()
        self._thread = None
