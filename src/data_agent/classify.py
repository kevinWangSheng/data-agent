"""classify_text (ticket 70): label each row's text with one of the given categories, in batches
sent to the run's own model. Both tool servers (`mcp_server`, `evals.dab_server`) serve it from
here; each fetches the rows through its read-only path and stores the label table its own way.

The model and its endpoint are the run's (`ModelSettings.tool_env`, set for the server's process);
every call's tokens go into the run's spend ledger (`tool_spend`), and no batch starts once the
run's spend has reached its budget. Labels are facts: a label outside the categories, a row the
reply left out, or a failed call is reported as such and gets no category.
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
from data_agent.tool_spend import LEDGER_ENV, Ledger, ToolTokens

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

Returns the number of rows per category; the rows that got no category, by reason (a label \
outside the categories, no label in the reply, a failed model call, an empty text, or the \
run's budget reached); and where the label table is: {stored} It has the query's columns \
except the text, then `label` (a category, or null), `outcome` and `model_label` (the model's \
answer as given). The model calls count toward this run's spend and budget.
"""

SYSTEM_PROMPT = """\
You sort texts into categories. You get a list of categories, possibly instructions on how to \
choose, and numbered texts. Give every text exactly one category from the list, written exactly \
as it is in the list. Reply with only a JSON object that maps each text's number to its category, \
for example {"1": "<category>", "2": "<category>"}, and nothing else.
"""
TEXT_BLOCK = '<text id="{number}">\n{text}\n</text>'


@dataclass(frozen=True)
class Reply:
    text: str
    tokens: ToolTokens


# (system prompt, user prompt) -> the model's reply.
ModelCall = Callable[[str, str], Reply]


def anthropic_model(base_url: str, api_key: str, model: str) -> ModelCall:
    """A call to an Anthropic-compatible endpoint, authenticated as the CLI is
    (`ANTHROPIC_AUTH_TOKEN`: a bearer token). Temperature 0, so a batch's labels repeat."""
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
        return Reply(text, tokens)

    return call


@dataclass(frozen=True)
class Setup:
    model: ModelCall
    ledger: Ledger
    secrets: list[str]  # masked out of every error the tool returns


def setup_from_env(env: Mapping[str, str]) -> Setup:
    """The run's model and spend ledger, from the variables the run gives the server. A server not
    started by a run (or a run whose model has no price, so no budget the tool could keep to)
    has no ledger, and the tool refuses to call a model."""
    directory = env.get(LEDGER_ENV)
    ledger = Ledger(Path(directory)) if directory else None
    if ledger is None or ledger.run_state() is None:
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
    return Setup(model, ledger, known_secrets(env))


def check_categories(categories: Sequence[str]) -> list[str]:
    cleaned = [category.strip() for category in categories]
    if not cleaned or any(not category for category in cleaned):
        raise ToolError("categories must be a non-empty list of non-empty names")
    if len(set(cleaned)) != len(cleaned):
        raise ToolError("categories must not repeat")
    return cleaned


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


def check_rows(result: QueryResult, key_column: str, text_column: str) -> None:
    """Refuse, before any model call, rows the label table could not key."""
    if len(result.rows) > ROW_LIMIT:
        raise too_many(len(result.rows))
    if not result.rows and not result.columns:
        return  # DAB's rows are dicts: an empty result names no columns
    for name in (key_column, text_column):
        if name not in result.columns:
            raise ToolError(f"the query returns no column {name!r}; it returns {result.columns}")
    if key_column == text_column:
        raise ToolError("key_column and text_column must be different columns")
    clash = [c for c in result.columns if c != text_column and c in ADDED_COLUMNS]
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


def run_batch(
    setup: Setup, categories: list[str], instructions: str, batch: list[tuple[int, str]]
) -> BatchResult:
    prompt = user_prompt(categories, instructions, [text for _, text in batch])
    try:
        reply = setup.model(SYSTEM_PROMPT, prompt)
    except Exception as exc:
        error = mask(f"{type(exc).__name__}: {exc}", setup.secrets)
        failed = Outcome(None, FAILED, None)
        return BatchResult({index: failed for index, _ in batch}, error=error)
    setup.ledger.record(TOOL, reply.tokens)
    try:
        labels = parse_labels(reply.text)
    except ValueError as exc:
        failed = Outcome(None, FAILED, None)
        return BatchResult({i: failed for i, _ in batch}, reply.tokens, f"unreadable reply: {exc}")
    allowed = set(categories)
    outcomes = {}
    for number, (index, _) in enumerate(batch, 1):
        answer = labels.get(number)
        if answer is None:
            outcomes[index] = Outcome(None, NO_LABEL, None)
        elif answer in allowed:
            outcomes[index] = Outcome(answer, LABELED, answer)
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


def classify(
    result: QueryResult,
    key_column: str,
    text_column: str,
    categories: Sequence[str],
    instructions: str,
    setup: Setup,
) -> Classification:
    """Label every row of `result`; rows checked by `check_rows`. Up to MAX_CONCURRENT_BATCHES
    batches run at once. A batch starts only while the run's spend (its own and every tool
    call's), with the costliest call so far reserved for each batch still running, is under the
    budget; until one call's cost is known, one batch runs at a time. Once the budget is reached
    with nothing running, no more start. So a classification passes the budget by the calls in
    flight when it is reached: one call when every batch costs about the same, more when a batch
    costs more than every one before it (a run passes it the same way by its last main call,
    `agent.Spend`)."""
    allowed = check_categories(categories)
    check_rows(result, key_column, text_column)
    text_at = result.columns.index(text_column) if result.columns else -1
    texts: list[tuple[int, str]] = []
    outcomes: dict[int, Outcome] = {}
    for index, row in enumerate(result.rows):
        value = row[text_at]
        text = "" if value is None else str(value)
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
                running.add(pool.submit(run_batch, setup, allowed, instructions, todo.pop(0)))
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
    keep = [i for i in range(len(result.columns)) if i != text_at]
    rows = [
        (*(row[i] for i in keep), o.label, o.outcome, o.model_label)
        for index, row in enumerate(result.rows)
        if (o := outcomes.get(index)) is not None
    ]
    counts = dict.fromkeys(allowed, 0)
    unlabeled: dict[str, int] = {}
    outside: Counter[str] = Counter()
    for o in outcomes.values():
        if o.label is not None:
            counts[o.label] += 1
        else:
            unlabeled[o.outcome] = unlabeled.get(o.outcome, 0) + 1
            if o.outcome == OUTSIDE and o.model_label is not None:
                outside[o.model_label] += 1
    total = ToolTokens(*tokens)
    return Classification(
        columns=[*(result.columns[i] for i in keep), *ADDED_COLUMNS],
        rows=rows,
        fetched=len(result.rows),
        categories=allowed,
        counts=counts,
        unlabeled=unlabeled,
        outside=outside,
        errors=errors,
        not_run=sum(len(batch) for batch in todo),
        budget_usd=budget if stopped else None,
        calls=calls,
        tokens=total,
        cost_usd=state.priced_usd(total) if state else 0.0,
    )


def report(c: Classification, stored: str) -> str:
    """The tool's reply: counts and where the labels are, every unlabeled row accounted for."""
    labeled = sum(c.counts.values())
    lines = [f"{c.fetched} rows fetched; {labeled} labeled with a category."]
    lines.append("Rows per category:")
    lines += [f"  {category}: {c.counts[category]}" for category in c.categories]
    if c.unlabeled or c.not_run:
        lines.append("Rows without a category (counted in no category above):")
        for outcome, note in UNLABELED_NOTES.items():
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
        f"`{OUTCOME}` is one of {LABELED}, {', '.join(UNLABELED_NOTES)}."
    )
    t = c.tokens
    lines.append(
        f"Model calls: {c.calls} ({t.uncached} input, {t.cached} cached input, {t.output} output "
        f"tokens; ${c.cost_usd:.4f}, counted in the run's spend)."
    )
    return "\n".join(lines)
