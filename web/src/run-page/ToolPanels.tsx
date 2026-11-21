// Structured display panels for `sandbox_exec` and `dbt_execute`, shared by the run page
// (./Detail.tsx) and the chat page (../chat-page/Turn.tsx).

import { FileOutput } from 'lucide-react'

import { ChatCode, CopyButton } from '../code/chat-code'
import { parseDbtInput, parseDbtResult, parseSandboxInput, parseSandboxResult } from './tool-views'
import { Caret, Pre } from './ui'

function lineCount(text: string): string {
  const n = text ? text.split('\n').length : 0
  return `${n} line${n === 1 ? '' : 's'} · ${text.length.toLocaleString()} chars`
}

function ExitChip({ exitCode, timedOut }: { exitCode: number | null; timedOut: boolean }) {
  if (timedOut) return <span className="chip chip-fail">TIMED OUT</span>
  if (exitCode === null) return null
  return <span className={`chip ${exitCode === 0 ? 'chip-ok' : 'chip-fail'}`}>exit {exitCode}</span>
}

export function SandboxInputView({ input, compact = false }: { input: unknown; compact?: boolean }) {
  const { command, dataFiles, timeoutSeconds } = parseSandboxInput(input)
  return (
    <div className="space-y-2" data-testid="sandbox-input">
      <div>
        <div className="flex items-center justify-between gap-2 mb-1">
          <span className="text-[11px] text-ink-3">Command</span>
          <div className="flex items-center gap-2">
            {timeoutSeconds !== null && <span className="mono text-[10.5px] text-ink-3">timeout {timeoutSeconds}s</span>}
            {command && <CopyButton text={command} label="Copy" />}
          </div>
        </div>
        <ChatCode code={command} language="shell" maxHeight={compact ? 220 : 360} />
      </div>
      {dataFiles.length > 0 && (
        <div className="flex flex-wrap items-center gap-1.5" data-testid="sandbox-data-files">
          <span className="text-[11px] text-ink-3">data_files</span>
          {dataFiles.map((id) => (
            <span key={id} className="chip chip-na mono" title={`Written to data/${id}.csv before execution`}>
              {id}
            </span>
          ))}
        </div>
      )}
    </div>
  )
}

export function SandboxResultView({ result, compact = false }: { result: string; compact?: boolean }) {
  const parsed = parseSandboxResult(result)
  if (!parsed) return <Pre text={result} maxHeight={compact ? 220 : 520} />
  const { exitCode, stdout, stderr, outputCut, outputFiles, outputFilesCut, timedOut, timeoutNotice } = parsed
  const hasStream = (stdout !== null && stdout.length > 0) || (stderr !== null && stderr.length > 0)
  const maxHeight = compact ? 200 : 420

  return (
    <div className="space-y-2" data-testid="sandbox-result">
      <div className="flex flex-wrap items-center gap-2">
        <ExitChip exitCode={exitCode} timedOut={timedOut} />
        {outputFiles.length > 0 && (
          <span className="text-[11px] text-ink-3">
            {outputFiles.length} output file{outputFiles.length === 1 ? '' : 's'}
          </span>
        )}
      </div>

      {timeoutNotice && <div className="banner banner-fail text-[12px]">{timeoutNotice}</div>}

      {stdout !== null && stdout.length > 0 && (
        <details open={!compact || stdout.length <= 2000} className="border border-line rounded" data-testid="sandbox-stdout">
          <summary className="px-2.5 py-1.5 flex items-center gap-2 text-[11.5px] text-ink-2">
            <Caret />
            <span className="mono font-medium">stdout</span>
            <span className="text-ink-3">{lineCount(stdout)}</span>
          </summary>
          <div className="px-2 pb-2">
            <Pre text={stdout} maxHeight={maxHeight} />
          </div>
        </details>
      )}

      {stderr !== null && stderr.length > 0 && (
        <details open className="border border-line rounded" data-testid="sandbox-stderr">
          <summary className="px-2.5 py-1.5 flex items-center gap-2 text-[11.5px] text-fail">
            <Caret />
            <span className="mono font-medium">stderr</span>
            <span className="text-ink-3">{lineCount(stderr)}</span>
          </summary>
          <div className="px-2 pb-2">
            <Pre text={stderr} maxHeight={maxHeight} />
          </div>
        </details>
      )}

      {!hasStream && !timedOut && <div className="text-[12px] text-ink-3">(no output)</div>}

      {outputCut && (
        <div className="banner banner-deny text-[11.5px] mono" data-testid="sandbox-output-cut">
          {outputCut}
        </div>
      )}

      {(outputFiles.length > 0 || outputFilesCut) && (
        <div className="border border-line rounded px-2.5 py-2 space-y-1.5" data-testid="sandbox-output-files">
          <div className="flex items-center gap-1.5 text-[11px] text-ink-3">
            <FileOutput className="h-3.5 w-3.5" />
            <span>Output files</span>
          </div>
          {outputFiles.length > 0 && (
            <ul className="flex flex-wrap gap-1.5 list-none m-0 p-0">
              {outputFiles.map((file) => (
                <li key={file} className="chip chip-info mono">
                  {file}
                </li>
              ))}
            </ul>
          )}
          {outputFilesCut && (
            <div className="banner banner-deny text-[11.5px] mono" data-testid="sandbox-output-files-cut">
              {outputFilesCut}
            </div>
          )}
        </div>
      )}
    </div>
  )
}

export function DbtInputView({ input }: { input: unknown }) {
  const { command, select, exclude, projectDir, fullRefresh, threads } = parseDbtInput(input)
  const fields: [string, string][] = []
  if (select) fields.push(['select', select])
  if (exclude) fields.push(['exclude', exclude])
  if (projectDir) fields.push(['project_dir', projectDir])
  if (fullRefresh) fields.push(['full_refresh', 'true'])
  if (threads !== null) fields.push(['threads', String(threads)])

  return (
    <div className="space-y-2" data-testid="dbt-input">
      <div className="flex flex-wrap items-center gap-2">
        <span className="chip chip-info mono">dbt {command || '?'}</span>
        {fullRefresh && <span className="chip chip-deny mono">--full-refresh</span>}
      </div>
      {fields.length > 0 && (
        <dl className="kv">
          {fields.map(([k, v]) => (
            <div key={k} className="contents">
              <dt>{k}</dt>
              <dd>{v}</dd>
            </div>
          ))}
        </dl>
      )}
    </div>
  )
}

export function DbtResultView({ result, compact = false }: { result: string; compact?: boolean }) {
  const parsed = parseDbtResult(result)
  if (!parsed) return <Pre text={result} maxHeight={compact ? 220 : 520} />
  const { targetSchema, exitCode, output, runResults, failures, timedOut, timeoutNotice } = parsed
  const maxHeight = compact ? 220 : 480

  return (
    <div className="space-y-2" data-testid="dbt-result">
      <div className="flex flex-wrap items-center gap-2">
        <ExitChip exitCode={exitCode} timedOut={timedOut} />
        {targetSchema && (
          <span className="chip chip-na mono" data-testid="dbt-target-schema" title="Per-run scratch schema">
            schema: {targetSchema}
          </span>
        )}
        {runResults && (
          <span className={`chip mono ${/\b(error|fail)=/.test(runResults) ? 'chip-fail' : 'chip-ok'}`} data-testid="dbt-run-results">
            {runResults}
          </span>
        )}
      </div>

      {timeoutNotice && <div className="banner banner-fail text-[12px]">{timeoutNotice}</div>}

      {failures.length > 0 && (
        <div className="banner banner-fail space-y-1" data-testid="dbt-failures">
          <div className="text-[11.5px] font-semibold">Failures</div>
          <ul className="m-0 pl-4 text-[11.5px] mono space-y-0.5">
            {failures.map((f, i) => (
              <li key={i} className="whitespace-pre-wrap">
                {f}
              </li>
            ))}
          </ul>
        </div>
      )}

      {output !== null && output.length > 0 ? (
        <details open={!compact || output.length <= 2000} className="border border-line rounded" data-testid="dbt-output">
          <summary className="px-2.5 py-1.5 flex items-center gap-2 text-[11.5px] text-ink-2">
            <Caret />
            <span className="mono font-medium">output</span>
            <span className="text-ink-3">{lineCount(output)}</span>
          </summary>
          <div className="px-2 pb-2">
            <Pre text={output} maxHeight={maxHeight} />
          </div>
        </details>
      ) : (
        !timedOut && <div className="text-[12px] text-ink-3">(no output)</div>
      )}
    </div>
  )
}
