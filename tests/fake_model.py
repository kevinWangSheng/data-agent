"""A stand-in for the run's Anthropic-compatible model endpoint, on localhost, for the
classify_text tests (no network, no real model).

It answers `POST .../v1/messages` as the endpoint does. The labels come from `label` (text ->
category, or None to leave the text out of the reply); a batch for which `fail` is true gets an
HTTP 400 whose message quotes the request's Authorization header, as a careless endpoint might.
Every request is kept in `requests`.
"""

import csv
import json
import re
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

FIXTURE = Path(__file__).parent / "fixtures" / "classify_texts.csv"
TEXT = re.compile(r'<text id="(\d+)">\n(.*?)\n</text>', re.DOTALL)
# Tokens the fake reports for every call: uncached input, cached input, output.
USAGE = (1000, 200, 50)


@dataclass
class FakeModel:
    label: Callable[[str], str | None]
    fail: Callable[[list[str]], bool] = lambda texts: False
    usage: tuple[int, int, int] = USAGE
    requests: list[dict[str, Any]] = field(default_factory=list)
    url: str = ""

    def texts(self, request: dict[str, Any]) -> list[str]:
        prompt = request["body"]["messages"][0]["content"]
        return [text for _, text in TEXT.findall(prompt)]


def handler(model: FakeModel) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            request = {"path": self.path, "headers": dict(self.headers), "body": body}
            model.requests.append(request)
            texts = model.texts(request)
            if not self.path.endswith("/v1/messages"):
                self.reply(404, {"type": "error", "error": {"type": "not_found_error"}})
            elif model.fail(texts):
                message = f"fake failure for {self.headers.get('Authorization')}"
                error = {"type": "invalid_request_error", "message": message}
                self.reply(400, {"type": "error", "error": error})
            else:
                labels = {
                    str(n): label
                    for n, text in enumerate(texts, 1)
                    if (label := model.label(text)) is not None
                }
                uncached, cached, output = model.usage
                self.reply(
                    200,
                    {
                        "id": f"msg_{len(model.requests)}",
                        "type": "message",
                        "role": "assistant",
                        "model": body["model"],
                        "content": [{"type": "text", "text": json.dumps(labels)}],
                        "stop_reason": "end_turn",
                        "stop_sequence": None,
                        "usage": {
                            "input_tokens": uncached,
                            "cache_creation_input_tokens": 0,
                            "cache_read_input_tokens": cached,
                            "output_tokens": output,
                        },
                    },
                )

        def reply(self, status: int, payload: dict[str, Any]) -> None:
            data = json.dumps(payload).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, format: str, *args: Any) -> None:
            return None

    return Handler


@contextmanager
def serve(model: FakeModel) -> Iterator[FakeModel]:
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler(model))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    model.url = f"http://127.0.0.1:{server.server_address[1]}/anthropic"
    try:
        yield model
    finally:
        server.shutdown()
        server.server_close()


def fixture_rows() -> list[tuple[int, str, str]]:
    """(id, text, expected category) of the sample texts."""
    with FIXTURE.open(newline="") as f:
        return [(int(r["id"]), r["text"], r["category"]) for r in csv.DictReader(f)]


def by_fixture(text: str) -> str | None:
    """The sample's own category for a sample text; None for any other text."""
    return {t: c for _, t, c in fixture_rows()}.get(text)
