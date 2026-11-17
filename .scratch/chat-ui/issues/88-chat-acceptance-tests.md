# 88 — 对话界面：验收测试（真模型端到端、浏览器、三组不调模型的）

Status: ready-for-agent
Blocked by: —
Spec: ../spec.md
Claimed by: —
Branch: —

## Goal

spec "Testing Decisions" 列的验收测试全部写好：真模型端到端 a–d、f、g、一条 Playwright 浏览器主流程、对话视图模型、后端对话接口、评测配置不变。还没实现的行为标预期失败（`xfail(strict=True)` / `test.fails`）；评测配置不变一组现在就通过。对话的契约（Context 第 1–6 项）写在代码里，函数体和接口实现留空。本工单由人审过才合并，之后的工单不能改这些测试。

## Authority

- May edit:
  - `tests/acceptance/test_chat_e2e.py`、`tests/acceptance/test_chat_api.py`、`tests/acceptance/test_chat_eval_unchanged.py`（新建）；
  - `tests/fixtures/chat/`（新建）：端到端用的两个小 DuckDB 夹具库（如订单库和客户库：订单库带一个会改变结果的歧义，如收入含不含退款；场景 g 要两个库的数据才能答对）和它们的数据源配置文件、手写事件夹具、对话索引夹具、假的单轮子进程；
  - `web/src/chat-view/`（新建）：视图模型的类型、函数空壳（抛出未实现）、vitest 验收测试；
  - `web/e2e/`、`web/playwright.config.ts`（新建）：Playwright 主流程；
  - `web/package.json`、`web/pnpm-lock.yaml`：只加 `@playwright/test`；
  - `Makefile`：只为让 `make test-integration` 也跑 Playwright 主流程、加装浏览器的目标；`make check` 和 CI 不跑真模型；
  - `src/data_agent/web/app.py`：只给 `create_app` 加带默认值的参数（签名），不加行为。
- Must not edit: `src/data_agent/` 的其他文件和 `evals/` 的实现代码、已有的 `tests/acceptance/` 文件、`tests/fixtures/` 里已有的文件（已合入的测试在用）、`.github/workflows/`、其他工单的文件、`.claude/`、`CODING_STANDARDS.md`
- Server access: none
- Spend cap: $0（工单 agent 加载不了模型环境；真模型测试由主会话跑，见 Context）

## Context

- spec：`.scratch/chat-ui/spec.md`。"Testing Decisions" 逐条列了每组要断言的行为和判定规则，照它写；"决定"第 3、8、9、14–20 条是行为来源（2026-11-17 晚改：对话只做查数，不选类型和数据源，agent 自己选库；dbt 不进对话，原来的场景 e 和改动文件、diff、补丁相关内容都删了）；"写工单前先验证"记了 resume 和插话的探针结果。
- 先例：
  - 真模型验收：`tests/acceptance/test_seam_a_run_cli.py`（`pytestmark = pytest.mark.integration`，夹具 `tests/fixtures/shop.sql`）。
  - 不调模型的验收：工单 73 `.scratch/web-ui/issues/73-web-acceptance-tests.md` 和它合入的 `web/src/run-view/acceptance/run-view.test.ts`、`tests/acceptance/test_web_api.py`、`tests/fixtures/web/fake_run.py`；`src/data_agent/web/app.py:53` `create_app`、`src/data_agent/web/launcher.py:40` `StartRun`。
- 本工单要定下、并在最终回复里列出供人审的契约：
  1. 对话目录和索引文件的格式（spec 第 17 条：一段对话一个目录，一条用户消息一次运行、一个事件文件）。
  2. 新事件和字段：澄清事件（问题加 2–5 个选项，nao `shared/src/tools/clarification.ts:3-24`）、插话送达事件、每个事件文件记下所属对话和第几轮（第 17 条）、每条查询记下查的是哪个库（第 20 条）。旧文件没有这些字段。
  3. 对话视图模型的 TypeScript 类型和函数签名：输入索引加各轮事件（可以只是前缀），输出对话视图；每轮的卡片复用 `web/src/run-view/model.ts` 的运行视图模型，不重写。
  4. 后端接口：列出对话（单次运行显示成一轮、不能追问）、新建对话（不带类型和数据源）、发消息（空闲时开新一轮，运行中送 stdin）、取消。
  5. `data-agent run` 新增的参数：数据源清单（沿用 `--data-sources` 的 TOML，第 20 条）；第二轮起传上一轮的 session id 和对话目录（第 14、16 条）；一个对话模式开关，打开时才有澄清工具和 stdin 插话，默认关，评测和单次运行走默认（第 19 条）。session id 从上一轮事件文件里读（现在写在 `src/data_agent/agent.py:907`）。
  6. stdin 消息的字段（spec 第 9 条的通道：每行一个 JSON，子进程转成 `client.query()`；SP 带 uuid 对账，`notebook-server/signalpilot/_server/ai/claude_agent.py:355-408`）。
- 真模型端到端（a–d、f、g）：
  - 起真的 `data-agent web`（本机地址、独立端口、独立运行目录），全程经 HTTP 接口，和 Playwright 主流程用同一组契约。
  - 每个场景跑 3 次（参数化），3 次都过才算过。后端配两个夹具库，a–d、g 都不指定库。
  - 答案数值和正确口径在夹具里写死，比较带容差，照 seam A 的写法。夹具库和问题自己写，不用榜单题目、不碰留出集（ADR-0004、ADR-0005）。
  - a–d、g 标 `xfail(strict=True)`。f 走现有的 `data-agent run`，2026-11-17 主会话已用样例真跑 3 次全过，不标预期失败（作守门）；本工单写成正式测试后，主会话合并前再真跑一次确认。
- 预期失败按转绿的工单分标记（照工单 73 的 `TICKET_NN = pytest.mark.xfail(...)` 写法），转绿的工单只删自己那个标记：
  - `TICKET_93`：后端接口一组、真模型端到端 a–d、g（93 是对话后端，依赖 89–92 都合入，所以端到端在它合并时一起转绿）；
  - `TICKET_94`：对话视图模型一组（vitest `test.fails`）；
  - `TICKET_95`：Playwright 主流程。
- 假子进程：另写在 `tests/fixtures/chat/`，不改 `tests/fixtures/web/fake_run.py`。它要能按脚本写事件、读 stdin 并写送达事件、把收到的参数（session id、对话目录、数据源清单）记下来供断言、按门文件结束；测试用自己的 `start_run` 以 stdin 管道启动它。
- 评测配置不变：DAB 直接调 `build_options`（`evals/dab.py:417`），Harbor 走 `data-agent run`（`evals/harbor_agent.py`）。断言这些入口给出的选项里没有澄清工具、`resume` 为空、`env` 里没有 `CLAUDE_CONFIG_DIR`，工具、`allowed_tools`、`disallowed_tools`、权限设置，以及 MCP 服务列出的工具清单和每个工具的参数（第 20 条：单库时不出现库名参数），都和本工单开始时的 main 一致（查数和 dbt 各一份，按评测用的配置）。不调模型，现在就通过。
- 只用 pnpm、uv。

## Acceptance

- [ ] spec "Testing Decisions" 每一组列出的每条行为都有断言
- [ ] 不调模型的视图模型、后端接口两组当前都以预期失败的方式通过检查，没有被跳过的；评测配置不变一组当前通过
- [ ] 每个预期失败的测试，失败原因都是功能还没做（如对话接口返回 404/405、视图模型抛出未实现），不是夹具或服务没起来：起服务的夹具先断言进程活着、接口可达；最终回复贴每组一个用 `--runxfail` 跑出的真实失败原因（2026-11-17 样例里，xfail 曾把"运行目录不存在、服务没起来"藏成预期失败）
- [ ] 不调模型的三组都临时写参考实现（不提交）验证过：去掉预期失败标记后能通过；每组至少一个反向检查会失败（如第二轮子进程没拿到上一轮的 session id、评测入口多了澄清工具、单库时工具多了库名参数）
- [ ] 真模型端到端和 Playwright 主流程：`uv run pytest tests/acceptance/test_chat_e2e.py --collect-only` 和 `pnpm --dir web exec playwright test --list` 列出全部用例；`make check` 不跑它们
- [ ] 夹具里没有密钥、地址、主机名（`python -m evals scrub` 输出 0 处改动）
- [ ] Context 第 1–6 项契约都写在代码里，并在最终回复里逐项列出，供人审
- [ ] `make check` 通过

## Evidence required

`make check` 的完整输出；`pnpm --dir web test` 的输出（每个对话验收测试的名字和预期失败）；`uv run pytest tests/acceptance/test_chat_api.py tests/acceptance/test_chat_eval_unchanged.py -v` 的输出；端到端和 Playwright 的用例清单；参考实现和反向检查的结果；脱敏命令的输出；最终回复里的契约清单，以及交给主会话的真跑命令（f 场景和整套 `make test-integration`）。每个场景的夹具答案附一条算出它的 SQL，供人核对。

## Stop conditions

- spec 的某条行为没法在约定的接缝上测，或者要改 Authority 以外的实现代码才能测。
- 评测配置不变一组写不出来，或者写出来现在就不通过。
- spec 自相矛盾，或者缺一个需要人定的决定（契约的具体字段名不算，由本工单定、人审）。
- 第二轮审查修复后仍不过。

## Failure handling

停下时：最终回复写明 `Status: ready-for-human`，Comment 写试了什么、哪里失败、分支名。

## Ledger

- Attempts: 0
- Review rounds: 0
- Model spend: $0 (data-agent model calls only, read from run event files; Claude Code / Codex subscription usage is not recorded)
- Outcome: — (accepted | returned)
- Human review minutes: — (filled by the human at merge, optional)

## Comments
