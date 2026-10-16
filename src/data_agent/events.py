"""The run's event file: one append-only JSONL file per run, the run's only raw record (plan §5).

Every event carries a consecutive `seq` (from 0), a `type` and a UTC `ts`. Known secret values and
the model endpoint are masked before anything reaches the file, so event files can be published.
"""

import json
import re
from collections.abc import Iterable, Mapping
from datetime import UTC, datetime
from pathlib import Path
from types import TracebackType
from typing import Any, Self
from urllib.parse import urlsplit

import psycopg
from psycopg.conninfo import conninfo_to_dict

REDACTED = "[REDACTED]"
HOST_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9-]*")
LOOPBACK_NAMES = frozenset({"localhost", "ip6-localhost", "ip6-loopback"})

# Environment variables whose values must never reach an event file; DATA_AGENT_ANTHROPIC_API_KEY
# is the optional Claude key from .env.example, masked whether or not this run uses it.
SECRET_ENV = ("DATA_AGENT_API_KEY", "DATA_AGENT_ANTHROPIC_API_KEY", "DATA_AGENT_BASE_URL")


def known_secrets(env: Mapping[str, str]) -> list[str]:
    """Values to mask: API keys, the model endpoint, and the endpoint's host on its own."""
    secrets = [env[name] for name in SECRET_ENV if env.get(name)]
    host = urlsplit(env.get("DATA_AGENT_BASE_URL", "")).hostname
    if host:
        secrets.append(host)
    return secrets


def url_hosts(url: str) -> list[str]:
    """The hosts a database connection string (URL or key=value form) names; [] when unparsable."""
    try:
        params = conninfo_to_dict(url) if url else {}
    except psycopg.ProgrammingError:
        return []
    hosts = str(params.get("host") or "").split(",") + str(params.get("hostaddr") or "").split(",")
    return [host for host in hosts if host]


def url_secrets(url: str) -> list[str]:
    """Values to mask anywhere for a database connection string: the string, its password, and
    its hosts when they are addresses or domain names (bare names are `url_names`)."""
    if not url:
        return []
    try:
        params = conninfo_to_dict(url)
    except psycopg.ProgrammingError:
        return [url]
    hosts = [host for host in url_hosts(url) if "." in host or ":" in host]
    return [url, str(params.get("password") or ""), *hosts]


def url_names(url: str) -> list[str]:
    """Single-label hosts such as `postgres` or `prod-db` (common under Docker and Kubernetes),
    masked only as whole words so `dbt` survives a host named `db`. Loopback names and socket
    directories say nothing about the server."""
    return [
        host
        for host in url_hosts(url)
        if HOST_NAME.fullmatch(host) and host.lower() not in LOOPBACK_NAMES
    ]


def mask(text: str, secrets: Iterable[str], names: Iterable[str] = ()) -> str:
    """`text` with every secret replaced wherever it appears and every name replaced as a whole
    word. Longest first, so a URL is masked whole before its host would be."""
    for secret in sorted({s for s in secrets if s}, key=len, reverse=True):
        text = text.replace(secret, REDACTED)
    for name in sorted({n for n in names if n}, key=len, reverse=True):
        text = re.sub(rf"(?<![\w-]){re.escape(name)}(?![\w-])", REDACTED, text)
    return text


class EventLog:
    def __init__(self, path: Path, secrets: Iterable[str], names: Iterable[str] = ()) -> None:
        self._secrets = list(secrets)
        self._names = list(names)
        self._file = path.open("x", encoding="utf-8")
        self._seq = 0

    def emit(self, type: str, **fields: Any) -> None:
        event = {"seq": self._seq, "type": type, "ts": datetime.now(UTC).isoformat(), **fields}
        line = json.dumps(self._mask(event), ensure_ascii=False)
        self._file.write(line + "\n")
        self._file.flush()
        self._seq += 1

    def close(self) -> None:
        self._file.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()

    def _mask(self, value: Any) -> Any:
        if isinstance(value, str):
            return mask(value, self._secrets, self._names)
        if isinstance(value, Mapping):
            return {str(key): self._mask(item) for key, item in value.items()}
        if isinstance(value, list | tuple):
            return [self._mask(item) for item in value]
        if value is None or isinstance(value, bool | int | float):
            return value
        # Anything else is written as its text, masked like any other string.
        return self._mask(str(value))
