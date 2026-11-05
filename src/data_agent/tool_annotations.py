"""Annotations the MCP tools declare to the client."""

from mcp.types import ToolAnnotations

# Every tool is read-only (SQL goes through the read-only gateway; map_columns only reads
# workspace files). Claude Code runs an MCP tool concurrently with others in the same turn
# only when it carries readOnlyHint; without it the calls of one turn run one at a time.
READ_ONLY = ToolAnnotations(read_only_hint=True)
