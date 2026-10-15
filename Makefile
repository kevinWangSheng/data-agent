.PHONY: check fmt test

# Fast checks: no network, no model calls. Run after every change; must pass before commit.
check:
	uv run ruff check .
	uv run ruff format --check .
	uv run basedpyright
	uv run pytest -q

# Auto-fix lint and formatting.
fmt:
	uv run ruff check --fix .
	uv run ruff format .

# Integration tests call a real model; the DATA_AGENT_* variables come from ~/.config/data-agent/env (plan §6).
test-integration:
	set -a && . $(HOME)/.config/data-agent/env && set +a && uv run pytest -q -m integration
