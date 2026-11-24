"""Starting the adapters from `data-agent web --im FILE` (contract item 7 of ticket 130).

`serve_web` reads the file (`config.load_config` with `os.environ`; an `ImConfigError` stops the
server from starting, as a bad `--data-sources` does), builds the app with `create_app`, then
calls `start(app, configs)` before `uvicorn.run`. It looks `start` up on this module when it
runs (`from data_agent.im import service` inside `serve_web`, as it imports `create_app`), so
the acceptance tests can put a stand-in here; it never starts a platform any other way. Without
`--im` it does not call `start`, and no platform library is imported.

`start` attaches one `adapter.Adapter` per platform to the app: built with the app's own
`Conversations` and `RunsDir` (the same launcher, so one run at a time holds across the web UI
and every platform), started when the app starts serving, and stopped, with its platform, when
the app shuts down, before the launcher stops the run that is left. An adapter's error is
logged and does not stop the web UI.
"""

from collections.abc import Mapping

from data_agent.im.config import PlatformConfig


def start(app: object, configs: Mapping[str, PlatformConfig]) -> None:
    """`app` is what `create_app` returned; `configs` what `load_config` did."""
    raise NotImplementedError("ticket 131")
