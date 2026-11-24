// What kind of tool a step called, for its tag and colour. Names from src/data_agent/mcp_server.py,
// verifier_tools.py and the built-in tools the agent is given.

export type ToolKind = 'sql' | 'context' | 'schema' | 'check' | 'file' | 'edit' | 'shell' | 'sandbox' | 'dbt' | 'agent' | 'chart' | 'external' | 'other'

const DATA = 'mcp__data__'
// `mcp__<server>__<tool>` from any other MCP server the run was given (`--mcp-servers`). Server
// names never contain `__` (src/data_agent/external_mcp.py), so the first `__` ends the server.
const EXTERNAL = /^mcp__(.+?)__(.+)$/
const SQL = new Set(['query_database', 'query_labels', 'explain_query', 'classify_text'])
const CONTEXT = new Set(['discover', 'hydrate', 'dictionary_search'])
const CHECK = new Set([
  'map_columns',
  'check_model_schema',
  'audit_model_sources',
  'verify_model_values',
  'model_diff',
  'contract_check',
  'grain_facts',
  'compare_by_key',
])

export function shortTool(tool: string): string {
  if (tool.startsWith(DATA)) return tool.slice(DATA.length)
  const external = EXTERNAL.exec(tool)
  return external ? `${external[1]} · ${external[2]}` : tool
}

export function toolKind(tool: string): ToolKind {
  if (tool.startsWith(DATA)) {
    const name = shortTool(tool)
    if (SQL.has(name)) return 'sql'
    if (CONTEXT.has(name)) return 'context'
    if (CHECK.has(name)) return 'check'
    if (name === 'sandbox_exec') return 'sandbox'
    if (name === 'dbt_execute') return 'dbt'
    if (name === 'display_chart') return 'chart'
    return 'schema'
  }
  if (EXTERNAL.test(tool)) return 'external'
  if (tool === 'Read' || tool === 'Glob' || tool === 'Grep') return 'file'
  if (tool === 'Write' || tool === 'Edit') return 'edit'
  if (tool === 'Bash') return 'shell'
  if (tool === 'Agent' || tool === 'Task') return 'agent'
  return 'other'
}

export const KIND_TAG: Record<ToolKind, string> = {
  sql: 'SQL',
  context: 'CTX',
  schema: 'SCH',
  check: 'CHK',
  file: 'FILE',
  edit: 'EDIT',
  shell: 'SH',
  sandbox: 'SBX',
  dbt: 'DBT',
  agent: 'AGT',
  chart: 'CHRT',
  external: 'EXT',
  other: 'TOOL',
}

export const KIND_NAME: Record<ToolKind, string> = {
  sql: 'SQL through the read-only gateway',
  context: 'Context tool (by reference)',
  schema: 'Schema tool',
  check: 'Verifier fact tool',
  file: 'File read or search',
  edit: 'File write or edit',
  shell: 'Shell (legacy)',
  sandbox: 'Sandbox shell',
  dbt: 'dbt execution',
  agent: 'Subagent dispatch',
  chart: 'Answer chart',
  external: 'External MCP tool',
  other: 'Tool',
}
