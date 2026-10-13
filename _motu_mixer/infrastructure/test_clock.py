import asyncio
import time

from _motu_mixer.application.ports import Clock
from _motu_mixer.infrastructure.clock import AsyncioClock


def test_clock_reads_non_decreasing_seconds_and_sleeps_at_least_as_asked_while_the_loop_runs():
    clock = AsyncioClock()
    assert isinstance(clock, Clock)

    readings = [clock.monotonic() for _ in range(1000)]
    assert all(isinstance(r, float) for r in readings)
    assert readings == sorted(readings)

    async def scenario():
        ran_meanwhile = []

        async def other_task():
            ran_meanwhile.append(True)

        started = clock.monotonic()
        wall_started = time.monotonic()
        other = asyncio.create_task(other_task())
        await clock.sleep(0.05)
        ran_during_sleep = list(ran_meanwhile)
        await other
        return clock.monotonic() - started, time.monotonic() - wall_started, ran_during_sleep

    by_clock, by_wall, ran_meanwhile = asyncio.run(scenario())

    assert by_clock >= 0.05
    assert by_wall >= 0.05
    # the sleep hands the event loop to other tasks
    assert ran_meanwhile == [True]
