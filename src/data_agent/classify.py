"""classify_text (ticket 70): label each row's text with one of the given categories, in batches
sent to the run's own model. Both tool servers (`mcp_server`, `evals.dab_server`) serve it from
here; each fetches the rows through its read-only path and stores the label table its own way.

The model and its endpoint are the run's (`ModelSettings.tool_env`, set for the server's process);
every call's tokens go into the run's spend ledger (`tool_spend`), and no batch starts once the
run's spend has reached its budget. Labels are facts: a label outside the categories, a row the
reply left out, or a failed call is reported as such and gets no category.

Ticket 106 (ideas from Stela's `semantic-analysis`: its unresolved outcome and `resume=`): with
`allow_unresolved` the model may answer UNRESOLVED_MARK for a text it cannot place, and the row is
`unresolved`; a call may resume from an earlier label table of the run, sending the model only the
rows that table left as `failed` or `no_label` (and `unresolved`, when asked) or does not have;
and a row's text may be several columns, each given by its name.
"""

import json
import re
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import anthropic
from anthropic.types import MessageParam
from mcp.server.mcpserver.exceptions import ToolError
from sqlglot import exp

from data_agent.agent import ConfigError, ModelSettings
from data_agent.db_facts import RunQuery
from data_agent.events import known_secrets, mask
from data_agent.gateway import MAX_ROWS, QueryResult
from data_agent.tool_spend import LEDGER_ENV, Ledger, RunState, ToolTokens

TOOL = "classify_text"
# Rows one call may classify (provisional; the main session sets it from the measured cost per
# 1,000 rows). More is an error before any model call, never a cut.
ROW_LIMIT = 20_000
BATCH_ROWS = 50
BATCH_CHARS = 30_000  # a longer text goes in a batch of its own
MAX_CONCURRENT_BATCHES = 8
MAX_OUTPUT_TOKENS = 8192
MODEL_TIMEOUT_S = 120.0
MODEL_RETRIES = 2

# The columns the label table adds to the query's (its text column left out).
LABEL, OUTCOME, MODEL_LABEL = "label", "outcome", "model_label"
ADDED_COLUMNS = (LABEL, OUTCOME, MODEL_LABEL)
LABELED = "labeled"
OUTSIDE = "outside_categories"  # the model answered something not in the list
NO_LABEL = "no_label"  # the model's reply left the row out
FAILED = "failed"  # the call failed or its reply could not be read
EMPTY = "empty_text"  # nothing to classify; no call made
UNRESOLVED = "unresolved"  # the model answered UNRESOLVED_MARK (only with allow_unresolved)
UNRESOLVED_MARK = "__unresolved__"
UNRESOLVED_NOTE = "the model answered it could not tell which category fits"
RETRIED = (FAILED, NO_LABEL)  # the outcomes a resume sends to the model again
UNLABELED_NOTES = {
    OUTSIDE: "the model answered a label outside the categories",
    NO_LABEL: "the model's reply had no label for them",
    FAILED: "the model call failed or its reply could not be read",
    EMPTY: "their text is empty (not sent to the model)",
}

# The tool's description on both servers; {source} names where the rows come from, {stored}
# where the label table goes.
DESCRIPTION = """\
Label each row of a read-only query with one of `categories`, by the meaning of its text, using \
this run's model in batches. Use it when the answer depends on what texts are about (topic, \
sentiment, kind) and matching keywords would only approximate it.

{source} It returns one row per item: `key_column` identifies each row (no repeats) and \
`text_column` holds its text; any other columns are kept in the label table. At most \
{limit} rows per call: more is an error and nothing is classified. `instructions` (optional) \
says how to choose between the categories.

`text_column` may be a list of columns: each row's text is then every non-empty one of them, \
each line starting with the column's name ("title: ..."). `allow_unresolved` (default false) \
lets the model answer `{mark}` for a text it cannot place in a category; such a row gets the \
outcome `unresolved` and no category. `resume_from` takes {resumed}: this call then fetches \
the same query's rows (give the same `sql`, `key_column` and `text_column`) and sends the model \
only the rows that table left `failed` or `no_label`, plus those it does not have (left out \
when the budget was reached); with `retry_unresolved` its `unresolved` rows too. Every other row \
is kept as it was, and the merged table is stored as a new one.

Returns the number of rows per category; the rows that got no category, by reason (a label \
outside the categories, no label in the reply, a failed model call, an empty text, or the \
run's budget reached); and where the label table is: {stored} It has the query's columns \
except the text, then `label` (a category, or null), `outcome` and `model_label` (the model's \
answer as given).{budget}
"""
# DESCRIPTION's last paragraph when the run's ledger is there; {budget} is the ledger's.
BUDGET_NOTE = """

The model calls are paid from this run's budget of ${budget} (US dollars at this run's model \
prices). Every reply ends with the run's spend so far against it. Once the spend passes the \
budget the run ends at once with no answer, so a call that reaches the budget leaves no turn to \
use its labels. This is the limit that ends the run: a "USD budget" system reminder, if you see \
one, is counted at other prices against a higher backstop and leaves this tool's calls out."""

SYSTEM_PROMPT = """\
You sort texts into categories. You get a list of categories, possibly instructions on how to \
choose, and numbered texts. Give every text exactly one category from the list, written exactly \
as it is in the list. Reply with only a JSON object that maps each text's number to its category, \
for example {"1": "<category>", "2": "<category>"}, and nothing else.
"""
UNRESOLVED_RULE = f"""\
If a text does not let you tell which category fits, answer "{UNRESOLVED_MARK}" for it instead \
of a category.
"""
TEXT_BLOCK = '<text id="{number}">\n{text}\n</text>'


@dataclass(frozen=True)
class Reply:
    text: str  # the reply's text blocks (any thinking left out)
    tokens: ToolTokens
    stop_reason: str | None  # "max_tokens": cut off at MAX_OUTPUT_TOKENS


# (system prompt, user prompt) -> the model's reply.
ModelCall = Callable[[str, str], Reply]


def anthropic_model(base_url: str, api_key: str, model: str) -> ModelCall:
    """A call to an Anthropic-compatible endpoint, authenticated as the CLI is
    (`ANTHROPIC_AUTH_TOKEN`: a bearer token). Temperature 0, so a batch's labels repeat; thinking
    off, so an endpoint that thinks by default spends the output on the labels' JSON."""
    client = anthropic.Anthropic(
        base_url=base_url,
        auth_token=api_key,
        max_retries=MODEL_RETRIES,
        timeout=MODEL_TIMEOUT_S,
    )

    def call(system: str, user: str) -> Reply:
        messages: list[MessageParam] = [{"role": "user", "content": user}]
        message = client.messages.create(
            model=model,
            max_tokens=MAX_OUTPUT_TOKENS,
            # Not a parameter of this SDK's create(); the endpoint takes it in the body.
            extra_body={"temperature": 0},
            thinking={"type": "disabled"},
            system=system,
            messages=messages,
        )
        usage = message.usage
        tokens = ToolTokens(
            usage.input_tokens + (usage.cache_creation_input_tokens or 0),
            usage.cache_read_input_tokens or 0,
            usage.output_tokens,
        )
        text = "".join(block.text for block in message.content if block.type == "text")
        return Reply(text, tokens, message.stop_reason)

    return call


@dataclass(frozen=True)
class Setup:
    model: ModelCall
    ledger: Ledger
    secrets: list[str]  # masked out of every error the tool returns


def run_ledger(env: Mapping[str, str]) -> tuple[Ledger, RunState] | None:
    """The run's spend ledger and what the run last wrote to it; None for a server not started
    by a run, or before the run has written its state."""
    directory = env.get(LEDGER_ENV)
    ledger = Ledger(Path(directory)) if directory else None
    state = ledger.run_state() if ledger else None
    return (ledger, state) if ledger and state else None


def setup_from_env(env: Mapping[str, str]) -> Setup:
    """The run's model and spend ledger, from the variables the run gives the server. A server not
    started by a run (or a run whose model has no price, so no budget the tool could keep to)
    has no ledger, and the tool refuses to call a model."""
    found = run_ledger(env)
    if found is None:
        raise ToolError(
            f"{TOOL} is unavailable: this tool server has no spend ledger from a data-agent run "
            "(the run's model needs a price in the price table), so its model calls could not "
            "count toward the run's budget"
        )
    try:
        settings = ModelSettings.from_env(env)
    except ConfigError as exc:
        raise ToolError(f"{TOOL} is unavailable: {exc} for the tool server") from exc
    model = anthropic_model(settings.base_url, settings.api_key, settings.model)
    return Setup(model, found[0], known_secrets(env))


def description(source: str, stored: str, resumed: str, env: Mapping[str, str]) -> str:
    """DESCRIPTION for a server (`resumed`: what `resume_from` names there); with the run's
    budget when the run's ledger is there (without one the tool refuses to call a model, and the
    description names no budget)."""
    found = run_ledger(env)
    note = BUDGET_NOTE.format(budget=found[1].budget_usd) if found else ""
    return DESCRIPTION.format(
        source=source,
        limit=ROW_LIMIT,
        stored=stored,
        resumed=resumed,
        mark=UNRESOLVED_MARK,
        budget=note,
    )


def check_categories(categories: Sequence[str]) -> list[str]:
    cleaned = [category.strip() for category in categories]
    if not cleaned or any(not category for category in cleaned):
        raise ToolError("categories must be a non-empty list of non-empty names")
    if len(set(cleaned)) != len(cleaned):
        raise ToolError("categories must not repeat")
    return cleaned


def text_columns(text_column: str | Sequence[str]) -> list[str]:
    """The text columns: one name, or a list of them."""
    columns = [text_column] if isinstance(text_column, str) else list(text_column)
    if not columns:
        raise ToolError("text_column must name at least one column")
    if len(set(columns)) != len(columns):
        raise ToolError("text_column must not repeat a column")
    return columns


@dataclass(frozen=True)
class Resume:
    """An earlier label table of the run to resume from: `name` as its reply gave it."""

    name: str
    table: QueryResult
    retry_unresolved: bool = False

    @property
    def retried(self) -> tuple[str, ...]:
        return (*RETRIED, UNRESOLVED) if self.retry_unresolved else RETRIED


def resume_of(name: str, table: QueryResult | None, retry_unresolved: bool) -> Resume | None:
    """The Resume for a call's `resume_from` (None when it is empty), `table` being what the
    server has under that name."""
    if not name:
        if retry_unresolved:
            raise ToolError("retry_unresolved needs resume_from (the label table to resume from)")
        return None
    if table is None:
        raise ToolError(
            f"there is no label table {name!r} from {TOOL} in this run: "
            "resume_from takes the label table a reply of this tool named"
        )
    return Resume(name, table, retry_unresolved)


def too_many(count: int) -> ToolError:
    return ToolError(
        f"the query returns {count} rows; {TOOL} classifies at most {ROW_LIMIT} rows per call. "
        "Nothing was classified: narrow the query (WHERE, or a key range per call)"
    )


def fetch_sql(run: RunQuery, sql: str, dialect: str, key_column: str) -> QueryResult:
    """Every row of `sql`, through `run` (the server's gateway path, whose row cap is MAX_ROWS):
    a count first, so too many rows fail before anything else; more than MAX_ROWS come in pages
    ordered by the key."""
    inner = sql.strip().rstrip(";")
    count = int(run(f"SELECT count(*) AS n FROM (\n{inner}\n) AS classify_source", 1).rows[0][0])
    if count > ROW_LIMIT:
        raise too_many(count)
    if count <= MAX_ROWS:
        return run(inner, MAX_ROWS)
    key = exp.to_identifier(key_column, quoted=True).sql(dialect)
    columns: list[str] = []
    rows: list[tuple[Any, ...]] = []
    for offset in range(0, count, MAX_ROWS):
        page = run(
            f"SELECT * FROM (\n{inner}\n) AS classify_source "
            f"ORDER BY {key} LIMIT {MAX_ROWS} OFFSET {offset}",
            MAX_ROWS,
        )
        columns = page.columns
        rows.extend(page.rows)
    return QueryResult(columns, rows)


def check_rows(result: QueryResult, key_column: str, texts: Sequence[str]) -> None:
    """Refuse, before any model call, rows the label table could not key."""
    if len(result.rows) > ROW_LIMIT:
        raise too_many(len(result.rows))
    if not result.rows and not result.columns:
        return  # DAB's rows are dicts: an empty result names no columns
    for name in (key_column, *texts):
        if name not in result.columns:
            raise ToolError(f"the query returns no column {name!r}; it returns {result.columns}")
    if key_column in texts:
        raise ToolError("key_column and text_column must be different columns")
    clash = [c for c in result.columns if c not in texts and c in ADDED_COLUMNS]
    if clash:
        raise ToolError(f"rename the query's column(s) {clash}: the label table adds them")
    key = result.columns.index(key_column)
    keys = Counter(row[key] for row in result.rows)
    repeated = {k: n for k, n in keys.items() if n > 1}
    if repeated:
        example = next(iter(repeated))
        raise ToolError(
            f"key_column {key_column!r} must identify each row: {len(repeated)} key(s) repeat "
            f"(e.g. {example!r}, {repeated[example]} rows). Nothing was classified"
        )


def user_prompt(categories: list[str], instructions: str, texts: list[str]) -> str:
    parts = [f"Categories: {json.dumps(categories, ensure_ascii=False)}"]
    if instructions.strip():
        parts.append(f"How to choose:\n{instructions.strip()}")
    blocks = (TEXT_BLOCK.format(number=n, text=text) for n, text in enumerate(texts, 1))
    parts.append("Texts:\n" + "\n".join(blocks))
    return "\n\n".join(parts)


def parse_labels(reply: str) -> dict[int, str]:
    """The reply's JSON object, text number -> label; ValueError when there is none."""
    start, end = reply.find("{"), reply.rfind("}")
    if start < 0 or end < start:
        raise ValueError("the reply has no JSON object")
    data = json.loads(reply[start : end + 1])
    if not isinstance(data, dict):
        raise ValueError("the reply is not a JSON object")
    return {
        int(number): label.strip()
        for number, label in data.items()
        if re.fullmatch(r"\s*\d+\s*", number) and isinstance(label, str)
    }


def batches(texts: list[tuple[int, str]]) -> list[list[tuple[int, str]]]:
    """(row index, text) pairs in batches of at most BATCH_ROWS rows and BATCH_CHARS characters."""
    out: list[list[tuple[int, str]]] = []
    chars = 0
    for item in texts:
        if not out or len(out[-1]) >= BATCH_ROWS or chars + len(item[1]) > BATCH_CHARS:
            out.append([])
            chars = 0
        out[-1].append(item)
        chars += len(item[1])
    return out


@dataclass(frozen=True)
class Outcome:
    label: str | None  # a category, or None
    outcome: str
    model_label: str | None  # what the model answered, as it answered it


@dataclass
class BatchResult:
    outcomes: dict[int, Outcome]
    tokens: ToolTokens | None = None  # None: the call failed before a reply
    error: str | None = None


@dataclass
class Classification:
    columns: list[str]  # the label table's: the query's without the text column, then ADDED
    rows: list[tuple[Any, ...]]  # one per row classified (budget-skipped rows are not in it)
    fetched: int
    categories: list[str]
    counts: dict[str, int]  # category -> rows labeled with it
    unlabeled: dict[str, int]  # outcome -> rows without a category
    outside: Counter[str]  # labels outside the categories, as answered
    errors: list[str]  # distinct failures, masked
    not_run: int  # rows left when the budget was reached
    budget_usd: float | None  # the budget, when it stopped the classification
    calls: int
    tokens: ToolTokens
    cost_usd: float
    run_spent_usd: float | None  # the run's spend after the last batch, every tool call's included
    run_budget_usd: float | None
    unresolved_allowed: bool = False
    resume: Resume | None = None
    sent: int = 0  # rows sent to the model
    kept: int = 0  # rows of the resumed table kept as they were
    not_retried: int = 0  # of those, rows to retry that the budget left out


def run_batch(
    setup: Setup,
    categories: list[str],
    instructions: str,
    batch: list[tuple[int, str]],
    allow_unresolved: bool = False,
) -> BatchResult:
    prompt = user_prompt(categories, instructions, [text for _, text in batch])
    system = SYSTEM_PROMPT + UNRESOLVED_RULE if allow_unresolved else SYSTEM_PROMPT
    try:
        reply = setup.model(system, prompt)
    except Exception as exc:
        error = mask(f"{type(exc).__name__}: {exc}", setup.secrets)
        failed = Outcome(None, FAILED, None)
        return BatchResult({index: failed for index, _ in batch}, error=error)
    setup.ledger.record(TOOL, reply.tokens)
    try:
        labels = parse_labels(reply.text)
    except ValueError as exc:
        failed = Outcome(None, FAILED, None)
        if reply.stop_reason == "max_tokens":
            error = (
                f"unreadable reply: the reply was cut off at the output limit ({MAX_OUTPUT_TOKENS} "
                "tokens) and has no readable JSON object"
            )
        else:
            error = f"unreadable reply: {exc}"
        return BatchResult({i: failed for i, _ in batch}, reply.tokens, error)
    allowed = set(categories)
    outcomes = {}
    for number, (index, _) in enumerate(batch, 1):
        answer = labels.get(number)
        if answer is None:
            outcomes[index] = Outcome(None, NO_LABEL, None)
        elif answer in allowed:
            outcomes[index] = Outcome(answer, LABELED, answer)
        elif allow_unresolved and answer == UNRESOLVED_MARK:
            outcomes[index] = Outcome(None, UNRESOLVED, answer)
        else:
            outcomes[index] = Outcome(None, OUTSIDE, mask(answer, setup.secrets))
    return BatchResult(outcomes, reply.tokens)


def over_budget(ledger: Ledger, reserved: float) -> tuple[bool, float | None]:
    """Whether the run's spend so far plus `reserved` (for the calls still running) has reached
    its budget; and the budget."""
    state = ledger.run_state()
    if state is None:
        return True, None
    return ledger.spent_usd(state) + reserved >= state.budget_usd, state.budget_usd


def row_text(row: tuple[Any, ...], at: list[tuple[int, str]]) -> str:
    """A row's text: one column's value as it is; several, each non-empty one by its name."""
    if len(at) == 1:
        value = row[at[0][0]]
        return "" if value is None else str(value)
    parts = [(name, "" if row[i] is None else str(row[i])) for i, name in at]
    return "\n".join(f"{name}: {value}" for name, value in parts if value.strip())


def resumed_rows(
    resume: Resume, columns: list[str] | None, key_column: str
) -> dict[Any, tuple[Any, ...]]:
    """The resumed table's rows by key, refused unless it is a label table with the columns this
    call's would have (`columns`; None for a result that names no columns)."""
    table = resume.table
    if tuple(table.columns[-len(ADDED_COLUMNS) :]) != ADDED_COLUMNS:
        raise ToolError(
            f"{resume.name} is not a label table of {TOOL}: its columns are {table.columns}"
        )
    if columns is not None and table.columns != columns:
        raise ToolError(
            f"cannot resume from {resume.name}: its columns are {table.columns}, and this "
            f"query's label table has {columns}. Resume with the query and text_column that "
            "made it"
        )
    if key_column not in table.columns:
        raise ToolError(f"cannot resume from {resume.name}: it has no column {key_column!r}")
    key = table.columns.index(key_column)
    return {row[key]: row for row in table.rows}


def classify(
    result: QueryResult,
    key_column: str,
    text_column: str | Sequence[str],
    categories: Sequence[str],
    instructions: str,
    setup: Setup,
    allow_unresolved: bool = False,
    resume: Resume | None = None,
) -> Classification:
    """Label every row of `result`; rows checked by `check_rows`. Up to MAX_CONCURRENT_BATCHES
    batches run at once. A batch starts only while the run's spend (its own and every tool
    call's), with the costliest call so far reserved for each batch still running, is under the
    budget; until one call's cost is known, one batch runs at a time. Once the budget is reached
    with nothing running, no more start. So a classification passes the budget by the calls in
    flight when it is reached: one call when every batch costs about the same, more when a batch
    costs more than every one before it (a run passes it the same way by its last main call,
    `agent.Spend`).

    With `resume`, a row the resumed table has with an outcome outside `resume.retried` is kept
    as it is there and not sent; the label table is the resumed one with the new outcomes in
    place (a retried row the budget left out keeps its old outcome)."""
    allowed = check_categories(categories)
    if allow_unresolved and UNRESOLVED_MARK in allowed:
        raise ToolError(f"{UNRESOLVED_MARK!r} is the unresolved answer, not a category")
    texts_named = text_columns(text_column)
    check_rows(result, key_column, texts_named)
    at = [(result.columns.index(name), name) for name in texts_named] if result.columns else []
    text_at = {i for i, _ in at}
    keep = [i for i in range(len(result.columns)) if i not in text_at]
    columns = [*(result.columns[i] for i in keep), *ADDED_COLUMNS]
    old: dict[Any, tuple[Any, ...]] = {}
    retried: tuple[str, ...] = ()
    resumed = resume.name if resume else ""
    if resume is not None:
        old = resumed_rows(resume, columns if result.columns else None, key_column)
        retried = resume.retried
        if not result.columns:
            columns = resume.table.columns
    key_at = result.columns.index(key_column) if result.columns else -1
    texts: list[tuple[int, str]] = []
    outcomes: dict[int, Outcome] = {}
    kept: dict[int, tuple[Any, ...]] = {}
    for index, row in enumerate(result.rows):
        before = old.get(row[key_at])
        if before is not None and before[-2] not in retried:  # [-2]: its OUTCOME
            if before[-3] is not None and before[-3] not in allowed:  # [-3]: its LABEL
                raise ToolError(
                    f"cannot resume from {resumed}: its label {before[-3]!r} is not one of "
                    "the categories. Resume with the categories that made it"
                )
            kept[index] = before
            continue
        text = row_text(row, at)
        if text.strip():
            texts.append((index, text))
        else:
            outcomes[index] = Outcome(None, EMPTY, None)
    todo = batches(texts)
    calls = 0
    tokens = [0, 0, 0]
    errors: list[str] = []
    budget: float | None = None
    stopped = False
    state = setup.ledger.run_state()
    per_call: float | None = None  # the costliest call so far
    with ThreadPoolExecutor(MAX_CONCURRENT_BATCHES) as pool:
        running: set[Future[BatchResult]] = set()
        while True:
            while (
                todo
                and len(running) < MAX_CONCURRENT_BATCHES
                and (per_call is not None or not running)
            ):
                in_flight = sum(not future.done() for future in running)
                over, budget = over_budget(setup.ledger, in_flight * (per_call or 0.0))
                if over:
                    stopped = not running  # with calls running, wait for their real cost
                    break
                batch = todo.pop(0)
                running.add(
                    pool.submit(run_batch, setup, allowed, instructions, batch, allow_unresolved)
                )
            if not running:
                break
            done, running = wait(running, return_when=FIRST_COMPLETED)
            for future in done:
                batch = future.result()
                outcomes |= batch.outcomes
                calls += 1
                if batch.tokens is not None:
                    tokens = [t + n for t, n in zip(tokens, batch.tokens, strict=True)]
                    if state is not None:
                        per_call = max(per_call or 0.0, state.priced_usd(batch.tokens))
                if batch.error and batch.error not in errors:
                    errors.append(batch.error)
    left = [index for batch in todo for index, _ in batch]
    # Of the rows the budget left out, those the resumed table has keep their outcome there.
    not_retried = sum(result.rows[index][key_at] in old for index in left)
    rows: list[tuple[Any, ...]] = []
    fetched_keys: set[Any] = set()
    for index, row in enumerate(result.rows):
        fetched_keys.add(row[key_at])
        if (o := outcomes.get(index)) is not None:
            rows.append((*(row[i] for i in keep), o.label, o.outcome, o.model_label))
        elif index in kept:
            rows.append(kept[index])
        elif (before := old.get(row[key_at])) is not None:
            rows.append(before)  # a retried row the budget left out: as it was
    # The resumed table's rows this query no longer returns stay too.
    rows += [row for key, row in old.items() if key not in fetched_keys]
    counts = dict.fromkeys(allowed, 0)
    unlabeled: dict[str, int] = {}
    outside: Counter[str] = Counter()
    for *_, label, outcome, model_label in rows:
        if label is not None:
            counts[label] = counts.get(label, 0) + 1
        else:
            unlabeled[outcome] = unlabeled.get(outcome, 0) + 1
            if outcome == OUTSIDE and model_label is not None:
                outside[model_label] += 1
    total = ToolTokens(*tokens)
    after = setup.ledger.run_state()
    return Classification(
        columns=columns,
        rows=rows,
        fetched=len(result.rows),
        categories=allowed,
        counts=counts,
        unlabeled=unlabeled,
        outside=outside,
        errors=errors,
        not_run=len(left) - not_retried,
        budget_usd=budget if stopped else None,  # not_run or not_retried rows left
        calls=calls,
        tokens=total,
        cost_usd=state.priced_usd(total) if state else 0.0,
        run_spent_usd=setup.ledger.spent_usd(after) if after else None,
        run_budget_usd=after.budget_usd if after else None,
        unresolved_allowed=allow_unresolved,
        resume=resume,
        sent=len(texts) - len(left),
        kept=len(rows) - len(outcomes),
        not_retried=not_retried,
    )


def report(c: Classification, stored: str) -> str:
    """The tool's reply: counts and where the labels are, every unlabeled row accounted for."""
    labeled = sum(c.counts.values())
    lines = [f"{c.fetched} rows fetched; {labeled} labeled with a category."]
    if c.resume is not None:
        retried = c.resume.retried
        named = f"{', '.join(retried[:-1])} or {retried[-1]}"
        lines.append(
            f"Resumed from {c.resume.name}: {c.sent} rows sent to the model ({named} there, or "
            f"not in it); {c.kept} rows kept as they were."
        )
        if c.not_retried:
            lines.append(
                f"The run's budget of ${c.budget_usd} was reached before {c.not_retried} of the "
                f"rows to retry were sent: they keep their outcome from {c.resume.name}."
            )
    lines.append("Rows per category:")
    lines += [f"  {category}: {c.counts[category]}" for category in c.categories]
    notes = UNLABELED_NOTES
    if c.unresolved_allowed or c.unlabeled.get(UNRESOLVED):
        notes = {UNRESOLVED: UNRESOLVED_NOTE, **UNLABELED_NOTES}
    if c.unlabeled or c.not_run:
        lines.append("Rows without a category (counted in no category above):")
        for outcome, note in notes.items():
            if c.unlabeled.get(outcome):
                lines.append(f"  {outcome}: {c.unlabeled[outcome]} ({note})")
        if c.not_run:
            lines.append(
                f"  not classified: {c.not_run} (the run's budget of ${c.budget_usd} was reached; "
                "they are not in the label table)"
            )
    if c.outside:
        answered = ", ".join(f"{label!r} x{n}" for label, n in c.outside.most_common(10))
        lines.append(f"Labels outside the categories: {answered}")
    if c.errors:
        lines.append("Errors: " + "; ".join(c.errors[:3]))
    lines.append(
        f"Label table: {stored}. Columns: {', '.join(c.columns)}; `{LABEL}` is a category or null, "
        f"`{OUTCOME}` is one of {LABELED}, {', '.join(notes)}."
    )
    t = c.tokens
    lines.append(
        f"Model calls: {c.calls} ({t.uncached} input, {t.cached} cached input, {t.output} output "
        f"tokens; ${c.cost_usd:.4f}, counted in the run's spend)."
    )
    if c.run_spent_usd is not None:
        lines.append(
            f"Run spend so far: ${c.run_spent_usd:.4f} of the run's ${c.run_budget_usd} budget "
            "(US dollars at this run's model prices, this tool's calls included). The run ends "
            "with no answer once its spend passes the budget."
        )
    return "\n".join(lines)
