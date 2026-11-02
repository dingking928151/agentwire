### Task 3：实现 gateway 与 model-direct 文本构造

**Files:** 新增 `agentwire/gateway.py`、`agentwire/prompts.py`、`tests/test_agentwire_modes.py`。

**Interfaces:** 消费 Task 1–2；实现 `decode_at_gateway`、benchmark-only `deliver_benchmark_at_gateway` 和 `build_direct_model_request`。

- [ ] **Step 1：写失败测试。** `test_gateway_returns_only_complete_message_or_fallback` 验证 AWP1 成功完整解码及任一字段错误时 `message is None`；`test_gateway_decodes_exact_domain_natural_language_renderer` 验证 `natural_language` 输入由同一 domain MessageV1 经 renderer 生成并 strict parse 成相同完整实例；`test_gateway_passes_freeform_through_without_parsing_or_model_context` 验证 `natural_language_freeform` 原文仅 pass-through、无规范 model_context 且不触发 parser；`test_gateway_rejects_out_of_order_or_missing_labels` 验证 labelled-field 错序/缺字段不会被猜测；`test_gateway_decodes_only_declared_json_ac1_ac2_and_awp1` 验证其他格式按显式类型使用 strict parser，未知/格式错时不猜另一 decoder；`test_gateway_does_not_auto_fallback_or_execute` 验证错误只返回 reason code，不调用网络、subprocess 或工具；`test_direct_request_keeps_common_task_prompt_in_every_format` 验证同一任务提示在每个 model-direct 条件中原样出现；`test_direct_request_uses_format_specific_instructions_and_full_glossary` 验证 AC1/AC2/AWP1 各自完整固定说明、两张 AWP1 enum 表和所选词典的完整 glossary 都在请求中；`test_natural_language_direct_requests_add_no_compact_protocol_legend` 验证两种自然语言候选均不携带短码说明。
- [ ] **Step 2：验证 RED。** 运行 `.venv/bin/python -m pytest tests/test_agentwire_modes.py -q`，确认只因新增接口/行为失败。
- [ ] **Step 3：实现纯本地适配。** AWP1 gateway 仅调用严格 decoder 并映射固定 fallback reason；benchmark-only gateway dispatcher 对 `natural_language` 使用现有 strict labelled-field parser（只接受 renderer 的精确 11-field 格式），对 JSON/AC1/AC2/AWP1 按显式类型调用各自 strict parser，成功后统一构造完整规范 JSON model context；对 `natural_language_freeform` 只原样 pass-through 并返回 transport 文本，不解析、不提供规范 model_context。任何结构化/labelled 格式错时不猜别的 decoder、不回显正文。Model-direct builder 接收同一 domain 的共同 task_prompt、上下文、格式和 payload，为 compact formats 附上各自固定可读说明，并按需附完整 glossary，但不发送、不创建 client、不声称理解。无缓存假设：协议说明或 glossary 每次重复时都逐次计入。
- [ ] **Step 4：验证 GREEN。** 重跑同一命令；使用合成 sentinel 断言无 URL/file/command 执行；`natural_language` renderer 输出必须严格解析回同 domain 的完整 MessageV1，labelled-field 错序/缺标签拒绝；`natural_language_freeform` 保持原文 pass-through 且不产生规范 model_context；所有成功 wire/labelled-field 格式交给模型的规范 JSON context 字段完全相同；词典不匹配时 fail-closed，消息原文和 glossary 不进入错误字符串。
- [ ] **Step 5：自审并提交。** 仅提交 gateway、prompts 和测试文件，提交信息 `feat: add local AgentWire receive modes`。

