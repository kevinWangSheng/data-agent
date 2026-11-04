# 44 — 验证层 v2 的验收测试与夹具

Status: in-review
Blocked by: —
Spec: ../spec.md
Claimed by: claude (worktree 44-acceptance-tests)
Branch: worktree-44-acceptance-tests

## Goal

把 spec「Testing Decisions」里的每一条写成验收测试，放在 `tests/acceptance/test_verifier_v2.py`，夹具放在 `tests/fixtures/verifier_v2/`。测试里定下各个接缝的接口：模块名、函数签名、返回结构。全部标 `xfail(strict=True)`，`make check` 保持绿。人审后合入，之后 45–47 都不能改这些测试。

## 已和用户定下的（2026-11-04）

- 测试内容以 spec「Testing Decisions」为准，一条不少；不加 spec 里没有的行为。
- 接口由这张工单提出，人审时确认。约定如下：
  - 快照、4 个工具、报告解析、两个钩子，各自有一个**纯函数**入口，返回结构化结果（dataclass 或 dict）；MCP 和钩子只是很薄的一层包装。测试直接测纯函数，钩子测试用构造好的 hook 输入，不调模型。
  - 新代码放在 `src/data_agent/verifier_v2/` 包里（模块拆分由本工单提出）。测试只从这个包 import。
- spec 2026-11-04 修订后新增的测试，必须包含：
  - Stop 钩子按模型分别记录状态：先验证 A、B，B 报 FAIL；之后只重新验证 A 并通过，B 仍然拦截；
  - 4 个开着验证器的配置（full、no-skill、no-context、full-old-skill）都只注册一个 `verifier`；
  - `compare_by_key` 的相等规则：DECIMAL、浮点、其他类型、NULL，各一个测试；
  - 集成测试：`updatedInput` 整体替换 `Agent` 的 `prompt`。这条标 `integration`，不进 `make check`，测试体照 `docs/research/2026-11-04-agent-updatedinput-spike.md` 写。
- 夹具：
  - 一个小 dbt 项目，带"改动前"和"改动后"两个版本，覆盖 spec 列出的全部改动类型；
  - 一个小 DuckDB 文件，包含粒度键有 NULL、有重复、窗口排序不唯一的数据；
  - 一份 subagent transcript 样例，用来测报告解析和轮数统计。可以参考现有的 `tests/fixtures/subagent_transcript.jsonl`。

## Authority

- May edit: `tests/acceptance/test_verifier_v2.py`（新建）、`tests/fixtures/verifier_v2/`（新建）、`src/data_agent/verifier_v2/__init__.py`（只放让 import 成功所需的空桩，抛 `NotImplementedError`）
- Must not edit: 其他 `tests/acceptance/` 文件、`src/` 下其余文件、`skills/`、其他工单的文件、`.claude/`、`CODING_STANDARDS.md`
- Server access: none
- Spend cap: none

## Context

- spec：`.scratch/verifier-v2/spec.md`（Implementation Decisions、Testing Decisions）；ADR-0009。
- 现有可复用的东西：报告解析的正则 `CHECK_RE`、`MODEL_RE`（`src/data_agent/verifiers.py`）；workspace 守卫（`src/data_agent/workspace_guard.py`），测试"agent 用 Read 读快照会被拒绝"时用它。
- 派单改写的实测结论：`updatedInput` 是整体替换，必须带上 `subagent_type` 等其他字段（2026-11-04 本地实测，见 spec「派单」）。
- 工单 01 的写法可以参考：`.scratch/phase1/issues/01-acceptance-tests.md`。

## Acceptance

- [ ] spec「Testing Decisions」的每一条都有对应的测试函数；在 Comments 里贴一张"spec 条目 → 测试函数名"对照表。
- [ ] 全部测试标 `xfail(strict=True)`，并且确实因为未实现（`NotImplementedError`）而失败，不是因为夹具错误。
- [ ] 夹具很小：dbt 项目少于 10 个模型，DuckDB 文件小于 1 MB。
- [ ] `make check` 通过。

## Evidence required

`uv run pytest tests/acceptance/test_verifier_v2.py -q` 的输出，显示全部 xfailed；对照表。

## Stop conditions

- spec 的某条测试写不出来，或者两条互相矛盾：停下，写明是哪一条。
- 要测某条行为就必须调模型。

## Failure handling

Status 改为 `ready-for-human`，Comment 写明试了什么、哪里失败、分支名。

## Ledger

- Attempts: 1
- Review rounds: 2（Codex `da-review` 1 轮 + `eng:code-review` 1 轮，均已修完）
- Model spend: $0（本工单不调 data agent 模型）
- Outcome: in-review，branch `worktree-44-acceptance-tests`（commits 3263852、9351938、5f84005）；待人审 PR
- Human review minutes: —

## Comments

### 2026-11-04 工单会话（worktree 44-acceptance-tests）：实现完成，交人审

**提交**：3263852 初版；9351938 修 Codex 审查；5f84005 修 eng:code-review。

**产出**
- `tests/acceptance/test_verifier_v2.py`：40 个默认用例 + 1 个 `integration` 用例，全部 `xfail(strict=True)`。接口（模块名、签名、返回结构）写在模块 docstring 里。
- `src/data_agent/verifier_v2/__init__.py`：12 个空桩，全部 `raise NotImplementedError`；docstring 提议模块拆分（snapshot / model_diff / contract / grain / compare / report / dispatch / stop / hooks）。
- `tests/fixtures/verifier_v2/`：`project_before/`、`project_after/`（9 个模型，覆盖 spec 列的全部改动类型）、`verifier_v2.sql`（建 DuckDB）、`verifier_transcript.jsonl`（3 轮、4 次工具调用，末尾是 v2 格式报告）、`README.md`。

**真实运行证据**

```
uv run pytest tests/acceptance/test_verifier_v2.py -q
xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx   [100%]
1 deselected, 40 xfailed in 3.68s

make check（最终提交 5f84005）
All checks passed! / 186 files already formatted / 0 errors, 0 warnings, 0 notes
1445 passed, 1 skipped, 37 deselected, 40 xfailed in 313.49s
```

`--runxfail` 核实：37 个用例因 `NotImplementedError` 失败（xfail 带 `raises=NotImplementedError`，夹具错误会直接报红）；3 个配置用例因断言失败（见下"需要人看的决定" 1）；集成用例在读取凭据之前就命中桩，`NotImplementedError`。

**spec 条目 → 测试函数**

| spec「Testing Decisions」 | 测试函数 |
|---|---|
| 快照不含 dbt_packages/target、在 workspace 外、Read 被守卫拒绝 | `test_snapshot_keeps_logic_files_outside_the_workspace` |
| 删掉原有过滤条件报为"删除" | `test_model_diff_reports_a_removed_filter` |
| 换了数据源 | `test_model_diff_reports_a_swapped_source` |
| UNION → UNION ALL | `test_model_diff_reports_union_to_union_all` |
| 加了 LIMIT | `test_model_diff_reports_an_added_limit` |
| dbt_project.yml 物化方式改动 | `test_model_diff_reports_a_materialization_change` |
| 含 `{% if %}` 退回文本 diff、标未解析 | `test_model_diff_falls_back_to_a_text_diff_for_jinja_control_flow` |
| 没动过的模型和逻辑不出现 | `test_model_diff_leaves_out_untouched_models`（+ removed_filter 里断言保留的谓词不出现） |
| grain：键有 NULL 判 FAIL | `test_grain_facts_counts_null_keys` |
| grain：重复但原有 YML 定义更细粒度，可豁免 | `test_grain_facts_exempts_duplicates_the_snapshot_yml_explains` |
| grain：豁免只在 agent 新写的 YML，判 FAIL | `test_grain_facts_ignores_an_exemption_only_the_agent_wrote` |
| contract：有原有 YML 按它检查 | `test_contract_check_uses_the_snapshot_yml` |
| contract：YML 是 agent 新建的只按题面 | `test_contract_check_uses_only_the_task_for_an_agent_written_yml` |
| contract：没改动的窗口函数记 N/A | `test_contract_check_marks_an_unchanged_window_na` |
| contract：改动过、排序不唯一被检出 | `test_contract_check_finds_a_changed_window_with_non_unique_ordering` |
| compare：引用被测模型 / 其他改动模型被拒 | `test_compare_by_key_rejects_a_reference_that_reads_the_model`、`..._reads_a_changed_model` |
| compare：全键比对报出单边键和不一致键 | `test_compare_by_key_reports_one_sided_and_mismatched_keys` |
| 相等规则：DECIMAL / 浮点 / 其他类型 / NULL | `test_compare_by_key_rounds_the_reference_to_the_decimal_scale`、`..._allows_1e6_relative_error_for_floats`、`..._needs_exact_equality_for_other_types`、`..._treats_two_nulls_as_equal_and_one_as_a_mismatch` |
| 报告解析：四种状态 | `test_report_parser_recognizes_the_four_statuses` |
| 报告解析：解析不出来当 NEEDS_INFO | `test_report_without_check_lines_is_needs_info` |
| 报告解析：撞 maxTurns 当 NEEDS_INFO | `test_report_cut_off_by_max_turns_is_needs_info` |
| 轮数统计（工单夹具要求） | `test_transcript_summary_counts_turns_and_tool_calls` |
| 派单：第一次只有模板三项 | `test_first_dispatch_prompt_is_only_the_template` |
| 派单：再次派单附加内容带固定标题 | `test_redispatch_appends_the_main_agents_text_under_the_fixed_heading` |
| 派单：保留 subagent_type | `test_rewritten_input_keeps_the_other_fields` |
| 派单：非验证器不改写 | `test_other_subagents_and_tools_are_not_rewritten` |
| Stop：改过但从没验证过 | `test_stop_blocks_a_changed_model_never_verified` |
| Stop：报告之后又改过 | `test_stop_blocks_a_model_changed_after_its_report` |
| Stop：报告里有 FAIL | `test_stop_blocks_on_a_fail` |
| Stop：按模型分别记状态（A、B；B FAIL；只重验 A） | `test_stop_keeps_state_per_model` |
| Stop：只有 NEEDS_INFO 不拦 | `test_stop_does_not_block_on_needs_info` |
| Stop：拦满 2 次放行并记 `ended_with_unresolved` | `test_stop_lets_the_run_end_after_two_blocks_and_records_it` |
| Stop：baseline / no-verifiers 不挂 | `test_hooks_follow_the_verifiers_switch` |
| 配置：off 配置看不到新工具 | `test_new_tools_follow_the_verifiers_switch` |
| 配置：off 配置不做快照 | `test_snapshot_only_in_verifier_configs` |
| 配置：4 个 on 配置只注册一个 `verifier` | `test_verifier_configs_define_one_verifier` |
| 集成：`updatedInput` 整体替换 `Agent` 的 prompt | `test_updated_input_replaces_the_prompt_end_to_end`（`integration`，照 spike 写） |

**需要人看的决定**
1. **3 个配置用例的 xfail 不带 `raises=NotImplementedError`**（新工具按开关注册、快照只在 on 配置、只有一个验证器）。它们今天因断言失败（`verify_model_values` 仍注册、没调快照、agents 是 `{verifier, value-verifier}`），不是夹具错误，但不是字面上的 `NotImplementedError`。原因：这三条测的是 `agent.py` / MCP server 的接线，桩在包外没法让它们抛 NotImplementedError。
2. **DuckDB 没提交二进制**：`verifier_v2.sql` 用 duckdb 1.2.2 建出来 1.8 MB，超过 1 MB。与工单 01 一样改为每个测试在 `tmp_path` 现建，也满足"每个 worktree 用自己的 DuckDB 文件"。
3. **两条 Testing Decisions 之外的断言**：`verify_model_values` 在 on 配置里不出现（Codex 审查要求，spec「工具」一节有）；`AgentDefinition.maxTurns == 50`（工单 47 的验收明确写了"44 里 … maxTurns 为 50"）。若不要，删掉这两行即可。
4. **列契约对多出来的列**：`customer_ltv` 多出 `bonus_col`，测试定为 PASS（只查题面列是否齐全，多出的列进 `extra` 不判 FAIL）。spec 没写明，请确认。
5. **接线约定**：`agent.run` 通过 `verifier_v2.take_snapshot`（包属性）调快照，测试才能替换它；Stop 钩子的挂载通过 `verifier_v2.run_hooks(config, ...)` 按 `verifiers` 开关返回空或三个钩子。
6. **给工单 45 的提醒**：现有 `tests/acceptance/test_seam_b_mcp_server.py` 的 `test_verifier_tools_follow_config` 断言 on 配置含 `verify_model_values`（`seam_support.VERIFIER_TOOLS`）。45 拿掉该工具时这条会红，而 45 的 Authority 不允许改 `tests/acceptance/`。需要主会话决定：扩 45 的 Authority 一行，或由主会话改 `seam_support.VERIFIER_TOOLS`。

**审查结果与处理**
- Codex `da-review main`（4 条，都修了，9351938）：断言 `parse_report` 的 `overall`；在已有 contract 用例里加 `exists == PASS`；快照用例加入 macros/snapshots/tests/seeds 各一个文件；断言 `verify_model_values` 不再出现。没加"模型不存在"用例和 5 MB seed 用例：都不在 Testing Decisions 里。
- `eng:code-review main`：
  - 修了（5f84005）：SubagentStop 用例没证明报告进了 Stop 状态 → 模型改名 `alpha`/`bravo`，断言拦截理由只提 bravo（规则 3）且 `verifier_end` 带 `checks`；`Emit` 重复定义 → 改从 `verifiers` 导入；`max_blocks` 参数没人用 → 删掉；`rules`/`text` 命名 → `blocking_rules`/`change_text`；`rewrite_dispatch` 重复调用 → 帮助函数加 `tool_name`。
  - 不修：`(workspace, snapshot)` 参数成对出现（打包成类型就得在 `__init__.py` 放非桩代码，工单不允许）；返回值用 dict 和字符串状态（工单允许 dict）；`model_diff` 的 `dialect` 参数（spec 说解析方言跟随网关，已在 docstring 说明）；文件 860 行（一条 spec 一个测试，按节分段）。

### 2026-11-04 主会话

- PR #40 已开，CI 和 Codex app 意见待看。**按 plan 第 86 行，这个 PR 由人审验收测试后才合入**，主会话不合。
- 上面"需要人看的决定"第 6 条（`seam_support.VERIFIER_TOOLS` 断言 on 配置含 `verify_model_values`，45 拿掉该工具时会红）要在派 45 前定：扩 45 的 Authority 一行，或主会话改 `seam_support`。
