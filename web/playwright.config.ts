// The chat page's browser main flow (web/e2e/, ticket 88): a real `data-agent web` with a real
// agent and model, on a port and a runs directory of its own, serving the UI built into the
// package (`pnpm --dir web build:local`; `make test-integration` builds it first). The data
// sources are the end-to-end tests' two DuckDB fixtures (tests/fixtures/chat/build_sources.py).
// The model's DATA_AGENT_* variables come from the environment (`make test-integration`).

import { mkdtempSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'

import { defineConfig } from '@playwright/test'

// Set once by the runner; its workers inherit the environment, so all of them see one directory.
process.env.CHAT_E2E_DIR ??= mkdtempSync(join(tmpdir(), 'chat-e2e-'))
const dir = process.env.CHAT_E2E_DIR
const port = Number(process.env.CHAT_E2E_PORT ?? 8797)
const repo = join(import.meta.dirname, '..')

export default defineConfig({
  testDir: 'e2e',
  // Traces and failure context go with the other run outputs, which git ignores.
  outputDir: join(repo, 'runs', 'playwright'),
  // A real agent answers several turns.
  timeout: 45 * 60_000,
  workers: 1,
  retries: 0,
  reporter: 'list',
  // Clicks and fills wait a minute at most; the waits for the agent set their own timeout.
  use: { baseURL: `http://127.0.0.1:${port}`, actionTimeout: 60_000, trace: 'retain-on-failure' },
  webServer: {
    command:
      `uv run python tests/fixtures/chat/build_sources.py ${dir}/sources && mkdir -p ${dir}/runs && ` +
      `uv run data-agent web --runs-dir ${dir}/runs --port ${port} --data-sources ${dir}/sources/sources.toml`,
    cwd: repo,
    // Up once the run API answers; a page that is not built shows as the test's failure.
    url: `http://127.0.0.1:${port}/api/runs`,
    reuseExistingServer: false,
    timeout: 120_000,
  },
})
