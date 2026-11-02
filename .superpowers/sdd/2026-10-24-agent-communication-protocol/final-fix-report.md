# AgentWire 最终修复波次报告

## 已完成

1. **Gateway freeform 对照现在只比较传输与 setup 成本。** Source domain 的 gateway 候选会与人工撰写的 `natural_language_freeform` baseline 比较 transport token 差值及 setup break-even；该路径不使用 model-input、response 或 end-to-end 成本。Freeform pass-through 自身仍将这些模型侧和端到端字段保持为 null。若 tokenizer、历史或任一所需传输成本未知，则比较状态为 `cannot_compare`，不把未知当作零。
2. **`--modes` 在基准运行前生效。** CLI 将选定模式传给 runner；runner 只执行所选模式，默认仍包含 `model_direct` 与 `gateway`，且保留原有行顺序。单次 runner 调用内按 tokenizer profile 和完整事件文本签名复用已测 token 结果，减少 reuse=1/10/50 的重复计数；事件 multiplicity 仍独立累计，缓存不跨 runner 调用复用。
3. 更新了 AgentWire 成本文档，明确 gateway freeform 对照的范围和限制。

## 验证结果

- **TDD RED：** 新 benchmark 回归测试在旧实现上按预期失败（4 failed）；CLI 模式传递测试也因 runner 收到默认全模式而失败（1 failed）。
- **定向 GREEN：** `tests/test_agentwire_benchmark.py tests/test_agentwire_costs.py`：40 passed；`tests/test_agentwire_cli.py`：17 passed。新增覆盖包括 transport delta、setup break-even、未知成本、runner 前置模式限制及 reuse 文本计数复用。
- **默认缓存集成：** 使用仓库已有的 `o200k_base` 与 `cl100k_base` tokenizer 缓存运行未裁剪的默认 CLI；运行时主动封锁 socket 网络连接。命令成功，耗时 **353.54 秒**，报告含 **84 个 scenarios、4,536 行**，覆盖两种模式、两种 tokenizer 和 reuse counts 1/10/50；临时输出目录随后清理。未输出或保留任何原始夹具内容。
- **全仓测试：** `pytest -q`：**1,746 passed in 200.66s**。
- 修改文件通过 `py_compile`；`git diff --check` 通过。
- **独立只读 scoped review：** 未发现 Critical 或新增 Important。审查提出的唯一验收缺口是默认缓存集成尚无完成证据；上述 353.54 秒实测已补齐该证据。

## 范围与状态

仅改动批准 AgentWire 工作树中的 benchmark、costs、CLI、对应测试和成本说明文档。本报告及进度记录也位于该工作树。未修改或披露 corpus/dictionary 夹具内容；未安装/下载依赖、访问网络/外部服务或使用凭据。未提交、合并或推送。

目标分支仍为 `agentcodec-agent-communication-design`，HEAD 保持在基线 `16ffb917d973fe748c68c2c1877b80edfa401e25`。预先存在的未跟踪计划文件保持未修改、未暂存。


## 交接补记：commit 与 scoped re-review

- **本地 commit：** `084a0e0f542959c834d9afb9abed3c50d6457be4`（`fix: close final AgentWire benchmark review findings`）。commit 的路径恰为六个获准文件：`docs/agentwire-compact-profile-v1.md`、`src/agentcodec/agentwire/benchmark.py`、`src/agentcodec/agentwire/costs.py`、`src/agentcodec/cli.py`、`tests/test_agentwire_benchmark.py`、`tests/test_agentwire_cli.py`。预先存在的未跟踪计划文档没有暂存或提交。
- **独立 scoped re-review 范围：** reviewer 在提交前只读审查了工作树相对基线 `16ffb917d973fe748c68c2c1877b80edfa401e25` 的本轮 diff，重点检查 gateway transport-only 比较、模式前置筛选、reuse 计数缓存和测试行为断言；没有查看或输出夹具内容。
- **Finding 1 — ADDRESSED（reviewer verdict）。** Reviewer 确认 source-domain gateway freeform baseline 使用传输差值及 setup break-even，未知成本 fail-closed，模型输入/response/end-to-end 字段保持不可用；相关回归断言存在。
- **Finding 2 — NOT ADDRESSED（reviewer 当时的 verdict）；现有实现/证据状态 — ADDRESSED。** Reviewer 确认 `--modes` 已前置传入、默认仍含双模式且 reuse 文本缓存为调用内限定，但当时因尚无完整缓存默认运行证据，将此 finding 保留为未闭环。该 review 之后，我们实际完成了默认双 tokenizer、双模式、全 reuse workload 的离线 CLI 运行：exit 0，353.54 秒，84 scenarios、4,536 行。因此缺失的验收证据现已补足；**reviewer 没有在该运行后再次复审**，不把这次后续验证表述为 reviewer 的二次 verdict。
- **reviewer 是否运行测试：** reviewer 结果没有报告测试运行证据，也没有运行默认缓存 CLI；将该 scoped review 记录为只读代码审查，不把它计作测试通过。实现验证由本任务单独完成：benchmark+costs 40 passed、CLI 17 passed、完整 `pytest -q` 1,746 passed in 200.66s；另有真实默认缓存 CLI 完成记录见上文。
- commit 后未修改代码或测试；报告只作本地补记，未纳入该 commit。未推送、合并或重新派发 reviewer。
