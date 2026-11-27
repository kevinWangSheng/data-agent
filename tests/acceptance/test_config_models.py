"""Models and roles from the config (.scratch/product-config/spec.md "分节" `[models.<名字>]`,
`[roles]`; decisions.md Q2, Q8).

Written by ticket 141. Every test here is xfail(strict) until ticket 145 makes it pass and removes
the marker, changing nothing else. The seam: `data-agent run --config FILE` in-process, the
session stopped at the SDK's client (tests/acceptance/product_config_support.py).

The contract fixed here (for the human review):

1. `[roles].main` names a `[models.<name>]`: the options' `model` is `<name>`; the CLI's
   `ANTHROPIC_BASE_URL` and `ANTHROPIC_AUTH_TOKEN` (`agent.ModelSettings.sdk_env`) are its
   `base_url` and its `api_key`, the `${VAR}` expanded. The expanded key is in the options' `env`
   only, never in the event file.
2. `context_window` and `compact_window` are `CLAUDE_CODE_MAX_CONTEXT_TOKENS` and
   `CLAUDE_CODE_AUTO_COMPACT_WINDOW`; left out, 1000000 and 400000 (today's values).
3. `[roles].small` is `ANTHROPIC_SMALL_FAST_MODEL` and `ANTHROPIC_DEFAULT_HAIKU_MODEL`;
   `[roles].verifier` is the verifier subagents' model: `CLAUDE_CODE_SUBAGENT_MODEL`, or the
   model of every verifier agent in the options' `agents` (ticket 145 picks which; either
   passes). Left out, both are `main`.
4. Prices (US dollars per million tokens, `input_price`, `cache_read_price`, `output_price`): a
   priced model gets the price-table cap, and the SDK's cap is the backstop scaled as
   `agent.sdk_budget_usd` scales it today, by the largest ratio of the CLI's fallback rates
   ($6.25 uncached input, $0.50 cache read, $25 output) to the model's. An unpriced model:
   the options' `max_budget_usd` is None (no cap is passed to the SDK); the run warns at its
   start, on stderr (a line holding `warning`, the model's name and `price`) and as a `warning`
   event (`model`: the name) before the `result` event; and the `result` event's `cost_basis`
   is `unknown` with `cost_usd` None (contract item 7 of ticket 141).
5. `efforts`: given, a `[limits].effort` outside it is refused before any session (exit 2, stderr
   naming `limits.effort` and the model); a value inside it, even one today's `--effort` does not
   take (`medium`), is taken (the run starts). That it then is the options' `effort` needs
   ticket 144's `[limits]` wiring as well, which 145 is not blocked by: not asserted here (a
   known gap, for the ticket after both). Left out, nothing is checked.
"""

from pathlib import Path

import pytest
from product_config_support import (
    Capture,
    assert_refused_before_session,
    assert_session,
    attr_of,
    dbt_args,
    events_text,
    fake_home,
    key_of,
    query_args,
    read_events,
    run_cli,
    stop_sessions,
    write_file,
)

TICKET_145 = pytest.mark.xfail(
    strict=True,
    raises=AssertionError,
    reason="ticket 145: the model settings do not come from the config yet",
)
pytestmark = TICKET_145

KEY_ENV = "DATA_AGENT_TEST_MODEL_KEY"
MODEL_KEY = "mk-" + "Nf27" + "-made-up"
BASE_URL = "https://llm.example/anthropic"


@pytest.fixture
def capture(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Capture:
    capture = stop_sessions(monkeypatch, fake_home(tmp_path))
    monkeypatch.setenv(KEY_ENV, MODEL_KEY)
    return capture


def model_table(name: str, extra: str = "") -> str:
    return f'[models.{name}]\nbase_url = "{BASE_URL}"\napi_key = "${{{KEY_ENV}}}"\n{extra}\n'


PRICED = "input_price = 2.0\ncache_read_price = 0.2\noutput_price = 8.0\n"


def test_the_main_role_picks_the_model_its_endpoint_and_key(
    tmp_path: Path, capture: Capture, capsys: pytest.CaptureFixture[str]
) -> None:
    config = write_file(
        tmp_path / "models.toml",
        model_table("acme-large", PRICED) + '[roles]\nmain = "acme-large"\n',
    )
    argv, events_dir = query_args(tmp_path / "run", "--config", str(config))
    options = assert_session(run_cli(capsys, argv), capture).options

    assert options.model == "acme-large"
    assert key_of(options.env, "ANTHROPIC_BASE_URL") == BASE_URL
    assert key_of(options.env, "ANTHROPIC_AUTH_TOKEN") == MODEL_KEY
    assert key_of(options.env, "ANTHROPIC_MODEL") == "acme-large"
    assert MODEL_KEY not in events_text(events_dir)


def test_the_context_and_compaction_windows_come_from_the_model(
    tmp_path: Path, capture: Capture, capsys: pytest.CaptureFixture[str]
) -> None:
    config = write_file(
        tmp_path / "models.toml",
        model_table("windowed", PRICED + "context_window = 128000\ncompact_window = 64000\n")
        + model_table("plain", PRICED)
        + '[roles]\nmain = "windowed"\n',
    )
    argv, _ = query_args(tmp_path / "windowed", "--config", str(config))
    windowed = assert_session(run_cli(capsys, argv), capture).options
    capture.sessions.clear()
    plain_config = write_file(
        tmp_path / "plain.toml", model_table("plain", PRICED) + '[roles]\nmain = "plain"\n'
    )
    argv, _ = query_args(tmp_path / "plain", "--config", str(plain_config))
    plain = assert_session(run_cli(capsys, argv), capture).options

    assert key_of(windowed.env, "CLAUDE_CODE_MAX_CONTEXT_TOKENS") == "128000"
    assert key_of(windowed.env, "CLAUDE_CODE_AUTO_COMPACT_WINDOW") == "64000"
    assert key_of(plain.env, "CLAUDE_CODE_MAX_CONTEXT_TOKENS") == "1000000"
    assert key_of(plain.env, "CLAUDE_CODE_AUTO_COMPACT_WINDOW") == "400000"


def verifier_model(options: object) -> set[str]:
    """What the verifier subagents run on: the subagent variable, and each agent's model."""
    env = attr_of(options, "env")
    agents = attr_of(options, "agents") or {}
    models = {str(attr_of(agent, "model")) for agent in agents.values()}
    return {env.get("CLAUDE_CODE_SUBAGENT_MODEL", ""), *models} - {"inherit", ""}


def test_the_small_and_verifier_roles_pick_their_models(
    tmp_path: Path, capture: Capture, capsys: pytest.CaptureFixture[str]
) -> None:
    roles = '[roles]\nmain = "acme-large"\nsmall = "acme-small"\nverifier = "acme-check"\n'
    config = write_file(
        tmp_path / "roles.toml",
        model_table("acme-large", PRICED)
        + model_table("acme-small", PRICED)
        + model_table("acme-check", PRICED)
        + roles,
    )
    argv, _ = dbt_args(tmp_path / "roles", "--config", str(config))
    options = assert_session(run_cli(capsys, argv), capture).options
    capture.sessions.clear()
    main_only = write_file(
        tmp_path / "main.toml", model_table("acme-large", PRICED) + '[roles]\nmain = "acme-large"\n'
    )
    argv, _ = dbt_args(tmp_path / "main", "--config", str(main_only))
    defaults = assert_session(run_cli(capsys, argv), capture).options

    assert key_of(options.env, "ANTHROPIC_SMALL_FAST_MODEL") == "acme-small"
    assert key_of(options.env, "ANTHROPIC_DEFAULT_HAIKU_MODEL") == "acme-small"
    assert "acme-check" in verifier_model(options)
    assert key_of(defaults.env, "ANTHROPIC_SMALL_FAST_MODEL") == "acme-large"
    assert verifier_model(defaults) <= {"acme-large"}


def test_a_priced_model_scales_the_sdk_cap_by_its_prices(
    tmp_path: Path, capture: Capture, capsys: pytest.CaptureFixture[str]
) -> None:
    config = write_file(
        tmp_path / "priced.toml",
        model_table("acme-large", PRICED) + '[roles]\nmain = "acme-large"\n',
    )
    argv, _ = query_args(tmp_path / "run", "--config", str(config))
    options = assert_session(run_cli(capsys, argv), capture).options

    # The default spend cap, $1.0 (`max_budget_usd` of src/data_agent/configs/*.toml, kept as the
    # code default), scaled by max(6.25 / 2.0, 0.5 / 0.2, 25 / 8.0) = 3.125. Not a `[limits]`
    # budget: reading that is ticket 144's, and 145 is not blocked by 144.
    assert options.max_budget_usd == pytest.approx(1.0 * 3.125)


def test_an_unpriced_model_has_no_cap_a_warning_and_an_unknown_cost(
    tmp_path: Path, capture: Capture, capsys: pytest.CaptureFixture[str]
) -> None:
    config = write_file(
        tmp_path / "unpriced.toml", model_table("acme-free") + '[roles]\nmain = "acme-free"\n'
    )
    argv, events_dir = query_args(tmp_path / "run", "--config", str(config))
    run = run_cli(capsys, argv)
    options = assert_session(run, capture).options
    events = read_events(events_dir)

    assert options.max_budget_usd is None
    warnings = [
        line
        for line in run.stderr.splitlines()
        if "warning" in line.lower() and "acme-free" in line and "price" in line.lower()
    ]
    assert warnings, f"no price warning on stderr: {run.stderr[-2000:]!r}"
    warned = [event for event in events if event.get("type") == "warning"]
    assert warned and key_of(warned[0], "model") == "acme-free"
    result = events[-1]
    assert result.get("type") == "result"
    assert key_of(result, "cost_basis") == "unknown"
    assert key_of(result, "cost_usd") is None


def test_the_models_efforts_bound_the_effort(
    tmp_path: Path, capture: Capture, capsys: pytest.CaptureFixture[str]
) -> None:
    table = model_table("acme-large", PRICED + 'efforts = ["low", "medium"]\n')
    outside = write_file(
        tmp_path / "outside.toml",
        table + '[roles]\nmain = "acme-large"\n\n[limits]\neffort = "high"\n',
    )
    argv, _ = query_args(tmp_path / "outside", "--config", str(outside))
    assert_refused_before_session(run_cli(capsys, argv), capture, "limits.effort", "acme-large")

    inside = write_file(
        tmp_path / "inside.toml",
        table + '[roles]\nmain = "acme-large"\n\n[limits]\neffort = "medium"\n',
    )
    argv, _ = query_args(tmp_path / "inside", "--config", str(inside))
    # Taken: the run starts. That the options then carry it is `[limits].effort`'s wiring, ticket
    # 144's (test_config_wiring.py); 145 is not blocked by 144.
    assert_session(run_cli(capsys, argv), capture)
