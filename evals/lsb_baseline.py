# MIT License
#
# Copyright (c) 2024 bird_sql
#
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in all
# copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
# SOFTWARE.
"""LiveSQLBench's official single-call baseline (ticket 138, spec D2): its prompt and its SQL
extraction, copied from upstream LiveSQLBench @ e15cd22 (github.com/bird-bench/livesqlbench):

- `assistant_prompt` <- baseline/src/prompt.py, unchanged but for type annotations;
- `load_db_data` <- baseline/src/prompt_generator.py `load_db_data_if_needed` and the part of
  `generate_prompts` that fills a record: the same files and reads (column meanings' keys
  lower-cased, the knowledge base keyed by its `knowledge` name), for one database directory,
  returned instead of cached, and a missing file raises instead of being logged and replaced;
- `parse_sql`, `extract_sql_from_response` <- baseline/src/post_process.py, unchanged but for type
  annotations and the commented-out code left out.

`build_prompt` is this repo's: it gives `assistant_prompt` a record of the database, the question
and the three public asset files. Nothing else reaches the prompt: no `external_knowledge`,
`sol_sql`, `test_cases` or task file (spec U8; upstream's record carries no hidden field either).
"""

import json
import re
from pathlib import Path
from typing import Any


def assistant_prompt(json_data: dict[str, Any]) -> str:
    """Generate a prompt for the CoT SQL assistant.

    Args:
        json_data (dict): Dictionary containing:
            - query: The user's query
            - selected_database: Database name
            - schema: Database schema as text (from schema.txt)
            - column_meanings: Column meanings dictionary with case-insensitive keys
            - knowledge: External knowledge dictionary keyed by knowledge name
    """
    query = json_data["query"]
    db_name = json_data["selected_database"]  # noqa: F841 (upstream reads it and does not use it)

    # Get schema (already loaded as text)
    schema = json_data.get("schema", "Schema not available")

    # Get column meanings (already loaded as dict with case-insensitive keys)
    column_meanings = json_data.get("column_meanings", {})
    col_meanings_str = json.dumps(column_meanings, indent=2)

    # Get knowledge base (already loaded as dict)
    knowledge = json_data.get("knowledge", {})
    visible_kbs: list[dict[str, Any]] = []
    for k_info in knowledge.values():
        visible_fields = ["id", "knowledge", "description", "definition"]
        visible_kbs.append({k: k_info[k] for k in visible_fields if k in k_info})
    knowledge_str = json.dumps(visible_kbs, indent=2)

    # Build the full prompt using the template
    # (Upstream's template, verbatim. Its last line is a plain string so that its trailing space
    # survives the whitespace hooks.)
    return (
        f"""# Database Schema:
{schema}

# Column Meanings:
{col_meanings_str}

# External Knowledge:
{knowledge_str}

# User Task:
{query}

Generate the correct PostgreSQL to handle the user task above:
(FORMAT: You should enclose your final PostgreSQL in '```postgresql\n[Your Generated SQLs]\n```' in the end. Could use semicolon to separate multiple statements.)

"""  # noqa: E501
        + "# Your Generated SQL: \n```postgresql"
    )


def load_db_data(db_folder_path: Path, db_name: str) -> dict[str, Any]:
    """The schema, column meanings and knowledge of one database's asset directory."""
    # Load Schema
    schema = (db_folder_path / f"{db_name}_schema.txt").read_text()

    # Load Column Meanings
    meanings = json.loads((db_folder_path / f"{db_name}_column_meaning_base.json").read_text())
    # Case-insensitive keys
    column_meanings = {k.lower(): v for k, v in meanings.items()}

    # Load External Knowledge
    kb: dict[str, Any] = {}
    with (db_folder_path / f"{db_name}_kb.jsonl").open() as f:
        for line in f:
            knowledge = json.loads(line.strip())
            kb[knowledge["knowledge"]] = knowledge
    return {"schema": schema, "column_meanings": column_meanings, "knowledge": kb}


def build_prompt(db_folder_path: Path, db_name: str, query: str) -> str:
    """Upstream's prompt for `query` on the database whose assets are in `db_folder_path`."""
    record = {"query": query, "selected_database": db_name}
    return assistant_prompt(record | load_db_data(db_folder_path, db_name))


def parse_sql(response: str) -> str:
    """Extracts the SQL query from the CoT model's response."""
    if "`" in response:
        # First try to find SQL between triple backticks
        sql_match = re.search(r"```postgresql(.*?)```", response, re.DOTALL)
        if sql_match:
            return sql_match.group(1).strip()

        # Then try to find SQL between triple backticks
        sql_match = re.search(r"```sql(.*?)```", response, re.DOTALL)
        if sql_match:
            return sql_match.group(1).strip()

        # Then try to find SQL between ```  ```
        sql_match = re.search(r"```(.*?)```", response, re.DOTALL)
        if sql_match:
            return sql_match.group(1).strip()

        # Try to find without ```postgresql
        sql_match = re.search(r"(.*?)```", response, re.DOTALL)
        if sql_match:
            return sql_match.group(1).strip()

        # Then try to find SQL between single backticks
        sql_match = re.search(r"`(.*?)`", response, re.DOTALL)
        if sql_match:
            return sql_match.group(1).strip()
    else:
        # Finally, try to find a SELECT statement
        sql_match = re.search(r"(SELECT\s+.*?;)", response, re.DOTALL | re.IGNORECASE)
        if sql_match:
            return sql_match.group(1).strip()

    # If no SQL found, return empty string
    return response


def extract_sql_from_response(response_string: str) -> list[str]:
    """
    Extract all SQL code blocks wrapped with ```sql and ``` from the response string.
    Returns a list of SQL statements.
    """
    sql_statements = [parse_sql(response_string)]

    return sql_statements
