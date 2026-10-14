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

# Integration tests call a real model; key is passed to the test process only.
test-integration:
	uv run --env-file .env pytest -q -m integration
