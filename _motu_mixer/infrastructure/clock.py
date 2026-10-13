"""The real clock: monotonic seconds and an event-loop sleep."""
import asyncio
import time


class AsyncioClock:
    def monotonic(self) -> float:
        return time.monotonic()

    async def sleep(self, seconds: float) -> None:
        await asyncio.sleep(seconds)
