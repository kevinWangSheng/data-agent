"""The final-answer rule of query tasks (ticket 68): the product query prompt and the DAB task
prompt end with the same rule, and the rule is generic (ADR-0005)."""

import re

from data_agent import agent
from evals import dab


def final_paragraph(prompt: str) -> str:
    """The prompt's last paragraph, whitespace-normalized: where the answer rule sits."""
    return " ".join(prompt.strip().split("\n\n")[-1].split())


def test_both_query_prompts_end_with_the_same_answer_rule() -> None:
    product, benchmark = final_paragraph(agent.QUERY_PROMPT), final_paragraph(dab.TASK_PROMPT)
    assert product == benchmark
    rule = product.lower()
    assert "answer first" in rule
    assert "only what the task asks" in rule  # 1. answer only what is asked
    assert "right after its name" in rule  # 2. each name is followed by its value
    assert "exactly as they appear in the data" in rule  # 3. names verbatim
    assert "ellipsis" in rule
    assert "after the answer" in rule and "short sentences" in rule  # 4. assumptions, briefly
    assert "no tables" in rule and "alternative" in rule


def test_the_answer_rule_names_no_benchmark() -> None:
    rule = final_paragraph(agent.QUERY_PROMPT)
    for word in ("DAB", "DataAgentBench", "benchmark", "validate", "grader", "ground truth"):
        assert not re.search(re.escape(word), rule, re.IGNORECASE), word


def test_the_dbt_prompt_keeps_its_own_ending() -> None:
    assert agent.ANSWER_RULE not in agent.DBT_PROMPT
    assert "answer first" not in agent.DBT_PROMPT
