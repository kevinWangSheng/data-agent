# 154 — 上下文从 git：git 地址 → 本地路径（浅克隆、`[server].refresh`、原子切换、失败回退）

Status: ready-for-agent
Blocked by: product-config 143, product-config 147, product-config 148, 151, 152
Spec: ../spec.md（Implementation Decisions"上下文从 git"）；决定：`.scratch/product-config/decisions.md` Q8"git 来源"
Claimed by: —
Branch: —

## Goal

`[skills].dirs`、`[instructions].files`、`[sources.*].docs` 的值可以写成 `git+https://…@<分支或提交>#<子目录>`，本工单把这样的值解析成本地路径（Q8）：
- 服务器进程启动时浅克隆到 `<runs_dir>/.cache/git`；运行读这份，不自己克隆。
- 按 `[server].refresh`（如 `"1h"`，不写就不刷新）再拉。刷新先克隆到新目录，再原子切换；正在跑的运行继续用旧目录。
- 拉失败时继续用上一次的内容，并写一条错误日志。
- git 的令牌走 `${VAR}`，经 `GIT_ASKPASS` 传，不进 URL、`.git/config`、日志。

使用这些路径的代码是 product-config 147、148 的，不在本工单。151 里标 `TICKET_154` 的测试全部转绿。

## Authority

- May edit：
  - 新建 `src/data_agent/git_sources.py`，或者 151 契约 5 定的模块名。
  - 143 合入的配置模块：接受 git 值，把 `refresh` 补进 `[server]` 的格式（Q8）。以 143 实际建的文件为准。
  - `src/data_agent/cli.py`、`src/data_agent/web/app.py`：只为服务器启动时拉取、lifespan 里按间隔刷新接线，以及把解析出的路径交给运行。
  - `tests/`（除 `tests/acceptance/`）。
- Must not edit：
  - `tests/acceptance/`。唯一例外：删掉 151 文件里的 `TICKET_154` 标记，以及因此没用了的 import 或定义（AGENTS.md 硬规矩）。
  - 使用这三个键的代码：Skill 打包与加载归 147，说明文件和数据源文档归 148。
  - `src/data_agent/sandbox.py`、`agent.py`、`web/src/`、`evals/`、`skills/`。
  - `Dockerfile`、`deploy/`、`docs/`（文档归 155）。
  - `Makefile`、`pyproject.toml`（不加依赖）、`.github/workflows/`、其他工单的文件、`.claude/`、`CODING_STANDARDS.md`、`AGENTS.md`。
- Server access: none
- Spend cap: $0。测试只用本地 `file://` 仓库，不联网、不调模型。

## Context

行号取自 main `e494e49`。

- **决定**：
  - spec `.scratch/cloud-deploy/spec.md`"上下文从 git"。
  - `.scratch/product-config/decisions.md` Q7"上下文从 git"（照 nao），以及 Q8"主会话定的实现细节"的 git 来源一条。两者不一致时以 Q8 为准。
- **取证**：`docs/research/2026-11-27-config-layering-local-cloud.md` 第 8 条（`:16`）；附录 A nao（`:74`：`NAO_CONTEXT_SOURCE=git`、令牌或 SSH key 从环境变量来、`NAO_REFRESH_SCHEDULE` 定时拉）。
- **上游实现，照着做**：nao 的 `cli/nao_core/context/git.py`（`~/dev/AI/data-agent-refs/nao/`）。
  - 这个文件没有 `@license Enterprise` 头，可以参考。带这个头的 nao 文件不能复制（AGENTS.md 硬规矩）。复制代码就保留版权头并登记 `NOTICE`（Apache-2.0）。
  - 浅克隆：`git clone --branch <分支> --depth 1 --single-branch`（`git.py:76-116`）。
  - 子目录：nao 用 `git sparse-checkout set <子路径>`（`docker/entrypoint.sh:91`、`:111`）。
  - 定时：nao 在 FastAPI lifespan 里用 APScheduler（`apps/backend/fastapi/main.py:75-118`）。这里用 `[server].refresh` 的间隔，不加新依赖，一个后台任务就够。网页已有 lifespan（`src/data_agent/web/app.py:92-100`）。
  - 不照 nao 的两处：
    - nao 刷新时在原目录里 fetch 再 reset（`git.py:118-169`）。这里按 Q8 先克隆到新目录、再原子切换（如换一个符号链接，或换路径指针），旧目录在没有运行用它之后再清理。
    - nao 把令牌拼进 URL（`git.py:40-55`），`git clone` 会把它写进 `.git/config`。这里按 Q8 经 `GIT_ASKPASS`。
- **克隆位置**：`<runs_dir>/.cache/git`（Q8）。`runs_dir` 里隐藏名字的目录不算运行（`src/data_agent/web/runs.py:44-51`），所以不会出现在运行列表里。`[server].runs_dir` 由 144 接线（Q8）。
- **单独的 `data-agent run`**（不经服务器）：Q8 只定了服务器进程统一拉。`run` 碰到 git 值是启动时拉一次，还是报错要求先由服务器拉，由 151 契约 5 定；没定就停下。
- **沙箱看得到的路径**：
  - `DockerSandbox` 把 Skill 目录按 `--mount type=bind,source=<路径>` 挂进沙箱（`src/data_agent/sandbox.py:277-278`）。data-agent 在容器里时，这个源路径按宿主机解析。
  - 克隆目录、原子切换后的路径都要在 152 选定的方案里能挂进沙箱。照 152 的研究笔记 `docs/research/2026-11-27-sandbox-in-container-spike.md`（152 是 Blocked by，合入时笔记已在）。
  - 切换方式如果用符号链接，要确认挂载按解析后的路径挂，沙箱看得到真实目录。
- **验收测试**：151 的 `tests/acceptance/test_cloud_git_sources.py`。契约 5、6（模块名、函数签名、接受 `git+file://`）见 151 的测试 docstring。
- **配置层**：product-config 143 的未知键报错，所以 `refresh` 要加进 143 的类型定义。

## Acceptance

- [ ] `tests/acceptance/test_cloud_git_sources.py` 里标 `TICKET_154` 的测试去掉标记后通过，守门测试仍通过
- [ ] 单元测试（`tests/`，不在 `tests/acceptance/`）覆盖：
  - 地址解析：分支、提交、子目录，以及缺 `@` 或 `#` 的写法。非法写法报错，并点名文件和键。
  - `[server].refresh` 的取值解析，非法值报错。
  - 原子切换：刷新过程中，读旧路径的一方始终看到完整的旧内容。
  - `GIT_ASKPASS`：用一个假令牌值，断言它不在克隆命令的参数、`.git/config`、日志、异常文字里。
- [ ] 普通本地路径的值行为不变（已有测试不改就通过）
- [ ] 解析在配置加载时完成，147、148 的代码拿到的是本地路径、不用改；真实运行里 git 来源的 Skill 目录和说明文件确实被这次运行读到（`decisions.md`"留给下一个会话"第 3 条的处理结果）
- [ ] `make check` 通过

## Evidence required

贴到 Comments：
- `make check` 输出。
- `uv run pytest tests/acceptance/test_cloud_git_sources.py -v`。
- 一次真实运行（$0，本地 `file://` 仓库）。贴命令和输出：
  1. 起 `data-agent web`，`<runs_dir>/.cache/git` 下有克隆；`data-agent config show` 显示解析后的本地路径。
  2. 再提交一次，过了 `refresh` 间隔后切到新目录。
  3. 把仓库移走，看到错误日志，旧内容仍在。

## Stop conditions

- 151 的某个验收测试看起来不对，或要改它才能转绿。
- 143 合入的配置层里，这三个键的值不能在加载时替换成本地路径，要改 143 的契约。
- 要改 147、148 使用这些键的代码，或者 `sandbox.py`。
- 152 的路径方案下，克隆目录或切换后的路径挂不进沙箱。
- 单独 `data-agent run` 碰到 git 值怎么办，151 契约没定。
- 第二轮审查修复后仍不过。
- 不停止、不重启本机的 Docker/colima 守护进程。本工单不需要 Docker。

## Failure handling

停下时：最终回复写明 `Status: ready-for-human`，Comment 写试了什么、哪里失败、分支名。

## Ledger

- Attempts: 0
- Review rounds: 0
- Model spend: $0 (data-agent model calls only, read from run event files; Claude Code / Codex subscription usage is not recorded)
- Outcome: — (accepted | returned)
- Human review minutes: — (filled by the human at merge, optional)

## Comments

- 2026-11-27（主会话建票）：cloud-deploy 的 git 上下文来源。等 product-config 143、本 spec 的 151 和 152 合入。本工单只把 git 地址解析成本地路径，接入由 147、148 做。克隆位置、拉取方、原子切换、`GIT_ASKPASS` 按 `decisions.md` Q8 定。
