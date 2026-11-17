.PHONY: check fmt test web-install test-integration playwright-install

# Fast checks: no network, no model calls. Run after every change; must pass before commit.
check: web/node_modules
	uv run ruff check .
	uv run ruff format --check .
	uv run basedpyright
	uv run pytest -q
	pnpm --dir web typecheck
	pnpm --dir web test

# The web UI's packages, installed from the lockfile whenever it changes; offline, from pnpm's
# store, so `check` stays off the network. Run `make web-install` once where the store is empty.
web/node_modules: web/package.json web/pnpm-lock.yaml
	pnpm --dir web install --frozen-lockfile --offline
	touch web/node_modules

web-install:
	pnpm --dir web install --frozen-lockfile
	touch web/node_modules

# Auto-fix lint and formatting.
fmt:
	uv run ruff check --fix .
	uv run ruff format .

# Integration tests call a real model; the DATA_AGENT_* variables come from ~/.config/data-agent/env (plan §6).
# After pytest, the chat page's Playwright main flow, on the UI built into the package; it needs
# the browser from `make playwright-install`.
test-integration: web/node_modules
	set -a && . $(HOME)/.config/data-agent/env && set +a && uv run pytest -q -m integration
	pnpm --dir web build:local
	pnpm --dir web exec tsc -p e2e/tsconfig.json
	set -a && . $(HOME)/.config/data-agent/env && set +a && pnpm --dir web exec playwright test

# The browser the Playwright main flow drives (downloads it once).
playwright-install: web/node_modules
	pnpm --dir web exec playwright install chromium
