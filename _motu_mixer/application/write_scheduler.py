"""Sends planned writes to the control port: latest value per address, paced per address, in submit order."""
import asyncio
from collections import deque

from _motu_mixer.domain.values import SentFrame
from _motu_mixer.protocol.sysex import encode_set

from .ports import PortGone

# A little over 1/50 s between frames to one address keeps any one-second window at 50 or fewer.
MIN_INTERVAL = 0.021
LOG_SIZE = 500


class WriteScheduler:
    def __init__(self, clock, encode=encode_set):
        self._clock = clock
        self._encode = encode
        self._port = None
        self._task = None
        self._wake = None
        self._pending = {}
        self._next_allowed = {}
        self._log = deque(maxlen=LOG_SIZE)

    def attach(self, port):
        self.detach()
        self._port = port
        self._wake = asyncio.Event()
        self._task = asyncio.get_running_loop().create_task(self._send(port))

    def detach(self):
        if self._task is not None:
            self._task.cancel()
        self._task = None
        self._port = None
        self._pending.clear()
        self._next_allowed.clear()

    def submit(self, writes):
        frames = tuple(self._encode(write.address, write.value) for write in writes)
        if self._port is not None:
            for write, frame in zip(writes, frames):
                self._pending[write.address] = frame
            self._wake.set()
        return frames

    def sent_log(self, n):
        return tuple(self._log)[-n:] if n > 0 else ()

    async def _send(self, port):
        while True:
            if not self._pending:
                self._wake.clear()
                await self._wake.wait()
                continue
            now = self._clock.monotonic()
            ready = next((a for a in self._pending if self._next_allowed.get(a, now) <= now), None)
            if ready is None:
                await self._clock.sleep(min(self._next_allowed[a] for a in self._pending) - now)
                continue
            frame = self._pending.pop(ready)
            try:
                port.write(frame)
            except PortGone:
                self._task = None
                self.detach()
                return
            self._next_allowed[ready] = now + MIN_INTERVAL
            self._log.append(SentFrame(now, frame))
