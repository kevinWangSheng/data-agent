// What kind of tool a step called, for its tag and colour. Names from src/data_agent/mcp_server.py,
// verifier_tools.py and the built-in tools the agent is given.

export type ToolKind = 'sql' | 'context' | 'schema' | 'check' | 'file' | 'edit' | 'shell' | 'sandbox' | 'dbt' | 'agent' | 'other'

const DATA = 'mcp__data__'
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
  return tool.startsWith(DATA) ? tool.slice(DATA.length) : tool
}

export function toolKind(tool: string): ToolKind {
  if (tool.startsWith(DATA)) {
    const name = shortTool(tool)
    if (SQL.has(name)) return 'sql'
    if (CONTEXT.has(name)) return 'context'
    if (CHECK.has(name)) return 'check'
    if (name === 'sandbox_exec') return 'sandbox'
    if (name === 'dbt_execute') return 'dbt'
    return 'schema'
  }
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
  other: 'Tool',
}
