# 宽 schema 上下文策略：抄 ktx 的工具契约，不运行 ktx

ktx（TS ~115k 行 + Python ~10k 行）需要 LLM 驱动的 ingest、embedding 后端与 Python 守护进程，MongoDB 只做上下文不做 SQL，且无端到端准确率数据（`docs/research/2026-09-21-ktx-context-layer.md`）。决定：不集成 ktx，只复制三个思路——(1) discover→hydrate 分层：搜索工具只回引用 + ≤200 字摘要，agent 再按需读全文；(2) `dictionary_search`：按关键词查列的真实取值；(3) join 发现先用 SignalPilot `analyze_project_db` 的 `_id` 列匹配，不够再抄 ktx 的 7 信号打分与预算（25 候选/列，≥0.85 接受）。

## Consequences

- 上下文层是三四个 MCP 工具，无 ingest、无向量库；agent 拿不到"整库预扫"的语义层，靠运行时探索 + Memory 补。
