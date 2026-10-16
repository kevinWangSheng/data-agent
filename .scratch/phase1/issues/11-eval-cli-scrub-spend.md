# 11 — 评测 CLI：scrub 与累计花费

Status: in-review
Blocked by: 01
Spec: ../spec.md
Claimed by: claude-opus-5-5 (worktree 11-eval-cli-scrub-spend)
Branch: worktree-11-eval-cli-scrub-spend

## Goal

上传前的 scrub 命令能移除 API key、私有端点、IP、主机名；批量运行累计花费到上限 80% 时停止并提示找人。

## Authority

- May edit: `evals/`、`tests/`（除 `tests/acceptance/`）
- Must not edit: `tests/acceptance/` 下的验收测试（唯一例外：本工单让某个测试转绿时，删掉该测试的 `xfail` 标记，其余一字不改）、其他工单的文件、`.claude/`、`CODING_STANDARDS.md`
- Server access: none
- Spend cap: none

## Context

plan §7（上榜轨迹公开、清理脚本强制运行、80% 护栏）；`.gitleaks.toml` 里已有的 IP 与 key 规则可参考。

## Acceptance

- [ ] 接缝 C 中 scrub 与累计花费的验收测试转绿
- [ ] `make check` 通过

## Evidence required

对夹具轨迹跑 scrub 前后的差异（用假数据）。

## Stop conditions

Spec 自相矛盾或验收测试看起来有错（接口猜错）；需要人做决定；花费到上限；第 2 轮审查仍不过。

## Failure handling

`Status: ready-for-human`，Comment 写清试了什么、哪里失败、分支名。

## Ledger

- Attempts: 2（第 2 次是退回返工）
- Review rounds: da-review 4 次（修了 3 轮，第 3 轮是人批准的返工），eng:code-review 1 轮（已修）
- Model spend: $0（没有调模型；只有审查本身的花费）
- Outcome: 退回的 2 条 P2 已修（abb147d）；第 4 次 da-review 又报 2 条新 P2，照退回要求只记录不修，待人决定
- Human review minutes: —

## Comments

### 2026-10-16 claude-opus-5-5：实现完成；第 3 次 da-review 有 2 条 P2 未修，交回

分支 `worktree-11-eval-cli-scrub-spend`，提交：7434cb6、66ec447、609a2d3、2ebf36e。

**做了什么**
- `python -m evals scrub DIR [--hostname NAME ...]`（`evals/scrub.py`）：原地改写文件，移除以下内容：
  - `DATA_AGENT_API_KEY` 的值；
  - 形似密钥的串：`sk-…`、Bearer、JWT、`gh*_`、`AKIA…`；
  - `DATA_AGENT_BASE_URL` 整条和它的主机名；
  - IPv4 地址；
  - `--hostname` 给的主机名（大小写不敏感，按整段 label 匹配，所以 `db` 不会命中 `db-1`）。
  - `.json` / `.jsonl` 先解码，按字符串逐个清理，有改动才重新序列化；解析不了的行按纯文本清理。
  - 碰到非 UTF-8 文件、读不了的文件或目录、symlink：列出来，退出 1（宁可失败也不放过）。
- `python -m evals spend RUNS_DIR --budget USD`（`evals/spend.py`）：用 Decimal 累加各 `*.jsonl` 里 result 事件的 `cost_usd`。
  - 到 80% 的预算退出 1；
  - 花费算不清时退出 2：坏行、result 没有 cost、目录读不了、symlink、参数错。
- `evals/walk.py`：共用的遍历，把读不了的目录和 symlink 报出来。原来用的 `rglob` 会静默跳过它们。
- 验收：接缝 C 的 `test_scrub_removes_keys_endpoints_ips_and_hostnames`、`test_spend_stops_at_80_percent_of_budget` 已转绿，只删了这两条的 `xfail`。另有边界单测 `tests/test_eval_scrub.py`、`tests/test_eval_spend.py`。
- `make check`：19 passed，15 deselected，37 xfailed，ruff 和 basedpyright 0 错误。

**真实运行（假数据）**：先复制 harbor_job 夹具，再往里追加一行泄漏；用假的 `DATA_AGENT_API_KEY` / `DATA_AGENT_BASE_URL` 加 `--hostname build-box-7` 跑 scrub：

```
scrub: changed 1 file(s) of 43; redactions: key=1 endpoint=1 host=1 ip=2
exit=0
diff before/after (dbt-alpha__Qm3xT9a/agent/events.jsonl):
< {"seq": 4, "type": "tool_end", "tool": "Bash", "output": "POST https://llm-gw.corp.example:8443/anthropic/v1/messages x-api-key=sk-e2e7…(假 key) from 10.20.30.40 via 203.0.113.7 on build-box-7; built customer_orders in 3.2s"}
> {"seq": 4, "type": "tool_end", "tool": "Bash", "output": "POST [redacted-endpoint]/v1/messages x-api-key=[redacted-key] from [redacted-ip] via [redacted-ip] on [redacted-host]; built customer_orders in 3.2s"}
```

```
$ python -m evals spend tests/fixtures/harbor_job --budget 3.5
spend: $2.3750 over 21 event file(s); budget $3.5, stop at 80% = $2.8000      exit=0
$ python -m evals spend tests/fixtures/harbor_job --budget 2.96875
spend: reached 80% of the budget; stop the batch and ask a human
spend: $2.3750 over 21 event file(s); budget $2.96875, stop at 80% = $2.3750  exit=1
```

**审查**
- da-review 第 1 次，已修（66ec447）：
  - P1：紧跟在 JSON 转义（`\n`、`\t`）后的主机名或 key 漏掉了；
  - P2：`rglob` 静默跳过读不了的目录，scrub 和 spend 都会因此误报成功。
- da-review 第 2 次，已修（609a2d3）：直接在原文上替换时，`-` 写法的主机名会漏掉，而且可能吞掉 `\n` 的反斜杠，把 JSON 弄坏。改成先解码再清理。
- eng:code-review（Standards + Spec），已修（2ebf36e）：
  - 补了更多密钥形状；
  - 重新序列化时保留非 ASCII 字符；
  - 80% 的文案改由常量 `STOP_FRACTION` 生成；
  - 去掉预算解析失败时的 sentinel；
  - 加了 `Rules` 类型别名。
- eng:code-review 中不修的条目及理由：
  - IPv6：验收接口写的是 "IPv4 addresses"。IPv6 的正则容易误伤 `::`、时间戳和代码。
  - 遇到二进制文件就失败：是有意的。上传前不能放过检查不了的文件；真实 Harbor 目录里有没有二进制产物还没确认。
  - spend 用退出码 2 区分"花费算不清"：两者都非零，批量 runner（工单 12）只看非零就停。
  - 中途崩掉、没写 result 事件的运行按 $0 计：SDK 只在 `ResultMessage` 里给花费，事件文件里没有别的数可以累加；Harbor 回填的花费也来自同一个事件。
  - `run()` 开头的小段重复、argparse 私有类型 `_SubParsersAction`（这是标准写法）、`walk.py` 里的 `elif name in filenames`：改了收益很小。
- **da-review 第 3 次，未修，已达 2 轮上限，所以交回**：
  1. P2 `evals/scrub.py`：工具输出里嵌着的 JSON 字符串只解码了外层；内层如果是 `build-box-7` 这类转义写法，仍会漏掉。建议：在字符串值里认出嵌套 JSON，就递归解码再清理；或者字符串里还有 `\uXXXX` 转义时，判为检查不了，直接失败。
  2. P2 `evals/spend.py:36`：`splitlines()` 会在 U+2028、U+2029、U+0085 处切行。这个问题是 2ebf36e 加 `ensure_ascii=False` 后带出来的回归：scrub 写回字面 U+2028 之后，spend 会把这个合法文件判成坏文件，返回 2。修法是一行：改成 `split("\n")`，或者按文件行迭代；也可以撤回 `ensure_ascii=False`。

**需要人决定**：要不要再给一轮修复，处理上面 2 条；还是先合并，作为已知限制另开工单。

### 2026-10-16 主会话：退回：第 3 次 da-review 的 2 条 P2，人已同意再修一轮

在原分支 `worktree-11-eval-cli-scrub-spend` 上接着修。只修下面 2 条，各配一个修复前会失败的测试；修完 `make check`、提交，再跑一次 `da-review main` 只确认这两条已修好（新发现写进 Comments，不再开修复轮）。

1. `evals/scrub.py`：字符串值里嵌的 JSON 只解码了外层，内层转义写法（如 `build\u002dbox-7`）的主机名会漏掉。递归解码嵌套 JSON 再清理；解不了而仍含 `\uXXXX` 转义的，判为检查不了，失败退出。
2. `evals/spend.py:36`：`splitlines()` 会在 U+2028/U+2029/U+0085 处切行，把 scrub 写回的合法文件判成坏文件。改为只按 `\n` 切行。

注意：ticket 10（PR #3）也改 `evals/__main__.py`，后合并的一方由主会话 rebase，你不用处理。

### 2026-10-16 claude-opus-5-5：返工完成，退回的 2 条已修（abb147d）

分支 `worktree-11-eval-cli-scrub-spend`，提交 abb147d。

**改了什么**
1. `evals/scrub.py`：字符串里只要还有 `\uXXXX`，就先整体按 JSON 解码，失败再按 JSONL 逐行解码，然后递归清理。仍然解不了而且还含 `\uXXXX` 的文本，标成检查不了：列出文件，退出 1，文件不改写。纯文本文件和解析不了的 JSON 行用的是同一套规则。没有 `\uXXXX` 的字符串走原来的路径。
   - 修复前会失败的测试：`test_scrub_decodes_json_nested_in_strings`、`test_scrub_fails_on_unicode_escapes_outside_json`。
2. `evals/spend.py`：改成只按 `\n` 切行。
   - 修复前会失败的测试：`test_spend_reads_lines_with_unicode_line_separators`（修复前退出 2，报 "line 1 is not JSON"）。

`make check`：22 passed，15 deselected，37 xfailed；basedpyright 0 errors。

**真实运行（假数据）**：复制 harbor_job 夹具，追加一行工具输出。这行输出本身是 JSON，内层用转义写了主机名，还带一个字面的 U+2028：

```
$ python -m evals scrub $D --hostname build-box-7
scrub: changed 1 file(s) of 43; redactions: key=0 endpoint=0 host=1 ip=0      exit=0
< {"seq": 9, ..., "output": "{\"host\": \"build\\u002dbox-7\", \"note\": \"a<U+2028>b\"}"}
> {"seq": 9, ..., "output": "{\"host\": \"[redacted-host]\", \"note\": \"a<U+2028>b\"}"}
$ python -m evals spend $D --budget 3.5        # 读的是清理后、带字面 U+2028 的文件
spend: $2.3750 over 21 event file(s); budget $3.5, stop at 80% = $2.8000       exit=0
$ printf 'echo build\\u002dbox-7\n' > $D/trial.log; python -m evals scrub $D --hostname build-box-7
scrub: cannot check these; remove or scrub by hand before upload:
  .../job/trial.log: a \uXXXX escape outside JSON could hide a name             exit=1
```

**确认用的 da-review（第 4 次）**：原来那 2 条没有再报。新报了 2 条 P2，照退回要求只记录、没修：
1. P2 `evals/scrub.py:125`：`json.loads` 遇到重复键时只留最后一个值。`{"output":"build-box-7","output":"ok"}` 看不出有改动，所以原文原样保留，退出 0，被丢掉的那个值就漏了。建议用 `object_pairs_hook` 检查重复键，有重复就判为检查不了。
2. P2 `evals/scrub.py:118`：对象的键走的是 `scrub_text`，不是 `scrub_string`。键里嵌的 JSON 或剩下的 `\uXXXX` 既不解码也不拒绝。修法是一行，把键也交给 `scrub_string`。

两条都是一两行的修法，各配一个测试就能修好。**需要人决定**：是在本工单再给一轮修，还是先合并，另开工单处理。
