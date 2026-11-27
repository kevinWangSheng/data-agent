# 138 — LiveSQLBench：照官方 baseline 的单次调用对照（spec D2）

Status: ready-for-agent
Blocked by: —
Spec: ../spec.md（"Update 2026-11-27"一节，D2）
Claimed by: —
Branch: —

## Goal

能在服务器上用一条 `da-remote start <name> -- <evals 子命令>` 在开发集上跑"官方 baseline 的做法"：同一个模型 `deepseek-flash`，提示词里直接放 schema、列含义、知识库，单次调用写 SQL，交 `/app/pred.json`，由任务自己的验证脚本（136 已对拍）判分，得出每题 3 次的准确率和花费。用来回答"分数低是模型本身还是 agent 的做法"。

## Authority

- May edit：
  - 新建 `evals/` 下的模块（如 `evals/lsb_single_shot.py`）和 `evals/configs/` 下的新 job 配置；
  - `evals/__main__.py`：只加注册新子命令的几行；
  - `tests/`（除 `tests/acceptance/`）；
  - `NOTICE`：登记复制的上游代码；
  - `docs/setup.md`：写服务器上的运行命令。
- Must not edit：
  - `src/`、`skills/`；
  - `evals/livesqlbench.py`、`evals/livesqlbench_server.py`（工单 139 改）；
  - `evals/ablate.py`、`evals/report.py`、`evals/spend.py`、`evals/harbor_agent.py`、`evals/manifest.json`；
  - `tests/acceptance/`、`.claude/`、`CODING_STANDARDS.md`、`AGENTS.md`、其他工单的文件。
- Server access：工单 agent 没有（会话里不能 source 模型的 env 文件）。真实运行由主会话做。
- Spend cap：工单 agent $0。主会话的真实运行算在 spec D1–D4 合计 $15 里。

## Context

1. **上游做法**（`/Users/shenghuikevin/dev/AI/data-agent-refs/livesqlbench/`，根目录 `LICENSE` 是 MIT，"Copyright (c) 2024 bird_sql"）：
   - 提示词：`baseline/src/prompt.py` `assistant_prompt`，资料的读法：`baseline/src/prompt_generator.py:37-98`（列含义的键转小写；知识库每条只留 `id`、`knowledge`、`description`、`definition`，不给 `type`、`children_knowledge`）。
   - 调用参数：`baseline/src/call_api.py:112-116`（temperature 0、max_tokens 6000）。
   - 从回复里取 SQL：`baseline/src/post_process.py` `parse_sql` / `extract_sql_from_response`。
   - 照抄，不重写；复制的部分保留原版权与许可声明，登记 `NOTICE`（AGENTS.md MUST）。
2. **不给的东西**：`external_knowledge`、`sol_sql`、`test_cases` 都是标准答案（spec U8、`:27`），不进提示词。运行器不读答案文件，也不读任务的 `tests/`。
3. **模型**：和 agent 同一个模型与入口——`deepseek-flash`，`DATA_AGENT_BASE_URL`（默认 `https://api.deepseek.com/anthropic`，`src/data_agent/agent.py:106-107`、`:1243`）、`DATA_AGENT_*` 的密钥变量；`anthropic` SDK 已在依赖里（`pyproject.toml:8`）。价格表和峰谷计费照 `src/data_agent/agent.py:405` 与 `evals/spend.py`。
4. **判分**：任务的验证脚本（separate verifier，128/136）。推荐做法是写成一个 Harbor agent（不装进容器的 `BaseAgent`，在宿主上调模型；资料从 agent 环境的 `/app/db_assets/` 读，答案写到 `/app/pred.json`），用一个新的 job 配置跑生成好的任务，这样判分、`pred.json` 拷贝、trial 目录结构都和 agent 运行一样。若 Harbor 0.24 的接口让这条路走不通，换别的做法也行，但判分必须走任务自己的验证脚本，在 Comments 写明理由。
5. **花费与报告**：每个 trial 的 token 和花费要记下来，能和 agent 运行同口径汇总（标价、实扣、峰谷）。先读 `evals/spend.py`、`evals/report.py` 需要的事件文件格式：能写出它们认的最小事件文件就用现有的 `evals spend` / `evals report`；不能的话，新子命令自己出汇总（准确率、每题 3 次、标价与实扣），在 Comments 写明为什么不能复用。不改这两个模块。
6. **只跑开发集**：新子命令只接受 `--split dev`（或只跑清单 `dev` 里的 `lsb-` 题），给留出题直接拒绝（ADR-0004；spec 本轮不跑留出集）。输出放 `runs/<out>/`，不含标准答案（U7）。
7. **不开 `--network`**（AGENTS.md MUST）。
8. 服务器上的任务目录：`../da-benchmarks/livesqlbench/tasks`（`scripts/da-remote:534`）。

## Acceptance

- [ ] 单元测试（替身模型客户端，不联网、不连库）：
  - 提示词含该库的 schema、列含义、知识库，知识库每条只含 `id`、`knowledge`、`description`、`definition`；
  - 提示词里没有 `external_knowledge`、`sol_sql`、`test_cases` 的任何内容，也没有答案文件路径；
  - 调用参数是 temperature 0、max_tokens 6000、模型 `deepseek-flash`；
  - 回复里取出的 SQL 按官方格式写成 `{"instance_id", "predicted_sql": [...]}`；取不出时也写出文件（空列表或原样），由验证脚本判 0，不报基础设施错误；
  - 留出题被拒绝；
  - 每个 trial 的 token 与花费被记下。
- [ ] 复制的上游代码保留版权头，`NOTICE` 已登记。
- [ ] `docs/setup.md` 写明服务器上的命令（`da-remote start <name> -- …`，开发集 60 × 3）。
- [ ] 本机用 Harbor 加替身镜像或替身模型跑通一题（$0），贴命令与输出。
- [ ] `make check` 通过。

## Evidence required

`make check` 输出；本机 $0 跑通一题的命令和输出；给主会话的服务器运行顺序（push 后 `da-remote setup <commit>`、`check`、先 1 题、再 60 × 3）。

## Stop conditions

- 判分只能绕开任务的验证脚本才做得到。
- 复用花费汇总必须改 `evals/spend.py` 或 `evals/report.py`。
- 需要改 `src/` 或 139 的文件。
- 第二轮审查修复后仍不过。

## Failure handling

停下时：最终回复写明 `Status: ready-for-human`，Comment 写试了什么、哪里失败、分支名。

## Ledger

- Attempts: 0
- Review rounds: 0
- Model spend: $0
- Outcome: —
- Human review minutes: —

## Comments

- 2026-11-27（对齐会话建票）：人定 spec D1–D5（"Update 2026-11-27"）。本工单是 D2；和 139 互不依赖，可并行。
