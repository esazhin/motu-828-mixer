"""The HTTP and WebSocket face of the service: the page, its state, changes, the port hand-over and the sent log."""
import asyncio
import contextlib
import json
from pathlib import Path

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse

from _motu_mixer.application.ports import ChangeOutcome
from _motu_mixer.domain.status import Refusal
from _motu_mixer.presentation import view_model as _view_model

_ASSETS = {
    "/": ("index.html", "text/html"),
    "/app.js": ("app.js", "application/javascript"),
    "/mixer_client.js": ("mixer_client.js", "application/javascript"),
    "/eq_math.js": ("eq_math.js", "application/javascript"),
}


def create_app(service, session, hub, static_dir, view_model=_view_model):
    @contextlib.asynccontextmanager
    async def lifespan(app):
        running = asyncio.get_running_loop().create_task(session.run())
        try:
            yield
        finally:
            session.stop()
            await running

    app = FastAPI(lifespan=lifespan)
    static_dir = Path(static_dir)

    def asset(name, media_type):
        return lambda: FileResponse(static_dir / name, media_type=media_type)

    for path, (name, media_type) in _ASSETS.items():
        app.add_api_route(path, asset(name, media_type), methods=["GET"])

    def outcome_for(obj, client_id):
        action = view_model.parse_action(obj)
        if isinstance(action, Refusal):
            return ChangeOutcome.Refused(action)
        return service.request_change(action, client_id)

    @app.get("/api/state")
    def state():
        return view_model.snapshot_json(service.snapshot())

    @app.post("/api/change")
    async def change(request: Request):
        try:
            body = json.loads(await request.body())
        except ValueError:
            body = None
        outcome = outcome_for(body, None)
        status = 200 if isinstance(outcome, ChangeOutcome.Accepted) else 409
        return JSONResponse(view_model.outcome_json(outcome), status_code=status)

    @app.post("/api/port/release")
    async def release():
        return {"status": str(await session.release())}

    @app.post("/api/port/acquire")
    async def acquire():
        return {"status": str(await session.acquire())}

    @app.get("/api/diag/sent")
    def sent(n: int = 50):
        return [{"t": entry.t, "hex": entry.frame.hex(" ")} for entry in service.sent_log(n)]

    @app.websocket("/ws")
    async def socket(ws: WebSocket):
        await ws.accept()
        client_id = await hub.register(ws)
        try:
            while True:
                try:
                    message = json.loads(await ws.receive_text())
                except ValueError:
                    continue
                if isinstance(message, dict) and message.get("type") == "change":
                    await hub.reply(client_id, outcome_for(message.get("action"), client_id))
        except WebSocketDisconnect:
            pass
        finally:
            hub.unregister(client_id)

    return app
