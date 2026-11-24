"""The status message's text of a turn (.scratch/im/spec.md "澄清、进度、取消"; contract item 6 of
ticket 130), in English as the web UI.

While the turn runs, the text is its current step, from the latest event that names one:

- `tool_start`, by the tool's kind as the web UI's `toolKind` has it
  (web/src/run-page/tools.ts): querying the database (`query_database`, ...), reading the
  schema or the context, checking, running a sandbox command, running dbt, drawing a chart, or
  an external server's tool by its server and tool name;
- `verifier_start`: verifying;
- `thinking`: thinking;
- `answer`: writing the answer.

Before any of them it says the turn has started. Once the turn has ended the text says how,
differently from every running text:

- a `result`: done (or, after a `clarification` with no `answer`, waiting for the answer to the
  question), or failed; with the duration and the cost (`result.cost_usd`). The duration runs
  from the first event's `ts` to `result.ended_ts` (ticket 122), or `result.ts` without it, as
  the web UI's run list counts it;
- no `result`, and the server's record says `cancelled` or `failure` (`ended`): stopped, or
  failed.
"""

from collections.abc import Sequence
from typing import Any


def status_text(events: Sequence[dict[str, Any]], ended: str | None = None) -> str:
    """The text for a turn whose events so far are `events`; `ended` is the record's status of a
    run that exited without a `result` (`cancelled`, `failure`), else None."""
    raise NotImplementedError("ticket 131")
