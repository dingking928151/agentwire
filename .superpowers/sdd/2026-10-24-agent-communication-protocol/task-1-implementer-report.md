# Task 1 实施报告：AgentWire v1 contracts

## 结果

Task 1 已完成并提交。实现严格、冻结且禁止额外字段的 AgentWire 契约；只新增计划指定的三个文件，没有开始 Task 2 或修改其他源文件/测试。

提交：`11159a34aaba28b51598ad1902fc66eb94e5dc7f`  
提交信息：`feat: define AgentWire v1 contracts`

## TDD 记录

先写 `tests/test_agentwire_contracts.py`，再运行 RED。首次尝试的完整命令为：

```bash
/workspace/agentcodec/.worktrees/agentcodec-mvp/.venv/bin/python -m pytest tests/test_agentwire_contracts.py -q
```

第一次 RED 显示新模块缺失，但 pytest 将延迟 fixture 的失败计为 setup errors。为排除这类形式上的错误，调整测试代理后以相同命令重跑。最终 RED 代表性输出：

```text
FFFFFF                                                                   [100%]
...
FAILED ... AgentWire contract API is not implemented: dictionary_identity_for
FAILED ... AgentWire contract API is not implemented: ReceiverCapabilities
FAILED ... AgentWire contract API is not implemented: NegotiationResult
FAILED ... AgentWire contract API is not implemented: GatewayDelivery
FAILED ... AgentWire public package is not implemented
6 failed in 0.33s
```

这些是新 API 不存在导致的测试业务失败，不是依赖或环境错误。

实现后，同一 focused 命令通过：

```text
......                                                                   [100%]
6 passed in 0.29s
```

最终实现加上边界断言后，再次运行同一 focused 命令：

```text
......                                                                   [100%]
6 passed in 0.29s
```

随后按要求从目标 worktree 使用既有解释器运行完整项目套件：

```bash
/workspace/agentcodec/.worktrees/agentcodec-mvp/.venv/bin/python -m pytest -q
```

结果：

```text
........................................................................ [ 99%]
.....                                                                    [100%]
1661 passed in 196.54s (0:03:16)
```

未安装或下载依赖。

## 实现内容

- `src/agentcodec/agentwire/contracts.py`：导出严格 `BenchmarkCandidate` 与 `AgentWireReasonCode` Literal；定义 `DictionaryIdentity`、`ReceiverCapabilities`、`NegotiationResult`、`GatewayResult`、`GatewayDelivery` 和 `AgentWireError`。数据模型使用 `strict=True`、`frozen=True`、`extra="forbid"`。版本 tuple 拒绝重复项；unknown version 只保留为显式声明，不被升级或猜测。结果模型检查成功/失败状态与负载/reason 互斥；freeform delivery 仅允许 pass-through，并保留空字符串和 `None` 的区别。
- `dictionary_identity_for` 对 `UserDictionary.model_dump()` 结果递归深拷贝，再以 strict `UserDictionary` schema 重构；canonical JSON 使用 UTF-8、`ensure_ascii=False`、排序键、紧凑分隔符、无末尾 LF。测试以手写 canonical 字节验证 hash，并覆盖完整 alias 集、单 alias 改动、Unicode 与构造后的非法嵌套变异拒绝。SHA-256 限定为 64 位小写十六进制。
- `src/agentcodec/agentwire/__init__.py`：只重导出计划要求的九个 Task 1 公共接口。
- `tests/test_agentwire_contracts.py`：全部使用手写合成数据；覆盖严格/冻结/extra-forbid、重复版本、正容量与负/零容量、unknown version 不猜测、结果不变量、GatewayDelivery 三态、Literal 精确值及错误信息不回显用户数据。

## 自审与工作区

自审完整 staged diff，`git diff --cached --check` 通过。公共包导入链仅引入 `contracts` 和现有 `agentcodec.schema`；新增实现没有 tokenizer、网络/API、文件读写或其他 I/O 导入/调用。实现范围仅包含：

- `src/agentcodec/agentwire/__init__.py`
- `src/agentcodec/agentwire/contracts.py`
- `tests/test_agentwire_contracts.py`

提交后 `git status --short --branch` 显示分支 `agentcodec-agent-communication-design` 干净，唯一未跟踪项为开始时已存在的 `docs/superpowers/plans/2026-10-24-agent-communication-protocol.md`；该计划文件未读取、修改或暂存进提交。没有 push/merge。

## Concern

未发现阻塞或未解决 concern。首次 RED 的 fixture setup error 已通过调整测试延迟导入代理解决；正式 RED 重跑为 6 个预期业务失败。


## Fix round 1

### 审查发现与修复

1. `GatewayDelivery` 的 `decoded` 状态此前只要求 `model_context` 非空，可能接受非 JSON、非完整/无效 `MessageV1` 或非规范 JSON。本轮在 `contracts.py` 中用 stdlib `json.loads`、strict `MessageV1.model_validate`，再以 `json.dumps(message.model_dump(), ensure_ascii=False, separators=(",", ":"))` 重建并与输入精确比较；任何解析/验证失败或格式不一致均拒绝，不会规范化或替换输入。测试确认 canonical MessageV1 JSON 可通过，并拒绝非 JSON、空/错误字段结构、非法字段值、多余空格及 key 顺序变化。
2. `AgentWireError` 对无效 reason code 的 Pydantic `ValidationError` 会回显输入值。本轮捕获验证错误并抛出固定 `ValueError("invalid AgentWire reason code") from None`；有效 reason code 仍按原值保存。测试用 `SENSITIVE_SENTINEL_TEST` 确认异常字符串不含 sentinel。

### TDD 与验证

新增测试后，首次 focused 命令因测试代码中的字符串语法错误在收集阶段失败；修正测试后再次执行，得到有效 RED：

```bash
/workspace/agentcodec/.worktrees/agentcodec-mvp/.venv/bin/python -m pytest tests/test_agentwire_contracts.py -q
```

```text
....FF                                                                   [100%]
2 failed, 4 passed in 0.45s
```

两项失败分别为 `GatewayDelivery` 未拒绝非 canonical 内容（`Failed: DID NOT RAISE ValidationError`）和异常字符串包含 `SENSITIVE_SENTINEL_TEST`；其他测试通过，无导入或环境错误。

实现后的同一 GREEN 命令输出：

```text
......                                                                   [100%]
6 passed in 0.38s
```

提交前完整测试命令：

```bash
/workspace/agentcodec/.worktrees/agentcodec-mvp/.venv/bin/python -m pytest -q
```

```text
........................................................................ [ 99%]
.....                                                                    [100%]
1661 passed in 190.10s (0:03:10)
```

`git diff --check` 与提交前 staged diff 检查均通过；未安装或下载依赖。无 raw corpora 或既有词典数据被读取、复制或输出。

### 提交

提交：`28b91d56bd093780612b12fa3c7ffe94873d0684`  
提交信息：`fix: harden AgentWire result contracts`

本提交仅包含：

- `src/agentcodec/agentwire/contracts.py`
- `tests/test_agentwire_contracts.py`

未 push/merge。提交后工作区除开始时已存在的未跟踪计划文件外干净。


## Fix round 2

### 修复与 TDD

只为无效 `GatewayDelivery` decoded `model_context` 增加合成 `SENTINEL_TEST` 回显断言，并在 `_StrictFrozenModel` 的 `ConfigDict` 中设置 `hide_input_in_errors=True`。此设置只隐藏 Pydantic 验证错误字符串里的输入值，不改变字段或模型校验语义。指定虚拟环境的 Pydantic 版本为 `2.14.1`。

RED 命令：

```bash
/workspace/agentcodec/.worktrees/agentcodec-mvp/.venv/bin/python -m pytest tests/test_agentwire_contracts.py -q
```

失败摘录（原始输出为 pytest 彩色格式）：

```text
....F.                                                                   [100%]
E       AssertionError: assert 'SENTINEL_TEST' not in '1 validatio.../value_error'
E         'SENTINEL_TEST' is contained here:
E           ._CONTEXT_SENTINEL_TEST'}, input_type=dict]
=========================== short test summary info ============================
FAILED tests/test_agentwire_contracts.py::test_gateway_delivery_enforces_decoded_fallback_and_pass_through_states - AssertionError: assert 'SENTINEL_TEST' not in '1 validatio.../value_error'
1 failed, 5 passed in 0.45s
```

GREEN：相同 focused 命令输出 `...... [100%]`，`6 passed in 0.39s`。

完整套件命令 `/workspace/agentcodec/.worktrees/agentcodec-mvp/.venv/bin/python -m pytest -q` 输出 `1661 passed in 194.33s (0:03:14)`。`git diff --check` 与 staged diff 检查均通过；未安装或下载依赖。

### 提交

提交：`1c329e2cfaaef30bc9b13226c0e90b5f92ca176e`  
提交信息：`fix: hide contract validation inputs`

提交只包含 `src/agentcodec/agentwire/contracts.py` 和 `tests/test_agentwire_contracts.py`。既有未跟踪计划文件未修改或暂存；未 push/merge。
