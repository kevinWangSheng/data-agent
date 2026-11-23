"""A stand-in for an stdio MCP server's command in tests/acceptance/test_mcp_launch.py (ticket 115).

`python print_env.py OUT` writes its own environment (`os.environ`) as a JSON object to OUT, then
copies stdin to stdout line by line, unchanged, until stdin ends. The test reads OUT to see what a
server started through the stdio wrapper is given, and the echo to see that the wrapper passes
stdin and stdout through untouched (an MCP server speaks JSON-RPC over them). Standard library
only.
"""

import json
import os
import sys
from pathlib import Path


def main() -> None:
    Path(sys.argv[1]).write_text(json.dumps(dict(os.environ)), encoding="utf-8")
    for line in sys.stdin:
        sys.stdout.write(line)
        sys.stdout.flush()


if __name__ == "__main__":
    main()
