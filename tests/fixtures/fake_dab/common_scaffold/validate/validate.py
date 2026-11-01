"""Stand-in for DAB's validate entry point, written for our tests (no DAB code).

Same contract: an empty answer fails without running the query's checker; otherwise the query
directory's `validate.py` is loaded and its `validate(answer)` returns `(is_valid, reason)`, which
comes back in a dict under `is_valid` and `reason`.
"""

import importlib.util
from pathlib import Path
from typing import Any


def validate(query_dir: Path, llm_answer: str, reason: str | None = None) -> dict[str, Any]:
    if llm_answer == "":
        return {"is_valid": False, "reason": reason}
    spec = importlib.util.spec_from_file_location("validate", str(query_dir / "validate.py"))
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    is_valid, why = module.validate(llm_answer)
    return {"is_valid": is_valid, "reason": why}
