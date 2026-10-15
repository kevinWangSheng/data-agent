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

# Integration tests call a real model and need the DATA_AGENT_* variables from ~/.config/data-agent/env
# in the environment (plan §6).
test-integration:
	uv run pytest -q -m integration
