"""Stand-in for DAB's ExecTool, written for our tests (no DAB code).

Same interface: constructed with a work directory, `exec({"code", "env"})` runs the code with the
`env` entries as variables in the work directory and returns `{"success": bool, "result": ...}`,
the result being what the code printed after the result marker. DAB runs the code in a Docker
container; this runs it in a local Python subprocess.
"""

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

from common_scaffold.tools.exec_utils.parse_result import parse_result_python

DESCRIPTION = "FAKE-DAB exec description."
CODE_ARG = "FAKE-DAB code argument."
RUNNER = "import sys\nenv = {env!r}\nexec(sys.stdin.read(), env)\n"


class ExecTool:
    def __init__(self, log_path: str, name: str, work_dir: Path, timeout: int = 600) -> None:
        self.log_path = log_path
        self.name = name
        self.work_dir = work_dir
        self.timeout = timeout

    def get_spec(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": DESCRIPTION,
                "parameters": {
                    "type": "object",
                    "properties": {"code": {"type": "string", "description": CODE_ARG}},
                    "required": ["code"],
                },
            },
        }

    def exec(self, args: dict[str, Any]) -> dict[str, Any]:
        with open(self.log_path, "a") as f:
            f.write(json.dumps({"tool_name": self.name, "args": args}) + "\n")
        proc = subprocess.run(
            [sys.executable, "-c", RUNNER.format(env=args["env"])],
            input=args["code"],
            capture_output=True,
            text=True,
            cwd=self.work_dir,
            timeout=self.timeout,
        )
        if proc.returncode != 0:
            return {"success": False, "result": proc.stderr.strip().splitlines()[-1]}
        try:
            return {"success": True, "result": parse_result_python(proc.stdout)}
        except ValueError as e:
            return {"success": False, "result": str(e)}

    def clean_up(self) -> None:
        pass
