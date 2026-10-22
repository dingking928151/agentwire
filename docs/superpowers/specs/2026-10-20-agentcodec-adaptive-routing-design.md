# AgentCodec 自适应编码与词典摊销设计提案

**状态：完整设计及六项范围取舍已获用户批准，配套实施计划也已获批准。** 本文保留已批准的技术范围，不单独充当实现或验收证据；实际结果见配套计划、checklist 与本地任务台账。下文“本轮”限制及未重跑说明记录原设计审批阶段，不撤销后续明确批准的离线实施与限定本地提交；沿用已选择的 subagent-driven 流程。

工作区：`/workspace/agentcodec/.worktrees/agentcodec-mvp`；当前本地分支：`agentcodec-auto-select`；基线 HEAD：`dd117a963486767f7108cb16c6884ac2f92ff09b`。原 `agentcodec-mvp` ref 保持同一基线，不修改。沿用现有 worktree，不嵌套新建；已批准计划允许限定本地提交，仍不合并、不推送。

## 1. 首版建议：只选一个消息的编码

建议新增独立、显式开启的本地选择入口：先确认候选已获准、接收方支持且严格可逆，再用指定目标 tokenizer 比较完整成本。不把 AC2 或词典直接变成默认。

**设计者建议 v1 只做 per-message selection。** 默认计算当前消息使用一次的成本；调用方可明确声明“这条完全相同的消息/固定模板实例总共使用 N 次”，选择器据此计算每个固定候选的重复成本，仍只返回一个 payload。固定模板必须连变量值和完整序列化实例都相同；仅模板相同但变量值不同，就是不同消息。

不实现多个不同消息之间的全局资源优化、最短路径/动态规划，也不因某条失败动态调整其余消息的选择策略。离线 benchmark 可以汇总固定语料场景，但不能把汇总称为在线全局路由策略，不能保证整组全局最优。这些扩展只能另行设计。

原设计审批阶段只修订这份设计文档：不读取原始 JSONL/词典文件、不展示原始语料，不运行测试或 benchmark，不安装、不联网、不取凭据、不调用模型/API、不外发语料，不改生产代码或其他文档，不写计划、不提交。

## 2. 为什么需要选择，而不是统一换格式

以下是原稿记录的仓库依据，本轮没有重跑实验：

- [基础设计](2026-10-19-agentcodec-design.md)要求严格可逆、手工用户词典和 fail closed。
- [AC2 实验设计](2026-10-19-agentcodec-ac2-experiment-design.md)保持 AC1 默认不变；AC2 是固定十一字段的 JSON 数组实验格式。
- [离线评测设计](2026-10-20-agentcodec-offline-evaluation-design.md)区分 source/transport oracle；mock 只验证严格解析和只读回执。
- [README](../../../README.md)已有两组各 30 条合成消息、两种 tokenizer 的结果。下面仅保留原稿摘录的 reuse=1、含 setup 和观察到零 fallback 的 session total，顺序为 `o200k_base / cl100k_base`：

| 固定格式 | diverse：30 条 | repetitive：30 条 |
| --- | ---: | ---: |
| 紧凑 JSON | 2,576 / 2,559 | 7,760 / 7,785 |
| AC1 | 4,691 / 4,494 | 20,356 / 20,129 |
| AC1 + 本组词典 | 5,316 / 5,089 | 7,779 / 7,573 |
| AC2 | 1,999 / 1,983 | 7,179 / 7,207 |
| AC2 + 本组词典 | 2,125 / 2,103 | 2,957 / 2,946 |

短词典可能增加成本；长重复词条可能显著降低成本。AC1+词典在 repetitive 的 o200k_base 首次会话仍比 JSON 贵。不能抹掉负结果，也不能合并两组平均制造优势。这里 reuse 指重复整组消息，不是每条消息独享一次免费 setup，更不是 SDK 的单实例 repeat_count。

现有约束：`codec.py` 的 `encode_message/decode_message` 专用于 AC1，`decode_payload` 只接受精确 AC1/AC2 前缀；`ac2.py` 共用 schema、枚举及整串别名规则；`evaluation/formats.py` 按声明格式严格解析。`benchmark`/`benchmark-suite` 是计数接口，不是业务执行器；`evaluate-offline` 默认 AC1、无词典配对、record-only、零 retry，不初始化 tokenizer。新入口不得改变这些默认和语义。

## 3. 目标、边界与不可改变的条件

目标是在同一消息、同一元数据域、同一资源快照和显式重复假设下，从可接收、严格可逆、已启用的候选中选取最低预计完整文本成本，并解释 setup、上下文暴露、payload 和 break-even。先判断能否比较，再比较成本，不以便宜为由放宽安全条件。

首版只证明**本地 ordinary-text tokenizer 计量**，不是模型理解、业务成功、provider 性能、延迟或实际账单的证明。两种 tokenizer 不代表所有模型；任何真实 API 阶段都需要新许可。无真实 provider、网络发送、服务端、认证、计费、跨用户词典服务或业务执行器。

必须保持：

- 十一字段类型和值严格相等；null、false、空串、空列表、列表顺序/重复、空白、Unicode 均原样保留，不 trim、不 normalization。
- 否定、金额、预算、日期、权限/授权、审批和证据不可语义压缩。不做摘要、同义改写；允许精确整串替换，但须用同用户同版本词典确定性完全展开。
- 词典仅人工维护、私有隔离、显式启用；限定 `task/blocker/next_step/evidence/constraints` 的完整字符串或完整列表项。不匹配子串、不模糊匹配、不学习、不自动维护、不跨用户搜词。
- 未知格式/协议版本/枚举/别名、缺失或不匹配词典、重复键、畸形输入均 fail closed，不猜测、不输出部分恢复消息。
- `approval_required`、消息状态、协议和词典文本都不是执行授权。所有内容只是只读数据，不执行任务、证据链接或内嵌指令。
- 自适应和词典都 opt-in；旧 SDK、CLI、AC1 默认/fallback、golden wire、legacy API、旧报告字段保持不变。

## 4. 路线取舍

| 路线 | 主要问题 | 设计者建议 |
| --- | --- | --- |
| 把 AC2/词典直接改默认 | 兼容破坏；冷启动未必最省 | 不采用 |
| 每条只比 payload 长度 | 漏掉初始化和上下文 | 仅离线消融对照 |
| 将 setup 固定平均给未知未来消息 | 虚构复用、重复计费 | 不采用 |
| 单消息 + 资源快照 + 明确同实例重复 N 次 | 可解释，覆盖冷/热和摊销 | v1 推荐 |
| 多个不同消息的全局优化 | 扩大状态、恢复和策略范围 | 非目标，未来另议 |

不建设开放插件注册表，不预测任意未来业务。所有“最优”都只指声明边界内的单决策，不是未知未来或整组语料的保证。

## 5. 候选及严格等价域

### 5.1 可用候选

建议固定五个 ID：`json`、`ac1`、`ac2`、`ac1_dictionary`、`ac2_dictionary`。新入口默认 allowlist 为 `json,ac1`；AC2、词典候选都要调用方另外显式启用。自然语言和 DRIXL 只保留旧基准参考，不进入 v1 adaptive；不声称它们已有任意消息的严格端到端 decoder。

JSON 使用固定版本的紧凑 renderer，保留全部十一字段和 metadata。AC1/AC2 使用现有 codec，有词典候选使用调用方选定词典，不新增 wire/短码语义。每个候选必须本地严格解码、逐字段一致才 eligible；本地 round-trip 不能证明远端支持。

### 5.2 路由器绝不改变 dictionary_version

**设计者建议：每个比较域只有一个调用方明确提供的 canonical MessageV1。** 词典与版本由调用方提供和授权，路由器不清空、补填或替换 `dictionary_version`。

- 输入版本 null：JSON、无词典 AC1/AC2 可比；词典候选不可用，即使本地有词典也不填版本。
- 输入版本非空且有显式同用户同版本词典、已启用词典候选：JSON 保持该版本；现有无词典 AC1/AC2 编码器不能处理此输入，故不参与；对应有词典候选可比。
- 如需比较“显式选用词典版本是否值得”，由调用方在路由前授权并构造 transport 副本，保留 source 和唯一 metadata 差异。两个域分别报告，不称十一字段完全相同。
- 旧 suite 可继续在词典条件给 null 填版本，不改变旧契约。新 adaptive 同域比较时 JSON 也必须使用相同 transport metadata，source JSON 另列参考，不能混作同域最优。

不扩展旧 codec 为“保留非空版本但不做词典替换”，也不新增 JSON alias 格式。

### 5.3 不适用与错误的优先级

普通词条未命中只保留原字符串，低覆盖率不是错误。未选词典/版本 null 造成候选不适用，标 unavailable，不记为已发送 fallback。

显式词典 owner/version/hash 冲突、未知版本/alias、schema 错误是配置或完整性错误，整次 fail closed。优先级为：

1. 输入/显式配置/完整性错误：`status=fallback_required`、`payload=null`，不能走保持格式分支；即使原格式是 JSON 也不能忽略错误词典。
2. 合法配置下的候选不适用：记录原因，不发送。
3. 合法输入、合法配置但计量不足：`comparison_status=cannot_compare`，才可保持合法原格式。

错误后调用方可另行授权用保留的 JSON 恢复，但 selector 不执行、不重试、不自动回退。`fallback_required` 只是“需要调用方处理”的状态，不是恢复动作。

## 6. 完整成本及摊销

### 6.1 计量边界与历史

边界为一个 `scope_id`、一个 tokenizer、接收能力/资源快照及有效期内的一条消息决策。默认使用一次；显式 N 次时比较固定选用每个候选 N 次的成本，返回一个 payload，不返回 N 步策略。scope 可以包含历史资源账本，但不使 selector 成为跨消息优化器。

这是文本 token 模型，不是金额模型。本地 encode/decode/计数 CPU 不计传输 token，本地候选生成不是发送。多个 tokenizer/端点不能直接相加称统一最优；拆 scope 或另行批准权重模型。默认无模型响应、无网络 ack，相关成本为 0 并说明原因；只计输入时输出方向须列 excluded，不能称全 API 生命周期。

建议明确三个起算值：

- `remaining_expected_tokens`：从本次快照起算，是选择目标。
- `scope_spent_tokens`：本 scope 起点至快照前已发生的互斥文本事件成本。
- `scope_lifecycle_expected_tokens = scope_spent_tokens + remaining_expected_tokens`。

历史事件带 scope、资源身份、计量 profile、唯一事件 ID 和成本，按 ID 去重。其他 scope 已付成本不转嫁；导入 ready 资源须说明来源和无需重付 setup 的依据。共同历史沉没成本不改变当前边际排名。

历史缺失/不完整但 ready 与剩余成本仍可靠时，可比较 remaining；`scope_spent_tokens` 和 `scope_lifecycle_expected_tokens` 为 null、标 `history_incomplete`，不能把缺历史当 0 或报告累计节省。缺失影响 ready 真实性/是否需重付则 cannot_compare。已提交历史账本若结构或计量身份冲突，属于显式配置错误，按 §5.3 fail closed。

### 6.2 资源与暴露模型

资源以 `(scope, receiver, user, resource_kind, version, content_hash, tokenizer_profile)` 标识，状态为 `builtin`、`ready`、`cold`、`invalid`。builtin 指接收端确定性 decoder 真正内置协议/schema，本地安装 SDK 不证明远端内置；ready 须有快照确认、有效 epoch 和计量暴露依据；cold 须计完整初始化/同步；invalid 不参与，显式冲突按 §5.3 拒绝。

建议区分三种暴露模型：

| 模型 | 文本成本 |
| --- | --- |
| `builtin` | 内置资源不需要文本 setup；默认本地 deterministic decoder 场景 |
| `once_per_scope` | 明确一次性本地计量假设；复现旧 suite 时显式选冷/热 |
| `per_request_context` | 每次请求计完整有效上下文；ready 不代表免费 |

上下文缓存折扣不使 token 为 0，账单折扣不在本模型内。per-request-context 首次正文只计该次 context，不另重收一次 setup；若有独立初始化/同步请求及确认，再另计该独立事件。加载本地文件/快照没有文本 token 事件。冷/热不决定收费次数，暴露模型决定。

### 6.3 成本公式及不重复计费

`T_e(text)` 是指定本地 tokenizer 对完整 ordinary text 的计数，只对该文本与词表精确，不代表服务端完整 token 数。

对声明场景中的路径 π、初始状态 s：

`C_e(π|s) = Σsetup/同步 S + Σ首次payload W + Σ额外同格式retry R + Σ另次JSON fallback F + Σ未被前项包含的其他文本 A`。

生产/SDK v1 的 R、F 固定为 0；通用账本保留这些项仅供独立离线故障模拟，不表示生产可开启自动恢复。

W 包括完整消息、AC 前缀、格式声明、封装和进入目标文本的 metadata；out-of-band 不进入 tokenizer 时列 0 及依据。S 包括协议、schema、完整词典（不只命中词条）、版本/hash/同步声明和需计量确认。共享协议/schema 取资源并集，不对 AC1/AC2 重收同一份资源。

R 是额外同格式同 payload 请求，F 是另一次原 JSON 请求，各含本次上下文、封装及已声明可计量响应。首次模拟发送失败不能抹掉 W；编码前拒绝无发送事件，不计失败候选 W。响应/ack 若已在 attempt 成本中，不能再加 A。

账本用 attempt 类型 × 文本组件二维记录，只按一维汇总一次。实际拼接为一条文本时先 tokenize 完整文本，分解用固定顺序的 token 增量（可为负），不能逐字段独立 tokenize 后相加。独立请求才分别计数。renderer/profile 固定最终 LF 边界；旧 benchmark renderer 不含 CLI 输出 LF，新兼容口径沿用。

### 6.4 失败边界与其余消息如何继续

**设计者建议生产 SDK/CLI 固定 record-only、零 retry、无自动 fallback。** 一次只为一个 payload 决策，不发送，也不在失败后恢复。选择成本采用明确 `deterministic_clean` 本地假设，不能解释为真实成功率，不用故障概率改变 v1 在线选择目标。

若离线 benchmark 单独模拟恢复，须在运行前固定：消息顺序、每条初始候选决策、故障日程/显式路径概率、恢复顺序、资源有效/重初始化日程、失败后继续或整组停止的规则。最多 initial + 同格式 retry + JSON fallback 三次，禁止递归路由。

建议模拟故障记账后继续预声明的下一条；若其固定资源前置条件不满足，记拒绝并继续，不能临时换候选或重规划后续消息。若场景声明整组停止，剩余项标未执行，而非成功或零成本发送。全部失败、setup、context、重试、回退及响应成本逐项记录；模拟事件不得写成真实已发送。

仅对这类固定离线场景，可定义有限路径集合 Ω：`E[C_e]=Σ p(ω)·C_e(ω|s)`，`Σp(ω)=1`。区分预发送拒绝、首次成功/失败停止、retry 成功/失败、fallback 成功/失败。概率来自显式场景，不把 clean 合成集零故障冒充真实概率。所有未知量标 `configured/locally_measured/unknown`；影响比较的必要未知量无界时 cannot_compare。敏感性区间不等于精确最优。

### 6.5 重复次数与 break-even

SDK `repeat_count` 是包含当前实例的总次数，默认 1；若界面采用 `expected_future_reuses`，总次数为 `1+future_reuses`，未来 0/1 分别对应总次数 1/2。这只是声明假设，不触发执行。非整数、负数、不同实例冒充重复、混淆单条与整组均拒绝。

`repeat_count=0` 仅用于离线 benchmark 的显式 dry-run：无发送、setup 或摊销。SDK 选择拒绝 0，不除零。离线整组 1/10/50 次使用独立场景参数，与 SDK 单实例次数分列。

对同实例候选 a、b，setup 一次、资源持续有效、增量平稳时：

`C_a(n)=S_a+n·V_a`，`C_b(n)=S_b+n·V_b`。

S 是尚未支付 setup；生产 V 无 retry/fallback，离线故障场景则包含显式恢复成本。令 `ΔS=S_a−S_b`、`ΔV=V_b−V_a`，a 严格便宜当且仅当 `n·ΔV>ΔS`。

`n_BE=min{n∈正整数:C_a(n)<C_b(n)}`，空集为 infinity。ΔV>0 时为 `max(1,floor(ΔS/ΔV)+1)`，相等不算严格胜出；ΔV=0 时仅 ΔS<0 在 n=1 胜出；ΔV<0 报满足不等式的整数区间，不能说复用越多越好。

每次重付上下文、版本更新、失效或非平稳故障时不能套直线永久阈值；仅离线按固定事件序列报告适用区间/首次严格胜出点。SDK 的明确重复假设须有覆盖 N 次的资源有效性和成本依据，否则 cannot_compare。

共享 setup 在离线报表可按实际选用数 M 展示 S/M，仅是展示；未使用不摊销，不能每条重复计 setup。ready 的历史成本不再进入当前边际成本，但完整生命周期保留历史。提前停止的离线场景展示未摊回成本。

## 7. 选择规则和计量不足时的处理

### 7.1 精确性与无法比较

tokenizer profile 必须指定编码名、库/词表身份及本地校验、完整序列化规则；模型到词表映射须有调用方依据，不从模型名猜测。v1 只支持已本地 provision 的 `o200k_base`、`cl100k_base`，沿用缓存完整性检查，禁止下载。

- `exact_local_text`：文本边界和目标编码完整，只对本地文本精确，生命周期受声明假设约束。
- `surrogate_only`：只是近似词表映射，标明近似，不自动切换，不作为精确最优证据。
- `unavailable/unknown`：不造 token 数，不用字符/字节启发式替代。

不支持、缺失/损坏 tokenizer、profile 不可靠或必要成本未知时，`comparison_status=cannot_compare`，不可确定的比较成本/节省为 null，给稳定原因码。只能在 §5.3 的合法配置前提下保持调用方合法原格式（缺省仍 AC1），本地严格验证，`status=preserved_without_comparison`。若不能保持，`status=fallback_required`、payload=null；不自动改 JSON、AC1 或词典。保留格式成功不等于成功比较。

### 7.2 单决策算法与明确的范围拒绝

先校验输入、用户、词典、能力和资源快照；枚举 allowlist 内 eligible 候选，渲染、严格 round-trip。默认计算一次成本；只有明确声明同实例 N 次时计算每个固定候选的 N 次完整成本，不推测未来分布。

对 eligible 且 `exact_local_text` 的候选取最低成本。无显式重复声明则按一次；显式提交不同消息序列或全局优化请求属于超出 v1 范围，拒绝而非静默忽略。更改次数、字段值、版本、资源或 tokenizer 后重新计算。离线 runner 可按固定顺序汇总已声明单条决策及资源事件，不返回在线全局策略。

### 7.3 默认零阈值，确定性 ties

设计者建议默认 `min_savings_tokens=0`、`min_savings_ratio=0`、无滞回。任意严格较低成本都胜出，不设隐藏百分比门槛。精确同成本时依次优先：调用方原格式、不引入新资源、固定顺序 `json/ac1/ac2/ac1_dictionary/ac2_dictionary`。

若保留可选抗抖动政策，必须调用方显式启用。incumbent 是明确指定且仍 eligible 的原格式，不自动学习其他消息的策略。仅当同一单实例重复边界内节省 D **严格大于** `max(min_savings_tokens,min_savings_ratio·C_incumbent)` 才切换，等阈值保持；incumbent 不合法则选 eligible 最低成本，安全优先。

可选阈值开启时报告 `policy_retained`、真正最低成本候选、未实现节省及上界，不能声称实际选择数学最小。不根据样本/负结果自动改阈值。

## 8. 建议接口与状态契约

以下为已批准逻辑接口，不单独代表实现验收结果。建议独立 opt-in SDK `select_encoding`，独立 CLI `select-encoding` 和离线 `benchmark-adaptive`；v1 **不提供 `select_workflow`**，不向旧 encode 注入默认自适应分支。CLI 返回结构化 decision，不无声明地把 JSON 混入旧 AC wire。

| 输入 | 含义 |
| --- | --- |
| `SelectionRequest` | 契约版本、单个严格 MessageV1、expected_user_id、原格式、allowlist、tokenizer profile、scope/资源快照 |
| `DictionarySelection` | 显式词典或 null；user/version/hash/本地只读快照及启用标志；无远程 URL、不搜索其他用户 |
| `ReceiverCapabilities` | 格式/协议、资源确认和有效 epoch、暴露模型；本地声明不是远端认证 |
| `CostPolicy` | 单实例 repeat_count 及声明、setup/同步文本和封装、clean 假设、显式可选阈值；生产零恢复为固定约束 |
| `OfflineScenario`（仅 benchmark） | 固定语料顺序、整组次数、预声明候选决策和资源日程、故障及余项处理规则；非 SDK 在线请求 |

资源快照有 `history_complete`、可选 `historical_events`，缺历史按 §6.1 为 null，不伪造 0。节省字段注明 remaining/scope_lifecycle 边界。

建议独立 `adaptive-selection-v1` decision 包含：

- `status=selected|preserved_without_comparison|fallback_required`，独立 `comparison_status=exact_local_text|surrogate_only|cannot_compare` 及原因码；不能输出 payload 的范围/配置拒绝归 fallback_required 并给明确原因。
- 选中候选、声明格式、payload（失败 null）、source/transport 身份及 metadata_selection（包括未改动）。
- 全候选 eligibility/拒绝原因、完整 payload/setup/context/retry/fallback/其他成本、excluded 项、计量标签、声明假设和边界。
- 真正最低成本候选、实际策略选择、基线/节省、机会成本、break-even 与适用区间。
- 资源前后**预计**状态、初始化需求、snapshot epoch、decision ID、tokenizer/policy/renderer 版本。

默认摘要不回显原文、词条、完整 payload 或自由文本摘要。使用本地 case ID/稳定序号；必要哈希只存受控本地 manifest，哈希不是匿名化。SDK 可返回 payload；CLI payload 只能显式选择本地输出，与脱敏摘要分离，不沿用旧 suite 默认倾倒完整词典。

### 资源状态不是执行事实

选择函数是纯建议，不将资源真正标 ready。只有调用方确认本地初始化后才提交事件；失败/未执行不就绪。`cold → proposed → ready` 中 proposed 只在 decision 内。无数据库、无跨进程全局缓存，scope 由调用方显式传入/保存到受控本地文件。

提交须校验 epoch，旧快照重算或拒绝，不新增分布式锁。版本/hash/用户/接收者/tokenizer/有效期变化重新确认；同版本异 hash 是冲突。计数前固定不可变快照，避免文件替换，不覆盖旧资源、不跨用户复用。

### 默认建议汇总

| 项 | 设计者建议 |
| --- | --- |
| 自适应 | 关闭，仅独立入口 opt-in |
| 新入口 allowlist | json/ac1；AC2 显式另开 |
| 词典 | 关闭；显式词典、私有 namespace、匹配版本及授权 |
| 原格式/旧 fallback baseline | 调用方指定，缺省 AC1；不自动发送回退 |
| tokenizer | 必须显式；不猜模型映射、不下载 |
| 次数 | 单实例总共 1 次；N 次须明确声明 |
| 本地资源 | deterministic decoder、builtin |
| 基准资源 | 显式冷/热、once_per_scope 或 per_request_context，分场景 |
| 生产恢复 | 固定 record-only、零 retry、无自动 fallback |
| 阈值 | 0/0、滞回关闭；显式可选政策单列 |
| 输出 | 脱敏计数/原因/假设，不展示原始语料 |

## 9. 从输入到决策的行为

调用方保留 canonical source JSON，明确候选、用户、tokenizer、能力、资源和次数；词典版本先由调用方选择。selector 校验并固定快照，显式错误立即 fail closed；合法候选严格解码全字段比对后才计量。

计量不足则按 §7.1 合法保持原格式，不报告假节省；否则按同消息重复边界计算完整成本，应用零阈值和 ties（或显式可选政策），返回一个 decision/payload 及预计初始化事件。没有发送、模型调用、业务执行或词典维护。

后续本地初始化由调用方确认，失败仅记录，不自动恢复或改后续消息。离线故障模拟另按固定政策执行并标模拟账本。真实外部使用的授权、认证和重发不在此功能内；真实 API 阶段需新许可。

## 10. 未来评测应怎样证明与怎样避免夸大

本节是设计级验收条件，不是实施任务清单；**原设计审批阶段没有测试或新评测；后续证据按已批准计划单独记录**。已获批本地 runner 可读取固定 diverse/repetitive 各 30 条，源顺序、fixture、词典不改，不合并总体、不自动选词条，报告和子代理审阅不输出原始语料/词条，不发外部服务。

独立 `adaptive-benchmark-v1` 报告保存 revision、受控源哈希/数量、profile/renderer 身份、快照、allowlist、source/transport 域、复用单位、故障日程、恢复/余项政策、成本假设、excluded 项。旧数字只在相同 renderer/setup/边界下核对，不把新口径写回旧字段。

应分开覆盖：两个 corpus；两种 tokenizer 与 unknown/坏缓存/surrogate 负例；固定五格式及 adaptive 无词典/显式词典/payload-only 消融；保守与显式全 allowlist；source/transport；builtin/once_per_scope 冷热/per-request-context；SDK 单实例 1/N/未来 0、1 次；离线整组 1/10/50、0 dry-run；break-even 相等和两侧；固定故障恢复及冲突拒绝。不同域、allowlist、资源、故障条件不能共用节省分母。

| 指标 | 验收建议 |
| --- | --- |
| 严格可逆 | clean eligible/选中候选十一字段全等 100%，含列表、空值、Unicode、metadata |
| 关键字段 | task/blocker/next_step/evidence/constraints/approval_required 零改变/未恢复；金额、日期、否定靠精确全等保护，不称语义推理 |
| Fail closed | 错格式/版本/alias/owner/hash/畸形输入无部分恢复、无静默修补 |
| 单决策最优性 | 同状态/同实例次数下独立枚举候选，零阈值 `predicted_regret=C_selected−C_min=0`；阈值机会成本有界且透明；不验收不同消息全局最优 |
| 选择合法性 | allowlist、接收能力、round-trip、资源条件违规为 0；单列 unavailable/cannot_compare/policy_retained |
| 声明偏差 | 离线固定条件的 `simulated_realized_regret` 与复用不足损失单列；不是 provider 实测，也不与实现 bug 混淆 |
| 计量 | 完整相同文本/词表独立复计数差为 0；未知为 null、近似明标；分项还原完整计数 |
| 账本 | 互斥事件和等于总量、共享资源只计应计次数、上下文每次计、失败/恢复不漏不重；历史缺失累计 null |
| Break-even | 严格不等式、相等/两侧整数/无交点/优势消失；非平稳不外推直线 |
| 可复现 | 相同输入/revision/profile/policy/state/固定场景输出确定；无时间/机器路径随机污染 |
| 兼容无副作用 | AC1 golden/default/SDK、旧五条件 benchmark、七条件 suite-v1、offline-eval-v1 不变；无网络、凭据、下载、执行消息或语料泄露 |

不预设“必须节省 X%”。零阈值数学契约只适用于同域同状态同单实例重复边界的合法候选，不保证整组汇总优于所有固定格式策略，不保证超越不同 metadata 的旧表，更不保证实际业务收益。

mock 只验证严格解析/只读回执，不执行任务。tokenizer-only、100% round-trip、关键字段全等和 mock 成功都不能证明模型理解、真实业务成功、安全语义、provider 性能或账单节省；报告首页和对比摘要必须保留限制。

## 11. 风险防线

禁止为了省 token 改否定、预算、日期、授权或证据；严格全字段恢复失败即拒绝。资源须版本/hash/epoch 确认，不把 ready 当免费缓存；词典须 owner/version/hash/快照约束，namespace 不是认证，也不防恶意文件拥有者。

协议/词典均是不可信只读数据，不声称已解决真实模型提示注入。默认一次减少虚构未来复用；声明 N 次提供 break-even/敏感性，离线提前停止记录未摊回 setup。词表或边界不可靠则 cannot_compare，不硬选。

生产固定零恢复；离线最多三 attempt，不递归路由，不因失败改变余项候选。快照和 epoch 防状态误用，不自动全局共享。输入字节、声明次数、离线事件应有限额，超限拒绝不截断，具体容量留待获批后决定。

本地明文文件由调用方保管；本设计不是沙箱、身份认证、加密存储或费用保障。不回显原始语料，也不把内容哈希当匿名化。

## 12. 已获 owner 批准的六项范围取舍

以下六项设计者建议已随完整设计获用户批准；此处仅修正批准状态，不改变技术取舍：

1. **v1 只做单消息选择。** 默认一次；明确同消息/固定模板完全相同实例总共 N 次才计重复，多个不同消息全局优化留待未来；离线固定语料汇总不称在线全局策略。
2. **路由器绝不改 dictionary_version。** 调用方显式提供并授权词典/版本，source 与显式 transport 分域；不能总让五候选同时可用。
3. **生产 record-only、零 retry、无自动 fallback。** 决策只针对一个 payload；离线恢复另用预声明固定政策、完整记账、明确失败后继续/停止，不能动态改后续选择。
4. **首版只证明本地 ordinary-text 成本。** 不证明模型理解、业务成功、provider 性能或账单；任何真实 API 阶段需新许可。
5. **新入口 opt-in。** 默认 json/ac1；AC2、词典分别显式启用；旧 AC1 fallback/legacy 默认保持不变。
6. **默认严格最低精确本地成本、零阈值和确定性 ties。** 滞回/阈值只能显式选用，单列机会成本，不能仍称严格最优。

这些范围决定已获 owner 批准。当前没有必须由 owner 补充的账号、数据或环境参数。命名、纯函数拆分及容量具体值可留给日后工程判断，但不得增加全局工作流优化或突破安全/兼容边界。

本次修订依据既有 builder-check 的六类问题收敛建议范围，并保留配置错误优先 fail closed、缺历史累计为 null、per-request-context 首次正文不重复计 setup、attempt/组件互斥记账等澄清。这些澄清已包含在用户批准的完整设计中，不构成新增范围。

**审批状态：完整设计及实施计划已获用户批准；本文件按 Task 10 要求单独作本地文档提交。分支仍为 `agentcodec-auto-select`，基线为 `dd117a963486767f7108cb16c6884ac2f92ff09b`，原 `agentcodec-mvp` 不变。最终全分支独立审查另由协调者执行；不合并、不推送。**
