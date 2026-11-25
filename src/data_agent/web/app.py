"""Entry point of the web backend: the FastAPI app behind `data-agent web`.

Signature fixed by ticket 73 (the acceptance tests in tests/acceptance/test_web_api.py build the
app through it); tickets 77 and 78 implement it.
"""

import json
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, Any, Protocol

import anyio
from anyio import to_thread
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import (
    FileResponse,
    JSONResponse,
    PlainTextResponse,
    Response,
    StreamingResponse,
)
from pydantic import BaseModel

from data_agent.run_config import CONFIG_NAMES
from data_agent.web.conversations import Conversations
from data_agent.web.launcher import TASK_TYPES, Launcher, Refused, StartRun
from data_agent.web.runs import RunsDir

# Re-exported: the acceptance tests (ticket 73) import DataSource from this module.
from data_agent.web.sources import DataSource as DataSource

# The built web UI: a wheel carries it (hatch_build.py); a checkout gets it from
# `pnpm --dir web build:local`.
UI_DIR = Path(__file__).parent / "static"
NOT_BUILT = (
    "The web UI is not built into this install. In a checkout, build it with "
    "`pnpm --dir web install && pnpm --dir web build:local`; a wheel built where pnpm is on PATH "
    "carries it. The API under /api works without it.\n"
)

# How often a stream looks for new events, and how long it stays silent before a keepalive.
POLL_SECONDS = 0.25
KEEPALIVE_SECONDS = 15.0


class Submit(BaseModel):
    task: str
    task_type: str
    data_source: str
    config: str
    network: bool = True


class NewConversation(BaseModel):
    """Empty: a conversation is not made for a task type or a data source."""


class Message(BaseModel):
    text: str
    network: bool = True


class Service(Protocol):
    """Something running beside the app for as long as it serves (the IM bot); neither method
    raises."""

    async def start(self) -> None: ...

    async def stop(self) -> None: ...


def create_app(
    runs_dir: Path,
    data_sources: Sequence[DataSource],
    start_run: StartRun,
    ui_dir: Path = UI_DIR,
    mcp_servers: Path | None = None,
) -> FastAPI:
    """The app serving the runs under `runs_dir` (one subdirectory per run, named by run id, holding
    its event file), starting new runs through `start_run`, and serving the built web UI from
    `ui_dir`. ValueError when a dbt run could not copy a source's project into its own working
    directory (`check_layout`). With `mcp_servers` (ticket 116) every run, of the run form or a
    conversation, gets that path as `--mcp-servers`."""
    runs = RunsDir(runs_dir)
    launcher = Launcher(runs, data_sources, start_run, mcp_servers)
    conversations = Conversations(runs, launcher, data_sources)
    # What `data-agent web --im` attaches (data_agent.im.service.start): started once the app
    # serves, stopped before the launcher stops the run that is left.
    services: list[Service] = []

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        for service in services:
            await service.start()
        yield
        for service in reversed(services):
            await service.stop()
        # The server leaves no run it started behind.
        await to_thread.run_sync(launcher.shutdown)

    app = FastAPI(title="data-agent", lifespan=lifespan)
    app.state.runs = runs
    app.state.conversations = conversations
    app.state.services = services

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
        return await to_thread.run_sync(runs.summaries)

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
                launcher.submit,
                body.task,
                body.task_type,
                body.data_source,
                body.config,
                body.network,
            )
        except Refused as exc:
            raise HTTPException(exc.status, exc.detail) from exc
        return {"id": run_id}

    @app.post("/api/runs/{run_id}/cancel")
    async def cancel(run_id: str) -> dict[str, Any]:
        """Stops the run; answers with its row as it ended."""
        try:
            return await to_thread.run_sync(launcher.cancel, run_id)
        except Refused as exc:
            raise HTTPException(exc.status, exc.detail) from exc

    @app.get("/api/conversations")
    async def list_conversations() -> list[dict[str, Any]]:
        """Every conversation, and every run in none as a conversation of one turn."""
        return await to_thread.run_sync(conversations.rows)

    @app.get("/api/conversations/{conversation}")
    async def conversation_row(conversation: str) -> dict[str, Any]:
        row = await to_thread.run_sync(conversations.row, conversation)
        if row is None:
            raise HTTPException(404, f"no conversation {conversation}")
        return row

    @app.post("/api/conversations", status_code=201)
    async def new_conversation(_: NewConversation) -> dict[str, str]:
        """Takes no task type and no data source: a turn gets every configured source."""
        return {"id": await to_thread.run_sync(conversations.create)}

    @app.post("/api/conversations/{conversation}/messages")
    async def send(conversation: str, body: Message) -> JSONResponse:
        """A new turn (201) when none is running, else a steer into the running one (202)."""
        try:
            sent = await to_thread.run_sync(
                conversations.send, conversation, body.text, body.network
            )
        except Refused as exc:
            raise HTTPException(exc.status, exc.detail) from exc
        return JSONResponse(sent, status_code=201 if sent["kind"] == "turn" else 202)

    @app.post("/api/conversations/{conversation}/cancel")
    async def cancel_turn(conversation: str) -> dict[str, Any]:
        """Stops the running turn; answers with the conversation's row."""
        try:
            return await to_thread.run_sync(conversations.cancel, conversation)
        except Refused as exc:
            raise HTTPException(exc.status, exc.detail) from exc

    serve_ui(app, ui_dir)
    return app


def serve_ui(app: FastAPI, ui_dir: Path) -> None:
    """The UI's bundle under `/assets/`, and its page at every other address outside `/api/`: the
    app routes in the browser, so a reload of `/runs/<id>` gets the page too. Registered after the
    API, so it never shadows an API route."""
    root = ui_dir.resolve()

    @app.get("/{path:path}", include_in_schema=False)
    def page(path: str) -> Response:
        if path == "api" or path.startswith("api/"):
            raise HTTPException(404, "Not Found")
        if path.startswith("assets/"):
            file = (root / path).resolve()
            if file.is_relative_to(root) and file.is_file():
                # Vite names each bundle file by its content hash.
                return FileResponse(file, headers={"Cache-Control": "max-age=31536000, immutable"})
            raise HTTPException(404, "Not Found")
        index = root / "index.html"
        if not index.is_file():
            return PlainTextResponse(NOT_BUILT, status_code=503)
        # Revalidated each time, so a new install's page names its new bundle.
        return FileResponse(index, headers={"Cache-Control": "no-cache"})


async def stream(runs: RunsDir, run_id: str, cursor: int, request: Request) -> AsyncIterator[str]:
    """SSE after SignalPilot's run stream (docs/research/2026-11-01-upstream-web-ui.md "后端"):
    the backlog after the cursor, then each new event as the run appends it, one frame per event
    with its `seq` as the frame id; the stream closes once the `result` event is sent, or once a run
    this server saw exit without one has nothing more to send. While nothing arrives it sends a
    comment line now and then, so proxies and browsers keep the connection."""
    offset = 0
    idle = 0.0
    while not await request.is_disconnected():
        # Checked before the read: the record says a run ended only after its process exited.
        ended = await to_thread.run_sync(runs.ended, run_id)
        chunk = await to_thread.run_sync(runs.read, run_id, offset)
        if chunk is None or not chunk.events:
            if ended:
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
