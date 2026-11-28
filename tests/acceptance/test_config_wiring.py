"""The config decides the run (.scratch/product-config/spec.md "分节", "产品默认与入口";
decisions.md Q2, Q8): `[limits]`, `[sandbox]`, `[policy]`, `[server]`, `[trace]`; the product's
default layers; the web form; the effort and time limit of the web's and IM's runs.

Written by ticket 141. The tests marked TICKET_144 are xfail(strict) until ticket 144 makes them
pass and removes the marker, changing nothing else. The one marked TICKET_143 (a run without any
config file has every layer on) passes with ticket 143's loader, which removes that marker. The
tests named `test_guard_...` pass now and must keep passing: a conversation turn and an IM turn
run with every layer on today.

Seams: `data-agent run` in-process, sessions stopped at the SDK's client
(tests/acceptance/product_config_support.py); the web app built by `create_app` with a
`start_run` that records each run's arguments and starts tests/fixtures/chat/fake_chat_run.py
(as tests/acceptance/test_mcp_web.py does); `data-agent web` in-process with `uvicorn.run` stood
in for. A web run's recorded arguments are then run in-process (`data-agent run <args>`) to the
stopped session, which shows the layers (`version.layers`), limits and options that run gets:
the comparison is never with a `--config` value, which changes with ticket 143.

The contract fixed here (ticket 141 Context contract items 4 and 5; for the human review):

4. Each web run (the run form's, a conversation turn's, so an IM turn's) gets the web server's
   effective config: the server writes it into the run's directory and the run's arguments name
   that file with `--config` (an explicit file, so no user-level file is merged in again). How
   is not asserted: only what the run, run as its arguments say, is given. `create_app(runs_dir,
   data_sources, start_run, ui_dir=UI_DIR, config=None)` keeps `runs_dir`, `data_sources` and
   `start_run`; `config` is the server's `EffectiveConfig` (None: the code defaults). Today's
   `create_app(runs_dir, sources, start_run)` call stays valid.
5. `GET /api/options` has no `configs`; `POST /api/runs` takes no `config` (the form picks no
   ablation) and succeeds without it, the run getting the server config's layers (all on by
   default). What happens to a body that still sends `config` is not pinned (decisions.md
   "调研后定": refuse, never quietly change, Q8); no test here sends one.
6. `[limits]`: `max_turns.dbt`/`max_turns.query` are the options' `max_turns`; `max_budget_usd` is
   the spend cap (the options' `max_budget_usd` scales with it); `effort` is the options' `effort`
   and `CLAUDE_CODE_EFFORT_LEVEL`; `agent_timeout_sec` is the run's deadline, as
   `--agent-timeout-sec` is: one under the deadline margin stops the run before its session, with
   the `result` event's `terminal_reason` `deadline`. Command-line values win over the config's.
7. `[sandbox]`: `backend` is `--sandbox`'s default and `network` the docker sandbox's network,
   both seen in the data server's arguments (`--sandbox`, `--network`). `image`, `cpus`,
   `memory` and `exec_max_seconds` reach the sandbox inside the MCP server process, which the
   stopped session never starts: ticket 144's unit tests cover them. So do `[sources.<name>]`'s
   `query_timeout_seconds`, `max_rows`, `duckdb.memory_limit`, `duckdb.max_temp_directory_size`
   and `dbt_timeout_seconds`, and `schemas.include`/`exclude`: they act in the MCP server.
8. `[policy]` against the command line: refused before any session, exit 2, stderr naming the
   policy key (`policy.dbt_target` for `--dbt-target task`, `policy.network` for `--network`);
   a `[roles]` model in `policy.disabled_models` is refused naming `policy.disabled_models`.
9. `[server]`: `data-agent web` without `--runs-dir`/`--port` uses `runs_dir`/`port`; the options
   win when given; `host` defaults to `127.0.0.1` (what `uvicorn.run` is given).
10. `[trace]`: `tier` is `--trace-tier`'s default; `base_url` is Langfuse's address in place of
   `DATA_AGENT_LANGFUSE_BASE_URL`, the keys still from the environment. Seen in the `RunExport`
   a run builds (its `settings.base_url` and `tier`), its `start` stood in for so nothing is
   sent.
"""

from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from product_config_support import (
    FULL_LAYERS,
    Capture,
    Web,
    assert_refused_before_session,
    assert_session,
    dbt_args,
    fake_home,
    flag_value,
    im_turn,
    key_of,
    layers_of,
    no_docker,
    query_args,
    read_events,
    run_cli,
    served,
    server_args,
    start_turn,
    stop_sessions,
    submit_form,
    toml_path,
    web_app,
    write_config,
    write_file,
)

TICKET_144 = pytest.mark.xfail(
    strict=True,
    raises=AssertionError,
    reason="ticket 144: the config is not wired to the run yet",
)

BASELINE_LAYERS = {"skill": False, "verifiers": False, "context": False}
LIMITS = (
    '[limits]\nmax_budget_usd = 0.5\neffort = "max"\n\n[limits.max_turns]\ndbt = 7\nquery = 5\n'
)


@pytest.fixture
def home(tmp_path: Path) -> Path:
    return fake_home(tmp_path)


@pytest.fixture
def capture(home: Path, monkeypatch: pytest.MonkeyPatch) -> Capture:
    no_docker(monkeypatch)
    return stop_sessions(monkeypatch, home)


def own_events_dir(args: list[str], events_dir: Path) -> list[str]:
    """A web run's arguments with `--events-dir` swapped for `events_dir`: the fake run the web
    started writes its own event file in the run's directory."""
    out = list(args)
    out[out.index("--events-dir") + 1] = str(events_dir)
    return out


def replayed(
    capsys: pytest.CaptureFixture[str], args: list[str], capture: Capture, events_dir: Path
) -> Path:
    """A web run's arguments run in-process to the stopped session; its events dir."""
    flag_value(args, "--events-dir")
    run = run_cli(capsys, ["run", *own_events_dir(args, events_dir)])
    assert_session(run, capture)
    return events_dir


def result_event(events_dir: Path) -> dict[str, Any]:
    results = [event for event in read_events(events_dir) if event.get("type") == "result"]
    assert results, "no result event"
    return results[-1]


# --- [limits] -------------------------------------------------------------------------------


@TICKET_144
def test_limits_set_the_turn_caps_spend_cap_and_effort(
    tmp_path: Path, capture: Capture, capsys: pytest.CaptureFixture[str]
) -> None:
    plain, _ = query_args(tmp_path / "plain")
    default_cap = assert_session(run_cli(capsys, plain), capture).options.max_budget_usd
    capture.sessions.clear()
    config = write_file(tmp_path / "limits.toml", LIMITS)

    query, _ = query_args(tmp_path / "query", "--config", str(config))
    options = assert_session(run_cli(capsys, query), capture).options
    capture.sessions.clear()
    dbt, _ = dbt_args(tmp_path / "dbt", "--config", str(config))
    dbt_options = assert_session(run_cli(capsys, dbt), capture).options

    assert options.max_turns == 5
    assert dbt_options.max_turns == 7
    assert default_cap is not None and options.max_budget_usd is not None
    assert options.max_budget_usd == pytest.approx(default_cap * 0.5)
    assert options.effort == "max"
    assert key_of(options.env, "CLAUDE_CODE_EFFORT_LEVEL") == "max"


@TICKET_144
def test_the_command_line_wins_over_the_limits(
    tmp_path: Path, capture: Capture, capsys: pytest.CaptureFixture[str]
) -> None:
    config = write_file(tmp_path / "limits.toml", LIMITS)
    argv, _ = query_args(tmp_path / "run", "--config", str(config), "--effort", "low")
    options = assert_session(run_cli(capsys, argv), capture).options

    assert options.effort == "low"
    assert options.max_turns == 5


@TICKET_144
def test_the_agent_timeout_is_the_runs_deadline(
    tmp_path: Path, capture: Capture, capsys: pytest.CaptureFixture[str]
) -> None:
    # Under the deadline margin (`agent.DEADLINE_MARGIN_S`): the run stops itself at once.
    config = write_file(tmp_path / "timeout.toml", "[limits]\nagent_timeout_sec = 1\n")
    argv, events_dir = query_args(tmp_path / "run", "--config", str(config))
    run_cli(capsys, argv)

    assert key_of(result_event(events_dir), "terminal_reason") == "deadline"
    assert capture.sessions == []


# --- [sandbox] ------------------------------------------------------------------------------


@TICKET_144
def test_sandbox_backend_and_network_reach_the_data_server(
    tmp_path: Path, capture: Capture, capsys: pytest.CaptureFixture[str]
) -> None:
    config = write_file(tmp_path / "sandbox.toml", '[sandbox]\nbackend = "local"\n')
    argv, _ = query_args(tmp_path / "local", "--config", str(config))
    argv.remove("--sandbox")
    argv.remove("local")
    local = server_args(assert_session(run_cli(capsys, argv), capture).options)
    capture.sessions.clear()
    config = write_file(tmp_path / "net.toml", '[sandbox]\nbackend = "docker"\nnetwork = true\n')
    argv, _ = query_args(tmp_path / "docker", "--config", str(config))
    argv[argv.index("--sandbox") + 1] = "docker"
    docker = server_args(assert_session(run_cli(capsys, argv), capture).options)

    assert flag_value(local, "--sandbox") == "local"
    assert flag_value(docker, "--sandbox") == "docker"
    assert "--network" in docker


# --- [policy] -------------------------------------------------------------------------------


@TICKET_144
def test_policy_refuses_a_dbt_target_against_it(
    home: Path, tmp_path: Path, capture: Capture, capsys: pytest.CaptureFixture[str]
) -> None:
    write_config(home, '[policy]\ndbt_target = "scratch"\n')
    argv, _ = dbt_args(tmp_path / "run", "--dbt-target", "task")
    assert_refused_before_session(run_cli(capsys, argv), capture, "policy.dbt_target")


@TICKET_144
def test_policy_refuses_the_network_against_it(
    home: Path, tmp_path: Path, capture: Capture, capsys: pytest.CaptureFixture[str]
) -> None:
    write_config(home, "[policy]\nnetwork = false\n")
    argv, _ = query_args(tmp_path / "run", "--network")
    assert_refused_before_session(run_cli(capsys, argv), capture, "policy.network")


@TICKET_144
def test_policy_refuses_a_disabled_model(
    home: Path, tmp_path: Path, capture: Capture, capsys: pytest.CaptureFixture[str]
) -> None:
    write_config(
        home,
        '[policy]\ndisabled_models = ["deepseek-v4-pro"]\n\n[roles]\nmain = "deepseek-v4-pro"\n',
    )
    argv, _ = query_args(tmp_path / "run")
    assert_refused_before_session(
        run_cli(capsys, argv), capture, "policy.disabled_models", "deepseek-v4-pro"
    )


# --- The product's default layers -----------------------------------------------------------


# Ticket 143's: it makes `--config` optional (test_config_cli.py) with every layer on by default
# (test_config_load.py), so this passes with it; a `TICKET_144` mark would XPASS there.
def test_a_run_without_any_config_has_every_layer_on(
    tmp_path: Path, capture: Capture, capsys: pytest.CaptureFixture[str]
) -> None:
    argv, events_dir = query_args(tmp_path / "run")
    assert_session(run_cli(capsys, argv), capture)
    assert layers_of(events_dir) == FULL_LAYERS


@TICKET_144
def test_the_run_form_offers_no_ablation_and_runs_with_every_layer_on(
    tmp_path: Path, capture: Capture, capsys: pytest.CaptureFixture[str]
) -> None:
    from fastapi.testclient import TestClient

    with web_app(tmp_path / "web") as web:
        with TestClient(web.app) as client:
            options = client.get("/api/options").json()
        args = submit_form(web)
        events_dir = replayed(capsys, args, capture, tmp_path / "replayed")

    assert "configs" not in options
    assert layers_of(events_dir) == FULL_LAYERS


@TICKET_144
def test_a_web_run_gets_the_servers_config(
    home: Path,
    tmp_path: Path,
    capture: Capture,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from seam_support import build_shop_duckdb

    db = build_shop_duckdb(tmp_path / "shop.duckdb")
    runs_dir = tmp_path / "runs"
    runs_dir.mkdir()
    write_config(
        home,
        f'[sources.shop]\nkind = "duckdb"\npath = {toml_path(db)}\n\n'
        "[layers]\nskill = false\nverifiers = false\ncontext = false\n\n"
        '[limits]\neffort = "max"\n',
    )
    with served(tmp_path / "web", monkeypatch, ["--runs-dir", str(runs_dir)]) as server:
        assert server.web is not None, f"data-agent web exited {server.code!r}"
        args = submit_form(server.web)
        events_dir = replayed(capsys, args, capture, tmp_path / "replayed")
        options = capture.only().options

    assert layers_of(events_dir) == BASELINE_LAYERS
    assert options.effort == "max"


@TICKET_144
@pytest.mark.parametrize("route", ["form", "turn", "im"])
def test_web_and_im_runs_take_the_effort_and_time_limit_from_the_config(
    route: str,
    home: Path,
    tmp_path: Path,
    capture: Capture,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from seam_support import build_shop_duckdb

    db = build_shop_duckdb(tmp_path / "shop.duckdb")
    runs_dir = tmp_path / "runs"
    runs_dir.mkdir()
    write_config(
        home,
        f'[sources.shop]\nkind = "duckdb"\npath = {toml_path(db)}\n\n'
        '[limits]\neffort = "max"\nagent_timeout_sec = 1\n',
    )
    routes: dict[str, Callable[[Web], list[str]]] = {
        "form": submit_form,
        "turn": start_turn,
        "im": im_turn,
    }
    with served(tmp_path / "web", monkeypatch, ["--runs-dir", str(runs_dir)]) as server:
        assert server.web is not None, f"data-agent web exited {server.code!r}"
        args = routes[route](server.web)
        flag_value(args, "--events-dir")
        events_dir = tmp_path / "replayed"
        run = run_cli(capsys, ["run", *own_events_dir(args, events_dir)])

    # A time limit under the deadline margin: the run stops before its session, as the config
    # says; the `version` event records the effort the run was given.
    assert key_of(result_event(events_dir), "terminal_reason") == "deadline", run.stderr[-2000:]
    assert key_of(read_events(events_dir)[0], "effort") == "max"


# --- Guards: a conversation turn and an IM turn run with every layer on today ----------------


@pytest.mark.parametrize("route", ["turn", "im"])
def test_guard_conversation_and_im_turns_have_every_layer_on(
    route: str, tmp_path: Path, capture: Capture, capsys: pytest.CaptureFixture[str]
) -> None:
    turn = start_turn if route == "turn" else im_turn
    with web_app(tmp_path / "web") as web:
        args = turn(web)
        events_dir = replayed(capsys, args, capture, tmp_path / "replayed")

    assert layers_of(events_dir) == FULL_LAYERS


# --- [server] and [trace] -------------------------------------------------------------------


@TICKET_144
def test_web_takes_its_runs_dir_and_port_from_the_server_section_unless_given(
    home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capture: Capture
) -> None:
    from_config = tmp_path / "runs-from-config"
    from_config.mkdir()
    given_dir = tmp_path / "runs-given"
    given_dir.mkdir()
    write_config(home, f"[server]\nruns_dir = {toml_path(from_config)}\nport = 8790\n")
    with served(tmp_path / "web-config", monkeypatch, []) as server:
        assert server.created is not None and server.uvicorn is not None, server.code
        created, given = server.created, server.uvicorn
    argv = ["--runs-dir", str(given_dir), "--port", "8791"]
    with served(tmp_path / "web-given", monkeypatch, argv) as server:
        assert server.created is not None and server.uvicorn is not None, server.code
        created_given, given_given = server.created, server.uvicorn

    assert Path(key_of(created, "runs_dir")).resolve() == from_config.resolve()
    assert key_of(given, "port") == 8790
    assert key_of(given, "host") == "127.0.0.1"
    assert Path(key_of(created_given, "runs_dir")).resolve() == given_dir.resolve()
    assert key_of(given_given, "port") == 8791


@TICKET_144
def test_trace_sets_the_tier_and_the_langfuse_address(
    home: Path,
    tmp_path: Path,
    capture: Capture,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from data_agent import tracing

    exports: list[tuple[str, str]] = []

    def start(self: Any, facts: Any) -> None:
        exports.append((self.settings.base_url, self.tier))

    monkeypatch.setattr(tracing.RunExport, "start", start)
    monkeypatch.setenv("DATA_AGENT_LANGFUSE_PUBLIC_KEY", "pk-" + "lf" + "-fake")
    monkeypatch.setenv("DATA_AGENT_LANGFUSE_SECRET_KEY", "sk-" + "lf" + "-fake")
    write_config(home, '[trace]\nbase_url = "https://langfuse.example"\ntier = "structure"\n')
    argv, _ = query_args(tmp_path / "run")
    run_cli(capsys, argv)

    assert exports == [("https://langfuse.example", "structure")]
