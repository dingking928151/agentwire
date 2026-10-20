# AgentCodec 离线评测设计

**状态：已批准并按计划实施（2026-10-20）。** 批准范围仅限本地离线评测管线；不包含真实线上模型/API 评测。不得改动协议语义或原始语料；不得安装依赖、外发语料或连接外部服务。旧 code/benchmark 含义保持不变。2026-10-20 用户另批准一项窄范围契约修正：`AttemptRecord.adapter_id` 只允许 `deterministic-mock-v1` 与测试专用的 `scripted-mock-test-v1`，供 Task 5 测试故障注入器如实留痕；生产 CLI 仍只允许确定性 mock。

## 最重要的结论

下一阶段先建立一条可审计、可复现的离线评测管线，不接真实模型。自然语言、JSON、AC1、AC2 采用同一请求/响应记录结构、同一确定性 mock、同一评分器。现有多样与高重复语料分别报告，每组 30 条，不用一个平均数掩盖两组差异。

**mock 只验证管线，不代表模型理解。** 本阶段所谓“任务完成”只指完成预先定义的“严格解析消息并返回只读回执”测试任务，不是执行语料中的报告审核、采购、退款、预约、发布等原始业务任务。不会读取语料里引用的文件、打开 URL、执行命令或模拟外部操作。

## 已核对的基础

工作树为 `/workspace/agentcodec/.worktrees/agentcodec-mvp`，分支 `agentcodec-mvp`，提案基线 HEAD 为 `26cc1fa1013f847709c2a5ef33edc8c074b1b476`；开始规划时工作树干净。继续使用现有 Python 3.11+、Pydantic v2、Typer、pytest/Hypothesis，不增加依赖。

本设计承接 [基础产品设计](2026-10-19-agentcodec-design.md) 和 [AC2 实验设计](2026-10-19-agentcodec-ac2-experiment-design.md)，不替换旧 benchmark 的含义或已披露的负面结果。现有 `MessageV1` 11 字段、严格 codec、自然语言/JSON renderer 和词典可以复用；现有 token benchmark 不是本次的任务评分器。

| 固定输入 | 条数 / 身份 | SHA-256 |
| --- | --- | --- |
| `bench/tasks.v1.jsonl` | diverse，30 条，`s01`–`s30` | `9284dc0b7a878355c6e8ddc0195cd7adcade9b4966dc3a07aeb58f2f1f2ad7b8` |
| `bench/tasks-repetitive.v1.jsonl` | repetitive，30 条 | `f5c8d2d98837edd5944f10e6fb7a63bd44dfcff6b022aab57502b2197cd239fe` |
| `bench/dictionaries/repeat-v1.json` | 手工 repeat-user / repeat-v1 词典 | `9b320df2e125804c07dc53418164633345c4d26ff4e56406e42b3fcd94338d86` |

多样组配对词典继续使用 `benchmark.DEMO_DICTIONARY`（demo-user / demo-v1），把其规范序列化内容和哈希写入 manifest；不得自动学习词典。

## 目标与不可越过的边界

- 不调用真实线上/第三方模型或 API，不获取或使用凭据，不连接外部服务，不外发语料，不安装依赖。沿用既定的 subagent-driven development，在本地工作区内委派实现和代码审查；实现子任务可读取完成本地工作所需的 fixture，审查子任务只接触必要代码/测试差异，不读取原始语料文件。不得把语料发送给外部模型或服务。
- 所有工作只在上述本地未合并分支；不 merge、push、publish、deploy，不创建远端资源。
- AC1 默认、AC2 显式。旧 `encode` 的默认与 golden bytes 不变。新评测命令也默认只选 AC1；完整矩阵必须显式包含 AC2。
- 本阶段不修改现有两组语料与词典，不改 MessageV1 或 AC1/AC2 wire 格式，不自动修补缺失字段或猜测版本。
- 旧 `benchmark`、`benchmark-suite` 的接口、报告版本、token 结论不变。新报告独立标版，不把 mock 分数塞进旧 token 指标。
- 实际任务动作、模型理解、API 费用、延迟与真实重试策略都不在本阶段范围。新评分不初始化 tokenizer，也不下载词表。

## 方案选择（提案者的工程决定，不冒充用户已批准）

比较了三条路线：沿用 token benchmark 塞入任务评分，会混淆“可逆编码”和“理解”；直接预留真实 provider SDK，会过早引入凭据、网络和费用；新增独立离线评测模块，复用已有 codec/renderers，则可把边界锁死。推荐第三条，薄 CLI + 严格契约 + 纯函数评分 + 无状态 mock。

不建设 provider 注册表、插件系统、异步并发、数据库、可视化仪表盘或通用业务执行器。实现按契约 → 表达层 → mock → 评分 → 有界恢复/runner → CLI 顺序推进。

## 实际 mock 流程

1. 严格加载现有 JSONL，拒绝重复键/ID、非法 UTF-8、缺字段和空数据集；确认每组 30 条与固定哈希。源文件只读。
2. 为每个条件保存 `source_message` 和 `transport_message`。无词典时两者相同；词典条件仅当源 `dictionary_version` 为 null 时，在副本中显式选择本组版本，并记录固定结构的 `metadata_selection`。已有非空版本绝不覆盖；即使它与本条件词典不匹配，也保留原值并让严格 codec 按既有规则拒绝。
3. 把 transport 副本渲染成指定输入格式。自然语言严格限定为现有 `render_natural_language` 产生的十一项带标签、含 JSON 字面值的文本，**不是任意自然语言理解**。
4. 创建 `EvalRequest`，交给 `DeterministicMockAdapter.invoke(request)`。request 只含实际传递的 payload、格式、命名空间、词典快照和本地控制参数；不含 expected/oracle，不传源消息对象，不让 adapter 偷看答案。
5. mock 用该格式的严格解析器得到 MessageV1。失败则返回结构化错误，不能输出部分消息或猜测。成功则返回严格 JSON `MockReceipt`：原样消息、消息哈希、固定 operation、空 actions。
6. runner 保存原始回执文本，再用独立的严格回执校验器解析。评分器拿保留的 transport oracle 比较，不根据 adapter 自报的成功判分。
7. 主矩阵策略为 `record_only`、`max_retries=0`。错误照实记录；不偷偷用原文恢复。恢复策略只在专门的本地故障测试中开启。
8. 输出 manifest、逐尝试 records、分组 report；对相同输入和版本重复运行，三个文件逐字节相同。

### 各格式与词典条件

基本矩阵为每组 30 × 4 = 120 条逻辑 case，两组共 240 条。四种输入格式为 `natural_language`、`json`、`ac1`、`ac2`；共同输出格式固定为 `mock_receipt_json_v1`。这统一的是输入/输出**记录与回执接口**，不声称验证了模型生成四种输出格式的能力。

可显式加开 `--dictionary-pairs paired`：只额外生成 AC1+本组词典和 AC2+同一本组词典，各组增加 60 条，共 360 条逻辑 case。自然语言和 JSON 不重复生成词典条件。默认 `--dictionary-pairs none`；pair 仅为已选择的 codec 格式生成，不会隐式启用 AC2。

自然语言解析用固定标签顺序和 `json.JSONDecoder.raw_decode` 消费值，再核对分隔文本；不可直接按 `;`、冒号、换行拆分，防止值内分隔符被误读。必须完整消费单条文本，禁止缺项、乱序、重复标签、尾随内容。JSON 复用同等级的重复键/严格 schema 检查。AC1/AC2 复用对应严格解码器并检查所声明的格式与前缀一致，禁止自动降级为其他表达。

## 统一记录契约 `offline-eval-v1`

新增模型均使用 Pydantic strict、`extra='forbid'`；所有字段显式提供，null、false、空字符串和空列表不等价。序列化采用 UTF-8、`ensure_ascii=False`、`sort_keys=True`、紧凑 JSON，加单个 LF；JSONL 只用 LF 分记录，不用 `splitlines()` 切 Unicode 内容。列表顺序和重复项均保留，不 trim、不 Unicode normalization。

### 身份与 oracle：`EvalCase`

runner 私有的 `EvalCase` 包含 `scenario`、`source_message`、`transport_message`、`condition`、`dictionary`、`metadata_selection: MetadataSelection`、`policy`；**不交给 adapter**。`condition` 取六个固定值：`natural_language`、`json`、`ac1`、`ac2`、`ac1_dictionary`、`ac2_dictionary`。`case_id` 用 `scenario/source_message.message_id/condition`，`record_id` 用 `case_id/attempt_index`；不能以 task 文本或 payload hash 当唯一 ID，否则高重复样本会被合并。

`MetadataSelection` 是每个 case 与 attempt 均必须提供的非空严格对象，精确包含 `selection_status`、`selected_dictionary_user_id`、`selected_dictionary_version`、`source_dictionary_version`、`transport_dictionary_version` 五字段；extra 禁止，版本字段可显式为 null。三种合法状态：

- `no_dictionary`：所选条件没有词典；selected 两字段为 null，transport 版本必须等于 source 版本。
- `applied_to_null_version`：条件选了词典且 source 版本为 null；selected 两字段为相应非空词典身份，transport 版本等于 selected dictionary version。
- `preserved_non_null_version`：条件选了词典但 source 版本非 null；selected 两字段仍记录本条件所用词典，transport 版本必须原样等于 source 版本，不能覆盖。它可能与 selected version 不同，后续严格 codec 将据此成功或要求 fallback。

`metadata_selection` 本身不允许 null。示例：无词典、source/transport 均 null 时为 `{"selection_status":"no_dictionary","selected_dictionary_user_id":null,"selected_dictionary_version":null,"source_dictionary_version":null,"transport_dictionary_version":null}`；demo 词典 applied 时对应身份为 `demo-user`/`demo-v1`，source 为 null，transport 为 `demo-v1`；已有 `legacy-v0` 时用 `preserved_non_null_version`，source/transport 都是 `legacy-v0`，selected 仍记 `demo-user`/`demo-v1`。

### 输入：`EvalRequest`

| 字段 | 精确含义 |
| --- | --- |
| `contract_version` | 固定 `offline-eval-v1` |
| `case_id`, `attempt_index` | 逻辑 case 与从 0 开始的尝试序号 |
| `input_format` | `natural_language\|json\|ac1\|ac2` |
| `payload` | 实际接收的完整字符串，不改写 |
| `expected_user_id` | 来自本地配置的显式 namespace；不是权限凭据 |
| `dictionary` | 已选 UserDictionary 的本地副本，或 null |
| `operation` | 固定 `inspect_message_v1` |

request 不含 `source_message`、`transport_message`、expected receipt、评分断言或 provider/key 等字段。adapter 不接收路径、环境变量或可执行工具。

### 输出：`AdapterResponse` 与 `MockReceipt`

`AdapterResponse` 字段为 `status`（`ok\|error`）、`raw_output`（str 或 null）、`error`（EvalError 或 null）。ok 时 error=null；error 时 raw_output=null。`MockReceipt` 字段固定为 `receipt_version='mock-receipt-v1'`、`operation='inspect_message_v1'`、`message: MessageV1`、`message_sha256`、`actions: list[str]`，且 actions 必须为空列表。

message hash 是 `MessageV1.model_dump()` 按上述规范 JSON 序列化（不含 LF）的 UTF-8 SHA-256。此哈希是校验手段，不是理解证据；评分还要逐字段比较，不能仅看 hash、source status 或“ok”。响应 validator 也检查 namespace、词典版本、operation、hash 与 actions；把格式/结构错误归为 invalid response。

### 逐尝试：`AttemptRecord`

每行包含 `record_version='offline-eval-v1'`、`record_id`、`scenario`、`source_message_id`、`condition`、非空且与 case 相同的 typed `metadata_selection: MetadataSelection`、`request`、`request_sha256`、`adapter_id`（只允许 `deterministic-mock-v1` 或测试专用 `scripted-mock-test-v1`）、`response`、`decoded_message`（或 null）、`decode_status`（`ok\|error\|not_attempted`）、`receipt_status`（`valid\|invalid\|absent`）、`error`、`transition`（`initial\|retry\|fallback_json`）、`fallback_required`、`fallback_performed`、`retry_performed`、`attempt_score`。

`decoded_message` 是从回执独立恢复并验证的消息，而非 source 的复制。adapter 解码失败时为空；解码成功但回执损坏时可为空并保守记未恢复。错误包含稳定 `stage`（`render\|decode\|adapter\|response\|policy`）、`code`、固定简短 `message`、`retryable`；不把 traceback、绝对路径、时间或环境内容写入可复现核心记录。底层 codec 无细分错误时统一 `decode_rejected`，不靠正则猜异常字符串来分类。

### manifest 与 report

manifest 固定 `manifest_version='offline-eval-manifest-v1'`，包含 contract/adapter/scorer 版本、`code_revision`、两组字节哈希与计数、完整词典快照与哈希、选中 formats/conditions、规范化规则、hard_fields、重试/回退策略和固定 disclaimer。`run_id` 是不含 run_id 自身的 manifest 规范 JSON 哈希；输出目录、墙钟时间、机器名、耗时不写入核心文件。未来修订规则必须升级 scorer/contract 版本，不能覆盖旧语义。

report 固定 `report_version='offline-eval-report-v1'`，含 `run_id`、`adapter_kind='mock'`、`pipeline_only=true`、disclaimer 和 `scenarios[diverse|repetitive].conditions[...]`。每条件保存 logical_case_count、attempt_count、initial 指标、final 指标和恢复事件计数。数组按 scenario 固定顺序 diverse/repetitive、源文件行序、条件顺序 NL/JSON/AC1/AC2/AC1-dictionary/AC2-dictionary、attempt_index 排列。

输入文件结构错误或固定语料哈希不匹配属于整次运行的配置错误：退出 1，不留下看似完整报告。case 内 codec/mock/回执失败则保留记录并继续；完成输出不等于所有 case 达标，报告含 `acceptance_passed`。

## 五类指标，绝不合成一个总分

每个 scenario/condition 单独报告，分母用逻辑 case，不用尝试数。`initial` 只取 attempt 0，`final` 取终态；恢复成功不能抹平首次失败。安全故障测试与 clean 主矩阵分开，不混入 30 条语料分母。

| 类别 | 评分定义 |
| --- | --- |
| 解码 / 字段保真 | `decode_success_count/N`；`full_message_equal_count/N`；11 个字段逐项 `field_equal_counts[field]/N`；`field_fidelity_rate=equal_field_total/(11*N)`。不可解码/缺字段按 0 项相等计，另报 `unrecovered_field_count`。比较严格类型和值，列表顺序/重复、null/空、metadata 均参与。 |
| 硬约束保持 | `hard_fields=('task','blocker','next_step','evidence','constraints','approval_required')`，与旧 critical 字段集合一致。逐字段精确相等作为保守代理；`hard_field_preservation_rate=equal_hard_total/(6*N)`；`hard_constraint_case_pass_count` 要求六项全等；另报 `changed_hard_field_count` 和 `unrecovered_hard_field_count`，后二者之和为 loss。不是语义级安全判断。 |
| 任务完成 | `mock_task_completed_count/N`：回执严格有效、operation 正确、11 字段全等、hash 正确、actions=[] 才通过。source.status=completed、failed 或 cancelled 都不直接影响此指标；它们只是待保留数据。字段缺失/未经 oracle 比较的 ok 回执不能得分。 |
| 回退 | 分别报告 `fallback_required_case_count/N`、`fallback_performed_case_count/N`、`fallback_recovered_case_count`。strict decode/response failure 或 oracle mismatch 要求 fallback；临时 adapter 故障先走有界 retry。仅保留原文并不等于发生回退。回退后的成功不提高 initial 指标。 |
| 重试 | `retried_case_count/N`、`retry_attempt_count`、`retry_recovered_case_count`。只把同一输入格式与 payload 的再次调用计 retry；改发原 JSON 是 fallback，不计 retry。没有执行重试时计 0，不能报告“重试成功率 100%”；其成功率分母为零时用 null。 |

所有比率在 JSON 中保留精确分子/分母和 float；分母为 0 时 rate=null。解码失败时字段指标不是 null，以免排除最差输入而虚增；只有真正无分母的 rate 才为 null。没有成本或实际模型任务成功指标；旧 tokenizer 结果不自动外推到本报告。

示例：一个 case 第一尝试临时错误，第二尝试同 payload 成功：attempt_count=2、retried_case_count=1、retry_attempt_count=1、retry_recovered_case_count=1，fallback_performed=0，initial mock completion=0，final=1。另一个首次 AC 解码失败、原 JSON 回退成功的 case：attempt_count=2、retry_attempt_count=0、fallback_required=1、performed=1、recovered=1，初始字段保真仍为 0，最终为 1。

## 确定性 mock 与有界恢复

生产 mock 是无状态纯本地实现：`invoke(request: EvalRequest) -> AdapterResponse`，只依赖 request；不读取 oracle、环境变量、文件内容或时钟，不使用随机、sleep 或网络。相同 request（包括 attempt_index）返回相同 response。

故障注入使用**测试专用** `ScriptedMockAdapter`，其 `adapter_id` 固定为 `scripted-mock-test-v1`；生产 `DeterministicMockAdapter.adapter_id` 固定为 `deterministic-mock-v1`。按显式的 `case_id + attempt_index` 脚本返回固定错误、损坏回执或字段变更；脚本由测试构造，不从自然语言内容判断，更不借模型评分。两者共用接口，但身份不得互相冒充；`scripted-mock-test-v1` 仅用于测试记录，不加入生产 CLI adapter 选项。脚本和 oracle 不传给生产 mock。

`EvalPolicy(fallback_mode: 'record_only'|'local_original_json'='record_only', max_retries: int=0)`，max_retries 只允许 0 或 1。本阶段 CLI 固定默认策略且不开放恢复开关；SDK 测试可显式选择恢复策略。

状态机：initial → 若 retryable adapter 错误且预算允许，最多同 payload retry 一次 → 仍失败或不可重试错误，标记 fallback_required → 仅在 local_original_json 且保留源通过严格验证时，最多发送一次原始 canonical JSON 给同一个本地 mock → 终态。回退请求禁用词典，保留 source 的原始 dictionary_version；JSON parser 可保留非空版本数据，但不能借此解析任何 alias。回退不再重试或再次回退。单 case 最多三次调用，不存在循环。

JSON/NL 的字面 MessageV1 可以携带 dictionary_version 元数据，但不得据此自行启用词典。codec 条件依旧要求所选 namespace/version 匹配。正常 clean 数据版本为 null；字典条件选择 transport 版本必须在 metadata_selection 中公开，并遵守三态规则与示例；`metadata_selection` 始终存在，不可用整个对象的 null 表示“未选词典”。

render 阶段失败也产生 attempt 0 的 `decode_status=not_attempted`、response absent/error 记录，不调用 adapter；request payload 以空字符串配 `stage=render` 标记，不能冒充真正发过消息。其字段/完成得分为 0。配置/输入加载错误则不进入 case 状态机。恢复后的 oracle：原始条件的 initial 始终对 transport；fallback 尝试的 attempt_score 对 source；final 总体要求恢复 source 内容且明确 metadata_selection 的回退变化，不把 transport dictionary_version 的不同算成丢失。报告同时存 `final_oracle='source'|'transport'`，绝不悄悄切分母或隐藏版本变化。

## 错误与安全用例

以下都在批准后的新测试中用内存副本/临时文件构造，不改现有 60 条语料。断言既检查预期拒绝，也检查“格式合法但内容被改”能被评分抓到。

| 用例 | 构造与预期 |
| --- | --- |
| 否定 | s07/s20、重复组；删“不要”、把 Do not 改 Do、删多语言否定。若仍 schema-valid，decode 可成功但字段/硬约束/完成失败；不能把语法成功当安全保持。 |
| 权限 / 预算 | s06/s12/s15/s26/s30 与 repeat-v1-04；把 `$25` 改 `$250`、改日期或 approval_required，注入 actions 非空，或把 completed/false 当许可。必须抓出差异/无效回执，不执行任何动作。 |
| 缺字段 | 在 JSON/NL、AC1/AC2 中逐一省掉十一字段（wire 为删除对应槽/项），必须拒绝、0 完成、fallback_required；不得补 null、false 或默认列表。 |
| 词典错配 | AC1/AC2 相同本组词典正例；错 user、错 version、缺词典、未知 alias。拒绝，无自动选版本/跨用户查询；record_only 不进行回退。 |
| 损坏消息 | AC1 非规范 percent escape，AC2 截断/多槽/错 enum/未知前缀；JSON 重复键/多余键/尾随内容，NL 缺标签/乱序/尾随内容，非法 UTF-8/孤立 surrogate。case 错误与文件级错误按前述分开。 |
| 边界保真 | s08/s17/s22/s27，重复组 Unicode 分隔符、引号、换行、反斜杠、百分号、字面 alias；严格保留 null/空/false、列表重复与尾空格。 |
| 数据不是命令 | s25/s28 与测试 sentinel；URL/路径/命令只作字符串。不读取 evidence 文件，不启动子进程，不创建 sentinel，不访问网络。 |
| 响应欺骗 / 重试 | malformed JSON、错 receipt hash、错 operation、空回执、自报 ok 但字段被改；均不能完成。retryable 一次成功/持续失败、不可重试、回退失败均验证次数上限和独立指标。 |

网络测试对 socket 连接/DNS入口设 fail-fast 哨兵，检查新模块无 provider SDK、HTTP client、凭据读取或动态执行；这是本地防回归测试，不声称 Python monkeypatch 等于 OS 沙箱。运行前若依赖或本地环境不足就停止报告，不安装、不下载、不自动放宽限制。

## 文件范围与复现入口

规划时仅新增此设计和 `docs/superpowers/plans/2026-10-20-agentcodec-offline-evaluation.md`，审批前不提交。两份已批准文档在 Task 7 与 README 一起显式纳入本地文档提交；任务清单集中在新计划，不覆盖既有 `tasks/todo.md`。

批准后预计新增 `src/agentcodec/evaluation/{contracts,formats,mock,scoring,runner}.py`、该包 `__init__.py`，以及五个对应测试文件和 `tests/test_evaluation_cli.py`；仅修改 `src/agentcodec/cli.py` 和 `README.md`。现有 schema/codec/ac2/benchmark、语料/词典、pyproject 和旧测试文件只读。若需扩大文件范围，先停下说明原因并重新获批。

新命令名为 `evaluate-offline`；必须显式提供 `--adapter mock-v1` 与三个本地输入路径；`--formats` 默认 `ac1`，未知格式、重复格式或未知 adapter 是 usage error。输出到一个不存在或为空的本地目录，写 `manifest.json`、`records.jsonl`、`report.json`；预检完成前不创建输出，已有非空目录拒绝覆盖。完整输出才返回 0；case 失败仍能返回完整报告，须看 acceptance_passed。配置/I/O错误返回1，usage返回2，不输出伪成功结果。

主复现命令、逐任务 RED/GREEN、两次 cmp、哈希保护和验收脚本见配套计划。Task 7 已执行本地复现并记录实测证据；原始设计中的预期值不替代日志。源消息数位于 `manifest.scenario_counts`，报告各组对象只有 `conditions`；计划验收示例已按实际 Task 6 实现及测试修正文档中的错误取值路径，不修改输出 schema 或验收门槛。完整分支独立审查由协调者在文档提交后安排，Task 7 自审不等于该审查。

## 验收标准

1. 正常四格式矩阵每组 120 个 case，全部恢复 11 字段，硬字段 loss=0、mock task completion=100%，主矩阵 fallback/retry 全为0。词典显式配对模式每组180个case，AC1/AC2使用同组同版本词典并通过同样门槛。不能据此宣称模型任务正确率100%。
2. 每个安全用例命中对应断言；schema-valid 约束篡改也必须被 oracle 评分捕获。恢复测试分别验证 initial/final 与独立 retry/fallback 计数，不因终态成功抹掉首错。
3. 两次相同输入/版本运行的 manifest、records、report 字节一致；输入哈希不变；旧测试全部通过，AC1 默认/golden 和旧报告语义不变。环境不足时明确阻塞，不假称已通过。
4. 本地无网络哨兵测试通过；不使用模型、凭据、服务，不安装依赖，不外发语料；分支保持本地未合并未推送。

## 后续真实 API 阶段：独立授权门槛

不在此计划中实现 provider adapter、收集 key、探测服务或发测试请求。“同意离线计划”不是任何 API 授权。

未来必须先单独提出并得到明确同意：**provider 与 endpoint、精确 model/version、外发的字段/语料与提示词内容、是否含词典和安全样本、请求数量/并发/最大重试、币种和总费用硬上限、token 上限与达到上限的停止规则、数据保留/训练政策及日志位置**。授权必须覆盖 provider/model、外发数据、费用上限三项，不能从“接一下 API”或之前的离线批准推定。超范围、换模型/服务、增加外发内容或提高费用需重新授权；凭据届时按单独获准的安全路径处理。

真实评测还需事先确定业务任务 gold、模型独立输出和安全判断标准；复用记录外壳不等于 mock 分数可与真实理解质量直接比较。

## Superpowers 审查与审批停点

本次按 brainstorming 形成完整设计，按 writing-plans 同时提供用户明确要求的**未批准实施拆分**；这不跳过设计审批、不进入实现技能。一般实现细节由提案给出默认，不再向用户追问。

Builder-check 已由独立只读审阅者完成：只读本设计文档，没有读语料/fixture、改文件或访问网络；结论为“nothing important left”。这是设计可计划性检查，不是代码审查。用户已于 2026-10-20 明确批准本设计及计划；实施沿用已选 subagent-driven development，但不进行任何真实线上模型/API 评测，也不向内部审查任务提供原始语料。自审重点还包括 oracle 泄漏、回退隐藏首错、任务成功混淆、版本选择、无分母、重复样本身份和格式分隔符。
