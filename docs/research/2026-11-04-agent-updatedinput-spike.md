# Agent 工具上的 `updatedInput` 实测（2026-11-04）

目的：确认 PreToolUse 钩子能不能把主 agent 派给子 agent 的 `prompt` 整段换掉。验证层 v2 的派单改写依赖这一点（ADR-0009，`.scratch/verifier-v2/spec.md`「派单」）。

环境：
- `claude_agent_sdk` 0.2.168，打包的 Claude Code CLI 2.1.301（`claude --version`）；
- 本机，模型 haiku，花费可以忽略。

## 方法

主 agent 被要求用 `Agent` 工具派 `checker` 子 agent，prompt 是 `ORIGINAL-123`。`checker` 只会原样复述它收到的任务文本。PreToolUse 钩子匹配 `tool_name == "Agent"` 并且 `subagent_type == "checker"`，返回 `permissionDecision: "allow"` 加 `updatedInput`。分两种模式各跑一次：

- `full`：`updatedInput` 是原输入的完整副本，只把 `prompt` 改成 `REPLACED-456`；
- `partial`：`updatedInput` 只有 `{"prompt": "REPLACED-456"}`。

## 结果

| 模式 | 子 agent 收到的内容 | 钩子触发次数 |
|---|---|---|
| full | `RECEIVED:REPLACED-456`，看不到原文 | 1 |
| partial | 子 agent 没有运行，没有任何 `RECEIVED:` 输出 | 2（主 agent 重试了一次） |

## 结论

1. 钩子能在 `Agent` 工具上把 `prompt` 整段换掉，子 agent 看不到主 agent 写的原文。
2. `updatedInput` 是**整体替换**原输入，不是合并：只给 `prompt` 时，`subagent_type` 等字段一起丢了，派单失败。改写时必须带上完整输入。
3. 匹配要同时看工具名和 `subagent_type`。这次子 agent 内部没有调工具，所以没有验证"钩子在子 agent 内部也会触发"。

这是 CLI 的行为，单元测试测不到。验证层 v2 在 `make test-integration` 里加了一个真实调用的用例（工单 46）。

## 脚本

当时的脚本原样附在下面。运行方式：`uv run --no-sync python spike.py full|partial`。

```python
import anyio, sys, json
from claude_agent_sdk import query, ClaudeAgentOptions, AgentDefinition, HookMatcher

MODE = sys.argv[1]  # full | partial
seen = []


async def hook(inp, tid, ctx):
    ti = inp["tool_input"]
    seen.append(("pre", inp.get("tool_name"), ti.get("subagent_type"), inp.get("agent_id")))
    if inp.get("tool_name") == "Agent" and ti.get("subagent_type") == "checker":
        new = dict(ti, prompt="REPLACED-456") if MODE == "full" else {"prompt": "REPLACED-456"}
        return {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "allow",
                "updatedInput": new,
            }
        }
    return {}


async def main():
    opts = ClaudeAgentOptions(
        model="haiku",
        permission_mode="bypassPermissions",
        setting_sources=[],
        agents={
            "checker": AgentDefinition(
                description="Echo checker",
                prompt="Reply with exactly: RECEIVED:<the full task text you were given>. Nothing else.",
                tools=["Read"],
                model="haiku",
            )
        },
        hooks={"PreToolUse": [HookMatcher(matcher=None, hooks=[hook])]},
        max_turns=6,
    )
    out = []
    async for m in query(
        prompt="Use the Agent tool with subagent_type 'checker' and prompt 'ORIGINAL-123'. Then print the subagent's reply verbatim.",
        options=opts,
    ):
        out.append(repr(m)[:400])
    txt = "\n".join(out)
    print(
        "MODE",
        MODE,
        "| ORIGINAL in subagent reply:",
        "RECEIVED:ORIGINAL" in txt,
        "| REPLACED:",
        "RECEIVED:REPLACED" in txt,
    )
    print(seen)
    import re

    print(re.findall(r"RECEIVED:[^'\"\\]{0,40}", txt))


anyio.run(main)
```
