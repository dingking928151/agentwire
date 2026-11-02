# Task 3 完成报告：gateway 与 model-direct 文本构造

## 状态

已按批准范围完成实现、自审并在本地分支提交。没有调用真实模型/API/服务，没有获取凭据、外发数据、安装或下载依赖，也没有修改既有 parser、codec、schema 或 benchmark 文件。

## 实现

新增 `src/agentcodec/agentwire/gateway.py`：

- `decode_at_gateway` 只调用严格 AWP1 decoder；成功只返回完整 `MessageV1`，失败只返回固定 reason code，不回显 payload。
- `deliver_benchmark_at_gateway` 由显式 candidate ID 选择解析路径。复用现有 `parse_input` 处理严格的 labelled natural language、JSON、AC1、AC2；只对 `awp1` / `awp1_dictionary` 使用 AWP1 decoder。
- `natural_language_freeform` 仅原样 pass-through，不解析，也不生成规范 `model_context`。其他候选在格式错误或词典身份不匹配时 fail-closed；不切换 decoder、不自动重试、不执行消息。
- 所有成功的结构化或 labelled-field 解析结果都输出完整、紧凑、同字段顺序的规范 `MessageV1` JSON context。

新增 `src/agentcodec/agentwire/prompts.py`：

- `build_direct_model_request` 每次都拼入调用方传入的完整 `context_prefix` 和 payload；为 JSON、AC1、AC2、AWP1 提供固定格式说明，自然语言两种候选不附加 compact-protocol 说明。
- AWP1 说明包含完整的两张枚举表；显式词典候选附上完整、可读的所选 glossary。非词典候选传入词典或词典候选缺少词典时统一拒绝，错误文本不含用户 payload 或 glossary。
- 只构造本地文本，不创建 client、不发送请求，也不声称模型理解或任务成功。

新增 `tests/test_agentwire_modes.py`，使用手写合成数据覆盖完整/错误 AWP1、renderer-labelled round-trip、freeform 不解析、标签顺序与缺失拒绝、候选显式解析、AC1/AC2/AWP1 词典身份检查、无自动 fallback/执行、共同任务提示、固定格式说明、完整 glossary 及自然语言不附协议图例。

## 验证

- RED：新增接口尚未实现时，focused 测试按预期因 gateway/prompts API 缺失而失败。
- GREEN：`tests/test_agentwire_modes.py` 最终 **12 passed**。
- 全套测试：**1684 passed**（198.02 秒）。
- 对三个新增文件执行 `compileall`；`git diff --check` 与 staged diff 检查均通过。
- 提交仅包含本任务三个目标文件；原有未跟踪计划文件保持未改动、未提交。

## 提交

`8950d41b5bae240bc579e8b86eca3604d7802177` — `feat: add local AgentWire receive modes`

## 注意事项

目标工作树没有简报指定的 `.venv/bin/python`，默认 `python3` 也未安装 pytest。为遵守“不安装/下载依赖”，focused 与全套测试使用同仓库 sibling worktree 现存的虚拟环境解释器运行（pytest 9.1.1、pydantic 2.14.1）。除此环境路径差异外，未发现阻塞项。


## Fix Round 1：绑定 model-direct AWP1 payload 与词典 identity

### 修复

针对独立审查的 Important finding，`_selected_dictionary_snapshot` 现在保留 canonical `DictionaryIdentity`；所有显式词典 model-direct 请求均以固定 JSON 数组形式写入完整 identity `[user_id, dictionary_version, sha256]`，并继续附上完整 glossary。对于 `awp1_dictionary`，请求文本构造前使用现有 `decode_awp1` 做纯本地严格校验：AWP1 envelope identity 必须与所选词典的完整 identity（包括 SHA-256）相同；拒绝路径统一抛出固定 `direct model request rejected`，不包含 payload 或 glossary。其他 candidate 的显式选择规则未变。未修改 `gateway.py`、codec、parser 或 Task 1/2 文件。

### TDD 与验证

- **RED：** 新增/调整合成回归测试后运行 `/workspace/agentcodec/.worktrees/agentcodec-mvp/.venv/bin/python -m pytest tests/test_agentwire_modes.py -q`。实现前结果为 **3 failed, 11 passed**：失败分别表明所有 dictionary candidate 请求缺少完整 identity、匹配的 AWP1 请求未输出 identity、hash 不同的 AWP1 payload 未被拒绝；与审查发现一致。
- **GREEN：** 同一 focused 命令最终 **14 passed**。覆盖匹配 identity 成功、同 user/version 但 alias 内容不同导致 hash 不匹配并拒绝、完整 identity 在各字典候选请求中可读，以及拒绝错误不泄露 payload/glossary。
- **全套：** `/workspace/agentcodec/.worktrees/agentcodec-mvp/.venv/bin/python -m pytest -q` — **1686 passed**（195.33 秒）。
- **自审：** `git diff --check` 与 staged diff 检查通过；diff 仅涉及下列两个 Task 3 文件，未改 `gateway.py` 或既有 parser/codec。未安装/下载依赖，未连接网络、模型或外部服务。

### 本轮文件与提交

- `src/agentcodec/agentwire/prompts.py`
- `tests/test_agentwire_modes.py`
- Commit: `f396e28e3d0a8a2ac8b8b0030c48519431ec1fba` — `fix: bind AWP1 requests to selected dictionary identity`
