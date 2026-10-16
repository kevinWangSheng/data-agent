"""Stand-in for DAB's result parsing, written for our tests (no DAB code)."""

import json

RESULT_MARKER = "FAKE-RESULT:"


def parse_result_python(text: str) -> object:
    _, marker, rest = text.rpartition(RESULT_MARKER)
    if not marker:
        raise ValueError(f"{RESULT_MARKER} not found in the printed output.")
    return json.loads(rest)
