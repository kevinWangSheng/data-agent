"""Code fences in the common formatting (data_agent.im.format), past what
tests/acceptance/test_im_format.py holds: a fence is read as CommonMark reads it (ticket 137), so a
line with code closed on it (```` ```SELECT 1``` ````) is no fence, a block opened with four
backticks is closed only by four or more, and a closing fence takes nothing after it. Not named
`test_im_format.py`: that basename is the acceptance file's, and pytest's default import mode
takes one module per basename."""

from data_agent.im.format import fence_after, split_message, tables_to_code

ONE_LINE_CODE = "```SELECT 1```"


def test_a_line_with_its_code_closed_on_it_is_no_fence() -> None:
    assert fence_after(ONE_LINE_CODE, "") is None
    assert fence_after("```sql", "") == "```"
    assert fence_after("````", "") == "````"
    # Inside a block: only a run as long as the opening, with nothing after it, closes it.
    assert fence_after("```", "````") is None
    assert fence_after("`````", "````") == ""
    assert fence_after("```` done", "````") is None
    assert fence_after(ONE_LINE_CODE, "```") is None


def test_a_table_after_one_line_code_becomes_a_code_block() -> None:
    text = f"Query:\n{ONE_LINE_CODE}\n\n| a | b |\n|---|---|\n| 1 | 2 |"
    assert tables_to_code(text) == f"Query:\n{ONE_LINE_CODE}\n\n```\na | b\n--+--\n1 | 2\n```"


def test_one_line_code_is_not_repeated_in_each_part_of_a_split_answer() -> None:
    paragraphs = [f"paragraph {number} " + "x" * 40 for number in range(6)]
    text = "\n\n".join([ONE_LINE_CODE, *paragraphs])
    parts = split_message(text, 120)
    assert "\n\n".join(parts) == text
    assert sum(part.count(ONE_LINE_CODE) for part in parts) == 1
    assert not any(part.endswith("```") for part in parts)


def test_a_four_backtick_block_split_in_two_is_fenced_in_each_part() -> None:
    code = [f"line {number} " + "y" * 30 for number in range(8)]
    # A run of three inside it is content, not its end.
    text = "\n".join(["````sql", code[0], "```", *code[1:], "````"])
    parts = split_message(text, 150)
    assert len(parts) > 1
    for part in parts:
        lines = part.split("\n")
        assert lines[0] == "````sql"
        assert lines[-1] == "````"
        assert len(part) <= 150
    body = [line for part in parts for line in part.split("\n")[1:-1]]
    assert body == [code[0], "```", *code[1:]]


def test_text_after_a_closing_run_keeps_the_block_open() -> None:
    """```` ``` and more ```` closes nothing (CommonMark): the table after it is code still."""
    text = "```\nselect\n``` and more\n| a | b |\n|---|---|\n| 1 | 2 |\n```"
    assert tables_to_code(text) == text
