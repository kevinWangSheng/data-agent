"""How the MCP tools are declared to the client: their annotations, and the `database` argument a
server with several databases adds to each query and context tool (ticket 89, after nao's
`execute_sql` `database_id`, required when several databases are configured)."""

import inspect
from collections.abc import Callable, Mapping
from typing import Annotated, Any

from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations
from pydantic import Field

# Every tool is read-only (SQL goes through the read-only gateway; map_columns only reads
# workspace files). Claude Code runs an MCP tool concurrently with others in the same turn
# only when it carries readOnlyHint; without it the calls of one turn run one at a time.
READ_ONLY = ToolAnnotations(read_only_hint=True)

DATABASE_ARGUMENT = inspect.Parameter(
    "database",
    inspect.Parameter.POSITIONAL_OR_KEYWORD,
    annotation=Annotated[
        str,
        Field(description="The database to use: one of the names the system prompt lists."),
    ],
)


def pick[D](databases: Mapping[str, D], name: str) -> D:
    if name not in databases:
        raise ToolError(f"Unknown database {name!r}. The databases are: {', '.join(databases)}")
    return databases[name]


def database_tools[D](
    server: MCPServer, databases: Mapping[str, D]
) -> Callable[..., Callable[[Callable[..., Any]], None]]:
    """A decorator factory like `server.tool`, for tools written as `impl(db, ...)`, where `db`
    is one of `databases` (name -> what the tool needs of that database). With one database the
    tool's parameters are impl's after `db`, so its definition is what it was before several
    databases existed; with more, a required `database` (one of the names) comes first and picks
    `db`, and an unknown name is an error that lists the names."""

    def tool(**options: Any) -> Callable[[Callable[..., Any]], None]:
        def register(impl: Callable[..., Any]) -> None:
            signature = inspect.signature(impl)
            params = list(signature.parameters.values())[1:]
            if len(databases) == 1:
                [only] = databases.values()

                def declared(**arguments: Any) -> Any:
                    return impl(only, **arguments)
            else:
                params.insert(0, DATABASE_ARGUMENT)

                def declared(**arguments: Any) -> Any:
                    db = pick(databases, arguments.pop("database"))
                    return impl(db, **arguments)

            declared.__name__ = impl.__name__
            declared.__doc__ = impl.__doc__
            # The MCP library reads a tool's parameters from its signature.
            declared.__signature__ = signature.replace(parameters=params)  # pyright: ignore[reportFunctionMemberAccess]
            server.tool(**options)(declared)

        return register

    return tool
