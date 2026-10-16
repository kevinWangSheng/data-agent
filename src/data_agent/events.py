"""The run's event file: one append-only JSONL file per run, the run's only raw record (plan §5).

Every event carries a consecutive `seq` (from 0), a `type` and a UTC `ts`. Known secret values and
the model endpoint are masked before anything reaches the file, so event files can be published.
"""

import json
from collections.abc import Iterable, Mapping
from datetime import UTC, datetime
from pathlib import Path
from types import TracebackType
from typing import Any, Self
from urllib.parse import urlsplit

import psycopg
from psycopg.conninfo import conninfo_to_dict

REDACTED = "[REDACTED]"

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


def url_secrets(url: str) -> list[str]:
    """Values to mask for a database connection string (URL or key=value form): the string, its
    password, and its hosts when they are addresses or domain names. A bare name such as `db`
    is left alone, since masking it would mangle unrelated words."""
    if not url:
        return []
    try:
        params = conninfo_to_dict(url)
    except psycopg.ProgrammingError:
        return [url]
    hosts = str(params.get("host") or "").split(",") + str(params.get("hostaddr") or "").split(",")
    return [
        url,
        str(params.get("password") or ""),
        *(host for host in hosts if "." in host or ":" in host),
    ]


class EventLog:
    def __init__(self, path: Path, secrets: Iterable[str]) -> None:
        # Longest first, so a URL is masked whole before its host would be.
        self._secrets = sorted({s for s in secrets if s}, key=len, reverse=True)
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
            for secret in self._secrets:
                value = value.replace(secret, REDACTED)
            return value
        if isinstance(value, Mapping):
            return {str(key): self._mask(item) for key, item in value.items()}
        if isinstance(value, list | tuple):
            return [self._mask(item) for item in value]
        if value is None or isinstance(value, bool | int | float):
            return value
        # Anything else is written as its text, masked like any other string.
        return self._mask(str(value))
