# Task 5 实施报告：AgentWire 离线配对 benchmark

## 状态与范围

Task 5 已完成并提交到 `agentcodec-agent-communication-design` 分支。实现新增 benchmark API、20 条人工撰写的合成消息和 10 个短合成字典短语，以及对应测试。source 与 selected-dictionary transport 保持分域；成本测量复用既有 codec、gateway、direct request builder、renderer/parser、CostEvent meter 与 break-even API，未修改 Task 1–4 合同。

未知请求文本、tokenizer、协议/字典历史或复用单位不会被当作零成本；source-domain freeform 在 gateway 仅报告 transport 指标。报告只含匿名 namespace hash，不输出消息/字典正文或原始 user/scope/scenario 标识。

## 验证

RED 阶段使用既有 sibling worktree 的解释器（目标 worktree 没有 `.venv`）：

```text
/workspace/agentcodec/.worktrees/agentcodec-mvp/.venv/bin/python -m pytest tests/test_agentwire_benchmark.py -q
```

结果：15 项失败，原因是预期中的 benchmark 模块/API 尚未实现；测试收集和运行正常。

最终目标测试：

```text
/workspace/agentcodec/.worktrees/agentcodec-mvp/.venv/bin/python -m pytest tests/test_agentwire_benchmark.py -q
```

结果：22 passed in 2.81s。

最终完整仓库测试：

```text
/workspace/agentcodec/.worktrees/agentcodec-mvp/.venv/bin/python -m pytest -q
```

结果：1719 passed in 220.76s（3分40秒）。另外，`python -m compileall -q src/agentcodec/agentwire/benchmark.py tests/test_agentwire_benchmark.py`、20 条 JSONL/10 个短短语 fixture 校验及 `git diff --check` 均通过。

## 离线与文件边界

全程未调用模型、provider API、外部服务、凭据或网络；完整测试使用本机已有 tokenizer 缓存。未安装/下载依赖。未读取、复制或披露既有原始 corpus/dictionary 内容；fixtures 均为本任务手写的合成内容。没有启动 Task 6，也没有派发 reviewer/subagent。

仅提交了：

- `src/agentcodec/agentwire/benchmark.py`
- `bench/agentwire/exchanges.v1.jsonl`
- `bench/agentwire/dictionary.v1.json`
- `tests/test_agentwire_benchmark.py`

预存未跟踪计划文档 `docs/superpowers/plans/2026-10-24-agent-communication-protocol.md` 未修改、未暂存、未提交。本报告按要求写入 SDD 路径，未纳入代码提交。

## Commit

- Hash：`b2058d98811b3b56f191dd3b8c27c3076a06a1bf`
- Message：`feat: benchmark AgentWire modes offline`

## Concerns

无未解决 concerns。报告中的 tokenizer 计数是本地文本估算，不是 provider 账单；严格 decoder equality 仅验证结构恢复，不代表模型理解或任务质量。


# Fix round 1

## Findings addressed

Gateway structured decode failure now keeps the fully known failed request/transport attempt in its event, request-token, and transport-byte/token counts, while model input/output, response/retry/fallback, distinct-total, per-use, amortized and lifecycle cost values are null. The row is `cannot_compare`; baseline cost delta and break-even cannot be reported. Comparison also explicitly requires successful gateway strict decode for structured rows. A cold protocol state or selected dictionary exposure with `history_complete=False` now propagates unknown history, nulls necessary history/setup/lifecycle costs, and blocks comparison rather than assuming zero.

## RED → GREEN

RED command:

```text
/workspace/agentcodec/.worktrees/agentcodec-mvp/.venv/bin/python -m pytest tests/test_agentwire_benchmark.py -q -k 'gateway_decode_failure or cold_protocol_setup_with_incomplete_history or cold_dictionary_exposure_with_incomplete_history'
```

Result: `3 failed, 22 deselected in 1.10s`, as expected. Each case exposed the prior erroneous `exact_local_text` status for a gateway decode failure, incomplete cold protocol history, or incomplete cold dictionary history; there were no fixture or collection errors.

GREEN, same focused command: `3 passed, 22 deselected in 0.58s`.

Focused file:

```text
/workspace/agentcodec/.worktrees/agentcodec-mvp/.venv/bin/python -m pytest tests/test_agentwire_benchmark.py -q
```

Result: `25 passed in 2.09s`.

Full suite:

```text
/workspace/agentcodec/.worktrees/agentcodec-mvp/.venv/bin/python -m pytest -q
```

Result: `1722 passed in 213.93s (0:03:33)`. `git diff --check` and `git diff --cached --check` passed.

## Scope, offline check, and commit

Only these code/test paths changed in fix round 1:

- `src/agentcodec/agentwire/benchmark.py`
- `tests/test_agentwire_benchmark.py`

This section was appended to the existing report as requested and was not included in the commit. The existing plan document remains untouched and untracked. All tests used the pre-existing sibling Python environment; no dependency installation/download, model/API/network/external-service call, merge/push, or Task 6 work occurred.

Fix commit: `fedf0c422a08721b9241bc4a32eedf243a89d469` — `fix: fail closed on incomplete AgentWire costs`. The commit contains only the two paths listed above.

## Concerns

No unresolved concerns for the two findings. No retry or synthetic fallback is performed by this benchmark path; gateway decode failures therefore remain non-comparable, while known failed-attempt text cost is retained.
