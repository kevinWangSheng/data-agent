"""Entry point of the web backend: the FastAPI app behind `data-agent web`.

Signature fixed by ticket 73 (the acceptance tests in tests/acceptance/test_web_api.py build the
app through it); tickets 77 and 78 implement it.
"""

import subprocess
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

from fastapi import FastAPI


@dataclass(frozen=True)
class DataSource:
    """A data source the run form offers by name (spec Q38). Exactly one of `duckdb` and
    `postgres_env` is set; a Postgres connection string stays in the server's environment under
    that variable and never reaches a browser."""

    name: str
    duckdb: Path | None = None
    postgres_env: str | None = None
    # dbt runs copy this project into their own working directory (spec Q39).
    dbt_project: Path | None = None


StartRun = Callable[[Sequence[str], Path], "subprocess.Popen[bytes]"]
"""Starts `data-agent run` with the given arguments (those after `data-agent run`) in the given
working directory and returns the process."""


def create_app(runs_dir: Path, data_sources: Sequence[DataSource], start_run: StartRun) -> FastAPI:
    """The app serving the runs under `runs_dir` (one subdirectory per run, named by run id, holding
    its event file) and starting new runs through `start_run`."""
    raise NotImplementedError("web backend: tickets 77 and 78")
