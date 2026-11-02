# AgentWire Compact Profile v1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.
>
> **状态：设计与本实施计划已获用户批准（2026-10-24）。** Task 1 已实现并通过两轮独立定向复审；后续任务按批准范围逐项推进。

**Goal:** 在不改变现有 AC1/AC2 行为的前提下，实现公开、可逆、fail-closed 的 AWP1 消息 profile，并提供严格本地 gateway / model-direct 文本成本评测；将固定 11-field labelled serialization 与逐条人工撰写的 freeform natural language 作为不同候选和基线指标。

**Architecture:** 新增独立 `agentcodec.agentwire` 包，复用 `MessageV1`、现有严格 JSON/AC renderer 和已验证的本地 tokenizer 计量能力。确定性 gateway 与 model-direct 提示/计量分开；benchmark 将固定 labelled-field serialization 与人工 freeform prose 分列，gateway 对 freeform 仅 pass-through 并计传输成本，不解析或构造规范 model_context；model-direct 计完整输入与合成响应成本，不调用任何接收模型。

**Tech Stack:** 现有 Python 3.11+、Pydantic 2、Typer、pytest/Hypothesis 和标准库 `json`/`hashlib`；零新增依赖。tokenizer 只从本地已配置缓存读取。

**Spec:** `docs/superpowers/specs/2026-10-22-agent-communication-protocol-design.md`（执行者须先读全文；设计为唯一技术边界）。

## Global Constraints

- 本计划已获用户单独批准（2026-10-24）。仍不接真实模型/API/服务、不获取凭据、不外发数据、不安装或下载依赖。
- 工作区固定 `/workspace/agentcodec/.worktrees/agentcodec-agent-communication-design`，本地分支 `agentcodec-agent-communication-design`，计划基线 `7cac6e05de05f859280492c6350df26720bffc3e`；只在该分支本地工作，不 merge、push、publish 或 deploy。
- 新 wire 仅为 `AWP1|` 加严格 JSON 二元数组 `[dictionary_identity_or_null, message_values]`；identity 仅可为 `null` 或三项数组 `[user_id, dictionary_version, lowercase_sha256]`，不得序列化成自由字段对象。后者严格是 `MessageV1` 的 11 项固定顺序。不得修改 `MessageV1`、AC1/AC2 wire、旧 decoder、旧默认值或既有 benchmark/evaluation 报告契约。
- `message_type` 固定映射 `delegate→D`、`status→S`、`result→R`；`status` 固定映射 `pending→P`、`running→R`、`blocked→B`、`completed→C`、`failed→F`、`cancelled→X`。不得按相似词推断或静默升级版本。
- null、false、空串、空列表、列表顺序/重复及 Unicode 原样保留；任何字段缺失、多余、类型不符、未知码、坏引用或身份不匹配都整体拒绝，不交付半解码 payload。
- 词典只可在显式选择、精确 user/version/hash 匹配时，对 `task`、`blocker`、`next_step`、`evidence`、`constraints` 五个字段中的完整字符串值或完整列表项替换为类型化 `{"$ref":"code"}`；不替换 ID、枚举、`approval_required`、字段名或子串，不自动学习/查找。`dictionary_identity` 必须与消息 `user_id` / `dictionary_version` 和本地被选词典三者精确匹配。SHA-256 是 canonical dictionary 内容标识，不是身份认证、授权或匿名化。
- 字典 identity 必须对应已确认选择；codec/encoder 绝不把 dictionary_version 为空的源 MessageV1 自动补成某版本。词典候选仅在调用方显式选择后建立独立 transport copy：源 `dictionary_version` 为 null 时，benchmark/helper 的新副本可记录所选版本；源已有非空版本时原样保留，身份不匹配则拒绝。编码器自身不得修改输入消息或静默改写其元数据。
- 所有评测数据使用本计划新增的合成样例；不得查看、复制或将原始语料/既有词典内容放入计划、实现子代理 brief、审查包或报告。测试样例须在测试代码中手写并可审计。
- 不调用真实模型或 provider；gateway 对结构化/labelled-field 候选只在进程内做确定性解码，对 freeform prose 仅 pass-through，不解析、不构造规范 model_context。model-direct 只构造并计量完整文本，不发送请求，不代表模型理解或任务成功。fallback/retry 仅作为本地明确标注的成本模拟，不自动重发、不执行消息。
- 计量只称本地 tokenizer ordinary-text 估计；缺少精确 tokenizer、完整请求上下文或复用边界时输出 `cannot_compare`，不得用字符/字节猜 token 或补零；不得下载词表或安装依赖。
- AWP1 能力协商不是认证或执行授权；状态、`approval_required`、证据、命令和词典码永不授权工具执行。源自然语言由调用方保留，解码失败时本协议不自行恢复或发送它。

## Review Focus

1. **11 项数组的错位、缺项/多项或 JSON 重复 key：** 必须拒绝且不返回部分消息。测试归 Task 2。
2. **`null`、`false`、空值、重复列表项、尾空格、Unicode 分隔符及字符串内 `|`：** 对有定义的 wire/labelled-field 解码候选，必须保持字段级完全相等，不能靠分隔符切分；`natural_language_freeform` 的 strict/full-field equality 为 `not_applicable`。测试归 Task 2。
3. **字典 hash、user/version、`$ref` 缺失/未知/伪造或只解出部分字段：** 必须整体 fail-closed；null identity 下不得展开引用。测试归 Task 1–3。
4. **未知 profile/schema/enum、消息超最大字节数或接收能力/词典不匹配：** 返回固定 reason code，不得输出消息或把消息交给 Agent。测试归 Task 1–3。
5. **gateway 展开后模型上下文、model-direct 的共同任务提示与每格式说明/词典 glossary、cold/ready/unknown setup、回复、重试与回退：** 必须按完整事件计量；gateway 的短传输不能被报告成模型上下文节省。freeform gateway 仅报告 pass-through transport 成本，不解析且无规范 model_context；未知成本不得伪装为零。测试归 Task 3–5。

## 审批与执行方式

- [ ] 本计划获用户另行批准后才启动 Task 1；当前阶段仅计划文件新增，不提交代码、不运行测试。
- [ ] 按用户指定的 subagent-driven 流程逐任务串行执行：每项由新的实现子代理完成 RED→GREEN→自审和本地任务提交，再由独立审查子代理做规格/质量审查；有问题先修复并定向复审。接口有依赖，禁止并行修改共享文件。
- [ ] 每个子代理只获其任务所需的合成测试、源代码和接口说明；不向任何子代理提供原始语料/词典内容。每任务记录精确提交、失败/通过命令及独立审查结论；环境依赖缺失不算 RED，不安装或下载来掩盖失败。
- [ ] 所有任务后由未参与实现的独立审查者审查本分支相对计划基线的完整差异。保留本地工作树及分支，不 merge/push。

## 文件地图与依赖

| 文件 | 单一职责 | 任务 |
| --- | --- | --- |
| `src/agentcodec/agentwire/__init__.py` | 新 profile 的窄公共导出，不导入 tokenizer 或执行 I/O | 1 |
| `src/agentcodec/agentwire/contracts.py` | 严格身份、能力、协商结果及稳定错误码 | 1 |
| `src/agentcodec/agentwire/codec.py` | AWP1 envelope 编码、全量解码和精确词典引用 | 2 |
| `src/agentcodec/agentwire/gateway.py` | 纯本地确定性接收/解码门面 | 3 |
| `src/agentcodec/agentwire/prompts.py` | model-direct 固定说明、枚举表和 glossary 拼装 | 3 |
| `src/agentcodec/agentwire/costs.py` | 完整文本事件、token/byte 归因、严格 break-even | 4 |
| `src/agentcodec/agentwire/benchmark.py` | 原有 8 个候选及独立 freeform baseline、两种接收模式与确定性报告 | 5 |
| `src/agentcodec/cli.py` | 仅注册新的 opt-in `benchmark-agentwire` 命令 | 6 |
| `bench/agentwire/exchanges.v1.jsonl` | 新增少量手写合成消息/合成回复配对 | 5 |
| `bench/agentwire/dictionary.v1.json` | 与合成 user namespace 匹配的手工测试词典 | 5 |
| `tests/test_agentwire_contracts.py` | hash、能力和协商边界 | 1 |
| `tests/test_agentwire_codec.py` | AWP1 golden、完整往返及 fail-closed 矩阵 | 2 |
| `tests/test_agentwire_modes.py` | gateway 与 model-direct 本地文本构建 | 3 |
| `tests/test_agentwire_costs.py` | 事件计量、未知成本、严格 break-even | 4 |
| `tests/test_agentwire_benchmark.py` | 条件矩阵、完整成本、重复/回退计账 | 5 |
| `tests/test_agentwire_cli.py` | 新命令参数、JSONL I/O 与版本化输出 | 6 |
| `tests/test_agentwire_compatibility.py` | 旧 AC1/AC2/CLI/API 不回归 | 7 |
| `docs/agentwire-compact-profile-v1.md` | 对外固定 wire、枚举、身份、协商及安全规则 | 7 |
| `README.md` | 新 profile/benchmark 用法及证据边界 | 7 |

依赖顺序：`1 → 2 → 3 → 4 → 5 → 6 → 7`。成本/benchmark 实现只复用 `agentcodec.adaptive.meter.measure_attempts` 等本地计数入口，不修改 adaptive/evaluation 的现有契约；benchmark 的新报告独立标版。既有 `decode_payload` 不识别 AWP1，调用方必须显式选择新 API。

## 共享接口

- `DictionaryIdentity(user_id: Identifier, version: Identifier, sha256: Sha256Hex)`；`dictionary_identity_for(dictionary: UserDictionary) -> DictionaryIdentity`。hash 输入固定为完整 `UserDictionary.model_dump()` 的 UTF-8 JSON，`ensure_ascii=False`、sorted keys、紧凑分隔符、无末尾 LF；调用前递归快照并以 strict schema 重新构造现有模型，拒绝构造后的嵌套 mutation；只接受 64 位小写十六进制 hash。
- `ReceiverCapabilities(user_id: Identifier, supported_profile_versions: tuple[str, ...], supported_schema_versions: tuple[str, ...], supported_enum_table_versions: tuple[str, ...], max_message_bytes: PositiveInt, dictionary_identity: DictionaryIdentity | None)`；model strict/frozen/extra-forbid，版本 tuple 禁重复。v1 精确版本标识为 profile `AWP1`、schema `MessageV1`、enum table `AWP1`；字段用于显式声明能力，不是认证。
- `AgentWireReasonCode = Literal["unsupported_profile", "unsupported_schema", "unsupported_enum_table", "receiver_user_mismatch", "message_too_large", "invalid_envelope", "unknown_enum", "dictionary_missing", "dictionary_identity_mismatch", "unknown_alias", "invalid_message"]`；`AgentWireError(reason_code: AgentWireReasonCode)` 暴露稳定固定码，不在异常文本里回显 payload、路径、用户原文或词典内容。
- `NegotiationResult(status: Literal["supported", "fallback_required"], reason_code: AgentWireReasonCode | None)`；`supported` 当且仅当 reason_code 为 null。`GatewayResult(status: Literal["decoded", "fallback_required"], message: MessageV1 | None, reason_code: AgentWireReasonCode | None)`；成功当且仅当 message 非空且 reason_code 为 null，失败当且仅当 message 为空且 reason_code 非空。
- `BenchmarkCandidate = Literal["natural_language", "natural_language_freeform", "json", "ac1", "ac1_dictionary", "ac2", "ac2_dictionary", "awp1", "awp1_dictionary"]`。原有八个规范候选 ID 保持不变；`natural_language` 明确仅指现有 `render_natural_language(domain_message)` 生成的固定 11-field labelled serialization，新增 `natural_language_freeform` 是独立的人工 prose 基线，不是协议候选别名。`awp1` 表示无字典 AWP1，`awp1_dictionary` 表示显式选择字典的 AWP1。可用条件按 metadata domain 显式 allowlist；不可用条件只标 `not_applicable`，绝不拿来参与最小成本选择。Freeform 文本仅用于 source domain，逐条人工撰写并与同一 `task_prompt` 和 source gold MessageV1 配对；另手工标注关键语义及硬约束。selected_dictionary_transport 中 freeform 不表达新增的 transport `dictionary_version`，其比较为 `not_applicable`。标注不构成解析或模型质量评估。
- `GatewayDelivery(candidate_id: BenchmarkCandidate, decode_status: Literal["decoded", "fallback_required", "pass_through"], model_context: str | None, pass_through_text: str | None, reason_code: AgentWireReasonCode | None)`：`decoded` 仅适用于 `natural_language` 的 strict labelled-field parser 或 JSON/AC1/AC2/AWP1 strict parser，须完整得到 MessageV1 并将该同域消息的规范 JSON 作为 model_context，且 pass_through_text/reason_code 为 null；`fallback_required` 时 model_context/pass_through_text 必须为 null 且只返回固定 reason code；`pass_through` 仅适用于 `natural_language_freeform`，原样回传输入文本，model_context 与 reason_code 必须为 null，不做解析或规范化。这个类型只用于本地评测适配，不接收或调度 Agent。
- `encode_awp1(message: MessageV1, dictionary: UserDictionary | None = None) -> str`；`decode_awp1(payload: str, receiver: ReceiverCapabilities, dictionary: UserDictionary | None = None) -> MessageV1`。解码器先以 receiver 上限检查实际 UTF-8 byte length，再 JSON parse；超限时不得建立完整 JSON 树。失败通过 `AgentWireError` 表示；编码不改输入，解码仅在所有字段和身份检查通过后返回完整消息。无 dictionary identity 时不替换 `$ref`，原消息 `dictionary_version` 原样保留；非空 metadata 不代表当前显式选用字典。
- `decode_at_gateway(payload: str, receiver: ReceiverCapabilities, dictionary: UserDictionary | None = None) -> GatewayResult`：仅 AWP1 入口，结果只含完整 `MessageV1` 或 `fallback_required`/reason code；失败时 message 为空，调用方原文不由 gateway 猜测或重发。
- `deliver_benchmark_at_gateway(candidate_id: BenchmarkCandidate, payload: str, receiver: ReceiverCapabilities, dictionary: UserDictionary | None = None) -> GatewayDelivery`：只用于离线评测。candidate_id 唯一决定 parser、pass-through 行为与是否启用词典；`*_dictionary` 必须收到精确匹配的 dictionary/receiver identity，非 dictionary candidate 必须 dictionary=None。`natural_language` payload 由该 metadata domain 的 MessageV1 经现有 renderer 生成，再严格解析；JSON/AC1/AC2/AWP1 同样按 candidate_id 指定格式 strict parse。`natural_language_freeform` 原样 pass-through，不解析、不提供规范 model_context；benchmark 对它只计 transport 成本。结构化/labelled-field 成功后才可交同域规范 JSON model_context；失败只返回固定 reason，不回显正文或自动 fallback。不声称模型理解。
- `ReuseScenario(unit: Literal["same_message_instance", "whole_corpus_pass"], total_uses: PositiveInt)`：`same_message_instance` 的 `total_uses` 含当前这条在内，必须是完全相同的序列化消息；`whole_corpus_pass` 的 `total_uses` 是相同固定合成集合完整重跑的次数。两种单位分别出分母，不互相换算。
- `MetadataDomain = Literal["source", "selected_dictionary_transport"]`；`TransportMessage(message: MessageV1, domain: MetadataDomain, selected_dictionary_identity: DictionaryIdentity | None)`；`prepare_transport_message(source: MessageV1, domain: MetadataDomain, dictionary: UserDictionary | None) -> TransportMessage`：source domain 原样保留 `dictionary_version` 并禁止选择字典；selected transport domain 要求明确 dictionary，source version 为 null 时只在新 transport copy 中设为 dictionary.version，已有非空 version 必须严格匹配否则拒绝。始终保留 immutable source 原件供独立 source 判分，不在 codec/encoder 中改 metadata。
- `DictionaryExposure = Literal["none", "cold_once_per_scope", "ready_with_complete_history", "history_unknown"]`。Cold gateway 场景要求完整 canonical dictionary+identity 同步文本事件，首次 scope 只算一次；ready 场景必须携带同身份、同 profile 的完整 setup 历史，生命周期成本计入历史 setup，边际成本另列；history_unknown 不假设免费或已可解码，生命周期/route 比较为 `cannot_compare`。Model-direct 仍在每次 request 的模型输入里包含所选完整 glossary；不因 gateway 词典 ready 而免计。
- `ProtocolExposure = Literal["none", "out_of_band_preinstalled", "cold_text_setup_once_per_scope", "ready_with_complete_history", "history_unknown"]`；`none` 只用于不需额外 profile setup 的 baseline；`out_of_band_preinstalled` 只能表示 decoder 以本地代码预装、不发生文本 setup event，非 token 安装成本不在此 benchmark 估值；cold/ready 必须有实际、完整 profile/grammar setup 文本事件和同 identity/profile 的历史；history_unknown 时该候选的 lifecycle total/break-even 为 `cannot_compare`。Model-direct 的格式说明仍每次加入该候选的 `model_input`，不得因代码预装而省略。
- `build_direct_model_request(context_prefix: str, candidate_id: BenchmarkCandidate, wire_payload: str, dictionary: UserDictionary | None = None) -> str`：构造一份完整 model-direct 输入，含该 case 的完整、固定 `context_prefix`（系统/开发者/共同 task_prompt 及已声明上下文）、candidate 对应格式说明（`natural_language` 与 `natural_language_freeform` 均不额外加协议说明；JSON/AC1/AC2/AWP1 使用各自固定说明）、完整 payload，以及且仅当 candidate_id 是 `*_dictionary` 时附上被选词典的完整可读 glossary；词典 candidate 必须显式传入并验证 dictionary。每次请求都重建，不假定隐式提示缓存。不连接模型。首版 benchmark 使用独立请求快照，不累积前轮 transcript；不得把它外推为多轮对话总成本。model-direct 的每候选成本另计完整共同提示、上下文和对应 `synthetic_response_fixture`。
- `CostPart(label: Literal["setup", "negotiation", "protocol_instructions", "context", "dictionary_glossary", "dictionary_transfer", "payload", "response", "retry", "fallback", "other"], text: str)`；按 `label` 映射到 meter 的已有 `ComponentKind`：setup/negotiation/dictionary_transfer→setup，protocol_instructions/context/dictionary_glossary→context，payload→payload，retry→retry，fallback→fallback，response/other→other。
- `CostEvent(event_id: str, kind: Literal["setup", "negotiation", "dictionary_distribution", "request", "response", "retry", "fallback"], simulated: Literal[True] = True, surfaces: tuple[Literal["transport", "model_input", "model_output"], ...], parts: tuple[CostPart, ...], multiplicity: PositiveInt = 1)`。全部事件固定为本地模拟。转换为现有 `TextAttempt(event_id, attempt_kind, components, simulated=True, multiplicity)`；`attempt_kind` 唯一由 `kind` 推导：setup/negotiation/dictionary_distribution→setup，request→initial，response→other，retry→retry，fallback→fallback。part 以 `TextComponent(kind, text, resource_identity=None)` 表示，不实现第二套 tokenizer。同一 event ID 的重复仅允许所有字段及完整 UTF-8 文本完全相同；不同 event ID 的同文本请求仍分别计费。
- `ProtocolState(candidate_id: BenchmarkCandidate, exposure: ProtocolExposure, history: tuple[CostEvent, ...])`：逐候选声明协议/profile 的初始暴露方式与完整历史。每个 candidate 在一个 receiver/user/domain/profile scope 内最多一个 state；缺项不得默认成免费，history_unknown 不得升级为 ready。
- `measure_cost_events(events: tuple[CostEvent, ...], profile: TokenizerProfile) -> CostSummary`；通过 `agentcodec.adaptive.meter.measure_attempts` 对各个完整独立事件精确计数，part 的 token attribution 沿用计量器的前缀 delta（可负，和严格等于完整事件 token）。输出 `transport_bytes`、`transport_tokens`、`model_input_tokens`、`model_output_tokens`、`distinct_text_event_tokens` 和按 event kind/part label 的本地计数；setup/retry/fallback 事件也走同一真实文本计量。当前离线样例的 `model_output_tokens` 仅来自 `synthetic_response_fixture`，报告必须标 `response_source=synthetic_fixture`，不得写成模型实际回复或输出观测。一个多 surface 事件的完整文本总量只加一次；transport/model 分面可重叠但不再相加成第二个总数。它不是 provider billed tokens。
- `calculate_break_even(candidate_setup: NonNegativeInt, candidate_per_message: NonNegativeInt, baseline_setup: NonNegativeInt, baseline_per_message: NonNegativeInt) -> BreakEvenResult`：求最小正整数 N，使候选完整累计成本严格低于同接收模式、同域、同计量面基线；分别以 `natural_language` labelled-field 与 `natural_language_freeform` 为基线报告，不合并二者。Gateway 只允许可比的 transport 成本对比，不对 freeform 声称模型上下文/端到端等价；无解时返回 `never`，必要计量未知或计量面不适用时返回 `cannot_compare`。

---

### Task 1：锁定身份、能力协商与稳定失败契约

**Files:** 新增 `agentwire/__init__.py`、`agentwire/contracts.py`、`tests/test_agentwire_contracts.py`。

**Interfaces:** `DictionaryIdentity`、`BenchmarkCandidate`、`ReceiverCapabilities`、`NegotiationResult`、`GatewayResult`、`GatewayDelivery`、`AgentWireError`、`dictionary_identity_for`、`AgentWireReasonCode`，签名按共享接口定义。

- [x] **Step 1：写失败测试。** `test_dictionary_identity_hashes_full_canonical_snapshot` 用手写 canonical JSON 字节验证 SHA-256，且只改一个 alias 就改变 hash；`test_mutated_dictionary_is_rejected_before_hashing` 在构造后篡改嵌套 alias 并验证深快照 strict 重构拒绝；`test_capability_contract_rejects_duplicate_or_invalid_versions` 验证 frozen/strict 字段、重复版本和正整数容量；`test_results_enforce_success_failure_invariants` 验证不允许 supported+reason 或 failure+payload；`test_gateway_delivery_enforces_decoded_fallback_and_pass_through_states` 验证 GatewayDelivery 三态字段互斥且 pass-through 原文完整保留。
- [x] **Step 2：验证 RED。** 使用既有虚拟环境运行 `.venv/bin/python -m pytest tests/test_agentwire_contracts.py -q`；预期仅因新模块/API 不存在或行为未实现而失败，不以依赖/环境错误作为 RED。
- [x] **Step 3：实现契约。** 在 `contracts.py` 定义 strict、`extra="forbid"` 的 Pydantic 类型、精确 hash 函数和稳定 reason code；协商只检查声明，不做网络请求、认证或隐式兼容。
- [x] **Step 4：验证 GREEN。** focused 契约测试 6 passed；最终全套测试 1,661 passed。新增 canonical JSON `model_context` 校验及验证错误输入隐藏，所有关键边界测试通过。
- [x] **Step 5：自审并提交。** 仅提交本任务三文件，确认新导入没有 tokenizer/I/O 副作用；实现提交 `11159a3`，两轮窄范围修复提交 `28b91d5`、`1c329e2`。
- [x] **Step 6：独立复审。** 两轮修复均由独立 reviewer 定向复审通过；未遗留 Important 问题，分支仍未推送/合并。

### Task 2：实现 AWP1 严格编解码

**Files:** 新增 `agentwire/codec.py`、`tests/test_agentwire_codec.py`。

**Interfaces:** 消费 Task 1 合同；实现 `encode_awp1(message, dictionary=None) -> str`、`decode_awp1(payload, receiver, dictionary=None) -> MessageV1`、`negotiate_awp1(receiver, payload, dictionary=None) -> NegotiationResult`。协商与 decoder 共用同一 strict parser，不能接受调用者伪造的已解析消息、identity 或 byte count。

- [x] **Step 1：写失败测试。** 手写独立 11 字段 fixture 与固定 wire golden。`dictionary_identity_or_null` 必须为 `null` 或 `[user_id,version,sha256]` 三项数组；拒绝自由字段对象、错长度和非小写/非 64 hex hash。`test_awp1_golden_round_trip_preserves_all_eleven_fields` 断言全字段往返严格相同；`test_dictionary_identity_and_whole_value_refs_round_trip` 验证仅五个批准文本字段的完整字符串/列表项可替换。`test_decoder_enforces_actual_utf8_byte_limit_before_json_parse` 以多字节 Unicode 验证 parse 前按实际 bytes 拒绝超限；`test_bad_envelope_or_any_bad_field_fails_closed` 覆盖错前缀、重复 object key、JSON 非有限值、top-level/二元 envelope/11 项数组与 identity 结构错误、未知枚举（包括两表同码不串表）、错类型/身份/hash/namespace/version、缺失/未知引用和 lone surrogate，失败没有 payload。`test_dictionary_version_mismatch_rejects_without_metadata_rewrite` 验证 codec 不替 transport message 改 metadata；`test_negotiation_reports_wire_or_receiver_mismatch_without_payload` 覆盖不支持 profile/schema/enum、receiver user mismatch、字典缺失/身份冲突、超限和坏消息；`test_negotiation_uses_wire_identity_and_actual_size_only` 验证不接受调用方伪造解析内容、身份或 byte count；`test_unselected_dictionary_version_is_preserved_without_alias_expansion` 验证没有显式 dictionary 时保留已有版本且不展开引用。
- [x] **Step 2：验证 RED。** 运行 `.venv/bin/python -m pytest tests/test_agentwire_codec.py -q`，确认目标接口/断言失败。
- [x] **Step 3：实现最小 codec。** Wire 固定为 `AWP1|` 加 `[dictionary_identity_or_null, message_values]`；身份为 null 或 `[user_id,version,sha256]`。`message_values` 按 spec 的 11 个字段顺序，JSON 字符串负责分隔符/引号/换行/Unicode。显式 dictionary 编码要求输入 transport MessageV1 的 `user_id` 和 `dictionary_version` 已与 dictionary 精确匹配；缺版本/冲突时拒绝，由单独的 transport helper 建立新副本，codec 不改输入或自动改 metadata。字典 identity 未选时不展开任何 `$ref`，即使消息保留非空 dictionary_version。完整 decode 后才严格 `MessageV1.model_validate(..., strict=True)`；既有 AC codec 不改。能力上限始终由 decoder 自己以实际 payload UTF-8 长度执行。
- [x] **Step 4：验证 GREEN 与边界。** 重跑同一命令；增加 null/false/空串/空列表、列表顺序/重复、尾空格、`|`/逗号/括号/换行、Unicode 分隔符、对象非精确 `$ref` 形状、null identity 下出现 ref 和字典输入不被变更等测试。
- [x] **Step 5：自审并提交。** 仅提交 codec 与测试文件，提交信息 `feat: add strict AWP1 codec`；确认任何未知值都整体拒绝，无半解码返回。

- [x] **Step 6：独立复审。** Reviewer 判定 Spec Compliance ✅、Task quality Approved；无 Critical、Important、Minor finding。测试运行结果按实现者报告记录，独立 reviewer 未重跑。
### Task 3：实现 gateway 与 model-direct 文本构造

**Files:** 新增 `agentwire/gateway.py`、`agentwire/prompts.py`、`tests/test_agentwire_modes.py`。

**Interfaces:** 消费 Task 1–2；实现 `decode_at_gateway`、benchmark-only `deliver_benchmark_at_gateway` 和 `build_direct_model_request`。

- [x] **Step 1：写失败测试。** `test_gateway_returns_only_complete_message_or_fallback` 验证 AWP1 成功完整解码及任一字段错误时 `message is None`；`test_gateway_decodes_exact_domain_natural_language_renderer` 验证 `natural_language` 输入由同一 domain MessageV1 经 renderer 生成并 strict parse 成相同完整实例；`test_gateway_passes_freeform_through_without_parsing_or_model_context` 验证 `natural_language_freeform` 原文仅 pass-through、无规范 model_context 且不触发 parser；`test_gateway_rejects_out_of_order_or_missing_labels` 验证 labelled-field 错序/缺字段不会被猜测；`test_gateway_decodes_only_declared_json_ac1_ac2_and_awp1` 验证其他格式按显式类型使用 strict parser，未知/格式错时不猜另一 decoder；`test_gateway_does_not_auto_fallback_or_execute` 验证错误只返回 reason code，不调用网络、subprocess 或工具；`test_direct_request_keeps_common_task_prompt_in_every_format` 验证同一任务提示在每个 model-direct 条件中原样出现；`test_direct_request_uses_format_specific_instructions_and_full_glossary` 验证 AC1/AC2/AWP1 各自完整固定说明、两张 AWP1 enum 表和所选词典的完整 glossary 都在请求中；`test_natural_language_direct_requests_add_no_compact_protocol_legend` 验证两种自然语言候选均不携带短码说明。
- [x] **Step 2：验证 RED。** 运行 `.venv/bin/python -m pytest tests/test_agentwire_modes.py -q`，确认只因新增接口/行为失败。
- [x] **Step 3：实现纯本地适配。** AWP1 gateway 仅调用严格 decoder 并映射固定 fallback reason；benchmark-only gateway dispatcher 对 `natural_language` 使用现有 strict labelled-field parser（只接受 renderer 的精确 11-field 格式），对 JSON/AC1/AC2/AWP1 按显式类型调用各自 strict parser，成功后统一构造完整规范 JSON model context；对 `natural_language_freeform` 只原样 pass-through 并返回 transport 文本，不解析、不提供规范 model_context。任何结构化/labelled 格式错时不猜别的 decoder、不回显正文。Model-direct builder 接收同一 domain 的共同 task_prompt、上下文、格式和 payload，为 compact formats 附上各自固定可读说明，并按需附完整 glossary，但不发送、不创建 client、不声称理解。无缓存假设：协议说明或 glossary 每次重复时都逐次计入。
- [x] **Step 4：验证 GREEN。** 重跑同一命令；使用合成 sentinel 断言无 URL/file/command 执行；`natural_language` renderer 输出必须严格解析回同 domain 的完整 MessageV1，labelled-field 错序/缺标签拒绝；`natural_language_freeform` 保持原文 pass-through 且不产生规范 model_context；所有成功 wire/labelled-field 格式交给模型的规范 JSON context 字段完全相同；词典不匹配时 fail-closed，消息原文和 glossary 不进入错误字符串。
- [x] **Step 5：自审并提交。** 仅提交 gateway、prompts 和测试文件，提交信息 `feat: add local AgentWire receive modes`。

- [x] **Step 6：独立复审与 fix round 1 闭环。** 初审 finding 已修复；新一轮 scoped reviewer 判定原 Important 已闭环，无 Critical/Important/Minor。

### Task 4：实现完整事件成本与严格 break-even

**Files:** 新增 `agentwire/costs.py`、`tests/test_agentwire_costs.py`；按需在 `agentwire/contracts.py` 增加成本结果类型。

**Interfaces:** 消费共享 `CostEvent` 定义和现有 `TokenizerProfile`；实现 `measure_cost_events`、`calculate_break_even`。

- [x] **Step 1：写失败测试。** `test_complete_request_response_and_setup_are_counted_once` 断言每个不同完整文本事件经现有 meter 进入总量、model input/output 分面按 surface 归因；`test_cost_events_convert_to_existing_text_attempts_without_heuristics` 验证 component kind、由 event kind 推导的 attempt kind、simulated 和 multiplicity 精确映射；`test_protocol_and_dictionary_setup_histories_are_independent_events` 验证两种 setup 有各自文本、身份和历史，不能互相抵扣；`test_direct_request_can_have_two_surfaces_without_double_total` 验证同一事件的 transport/model-input 分面重叠但唯一事件总量不重复；`test_same_id_with_changed_text_or_surface_is_rejected` 拒绝事件 ID 的任何字段冲突；`test_break_even_is_reported_separately_for_each_baseline` 覆盖分别相对 labelled-field 与 freeform 基线计算、相等不算赢、N=1、较大 setup 后首次胜出和永不胜出；`test_negative_costs_and_zero_reuse_are_rejected` 验证成本非负、复用次数为正；`test_missing_tokenizer_returns_cannot_compare_not_zero` 断言无 tokenizer 时成本为 null/cannot_compare。
- [x] **Step 2：验证 RED。** 运行 `.venv/bin/python -m pytest tests/test_agentwire_costs.py -q`，确认业务断言失败。
- [x] **Step 3：实现计量。** 完整 request 作为一个完整事件文本计数；protocol setup/negotiation、dictionary distribution、request、response、retry、fallback 各自成完整独立事件，历史与当前 setup 分账。复用 `agentcodec.adaptive.meter.measure_attempts` 的本地精确 tokenizer 路径，不新建 tokenizer 下载/字符估算。未知输入返回 cannot_compare。break-even 用整数公式计算最小 N，分别与同域、同接收模式、同计量面下的 labelled-field 和 freeform 基线比较；gateway freeform 只参与 transport 成本对比，不生成 model-context/端到端 break-even。
- [x] **Step 4：验证 GREEN。** 重跑同一命令；用测试专用 fake meter 只验证归因/聚合逻辑（不得进入生产实现），再测试 `MeasurementUnavailable`、响应成本、负 token delta 的分项解释和重复事件 multiplicity。
- [x] **Step 5：自审并提交。** 仅提交成本实现/结果类型和测试，提交信息 `feat: account for complete AgentWire text costs`。

- [x] **Step 6：独立审查与 fix round 1 闭环。** 空 parts Important 和 None break-even 测试 Minor 均已解决，定向复审通过，无新破坏。

### Task 5：实现双模式配对 benchmark 与合成样例

**Files:** 新增 `agentwire/benchmark.py`、`bench/agentwire/exchanges.v1.jsonl`、`bench/agentwire/dictionary.v1.json`、`tests/test_agentwire_benchmark.py`。

**Interfaces:** 消费 Tasks 1–4；`BenchmarkCase(case_id, task_prompt, source_message, freeform_text, freeform_annotations, synthetic_response_fixture)`；其中 `freeform_text` 为逐条人工撰写的自然语言，`freeform_annotations` 手工列出关键语义与硬约束；二者与同一 `task_prompt` 及 gold `source_message` 配对。`BenchmarkScenario(scenario_id, scope_id, corpus_id, metadata_domain, receiver_capabilities, protocol_states: tuple[ProtocolState, ...], dictionary_candidate_exposure: DictionaryExposure, dictionary_history: tuple[CostEvent, ...], cases: tuple[BenchmarkCase, ...], reuse: ReuseScenario)`；`run_agentwire_benchmark(scenarios, tokenizer_profiles, dictionary=None) -> AgentWireBenchmarkReport`。每个 scenario 固定一个 receiver/user、metadata domain、字典 identity/exposure 与 corpus；不同 tokenizer 为独立 scope/report group。source domain 保持原消息 metadata、不选择词典，candidate allowlist 为 `natural_language/natural_language_freeform/json/ac1/ac2/awp1`；selected_dictionary_transport 使用 benchmark-only `prepare_transport_message` 显式建立 transport copy，source 原件保留，所有候选都以该域的同一个 transport `MessageV1` 为 gold 输入，allowlist 为 `natural_language/json/awp1/ac1_dictionary/ac2_dictionary/awp1_dictionary`；`natural_language_freeform` 不属于该 domain，相关比较为 `not_applicable`，不得借用 source 文本/成本。原有八个候选 ID 不变；`natural_language` 为 labelled-field 基线，新增 `natural_language_freeform` 为单独人工 prose 基线；`awp1` 是无字典候选。每个 domain 的 `natural_language` 文本都从该域实际被候选编码的同一 MessageV1 调用 `render_natural_language` 即时生成；freeform 文本仅在 source domain 使用，必须与 source gold 和同一 task_prompt 配对，不能由 renderer 自动生成。selected-domain 的 `dictionary_version` 必须与 transport copy 完全相同，绝不复用 source-domain 文本或成本。AC1/AC2 无词典条件在 selected domain 明确为 `not_applicable`；不得跨域借用字典 setup/成本。source 与 transport hash、成本分母分别报告，禁止横向合并。`same_message_instance` 必须恰好一条 case，复用同一完整 11 字段实例；`whole_corpus_pass` 包含一组固定顺序 cases，每次完整重跑整组。两种单位分开，不跨 scope/user/receiver/domain/dictionary/profile 摊销；固定默认 `total_uses=1/10/50`。

`AgentWireBenchmarkReport(version="agentwire-benchmark-v1", scenarios: tuple[ScenarioReport,...])` 的每个 row 按固定次序包含 synthetic corpus ordinal、tokenizer profile id、receiver mode、`candidate_id`、metric surface、dictionary condition/exposure、reuse unit/total uses、case/event counts、transport bytes/tokens、model input tokens、synthetic response tokens、`distinct_text_event_tokens`、candidate-protocol-exposure/candidate-protocol-setup-history/candidate-protocol-setup/negotiation/dictionary-exposure/dictionary-history/dictionary-setup/request/response/retry/fallback cost、per-use/amortized/lifecycle cost。比较指标分别记录 `vs_natural_language_labelled` 与 `vs_natural_language_freeform` 的 comparison status、适用成本差与 break-even，不得合并成单一自然语言基线；model-direct 比较计入完整共同提示、上下文、payload 与 synthetic response fixture，gateway 对 source-domain freeform 只报告 transport bytes/tokens；model-context/input、model-output/synthetic-response 与端到端比较均为 `not_applicable`。selected_dictionary_transport 的 freeform 比较也全部为 `not_applicable`。strict decode/full-field/hard-field equality 对 `natural_language_freeform` 明确为 `not_applicable`，不得以 0 代替。必要计量未知时对应数值为 null，不能混作 0。报告不含消息/词典/glossary正文、user/scope/场景原始标识、payload 或输入内容 hash；同输入/revision/profile 必须确定性输出。Labelled-field 结果只称 labelled-field 对照，不能称为相对真实自然语言的节省；报告可以诚实显示 AWP1 更贵或不 break even，不设“必须节省”验收门槛。

- [x] **Step 1：写失败测试和合成 fixtures。** `test_candidate_allowlists_are_domain_specific` 验证 source domain 仅有 `natural_language/natural_language_freeform/json/ac1/ac2/awp1`；selected_dictionary_transport 仅有 `natural_language/json/awp1/ac1_dictionary/ac2_dictionary/awp1_dictionary`；`natural_language_freeform` 与相关比较为 `not_applicable`，不得跨域复用 source baseline；AC1/AC2 无词典条件在 selected domain 标 `not_applicable`，不参与 minimum。`test_natural_language_labelled_baseline_uses_same_domain_message` 验证 `natural_language` 每个 domain 都即时调用 `render_natural_language` 于该域候选使用的同一完整 MessageV1；`test_human_freeform_is_paired_and_manually_annotated` 验证每条人工 freeform 与同一 task_prompt、gold MessageV1 配对，且关键语义/硬约束有手工标注而非 renderer 派生。selected copy 的 dictionary_version 必须一致，source 文本不能复用。`test_prepare_transport_message_preserves_source_and_only_changes_selected_copy` 验证 null source version 只在新 selected copy 变成明示版本；`test_non_null_conflicting_dictionary_version_rejects_domain` 验证不会覆盖不匹配版本。`test_gateway_decodes_structured_and_labelled_conditions_to_same_domain_message` 验证该域 labelled-field/JSON/codec 成功后恢复同一 11 字段实例并给同一规范 JSON model context；`test_gateway_freeform_is_transport_only_and_equality_metrics_are_not_applicable` 验证 freeform 仅 pass-through/计 transport，严格解析与字段相等均为 `not_applicable`。`test_direct_mode_counts_common_prompt_context_and_response_fixture_for_freeform` 验证 freeform model-direct 计入完整共同提示、上下文、freeform payload 与 synthetic response fixture；各格式说明和完整 glossary 按每次请求计量；能力声明/协商事件只按实际传送文本归因，未知文本返回 `cannot_compare`。`test_protocol_state_is_candidate_scoped_and_dictionary_history_is_separate` 验证每个候选分别声明协议/profile history；字典 setup 与其分开计入，不能互相抵扣或跨候选假设 ready；`test_same_instance_and_whole_corpus_reuse_have_distinct_denominators` 与 `test_scope_mismatch_never_amortizes_across_users_receivers_domains_or_dictionaries` 验证两种分母分开且跨 namespace、receiver、domain、字典 hash、tokenizer profile 不共享 setup；报告成本、synthetic response、失败/回退计账和脱敏按本节后续用例覆盖。
- [x] **Step 2：验证 RED。** 运行 `.venv/bin/python -m pytest tests/test_agentwire_benchmark.py -q`；预期目标 API/报告断言失败。
- [x] **Step 3：实现合成矩阵。** 新增 20 条手写 synthetic source MessageV1（10 条多样委派/进度消息、10 条高重复流程消息）和仅含合成短语的词典，不引用既有 corpus/dictionary。每条消息配同一个 `task_prompt`、source gold MessageV1、逐条人工撰写且仅用于 source domain 的 `natural_language_freeform` 文本和手工关键语义/硬约束标注；作者逐条人工复核 freeform 是否表达这些标注，但这只是 fixture 内容审校，不是模型理解/任务质量测量。freeform 不由 labelled renderer 生成、不作为 decoder 输入，也不携带 selected transport 专属的 `dictionary_version`。`natural_language` 仍是对每个 domain 的实际候选 MessageV1 即时调用现有 `render_natural_language` 的固定 11-field labelled-field 表达。先由 `prepare_transport_message` 生成 immutable views：source domain 保持 gold 原件；selected_dictionary_transport 只复制并为显式选择的字典设置 dictionary_version。只对 labelled-field/JSON/AC1/AC2/AWP1 等可严格解析的候选按 allowlist 比较同域完整 11 字段：source 用 `natural_language/json/ac1/ac2/awp1`；selected transport 用 `natural_language/json/awp1/ac1_dictionary/ac2_dictionary/awp1_dictionary`；`natural_language_freeform` 仅 source-domain 配对测成本，不做 exact/full-field equality；selected-domain 的 freeform 与其比较均 not_applicable，其余不可用候选亦 not_applicable。selected-domain 的字典版本必须与同域 transport copy 完全一致。Model-direct 对每个同域候选计完整共同 task_prompt、上下文、格式说明（如适用）、payload 和 synthetic response fixture；gateway 对 labelled-field renderer 与其他可用 wire 格式严格解析并在成功后交同域规范 JSON model_context，source-domain freeform 则仅原样 pass-through、只报告 transport bytes/tokens，不解析、不提供规范 model_context，model input/output 与 synthetic response 成本也为 not_applicable；严格 decode/full-field/hard-field equality 均为 not_applicable。selected-domain freeform 和对照全部 not_applicable。自然语言-labelled 与 freeform 两个基线分别报告成本比较与 break-even，不合并；不得把 labelled-field 结果称为真实自然语言节省。任何能力声明/协商仅按确实传送的完整文本计入。未知必要文本或复用范围为 null/cannot_compare。所有适用模式报告 transport bytes/tokens、model input、synthetic response、distinct event total、setup/协商/词典/请求/响应/重试/回退成本及各自基线比较；注明本地 tokenizer 估算不是账单。不得声称模型理解或质量；不发送真实 fallback。两种 reuse unit 单独报告。
- [x] **Step 4：验证 GREEN。** 重跑同一命令；用测试覆盖协议 `out_of_band_preinstalled`、cold 文本 setup、完整 ready history 和 unknown history；unknown 不得补零。再覆盖首用/完全相同实例重复/整组重复三种分母与 setup 摊销；cold dictionary 同步计一次，ready history 纳入生命周期但不重复当前 setup；unknown dictionary history 不得假定 ready 或免费；每个失败尝试都留账；未知 tokenizer、请求文本或复用单位返回 null/`cannot_compare`，不能制造零成本。
- [x] **Step 5：自审并提交。** 仅提交 benchmark、新合成 fixtures 与测试，提交信息 `feat: benchmark AgentWire modes offline`；确认无既有语料或词典被读取、复制或输出。

- [x] **Step 6：独立审查与 fix round 1 闭环。** 两项 Important 均已修复并经定向复审通过；无新 Critical/Important，Minor 维护建议不阻塞。

### Task 6：增加独立 CLI 入口

**Files:** 修改 `src/agentcodec/cli.py`；新增 `tests/test_agentwire_cli.py`。

**Interfaces:** 注册 opt-in `benchmark-agentwire`；只加载仓库内固定 `bench/agentwire/exchanges.v1.jsonl` 和合成 `dictionary.v1.json`，不接受用户提供的语料/词典路径；必填 `--output-dir`（须是新的/空目录），`--tokenizers` 只接受仓库现有本地 profiles，`--instance-uses` 和 `--corpus-passes` 分别接受 1/10/50 组成的逗号列表，`--modes` 只接受 `gateway,model_direct`。缺省两类复用各跑 1/10/50，两种模式均跑；AC2/AWP1 候选仅因显式调用这个新 benchmark 命令才出现，不改旧命令或 encoder 默认。

- [x] **Step 1：写失败测试。** `test_benchmark_agentwire_writes_versioned_report` 断言退出码 0、输出仅含 versioned redacted report 且条件/模式齐全；`test_cli_import_has_no_tokenizer_or_network_side_effect` 验证冷导入新旧 CLI 不初始化 tokenizer、不访问网络或用户数据；`test_cli_uses_only_fixed_synthetic_fixtures` 验证命令无 `--input` 或 `--dictionary` 路径参数且 fixture manifest identity 固定；`test_reuse_options_remain_separate` 验证 `--instance-uses` 与 `--corpus-passes` 独立解析 1/10/50；`test_unknown_options_and_invalid_reuse_fail_closed` 验证未声明参数、非正数和越界次数拒绝且无成功报告；`test_output_path_must_be_new_empty_directory` 验证现存非空目录、路径碰撞、symlink/special output 失败且不覆盖；`test_missing_local_tokenizer_never_downloads` mock 本地 meter 不可用并断言 report 为 cannot_compare、无网络调用。
- [x] **Step 2：验证 RED。** 运行 `.venv/bin/python -m pytest tests/test_agentwire_cli.py -q`，确认新命令不存在或业务断言失败。
- [x] **Step 3：实现入口。** 仅在现有 Typer app 注册新 command；无外部输入路径选项，只从固定版本化合成 fixtures 读取；沿用 UTF-8、重复 JSON key 检查和既有错误风格。输出写入新空目录且只含脱敏摘要/计量值，不回显消息、词典、payload、glossary、绝对路径或输入 hash；禁止自动安装/下载或连接模型。
- [x] **Step 4：验证 GREEN。** 重跑同一命令，检查新 command help、复用单位分离和新目录输出；本地 tokenizer 不可用时保持明确 cannot_compare，不伪造指标。
- [x] **Step 5：自审并提交。** 仅提交 CLI 与新测试，提交信息 `feat: add AgentWire benchmark CLI`。

- [x] **Step 6：独立审查与 fix round 1 闭环。** 两项 Important 均已修复并经定向复审通过。复审者环境无法运行测试；协调者用仓库既有解释器补跑 Task 6/旧 CLI 回归共 191 项，通过。`model_copy(update=...)` 绕过共享 validator 的 Minor 记入台账，留待最终整支审查。

### Task 7：发布固定协议文档并验证旧兼容性

**Files:** 新增 `docs/agentwire-compact-profile-v1.md`、`tests/test_agentwire_compatibility.py`；修改 `README.md`。

**Interfaces:** 消费完整 AWP1 codec、gateway、benchmark 和 CLI 契约；不新增 wire/API 行为。

- [x] **Step 1：写兼容性失败测试。** `test_legacy_ac1_default_and_ac2_opt_in_are_unchanged` 锁定现有 API/golden；`test_legacy_dispatcher_does_not_guess_awp1` 断言既有 `decode_payload("AWP1|...")` 仍要求 fallback；`test_legacy_cli_and_report_versions_remain_stable` 验证旧 benchmark/evaluate/adaptive 命令仍注册且版本不变。
- [x] **Step 2：验证 RED。** 运行 `.venv/bin/python -m pytest tests/test_agentwire_compatibility.py -q`，确认 AWP1 导入或集成可能回归时能被测试捕获；若旧行为已满足，测试应在新增前绿，则明确记录其作为兼容守卫，不伪称 RED。
- [x] **Step 3：写规范文档。** 固定说明 11 个字段顺序、枚举表、JSON framing/escaping、canonical dictionary hash、能力协商/最大 UTF-8 bytes、精确引用、reason codes、fail-closed、gateway/model-direct 成本边界和“不是认证/授权/模型理解”限制；README 仅给本地合成命令和清楚免责声明，并分别标明人工 freeform 与固定 labelled-field 两种自然语言对照；不把 labelled-field token 差异称为真实自然语言节省。
- [x] **Step 4：最终验证。** 运行 `.venv/bin/python -m pytest tests/test_agentwire_compatibility.py -q`，再运行 `.venv/bin/python -m pytest -q`；预期全部通过，不安装依赖、不下载 tokenizer、不连接外部服务。运行 `git diff --check` 并复核旧文件改动仅限 `cli.py`/`README.md` 的明确增量。
- [x] **Step 5：自审并提交。** 仅提交两份文档和兼容性测试，提交信息 `docs: document AgentWire v1 and preserve legacy behavior`。随后按用户指定流程做完整分支独立审查；最终 fix wave `16ffb917..084a0e0` 的新鲜 scoped re-review 确认两项 Important 均已解决、无新 Critical/Important/Minor；不 merge/push。
