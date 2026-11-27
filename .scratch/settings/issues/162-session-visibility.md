# 162 — 会话查看权限：运行详情页和运行列表按 S8 检查

Status: ready-for-agent
Blocked by: 158, 159, 161
Spec: ../spec.md（Implementation Decisions"会话查看（S8）"）
Claimed by: —
Branch: —

## Goal

运行详情页（`GET /api/runs/{run_id}/events`，JSON 和 SSE 两种）按身份检查：
- 频道会话：该频道成员可看（Slack `conversations.members`，经 159 的缓存）；
- 私聊会话：本人可看；
- 组织管理员都能看；
- Telegram、Discord 的会话先只有组织管理员能看；
- 网页里发起的运行（表单、网页对话）：所有者 = 登录的人，本人和组织管理员可看（定的细节第 5 条）。

运行列表（`GET /api/runs`、`GET /api/conversations`）用同一条规则，只列出能看的（定的细节第 4 条）。

网页只看不接着聊：IM 会话在网页上不能发消息（接着问回到线程）；网页自己的对话页照旧。链接不带权限，打开要登录并按上面检查。local 模式（只监听本机）不登录，照旧都能看。

157 里标 `TICKET_162` 的测试全部转绿。

## Authority

- May edit：
  - `src/data_agent/web/app.py`：`read_events`、`list_runs`、`list_conversations`、`conversation_row`（及契约要求的其他运行接口）的查看检查；IM 会话的网页对话拒绝发消息。`create_app` 只往后加带默认值的参数。
  - `src/data_agent/web/runs.py`、`web/conversations.py`、`web/launcher.py`：读会话索引（运行 → 所属频道或本人）；网页发起的运行起跑时把所有者（登录的人）记进会话索引。
  - 158 的模块：只加会话索引的查询函数和"网页运行的所有者"的写入（若 158、161 没提供），不改表结构以外的东西；要改表结构就停下。
  - `tests/acceptance/`：唯一例外，删掉 157 文件里的 `TICKET_162` 标记，以及因此没用了的 import 或定义（AGENTS.md 硬规矩）。
  - `tests/`（除 `tests/acceptance/`）：单元测试。
- Must not edit：
  - `tests/acceptance/` 里除 `TICKET_162` 标记以外的一切；`tests/fixtures/`。
  - 159 的身份与权限模块（只调用）；`src/data_agent/im/`（161）；`web/src/`（160；前端被拒时的显示若要改，记进最终回复，另开工单）。
  - `pyproject.toml`、`evals/`、`skills/`、`docs/`、`Dockerfile`、`deploy/`。
  - `Makefile`、`.github/workflows/`、其他工单的文件、`.claude/`、`CODING_STANDARDS.md`、`AGENTS.md`。
- Server access: none
- Spend cap: $0。不调模型、不连真的 Slack；真实查看由主会话在部署后做。

## Context

行号取自 main `30a18e9`。140（in-review）、153、158、159 会移动这些行，认领时按符号名在当前 main 上核对。

- **决定**：spec"会话查看（S8）"；ADR-0012 决定 6；`decisions.md` S8 原文；`decisions.md`"建 settings 工单时主会话定的细节"第 3、4、5 条；"后续"清单（Telegram、Discord 的会话按平台成员身份在网页上看，排在后面）。
- **验收测试**：157 的 `test_settings_sessions.py` 里标 `TICKET_162` 的；契约 9（被拒时 403 还是 404）见 157 的测试 docstring 和人审结论。
- **现状**：
  - `read_events`（`src/data_agent/web/app.py:120-138`）：运行不存在时 404，JSON 或 SSE（`stream`，`app.py:227`）。检查放在读事件之前，SSE 的检查在开流之前做一次。
  - `GET /api/runs`（`app.py:116-118`，`RunsDir.summaries`，`web/runs.py:108-120`）、`GET /api/conversations`（`app.py:164-167`）和 `GET /api/conversations/{conversation}`（`app.py:169-174`）今天列出全部；改成按同一条规则过滤（定的细节第 4 条）。
  - 运行属于哪个频道或哪个人：158 建的会话索引，IM 回合由 161 写（线程 → 运行 id、所属频道或本人；定的细节第 3 条）；网页发起的运行由本工单在起跑时记所有者。IM 线程映射 `<runs_dir>/im/threads.json`（`im/adapter.py:96-97`）照旧，不用它做检查。
  - 网页对话的发消息接口 `POST /api/conversations/{conversation}/messages`（`app.py:181-190`）。IM 会话在网页上是否以对话出现、怎么拒绝，照契约。
- **身份**：159 的会话 Cookie 与权限函数（频道成员查询带 5 分钟缓存）；不另写成员查询。
- 只用 uv。

## Acceptance

- [ ] 157 里标 `TICKET_162` 的测试去掉标记后通过；`git diff main -- tests/acceptance tests/fixtures` 只有这些删除行
- [ ] local 模式不变：已有的 `tests/acceptance/test_web_api.py`、`test_chat_api.py`、`test_im_*.py` 不改就通过
- [ ] 单元测试：索引里查不到的运行（命令行、旧运行）的处理；SSE 在开流前检查；被拒的响应里没有运行内容；网页运行的所有者被记下
- [ ] `make check` 通过

## Evidence required

贴到 Comments：
- `make check` 输出。
- `uv run pytest tests/acceptance/test_settings_sessions.py -v`。

交给主会话（部署后，最终回复写出步骤）：
1. 在 Slack 频道问一题，点"在网页打开"，频道成员登录后看到运行详情。
2. 用不在该频道的账号打开同一链接，被拒。
3. 组织管理员打开一个私聊会话的运行，看到详情。

## Stop conditions

- 157 的某个验收测试看起来不对，或要改它才能转绿。
- 会话索引没有记所属频道或本人，要改 158 的表结构，或 161 没写索引。
- 159 的权限函数缺"能不能看这个频道"的查询，要改 159 的代码。
- 要改 `web/src/` 才能让验收测试转绿。
- 第二轮审查修复后仍不过。
- 不停止、不重启本机的 Docker/colima 守护进程。

## Failure handling

停下时：最终回复写明 `Status: ready-for-human`，Comment 写试了什么、哪里失败、分支名。

## Ledger

- Attempts: 0
- Review rounds: 0
- Model spend: $0 (data-agent model calls only, read from run event files; Claude Code / Codex subscription usage is not recorded)
- Outcome: — (accepted | returned)
- Human review minutes: — (filled by the human at merge, optional)

## Comments

- 2026-11-27（主会话建票）：会话查看权限。工单表写 Blocked by 159；会话索引在 158，所属频道由 161 写进索引，二者都不在 Blocked by 里，建票时列为待人确认的问题。改动在后端，照常跑 `da-review` 和 `eng:code-review`。
- 2026-11-27（主会话）：按"建 settings 工单时主会话定的细节"第 3、4、5 条改票：Blocked by 改为 158、159、161；运行列表用同一条可见性规则；网页发起的运行所有者 = 登录的人（本工单在起跑时记进索引）；IM 回合的索引由 161 写。
