// Unit tests for ticket 101: `sandbox_exec` and `dbt_execute` parsing and rendering in both the
// run page (`Detail`) and the chat page (`StepCard`), legacy Bash + guard-rejection compatibility,
// and the network switch defaults.

import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { createElement } from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, test } from 'vitest'

import { ChatNetworkToggle } from '../chat-page/Conversation'
import { StepCard } from '../chat-page/Turn'
import type { LiveSource } from '../data/source'
import { NewRun } from '../local/NewRun'
import { buildRunView, type RunEvent, type RunView, type Step } from '../run-view/model'
import { Detail } from './Detail'
import { sectionsOf } from './outline'
import {
  formatDbtCommandLine,
  parseDbtInput,
  parseDbtResult,
  parseSandboxInput,
  parseSandboxResult,
} from './tool-views'
import { KIND_NAME } from './tools'
import { inputLine } from './ui'

const FIXTURES = join(import.meta.dirname, '../../../tests/fixtures/web')

function loadEvents(rel: string): RunEvent[] {
  return readFileSync(join(FIXTURES, rel, 'events.jsonl'), 'utf8')
    .split('\n')
    .filter((line) => line.trim())
    .map((line) => JSON.parse(line) as RunEvent)
}

const agentToolsEvents = loadEvents('agent-tools')
const agentToolsView = buildRunView(agentToolsEvents)
const legacyCartEvents = loadEvents('real/dbt-cart-abandonment')
const legacyCartView = buildRunView(legacyCartEvents)

const stepOf = (view: RunView, id: string): Step => {
  const step = view.steps.find((s) => s.id === id)
  if (!step) throw new Error(`missing step ${id}`)
  return step
}

function renderRunStep(view: RunView, id: string): string {
  const step = stepOf(view, id)
  const { sections, loose } = sectionsOf(view)
  const order = [...loose, ...sections.flatMap((s) => s.items)]
  const numbers = new Map(view.steps.map((s, i) => [s.id, i + 1]))
  return renderToStaticMarkup(
    createElement(Detail, {
      view,
      selectedId: step.id,
      selected: { kind: 'step', step },
      order,
      numbers,
      sections,
      onSelect: () => {},
    }),
  )
}

function renderChatStep(step: Step, defaultOpen = true): string {
  return renderToStaticMarkup(createElement(StepCard, { step, database: undefined, defaultOpen }))
}

describe('parseSandboxInput and parseSandboxResult', () => {
  test('extracts command, data_files and timeout_seconds', () => {
    expect(
      parseSandboxInput({ command: 'echo hi', data_files: ['query_1', 'query_2'], timeout_seconds: 120 }),
    ).toEqual({
      command: 'echo hi',
      dataFiles: ['query_1', 'query_2'],
      timeoutSeconds: 120,
    })
  })

  test('parses exit_code, stdout, stderr and output_files', () => {
    const step = stepOf(agentToolsView, 't-sbx1')
    expect(parseSandboxResult(step.result!)).toEqual({
      exitCode: 0,
      stdout: 'count    3.000000\nmean   440.166667\nstd    260.531188',
      stderr: 'UserWarning: tight_layout applied',
      outputCut: null,
      outputFiles: ['outputs/revenue_chart.png', 'outputs/summary.csv'],
      outputFilesCut: null,
      timedOut: false,
      timeoutNotice: null,
    })
  })

  test('parses non-zero exit_code, [OUTPUT CUT] and [OUTPUT FILES CUT]', () => {
    const step = stepOf(agentToolsView, 't-sbx2')
    const parsed = parseSandboxResult(step.result!)
    expect(parsed).toMatchObject({
      exitCode: 2,
      stdout: 'processed 150 items',
      stderr: 'Traceback (most recent call last):\nValueError: missing column',
      outputFiles: ['outputs/batch_001.csv'],
      timedOut: false,
    })
    expect(parsed?.outputCut).toContain('[OUTPUT CUT]')
    expect(parsed?.outputCut).toContain('sandbox_logs/a1b2c3d4e5f6.stdout.txt')
    expect(parsed?.outputFilesCut).toContain('[OUTPUT FILES CUT]')
    expect(parsed?.outputFilesCut).toContain('sandbox_logs/f6e5d4c3b2a1.output_files.txt')
  })

  test('parses timeout reply and keeps partial output', () => {
    const raw =
      'Error executing tool sandbox_exec: The command timed out after 30 seconds and was killed. Its output until then:\nstdout:\npartial line\noutput_files:\n- outputs/partial.csv'
    expect(parseSandboxResult(raw)).toEqual({
      exitCode: null,
      stdout: 'partial line',
      stderr: null,
      outputCut: null,
      outputFiles: ['outputs/partial.csv'],
      outputFilesCut: null,
      timedOut: true,
      timeoutNotice: 'The command timed out after 30 seconds and was killed.',
    })
  })

  test('returns null on unstructured error text', () => {
    expect(parseSandboxResult('Error executing tool sandbox_exec: command must not be blank')).toBeNull()
  })
})

describe('parseDbtInput and parseDbtResult', () => {
  test('parses all structured arguments and builds a one-line summary', () => {
    const parsed = parseDbtInput(stepOf(agentToolsView, 't-dbt1').input)
    expect(parsed).toEqual({
      command: 'run',
      select: '+order_totals',
      exclude: 'tag:deprecated',
      projectDir: 'dbt_models_duckdb',
      fullRefresh: true,
      threads: 4,
    })
    expect(formatDbtCommandLine(parsed)).toBe(
      'dbt run --select +order_totals --exclude tag:deprecated --full-refresh --threads 4 --project-dir dbt_models_duckdb',
    )
    expect(inputLine(stepOf(agentToolsView, 't-dbt1'))).toBe(formatDbtCommandLine(parsed))
  })

  test('parses target_schema, exit_code, output and run_results on success', () => {
    const parsed = parseDbtResult(stepOf(agentToolsView, 't-dbt1').result!)
    expect(parsed).toMatchObject({
      targetSchema: 'da_run_20261121t100000z_abc123_deadbeef',
      exitCode: 0,
      runResults: 'success=2',
      failures: [],
      timedOut: false,
    })
    expect(parsed?.output).toContain('Running with dbt=1.9.4')
  })

  test('parses failures on non-zero dbt_execute', () => {
    const parsed = parseDbtResult(stepOf(agentToolsView, 't-dbt2').result!)
    expect(parsed).toMatchObject({
      targetSchema: 'da_run_20261121t100000z_abc123_deadbeef',
      exitCode: 1,
      runResults: 'fail=1',
      failures: ['test.shop.not_null_order_totals_tier: Got 1 result, configured to fail if != 0'],
      timedOut: false,
    })
  })
})

describe('run page and chat page rendering: sandbox_exec and dbt_execute', () => {
  test('sandbox_exec renders highlighted command, data_files, exit code, collapsible stdout/stderr, output_files and cut notices on run page', () => {
    const okHtml = renderRunStep(agentToolsView, 't-sbx1')
    expect(okHtml).toContain('data-testid="sandbox-input"')
    expect(okHtml).toContain('tok-comment')
    expect(okHtml).toContain('tok-string')
    expect(okHtml).toContain('data-testid="sandbox-data-files"')
    expect(okHtml).toContain('query_1')
    expect(okHtml).toContain('timeout 120s')
    expect(okHtml).toContain('chip-ok">exit 0</span>')
    expect(okHtml).toContain('data-testid="sandbox-stdout"')
    expect(okHtml).toContain('data-testid="sandbox-stderr"')
    expect(okHtml).toContain('outputs/revenue_chart.png')
    expect(okHtml).toContain('outputs/summary.csv')

    const failHtml = renderRunStep(agentToolsView, 't-sbx2')
    expect(failHtml).toContain('chip-fail">exit 2</span>')
    expect(failHtml).toContain('1+ output files')
    expect(failHtml).not.toContain('>1 output file<')
    expect(failHtml).toContain('data-testid="sandbox-output-cut"')
    expect(failHtml).toContain('sandbox_logs/a1b2c3d4e5f6.stdout.txt')
    expect(failHtml).toContain('data-testid="sandbox-output-files-cut"')
    expect(failHtml).toContain('sandbox_logs/f6e5d4c3b2a1.output_files.txt')
    expect(failHtml).toContain('max-w-full overflow-hidden text-ellipsis')
  })

  test('sandbox_exec renders all structured fields in chat page StepCard', () => {
    const html = renderChatStep(stepOf(agentToolsView, 't-sbx1'), true)
    expect(html).toContain('SBX')
    expect(html).toContain('data-testid="sandbox-input"')
    expect(html).toContain('query_1')
    expect(html).toContain('chip-ok">exit 0</span>')
    expect(html).toContain('outputs/revenue_chart.png')
    expect(html).toContain('outputs/summary.csv')
  })

  test('dbt_execute renders subcommand, select/exclude, optional flags, target_schema, exit code, run_results and failures on run page', () => {
    const okHtml = renderRunStep(agentToolsView, 't-dbt1')
    expect(okHtml).toContain('data-testid="dbt-input"')
    expect(okHtml).toContain('dbt run')
    expect(okHtml).toContain('+order_totals')
    expect(okHtml).toContain('tag:deprecated')
    expect(okHtml).toContain('dbt_models_duckdb')
    expect(okHtml).toContain('--full-refresh')
    expect(okHtml).toContain('chip-ok">exit 0</span>')
    expect(okHtml).toContain('da_run_20261121t100000z_abc123_deadbeef')
    expect(okHtml).toContain('success=2')
    expect(okHtml).toContain('data-testid="dbt-output"')

    const failHtml = renderRunStep(agentToolsView, 't-dbt2')
    expect(failHtml).toContain('chip-fail">exit 1</span>')
    expect(failHtml).toContain('data-testid="dbt-failures"')
    expect(failHtml).toContain('not_null_order_totals_tier')
  })

  test('dbt_execute renders structured fields in chat page StepCard', () => {
    const collapsed = renderChatStep(stepOf(agentToolsView, 't-dbt1'), false)
    expect(collapsed).toContain('DBT')
    expect(collapsed).toContain('dbt run --select +order_totals')

    const expanded = renderChatStep(stepOf(agentToolsView, 't-dbt1'), true)
    expect(expanded).toContain('data-testid="dbt-input"')
    expect(expanded).toContain('data-testid="dbt-result"')
    expect(expanded).toContain('chip-ok">exit 0</span>')
    expect(expanded).toContain('success=2')
  })
})

describe('legacy event files and updated copy', () => {
  test('legacy Bash call and guard rejection still render on run page and chat page', () => {
    const guardStep = stepOf(legacyCartView, 'call_01_ET_QZkjmQFAkDcmoBG3HfNH9615')
    expect(guardStep.tool).toBe('Bash')
    expect(guardStep.rejectedBy).toBe('guard')

    const runHtml = renderRunStep(legacyCartView, guardStep.id)
    expect(runHtml).toContain('DENIED · guard')
    expect(runHtml).toContain('Denied by a guard hook')
    expect(runHtml).toContain('Shell (legacy)')
    expect(runHtml).not.toContain('dbt only')

    const chatHtml = renderChatStep(guardStep, true)
    expect(chatHtml).toContain('DENIED · guard')
    expect(chatHtml).toContain('SH')
  })

  test('tool kind names reflect ADR-0010 (no outdated "dbt only" shell copy)', () => {
    expect(KIND_NAME.shell).not.toMatch(/dbt only/i)
    expect(KIND_NAME.sandbox).toBe('Sandbox shell')
    expect(KIND_NAME.dbt).toBe('dbt execution')
  })
})

describe('network switch defaults', () => {
  test('NewRun form renders network toggle checked by default', () => {
    const stubLive = { options: () => new Promise(() => {}) } as unknown as LiveSource
    const html = renderToStaticMarkup(createElement(NewRun, { live: stubLive, onStarted: () => {}, onClose: () => {} }))
    expect(html).toContain('data-testid="new-run-network"')
    expect(html).toMatch(/data-testid="new-run-network"[^>]*checked/)
  })

  test('ChatNetworkToggle is enabled in turn mode and disabled with "(next turn)" label in steer mode', () => {
    const turnHtml = renderToStaticMarkup(
      createElement(ChatNetworkToggle, { network: true, mode: 'turn', onChange: () => {} }),
    )
    expect(turnHtml).toContain('data-testid="chat-network"')
    expect(turnHtml).toContain('Network access')
    expect(turnHtml).not.toContain('disabled')
    expect(turnHtml).not.toContain('(next turn)')

    const steerHtml = renderToStaticMarkup(
      createElement(ChatNetworkToggle, { network: true, mode: 'steer', onChange: () => {} }),
    )
    expect(steerHtml).toContain('data-testid="chat-network"')
    expect(steerHtml).toMatch(/data-testid="chat-network"[^>]*disabled/)
    expect(steerHtml).toContain('opacity-60')
    expect(steerHtml).toContain('Network access (next turn)')
  })
})

describe('Detail step keying and raw-result button', () => {
  test('StepDetail is keyed by selected.step.id so state resets when switching steps', () => {
    const { sections, loose } = sectionsOf(agentToolsView)
    const order = [...loose, ...sections.flatMap((s) => s.items)]
    const numbers = new Map(agentToolsView.steps.map((s, i) => [s.id, i + 1]))
    for (const id of ['t-sbx1', 't-sbx2', 't-dbt1']) {
      const step = stepOf(agentToolsView, id)
      const tree = Detail({
        view: agentToolsView,
        selectedId: id,
        selected: { kind: 'step', step },
        order,
        numbers,
        sections,
        onSelect: () => {},
      })
      expect((tree.props as { children: { key: string | null } }).children.key).toBe(id)
    }
  })

  test('unstructured sandbox_exec and dbt_execute error text omits the raw result button', () => {
    const ts = '2026-11-21T10:00:00+00:00'
    const events: RunEvent[] = [
      { seq: 0, type: 'tool_start', ts, tool: 'mcp__data__sandbox_exec', tool_use_id: 's-err', input: { command: '' }, parent: null },
      { seq: 1, type: 'tool_end', ts, tool: 'mcp__data__sandbox_exec', tool_use_id: 's-err', content: 'Error executing tool sandbox_exec: command must not be blank', is_error: true, parent: null },
      { seq: 2, type: 'tool_start', ts, tool: 'mcp__data__dbt_execute', tool_use_id: 'd-err', input: { command: 'show' }, parent: null },
      { seq: 3, type: 'tool_end', ts, tool: 'mcp__data__dbt_execute', tool_use_id: 'd-err', content: 'Error executing tool dbt_execute: command must be one of: run, test', is_error: true, parent: null },
    ]
    const view = buildRunView(events)
    expect(renderRunStep(view, 's-err')).not.toContain('raw result')
    expect(renderRunStep(view, 'd-err')).not.toContain('raw result')
    // Structured results still show the raw result toggle.
    expect(renderRunStep(agentToolsView, 't-sbx1')).toContain('raw result')
    expect(renderRunStep(agentToolsView, 't-dbt1')).toContain('raw result')
  })
})
