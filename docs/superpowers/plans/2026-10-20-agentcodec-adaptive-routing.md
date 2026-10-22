# AgentCodec 自适应编码 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在显式授权的单消息、同实例重复次数和不可变资源快照内，选择严格可逆候选中最低完整本地 ordinary-text token 成本，并提供可审计解释。

**Architecture:** 新增独立 `adaptive` 包：先严格验证配置与候选，再建立互斥文本事件账本，最后作纯函数选择。生产仅返回一个建议 payload；独立离线 runner 按预声明顺序及固定故障日程汇总，不做多消息全局路由，不修改旧 benchmark/evaluation。

**Tech Stack:** 现有 Python 3.11+、Pydantic 2、Typer、tiktoken、pytest/Hypothesis；标准库 JSON/hashlib/base64/pathlib/fractions；零新增依赖。

**Spec:** `docs/superpowers/specs/2026-10-20-agentcodec-adaptive-routing-design.md`，执行者必须读全文。会话已批准最新版完整设计及六项取舍；Task 10 仅同步设计文件的历史审批状态文字，不改变技术范围。

**状态：实施计划已获用户批准，按 subagent-driven development 执行。** 本文件的技术要求保持不变；实际完成范围以对应复选框、任务报告和审查证据为准，不把批准当作验收。Task 10 的最终全分支独立审查仍由协调者另行执行；不合并、不推送。

## Global Constraints

- 工作区固定 `/workspace/agentcodec/.worktrees/agentcodec-mvp`，本地分支 `agentcodec-auto-select`，基线 `dd117a963486767f7108cb16c6884ac2f92ff09b`；原 `agentcodec-mvp` ref 必须保持该基线，不 checkout/reset/merge/push，不新建嵌套 worktree。
- 严守离线：无网络/API、凭据、词表下载、依赖安装、语料外发、服务端或业务执行。内部子任务只处理授权本地文件；不得将原始 JSONL/词典内容放入 brief、审查包或回复。
- v1 只做 per-message selection。一个完全确定的 MessageV1，默认总共一次；明确同一完整实例 N 次才比较各固定候选重复 N 次成本，仍只返回一个 payload。模板相同但变量不同不是重复；拒绝多消息/全局优化请求。
- 十一字段类型和值严格相等；null、false、空串、空列表、列表顺序/重复、空白、Unicode 均原样保留，不 trim、不 normalization。不摘要、不语义压缩；金额、否定、日期、授权、证据由精确全等保护。
- 词典仅人工维护、私有隔离、显式启用；限定 task/blocker/next_step/evidence/constraints 的完整字符串或完整列表项。不匹配子串、不模糊匹配、不学习、不维护、不跨用户搜词。
- 路由器不清空、补填或替换 dictionary_version；每个比较域只有调用方提供的一个 canonical MessageV1。调用方另行授权构造的 transport 与 source 分域报告，不能混用节省分母。
- 未知格式/版本/枚举/alias、畸形输入、显式 owner/version/hash/epoch/历史身份冲突均 fail closed。配置错误优先于无法计量；即使原格式 JSON 也不能绕过错误词典。合法但不适用候选标 unavailable，不计作发送或 fallback。
- 新入口 opt-in；默认 allowlist 为 json/ac1；AC2 与词典候选分别显式启用。原格式缺省 AC1。生产固定 record-only、零 retry、无自动 fallback；fallback_required 只是调用方处理状态。
- 仅本地 ordinary-text token 模型；不是模型理解、业务成功、provider 性能、延迟或实际账单证明。无真实 provider/网络 ack；响应未配置时为零并注明，输出方向 excluded，不能称全 API 生命周期。
- tokenizer 必须显式；仅支持本地已 provision 的 o200k_base/cl100k_base，不猜模型映射。缺失/损坏/不可靠或必要成本未知时 cannot_compare，不造字符/字节估算，不安装或下载。
- 完整计 setup/context/payload/封装/metadata/独立同步确认；生产 retry/fallback 固定零。拼成一条文本先计完整文本，组件按固定顺序增量归因，可为负，不逐字段独立计数相加。
- 默认 min_savings_tokens=0、min_savings_ratio=0、无滞回。严格较低即胜；ties 顺序为合法原格式、无新资源、json/ac1/ac2/ac1_dictionary/ac2_dictionary。显式阈值政策另列 policy_retained 和机会成本，不称数学最小。
- 选择是纯建议；cold→proposed 只在 decision，初始化必须调用方确认并校验 epoch 后才 ready。无数据库、后台初始化、跨进程/跨用户全局缓存。
- AC1 golden/default/SDK、旧五条件 benchmark、七条件 suite-v1、offline-eval-v1 及其默认 record-only/零 retry/不初始化 tokenizer 全部兼容。不改 MessageV1、UserDictionary、wire、旧 renderer 或语料/词典。
- 默认摘要不回显原文、词条、完整 payload、自由文本或内容哈希；只用局部 case 序号。SDK 可返回 payload；CLI 仅显式本地文件输出 payload，和脱敏摘要分离；哈希只留受控本地 manifest，哈希不是匿名化。

## Review Focus

1. **坏词典与缺 tokenizer 同时出现，原格式还是 JSON：** 必须先拒绝显式配置冲突，payload=null，不借 cannot_compare 保留。测试归 Task 2、6。
2. **ready 的 per_request_context、共享 schema、重复事件 ID：** ready 不等于免费；首次 context 不另收重复 setup，共享资源取并集，历史事件重复不重计、冲突拒绝。测试归 Task 3、4。
3. **边界相等、优势随 N 消失、有效期只覆盖部分重复：** break-even 用严格不等式和整数区间，不无限外推；N 未被快照覆盖则 cannot_compare。测试归 Task 5、6。
4. **null/非空 dictionary_version、模板变量改变及被修改的模型实例：** 不修补 metadata，不把不同消息当复用；重新严格验证当前字段，并固定深拷贝快照。测试归 Task 1、2、6。
5. **摘要/异常/路径泄露与部分输出、缓存被替换：** 默认不泄露内容，写失败无伪完整结果；快照计数前后身份变化不能继续精确比较，不触发联网。测试归 Task 4、9、10。

## 审批、流程与检查点

- [x] 用户已批准本实施计划后启动 Task 1；审批前未执行本计划命令或提交。
- [x] 启动时只读核对分支、HEAD、原 ref 与未提交文件；保留现有设计/计划/checklist，不覆盖用户变更。用技能 `sdd-workspace` 为本计划建立独立 git-ignored 台账，首行标本计划绝对路径；不读写其他计划台账。
- [ ] 每任务由新实现子任务执行 RED→GREEN→自审→本地提交，再由新审查子任务检查规格与质量，修复后复审；不得并行修改共享文件。审查包只含必要代码/合成测试与脱敏证据，不含语料、词条或实际 payload。
- [ ] 每任务记录 RED 失败断言、GREEN 命令/退出码、精确本地 commit、审查结论（Task 1–9 已记录，Task 10 实现证据完成后交协调者作最终审查）；不把缺依赖等环境失败当 RED，不把跳过当通过。已安装环境不可用时报告阻塞，不能安装。
- [x] 每 2–3 任务 checkpoint 核对接口、离线边界及旧兼容回归（A–D 证据已记录）；只有范围/安全偏差才重新请求批准，不机械重复询问是否继续。任何变更若突破已批准约束立即停止并报告。
- [ ] 最终由未参与实现的独立审查者检查整个 `dd117a9..HEAD` 分支而非最后一次提交；使用实际可用的最高审查能力，不虚构模型选择。所有重要问题解决后再报告；不合并、不推送、不删除该 worktree。

## 文件地图与依赖

以下为未来文件，不表示本轮创建。每任务最多四个功能文件；台账及本 checklist 的对应状态更新不算新功能文件。

| 文件 | 职责 | 归属 |
| --- | --- | --- |
| `src/agentcodec/adaptive/__init__.py` | 惰性导出 select_encoding，无初始化副作用 | 1、6 |
| `src/agentcodec/adaptive/contracts.py` | 请求/资源/文本事件/decision 的严格契约 | 1 |
| `src/agentcodec/adaptive/candidates.py` | 固定五候选、严格 round-trip、不可变输入 | 2 |
| `src/agentcodec/adaptive/resources.py` | 身份/历史/epoch 验证、建议与显式确认 | 3 |
| `src/agentcodec/adaptive/meter.py` | 本地准确 tokenizer、完整文本与增量分项 | 4 |
| `src/agentcodec/adaptive/costs.py` | 完整重复成本、历史、break-even/节省 | 5 |
| `src/agentcodec/adaptive/selector.py` | 单消息选择、保留原格式、政策与 ties | 6 |
| `src/agentcodec/adaptive/offline.py` | 独立离线场景契约、固定故障路径模拟 | 7 |
| `src/agentcodec/adaptive/benchmark.py` | 独立矩阵、脱敏报告与受控 manifest | 8 |
| `src/agentcodec/adaptive/cli.py` | 新命令的严格本地 I/O、摘要/显式 payload | 9 |
| `src/agentcodec/cli.py` | 仅注册两个新命令，不重构旧函数 | 9 |
| `tests/test_adaptive_contracts.py`、`test_adaptive_candidates.py`、`test_adaptive_resources.py`、`test_adaptive_meter.py`、`test_adaptive_costs.py`、`test_adaptive_selector.py`、`test_adaptive_offline.py`、`test_adaptive_benchmark.py`、`test_adaptive_cli.py`（均在 `tests/`） | 对应任务的合成单元测试 | 1–9 |
| `tests/test_adaptive_compatibility.py` | 旧 API/报告/默认及无副作用独立守卫 | 10 |
| `README.md`、`SECURITY.md` | 新入口、限制、旧负结果与资源边界 | 10 |

只读复用：`schema.py`、`codec.py`、`ac2.py`、`dictionary.py`、`benchmark.py`、`evaluation/*`、旧 tests、`pyproject.toml`、`bench/baselines.py`、两组 JSONL 和词典。必要适配放新包；若不得不改只读契约，先报告，不静默扩范围。`evaluation.formats.render_input/parse_input` 可严格复用 JSON/AC1/AC2；JSON 不能传 dictionary，JSON 中非空版本仅作 metadata。

依赖：`1 → 2 → 3 → 4 → 5 → 6 → 7 → 8 → 9 → 10`。2/3/4 在接口冻结后技术上可独立，但按用户选择逐任务审查，不并行写共享文件。SDK 不导入 `adaptive.offline` 或 `adaptive.benchmark`；offline 单向调用 selector/costs。允许按需复用既有 benchmark 的纯 renderer/本地缓存守卫，但导入不能初始化 tokenizer 或执行基准。

## 共享接口：先冻结再消费

下列字段与签名是实施合同，不是实现代码。严格模型 `strict=True, extra='forbid'`；拒绝 bool 充整数、NaN/Infinity、surrogate 和隐式类型转换。所有运行入口重新验证当前模型原始字段；深拷贝 MessageV1、词典和列表后固定为内部快照，不信任可变 Pydantic 实例。稳定错误码不夹带原文。

### Task 1 产出的类型

- `CandidateId` 固定 `json|ac1|ac2|ac1_dictionary|ac2_dictionary`；`ResourceState=builtin|ready|cold|invalid`；`Exposure=builtin|once_per_scope|per_request_context`。
- `TokenizerProfile(encoding_name: str, library_version: str, vocabulary_sha256: str, renderer_version: str='adaptive-text-v1', accuracy: exact_local_text|surrogate_only|unknown, mapping_basis: str|None)`；模型名不触发自动映射，v1 不接受其他 renderer_version。profile 不可靠属于计量不足而非造估算。
- `ResourceIdentity(scope_id,receiver_id,user_id,resource_kind,version,content_hash,tokenizer_profile_id: str)`；`ResourceSnapshot(identity, state, exposure, epoch:int, valid_through_repeat:int|None, readiness_basis:str|None, setup_text:str|None, context_text:str|None, independent_setup:bool=False)`。有效性用调用方快照 epoch 和覆盖次数，不用机器时钟；None 表示覆盖不明。共同协议/schema 用同一身份去重；候选依赖由 ReceiverCapabilities 明确声明，不自动假设 AC1/AC2 共享不同协议。
- `HistoricalEvent(event_id:str, identity:ResourceIdentity, profile_id:str, tokens:int, kind:setup|context|payload|retry|fallback|other)`；`ScopeSnapshot(scope_id,receiver_id,user_id:str,epoch:int,history_complete:bool,historical_events:tuple[HistoricalEvent,...],resources:tuple[ResourceSnapshot,...])`。重复相同 ID/内容去重，ID 内容冲突拒绝。事件成本是互斥全文请求成本，历史不再逐组件重复累计。
- `DictionarySelection(enabled:bool=False, dictionary:UserDictionary|None=None, content_hash:str|None=None)`；hash 定义为严格模型 `model_dump` 后 sorted-key/compact/ensure_ascii=False UTF-8 无 LF 的 SHA-256；snapshot 显式复制完整词典，不只命中条目。输入文件原始字节 hash 与该规范内容 hash 分列。
- `ReceiverCapabilities(receiver_id:str, supported_candidates:tuple[CandidateId,...], candidate_resources:dict[CandidateId,tuple[ResourceIdentity,...]], epoch:int)`；这是本地声明而非远端认证。builtin 必须由调用方确认确定性 decoder 真正内置，不因安装 SDK 而推断远端能力。
- `RepeatDeclaration(unit:Literal['same_message_instance'], total_count:int, instance_sha256:str)`；instance hash 是全部十一字段规范 JSON 的 hash，不含自由模板。`CostPolicy(repeat_count:int=1, repeat_declaration:RepeatDeclaration|None=None, min_savings_tokens:int=0, min_savings_ratio:float=0.0, fallback_mode:Literal['record_only']='record_only', max_retries:Literal[0]=0, assumption:Literal['deterministic_clean']='deterministic_clean')`。N>1 必须有总次数与全实例 hash 一致的声明；显式声明即使 N=1 也核对。ratio 为有限 [0,1]，token 阈值非负整数；零默认不启用滞回。
- `TextEnvelope(prefix:str='', suffix:str='', final_lf:bool=False)`；payload 目标文本为 prefix+完整 renderer 输出+suffix+显式 LF。CLI 文档输出 LF 不自动成为传输边界。上下文与 payload 拼接为同请求时由 meter 一次计全文。
- `SelectionRequest(contract_version:Literal['adaptive-selection-v1'], message:MessageV1, expected_user_id:str, tokenizer:TokenizerProfile, capabilities:ReceiverCapabilities, snapshot:ScopeSnapshot, original_format:CandidateId='ac1', allowlist:tuple[CandidateId,...]=('json','ac1'), dictionary:DictionarySelection=关闭值, policy:CostPolicy=默认值, envelope:TextEnvelope=默认值)`。空/重复 allowlist、未知字段、消息列表或 workflow/global 参数拒绝。合法原格式的保留要求仍在 allowlist 且受接收能力支持；不借计量失败扩大 allowlist。
- `CandidatePayload(candidate_id:CandidateId, declared_format:json|ac1|ac2, payload:str|None, eligibility:eligible|unavailable, reason_codes:tuple[str,...], required_resources:tuple[ResourceIdentity,...])`；所有五个 ID 都出现在 decision，未启用标 unavailable。异常 round-trip/未知 alias 不输出部分恢复，属完整性拒绝，不因其他便宜候选存在而忽略。合法声明的 invalid 资源使依赖候选 unavailable；显式身份冲突仍整次拒绝。
- `TextComponent(kind:setup|context|payload|retry|fallback|other,text:str,resource_identity:ResourceIdentity|None)`；`TextAttempt(event_id:str,attempt_kind:setup|initial|retry|fallback|other,components:tuple[TextComponent,...],simulated:bool,multiplicity:int=1)`。一次 request 是一个 TextAttempt，独立请求是独立事件；同文本同资源且有效性覆盖的重复可压成 multiplicity。`MeasuredAttempt(event_id:str,attempt_kind:setup|initial|retry|fallback|other,total_tokens:int,component_tokens:tuple[int,...],simulated:bool,multiplicity:int=1)`；total_tokens 和 component_tokens 是单次，汇总只乘 multiplicity 一次，分项可负但单次总量非负。
- `CandidateCost(candidate_id, remaining_expected_tokens:int|None, scope_spent_tokens:int|None, scope_lifecycle_expected_tokens:int|None, attempts:tuple[MeasuredAttempt,...], component_totals:dict[str,int]|None, excluded:tuple[str,...], reason_codes:tuple[str,...], new_resource_count:int)`；component_totals 恰好 setup/context/payload/retry/fallback/other，生产 retry/fallback=0。单条精确重复可用事件 multiplicity 整数记录，不分配 N 份 payload。
- `BreakEvenResult(kind:finite|infinity|interval|not_applicable, first_strict_win:int|None, winning_interval:tuple[int,int|None]|None, assumptions:tuple[str,...], reason_codes:tuple[str,...])`；infinity 用 kind 表达，不输出非 JSON Infinity。
- `SelectionDecision(contract_version, status:selected|preserved_without_comparison|fallback_required, comparison_status:exact_local_text|surrogate_only|cannot_compare, reason_codes, selected_candidate:CandidateId|None, declared_format:str|None, payload:str|None, candidates:tuple[CandidatePayload,...], costs:tuple[CandidateCost,...], minimum_candidate:CandidateId|None, policy_retained:bool, baseline_candidate:CandidateId|None, savings_tokens:int|None, savings_ratio:float|None, opportunity_cost_tokens:int|None, opportunity_cost_upper_bound:int|None, breakeven:dict[CandidateId,BreakEvenResult], metadata_selection:dict[str,str|None], proposed_resources:tuple[ResourceSnapshot,...], snapshot_epoch:int, decision_id:str, profile_id:str, policy_version:Literal['adaptive-policy-v1'], renderer_version:Literal['adaptive-text-v1'], assumptions:tuple[str,...], excluded:tuple[str,...])`。metadata_selection 只含 selection_status='unchanged'、source_dictionary_version、transport_dictionary_version，两个版本严格相等；SDK decision_id 哈希输入/政策/快照而非时间路径，仅受控本地保存。

### 约束和错误通道

解析/配置结构错误对 SDK 抛现有 `FallbackRequired`，code 用固定新错误码；`select_encoding(request)` 捕获边界内的配置/完整性错误，返回 fallback_required/cannot_compare、payload=null。CLI 输入解析也生成同版脱敏失败 decision。计量不足合法保留时 comparison_status=cannot_compare，reason_codes 区分 tokenizer_unavailable/profile_unreliable/surrogate_only/required_cost_unknown/resource_validity_unknown。profile 是 surrogate 时不精确排名，保留成功可标 comparison_status=surrogate_only；所有无法比较成本/节省为 null。配置错误始终压过这个分支。

容量工程值：新入口单个输入文件/单条文本快照上限 8 MiB UTF-8（含完整词典/上下文），SDK repeat_count 上限 1,000,000，快照历史/离线事件总数上限 100,000，离线每组消息上限 10,000；计数前检查展开事件总量，超限拒绝不截断。SDK 只接受 [1,1,000,000]；offline whole_corpus_repeats 接受 [0,1,000,000]，0 专属 dry-run。旧入口不添加这些新限制。

---

### Task 1：严格契约与单实例边界

**Files:** Create `src/agentcodec/adaptive/__init__.py`、`src/agentcodec/adaptive/contracts.py`、`tests/test_adaptive_contracts.py`。依赖：无。

**Interfaces:** 产出上节全部类型及 `canonical_text(value:object)->str`、`profile_id(profile:TokenizerProfile)->str`、`instance_id(message:MessageV1)->str`、`freeze_request(request:SelectionRequest)->SelectionRequest`。规范文本 sorted keys、compact、Unicode 原值、无 LF；包导入不加载 tokenizer。

- [x] **Step 1 — 编写失败断言。** `test_defaults_are_opt_in_and_record_only` 核对 allowlist=('json','ac1')、original_format='ac1'、repeat_count=1、record_only/0；`test_repeat_declaration_pins_full_instance` 核对 N=2 必须匹配完整 message hash；改一个变量/尾空格/列表顺序即拒绝。`test_reject_coercion_multi_message_and_limits` 覆盖 bool/float/string 次数、0、负数、超限、workflow/global、重复候选、每字段缺失。`test_mutated_models_are_revalidated_and_frozen` 验证后改输入列表/词典不改变快照，再次输入已被篡改类型的模型必须拒绝（Review Focus 4）。`test_decision_invariants_and_json_safe_infinity` 断言失败 payload=null、不可比较 savings=null、禁止 NaN 和额外字段。
- [x] **Step 2 — RED。** `.venv/bin/python -m pytest tests/test_adaptive_contracts.py -q`；预期新模块/上述严格不变量失败，不接受 collection 环境损坏为 RED。
- [x] **Step 3 — 最小实现。** 仅实现类型、边界验证、规范身份及深快照；不实现 codec、计数器或多消息调度。可选 future_reuses 仅作 CLI adapter，将 0/1 明确转换为总数 1/2，不在 SDK 增加第二套次数字段。
- [x] **Step 4 — GREEN。** 同命令全部通过；`test_import_is_inert` 阻断网络/进程执行/凭据访问并断言导入未初始化 tokenizer。断言各默认和容量边界，空值/false 不被合并。
- [x] **Step 5 — 本地提交与独立审查。** `git add src/agentcodec/adaptive/__init__.py src/agentcodec/adaptive/contracts.py tests/test_adaptive_contracts.py && git commit -m "feat: define adaptive selection contracts"`。审查跨任务接口与深快照；不使用 `git add .`。

### Task 2：固定候选与严格等价域

**Files:** Create `src/agentcodec/adaptive/candidates.py`、`tests/test_adaptive_candidates.py`。依赖：1。

**Interfaces:** 消费冻结 SelectionRequest；产出 `build_candidates(request:SelectionRequest)->tuple[CandidatePayload,...]`、`validate_explicit_selection(request:SelectionRequest)->None`。只调用既有 encode/decode 或 evaluation.formats 的明确格式接口；JSON 传 dictionary=None，不展开 alias；返回顺序固定五 ID。显式词典先核对 owner/content_hash，非空版本再核对版本；null 输入的同 owner 合法显式词典只能标不适用，不能填版本。

- [x] **Step 1 — 写失败断言。** `test_null_version_never_enables_dictionary` 对默认两候选及显式 AC2 验证 source 未变、词典候选 unavailable；`test_non_null_domain_keeps_json_metadata` 断言同域 JSON 与对应词典候选全字段一致、无词典 AC 不适用。`test_explicit_conflict_wins_over_json_and_missing_meter` 覆盖 owner/hash/非空 version 冲突及坏 schema，即使 JSON 原格式也抛完整性拒绝（Review Focus 1）。`test_five_candidates_exact_round_trip` 合成消息覆盖十一字段、预算/否定/日期/approval、空/false、Unicode/组合字符、分隔符、重复列表与尾空白（Review Focus 4）。
- [x] **Step 2 — RED。** `.venv/bin/python -m pytest tests/test_adaptive_candidates.py -q`；预期缺候选接口或等价域断言失败。
- [x] **Step 3 — 最小实现。** 先显式配置验证，再 allowlist/能力/词典适用性，再 render→声明格式严格 decode→逐字段全等。低覆盖率保留原串；禁止 substring/alias 学习；没有自然语言/DRIXL 候选。不为适应非空版本修改旧 codec。
- [x] **Step 4 — GREEN。** 同命令；追加 `test_corrupt_wire_alias_duplicate_key_is_integrity_failure`，以测试替身在候选生成边界注入错前缀/enum/alias/重复键/部分恢复，整次拒绝。`test_capabilities_and_allowlist_gate_every_candidate` 确认每个禁用候选均无可发送 payload、不计 fallback；expected_user_id 不匹配失败。Review fix round 增加合法词典但 `enabled=False` 的行为断言，并把 invalid-resource 门控验证扩至五候选；变异验证相应 gate 被移除时测试失败。
- [x] **Step 5 — 提交与审查。** `git add src/agentcodec/adaptive/candidates.py tests/test_adaptive_candidates.py && git commit -m "feat: validate adaptive candidates without metadata rewriting"`；审查全字段、五候选 gating 与优先级。
- [x] **Step 6 — Scoped fix re-review。** 独立 reviewer 确认显式词典 opt-in 与五候选 invalid-resource 覆盖均已补齐，fix diff 无新 Critical/Important/Minor breakage。

**Checkpoint A（1–2 后）：** [x] `.venv/bin/python -m pytest tests/test_adaptive_contracts.py tests/test_adaptive_candidates.py tests/test_codec.py tests/test_ac2.py tests/test_evaluation_formats.py -q`；全部通过、旧 golden 不变、无 tokenizer 初始化。原 ref 仍为基线。

### Task 3：资源快照、历史与显式确认

**Files:** Create `src/agentcodec/adaptive/resources.py`、`tests/test_adaptive_resources.py`。依赖：1–2。

**Interfaces:** `validate_snapshot(request:SelectionRequest)->ScopeSnapshot`；`required_resources(candidate:CandidatePayload,snapshot:ScopeSnapshot)->tuple[ResourceSnapshot,...]`（依赖并集）；`propose_resources(resources:tuple[ResourceSnapshot,...])->tuple[ResourceSnapshot,...]`；`confirm_initialization(snapshot:ScopeSnapshot, *, expected_epoch:int, event:HistoricalEvent, succeeded:bool)->ScopeSnapshot`。确认返回新快照，epoch 成功变更时加一，失败保持原状态；旧 epoch 抛 FallbackRequired，不自动重算/保存。proposed 在 decision 描述中用 reason/assumption 标记，不给 ResourceState 添加第五种持久状态。

- [x] **Step 1 — 写失败断言。** `test_selection_proposal_never_mutates_ready` 断言 cold 仍 cold；`test_confirm_requires_epoch_and_success` 断言失败/未执行不 ready、旧 epoch 拒绝、成功新 epoch 且原快照不变。`test_resource_identity_rejects_conflicts` 覆盖 scope/receiver/user/profile/version/hash/epoch 冲突、同版本异 hash；其他 scope 历史不转嫁、显式混入异身份账本拒绝。
- [x] **Step 2 — RED。** `.venv/bin/python -m pytest tests/test_adaptive_resources.py -q`；正式 RED 40 failed（缺少实现模块），并经自审追加 invalid-builtin 用例的 1-failed RED。
- [x] **Step 3 — 最小实现。** 校验完整身份及 ready 确认依据；按唯一身份取资源并集。历史相同 event_id/内容只一次，异内容拒绝；history_complete=false 且 ready/remaining 可信只让累计为 null，缺失影响 ready 则标未知。只返回新对象，不写文件、不建全局状态。
- [x] **Step 4 — GREEN。** 同命令；`test_history_dedup_conflict_and_incomplete` 锁定以上三态。`test_ready_per_request_context_still_requires_exposure` 验证 ready 仍需完整 context；`test_repeat_validity_unknown_is_not_free_setup` 对 N 未被有效覆盖标资源成本不明（Review Focus 2、3）。builtin 无文本 setup，但须有调用方内置依据。最终 41 focused tests 和 1,323 全套测试通过。
- [x] **Step 5 — 提交。** `git add src/agentcodec/adaptive/resources.py tests/test_adaptive_resources.py && git commit -m "feat: validate local resource epochs and history"`；提交仅含两个指定文件。
- [x] **Step 6 — 独立审查。** 新鲜只读 reviewer Approved，未发现 Critical、Important 或 Minor；审查仅批准 Task 3，不替代后续成本层/选择器验收。

### Task 4：离线精确计量与互斥事件账本

**Files:** Create `src/agentcodec/adaptive/meter.py`、`tests/test_adaptive_meter.py`。依赖：1、3。

**Interfaces:** `validate_profile(profile:TokenizerProfile)->None`；`measure_attempt(attempt:TextAttempt,profile:TokenizerProfile)->MeasuredAttempt`；`measure_attempts(attempts:tuple[TextAttempt,...],profile:TokenizerProfile)->tuple[MeasuredAttempt,...]`。计量不足抛固定 `MeasurementUnavailable(reason_code)`（本模块定义，selector 捕获），不能包含原文。生产无任意可执行插件；合成 stub 仅测试 monkeypatch 本模块计数边界。

- [x] **Step 1 — 写失败断言。** `test_whole_text_and_signed_prefix_deltas` 使用非可加 tokenizer stub：拼接全文 token 总量不同于各段独立和，断言分项按 setup/context/payload/其他的实际序列前缀差求得、允许负增量、总和精确还原全文。`test_initial_context_not_double_setup` 对 per_request_context 首次正文只收 context，只有显式 independent_setup 的独立同步请求才另外收 setup。`test_shared_resource_and_duplicate_event_charged_once` 锁定并集及 ID 去重/冲突拒绝（Review Focus 2）。
- [x] **Step 2 — RED。** `.venv/bin/python -m pytest tests/test_adaptive_meter.py -q`；stub 测试不依赖真实词表。正式 RED 为 21 个预期 ModuleNotFoundError；自审补强 whole-request-first 时另观察一项针对性 RED。
- [x] **Step 3 — 最小实现。** 沿用旧 `_VOCAB_HASHES` 校验和 `_get_encoding`/encode_ordinary 的离线路径，但新 adapter 每次冻结 profile/缓存内容身份，不能因旧 lru_cache 绕过本次校验。初始化前后比对本地词表字节 hash/库版本和已建编码身份；检测替换即 MeasurementUnavailable。禁止在检查不通过时调用 tiktoken 获取词表。每一条完整请求独立计数，拼接组件用 prefix token 差归因，按 attempt 维度汇总一次，组件只作交叉解释。
- [x] **Step 4 — GREEN。** 同命令；`test_unknown_missing_corrupt_surrogate_never_downloads` 对未知词表/无缓存/坏缓存/错误库版本/surrogate profile 断言无数值、不调用 socket/HTTP/subprocess/安装；即使已初始化缓存也需复核。`test_cache_replacement_and_lf_boundary` 模拟检查间替换须 cannot_compare，完整文本明确 LF 开关；special-token-looking 字符串按普通文本。两种已 provision 词表用独立 encode_ordinary 复计同全文差为 0，缺少词表报告环境阻塞不补装（Review Focus 5）。最终 21 focused tests、1,344 全套测试通过。
- [x] **Step 5 — 提交。** `git add src/agentcodec/adaptive/meter.py tests/test_adaptive_meter.py && git commit -m "feat: meter immutable local ordinary text attempts"`；提交仅含两项指定文件。
- [x] **Step 6 — 独立审查。** Spec Compliance PASS，Task quality APPROVE WITH NITS；无 Critical/Important。将事件 ID 冲突中“仅 text 改变”和“仅 resource_identity 改变”用例建议登记为非阻塞 Minor，交最终分支审查一并 triage。

### Task 5：完整重复成本与严格 break-even

**Files:** Create `src/agentcodec/adaptive/costs.py`、`tests/test_adaptive_costs.py`。依赖：1–4。

**Interfaces:** `price_candidate(candidate:CandidatePayload,request:SelectionRequest)->CandidateCost`；`break_even(setup_a:int, variable_a:int, setup_b:int, variable_b:int)->BreakEvenResult`；`compare_costs(cost:CandidateCost,baseline:CandidateCost)->tuple[int|None,float|None]`（baseline−cost，比例零分母为 null）。S 是尚未支付 setup，V 包括每次完整有效 context/封装/payload；builtin S=0，once_per_scope 冷计一次/热不重付，per_request 每次计且不重复首次 setup。全部生产假设 deterministic_clean。

- [x] **Step 1 — 写失败断言。** `test_complete_cost_boundary_and_sunk_history` 用合成整数断言 remaining=S+N·V、lifecycle=spent+remaining，历史共同沉没不改变排名；history_incomplete 时 spent/lifecycle 为 null 而非零。`test_exposure_union_and_full_dictionary` 覆盖整词典非命中子集、共享 schema 并集、metadata/封装/LF；本地文件读取无 token 事件。`test_break_even_strict_equality_and_reversing_advantage` 断言相等不胜、两侧整数、ΔV 正/零/负、无交点/infinity、有界优势区间；不以“复用越多越好”概括负 ΔV（Review Focus 3）。
- [x] **Step 2 — RED。** `.venv/bin/python -m pytest tests/test_adaptive_costs.py -q`；预期缺公式/边界行为失败。观察到 29 项预期缺模块失败，退出码 1。
- [x] **Step 3 — 最小实现。** 以 meter 的完整 attempt 为权威，资源并集/上下文规则构造事件，重复平稳事件用整数 multiplicity 不重复分配数据。生产 R=F=0、CPU=excluded、无 ack 原因明确。ΔV>0 用严格整数 floor+1，ΔV=0 只比较 setup，ΔV<0 解正整数区间；非平稳标 not_applicable，不做永久阈值。
- [x] **Step 4 — GREEN。** 同命令；`test_validity_must_cover_all_n` 覆盖有效性只到 N−1、setup/context 文本未知，成本为 null/原因稳定；`test_break_even_matches_independent_integer_enumeration` 在合成有界小整数范围用独立枚举核对所有正整数边界，不调用被测 break_even 生成 expected。`test_no_double_sum_and_no_free_unknown_history` 验证 attempt/组件守恒、响应已在请求事件就不另加其他项。最终 29 focused、Checkpoint B 162、完整 1,373 项均通过；6 项临时变异均被测试捕获。
- [x] **Step 5 — 提交。** `git add src/agentcodec/adaptive/costs.py tests/test_adaptive_costs.py && git commit -m "feat: account complete repeat costs and break even"`；提交仅含两个指定文件。
- [x] **Step 6 — 独立审查。** Spec Compliance PASS，Task quality APPROVE WITH NITS；无 Critical/Important。记录一个非阻塞 Minor：使用非可加 oracle 锁定多条独立 setup request 边界，交最终分支审查 triage。

**Checkpoint B（3–5 后）：** [x] `.venv/bin/python -m pytest tests/test_adaptive_resources.py tests/test_adaptive_meter.py tests/test_adaptive_costs.py tests/test_benchmark.py -q`；162 项通过，旧 benchmark 也在范围内；实现者报告检查资源不落盘、无网络。若真实本地词表不齐，checkpoint 阻塞，不宣称通过。

### Task 6：单消息选择器与无法比较时保留

**Files:** Create `src/agentcodec/adaptive/selector.py`、`tests/test_adaptive_selector.py`；Modify `src/agentcodec/adaptive/__init__.py`（仅惰性公开新入口）。依赖：1–5。

**Interfaces:** `select_encoding(request:SelectionRequest)->SelectionDecision`；消费 build_candidates/validate_snapshot/price_candidate/break_even；不接受 workflow、列表或恢复策略。SDK 返回完整 payload 仅给调用方，公共摘要另由 Task 9 投影。decision_id 由冻结输入/profile/policy/state 规范哈希确定，输入字段变化须重算。

政策细则：baseline 为同域 eligible 原格式，不可用则 baseline/savings 为 null，不另找跨域分母。令 D=C_incumbent−C_min、H=max(min_savings_tokens,min_savings_ratio·C_incumbent)，仅 D>H 切换；按 ratio 的十进制字符串转 Fraction 比较，避免二进制浮点相等边界漂移。policy_retained 时机会成本为 D，上界为 floor(H)；否则机会成本为0、上界为0。精确计量不完整的必要候选不能被静默忽略并宣称全 allowlist 最优，应走 cannot_compare；明确 unavailable 不属于必要比较对象。

- [x] **Step 1 — 写失败断言。** `test_default_minimum_matches_independent_candidate_enumeration` 用独立成本表遍历同域/状态/N 下 eligible 候选，断言 selected 成本=min、predicted regret=0。`test_ties_incumbent_resource_then_fixed_order` 分别钉住三层 ties。`test_explicit_threshold_requires_strictly_greater` 对 D 等于/两侧阈值确认只有严格大于才切，policy_retained、真实 minimum、未实现节省及上界完整；无合法 incumbent 则安全选最低。
- [x] **Step 2 — RED。** `.venv/bin/python -m pytest tests/test_adaptive_selector.py -q`；预期 selector 缺失/选择或状态断言失败。观察 85 项缺功能 RED。
- [x] **Step 3 — 最小实现。** freeze→显式输入/配置完整性→能力/资源→严格候选→精确计量→同单实例固定 N 成本→ties/可选阈值。合法原格式仍 eligible 且安全验证通过时，计量不足可 preserved_without_comparison，否则 fallback_required、payload=null；不自动改格式、不 retry、不发送、不确认资源。original_format 不在 allowlist 或不受接收支持则不能被保留。expanded-event 容量拒绝不吞成 cannot_compare。
- [x] **Step 4 — GREEN。** 同命令；`test_bad_dictionary_precedes_unavailable_tokenizer_even_json`（Focus 1）；`test_cannot_compare_preserves_only_legal_original` 对 unknown/坏缓存/surrogate/未知成本断言无虚构节省、保留成功不等于比较成功。`test_same_instance_n_and_insufficient_epoch_coverage` 验证缺声明、变量变化、N=0/超限/多消息拒绝，效期不足 cannot_compare（Focus 3、4）。`test_no_send_retry_fallback_or_state_commit` 用网络/进程/执行 sentinel 验证固定零恢复，输入 source/dictionary/snapshot 不变，proposal 不 ready。精确请求重复调用字节可复现。最终 85 focused、1,458 全套测试通过；首轮回归抓到的 import-test 隔离问题已修正并通过新一轮验证。
- [x] **Step 5 — 提交。** `git add src/agentcodec/adaptive/selector.py src/agentcodec/adaptive/__init__.py tests/test_adaptive_selector.py && git commit -m "feat: select one message encoding with fail closed preservation"`；提交仅含三个指定文件。
- [x] **Step 6 — 独立审查。** Reviewer Approved；无 Critical、Important 或 Minor。审查对未改依赖列出 Cannot verify，不视为本任务缺陷。

### Task 7：独立离线固定场景及有界故障模拟

**Files:** Create `src/agentcodec/adaptive/offline.py`、`tests/test_adaptive_offline.py`。依赖：1–6。

**Interfaces:** 本模块定义严格 `OfflineCase(source_message:MessageV1,request:SelectionRequest,metadata_authorized:bool)`：source 与 request.message 必须全等，或仅 dictionary_version 一处不同且 metadata_authorized=true；其余差异拒绝。SDK request 内仍只有一个完全确定的 transport，source 不传入 selector。定义 `ScheduledSnapshot(case_index:int,repeat_index:int,stage:setup|initial|retry|fallback,snapshot:ScopeSnapshot)`；`OfflineScenario(scenario_id:str, cases:tuple[OfflineCase,...], whole_corpus_repeats:int, fixed_decisions:tuple[CandidateId,...], resource_schedule:tuple[ScheduledSnapshot,...], paths:tuple[OfflinePath,...], remainder_policy:continue_declared|stop_group)`；fixed_decisions 按 repeat_index 外层、case_index 内层覆盖每个逻辑位置，资源日程以同索引+stage唯一寻址。`OfflinePath(path_id:str, probability_numerator:int=1, probability_denominator:int=1, outcomes:tuple[OfflineOutcome,...])`；`OfflineOutcome(case_index:int,repeat_index:int,stage:pre_send_reject|initial|retry|fallback,success:bool, response_text:str|None)`；`OfflineResult(paths:tuple[dict[str,object],...], expected_tokens_numerator:int|None,expected_tokens_denominator:int|None,reason_codes:tuple[str,...])`。每条 paths 元素固定含 path_id、账本 attempts、case_statuses、unamortized_setup_tokens、simulated_realized_regret、repeat_shortfall_loss_tokens、winning_intervals；case状态仅 simulated_success/simulated_failure/rejected/not_executed，不伪称发送。

`simulate_scenario(scenario:OfflineScenario)->OfflineResult`。概率使用 Fraction 精确校验 sum=1；省略概率只允许单条确定路径 1/1。fixed_decisions 可由 Task 8 在运行前对声明快照作一次逐条选择后冻结；故障发生后不重选。固定决策和资源/故障日程必须完整覆盖声明次数，不接受在线 callback 或动态候选。initial/retry 对 transport，fallback 对保留 source JSON；共享 setup 只在离线展示时按实际使用 M 展示 S/M，M=0 不摊销、不能除零，不反写到选择成本。

- [x] **Step 1 — 写失败断言。** `test_fixed_failure_paths_charge_all_attempts` 覆盖预发送拒绝、initial成功/失败停止、retry成功/失败、fallback成功/失败；拒绝无 W，首次失败保留 W，最多 initial+同 payload retry+source JSON fallback 三次，每次都含完整 context/封装/声明响应，响应不重计。`test_continue_does_not_replan_later_message` 验证失败后下一条只按预声明决策/资源前置条件执行或拒绝；stop 时余项 not_executed，不写成功/零成本发送。
- [x] **Step 2 — RED。** `.venv/bin/python -m pytest tests/test_adaptive_offline.py -q`；初始 49 项在缺少模拟器时按预期失败。
- [x] **Step 3 — 最小实现。** 独立离线状态机只重放有界脚本，不发消息。事件全部 simulated=true；source JSON fallback 与 transport 成本域分列。失败初始化不 ready，版本/epoch 重初始化按日程，未满足前置条件不换候选。SDK 不导入本模块，也不开放生产恢复开关。
- [x] **Step 4 — GREEN。** 同命令；`test_probability_paths_and_unknown_required_cost` 验证概率和/负值/零分母拒绝，E[C] 按精确有限路径算；必要未知无界 cannot_compare，未知标签 configured/locally_measured/unknown 不混淆。`test_dry_run_and_reuse_units` whole_corpus_repeats=0 无 setup/payload/摊销，SDK=0 仍拒绝；1/10/50 与 same_instance repeat_count 分列。`test_nonstationary_break_even_and_early_stop` 非平稳只列固定事件序列适用区间/首次严格胜出，不外推永久直线，提前停止报告未摊回 setup 与复用不足损失，和实现 bug 分开。最终 69 聚焦、Checkpoint C 314、完整 1,527 项通过。
- [x] **Step 5 — 提交。** `git add src/agentcodec/adaptive/offline.py tests/test_adaptive_offline.py && git commit -m "feat: simulate bounded fixed offline adaptive scenarios"`；fix round commit 只改这两个 Task 7 文件。
- [x] **Step 6 — 初次独立审查与 fix round 1。** 初审 2 项 Important 已各自 RED→GREEN 修复：诊断已按输入分别追踪，停止 suffix 不再抹掉已知 prefix；setup 回收以跨位置 signed delta 聚合后再限制到 [0,S]。补充相应回归测试。
- [x] **Step 7 — Scoped re-review。** 两项均 ADDRESSED，fix diff 无新增 breakage；final 69/314/1,527 证据已记录。

**Checkpoint C（6–7 后）：** [x] `.venv/bin/python -m pytest tests/test_adaptive_selector.py tests/test_adaptive_offline.py tests/test_evaluation_runner.py tests/test_evaluation_contracts.py -q`；314 项通过。审查确认本 offline 模块不引入 select_workflow 或恢复后动态调度。

### Task 8：独立 adaptive benchmark 与验收矩阵

**Files:** Create `src/agentcodec/adaptive/benchmark.py`、`tests/test_adaptive_benchmark.py`。依赖：1–7。

**Interfaces:** 本模块定义 `BenchmarkInput(corpus_id:str,source_path:str,source_sha256:str,source_count:int,dictionary_path:str|None,dictionary_sha256:str|None,dictionary_source:local_file|legacy_demo|none)`；legacy_demo 只在显式选择时引用既有内置对象，不导出词条。`AdaptiveBenchmarkConfig(scenarios:tuple[OfflineScenario,...], code_revision:str, input_manifest:tuple[BenchmarkInput,...], scenario_corpora:dict[str,str])`；scenario_corpora 将每个唯一 scenario_id 映射到一个 corpus_id。`run_adaptive_benchmark(config:AdaptiveBenchmarkConfig)->AdaptiveBenchmarkArtifacts`；`AdaptiveBenchmarkArtifacts(manifest:dict[str,object], report:dict[str,object], records:tuple[dict[str,object],...])`。版本分别 `adaptive-benchmark-manifest-v1`、`adaptive-benchmark-v1`、`adaptive-benchmark-record-v1`。manifest 受控本地保存输入 hash/count/profile/renderer/快照/allowlist/版本域/复用单位/故障及余项政策/完整假设和 excluded；report/records 默认仅计数与局部序号，不含消息/词条/payload/内容 hash。SDK 决策的内部 hash 不投影到公开摘要。

报告固定含 disclaimer、acceptance_passed、scenarios（键仅局部稳定序号）；每场景含 corpus_id（仅 diverse/repetitive 或本地序号）、comparison_domain、reuse_unit、whole_corpus_repeats、logical_case_count、full_equal_count、hard_equal_count、eligibility_violation_count、predicted_regret、simulated_realized_regret、repeat_shortfall_loss_tokens、cost_totals、status_counts。acceptance_passed 仅针对明确声明的 clean 验收场景；故障/近似/未知场景独立标负例预期，不能让其失败“通过率”混入 clean 分母。没有必要精确成本时 regret/savings 均 null，不伪装0。

- [x] **Step 1 — 写失败断言。** `test_report_separates_domains_and_never_blends_corpora` 用独立合成小样本验证 groups/域/allowlist/暴露模型/故障条件不同就不同分母，source JSON 仅作参考，不进 transport 最小值。`test_clean_acceptance_independent_regret_and_fidelity` 核对全十一字段/六关键字段全等、eligible/allowlist违规为0、零阈值 predicted_regret=0、阈值机会成本透明，unavailable/cannot_compare/policy_retained 单列，未知累计 null。`test_ablations_are_not_selector_claims` payload-only 仅消融，固定格式/自然语言旧参考不冒充 adaptive候选。
- [x] **Step 2 — RED。** `.venv/bin/python -m pytest tests/test_adaptive_benchmark.py -q`；初轮缺少 runner 时 29 项预期失败，后续 manifest/验收 gate 补丁也有对应 RED。
- [x] **Step 3 — 最小实现。** runner 预先声明每条选择及资源日程，再调用固定模拟，不根据后续失败重路由。完整矩阵分层生成，按 corpus/profile/metadata域/资源暴露/allowlist/故障政策分成各自小于8 MiB的独立配置，不要求一份配置装完整笛卡尔积，不增加现有容量上限；避免单请求多 tokenizer 聚合。复用单位、文本边界、source/transport metadata_selection、历史缺失清楚标明。原始 payload/词典不进入默认 report/records，manifest 才留受控必要身份，不能复制旧 suite 的词典倾倒。修复后 controlled manifest 保留 effective receiver capabilities、candidate-resource bindings 和词典选择状态/身份；matrix harness 强制 acceptance 与覆盖完整。
- [x] **Step 4 — GREEN 与 post-commit 本地矩阵验证。** 最终 53 focused / 1,580 full tests passed；使用提交 `4d8ffd2441bb613695d520f7afd04648c6697c7e` 为 code_revision 的真实本地矩阵：120 groups，64 个非 dry clean acceptance groups 全通过，15,840 clean positions，全字段/关键字段相等，无 clean eligibility violation；120/120 ledger conservation，重复 artifacts byte-identical，输入身份和顺序未变。两类语料/两种本地 tokenizer 与不同域仍分组，不合并分母。
- [x] **Step 5 — 提交。** `git add src/agentcodec/adaptive/benchmark.py tests/test_adaptive_benchmark.py && git commit -m "feat: report reproducible per message adaptive benchmarks"`；随后 fix-round commit 也严格限于这两个指定文件。
- [x] **Step 6 — 独立审查与 fix round 1。** 初审的两项 Important（manifest 缺 effective bindings/selection；matrix false acceptance 仍能正常退出）已 TDD 修复；scoped re-review 两项 ADDRESSED，无新 breakage。
- [x] **Step 7 — Minor 处置。** 混组测试可能先被输入 manifest 数量不匹配拒绝；作为非阻塞建议记到最终整支审查，本轮未改。

| 矩阵维度 | 必须覆盖（参数化测试；不同组不合并） |
| --- | --- |
| 数据/计量 | diverse/repetitive 各30；o200k_base/cl100k_base；unknown、坏缓存、surrogate 负例 |
| 候选/metadata | 固定五格式、adaptive 无词典/显式词典、payload-only 消融；保守 json/ac1 与显式全 allowlist；null source 与调用方授权词典 transport 分域，不强求五候选同时 eligible |
| 资源/次数 | builtin、once_per_scope 冷/热、per_request_context；SDK 1/N、future 0/1→1/2；offline 整组1/10/50及0 dry-run |
| 数学/账本 | break-even 相等/两侧/无交点/优势消失、独立全文复计0差、互斥账本和等于总量、历史不完整累计null |
| 故障/复现 | 固定三attempt路径、continue/stop、失效/重初始化、显式冲突；声明偏差/复用不足损失分列；相同revision/profile/policy/state确定 |

### Task 9：独立 CLI、安全摘要与显式本地输出

**Files:** Create `src/agentcodec/adaptive/cli.py`、`tests/test_adaptive_cli.py`；Modify `src/agentcodec/cli.py`（只追加注册/惰性调用）。依赖：1–8。

**Interfaces:** `select_encoding_command(request:Path, summary_output:Path|None=None, payload_output:Path|None=None, expected_future_reuses:int|None=None)->None`；`benchmark_adaptive_command(config:Path,output_dir:Path)->None`；`redacted_decision(decision:SelectionDecision)->dict[str,object]`。Typer 名为 `select-encoding --request PATH [--summary-output PATH] [--payload-output PATH] [--expected-future-reuses N]` 和 `benchmark-adaptive --config PATH --output-dir PATH`。两个输入都为一个严格 UTF-8 JSON 配置文件，duplicate keys 拒绝，拒绝 URL；不复用旧 CLI 的任意 stderr 异常回显。

请求文件就是 SelectionRequest 的 JSON 结构，可内嵌显式词典；不搜索用户目录。future 参数只有 request 未显式给 repeat_count/repeat_declaration 时才可用；由 CLI 将 future_reuses+1 转成总数并为同一输入实例建立明确 RepeatDeclaration（future=0 也合法）；若有任何次数字段与之并存则 usage拒绝，不静默覆盖。离线 config 是 Task 8 的完整 AdaptiveBenchmarkConfig JSON：scenarios 内嵌完整 OfflineCase，不使用隐含模板/自动补值；input_manifest 的本地路径只用来一次读字节、严格解析、核对 hash/count/源顺序及内嵌 source_message 全等，不能以这些文件偷偷改写 request.message。JSON 数组在 JSON 边界显式转换为声明 tuple，仍逐项严格验证，不允许 SDK 任意 list/str 类型强转。相对路径仅相对 config 所在目录解析，不接受远端路径；本地 revision 用固定 `git rev-parse HEAD` 核对 code_revision，不运行任意配置命令。固定格式案例及词典 transport 由调用方配置显式声明，CLI 不默默补版本。

- [x] **Step 1 — 写失败断言。** `test_cli_is_opt_in_and_summary_is_redacted` 断言两新命令存在但 encode 默认不变；默认 stdout 仅固定字段计数/原因，不含 marker 原文、词条、payload、hash、自由文本/路径。`test_payload_requires_explicit_local_file` 未给输出不输出 payload，文件内容是完整已选 payload+文档LF，不计入 profile 除非 final_lf明确。`test_future_reuses_and_no_production_retry_flags` future0/1 对应总1/2，负数/混合参数/--retry/--provider 拒绝。
- [x] **Step 2 — RED。** `.venv/bin/python -m pytest tests/test_adaptive_cli.py -q`；首轮 50 failed、10 passed 符合缺功能预期；特殊文件输入另有明确 RED→GREEN 记录。
- [x] **Step 3 — 最小实现。** 检查输入字节限额、严格 JSON、所有输出路径互异且不覆盖输入/词典/快照，先验证后写；显式输出须新文件/空目录，已有文件不覆盖。用临时文件 staging+成功后发布，失败清理仅本次文件，不能把部分输出称成功，不承诺跨文件断电事务。select 返回 selected/preserved 时 exit0，但摘要区分比较成功与保留；fallback_required 输出脱敏失败 decision 并 exit1，绝不写 payload。usage exit2；I/O 错误固定码stderr且无原值。benchmark 仅三件完整 manifest.json/records.jsonl/report.json 发布后 exit0，完成报告不是验收通过。特殊文件检查在打开前拒绝 FIFO 等路径。
- [x] **Step 4 — GREEN。** Task 9 聚焦 63 passed；`Checkpoint D` 284 passed；完整套件 1,643 passed；提交后聚焦 63 passed。缓存缺失时 cannot_compare，不下载，旧 evaluate-offline 不调用 meter。
- [x] **Step 5 — 提交与审查。** `7dd8c931ab2ef69c6591d2932c497116b2cf0eaa` 提交 `src/agentcodec/adaptive/cli.py src/agentcodec/cli.py tests/test_adaptive_cli.py`。
- [x] **Step 6 — 独立审查。** Spec/task quality APPROVED WITH MINOR FOLLOW-UP；无 Critical/Important。一个 Minor import-boundary 建议与条件性 symlink-loop RuntimeError 观察留给最终整支审查，均非本任务阻塞。

**Checkpoint D（8–9 后）：** [x] `.venv/bin/python -m pytest tests/test_adaptive_benchmark.py tests/test_adaptive_cli.py tests/test_cli.py tests/test_evaluation_cli.py -q`；284 passed，旧命令/新命令不混 wire，旧输出规则不受新文件策略影响。两次新报告复现只比较同 revision；不因文档提交改变 revision 而误报。

### Task 10：兼容守卫、可运行说明与最终全分支审查

**Files:** Create `tests/test_adaptive_compatibility.py`；Modify `README.md`、`SECURITY.md`。依赖：1–9。本计划/设计/checklist 在本任务另作明确文档提交，不自动混入功能提交。

**Interfaces:** 不新增运行 API。兼容守卫仅消费旧 SDK/CLI/报告和新入口，证明行为不变，不重写旧期望值迎合实现。README 新增最小严格 request/config 的合成示例及本地命令；明确用法所有必填 profile/资源/次数字段，与本计划类型一致，无占位键漏字段。

- [x] **Step 1 — 写失败守卫/文档验收。** `test_legacy_defaults_wire_and_report_versions_unchanged` 锁 AC1 default/golden、旧五条件/七条件/suite-v1、offline-eval-v1；`test_evaluate_offline_does_not_initialize_tokenizer` 阻断 meter/tiktoken 调用；`test_new_path_never_executes_or_reads_credentials` 阻断 socket/HTTP/消息执行/凭据访问，并确认没有依赖安装或发出 API 请求。文档验收 `test_adaptive_docs_explain_boundaries` 核对两新命令、per-message、record-only/zero retry、cannot_compare、账单/理解限制、旧 AC1负结果及无全局最优声明；测试新建时缺文档应先失败。
- [x] **Step 2 — RED。** `.venv/bin/python -m pytest tests/test_adaptive_compatibility.py -q`；旧兼容断言可能已通过，必须至少文档缺项断言失败；若回归先已存在则按基线证据单列，不改旧报告掩盖。禁止为制造 RED 修改旧生产代码。
- [x] **Step 3 — 最小文档完善。** README 保留原数字和负结果，新增新字段/exit语义、严格完整配置与一次本地示例；SECURITY 区分新入口有容量/快照检查、旧入口仍原有限制，不泛化成 OS沙箱、认证/加密或完整TOCTOU防护。说明 hash不是匿名化、ready不是免费context、没有模型理解/账单证明、任何API阶段需新许可。
- [x] **Step 4 — GREEN 与最终验证。** 先同命令，再 `.venv/bin/python -m pytest -q`；预期全通过、无skip伪充真实矩阵。按 README 的合成配置运行 `.venv/bin/python -m agentcodec.cli select-encoding --request "$REQUEST" --summary-output "$SUMMARY" --payload-output "$PAYLOAD"`；运行两次 `.venv/bin/python -m agentcodec.cli benchmark-adaptive --config "$CONFIG" --output-dir "$OUT"`，REQUEST/CONFIG 为本任务写在专属受控台账的完整本地合成配置，OUT 每次为不同新空目录。逐文件 cmp 两份同revision产物；解析验收字段，检查两组各30、旧输入hash不变、N与整组单位不混淆、零阈值regret0、未知null、无原文输出。真实矩阵和旧回归仅在已授权本地依赖/缓存齐备时运行，不联网补装。另执行 `git diff --check`，核对只有计划文件地图范围改变。
- [ ] **Step 5 — 本地提交、独立全分支审查与停止。** 首先 `git add tests/test_adaptive_compatibility.py README.md SECURITY.md && git commit -m "docs: document adaptive boundaries and lock legacy compatibility"`；再仅显式添加本设计、本计划、`tasks/todo.md`，提交 `docs: record approved adaptive routing plan and checklist`（仅在用户批准计划后）。独立审查完整 `git diff dd117a963486767f7108cb16c6884ac2f92ff09b..HEAD -- src tests README.md SECURITY.md docs/superpowers tasks/todo.md`，不只看最后commit、不读取bench原始数据。复核任务审查证据、五项Review Focus、下表全覆盖。修复仅在本分支，重新跑对应及全套测试，再复审；没有 Critical/Important 未决项才可称实施完成。`git show-ref --verify refs/heads/agentcodec-mvp` 必须仍为基线，`git branch --show-current` 仍 agentcodec-auto-select。保留分支，明确未合并/未推送，报告真实证据而非预期结果。

**Task 10 实现证据：** 兼容守卫最终 5 passed，完整套件 1,648 passed；README 完整合成 CLI 两次报告逐文件相同；当前运行代码 revision `7dd8c931ab2ef69c6591d2932c497116b2cf0eaa` 的真实本地矩阵 120 组、64 个非 dry clean 组全部通过，15,840 个 clean 位置全字段/关键字段一致、regret 0；输入身份不变。测试与 README/SECURITY 已单独提交 `aeae0160a5bf071d639cf6ef76bd323af1ffb2d7`，本设计/计划/checklist 另作指定本地文档提交。Step 5 包含最终独立审查，因此保持未勾选，由协调者后续执行；Task 9 两项待裁定观察仍开放。完整命令、诊断偏差及验收证据见本计划专属 `task-10-report.md`，不把实现自审当独立批准。

## 计划自审：设计全条款落点

本节只检查计划覆盖，不替代测试执行或功能验收；实施复选框仅按已有证据更新，最终独立审查未完成项保持未勾选。

| 设计条款 | 实施与测试落点 |
| --- | --- |
| §1–4 六项范围、只单实例、旧负结果/非目标 | Global Constraints；Task1/6范围拒绝；Task8分域报告；Task10保留旧结论 |
| §3 十一字段、关键值、只读权限、人工词典、严格fail closed | Task1原值/深快照；Task2五候选round-trip及损坏矩阵；Task6零副作用；Task10sentinel |
| §5.1 固定五候选、JSON版本renderer、接收能力 | Task1冻结ID/profile；Task2明确格式渲染/解析与gating |
| §5.2 dictionary_version不变、source/transport域、旧suite例外 | Task2 null/非空域；Task8分母分域；Task10旧接口不改 |
| §5.3 配置错误优先、不适用/缺计量/恢复分离 | Task2显式冲突；Task6三状态优先；Task9失败输出 |
| §6.1 remaining/spent/lifecycle、历史去重/不完整 | Task3历史身份/事件；Task5公式与null累计；Task8摘要边界 |
| §6.2 资源完整身份、builtin/ready/cold/invalid、三种暴露 | Task1/3；Task4/5共享资源/每次context/首次不双收setup |
| §6.3 完整文本、二维互斥账本、LF、独立响应 | Task4全文前缀增量；Task5完整成本；Task7三attempt失败保留与响应不重计 |
| §6.4 生产零恢复；离线固定策略/故障/概率/余项 | Task6纯建议；Task7固定有界路径、概率和、继续/停止/失效日程 |
| §6.5 单实例次数、future0/1、offline0/1/10/50、严格break-even | Task1次数声明；Task5整数区间；Task7非平稳/未摊回；Task9转换及互斥参数 |
| §7.1 本地两词表/可靠profile/坏缓存/近似/合法原格式保留 | Task4本地cache守卫；Task6cannot_compare/surrogate与null；Task8负例矩阵 |
| §7.2–7.3 同实例最优、范围拒绝、0阈值ties/显式政策 | Task6独立枚举、tie次序、严格大于阈值及机会成本 |
| §8 全输入/decision/摘要/显式payload/预计状态/epoch | Task1共享类型；Task3确认；Task6单decision；Task9安全投影及I/O |
| §9 完整行为、无发送/维护/自动恢复 | Task2→3→4→5→6；Task7仅模拟；Task10无副作用守卫 |
| §10 可逆/关键字段/合法性/regret/计量/账本/复现/兼容 | Task2、4–8矩阵及独立期望；Task9隐私；Task10全套与全分支审查 |
| §11 安全/限额/明文/隐私/不得夸大 | 容量工程值；Task1/7/9超限拒绝；Task4离线；Task10安全说明 |
| §12 六项已批准取舍 | Global Constraints完整落实；Task6零阈值与可选政策；本计划审批门已满足 |

**自审结论：** 已按规格覆盖、步骤可执行性、跨任务类型/命名一致性、五项 Review Focus 归属、篇幅比例逐项检查；无已知实质性设计覆盖缺口。技术命令/类型/数字仍为已批准实施要求，执行结果另见各任务报告和对应复选框。无需账号、密钥、网络或新增依赖；后续本地环境若缺已有依赖/词表，只能报告阻塞。

**审批与停止边界：** 用户已批准实施计划，限定离线实现、验证和本地提交按计划执行；不调用模型/API、不安装、不合并、不推送。Task 10 后停止，由协调者另行组织最终全分支独立审查；原 `agentcodec-mvp` 不变。
