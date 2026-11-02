# AgentWire 真实模型评测设计提案

**状态：待审阅；未批准执行。** 本文仅提出一项未来真实模型评测的设计，不是执行计划，也不是模型调用授权。批准本文（若将来发生）只表示认可设计方向，不授权连接 provider、获取或使用凭据、发送提示词/语料、消耗 API 额度或费用。真实 API 评测须经过本文第 11 节的独立审批门，并取得针对确切请求范围的另行明确授权。

**当前基线：** 指定工作树 `/workspace/agentcodec/.worktrees/agentcodec-agent-communication-design`，分支 `agentcodec-agent-communication-design`，提案基线 HEAD `084a0e0f542959c834d9afb9abed3c50d6457be4`。本文不改产品代码、既有语料或词典，不合并、不推送，不安装依赖，不接入模型或 API，也不发送评测内容。

## 1. 目的、成功标准与非目标

本评测要回答两个彼此独立的问题：第一，模型直接读取 AgentWire 等候选表达时，能否准确恢复消息并理解受限的合成任务；第二，确定性网关解码后，模型能否对规范化消息完成同一只读任务。评测同时记录完整请求和响应的 provider 实际 usage，检验在结果质量不退化时是否存在真实模型输入成本优势。

评测不执行消息中提到的业务动作，不打开证据链接、不调用工具、不改变外部状态，也不把 wire 可逆、模型输出格式有效或 mock 成功当作模型理解证据。不验证真实用户对话、线上安全或一般化能力；不把一组模型、一个版本或一批合成样本的结果外推为普遍结论。研究结论分开报告解码/字段保真、硬约束、任务理解、实际 usage、错误/恢复和延迟，不合成一个总分。

## 2. 两种接收路径，分别解释结果

### 2.1 Model-direct：模型直接读取表达

provider 收到完整的模型请求：固定任务指令、候选格式说明、会话上下文、候选 payload，以及适用时的词典身份和完整可读 glossary。每一项实际发送的文本都计入该请求的 provider usage。若说明或 glossary 每个请求都重发，就每次都计费；不能当作一次性免费 setup。只有确实在请求边界之外预先安装、且不随请求传给模型的内容，才可从模型输入中排除，同时仍须单列其设置/分发成本。

该路径直接检验模型能否从格式中恢复 11 个字段、识别 profile/词典含义并理解任务。遇到未知版本或不透明别名时，正确行为是明确报告无法解读并要求原文/支持版本，而不是猜测字段含义。

### 2.2 Gateway：先在本地严格解码，再交模型

本地网关先检查格式、版本、能力、字段和字典身份；仅完全成功时才把规范化的完整 `MessageV1` 交给下游模型。未知 profile、坏 JSON、字典不匹配或其他解码失败均 fail closed：不调用模型、不传半成品、不自动重发。除非有独立授权，自然语言 fallback 也不自动执行。

对于结构化候选，网关之后的模型上下文是相同的规范化消息，因此它不能证明模型理解了原始短码，也不能把短 wire 的字节/token 数当作模型输入节省。该路径分别报告传输字节、网关解码/字段等值、失败拦截，以及共同规范化输入上的下游模型表现。`natural_language_freeform` 没有固定结构解析器，只能作为明确标记的 pass-through 对照，不进入严格网关解码成功率比较。Gateway 的 provider usage 只记共同的规范化下游控制请求，不归因于某一种压缩候选。

## 3. 候选表达与比较边界

model-direct 主矩阵拟比较当前本地基准支持的九个条件：

1. `natural_language`：固定标签字段的自然语言控制；
2. `natural_language_freeform`：人工撰写并经金标准核对的自由文本对照；
3. `json`：规范字段 JSON；
4. `ac1`：现有 AC1；
5. `ac1_dictionary`：AC1 加显式、配对词典；
6. `ac2`：现有 AC2；
7. `ac2_dictionary`：AC2 加同一配对词典；
8. `awp1`：AgentWire Compact Profile v1，不带词典；
9. `awp1_dictionary`：AWP1 加显式词典身份与精确引用。

不为自然语言或 JSON 虚构词典条件；有词典与无词典结果分开报告。模型直读模式的 profile 说明、枚举表、dictionary glossary 均纳入请求；词典候选只允许使用预先固定的合成词典，且不得把 opaque reference 当作模型天然理解的词义。当前版本的 AC1/AC2/AWP1 和字段契约保持不变；如实现细节与该矩阵不兼容，须先更新设计并单独审阅，不得在评测中静默改协议。

Gateway 主矩阵使用结构化候选做本地解码；下游模型的规范 JSON 控制只运行一次对应的配对任务，不对九种传输格式重复计算同一个模型输入。模型直读与网关结果不混合平均。

## 4. 合成样本、金标准与保护

首轮建议最多使用 30 个逻辑样本：15 个多样化、15 个高重复任务；具体样本从仓库已存在的合成基准中预先挑定或另行制作并审阅，形成只读、带版本和 SHA-256 的 manifest。不得直接取真实用户、客户、公司或线上 Agent 内容，不得含个人信息、真实凭据、私有 URL、真实账户标识或未公开业务信息。样本只用于提示模型完成预定义的文本任务，绝不执行 payload 中的命令或外部动作。

样本应覆盖委派、状态和结果消息；明确否定、日期/时区、金额/上限、审批要求、阻塞状态、缺失证据、空值与 `false`、空字符串、重复列表项、Unicode 和易混淆分隔符。对每个样本预先编写：规范 11 字段 gold、硬约束原子清单、可观察的任务意图/正确只读答复、不能推断的信息，以及模型不得声称已完成的外部动作。另设 6 个合成的畸形/未知 profile 或别名挑战，用于检验 fail-closed；它们与正常 30 个样本分开统计。

自由文本自然语言候选须由人工独立撰写，不可通过目标 codec 自动反向渲染；独立校对员确认表达不增删语义。候选生成后冻结所有样本、gold、提示模板、rubric 和词典快照。真实评测中不修订提示或金标准；发现缺陷则暂停、修订版本并重新审批。

## 5. 配对、重复与随机化

每个 provider、每个正常样本、每个候选条件进行 3 次独立单轮调用。每次都使用新请求、无共享对话历史；不在样本间传递模型输出。三次重复用来估计同一模型/条件的随机波动，不视作 90 个独立样本。每个 provider 另外对 6 个畸形/未知挑战各做 3 次 model-direct 调用，检查拒绝猜测行为；在 Gateway 路径中这些挑战必须由本地拒绝，预期 provider 调用次数为零。

在每个样本的重复区组内随机排列候选调用顺序，并记录固定随机种子、顺序、provider、尝试序号和 UTC 时间。不同 provider 使用同一预注册区组/随机化规则；不根据中途结果调整顺序或提示。provider 内各候选使用相同的模型参数与上下文预算；设 `temperature=0`（若支持），否则使用已记录的最低/固定采样配置。记录 seed、temperature、reasoning/thinking 配置及所有 provider 特有参数；不声称因此保证确定性。建议串行发请求，避免并发和速率限制造成偏差。

## 6. 请求与评分

所有模式使用统一、严格的 `model-eval-output-v1` 输出契约：完整恢复的 `MessageV1`、一份针对固定问题的结构化任务理解答复、对每条硬约束的判断及依据、以及 `protocol_unreadable` 标志。关闭工具调用、函数调用和联网能力。格式错误、缺字段、超出输出上限、拒绝结构化输出或无效 JSON 都按失败记录，不重问、不自动修补。

**硬约束评分。** 对 `task`、`blocker`、`next_step`、`evidence`、`constraints`、`approval_required` 做严格类型和值比较；保留空值、空字符串、空列表、顺序、重复项和原文标点。逐字段报告 exact-match 数/分母、整条消息 11 字段全等率，以及 6 个硬字段全部保留的 case pass rate。对样本中预先标注的否定、金额/日期边界、审批、禁止外发/调用工具/发布等关键约束实行零容忍：漏掉、反转、弱化或臆造任一项即记一次 critical hard-constraint failure，并触发暂停规则。精确匹配是保守保真指标，不等于完整安全证明。

**任务理解评分。** 先用机器检查结构化字段、允许的意图标签和可判定答案；对简短开放答复由两位不知道候选格式和 provider 条件的评审员独立盲评，分歧交第三人裁决。拟用 0–4 分 rubric：4=目标、状态/阻塞、证据需求和下一步均正确且无越权主张；3=非关键细节轻微遗漏、无硬约束错误；2=部分理解但至少遗漏一项重要的非安全要点；1=实质性误解或提出受禁止动作；0=捏造关键事实、反转关键约束或声称完成未执行的外部动作。另报告每项维度、无根据断言和越权主张，不以平均分掩盖单个关键失败。

主要指标按 provider × model revision × 样本组 × 候选 × 模式分别报告分子/分母、初次结果和重复间离散度。聚合与筛选规则预先固定：每个逻辑样本先对三次任务理解分数取算术平均；主要筛选只比较每个 provider 的 `awp1` 与 `natural_language`，以 30 个逻辑样本计算配对差（AWP1−自然语言），按逻辑样本成对 bootstrap 10,000 次取得 95% 区间。只有区间下界不低于 −0.25/4 才通过该筛选，不能只凭点估计通过；三个 provider 分别判定，不合并。其余候选（含 `awp1_dictionary`）及多样/重复子组差异均为探索性描述，不据此作确证性结论。任何候选/provider 出现一次关键硬约束失败即触发暂停规则；零观察失败也不证明现实风险为零。此门槛仅是待审阅的研究筛选规则，不是产品放行标准。

## 7. Provider 实际 usage、模型版本与审计记录

每个请求保存请求 ID、provider、endpoint/API version、请求与响应时间、实际返回的 model ID/revision、可用的 fingerprint、参数、HTTP/API 状态和完整实际 usage 字段。不得以本地 tokenizer 估计代替 provider usage；provider usage 缺失时标 `unknown`，不得记零。

- OpenAI：记录 `input_tokens`、`output_tokens`、`total_tokens`、输入缓存明细与输出 reasoning 明细（字段存在时）；参考 [Responses API usage](https://developers.openai.com/api/reference/cli/resources/responses/methods/create/)。
- Anthropic：记录 `usage.input_tokens`、`usage.output_tokens`、缓存读取/写入 usage（响应提供时）及对应 request ID；参考 [Messages usage](https://platform.claude.com/docs/en/build-with-claude/working-with-messages)。
- Gemini：记录 `usageMetadata` 中的 prompt、candidate、cached content、tool-use prompt 及其他相关计数，并记录输出上限；参考 [GenerateContent API](https://ai.google.dev/api/generate-content)。本方案禁用工具，若 usage 仍出现相关计数须原样记录并查明。

分别报告输入、输出、缓存、reasoning/候选 token、响应字符、延迟、重试与回退使用量。对每次 API attempt 以及每个完整候选条件汇总实际 usage。费用按发起前再次核对的公开单价估算，并与 provider 控制台/账单核对；公开 token usage 不等于最终账单金额。不得把输入缓存折扣、系统提示缓存或响应遗漏当作免费。

候选模型暂定为 OpenAI `gpt-6-luna`、Anthropic `claude-haiku-5-5`、Google `gemini-3.5-flash-lite`，仅作为方案预算示例；模型别名、快照、参数支持和价格须在独立审批前重新核实。来源包括 [OpenAI models](https://developers.openai.com/api/docs/models) 与 [pricing](https://developers.openai.com/api/docs/pricing)、[Anthropic models](https://platform.claude.com/docs/en/models/overview) 与 [pricing](https://platform.claude.com/docs/en/about-claude/pricing)、[Gemini models](https://ai.google.dev/gemini-api/docs/models) 与 [pricing](https://ai.google.dev/gemini-api/docs/pricing)。若不能固定不可变 revision，必须记录实际返回模型标识、fingerprint、时间及请求参数，并将复现性标为受限；不得悄悄以其他模型替换。

## 8. 重试、回退与停止条件

主矩阵不对语义错误、格式错误或低分答案重试，不做“再解释一次”的修复提示；每个主条件以第一次有效 provider 响应为首要结果。对明确未发送的传输失败，或 provider 明确返回的可重试 429/5xx，最多重试一次；本轮每家 provider 最多使用 45 个技术重试额度。超时但无法确定服务端是否已处理的请求视为可能已收费，不自动重试，先核查 request ID/usage；每次尝试均占用 call cap，并单独计量。上述重试额度仅为预算硬上限，不构成 API 调用授权，只有真实调用审批明确包含该重试规则后才可使用。

本轮完全不执行自然语言 fallback：Gateway 的 decode failure 必须零 provider 调用；model-direct 的未知/畸形输入挑战也不触发重发。未来若要研究 fallback，须另写预注册设计、重算请求/费用上限并取得新的明确授权，不得使用本轮技术重试额度。达到硬约束关键失败、模型版本不匹配、任何真实/敏感数据意外混入、provider usage/成本异常、响应含工具执行迹象、请求上限或费用停止线时，立即停止该 provider 的后续调用并封存已有结果，不自动换模型或绕过停止规则。

## 9. 建议预算上限

预算按**每次请求**的保守上限估算：最多 4,096 input tokens 和 512 output tokens；超过任一上限的请求在发送前拒绝。每个 provider 的主矩阵最多 30 × 9 × 3 = 810 次请求；另含 6 × 3 = 18 次 model-direct 畸形/未知挑战、30 × 3 = 90 次 Gateway 规范输入控制，以及最多 45 次技术重试，总计不超过 963 次 API 请求/provider。Gateway 本地失败挑战不产生 API 请求；本轮 fallback 请求数固定为零。任何新增候选、样本、重复或 fallback 均须重新设计、重算并另行授权，不能从技术重试额度中扣用。

按研究备忘录所列短上下文标准价估算（OpenAI 与 Anthropic：$0.10/百万输入、$0.50/百万输出；Gemini Flash-Lite：$0.30/百万输入、$2.50/百万输出），963 次均达到 token 上限时，未加预留的估算分别约为 $0.641、$0.641、$2.416；三家合计约 $3.698。加 30% 价格/usage 误差预留后约 $4.807。建议总硬上限为 **US$5.00**，分 provider 先设支出/请求限制，再由总预算停止线约束；若 provider 侧无法建立足以落实该上限的预算控制，或当时价格/条款使上界超过 $5.00，则不启动真实调用。上述价格只是公开网页的方案估算，不是报价、授权或实际费用承诺；发起审批时必须刷新价格并重新核算。不得使用免费层、训练/改进数据分享的 opt-in 计划，或任何会扩大数据用途的试用条件。

## 10. 数据隐私、保留与访问

即使数据完全合成，真实评测仍会把请求内容发送给第三方 provider；在独立授权前必须给出即将外发的样本、提示、协议说明、词典及 hash 清单。评测运行不得包含真实工作区文件内容或隐私/业务数据；API key 不写入仓库、manifest、日志或报告。只用经审阅的合成样本和必要的最小提示，禁止联网、工具调用和跨请求历史。

公共政策摘要指出：OpenAI API 内容默认不用于训练，但滥用监控日志通常可保留最长 30 天，ZDR/Modified Abuse Monitoring 需符合资格并启用；见 [OpenAI data controls](https://developers.openai.com/api/docs/guides/your-data)。Anthropic API 数据未经明确许可不会用于训练，但保留依功能而异，ZDR 仅适用于已启用且符合资格的功能；见 [Anthropic API retention](https://platform.claude.com/docs/en/manage-claude/api-and-data-retention)。Gemini 付费 API 的产品改进使用规则与日志设置取决于 tier/API 路径，滥用监控可能保留提示、上下文和输出最多 55 天，标记内容可能被审阅；见 [Gemini logs policy](https://ai.google.dev/gemini-api/docs/logs-policy) 和 [usage policies](https://ai.google.dev/gemini-api/docs/usage-policies)。这些政策须在运行前按实际账号、endpoint、tier 和开关重新确认，不从公开概述推定用户项目设置。

建议本地原始 prompt/response 与逐次 trace 仅在受限目录保留 30 天，完成评分与审计后删除；不提交、不推送、不上传到共享项目。只保留不含消息内容的版本化 manifest 与聚合指标；不以内容 hash 替代删除。任何更长本地留存、provider ZDR/定制 retention 要求或扩大语料范围，都必须在运行审批中明确决定。若某 provider 的实际保留政策不可接受，则从本轮矩阵移除该 provider，并重新计算预算与可比较范围。

## 11. 分阶段审批门：设计审阅不等于 API 授权

- **当前门（已授权）：** 只撰写并保存本提案。不得调用任何模型/API，也不得发送样本。
- **设计审阅门：** 用户可审阅本文并要求修改。即使用户认可本文，也仅表示接受方案草案；不代表批准修改产品代码或实施任何调用。
- **实施计划门：** 若设计获批，先另写本地评测实现计划，并等待单独批准。计划、代码、mock 测试和任何本地样本准备均不自动授权网络或 API。
- **真实调用审批门（必须另行明确）：** 在任何连接/请求之前，另行展示并由用户明确批准：精确 provider/endpoint 与模型版本、请求参数、语料和提示版本/hash、发送字段与词典、数据保留/训练控制、每调用 token 上限、调用次数和并发、重试/fallback 规则、币种与总/分 provider 硬预算、停止条件及开始时段。批准必须清楚表达为对该确切真实 API 评测的授权；不从“批准文档”“继续”“实施计划已批”推断授权。若任何实质条件改变，停止并再次申请批准。

## 12. 待用户审阅决定

本文默认建议 30 个平衡合成样本、九个 model-direct 候选、三个暂定 provider、每条件三次独立重复、最多 US$5.00 总预算及 30 天本地原始 trace 留存。这些都是提案者建议，不是用户已选定的执行范围。进入后续实施/调用审批前，仍需用户决定是否接受或修改：

1. 首轮是否采用 30 个样本的平衡子集，还是要求覆盖已有全部多样/高重复合成语料；
2. 是否保留三家暂定 provider/model，或改为更少/不同的具体模型版本；
3. 是否接受九个候选和三次重复，以及 US$5.00 上限、最多 45 次技术重试（不含 fallback）与 30 天原始数据保留建议；
4. 是否接受硬约束零容忍，以及仅以每个 provider 的 `awp1` 对自然语言主比较、按配对 bootstrap 区间下界 −0.25/4 筛选的规则。

这些问题可以在设计审阅时调整；逐项确定后仍需第 11 节所述独立真实调用授权。

## 13. 本地设计依据

本文承接已审阅的 Agent 间协议设计、已完成的 AWP1 profile 说明及仓库现有 AgentWire 离线基准契约。已有离线 mock 只验证本地管线，不代表模型理解；它不授权接入 provider。此前公开来源摘要记录了模型、价格、usage 与保留政策的参考链接，发起任何真实评测前应刷新核对。
