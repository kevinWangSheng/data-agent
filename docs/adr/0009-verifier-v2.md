# 验证层 v2：硬性事实交给工具和 harness，模型只对照题面判断改动

accept-2 里验证层花费大、信号弱：
- 全部 PASS 的 trial 成功率 63%，full 整体是 62%；34 个失败 trial 里只有 10 个收到过 FAIL。
- 验证器占 full 运行时间的约 1/3。

它还会把 agent 带偏：
- CHECK 6 审查没动过的逻辑，让 agent 删掉了评分器需要的过滤条件；
- CHECK 5 拿主 agent 自己的说法当"原样"；
- 粒度键里的 NULL 由模型判断，被文档说明豁免掉了；
- 值验证器用模型自己的数据源重算，等于复核实现本身；
- FAIL 只靠提示词约束。

上游 SignalPilot 的 benchmark 版有改动前快照（`prebuild_state.md`）和粒度、NULL 子检查，我们复制时没有带上。证据见 HANDOFF "accept-2 根因审查" 第 2 条。

决定（细节见 `.scratch/verifier-v2/spec.md`）：
- **快照**：harness 在 agent 开跑前保存 dbt 项目的逻辑文件，存在 workspace 外。"原样"以快照为准，不以 agent 的说法为准。
- **4 个确定性工具**：
  - `model_diff`：只报改动；
  - `contract_check`：存在、列契约（只认题面和原有 YML）、改动过的窗口函数的排序唯一性；
  - `grain_facts`：粒度键的 NULL 和重复是规则 FAIL，只有题面或原有 YML 能豁免；
  - `compare_by_key`：全键比对，拒绝引用被测模型或其他改动过的模型。
- **只保留一个验证子 agent**，拿掉值验证器。模型只做两件事：对照题面判断改动；从题面挑粒度键、写独立的参考 SQL。`maxTurns` 为 50，撞到上限按 NEEDS_INFO 处理。
- **harness 钩子**（只在启用了验证器的配置里挂）：
  - 第一次派单用模板改写 prompt，再次派单的附加内容加上"未经核实"的标题；
  - Stop 钩子：改过模型却没验证过、报告之后又改过模型、还有 FAIL，这三种情况拦截，最多拦 2 次，NEEDS_INFO 不拦；
  - SubagentStop 记录每次验证调用的轮数。
- **FAIL 处理只写在一处**（系统提示词）：先复现再做最小改动，复现不出来就带上反证再派一次。
- **报告格式**：每行一条 `CHECK n: STATUS — 理由`，供钩子和事件解析。

## Considered Options

- **让 FAIL 有约束力，但检查本身不改**：精度太低，会把错误的 FAIL 也强制执行。约束放在检查改好之后。
- **保留值验证器**：103 次调用只报出 17 次 FAIL，而且是复核实现本身。它唯一对照题面的部分，由 `compare_by_key` 加独立参考 SQL 替代。
- **只靠提示词禁止主 agent 在派单时带入假设**：accept-2 里这样写了，仍有 120/212 次派单写了源表名。所以改由 harness 改写派单，本地实测可行。
- **拿掉整个验证层**：统一评估会同时跑 no-verifiers，按 ADR-0008 Update 2026-11-04 的规则在 full 和 no-verifiers 之间选，不在这里预先定。

## Consequences

- full 的验证器调用次数和用时应该明显下降。初筛要求验证器墙钟时间的中位数 ≤155 秒（目标题基线 311 秒）。
- 验证层多了快照和钩子，只作用于启用了验证器的配置；baseline 和 no-verifiers 不受影响，三组配置的对照保持干净。
- 只能看到"改了什么"，看不到"该改而没改"。后者仍然要靠模型对照题面判断，以及 `compare_by_key`。
