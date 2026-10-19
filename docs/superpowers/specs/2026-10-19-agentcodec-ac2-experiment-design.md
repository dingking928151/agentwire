# AgentCodec AC2 与高重复语料实验设计

## 意图

第一版基准显示，现有 AC1 的 percent-escaped 字段格式在 30 条多样化样本上比紧凑 JSON 更贵。希望找出可量化的格式膨胀原因，并验证一种尽量贴近 JSON 原生编码、且可选用户词典的替代格式，在高重复工作流中是否能抵消初始化成本。

这是一次离线格式实验，不预设它一定省 token。既有多样化数据用于普适性对照；新增的重复语料单独报告，不能与原语料合并后掩盖差异。

## 目标

- 保持 AC1 的现有 wire 输出、行为和兼容性不变。
- 新增实验格式 AC2，使用 `AC2|` 前缀和紧凑 JSON 数组，固定沿用 `FIELDS` 的 11 个字段顺序。
- `message_type`、`status` 仍使用 AC1 现有的一字母枚举码；其他字段使用标准 JSON 类型和值，不再对每个 JSON 值做 percent-encoding。
- AC2 可对 `task`、`blocker`、`next_step`、`evidence`、`constraints` 中的完整字符串作用户词典精确替换，表示沿用 `{\"a\": code}`；不匹配部分子串，不替换 ID、枚举或 user/dictionary metadata。
- 保持现有 30 条多样化语料不变，另建 30 条高重复合成语料，重复长约束、任务模板和常用短语。新增语料需覆盖否定、金额/日期边界、审批字段、Unicode 和边界标点。
- 两组语料分别比较 compact JSON、精简自然语言、DRIXL 参考格式、AC1、AC1+本组词典、AC2、AC2+同一本组词典；这样 AC1/AC2 的差异不会被词典覆盖率混淆。多样化语料使用现有 demo-v1 词典，重复语料使用专门的 repeat-v1 词典。按 `o200k_base` 和 `cl100k_base` 分报 payload、协议/词典 setup、fallback、复用 1/10/50 的总量和摊销量。
- 对 AC1 原因作字段级诊断：对每条样本的非枚举字段，分别统计 compact JSON 值和 AC1 percent-escaped JSON 值在两种 tokenizer 下的 token 数及差值；另报 UTF-8 字节中被 percent-escape 的比例。对 `message_type`、`status`，单独列 compact JSON literal 与 AC1 一字母枚举码成本，标为枚举缩短，不误称 percent-encoding。字段独立统计仅用于解释来源，不代替完整 wire 实际 token 数，因为边界 tokenization 可能非加和。
- 继续报告逐字段 round-trip 和 critical-field loss。若 AC2 没有在重复语料中同时以两种 tokenizer 胜过 compact JSON，照实报告，不宣称节省。
- 将新编码格式加入离线 SDK/CLI 的显式选择；默认编码仍为 AC1。解码可按 `AC1` / `AC2` 前缀选择对应严格解码器，未知或畸形输入 fail closed。
- 更新 README 解释 AC2 是实验格式，且现有单次 benchmark 结论不能外推为实际 API 账单或模型任务成功率。

## 设计决定

- AC2 线格式为 `AC2|<compact JSON array>`，数组长度和顺序必须严格对应 `FIELDS`。未被枚举缩码或词典替换的字段使用规范 MessageV1 的 JSON 原生值；字符串保留精确内容，`ensure_ascii=False`，无无意义空格。仅分隔第一个 `|`，JSON 内部不得再做 percent-encoding。
- AC2 与 AC1 共用 `ENUMS`、`FIELDS`、`CONTENT_FIELDS`、`MessageV1` 和 `UserDictionary`。解码先验证前缀、数组长度、枚举码和完整规范 schema，再作精确别名扩展；未知/冲突/多余数据返回现有 `FallbackRequired`。
- AC1 的既有 `encode_message` 默认值和 payload 不变。增加独立明确的 AC2 SDK 函数；CLI `encode --format ac1|ac2` 默认 `ac1`，`decode` / wire `validate` 依据前缀严格派发，AC1 与 AC2 都保留支持。
- 新的重复语料使用单独版本化 JSONL 与人工编写的 `repeat-v1` 词典。每组中 AC1 与 AC2 使用完全相同、版本固定的词典做无词典/有词典配对；词典条目完整纳入 setup token 成本。原始 30 条数据不更改、不拼接进新数据以冲淡结果。
- AC1 成本归因在无词典替换条件下，按每个 canonical 字段并列展示 compact JSON 字面值与 AC1 percent-escaped JSON 字面值，并单独展示两种枚举的 JSON literal 与短码成本；这样诊断隔离 percent-encoding 与 enum 编码，不把词典替换算入。突出中文、Unicode、分隔符和常见标点等输入如何改变编码成本。每字段分别报告样本 token 增量均值与中位数。percent-escape 字节比例定义为序列化 JSON 值 UTF-8 字节中被编码成 `%HH` 的字节数除以该值原始 UTF-8 字节数。保留完整消息端到端 token 计数作为结论依据。
- Benchmark 报告应按场景隔离结果，并明确各场景使用哪个词典；相同语料、字段值及复用假设在不同格式间保持等价。系统说明和词典 setup 都计入最终总量。
- 新格式、语料和报表版本都显式标版本；benchmark 多次运行须产生可复现的等价报告。实验数据保存在仓库 `bench/`，CLI 的 `benchmark-suite` 显式接收两组 JSONL 路径和重复语料词典路径，避免依赖 wheel 是否携带仓库外文件。具体 API 结构和最小必要报告改动留给实现者，但不可隐式更改旧 report 语义。
- 编码只处理消息数据，绝不解释、执行或授权消息内容。无真实 Agent、在线模型 API、服务端、网络调用、发布、部署或自动词典学习。
- 所有工作留在 `/workspace/agentcodec/.worktrees/agentcodec-mvp` 的 `agentcodec-mvp` 本地分支；用户明确说先不要合并，禁止 merge/push/publish。

## 验收标准

- AC1 的现有 golden wire 输出和全部已有行为继续不变。
- AC2 对所有 11 个必需字段进行确定性 round-trip；有效 Unicode、空/空值、列表、分隔符、换行、字面 `{\"a\":...}`、未知 enum/version/alias、错位/多余字段及畸形 JSON 都有测试。
- 词典仅替换获准内容字段的完整精确词条；未知用户/版本/alias fail closed；绝不静默丢弃否定、约束或审批信息。
- 既有 30 条 diverse 数据保留不动；另 30 条 repetitive 数据有稳定 ID 和单独摘要。两组分别对比七种表达，AC1/AC2 在每组中必须使用相同字典作配对；两组均用两种 tokenizer，setup/fallback/复用成本都可审计且重复运行稳定。
- AC1 开销诊断包含非枚举字段 compact JSON vs percent-escaped 值 token 差额、percent-escape 字节占比，以及枚举码相对 JSON literal 的单独对照；诊断字段数之和不得冒充完整 payload 成本。
- 研究结论按实测数字陈述。zero critical loss 和 100% codec round-trip 是基础门槛；是否实际节省 token 是实验结果，不可作为测试通过条件或预先保证。
- CLI 新旧命令均离线、不执行解码文本；`encode` 未指定格式时仍输出 AC1。
- TDD 测试先红后绿；完整测试套件通过；独立任务审查和全分支最终审查通过。

## 非目标

- 不改为 AC2 默认、不弃用 AC1，不训练 tokenizer/模型。
- 不连接 A2A、MCP、真实 Agent 或任何协作平台，不测模型理解质量、API 计费或端到端 Agent 成功率。
- 不从真实用户数据挖掘或自动生成用户词典；重复语料与词典均为公开仓库中的合成样例。
- 不根据 benchmark 结果自动调参或隐去不利结果。
