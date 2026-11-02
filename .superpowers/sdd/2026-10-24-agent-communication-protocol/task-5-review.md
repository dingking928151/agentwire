# Task 5 独立审查结果

Review range: `c23cbe654ceedcc73e3fce6d22bd5691f7d9c383..b2058d98811b3b56f191dd3b8c27c3076a06a1bf`
Review package: `review-c23cbe6..b2058d9.diff`

## Spec compliance

存在两项 Important，Task quality Needs fixes。Domain separation、selected transport copy、freeform pairing/gateway pass-through、双基线分开和合成 fixtures 多数符合要求。

## Important findings

1. **Gateway decode failure 仍可能产生较低的精确 lifecycle cost 并参与 break-even。** `src/agentcodec/agentwire/benchmark.py:703-718,819-824,900-908` 中，gateway 解码失败会缺少 model context 和 synthetic response 成本，但 `metric_status` 仍可能是 `exact_local_text`，比较逻辑也未检查 `strict_decode_status`。这会让缺失的下游尝试成本制造虚假的成本优势。应将失败候选的端到端/lifecycle comparison 标为 `cannot_compare`、相关值置 null；或只有在明确完整计入合成 retry/fallback 且恢复后的可比任务完整时才报告 break-even。失败尝试本身仍须按有完整文本的事件计量，不能默认为零。

2. **不完整 cold history 可能被当作已知完整成本。** `src/agentcodec/agentwire/benchmark.py:102-115,123-132,480-510` 允许 cold text setup 状态携带 `history_complete=False`，但 `_has_incomplete_state` 未将此组合识别为未知历史；部分 history 因此可能形成看似精确的 historical/lifecycle cost。应拒绝 non-unknown 状态的 incomplete history，或传播为 `cannot_compare` 并把相关数值置 null。未知必要历史不得补零/假设为免费。

## Minor

`src/agentcodec/agentwire/benchmark.py:1-1092` 新模块较大，可在行为稳定后拆分职责。此项为维护建议，不阻塞本 fix round。

## Cannot verify

Reviewer 无法从该 diff 确认 Tasks 1–4 调用组件的内部 meter/multiplicity/distinct-text、break-even、gateway 解码与 request-builder 语义；只确认 diff 没有修改旧接口。Reviewer 未重跑测试；实现者报告的目标 22 passed、full 1719 passed 不能独立验证。离线边界也据实现者报告而非 diff 独立证明。
