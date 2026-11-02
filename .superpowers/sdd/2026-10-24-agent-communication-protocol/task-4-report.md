# Task 4 实施报告：完整 AgentWire 事件成本与严格 break-even

## 状态

已完成并在本地提交。分支：`agentcodec-agent-communication-design`。提交：`994c6337ce74796c1cd751b3174eec96072512e6`（`feat: account for complete AgentWire text costs`）。未 push、merge、publish 或 deploy；未开始 Task 5。

## 实现内容

- 在 `agentcodec.agentwire.contracts` 补上计划规定的共享 `CostPart`/`CostEvent`，并增加 `CostSummary`、`BreakEvenResult` 结果类型。事件、surface、part 标签、模拟标记和正 multiplicity 均使用严格契约；同一事件 ID 仅接受完整字段相同的重复项。
- 新增 `agentcodec.agentwire.costs.measure_cost_events`：按计划映射 part label 到现有 `TextComponent.kind`，按 event kind 推导 `attempt_kind`，再委托 `agentcodec.adaptive.meter.measure_attempts` 和现有 `TokenizerProfile`。完整事件文本由原有 meter 计量，不另行估算 token。独立 setup、request、response、retry、fallback 事件各自计量；相同 ID 的相同事件去重，不同 ID 不合并。multiplicity 只应用一次；多 surface 事件可以同时进入 transport/model-input 分面，但完整事件总量只计一次。meter 的分项 delta（包含负值）只作为 part 归因说明，不重复累加成成本。
- tokenizer 缺失或 `MeasurementUnavailable` 时返回 `cannot_compare`，token 成本与分项为 `null`，不会伪报为零；能从文本精确得到的 transport byte 数仍保留。响应事件只按明确标注的模拟文本计量，不代表真实模型输出或 provider 账单。
- 新增 `calculate_break_even`，用整数不等式求最小正整数 N，严格低于才算赢；相等不算赢。支持 `finite`、`never`、`cannot_compare`，负成本及非整数成本拒绝。两个基线由调用方分别传入并分别计算；比较的域、接收模式和计量 surface 必须由调用方保证一致，gateway freeform 只能用于 transport 成本比较。

## TDD 与验证

先新增合成行为测试，再运行 RED；9 个测试均因当时缺失的 `CostPart`/`CostEvent` 和成本 API 而失败（非收集或环境错误）。完成实现后同一聚焦测试 9 项通过。测试覆盖完整 setup/request/response 成本、part 与 attempt 映射、protocol/dictionary setup 独立计量、多 surface 不重复总量、冲突事件 ID、严格 break-even、负成本/零 multiplicity、tokenizer 不可用、响应成本、负 part delta 与重复事件 multiplicity。

目标工作树中没有 brief 指定的 `.venv/bin/python`，系统 `python3` 也未安装 pytest。未安装、下载或创建依赖环境；只读使用了已存在的仓库测试环境执行器，pytest 从当前目标工作树加载代码。执行命令和结果：

```text
.venv/bin/python -m pytest tests/test_agentwire_costs.py -q
结果：退出码 127；工作树中不存在 `.venv/bin/python`。

python3 -m pytest --version
结果：系统 Python 无 pytest 模块。

/workspace/agentcodec/.worktrees/agentcodec-mvp/.venv/bin/python -m pytest tests/test_agentwire_costs.py -q
RED：9 failed，均为预期的缺失契约/API 业务失败。

/workspace/agentcodec/.worktrees/agentcodec-mvp/.venv/bin/python -m pytest tests/test_agentwire_costs.py -q
GREEN：9 passed。

/workspace/agentcodec/.worktrees/agentcodec-mvp/.venv/bin/python -m pytest tests/test_agentwire_costs.py tests/test_agentwire_contracts.py tests/test_agentwire_modes.py tests/test_agentwire_codec.py tests/test_adaptive_meter.py -q
61 passed。

/workspace/agentcodec/.worktrees/agentcodec-mvp/.venv/bin/python -m pytest -q
1695 passed in 196.94s。

git diff --check
git diff --cached --check
结果：均无空白/patch 格式问题。

git add src/agentcodec/agentwire/contracts.py src/agentcodec/agentwire/costs.py tests/test_agentwire_costs.py
git commit -m "feat: account for complete AgentWire text costs"
结果：提交 994c6337ce74796c1cd751b3174eec96072512e6。
```

## 文件范围与边界

提交只包含：

- `src/agentcodec/agentwire/contracts.py`：共享成本事件与结果契约。
- `src/agentcodec/agentwire/costs.py`：事件计量及 break-even。
- `tests/test_agentwire_costs.py`：仅使用手写合成事件；fake meter 只用于测试归因/聚合。

保留了原有未跟踪计划文件 `docs/superpowers/plans/2026-10-24-agent-communication-protocol.md`，未暂存、未提交。未更改既有 adaptive/evaluation 契约。未查看或披露 raw corpus/dictionary；未调用真实模型、provider、API 或外部服务；没有网络访问、凭据使用、依赖安装/下载，也未派发 subagent/reviewer。

## 自审与关注事项

自审后提交文件范围与 Task 4 授权一致，现有 AgentWire/adaptive meter 回归通过，提交后工作树仅剩上述预存未跟踪计划文件。唯一环境关注项是目标 `.venv` 缺失，因此无法逐字执行 brief 给出的 pytest 命令；聚焦、相关回归和完整测试均已通过既有仓库 pytest 环境运行，未修改该环境或目标树以外的源代码。


## Fix round 1：独立审查修复

### 审查项与改动

Task 4 独立审查发现 `CostEvent.parts` 可为空，导致缺失文本的事件被 meter 当作空 `TextAttempt` 零成本计量；并建议直接测试 `calculate_break_even` 的未知成本输入。本轮只修改了：

- `src/agentcodec/agentwire/contracts.py`：将 `CostEvent.parts` 收紧为 `Annotated[tuple[CostPart, ...], Field(min_length=1)]`。
- `tests/test_agentwire_costs.py`：新增空 parts 拒绝测试，并验证一个显式 `CostPart(text="")` 仍被接受；新增 `None` 成本返回 `cannot_compare`、`first_strict_win is None` 的直接测试。

`src/agentcodec/agentwire/costs.py` 无需修改；未改 adaptive/evaluation contracts 或其他任务文件。

### TDD 与验证

先增加测试、未动生产代码时运行 RED：

```text
/workspace/agentcodec/.worktrees/agentcodec-mvp/.venv/bin/python -m pytest tests/test_agentwire_costs.py -q
结果：1 failed, 10 passed。唯一失败为 `test_cost_event_rejects_missing_parts_but_allows_explicit_empty_text`，具体断言 `pytest.raises(ValidationError)` 未触发，证实空 parts 当前被接受；None break-even 测试通过。
```

应用最小契约修复后运行 GREEN：

```text
/workspace/agentcodec/.worktrees/agentcodec-mvp/.venv/bin/python -m pytest tests/test_agentwire_costs.py -q
结果：11 passed。
```

全套回归：

```text
/workspace/agentcodec/.worktrees/agentcodec-mvp/.venv/bin/python -m pytest -q
结果：1697 passed in 197.29s。

git diff --check
git diff --cached --check
结果：无空白或 patch 格式问题。
```

### 自审、提交与边界

自审确认空 parts 会在 Pydantic 合约层拒绝；显式空字符串仍作为一个实际 part 保留，因而不会把“缺失事件文本”与“明确声明的空文本”混为一谈。未知 break-even 有直接回归测试，返回 `status="cannot_compare"`、`first_strict_win=None` 与稳定原因码。未进行网络、模型/provider/API 调用或依赖安装/下载；测试使用既有仓库 pytest 环境。

fix round 1 已单独本地提交：`c23cbe6`（完整哈希待最终状态核验），提交信息 `fix: reject AgentWire cost events without parts`。提交范围仅为 `contracts.py` 与 `tests/test_agentwire_costs.py`；报告与原有未跟踪计划文件未暂存。未 push、merge 或开始 Task 5。


### 最终提交哈希核验

前文提交行中的“完整哈希待最终状态核验”是写报告时的临时占位；核验结果为 `c23cbe654ceedcc73e3fce6d22bd5691f7d9c383`。HEAD 提交文件仅为 `src/agentcodec/agentwire/contracts.py` 与 `tests/test_agentwire_costs.py`。
