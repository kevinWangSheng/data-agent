"""Starting the adapters from `data-agent web --im FILE` (contract item 7 of ticket 130).

`serve_web` reads the file (`config.load_config` with `os.environ`; an `ImConfigError` stops the
server from starting, as a bad `--data-sources` does), builds the app with `create_app`, then
calls `start(app, configs)` before `uvicorn.run`. It looks `start` up on this module when it
runs (`from data_agent.im import service` inside `serve_web`, as it imports `create_app`), so
the acceptance tests can put a stand-in here; it never starts a platform any other way. Without
`--im` it does not call `start`, and no platform library is imported. Whether the libraries of
the extra `data-agent[im]` are installed is checked inside `start` (with the hint to run
`uv sync --extra im`), never in `serve_web` before it: the acceptance tests run without them.

`start` attaches one `adapter.Adapter` per platform to the app: built with the app's own
`Conversations` and `RunsDir` (the same launcher, so one run at a time holds across the web UI
and every platform), started when the app starts serving, and stopped, with its platform, when
the app shuts down, before the launcher stops the run that is left. An adapter's error is
logged and does not stop the web UI.
"""

import asyncio
import contextlib
import importlib
import logging
from collections.abc import Mapping

from fastapi import FastAPI

from data_agent.im.adapter import Adapter
from data_agent.im.config import PlatformConfig
from data_agent.im.platform import Platform

log = logging.getLogger(__name__)

# The libraries of the extra `data-agent[im]` each implemented platform needs.
LIBRARIES = {
    "slack": ("slack_bolt", "slack_sdk", "aiohttp"),
    "telegram": ("aiogram", "aiohttp"),
}
INSTALL = "run `uv sync --extra im` (or install `data-agent[im]`)"


class ImUnavailable(RuntimeError):
    """A platform of the `--im` file cannot start in this install; the text names it."""


def start(app: object, configs: Mapping[str, PlatformConfig]) -> None:
    """`app` is what `create_app` returned; `configs` what `load_config` did."""
    if not isinstance(app, FastAPI):
        raise TypeError(f"start takes the app create_app made, not {type(app).__name__}")
    for name in configs:
        if name not in LIBRARIES:
            raise ImUnavailable(f"--im: the {name} bot is not implemented yet; leave out [{name}]")
        for library in LIBRARIES[name]:
            try:
                importlib.import_module(library)
            except ImportError:
                raise ImUnavailable(
                    f"--im: the {name} bot needs the IM libraries ({library} is not installed); "
                    + INSTALL
                ) from None
    for config in configs.values():
        platform = make_platform(config)
        adapter = Adapter(platform, app.state.conversations, app.state.runs, config.allowed_users)
        app.state.services.append(Bot(adapter, platform))


def make_platform(config: PlatformConfig) -> Platform:
    # Imported only here: without `--im` no platform library is loaded.
    if config.platform == "telegram":
        from data_agent.im.telegram import TelegramPlatform

        return TelegramPlatform(config)
    from data_agent.im.slack import SlackPlatform

    return SlackPlatform(config)


class Bot:
    """An adapter running beside the app (data_agent.web.app `Service`): `start` connects in a
    task of its own, so a platform that cannot be reached never holds up the web UI, and then
    polls; `stop` ends both. Neither raises: an error is logged."""

    def __init__(self, adapter: Adapter, platform: Platform) -> None:
        self.adapter = adapter
        self.platform = platform
        self.task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        self.task = asyncio.create_task(self.serve(), name=f"im-{self.platform.name}")

    async def serve(self) -> None:
        try:
            await self.adapter.start()
        except Exception:
            log.exception(
                "%s: the bot could not connect; the web UI goes on without it",
                self.platform.name,
            )
            return
        log.info("%s: the bot is connected", self.platform.name)
        await self.adapter.run()

    async def stop(self) -> None:
        if self.task is not None:
            self.task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self.task
        try:
            await self.platform.stop()
        except Exception:
            log.exception("%s: stopping the bot failed", self.platform.name)
