"""Every open page's socket: the state on connect, replies to its own changes, and each published message to all."""
import asyncio
import itertools

from _motu_mixer.presentation import view_model as _view_model

# Meters arrive ~17 times a second and only the latest matters: a client that has stopped reading (a phone gone to
# sleep) keeps at most this many queued, while state, delta and status messages are always queued.
METER_BACKLOG = 8


class WebSocketHub:
    def __init__(self, snapshot_provider, view_model=_view_model):
        self._snapshot = snapshot_provider
        self._view_model = view_model
        self._clients = {}
        self._ids = itertools.count(1)

    async def register(self, ws):
        client_id = f"client-{next(self._ids)}"
        queue = asyncio.Queue()
        queue.put_nowait(self._view_model.state_message(self._snapshot()))
        task = asyncio.get_running_loop().create_task(self._send_each(client_id, ws, queue))
        self._clients[client_id] = (queue, task)
        return client_id

    def unregister(self, client_id):
        client = self._clients.pop(client_id, None)
        if client is not None:
            client[1].cancel()

    async def reply(self, client_id, outcome):
        client = self._clients.get(client_id)
        if client is not None:
            client[0].put_nowait(self._view_model.outcome_json(outcome))

    def publish_state(self, snapshot):
        self._broadcast(self._view_model.state_message(snapshot))

    def publish_delta(self, deltas):
        self._broadcast(self._view_model.delta_message(deltas))

    def publish_status(self, status, detail, loops):
        self._broadcast(self._view_model.status_message(status, detail, loops))

    def publish_meters(self, levels):
        message = self._view_model.meters_message(levels)
        for queue, _ in self._clients.values():
            if queue.qsize() < METER_BACKLOG:
                queue.put_nowait(message)

    def _broadcast(self, message):
        if message is None:
            return
        for queue, _ in self._clients.values():
            queue.put_nowait(message)

    async def _send_each(self, client_id, ws, queue):
        while True:
            message = await queue.get()
            try:
                await ws.send_json(message)
            except Exception:
                self._clients.pop(client_id, None)
                return
