"""The run's models from `[models.*]` and `[roles]` (ticket 145; spec Q2, decisions.md Q8):
`agent.ModelSettings`, and the `--effort` / `[limits].effort` check of `data-agent run`. What the
acceptance tests (tests/acceptance/test_config_models.py) do not pin."""

from collections.abc import Mapping
from pathlib import Path

import pytest

from data_agent import agent, cli
from data_agent.agent import ConfigError, ModelSettings, Price
from data_agent.config import default_config, load_effective_config

KEY_ENV = "DATA_AGENT_TEST_MODEL_KEY"
KEY = "model-key-" + "Wd40"
PRICED = "input_price = 2.0\ncache_read_price = 0.2\noutput_price = 8.0\n"


def settings(tmp_path: Path, text: str, environ: Mapping[str, str] | None = None) -> ModelSettings:
    path = tmp_path / "config.toml"
    path.write_text(text)
    env = {KEY_ENV: KEY, **(environ or {})}
    return ModelSettings.from_config(load_effective_config([path], env, {}), env)


def model(name: str, extra: str = "", url: str = "https://llm.example/a") -> str:
    return f'[models.{name}]\nbase_url = "{url}"\napi_key = "${{{KEY_ENV}}}"\n{extra}\n'


def test_the_code_defaults_give_the_settings_runs_had_before() -> None:
    """No config file: what `from_env` gave before ticket 145, `sdk_env` key for key."""
    env = {"DATA_AGENT_API_KEY": "k"}
    defaults = ModelSettings.from_env(env)
    assert defaults == ModelSettings.from_config(default_config(), env)
    assert (defaults.base_url, defaults.api_key, defaults.model) == (
        agent.DEFAULT_BASE_URL,
        "k",
        agent.DEFAULT_MODEL,
    )
    sdk_env = defaults.sdk_env()
    slots = (
        "ANTHROPIC_MODEL",
        "ANTHROPIC_SMALL_FAST_MODEL",
        "ANTHROPIC_DEFAULT_HAIKU_MODEL",
        "ANTHROPIC_DEFAULT_SONNET_MODEL",
        "ANTHROPIC_DEFAULT_OPUS_MODEL",
        "CLAUDE_CODE_SUBAGENT_MODEL",
    )
    assert {sdk_env[slot] for slot in slots} == {agent.DEFAULT_MODEL}
    assert sdk_env["CLAUDE_CODE_MAX_CONTEXT_TOKENS"] == "1000000"
    assert sdk_env["CLAUDE_CODE_AUTO_COMPACT_WINDOW"] == "400000"
    assert defaults.price() == agent.PRICES[agent.DEFAULT_MODEL]
    assert defaults.efforts == agent.EFFORTS
    assert defaults.tool_env() == {
        "DATA_AGENT_BASE_URL": agent.DEFAULT_BASE_URL,
        "DATA_AGENT_API_KEY": "k",
        "DATA_AGENT_MODEL": agent.DEFAULT_MODEL,
    }


def test_data_agent_model_overrides_the_main_role(tmp_path: Path) -> None:
    text = model("acme-large", PRICED) + model("acme-other", PRICED, "https://llm.example/b")
    text += '[roles]\nmain = "acme-large"\n'
    overridden = settings(tmp_path, text, {"DATA_AGENT_MODEL": "acme-other"})
    assert overridden.model == "acme-other"
    assert overridden.base_url == "https://llm.example/b"
    assert overridden.sdk_env()["ANTHROPIC_MODEL"] == "acme-other"
    # With `DATA_AGENT_BASE_URL` too: the overriding model's endpoint.
    both = settings(
        tmp_path,
        text,
        {"DATA_AGENT_MODEL": "acme-other", "DATA_AGENT_BASE_URL": "https://llm.example/c"},
    )
    assert (both.model, both.base_url) == ("acme-other", "https://llm.example/c")


def test_a_model_without_a_key_takes_data_agent_api_key(tmp_path: Path) -> None:
    text = '[models.acme]\nbase_url = "https://llm.example/a"\n\n[roles]\nmain = "acme"\n'
    assert settings(tmp_path, text, {"DATA_AGENT_API_KEY": "env-key"}).api_key == "env-key"
    with pytest.raises(ConfigError, match=r"\[models\.acme\]\.api_key"):
        settings(tmp_path, text)


def test_a_model_with_only_some_prices_is_refused(tmp_path: Path) -> None:
    text = model("acme", "input_price = 1.0\n") + '[roles]\nmain = "acme"\n'
    with pytest.raises(ConfigError) as caught:
        settings(tmp_path, text)
    message = str(caught.value)
    assert "[models.acme]" in message
    assert "cache_read_price, output_price" in message


def test_the_spend_is_counted_at_each_rates_highest_price_among_the_roles(
    tmp_path: Path,
) -> None:
    text = (
        model("acme-large", "input_price = 2.0\ncache_read_price = 0.1\noutput_price = 8.0\n")
        + model("acme-small", "input_price = 1.0\ncache_read_price = 0.5\noutput_price = 4.0\n")
        + '[roles]\nmain = "acme-large"\nsmall = "acme-small"\n'
    )
    run = settings(tmp_path, text)
    assert run.price() == Price(input=2.0, cached_input=0.5, output=8.0)
    cap = agent.spend_cap(run, 1.0)
    assert cap is not None and cap.price == run.price()


def test_any_role_without_a_price_leaves_the_run_unpriced(tmp_path: Path) -> None:
    text = (
        model("acme-large", PRICED)
        + model("acme-check")
        + '[roles]\nmain = "acme-large"\nverifier = "acme-check"\n'
    )
    run = settings(tmp_path, text)
    assert run.unpriced() == ["acme-check"]
    assert run.price() is None
    assert agent.spend_cap(run, 1.0) is None
    # A role naming a model with no table at all: a model name, without a price.
    bare = settings(
        tmp_path,
        model("acme-large", PRICED) + '[roles]\nmain = "acme-large"\nsmall = "acme-tiny"\n',
    )
    assert bare.small == "acme-tiny"


def test_the_classify_role_has_its_own_endpoint_and_key(tmp_path: Path) -> None:
    text = (
        model("acme-large", PRICED)
        + model("acme-label", PRICED, "https://labels.example/v1")
        + '[roles]\nmain = "acme-large"\nclassify = "acme-label"\n'
    )
    run = settings(tmp_path, text)
    assert run.tool_env() == {
        "DATA_AGENT_BASE_URL": "https://labels.example/v1",
        "DATA_AGENT_API_KEY": KEY,
        "DATA_AGENT_MODEL": "acme-label",
    }
    # The CLI's slots stay the main model's.
    assert run.sdk_env()["ANTHROPIC_BASE_URL"] == "https://llm.example/a"
    assert run.sdk_env()["ANTHROPIC_SMALL_FAST_MODEL"] == "acme-large"


def test_a_free_rate_leaves_no_backstop() -> None:
    """No factor bounds the SDK's charge for tokens that are free at our price: the price-table
    cap (`Spend`) is the only one."""
    assert agent.backstop_usd(1.0, Price(input=0.0, cached_input=0.5, output=25.0)) is None
    assert agent.backstop_usd(1.0, Price(input=0.0, cached_input=0.0, output=0.0)) is None
    assert agent.backstop_usd(1.0, Price(input=6.25, cached_input=0.5, output=25.0)) == 1.0


def run_cli(capsys: pytest.CaptureFixture[str], argv: list[str]) -> tuple[int, str]:
    with pytest.raises(SystemExit) as caught:
        cli.main(argv)
    return int(caught.value.code or 0), capsys.readouterr().err


def test_an_effort_outside_the_models_efforts_names_the_model_and_the_value(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Refused before anything else is checked (no workspace, no database needed)."""
    monkeypatch.setenv(KEY_ENV, KEY)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("DATA_AGENT_API_KEY", KEY)
    for name in ("DATA_AGENT_MODEL", "DATA_AGENT_BASE_URL", "DATA_AGENT_CONFIG"):
        monkeypatch.delenv(name, raising=False)
    path = tmp_path / "config.toml"
    path.write_text(
        model("acme-large", PRICED + 'efforts = ["low", "medium"]\n')
        + '[roles]\nmain = "acme-large"\n'
    )
    run = ["run", "--type", "query", "--events-dir", str(tmp_path / "events"), "--task", "x"]
    base = [*run, "--config", str(path), "--workspace", str(tmp_path / "none")]
    code, err = run_cli(capsys, [*base, "--effort", "max"])
    assert code == 2
    assert "--effort" in err and "'max'" in err and "acme-large" in err
    assert "low, medium" in err
    # The default model takes today's values only.
    code, err = run_cli(capsys, [*run, "--workspace", str(tmp_path), "--effort", "medium"])
    assert code == 2
    assert "'medium'" in err and agent.DEFAULT_MODEL in err


def test_the_configured_endpoints_and_their_hosts_are_secrets(tmp_path: Path) -> None:
    """Like `DATA_AGENT_BASE_URL` (`events.known_secrets`): an endpoint from `[models.*]` and its
    host are masked too, the `classify` role's among them; the code default's is public."""
    text = (
        model("acme-large", PRICED, "https://llm.example/a")
        + model("acme-label", PRICED, "https://labels.example/v1")
        + '[roles]\nmain = "acme-large"\nclassify = "acme-label"\n'
    )
    secrets = settings(tmp_path, text).secrets()
    for value in (KEY, "https://llm.example/a", "llm.example", "https://labels.example/v1"):
        assert value in secrets
    assert "labels.example" in secrets
    defaults = ModelSettings.from_env({"DATA_AGENT_API_KEY": "k"})
    assert defaults.secrets() == ["k"]


@pytest.mark.parametrize("value", ["-1.0", "nan", "inf"])
def test_a_price_that_is_negative_or_not_finite_is_refused(tmp_path: Path, value: str) -> None:
    prices = f"input_price = {value}\ncache_read_price = 0.2\noutput_price = 8.0\n"
    text = model("acme", prices) + '[roles]\nmain = "acme"\n'
    with pytest.raises(ConfigError) as caught:
        settings(tmp_path, text)
    assert "[models.acme].input_price" in str(caught.value)
