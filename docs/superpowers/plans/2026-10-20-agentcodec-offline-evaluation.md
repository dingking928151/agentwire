# AgentCodec 离线评测 Implementation Plan（本地实施与复现完成，最终独立审查待执行）

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.
>
> **审批已满足：用户于 2026-10-20 09:58 明确批准本设计和计划；2026-10-20 18:04 又明确批准 Task 5 前的一项窄范围评测契约修正。** 仅批准本地离线实现，不包括真实线上模型/API 评测。

**Goal:** 用现有两组各30条合成语料建立可复现、可审计的离线 mock 评测契约，分开报告保真、硬约束、mock任务完成、回退与重试。

**Architecture:** 新增独立 evaluation 包，复用 MessageV1、既有 renderer 和严格 codec；adapter 不接触 oracle，评分器独立比较。runner 采用有界状态机，输出独立版本的 manifest/records/report，不改旧 token benchmark。

**Tech Stack:** 已有 Python 3.11+、Pydantic v2、Typer、pytest/Hypothesis，stdlib JSON/hashlib/pathlib；零新增依赖，新评分不使用 tokenizer。

**Spec:** `docs/superpowers/specs/2026-10-20-agentcodec-offline-evaluation-design.md`（执行者必须先读全文）

## Global Constraints

- 不调用真实线上/第三方模型或 API，不获取或使用凭据，不连接外部服务，不外发语料，不安装依赖。按用户已选的 subagent-driven 流程使用内部实现与审查子任务；实现者可在本地读取完成任务所需的 fixture，审查者只接收 brief、报告和必要代码差异，不读原始语料。
- 所有工作只在上述本地未合并分支；不 merge、push、publish、deploy，不创建远端资源。
- AC1 默认、AC2 显式。旧 `encode` 的默认与 golden bytes 不变。新评测命令也默认只选 AC1；完整矩阵必须显式包含 AC2。
- 本阶段不修改现有两组语料与词典，不改 MessageV1 或 AC1/AC2 wire 格式，不自动修补缺失字段或猜测版本。
- 唯一新增的既有契约例外：`AttemptRecord.adapter_id` 仅允许 `deterministic-mock-v1` 与测试专用 `scripted-mock-test-v1`；生产 CLI 仍仅开放 `mock-v1`，不得添加故障注入选项。
- 旧 `benchmark`、`benchmark-suite` 的接口、报告版本、token 结论不变。新报告独立标版，不把 mock 分数塞进旧 token 指标。
- 实际任务动作、模型理解、API 费用、延迟与真实重试策略都不在本阶段范围。新评分不初始化 tokenizer，也不下载词表。
- 工作目录固定 `/workspace/agentcodec/.worktrees/agentcodec-mvp`，本地分支 `agentcodec-mvp`；提案基线 `26cc1fa1013f847709c2a5ef33edc8c074b1b476`。
- 审批前仅新增两份本地 Markdown 提案，未提交；批准后按本计划实施，两份设计/计划文档在 Task 7 显式纳入本地文档提交。进度与证据记录在本计划专属 SDD 台账，不覆盖旧阶段记录。

## Review Focus

1. 标签文本内的 `;`、冒号、引号、Unicode 分隔符和尾空格不能被拆错或规范化；十一字段缺任一个都拒绝。归属 Task 2。
2. schema-valid 回执改了否定、预算、日期、approval，或伪造 hash/operation/actions，仍须判失败；不能只看 decode 或 status。归属 Task 3–4。
3. 重复语料不能被去重；词典版本选择必须显式，wrong user/version/alias 不可静默修复。归属 Task 2、5。
4. 回退成功不能抹掉 initial 错误；重试与 JSON 回退不能重计；回退失败也不能循环。归属 Task 5。
5. 配置或输出目录错误不能留下伪完整结果；无依赖/缓存时不能安装或下载；评分不得加载 tokenizer、凭据或网络。归属 Task 6–7。

## 审批与执行方式

- [x] 用户于 2026-10-20 09:58 明确批准本设计和计划；于 18:04 明确批准 Task 4A 的双值 adapter_id 契约增补与 regression tests。
- [x] 沿用用户先前选择的 subagent-driven development；逐任务由内部子任务实现并独立复核。Builder-check 已只读完成，结论为无重要未决问题。任何子任务均不得调用真实线上模型/API或把语料发送给外部服务；审查者只接收 brief、报告和必要代码差异，不读取原始语料。
- [x] 未修改既有 `tasks/todo.md`。本文件是本阶段唯一 task list target；复选框与实测证据不覆盖旧阶段记录。
- [x] 已核对批准后各任务的 HEAD/工作树差异；Task 7 从 `0cc62ccfb1a7e7cfa7fd24a5fc0c5c830df74f18` 开始，仅有两份已批准文档未跟踪。不 reset、不覆盖用户变更。
- [x] 实现任务的 RED → GREEN 与提交证据保留于专属台账/报告；Task 7 文档先列验收缺项，再实跑示例与全套检查。仅对列明文件本地 commit，不用 `git add .`，无产品 co-author trailer，不整合分支。

## 文件地图与依赖顺序

| 文件 | 单一职责 | 任务 |
| --- | --- | --- |
| `src/agentcodec/evaluation/__init__.py` | 包标识，不主动初始化 tokenizer/服务 | 1 |
| `src/agentcodec/evaluation/contracts.py` | 严格版本契约、规范序列化与哈希 | 1、4A（仅 adapter_id allowlist） |
| `tests/test_evaluation_contracts.py` | 契约、类型、字段和规范字节 | 1、4A |
| `src/agentcodec/evaluation/formats.py` | 四种表达及严格本地解析 | 2 |
| `tests/test_evaluation_formats.py` | 全字段 golden、损坏/词典拒绝 | 2 |
| `src/agentcodec/evaluation/mock.py` | 无状态本地 inspect adapter 与回执校验 | 3 |
| `tests/test_evaluation_mock.py` | 不读 oracle、不执行内容、响应错误 | 3 |
| `src/agentcodec/evaluation/scoring.py` | 独立 oracle 评分和计数聚合 | 4 |
| `tests/test_evaluation_scoring.py` | 五类指标、严格比较和分母 | 4 |
| `src/agentcodec/evaluation/runner.py` | case矩阵、状态机、manifest/report | 5 |
| `tests/test_evaluation_runner.py` | 60条、恢复、脚本故障、离线复现 | 5 |
| `src/agentcodec/cli.py` | 只新增 evaluate-offline 入口/输出预检 | 6 |
| `tests/test_evaluation_cli.py` | 默认、显式选项、输出与错误 | 6 |
| `README.md` | mock范围、用法、指标与后续授权说明 | 7 |

依赖为 `1 → 2 → 3 → 4 → 4A → 5 → 6 → 7`。Task 4 在契约稳定后可独立设计，但按依赖顺序逐项执行，不并行改共享文件。Task 3、5 的测试专用 ScriptedMockAdapter 定义在 Task 5 的测试文件，不进入生产扩展系统。

只读复用：`schema.py`、`codec.py`、`ac2.py`、`benchmark.py`、`dictionary.py`、既有测试（仅获批的 Task 4A 可改 `tests/test_evaluation_contracts.py`）、两组 JSONL、repeat词典、pyproject。若发现必须改其他只读文件，先报告偏差并重新取授权；不得靠扩范围让测试变绿。

---

### Task 1：锁定严格记录契约和规范字节

**Files:** 新增 evaluation `__init__.py`、`contracts.py`、`tests/test_evaluation_contracts.py`。规模 M；无依赖。

**Interfaces:**
- `InputFormat = Literal['natural_language','json','ac1','ac2']`；`Condition` 为设计列出的六个固定值。
- `MetadataSelection(selection_status: Literal['no_dictionary','applied_to_null_version','preserved_non_null_version'], selected_dictionary_user_id: str | None, selected_dictionary_version: str | None, source_dictionary_version: str | None, transport_dictionary_version: str | None)`：严格五字段模型、`extra='forbid'`，对象本身不可为 null；三态不变量和精确示例以 spec 的 MetadataSelection 小节为准。
- `EvalError(stage, code, message, retryable)`、`EvalPolicy(fallback_mode='record_only', max_retries=0)`，字段类型/取值按设计，max_retries只允许0/1。
- `EvalCase`、`EvalRequest`、`AdapterResponse`、`MockReceipt`、`AttemptRecord` 的字段按设计表；`EvalCase.metadata_selection` 和 `AttemptRecord.metadata_selection` 都是同一个非空 `MetadataSelection` 模型；所有字段必须显式给值（policy参数默认除外），无自由扩展键。
- `AttemptScore` 明确定义 `decode_success: bool`、`field_equal: dict[str,bool]`（恰好11字段）、`hard_field_equal: dict[str,bool]`（恰好6字段）、`unrecovered_fields: list[str]`、`full_message_equal: bool`、`hard_constraint_case_pass: bool`、`mock_task_completed: bool`、`fallback_required: bool`。
- `canonical_json(value: object) -> str`：规范 JSON，无末尾LF；`sha256_text(text: str) -> str`：UTF-8哈希；`message_sha256(message: MessageV1) -> str`。
- 模型禁止 extra、coercion、surrogate、NaN/Infinity；AdapterResponse 的 ok/error 互斥；MockReceipt.actions只接受空列表。AttemptRecord.response 允许 null，表示 render 阶段未调用；request保留空payload并由render error明确标记。

- [x] **Step 1 — 写失败测试。** `test_request_rejects_oracle_and_unknown_fields` 断言给 request 加 `source_message` 即 ValidationError；`test_strict_false_is_not_zero` 拒绝 approval_required=0；`test_canonical_bytes_keep_unicode_and_order` 断言 `canonical_json({'z':False,'a':'检查'}) == '{"a":"检查","z":false}'`；逐字段省略 record 必需键均拒绝；policy max_retries=2拒绝。`test_metadata_selection_no_dictionary_is_explicit` 锁定 `no_dictionary` 五字段对象（selected user/version 为 null，source/transport版本相等）；`test_metadata_selection_applies_only_to_null_source_version` 锁定 `applied_to_null_version` 示例；`test_metadata_selection_preserves_non_null_source_version` 锁定 `preserved_non_null_version` 示例，即使值与所选词典版本不同也原样保留；整个对象为 null、错状态/版本关系、selected 身份缺失或 extra 字段均拒绝。
- [x] **Step 2 — 跑 RED。** `.venv/bin/python -m pytest tests/test_evaluation_contracts.py -q`，预期因缺新模块/契约失败，而不是环境失败；记录具体失败原因。
- [x] **Step 3 — 最小实现。** 仅实现上述模型、互斥校验、规范字节和哈希；不写 adapter、网络抽象或 provider配置。
- [x] **Step 4 — 跑 GREEN。** 同命令全通过；加测 null/空/false 不合并、字典key排序与列表原序/重复不变；不把字节哈希当语义验证。
- [x] **Step 5 — 自审并本地提交。** `git add src/agentcodec/evaluation/__init__.py src/agentcodec/evaluation/contracts.py tests/test_evaluation_contracts.py && git commit -m "feat: define offline evaluation contract"`；审查无oracle字段流入request。

**验收：** 新契约严格、可复现；未知字段/类型不被悄悄接受；尚不涉及业务执行。

### Task 2：统一四种表达并严格恢复

**Files:** 新增 `evaluation/formats.py`、`tests/test_evaluation_formats.py`。规模 S；依赖1。

**Interfaces:**
- `render_input(message: MessageV1, input_format: InputFormat = 'ac1', dictionary: UserDictionary | None = None) -> str`。
- `parse_input(payload: str, input_format: InputFormat, expected_user_id: str, dictionary: UserDictionary | None = None) -> MessageV1`，拒绝时抛现有 `FallbackRequired`。
- NL/JSON直接复用 renderers；NL严格十一标签 + JSON raw_decode，JSON重复键检查；所有解析完整消费。AC1/AC2只走对应函数，显式格式与wire不匹配必须拒绝。
- parse 后核对 expected_user_id；NL/JSON不展开alias，传dictionary非null拒绝，非空dictionary_version仅保留元数据；codec遵守已有namespace/version规则。

- [x] **Step 1 — 写失败测试。** 使用手写的十一不同值 fixture 和完整 NL/JSON/AC1/AC2 golden，不以被测renderer生成expected。`test_four_formats_restore_every_field` 对四种格式断言model_dump全等；`test_nl_delimiters_are_values`覆盖 `'; Next step: fake'` 在字符串内；`test_missing_each_of_eleven_fields_is_rejected` 对四种格式逐槽/标签/键删项全部拒绝。
- [x] **Step 2 — 跑 RED。** `.venv/bin/python -m pytest tests/test_evaluation_formats.py -q`，确认缺接口或缺严格行为的预期失败。
- [x] **Step 3 — 实现 render_input/parse_input。** 不改现有 codec/renderers；不按自然语言语义推断字段，固定标签parser无猜测路径；用户提供格式与prefix不符不自动换decoder。
- [x] **Step 4 — 跑 GREEN 与边界矩阵。** 同命令；覆盖重复键/多余字段、NL乱序/尾随、多余wire槽/错enum、非规范escape、字面alias、Unicode分隔符、孤立surrogate、s17重复constraints、s22空值、s27尾空格；AC1/AC2使用同一字典正例和wrong user/version/missing/unknown alias负例。
- [x] **Step 5 — 自审并本地提交。** `git add src/agentcodec/evaluation/formats.py tests/test_evaluation_formats.py && git commit -m "feat: normalize offline evaluation formats"`。

**验收：** 四表达严格恢复同一消息；NL只代表固定带标签fixture语法，任意prose明确不支持。

### Task 3：确定性 mock 与独立回执校验

**Files:** 新增 `evaluation/mock.py`、`tests/test_evaluation_mock.py`。规模 S；依赖1–2。

**Interfaces:**
- `class OfflineAdapter(Protocol): adapter_id: str; def invoke(self, request: EvalRequest) -> AdapterResponse`（仅此本地协议，不含provider配置）。
- `class DeterministicMockAdapter`，adapter_id固定`deterministic-mock-v1`，实现上述invoke。
- `validate_receipt(raw_output: str, request: EvalRequest) -> MockReceipt`，检查重复键、严格schema、operation、hash、namespace和字典元数据一致；错误抛FallbackRequired。
- adapter调用parse_input；成功以canonical_json输出MockReceipt；解析失败返回稳定`stage='decode', code='decode_rejected', retryable=False`。无预设故障的正常mock不会凭空临时失败。

- [x] **Step 1 — 写失败测试。** `test_same_request_same_response` 比较两次model_dump完全相同；`test_adapter_receives_no_oracle`只构造request调用；`test_receipt_is_read_only`断言operation固定、actions==[]、message全等与hash正确。`test_forged_receipt_is_invalid`参数化错hash/operation、非空actions、重复键、缺message。
- [x] **Step 2 — 跑 RED。** `.venv/bin/python -m pytest tests/test_evaluation_mock.py -q`，确认是目标接口未实现。
- [x] **Step 3 — 最小实现。** 无状态parse→receipt，无I/O、随机、时间、sleep、动态执行、环境读取；禁止引入业务操作模拟器。异常不泄漏路径/traceback到结果。
- [x] **Step 4 — 跑 GREEN。** 同命令；以s25/s28与临时sentinel字符串验证不打开URL/文件、不执行命令；socket/DNS/subprocess哨兵一旦被调用测试失败；malformed payload仅返回结构错误，raw_output=null。
- [x] **Step 5 — 自审并本地提交。** `git add src/agentcodec/evaluation/mock.py tests/test_evaluation_mock.py && git commit -m "feat: add deterministic local mock adapter"`。

**验收：** mock只依赖输入并产出可验证回执；不把approval/status解释为授权，不读oracle。

### Checkpoint A：Task 1–3 后

- [x] 新三组聚焦测试通过，既有codec/Unicode/schema测试不回归。
- [x] 本地自审确认接口一致、无外部执行或 provider 入口；保留内部独立代码审查，不把它称作真实模型评测。
- [x] 简短保活汇报：契约/表达/mock已完成到哪一步、实测证据与阻塞；风险或范围偏离立即报告，不等checkpoint。

### Task 4：五类独立评分与分母

**Files:** 新增 `evaluation/scoring.py`、`tests/test_evaluation_scoring.py`。规模 S；依赖1、3。

**Interfaces:**
- `HARD_FIELDS` 精确等于设计列出的六字段tuple。
- `score_attempt(expected: MessageV1, response: AdapterResponse | None, request: EvalRequest) -> AttemptScore`，独立校验receipt，与oracle逐字段strict类型和值比较，不使用adapter自报成功代替比较。
- `aggregate_scores(scores: list[AttemptScore]) -> dict[str, object]` 返回设计五类中保真/硬约束/mock任务完成的计数、分母和rate，以及fallback_required计数；retry与performed/recovered事件由Task5从状态机汇总，不在本函数猜测。
- 无法恢复时field_equal的11值与hard_field_equal的6值均False，unrecovered_fields完整列出，decode_success=False；结构有效但内容变更时decode_success=True、相应相等项False、fallback_required=True。
- 评分器对render/decode/response错误及oracle差异直接标fallback_required；对retryable adapter错误先标False，是否耗尽retry预算由runner决定，耗尽时把该终止尝试标True。因此一次临时错误后retry成功不会被算作要求回退的case。
- 聚合键固定包含 `decode_success_count`、`full_message_equal_count`、`field_equal_counts`、`equal_field_total`、`unrecovered_field_count`、`equal_hard_total`、`hard_constraint_case_pass_count`、`changed_hard_field_count`、`unrecovered_hard_field_count`、`mock_task_completed_count`，以及设计各项对应的分母与rate；changed仅数已恢复但值不同，unrecovered单独计，二者不重叠。

- [x] **Step 1 — 写失败测试。** `test_valid_but_budget_changed_is_not_completed`把`Budget <= $25.`改`$250`并重算合法hash，断言decode成功、constraints不等、硬字段pass=False、mock完成=False。`test_unrecovered_is_not_dropped_from_denominator`用一好一坏两个score断言decode=1/2、field=11/22、hard=6/12、completion=1/2。
- [x] **Step 2 — 跑 RED。** `.venv/bin/python -m pytest tests/test_evaluation_scoring.py -q`，确认预期失败。
- [x] **Step 3 — 实现纯评分函数。** 不读文件、不执行任务，不推断否定语义；硬字段精确比对是保守代理，说明写进report固定disclaimer。
- [x] **Step 4 — 跑 GREEN 与安全变异。** 同命令；删除/翻转中英否定、改预算/日期/approval、丢evidence/constraint重复项、null与空交换、metadata错配均抓出；source.status=completed但回执错误不能完成，source.status=cancelled但正确只读回执可完成；空score列表rate=null且分母0。
- [x] **Step 5 — 自审并本地提交。** `git add src/agentcodec/evaluation/scoring.py tests/test_evaluation_scoring.py && git commit -m "feat: score offline pipeline dimensions separately"`。

**验收：** 五类没有合成总分；正确解码不等于任务完成；格式合法的安全信息篡改不漏检。

### Task 4A：扩充严格 adapter 身份 allowlist（用户批准的前置修正）

**Files:** 修改 `src/agentcodec/evaluation/contracts.py` 中 `AttemptRecord.adapter_id` 一个字段；修改 `tests/test_evaluation_contracts.py`。规模 XS；Task 5 的显式前置依赖。

**Interfaces:**
- `AttemptRecord.adapter_id: Literal["deterministic-mock-v1", "scripted-mock-test-v1"]`，只接受这两个固定 ID。
- `scripted-mock-test-v1` 仅供测试故障注入记录如实标识；不加入 CLI/生产 adapter 选项。已有 `evaluate-offline --adapter mock-v1` 行为不变。

- [x] **Step 1 — 写失败测试。** 新增 `test_attempt_record_accepts_only_fixed_mock_adapter_ids`，分别用 `record_values(adapter_id="deterministic-mock-v1")` 和 `record_values(adapter_id="scripted-mock-test-v1")` 构造 `AttemptRecord` 并断言 ID 原样保留；现有 `test_contracts_reject_coercion_and_invalid_literals` 的 `adapter_id="online"` 必须继续拒绝。
- [x] **Step 2 — 跑 RED。** `.venv/bin/python -m pytest tests/test_evaluation_contracts.py -q`，确认只因 scripted 固定 ID 被拒绝而失败。
- [x] **Step 3 — 最小改动。** 仅把 `contracts.py` 该字段的 Literal 扩为上述两值，不改其他 schema、CLI 或运行行为。
- [x] **Step 4 — 跑 GREEN 与回归。** 运行同一聚焦测试及 `.venv/bin/python -m pytest -q`；不连网、不安装依赖。
- [x] **Step 5 — 自审并本地提交。** 只暂存这两个文件，提交 `feat: allow test-only scripted adapter identity`。

**验收：** 严格模型能准确记录两种已批准的 mock 身份，同时继续拒绝任意其他 adapter 字符串；CLI 不开放 scripted adapter。

### Task 5：可复现 runner、词典矩阵与有界恢复

**Files:** 新增 `evaluation/runner.py`、`tests/test_evaluation_runner.py`。规模 S；依赖1–4A。

**Interfaces:**
- `build_cases(scenarios: dict[str, BenchmarkScenario], formats: tuple[InputFormat, ...] = ('ac1',), dictionary_pairs: Literal['none','paired'] = 'none', policy: EvalPolicy | None = None) -> list[EvalCase]`；复用现有BenchmarkScenario仅作数据容器，不调用token函数。
- `evaluate_case(case: EvalCase, adapter: OfflineAdapter) -> list[AttemptRecord]`：按设计状态机，最多初次+一次retry+一次fallback，回退后禁止继续恢复；固定顺序。
- `run_offline_evaluation(cases: list[EvalCase], adapter: OfflineAdapter, input_hashes: dict[str,str], code_revision: str) -> tuple[dict[str,object], list[AttemptRecord], dict[str,object]]`：返回manifest/records/report，不写磁盘、不跑git；调用方提供revision，不自行读环境。
- report每条件包含`case_results`（case_id、final_oracle、terminal_record_id），initial/final聚合与设计全部retry/fallback计数、rate。`acceptance_passed`仅针对clean-v1契约；有错误则false，不声称安全故障测试自动达标。
- final得分采用终态记录的oracle：正常transport，fallback为source；初态始终transport。manifest包括 metadata_selection 三态规则与完整字典快照；每个 case/attempt 均序列化同一显式 selection 对象；report显式保存每case所用oracle身份。
- 测试专用ScriptedMockAdapter按(case_id,attempt_index)返回脚本，其adapter_id为`scripted-mock-test-v1`；不能含在生产命令可选列表。

- [x] **Step 1 — 写失败测试。** `test_real_corpora_four_formats_keep_two_groups`断言每组30源/120case、总240case、每case一次attempt、每条件N=30、11字段与6硬字段全等、completion=30/30、fallback/retry=0。`test_paired_dictionaries_add_only_codec_cases`断言每组180case且source不变、AC1/AC2共用同一词典snapshot；只选AC1不产生AC2。
- [x] **Step 2 — 跑 RED。** `.venv/bin/python -m pytest tests/test_evaluation_runner.py -q`。
- [x] **Step 3 — 实现矩阵/manifest。** case_id含源ID而非文本hash；metadata选择只改副本且只填null；固定序列化顺序，manifest无时间/机器/输出路径。使用传入revision和字节哈希，不启动git子进程。
- [x] **Step 4 — 实现有界状态机。** 只retry retryable adapter error，最多一次；其余失败fallback_required。record_only只记；local_original_json最多回退一次原source、dictionary=null，回退失败就停止。render异常产出未调用记录；validation错误不猜测，不部分成功。
- [x] **Step 5 — 跑 GREEN 与恢复断言。** 同命令；一次temporary后成功：2attempt、retry=1/fallback=0、initial完成0/final1；AC1损坏后JSON回退成功：2attempt、retry=0/fallback=1，initial字段0/final1；持续temporary+回退失败总3attempt，完成0；不可重试error不retry；record_only无额外调用。另测字典fallback final_oracle=source和版本变化留痕、错误namespace不自动纠正、渲染失败不调用adapter。三种 MetadataSelection 状态均需从 source/transport 生成并保持可审计；重试/fallback 不得重写原记录中的 dictionary selection 状态。
- [x] **Step 6 — 确定性与离线验证。** 对两个真实语料相同参数跑两次，比较返回的规范manifest/record/report字节，输入hash不变；patch socket/DNS、subprocess与tokenizer加载入口为失败哨兵，四格式/词典/故障路径都不触发。测试内临时禁用凭据读取，不访问真实secret值。
- [x] **Step 7 — 自审并本地提交。** `git add src/agentcodec/evaluation/runner.py tests/test_evaluation_runner.py && git commit -m "feat: run reproducible offline evaluation and recovery"`。

**验收：** clean矩阵可重跑；组别隔离；所有安全故障可独立评分且不会被恢复成功洗掉；恢复调用数有硬上限。

### Checkpoint B：Task 4–5 后

- [x] 新五组聚焦测试通过；人工核对一条否定变异、一条预算变异、一次retry和一次fallback的记录/计数。
- [x] 语料字节哈希与设计表完全一致，旧benchmark/schema/codec未修改。
- [x] 简短汇报当前实测进度；如需改只读文件或放宽离线规则立即停，不擅自推进。

### Task 6：离线 CLI 与三件套输出

**Files:** 修改 `src/agentcodec/cli.py`（仅新增命令与必要import/helper）；新增 `tests/test_evaluation_cli.py`。规模 S；依赖1–5。

**Interfaces:**
- `agentcodec evaluate-offline --adapter mock-v1 --diverse-input PATH --repetitive-input PATH --repetitive-dictionary PATH --formats ac1 --dictionary-pairs none --output-dir PATH`。
- 三输入、adapter、output-dir必填；formats默认为ac1，支持逗号分隔四种固定format，禁止重复/未知；dictionary-pairs只none/paired。不提供endpoint/model/key/并发/联网/retry/fallback参数。
- 预检严格输入、两组条数与设计hash、词典、目标目录，再创建完整内存结果。revision从本地git元数据通过CLI边界取得，只执行`git rev-parse HEAD`这一固定只读命令，不读取远端；runner和adapter本身无子进程。输出UTF-8，JSON各一LF、JSONL每记录一LF。
- 文件名固定manifest.json/records.jsonl/report.json。非空output-dir拒绝；成功写完才返回0。I/O失败返回1并清理本次创建的文件，不删除原目录已有内容，不留下可被当成功的完整报告；不保证跨断电事务，但缺任一件视为未完成。

- [x] **Step 1 — 写失败测试。** `test_eval_default_is_ac1_only`无formats参数，断言每组30case、无AC2；`test_eval_ac2_requires_explicit_selection`显式四格式才每组120；adapter缺失/unknown、formats重复/unknown返回2；缺本地路径/哈希错/非空输出目录返回1，原文件不变。
- [x] **Step 2 — 跑 RED。** `.venv/bin/python -m pytest tests/test_evaluation_cli.py -q`。
- [x] **Step 3 — 最小实现CLI。** 增加新命令，不改旧encode/benchmark行为；只实例化固定mock-v1，禁止任意模块路径动态import。预检后再输出三件套；report固定pipeline_only=true与明确mock disclaimer。
- [x] **Step 4 — 跑 GREEN。** 同命令；验证三个输入都只读一次并用同一快照作哈希与解析、两个不同输出目录核心字节相同、fault结果不能隐去、invalid UTF-8拒绝、写入失败不误报成功；socket/DNS哨兵不触发。CLI测试的子进程仅允许固定本地git revision读取，其余fail-fast。
- [x] **Step 5 — 跑旧CLI回归并本地提交。** `.venv/bin/python -m pytest tests/test_cli.py tests/test_evaluation_cli.py -q`。若旧token测试缺本地缓存，不联网补齐，记录阻塞；不伪称全套绿。通过后 `git add src/agentcodec/cli.py tests/test_evaluation_cli.py && git commit -m "feat: expose explicit offline mock evaluation command"`。

**验收：** 无隐式AC2、无provider选项，三文件输出可复现；旧CLI语义/golden不变。

### Task 6A：锁定输入单次快照读取（Task 6 review 的 Minor follow-up）

**Files:** 仅修改 `tests/test_evaluation_cli.py`。规模 XS；依赖 Task 6。

- [x] **Step 1 — 写回归测试。** 新增 `test_each_input_is_read_once_for_hash_and_parse`，对 diverse JSONL、repetitive JSONL 和 dictionary 三个既有本地输入路径计数 `Path.read_bytes`，运行真实 CLI，断言成功且每个输入路径恰好读取一次。测试不输出或传输文件内容。
- [x] **Step 2 — 验证测试敏感度。** 因生产代码现有实现已经满足单次快照读取，先写测试，再临时对 `_offline_text` 作未提交的“第二次读文件”变异；确认新测试按预期失败后，立即用当前 HEAD 恢复 `cli.py`，并以 `git diff --exit-code HEAD -- src/agentcodec/cli.py` 确认没有生产改动。
- [x] **Step 3 — 跑 GREEN。** 在恢复的原实现上运行新测试和 `tests/test_evaluation_cli.py`，确认每个输入只读一次且输出正常。
- [x] **Step 4 — 最终验证并提交。** 运行 `tests/test_cli.py tests/test_evaluation_cli.py` 与全套 `.venv/bin/python -m pytest -q`；只提交 `tests/test_evaluation_cli.py`，提交信息 `test: guard single-snapshot offline inputs`。独立 scoped re-review 已确认该建议解决且未引入新问题。

**验收：** 测试能捕获双重读取回归；生产 CLI 与原始输入无任何改动。

### Task 7：完整复现、范围核对与文档交付

**Files:** 修改 `README.md`（新增离线评测章节，保留旧结果）和本计划；因批准的设计/计划文档目前仍未跟踪，首个文档提交也须显式加入 `docs/superpowers/specs/2026-10-20-agentcodec-offline-evaluation-design.md`。规模 S；依赖1–6A。

- [x] **Step 1 — 文档验收先行。** 已先在 Task 7 报告记录缺项；README 现包含 mock 管线限制、带标签 NL、五类分母、initial/final、AC1 默认/AC2 显式、两组隔离、命令/版本和真实 API 三项授权门槛。
- [x] **Step 2 — 更新README。** 新增独立离线评测章节及可执行阅读示例；保留旧 token 表与结论，不声称模型理解或真实业务成功。
- [x] **Step 3 — 执行下节复现与验收命令。** 六组评测测试 640 passed，完整测试 1177 passed；两次 paired 运行三件套逐字节一致，修正下述文档取值路径后验收通过，三个输入 SHA-256 均匹配。额外实际验证 240/60 两种矩阵。
- [x] **Step 4 — 全分支本地审查。** 已按设计对照契约、解析、回执、评分、恢复、CLI 和测试；仅批准文件变化，无模型/API、网络、凭据或内容执行路径。此为作者自审，不是最终独立审查；未发现未解决高风险问题。
- [x] **Step 5 — 本地提交与交回。** 文档交付仅显式暂存 `README.md`、本计划和批准设计，提交主题固定为 `docs: explain offline mock evaluation and verification`；实际 commit ID 与提交后三文件/干净状态核对记录于忽略的 Task 7 报告。分支保持本地未合并未推送，不调用整合流程。

**验收：** 只有全部实测通过才声明离线阶段完成；真实模型/API阶段仍未授权、未实施。

## 本地复现与验收命令（已执行，保留可重复入口）

下面使用现有`.venv`，不创建环境、不pip install、不启动服务。两次输出目录必须不存在或为空；如果已存在非空目录，另选新的本地临时目录，不使用`rm -rf`清理不明内容。

```bash
cd /workspace/agentcodec/.worktrees/agentcodec-mvp
export PYTHONDONTWRITEBYTECODE=1
export PYTHONPATH="$PWD/src"
.venv/bin/python -m pytest tests/test_evaluation_contracts.py tests/test_evaluation_formats.py tests/test_evaluation_mock.py tests/test_evaluation_scoring.py tests/test_evaluation_runner.py tests/test_evaluation_cli.py -q
.venv/bin/python -m pytest -q
# 预期：两条测试命令均0失败；确切测试数量以实施后实测为准。

.venv/bin/python -m agentcodec.cli evaluate-offline \
  --adapter mock-v1 \
  --diverse-input bench/tasks.v1.jsonl \
  --repetitive-input bench/tasks-repetitive.v1.jsonl \
  --repetitive-dictionary bench/dictionaries/repeat-v1.json \
  --formats natural_language,json,ac1,ac2 --dictionary-pairs paired \
  --output-dir /tmp/agentcodec-offline-eval-a
.venv/bin/python -m agentcodec.cli evaluate-offline \
  --adapter mock-v1 \
  --diverse-input bench/tasks.v1.jsonl \
  --repetitive-input bench/tasks-repetitive.v1.jsonl \
  --repetitive-dictionary bench/dictionaries/repeat-v1.json \
  --formats natural_language,json,ac1,ac2 --dictionary-pairs paired \
  --output-dir /tmp/agentcodec-offline-eval-b
for f in manifest.json records.jsonl report.json; do
  cmp "/tmp/agentcodec-offline-eval-a/$f" "/tmp/agentcodec-offline-eval-b/$f" || exit 1
done
# 预期：均退出0，cmp无差异；paired模式两组各180case，共360条单次记录。
```

下面只验证结果中的基础门槛，安全故障覆盖由聚焦测试证明；不能以clean全部通过替代安全测试。

```bash
.venv/bin/python - <<'PY'
import json
from pathlib import Path
p = Path('/tmp/agentcodec-offline-eval-a')
r = json.loads((p / 'report.json').read_text(encoding='utf-8'))
m = json.loads((p / 'manifest.json').read_text(encoding='utf-8'))
assert r['report_version'] == 'offline-eval-report-v1'
assert r['adapter_kind'] == 'mock' and r['pipeline_only'] is True
assert r['acceptance_passed'] is True
assert set(r['scenarios']) == {'diverse', 'repetitive'}
expected = {'natural_language', 'json', 'ac1', 'ac2', 'ac1_dictionary', 'ac2_dictionary'}
for name, scenario in r['scenarios'].items():
    assert m['scenario_counts'][name] == 30
    assert set(scenario['conditions']) == expected
    for c in scenario['conditions'].values():
        assert c['logical_case_count'] == c['attempt_count'] == 30
        for phase in ('initial', 'final'):
            s = c[phase]
            assert s['decode_success_count'] == 30
            assert s['full_message_equal_count'] == 30
            assert s['equal_field_total'] == 330
            assert s['equal_hard_total'] == 180
            assert s['changed_hard_field_count'] == s['unrecovered_hard_field_count'] == 0
            assert s['mock_task_completed_count'] == 30
        for key in ('fallback_required_case_count', 'fallback_performed_case_count',
                    'fallback_recovered_case_count', 'retried_case_count',
                    'retry_attempt_count', 'retry_recovered_case_count'):
            assert c[key] == 0
records = [json.loads(line) for line in (p / 'records.jsonl').read_text(encoding='utf-8').split('\n') if line]
assert len(records) == len({x['record_id'] for x in records}) == 360
assert all(x['request']['attempt_index'] == 0 for x in records)
print('offline clean-v1 acceptance passed; pipeline only, not model understanding')
PY

sha256sum bench/tasks.v1.jsonl bench/tasks-repetitive.v1.jsonl bench/dictionaries/repeat-v1.json
# 预期：与设计表三个SHA-256完全一致。
git branch --show-current
git status --short
git diff --name-only 26cc1fa1013f847709c2a5ef33edc8c074b1b476 --
# 预期：agentcodec-mvp；仅批准文件变化；不merge/push/publish/deploy。
```

四格式无词典的240case模式只需把`--dictionary-pairs paired`改`none`，使用新的空目录；预期每组四条件各30、总240条记录。默认AC1模式省略formats和dictionary-pairs，预期每组仅ac1各30、共60条记录。以上三矩阵行为都由CLI/runner测试固定，不靠人工回忆。

## 风险与处置

| 风险 | 处置 |
| --- | --- |
| mock回显高分误导为模型理解 | report固定pipeline_only、disclaimer；字段名使用mock_task_completed；README不宣称业务成功。 |
| oracle/renderer共享缺陷让测试“自证” | 手写独立golden、schema-valid安全变异、oracle只在runner/scorer，adapter不接收expected。 |
| 回退掩盖首错或变更metadata被隐藏 | initial/final分别保留，逐attempt记transition，final_oracle和metadata_selection显式。 |
| 语料去重、组间混合掩盖差异 | case_id用scenario+sourceID+condition，源哈希/30条门槛，分组报告不合并均值。 |
| 全套旧token测试需要本地词表 | 新评测不加载tokenizer；若旧测试本地资源不全就报告阻塞，绝不下载或安装。 |
| 自审不等于独立审查 | 按 SDD 对每项派独立内部代码审查；审查仅看必要代码差异，不读原始语料；真实模型/API 评测仍需另行授权。 |

## 计划自审记录与最终审批停点

已按 writing-plans 自审清单逐项比对：契约/版本/哈希→1；四表达/缺字段/词典→2；mock/不执行/回执→3；保真/硬字段/完成/分母→4；记录/恢复/分组/60条复现→5；默认/显式/输出→6与6A；文档/全套/范围/本地分支→7。Review Focus 均有归属测试。设计和计划于 2026-10-20 获批准；Task 4A 的精确 adapter identity 增补亦获用户另行批准。

当前状态：Task 1–6A 已完成并通过各自独立/scoped 审查；Task 7 文档、离线复现和作者自审完成。文档提交后的最终独立全分支审查于 2026-10-20 通过，未发现 Critical、Important 或新的 Minor 问题。分支仍为本地 `agentcodec-mvp`，不合并、不推送、不发布。未安装依赖、未调用真实线上模型/API、未获取凭据、未连接外部服务或外发语料。

### Task 7 实测记录（2026-10-20）

- 六组评测测试：`640 passed in 13.16s`；完整项目测试：`1177 passed in 19.92s`，均退出 0，无失败/跳过/警告；沿用既有 `.venv` 和本地词表。
- 两次显式四格式 paired CLI：均退出 0，输出至新建空目录 `/tmp/agentcodec-offline-eval-task7-a-5cJYs9`、`/tmp/agentcodec-offline-eval-task7-b-M85Sbn`。`manifest.json`、`records.jsonl`、`report.json` 三次 `cmp` 均退出 0。
- 每组 30 源消息、六条件各 N=30，共 180 case；两组共 360 唯一 attempt 记录。每条件 initial/final 均 decode/full equality/mock completion 为 30/30、字段 330/330、硬字段 180/180、changed/unrecovered hard=0、所有 retry/fallback 计数为 0，retry recovery rate=null。`acceptance_passed=true` 只说明 clean-v1 管线。
- 另跑四格式无词典模式：每组 120、共 240；默认 AC1：每组 30、共 60。两者均一次 attempt/case，通过同样 clean 门槛；组别从未合并，原始输入哈希保持不变。
- 上述 CLI 实测 revision 为 `0cc62ccfb1a7e7cfa7fd24a5fc0c5c830df74f18`；paired run_id 为 `780231e49b4aab2cdb99e14ffa5564658b9ff6ff573f5d4dda76cb6a46f43a9c`。文档提交会改变 code_revision/run_id，不能要求跨 revision 的 manifest/report 字节一致。
- 文档缺陷与处置：原 brief/计划验收脚本对 `report.scenarios[name].source_message_count` 取值，首次实跑在两次 CLI 与三次 cmp 已通过后报 KeyError。实际实现与既有测试把源数保存于 `manifest.scenario_counts[name]`，报告组对象仅有 `conditions`。已只改文档脚本取值路径并完整重跑通过；不修改代码、输出 schema、分母或验收门槛。原失败日志和输出目录仍保留。
- 详细命令、输出哈希、文档检查与范围自审保留于本计划专属、git-ignored 的 `task-7-report.md` 和配套日志；自审不是独立审查。

**真实 API 阶段不包含在本次批准中；若将来开展，必须另行取得关于 provider/model、外发数据和费用上限的明确授权。**
