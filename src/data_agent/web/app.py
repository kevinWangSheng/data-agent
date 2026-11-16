"""Entry point of the web backend: the FastAPI app behind `data-agent web`.

Signature fixed by ticket 73 (the acceptance tests in tests/acceptance/test_web_api.py build the
app through it); tickets 77 and 78 implement it.
"""

import json
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, Any

import anyio
from anyio import to_thread
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse
from pydantic import BaseModel

from data_agent.run_config import CONFIG_NAMES
from data_agent.web.launcher import TASK_TYPES, Launcher, Refused, StartRun
from data_agent.web.runs import RunsDir
from data_agent.web.sources import DataSource as DataSource

# How often a stream looks for new events, and how long it stays silent before a keepalive.
POLL_SECONDS = 0.25
KEEPALIVE_SECONDS = 15.0


class Submit(BaseModel):
    task: str
    task_type: str
    data_source: str
    config: str


def create_app(runs_dir: Path, data_sources: Sequence[DataSource], start_run: StartRun) -> FastAPI:
    """The app serving the runs under `runs_dir` (one subdirectory per run, named by run id, holding
    its event file) and starting new runs through `start_run`."""
    runs = RunsDir(runs_dir)
    launcher = Launcher(runs, data_sources, start_run)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        yield
        # The server leaves no run it started behind.
        await to_thread.run_sync(launcher.shutdown)

    app = FastAPI(title="data-agent", lifespan=lifespan)

    @app.get("/api/options")
    def options() -> dict[str, list[str]]:
        """What the run form offers: data sources by name only, never their connections."""
        return {
            "data_sources": [source.name for source in data_sources],
            "task_types": list(TASK_TYPES),
            "configs": list(CONFIG_NAMES),
        }

    @app.get("/api/runs")
    async def list_runs() -> list[dict[str, Any]]:
        listed = await to_thread.run_sync(runs.summaries)
        return await to_thread.run_sync(launcher.unwritten) + listed

    @app.get("/api/runs/{run_id}/events", response_model=None)
    async def read_events(
        run_id: str,
        request: Request,
        after: int = -1,
        last_event_id: Annotated[int | None, Header()] = None,
    ) -> Response:
        """The run's events after the cursor: as JSON, or as an SSE stream when the client asks
        for `text/event-stream` (the cursor is then its `Last-Event-ID`, when it sends one)."""
        if runs.run_dir(run_id) is None:
            raise HTTPException(404, f"no run {run_id}")
        if "text/event-stream" in request.headers.get("accept", ""):
            cursor = after if last_event_id is None else last_event_id
            return StreamingResponse(
                stream(runs, run_id, cursor, request), media_type="text/event-stream"
            )
        chunk = await to_thread.run_sync(runs.read, run_id)
        events = chunk.events if chunk is not None else []
        return JSONResponse({"events": [event for event in events if event["seq"] > after]})

    @app.post("/api/runs", status_code=201)
    async def submit(body: Submit) -> dict[str, str]:
        """Starts a run; the data source is named, never given as a connection."""
        try:
            run_id = await to_thread.run_sync(
                launcher.submit, body.task, body.task_type, body.data_source, body.config
            )
        except Refused as exc:
            raise HTTPException(exc.status, exc.detail) from exc
        return {"id": run_id}

    @app.post("/api/runs/{run_id}/cancel")
    async def cancel(run_id: str) -> dict[str, str]:
        try:
            await to_thread.run_sync(launcher.cancel, run_id)
        except Refused as exc:
            raise HTTPException(exc.status, exc.detail) from exc
        return {"id": run_id, "status": "cancelled"}

    return app


async def stream(runs: RunsDir, run_id: str, cursor: int, request: Request) -> AsyncIterator[str]:
    """SSE after SignalPilot's run stream (docs/research/2026-11-01-upstream-web-ui.md "后端"):
    the backlog after the cursor, then each new event as the run appends it, one frame per event
    with its `seq` as the frame id; the stream closes once the `result` event is sent, or once a run
    this server cancelled has nothing more to send. While nothing
    arrives it sends a comment line now and then, so proxies and browsers keep the connection."""
    offset = 0
    idle = 0.0
    while not await request.is_disconnected():
        # Read before the events: a cancelled run writes nothing after its marker.
        cancelled = await to_thread.run_sync(runs.cancelled, run_id)
        chunk = await to_thread.run_sync(runs.read, run_id, offset)
        if chunk is None or not chunk.events:
            if cancelled:
                return
            await anyio.sleep(POLL_SECONDS)
            idle += POLL_SECONDS
            if idle >= KEEPALIVE_SECONDS:
                idle = 0.0
                yield ": keepalive\n\n"
            continue
        offset, idle = chunk.offset, 0.0
        for event in chunk.events:
            if event["seq"] > cursor:
                yield f"id: {event['seq']}\ndata: {json.dumps(event, ensure_ascii=False)}\n\n"
        if any(event["type"] == "result" for event in chunk.events):
            return
