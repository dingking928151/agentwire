# Task 4 独立审查结果

审查区间：`f396e28e3d0a8a2ac8b8b0030c48519431ec1fba..994c6337ce74796c1cd751b3174eec96072512e6`
Review package：`review-f396e28..994c633.diff`

## Spec compliance

基本符合；核心计量和 break-even 逻辑符合计划。事件映射复用既有 meter，按 ID 去重，多 surface 不重复总量，multiplicity 只应用一次；不可比较时 token 成本不补零，break-even 使用整数严格不等式。未发现既有 adaptive/evaluation contracts 被修改。

## Important finding

`src/agentcodec/agentwire/contracts.py:231` 的 `CostEvent.parts` 允许空 tuple。空 parts 可成为合法空 `TextAttempt`，随后被 meter 精确计为零 token；缺失的事件文本因此会被静默当作零成本。这会破坏完整事件成本口径。修复：将 parts 限制为至少一个元素；在 `tests/test_agentwire_costs.py` 增加拒绝空 parts 的回归测试。可保留 `CostPart(text="")` 作为明确表达的空文本事件（若契约允许）。

## Minor finding

`tests/test_agentwire_costs.py:210-222` 缺少 `calculate_break_even` 对未知输入 `None` 返回 `cannot_compare` 且无阈值的直接测试。实现已经显式处理 `None`；建议一并补测试。

## Cannot verify

- diff 没有调用方，无法验证调用者会分别比较 labelled-field/freeform、保证 domain/receiver/surface 同类或阻止 gateway freeform 用于端到端 break-even。
- 未重跑测试。实现者报告中的 focused 9、相关回归 61、full 1695 均未独立复验。

## Strengths

严格契约、既有 meter 复用、事件去重/多 surface/分项 delta 处理，以及 break-even 的严格整数公式均有清楚实现。reviewer 聚焦核对了本地 meter 的错误语义和计量接口。

## Verdict

Task quality: Conditional / Needs fix. Task 5 is blocked until the Important empty-parts issue is fixed and fresh scoped re-review closes it. No Critical findings.
