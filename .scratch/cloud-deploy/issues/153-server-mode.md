# 153 — `[server].mode`：令牌、监听地址、启动检查、联网、锁定、`/healthz`

Status: ready-for-agent
Blocked by: product-config 144, product-config 146, 151
Spec: ../spec.md（Implementation Decisions（Q7））；决定：`.scratch/product-config/decisions.md` Q8
Claimed by: —
Branch: —

## Goal

照 spec Q7 和 `decisions.md` Q8：
- 部署模式：`[server].mode = "local" | "cloud"`，也可由环境变量 `DATA_AGENT_MODE` 给。
- 网页的 bearer 令牌 `DATA_AGENT_WEB_TOKEN`：任何模式下，没令牌都拒绝监听非本机地址。
- cloud 下沙箱后端为 `local` 时拒绝启动。
- 联网：cloud 默认关，配置文件可打开；单次运行不能超过配置，超过就拒绝（409，`run` 退出 2）。
- cloud 下 `dbt_target` 锁定为 `scratch`；和命令行参数冲突时报错拒绝。
- 运行子进程的环境去掉 `DATA_AGENT_WEB_TOKEN`。
- 两种模式都有 `GET /healthz`，返回 `{"ready": true}`。

`local`（默认）其余行为不变。151 里标 `TICKET_153` 的测试全部转绿。Cookie 换取、前端输令牌页面、表单联网默认值是 156 的，不在本工单。

## Authority

- May edit：
  - `src/data_agent/cli.py`：`web` 的启动检查、令牌、运行子进程的环境；`run` 的锁定检查。
  - `src/data_agent/web/app.py`：bearer 鉴权、`/healthz`。
  - `src/data_agent/web/launcher.py`、`src/data_agent/web/conversations.py`：联网的上限与 409。
  - `src/data_agent/im/`：IM 的回合跟随服务器的联网设置。
  - 143/144 合入的配置模块：`[server].mode`、`DATA_AGENT_MODE`、cloud 的默认值。以 143 实际建的文件为准。
  - `tests/`（除 `tests/acceptance/`）。
- Must not edit：
  - `tests/acceptance/`。唯一例外：删掉 151 文件里的 `TICKET_153` 标记，以及因此没用了的 import 或定义（AGENTS.md 硬规矩）。断言、夹具、输入一字不改。
  - `web/src/`（前端归 156）、`evals/`、`skills/`、`sandbox-image/`。
  - `src/data_agent/agent.py`：端点不在本工单（Q8 人定 5）；`config` 事件是 146 的。
  - `Dockerfile`、`deploy/`、`docs/`（文档归 155）。
  - 154 的 git 来源代码。
  - `Makefile`、`.github/workflows/`、其他工单的文件、`.claude/`、`CODING_STANDARDS.md`、`AGENTS.md`。
- Server access: none
- Spend cap: $0。工单 agent 不能 source 模型的 env 文件，不调模型；真实运行交给主会话（见 Evidence required）。

## Context

行号取自 main `e494e49`。140、143、144、146 会移动这些行，认领时按符号名在当前 main 上核对。

- **决定**：
  - spec `.scratch/cloud-deploy/spec.md`"Implementation Decisions（Q7）"。
  - `.scratch/product-config/decisions.md` Q7，以及 **Q8**（"人定的 5 个"第 3–5 条；"主会话定的实现细节"的 `[server]`、`[policy]`、云端三条）。两者不一致时以 Q8 为准。
  - ADR-0011 第 5 条；ADR-0010 Update 2026-11-27。
- **验收测试**：151 的 `tests/acceptance/test_cloud_startup.py`、`test_cloud_web_token.py`、`test_cloud_locks.py` 里标 `TICKET_153` 的那些。契约 1、2 见 151 的测试 docstring；人审结论写在 151 的 Comments 里。标 `TICKET_156` 的不是本工单的。
- **配置层**：
  - `[server]` 的 host、port、runs_dir 由 144 接到 `data-agent web`（Q8）；本工单只加 `mode`。
  - `[policy]` 和命令行参数冲突时报错拒绝（Q8）。cloud 的收紧尽量落成"cloud 下 `[policy]` 等节的默认值不同"，复用 143/144 的锁定机制，不另写一套。
  - `config` 事件由 146 写（Q8：第二条事件，在 `version` 之后），记的是生效配置。
- **现状**：
  - 监听地址常量是 `WEB_HOST`（`src/data_agent/cli.py:37-38`），在 `uvicorn.run` 处使用（`cli.py:608`），144 改成从 `[server].host` 读。启动错误都经 `parser.error` 退出 2（`serve_web`，`cli.py:534-600`）。
  - `create_app` 的签名被之前的验收测试钉住（`src/data_agent/web/app.py:73-79`），只能往后加带默认值的参数。
  - API 路由在 `app.py:107-198`。UI 兜底路由（`app.py:210-224`）会吃掉 `/healthz`，所以 `/healthz` 要注册在 `serve_ui`（`app.py:200`）之前。
  - UI 页面和 `/assets/*` 不要凭据：156 的输令牌页面要能打开（151 契约 2）。
  - 联网：请求体默认 `network=True`（`app.py:52`、`:61`）。launcher 拼 `--network`（`web/launcher.py:179`），对话拼 `--network`（`web/conversations.py:143`）。IM 的回合不传 `network`（`im/adapter.py:302`），落到 `conversations.py:97` 的默认 `True`。
  - 140 正在给 `data-agent web` 加 `--network/--no-network`：关时要求联网的请求得到 409，IM 跟随。cloud 的联网上限接在 140 的同一处，不另写；认领时看 140 合入的样子。
  - dbt：网页总传 `--dbt-target scratch`（`launcher.py:178`），`run` 的默认值 140 在改（`cli.py:344`）。
  - 沙箱：网页总用 docker（`launcher.py:50`）；`run` 的 `--sandbox` 默认 docker（`cli.py:343`）。
  - 运行子进程由 `serve_web` 的 `start_run` 起（`cli.py:573-589`）。带 `--im` 时它已经用 `im_config.run_env` 去掉 IM 令牌的变量（`im/config.py:127-132`）。`DATA_AGENT_WEB_TOKEN` 照这个做法去掉，不带 `--im` 时也去掉。
  - 端点：cloud 照旧认 `DATA_AGENT_BASE_URL`（`agent.py:1237-1246`，部署层）。单次运行现在本来就改不了端点，不改代码、不写测试（Q8 人定 5）。
  - IM 名单为空、缺 `${VAR}`：143 起在所有模式都拒绝（Q8），151 写成守门测试，本工单不用另做。
- **上游先例**：
  - ktx `ktx mcp`：默认绑 127.0.0.1，非本机地址要 bearer token（`docs/research/2026-11-27-config-layering-local-cloud.md:100`）。ktx 另有允许的 host/origin 列表，不在 spec 里，不做。
  - SignalPilot `SP_DEPLOYMENT_MODE=local|cloud`：cloud 要求鉴权（同一文件 `:13`、`:120`）。
- **令牌**：
  - 比较用常数时间（`hmac.compare_digest`）。
  - 令牌的值不进日志、错误信息、事件文件、运行子进程的参数和环境。
  - 鉴权要写成一个函数，156 加 Cookie 时在同一处扩展。

## Acceptance

- [ ] `tests/acceptance/test_cloud_startup.py`、`test_cloud_web_token.py`、`test_cloud_locks.py` 里标 `TICKET_153` 的测试去掉标记后通过；守门测试仍通过；`TICKET_156` 的仍是预期失败
- [ ] `local` 模式不变：已有的 `tests/acceptance/test_web_api.py`、`test_chat_api.py`、`test_im_*.py`、`test_seam_a_run_cli.py` 不改就通过。143/141 已改过调用方式的，以合入的为准
- [ ] 令牌的常数时间比较、日志和异常文字不含令牌值，各有单元测试（`tests/` 下，不在 `tests/acceptance/`）
- [ ] 最终回复列出每条收紧的实际表现（报错文字、409 的 detail、`/healthz` 响应），供 155 写进 `docs/deploy.md`
- [ ] `make check` 通过

## Evidence required

贴到 Comments：
- `make check` 输出。
- `uv run pytest tests/acceptance/test_cloud_startup.py tests/acceptance/test_cloud_web_token.py tests/acceptance/test_cloud_locks.py -v`。

真实运行交给主会话（最终回复写出命令）：
1. 一份 cloud 配置。`data-agent web` 监听 `0.0.0.0`、不设令牌，贴拒绝启动的 stderr。
2. 设了令牌后：`curl` 不带令牌得到 401；带 bearer 的 `GET /api/runs` 得到 200；`GET /healthz` 得到 `{"ready": true}`。
3. 带 bearer 提交一题 `network: true`，得到 409；再提交一题 `network: false`，跑完。花费小，按小规模运行处理。

## Stop conditions

- 151 的某个验收测试看起来不对，或要改它才能转绿。
- product-config 146 没合入（Blocked by），或 143/144 的 `[policy]` 机制表达不了 cloud 的默认值，要改它们的契约。
- 联网上限和 140 合入的做法冲突。
- 要改 `web/src/` 或 `agent.py` 才能转绿。
- 第二轮审查修复后仍不过。
- 不停止、不重启本机的 Docker/colima 守护进程，不改它的配置。

## Failure handling

停下时：最终回复写明 `Status: ready-for-human`，Comment 写试了什么、哪里失败、分支名。

## Ledger

- Attempts: 0
- Review rounds: 0
- Model spend: $0 (data-agent model calls only, read from run event files; Claude Code / Codex subscription usage is not recorded)
- Outcome: — (accepted | returned)
- Human review minutes: — (filled by the human at merge, optional)

## Comments

- 2026-11-27（主会话建票）：cloud-deploy 的部署模式开关。等 product-config 144、146 和本 spec 的验收测试 151 合入。改动在后端（`src/`），照常跑 `da-review` 和 `eng:code-review`。建票时发现的问题按 `decisions.md` Q8 定。
