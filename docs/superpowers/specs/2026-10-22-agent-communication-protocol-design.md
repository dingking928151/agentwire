# Agent 间紧凑通信协议设计提案

**状态：待用户审阅；未批准实施。** 这是设计稿，不是实现计划或模型评测结果。当前没有修改产品代码、语料、词典或依赖，也没有连接任何模型、API 或外部服务。

1. 工作区：`/workspace/agentcodec/.worktrees/agentcodec-agent-communication-design`
2. 本地分支：`agentcodec-agent-communication-design`
3. 提案基线：`04fd206acb68d59d29018d128a40ec9c99ec547f`（已完成并审查的本地自适应分支 HEAD）

## 1. 想解决的问题

Agent 之间频繁传递委派任务、状态、阻塞、下一步、证据和约束时，自然语言会重复字段名称、固定状态和工作流用语。希望有一种公开、固定、可逆的短协议，减少可避免的结构开销，同时让接收方能检查版本、按精确规则解码，并在不认识协议时回到原始自然语言。

这里有两个不同的主张，不能混为一谈：

1. **机器可以无损解码。** 离线测试可以证明完整字段往返一致、未知输入会拒绝或触发回退，也可以用本地 tokenizer 测文本 token 数。
2. **模型能理解并正确完成任务。** 只有把协议交给真实接收模型做有 gold 标注的任务测试，才能开始验证。当前完全没有这类证据；编码器能解码，不代表模型直接看短码时懂它。

首版只提议围绕任务委派和进度回报，复用仓库现有严格 `MessageV1` 作为规范消息，不改旧 AC1 默认或已有 AC2 wire。若用户批准这份设计，之后再单独写实施计划；计划获批前不写实现。

## 2. 建议路线：公开协议，不造私密暗语

建议新增一个有明确版本、文档和固定字段顺序的“AgentWire Compact Profile v1”（暂定 wire 前缀 `AWP1|`）：

- 字段位置和枚举表公开、稳定、可审计；普通实现者不需要猜暗号。
- 自由文本仍是原始字符串；本协议不做摘要、同义改写或隐式删除否定、金额、日期、权限、审批、证据和限制。
- 可选用户词典只能按明确 user namespace、词典版本和内容 hash 选择；不自动学习、不跨用户搜寻、不根据相似度猜测。
- 未知协议/枚举/别名或字典身份不匹配时，一律不部分解码、不猜测。保留源自然语言，由调用方决定是否走自然语言路径。
- 任何状态、`approval_required`、词典码或“接收方支持协议”的声明都不是执行授权，也不是身份认证。

现有 AC2 已用固定顺序 JSON 数组降低字段名重复。新提案的增量不是把 AC2 冒充模型语言，而是把它定位成一个有明确版本、接收能力协商、可选字典身份和回退规则的互通 profile。最终实验必须把 AC2 本身保留作对照，证明新 framing/词典值得增加，而非预先假设更省。

## 3. 两种接收模式，成本和结论分开

### 3.1 确定性网关解码

发送方输出短协议；接收网关验证 profile 和字典后，精确解码成规范消息，再交给接收 Agent。此模式可验证无损和传输文本大小，但如果网关把完整自然语言/JSON 又放进模型上下文，就不能据短 wire 得出模型 input token 减少。它可能减少通道字节，也可能仍增加模型上下文，必须分项报告。

### 3.2 模型直接读取短协议

接收模型直接看到短 profile，并需在提示中获知字段位置、枚举和已选词典词义。这是“Agent 是否彼此理解短协议”的关键实验路径，但完全未验证。协议说明、会话协商、字典 glossary、完整上下文、模型输出、重试及自然语言回退都要计入每次实际输入/输出成本；不能把说明只算一次，却在 API 每个请求里重新发送。

只有第二种模式在完整请求输入仍然更少、且任务结果不退化时，才能支持“真实 Agent 对话有 token 节省”的结论。离线阶段不得作出这项结论。

## 4. 暂定 wire 结构与字段

建议使用 `AWP1|` 加一个严格 JSON 二元组：

```text
AWP1|[dictionary_identity_or_null, message_values]
```

`message_values` 必须严格按 `MessageV1` 的固定顺序提供全部 11 项：

```text
[message_id, message_type, task, status, blocker, next_step,
 evidence, constraints, approval_required, user_id, dictionary_version]
```

例如（仅合成演示）：

```text
AWP1|[null,["m42","D","Review the local report.","P",null,"Return a summary",[],["Do not publish."],true,"team-a",null]]
```

- `message_type` 使用固定短枚举，如 `delegate→D`、`status→S`、`result→R`；`status` 使用另一张独立固定码表。每张表带 profile 版本，不根据相似词推断。
- `null`、`false`、空串、空列表、列表顺序及重复项都保持原样，不省略、不默认补值。
- JSON 字符串转义负责逗号、括号、引号、换行和 Unicode；不能用按标点切分的脆弱文本 parser。不得将用户原文拆成不带转义的 `|` 分段。
- `dictionary_identity_or_null` 是 `null` 或 `[user_id, dictionary_version, sha256]`。hash 是 canonical dictionary 的内容标识，不是认证、授权或匿名化。接收方须验证消息里的 `user_id` / `dictionary_version` 和本地选中的 dictionary identity 一致。
- 多条记录由外层 transport 明确分帧；只有外层约定的 LF 用于区分记录，消息内部的 Unicode 内容不据此切行。

### 4.1 词典替换边界

仅允许对事先批准字段中的**完整字符串值或完整列表项**作精确替换，使用不与合法字符串冲突的类型化引用，例如 `{"$ref":"code"}`。不替换 ID、枚举、布尔值、字段名或部分子串；解码时必须按精确 user/version/hash 查表并展开为原字符串。

- 网关模式下，精确替换仍可逐字段证明无损，但其文本 token 优势只适用于短 wire/通道；网关展开后交给模型的成本另计。
- 模型直读模式下，opaque `$ref` 本身没有可推断语义。要么把完整可读 glossary 提供给接收模型并将其成本重复计入，要么不在该模式启用词典。不得因为本地 decoder 知道含义，就宣称模型也知道。
- 对关键约束文字、否定、预算、日期和审批要求，默认保留完整原句。不在离线版本里用缩写重新表达语义；若使用精确别名，网关解码后须与原句字节级/字段级一致，模型直读的可理解性另行评测。

## 5. 接收方兼容、协商与 fail-closed

在发送短协议前，调用方应取得接收方对以下内容的显式能力声明：支持的 `AWP` profile/schema 版本、固定枚举表、最大消息尺寸，以及（若启用字典）用户 namespace、dictionary version 和完整 hash。能力声明是互通条件，不是模型理解保证。

接收端行为建议如下：

1. 先检查前缀、profile 版本、数组长度、JSON 结构和字段严格类型；不接受多余、缺失或重复的 object key。
2. 再检查 enum、dictionary namespace/version/hash、别名是否全部有效；禁止模糊匹配、跨用户 lookup 或只恢复部分字段。
3. 完整解码后验证 canonical `MessageV1`，逐项比较全部字段；只有全成功才返回 decoded message。
4. 未知 profile、能力不足、字典缺失/冲突、坏 hash、未知引用或任一字段错误都返回固定的 `fallback_required`/reason code，payload 为空，不执行消息，不自动将半成品交给 Agent。
5. 调用方可以显式选自然语言 fallback。若真正向模型发出过短协议，fallback 是额外一条自然语言尝试，成本要把短协议、自然语言重发和相关输出/重试都计入；不能当成替换或免费恢复。

源自然语言必须由发送方/调用方另行保留，不能仅靠解码失败的短 payload 自己恢复。fallback 的发送策略不是本设计批准的执行能力；离线 benchmark 只模拟并记账。

## 6. 词典同步、隔离和安全

- 词典人工提供并按 user namespace 与版本隔离；若由一组 Agent 共用，须显式声明共享范围和每个接收端实际持有的版本/hash。
- 变更生成新版本和新内容 hash；旧版本保留或明确失效，不能静默覆盖。接收能力声明过期或不匹配即回退。
- 每个会话的可用词典只能由调用方明确选择；不得以 message 中声明的 `user_id` 作为身份认证。hash 用于发现版本不一致，不防篡改，也不能代替签名/安全通道。
- 初始阶段不存储真实 Agent 对话、不收集真实用户语料、不自动学习词典；只用合成样例或已授权的本地既有合成基准。
- 消息与词典都按不可信数据处理。内嵌指令、证据链接、命令、审批状态不授权工具执行；本协议只解析与传递数据。

## 7. 完整成本、setup 摊销与 break-even

基准要在相同逻辑消息、相同接收任务、相同模型/Tokenizer profile 下配对比较，不把不同语料、tokenizer、metadata 域或冷/热条件混成一个平均节省率。对协议候选 `p`，报告：

```text
C_p(N) = protocol/grammar setup
       + receiver capability negotiation
       + dictionary distribution/setup
       + Σ_i complete request_i
       + Σ_i complete response_i
       + retry and fallback attempts
```

基线自然语言也在完全相同边界内统计。`request_i` 必须包括实际发送的系统提示、协议说明/字段表、上下文、消息 wrapper 和 payload；若 API 每次都重发历史或说明，不能按一次 setup 计费。协议、词典或协商若通过真正 out-of-band 机制传递，只能在那个单独的传输指标中排除，不能在模型 token 指标里凭空视为免费。

计量方法：

- 离线阶段按完整 ordinary text 送入本地 tokenizer；不同 tokenizer 分开报告，不能用字符、字节或另一 tokenizer 补估。
- 统计首用、固定重复同一实例、整组重复三种单位时，把它们明确区分。只有字节完全相同的同一消息可作为单实例复用，变量变化的任务不能冒充重复消息。
- setup 与每次请求用真实 boundary 分开；同一消息各候选计完整上下文/封装，不把字段独立 token 数简单相加。
- 把协议说明、字典同步、所有尝试、拒绝、重试、natural-language fallback 以及接收模型响应纳入总量；同时单列首次成本和摊销成本。
- break-even 是最小 `N`，使累计完整候选成本严格低于同边界的自然语言成本；若任何必要 tokenizer/上下文/复用信息未知，给 `unknown/cannot_compare`，不造零成本或通用结论。

报告只可称为本地 tokenizer 的文本成本估计。真实服务的消息包装、系统提示复用、缓存计费、输出 token 和 API 用量须在另获批准后用 provider 实际 usage 单独核对。

## 8. 离线能测什么、不能测什么

### 离线可测

- 严格解析、字段数量/类型、编码确定性、unknown version/alias 行为。
- 编码再解码后 11 字段全等率、关键字段全等率、硬约束是否完整保留。
- 接收能力/字典版本不匹配是否 fail-closed、回退是否保留源原文、失败尝试成本是否完整记账。
- 在固定 tokenizer 下各格式的完整文本 token/字节成本，按语料、字段分布、字典冷/热和复用次数分别报告 setup 与 break-even。
- 同 revision、同输入、同配置的可复现性；结构错误及回退路径的自动测试。

### 离线不能证明

- 真实模型是否看懂字段位置、短枚举、dictionary reference 和未显式复述的否定/约束。
- 两个不同模型之间的语义等价、Agent 任务完成率、安全行为是否相同。
- provider 实际 token framing、缓存、计费、延迟、成功率或线上可用性。

因此离线阶段只回答“可无损编码/解码”和“本地 tokenizer 下的文本成本”；不得把字段往返通过说成模型理解，也不得把 token 数下降等同于 Agent 彼此交流已省时、省钱或省真实 API token。

## 9. 独立的后续模型评测门槛（本提案未授权）

只有在本设计获批并完成本地方案后，另写设计、取得针对性批准，才能开展真实跨模型理解试验。新批准至少要明确：

- 每个 provider/endpoint、精确 model/version 和输入输出方向。
- 将外发的语料、prompt、协议说明、词典、namespace 与可能含个人/业务信息的字段；保留/训练政策和日志位置。
- 币种硬费用上限、每请求 token 上限、请求量/并发/重试限制及立即停止条件。
- 任务金标准、blind scoring、关键约束零丢失门槛、容许的误解/回退标准及比较基线。

此前不获取/请求凭据、不连接服务、不发送任何消息、不花费任何费用。旧的离线授权不自动扩展到这一阶段。

## 10. 提案边界与待审点

本稿建议复用现有 `MessageV1` 与严格 codec 基础，新增独立 `AWP1` 通信 profile，不变更 AC1/AC2 旧行为；候选比较至少包括自然语言、compact JSON、AC1、AC2、AWP1 和显式字典条件，并把 gateway 与 model-direct 两个接收模式分开。具体测试集分组与字段、实现 API、CLI 和任务拆分应在用户批准本设计后另写计划。

请先审这份设计。实现计划和任何产品代码都先不动；若同意本文方向，下一步再由我单独拟实施计划。
