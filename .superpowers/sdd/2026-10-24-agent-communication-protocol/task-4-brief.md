### Task 4：实现完整事件成本与严格 break-even

**Files:** 新增 `agentwire/costs.py`、`tests/test_agentwire_costs.py`；按需在 `agentwire/contracts.py` 增加成本结果类型。

**Interfaces:** 消费共享 `CostEvent` 定义和现有 `TokenizerProfile`；实现 `measure_cost_events`、`calculate_break_even`。

- [ ] **Step 1：写失败测试。** `test_complete_request_response_and_setup_are_counted_once` 断言每个不同完整文本事件经现有 meter 进入总量、model input/output 分面按 surface 归因；`test_cost_events_convert_to_existing_text_attempts_without_heuristics` 验证 component kind、由 event kind 推导的 attempt kind、simulated 和 multiplicity 精确映射；`test_protocol_and_dictionary_setup_histories_are_independent_events` 验证两种 setup 有各自文本、身份和历史，不能互相抵扣；`test_direct_request_can_have_two_surfaces_without_double_total` 验证同一事件的 transport/model-input 分面重叠但唯一事件总量不重复；`test_same_id_with_changed_text_or_surface_is_rejected` 拒绝事件 ID 的任何字段冲突；`test_break_even_is_reported_separately_for_each_baseline` 覆盖分别相对 labelled-field 与 freeform 基线计算、相等不算赢、N=1、较大 setup 后首次胜出和永不胜出；`test_negative_costs_and_zero_reuse_are_rejected` 验证成本非负、复用次数为正；`test_missing_tokenizer_returns_cannot_compare_not_zero` 断言无 tokenizer 时成本为 null/cannot_compare。
- [ ] **Step 2：验证 RED。** 运行 `.venv/bin/python -m pytest tests/test_agentwire_costs.py -q`，确认业务断言失败。
- [ ] **Step 3：实现计量。** 完整 request 作为一个完整事件文本计数；protocol setup/negotiation、dictionary distribution、request、response、retry、fallback 各自成完整独立事件，历史与当前 setup 分账。复用 `agentcodec.adaptive.meter.measure_attempts` 的本地精确 tokenizer 路径，不新建 tokenizer 下载/字符估算。未知输入返回 cannot_compare。break-even 用整数公式计算最小 N，分别与同域、同接收模式、同计量面下的 labelled-field 和 freeform 基线比较；gateway freeform 只参与 transport 成本对比，不生成 model-context/端到端 break-even。
- [ ] **Step 4：验证 GREEN。** 重跑同一命令；用测试专用 fake meter 只验证归因/聚合逻辑（不得进入生产实现），再测试 `MeasurementUnavailable`、响应成本、负 token delta 的分项解释和重复事件 multiplicity。
- [ ] **Step 5：自审并提交。** 仅提交成本实现/结果类型和测试，提交信息 `feat: account for complete AgentWire text costs`。

