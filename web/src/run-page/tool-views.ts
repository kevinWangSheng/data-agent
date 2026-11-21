// Pure parsers for `sandbox_exec` and `dbt_execute` inputs and results (ticket 101).
// Formats follow src/data_agent/sandbox.py (`reply_text`, `_format_output_files`) and
// src/data_agent/dbt_execute.py (`DbtExecutor.execute`, `run_results`).

export const SANDBOX_EXEC_TOOL = 'mcp__data__sandbox_exec'
export const DBT_EXECUTE_TOOL = 'mcp__data__dbt_execute'

export const isSandboxExec = (tool: string): boolean => tool === SANDBOX_EXEC_TOOL
export const isDbtExecute = (tool: string): boolean => tool === DBT_EXECUTE_TOOL

export type SandboxInput = {
  command: string
  dataFiles: string[]
  timeoutSeconds: number | null
}

export type SandboxResult = {
  exitCode: number | null
  stdout: string | null
  stderr: string | null
  outputCut: string | null
  outputFiles: string[]
  outputFilesCut: string | null
  timedOut: boolean
  timeoutNotice: string | null
}

export type DbtInput = {
  command: string
  select: string
  exclude: string
  projectDir: string
  fullRefresh: boolean
  threads: number | null
}

export type DbtResult = {
  targetSchema: string | null
  exitCode: number | null
  output: string | null
  runResults: string | null
  failures: string[]
  timedOut: boolean
  timeoutNotice: string | null
}

function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === 'object' && !Array.isArray(value) ? (value as Record<string, unknown>) : {}
}

export function parseSandboxInput(input: unknown): SandboxInput {
  const raw = asRecord(input)
  const command = typeof raw.command === 'string' ? raw.command : ''
  const dataFiles = Array.isArray(raw.data_files) ? raw.data_files.filter((x): x is string => typeof x === 'string' && x.length > 0) : []
  const timeoutSeconds = typeof raw.timeout_seconds === 'number' && Number.isFinite(raw.timeout_seconds) ? raw.timeout_seconds : null
  return { command, dataFiles, timeoutSeconds }
}

const SANDBOX_TIMEOUT = /^(?:Error executing tool \S+:\s*)?(The command timed out after \d+ seconds and was killed\.) Its output until then:\n?/
const EXIT_CODE_LINE = /^exit_code:\s*(-?\d+)$/

export function parseSandboxResult(text: string): SandboxResult | null {
  let body = text
  let timedOut = false
  let timeoutNotice: string | null = null
  const tm = SANDBOX_TIMEOUT.exec(body)
  if (tm) {
    timedOut = true
    timeoutNotice = tm[1]
    body = body.slice(tm[0].length)
  }

  const lines = body.length ? body.split('\n') : []
  let end = lines.length

  // Parse trailing `output_files:` section (ticket 103, sandbox.py `_format_output_files`).
  let outputFiles: string[] = []
  let outputFilesCut: string | null = null
  let filesEnd = end
  let cutCandidate: string | null = null
  if (filesEnd > 0 && lines[filesEnd - 1].startsWith('[OUTPUT FILES CUT] ')) {
    cutCandidate = lines[filesEnd - 1]
    filesEnd -= 1
  }
  const foundFiles: string[] = []
  let cursor = filesEnd
  while (cursor > 0 && lines[cursor - 1].startsWith('- ')) {
    foundFiles.push(lines[cursor - 1].slice(2))
    cursor -= 1
  }
  if (cursor > 0 && lines[cursor - 1] === 'output_files:' && (foundFiles.length > 0 || cutCandidate !== null)) {
    outputFiles = foundFiles.reverse()
    outputFilesCut = cutCandidate
    end = cursor - 1
  }

  // Parse trailing `[OUTPUT CUT]` notice (before `output_files:` if present).
  let outputCut: string | null = null
  if (end > 0 && lines[end - 1].startsWith('[OUTPUT CUT] ')) {
    outputCut = lines[end - 1]
    end -= 1
  }

  // Parse leading `exit_code: N`.
  let start = 0
  let exitCode: number | null = null
  if (start < end) {
    const m = EXIT_CODE_LINE.exec(lines[start])
    if (m) {
      exitCode = Number(m[1])
      start += 1
    }
  }

  // Parse `stdout:` and `stderr:` sections.
  let stdout: string | null = null
  let stderr: string | null = null
  const rest = lines.slice(start, end)
  if (rest[0] === 'stdout:') {
    const errRel = rest.slice(1).indexOf('stderr:')
    if (errRel >= 0) {
      stdout = rest.slice(1, 1 + errRel).join('\n')
      stderr = rest.slice(2 + errRel).join('\n')
    } else {
      stdout = rest.slice(1).join('\n')
    }
  } else if (rest[0] === 'stderr:') {
    stderr = rest.slice(1).join('\n')
  }

  if (!timedOut && exitCode === null && stdout === null && stderr === null && outputCut === null && outputFiles.length === 0 && outputFilesCut === null) {
    return null
  }
  return { exitCode, stdout, stderr, outputCut, outputFiles, outputFilesCut, timedOut, timeoutNotice }
}

export function parseDbtInput(input: unknown): DbtInput {
  const raw = asRecord(input)
  return {
    command: typeof raw.command === 'string' ? raw.command.trim() : '',
    select: typeof raw.select === 'string' ? raw.select.trim() : '',
    exclude: typeof raw.exclude === 'string' ? raw.exclude.trim() : '',
    projectDir: typeof raw.project_dir === 'string' ? raw.project_dir.trim() : '',
    fullRefresh: raw.full_refresh === true,
    threads: typeof raw.threads === 'number' && Number.isFinite(raw.threads) ? raw.threads : null,
  }
}

/** One-line summary of a `dbt_execute` call, for outline rows and collapsed cards. */
export function formatDbtCommandLine(parsed: DbtInput): string {
  const parts = ['dbt', parsed.command || '?']
  if (parsed.select) parts.push('--select', parsed.select)
  if (parsed.exclude) parts.push('--exclude', parsed.exclude)
  if (parsed.fullRefresh) parts.push('--full-refresh')
  if (parsed.threads !== null) parts.push('--threads', String(parsed.threads))
  if (parsed.projectDir) parts.push('--project-dir', parsed.projectDir)
  return parts.join(' ')
}

const DBT_TIMEOUT = /^(?:Error executing tool \S+:\s*)?(dbt timed out after \d+ seconds and was killed\.) Its output until then:\n?/
const TARGET_SCHEMA_LINE = /^target_schema:\s*(.+)$/
const RUN_RESULTS_LINE = /^run_results:\s*(.+)$/

export function parseDbtResult(text: string): DbtResult | null {
  let body = text
  let timedOut = false
  let timeoutNotice: string | null = null
  const tm = DBT_TIMEOUT.exec(body)
  if (tm) {
    timedOut = true
    timeoutNotice = tm[1]
    body = body.slice(tm[0].length)
  }

  const lines = body.length ? body.split('\n') : []
  let start = 0
  let end = lines.length

  let targetSchema: string | null = null
  if (start < end) {
    const m = TARGET_SCHEMA_LINE.exec(lines[start])
    if (m) {
      targetSchema = m[1].trim()
      start += 1
    }
  }

  let exitCode: number | null = null
  if (start < end) {
    const m = EXIT_CODE_LINE.exec(lines[start])
    if (m) {
      exitCode = Number(m[1])
      start += 1
    }
  }

  // Parse trailing `run_results:` and optional `failures:` section (dbt_execute.py `run_results`).
  let runResults: string | null = null
  let failures: string[] = []
  const failuresIdx = lines.lastIndexOf('failures:', end - 1)
  if (failuresIdx > start && RUN_RESULTS_LINE.test(lines[failuresIdx - 1])) {
    const m = RUN_RESULTS_LINE.exec(lines[failuresIdx - 1])
    runResults = m ? m[1].trim() : null
    failures = lines.slice(failuresIdx + 1, end).filter((l) => l.length > 0)
    end = failuresIdx - 1
  } else if (end > start) {
    const m = RUN_RESULTS_LINE.exec(lines[end - 1])
    if (m) {
      runResults = m[1].trim()
      end -= 1
    }
  }

  let output: string | null = null
  const rest = lines.slice(start, end)
  if (rest[0] === 'output:') {
    output = rest.slice(1).join('\n')
  } else if (timedOut && rest.length > 0) {
    output = rest.join('\n')
  }

  if (!timedOut && targetSchema === null && exitCode === null && output === null && runResults === null && failures.length === 0) {
    return null
  }
  return { targetSchema, exitCode, output, runResults, failures, timedOut, timeoutNotice }
}
