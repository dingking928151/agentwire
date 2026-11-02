### Task 1：锁定身份、能力协商与稳定失败契约

**Files:** 新增 `agentwire/__init__.py`、`agentwire/contracts.py`、`tests/test_agentwire_contracts.py`。

**Interfaces:** `DictionaryIdentity`、`BenchmarkCandidate`、`ReceiverCapabilities`、`NegotiationResult`、`GatewayResult`、`GatewayDelivery`、`AgentWireError`、`dictionary_identity_for`、`AgentWireReasonCode`，签名按共享接口定义。

- [ ] **Step 1：写失败测试。** `test_dictionary_identity_hashes_full_canonical_snapshot` 用手写 canonical JSON 字节验证 SHA-256，且只改一个 alias 就改变 hash；`test_mutated_dictionary_is_rejected_before_hashing` 在构造后篡改嵌套 alias 并验证深快照 strict 重构拒绝；`test_capability_contract_rejects_duplicate_or_invalid_versions` 验证 frozen/strict 字段、重复版本和正整数容量；`test_results_enforce_success_failure_invariants` 验证不允许 supported+reason 或 failure+payload；`test_gateway_delivery_enforces_decoded_fallback_and_pass_through_states` 验证 GatewayDelivery 三态字段互斥且 pass-through 原文完整保留。
- [ ] **Step 2：验证 RED。** 运行 `.venv/bin/python -m pytest tests/test_agentwire_contracts.py -q`；预期仅因新模块/API 不存在或行为未实现而失败，不以依赖/环境错误作为 RED。
- [ ] **Step 3：实现契约。** 在 `contracts.py` 定义 strict、`extra="forbid"` 的 Pydantic 类型、精确 hash 函数和稳定 reason code；协商只检查声明，不做网络请求、认证或隐式兼容。
- [ ] **Step 4：验证 GREEN。** 重跑同一命令；增加 hash 对象键排序、别名完整纳入、Unicode 保留、负/零容量拒绝、unknown version 不猜测等断言。
- [ ] **Step 5：自审并提交。** 仅提交本任务三文件，提交信息 `feat: define AgentWire v1 contracts`；确认新导入没有 tokenizer/I/O 副作用。

