# AgentCodec MVP checklist

## Task 1: Canonical message model and strict wire codec
- [x] Define `MessageV1`, `FallbackRequired`, and deterministic wire codec.
- [x] Write and observe failing tests before production code.
- [x] Pass exact round-trip, escaping, Unicode/newline, invalid enum, unknown protocol, and missing-field tests.
- [x] Commit and pass independent spec/quality review, including fix round 1.

## Task 2: User dictionary isolation and property tests
- [x] Define and validate user-scoped dictionary loading.
- [x] Write and observe failing tests before production code.
- [x] Pass wrong namespace/version, duplicate alias/value, unknown alias, and cross-user isolation tests.
- [x] Pass Hypothesis round-trip tests for Unicode, delimiters, optional values, and exact substitutions.
- [x] Commit and pass independent spec/quality review.

## Task 3: Versioned benchmark dataset and token accounting
- [x] Add 30 synthetic bilingual task messages and baseline renderers.
- [x] Test JSONL loading, token counting via stub, amortization, and fallback cost before implementation.
- [x] Implement deterministic accounting for reuse 1/10/50 and local `o200k_base` / `cl100k_base` estimates.
- [x] Render SamoTech/DRIXL v0.3.0 documented compact syntax locally; do not install the package.
- [x] Run benchmark twice and verify deterministic counts.
- [x] Commit and pass independent spec/quality review.

## Task 4: Offline CLI and user documentation
- [x] Write failing tests for encode/decode/validate/benchmark before CLI implementation.
- [x] Implement file/stdin CLI with non-zero errors and no action execution.
- [x] Add README and SECURITY guidance on isolation, approvals, errors, and benchmark limitations.
- [x] Run CLI help, sample benchmark, and complete test suite.
- [x] Commit and pass independent spec/quality review.

## Final checkpoint
- [x] Independent whole-branch review completed.
- [x] No Critical/Important issues remain open.
- [x] Confirm all work stayed local; no publish, deploy, or real Agent/API integration.


# AC2 experiment checklist

## Task 1: Add opt-in AC2 codec
- [x] Write failing tests for golden payload, all-field round-trip, alias, dispatch, malformed data, and enum behavior.
- [x] Implement separate AC2 encode/decode; preserve existing AC1 functions and bytes.
- [x] Add strict AC1/AC2 wire dispatcher; run focused and full tests.
- [x] Commit and pass independent task review.

## Task 2: Add repetitive corpus and paired benchmark suite
- [x] Write failing tests for suite structure, paired dictionaries, per-field AC1 diagnosis, setup/fallback accounting, and old report compatibility.
- [x] Add 30 stable-ID repetitive messages and repeat-v1 dictionary; verify diverse dataset hash is unchanged.
- [x] Implement separate scenario reports with all seven formats and matched per-scenario dictionary for AC1/AC2.
- [x] Add non-enum field-level percent-escape diagnosis and separate enum shortcode comparison; keep full-wire costs authoritative.
- [x] Run twice for both tokenizers and compare reports; run full tests.
- [x] Commit and pass independent task review.

## Task 3: Expose opt-in CLI and update docs
- [x] Write failing CLI tests for AC1 default, explicit AC2, autodetect decode/validate, strict fallback, and benchmark-suite paths.
- [x] Implement `--format ac1|ac2` (default AC1) and explicit-path benchmark-suite command; preserve legacy benchmark behavior.
- [x] Update README with both corpus comparisons, matched dictionaries, diagnosis and actual results.
- [x] Run examples, deterministic benchmark twice, and full test suite.
- [x] Run final independent whole-branch review; stay on this branch without merge/push.

## Task 4: Close inherited test-coverage suggestions
- [x] Parameterize schema omission rejection across all eleven canonical fields.
- [x] Assert a full, hand-written natural-language baseline literal with distinct values for all eleven fields.
- [x] Assert paired-suite baseline and AC1/AC2 dictionary conditions preserve all fields and dictionary-version selection.
- [x] Demonstrate omission-sensitive failures with temporary mutations; restore production files and keep final diff test-only.
- [x] Run focused and full tests, commit, obtain independent task review, and repeat final whole-branch review; do not merge/push.

## AgentCodec adaptive routing — 2026-10-20（实施计划已批准）

计划：`docs/superpowers/plans/2026-10-20-agentcodec-adaptive-routing.md`。
设计与实施计划均已获用户批准；本 checklist 按实际执行和审查证据更新。保留上面所有历史内容，不重置或替代旧阶段进度。

- [x] 审批门：用户审阅并批准本实施计划；沿用已选择的 subagent-driven development。
- [x] 执行前只读核对：工作区仍为 `/workspace/agentcodec/.worktrees/agentcodec-mvp`、分支 `agentcodec-auto-select`；基线与原 `agentcodec-mvp` ref 为 `dd117a963486767f7108cb16c6884ac2f92ff09b`，保留用户未提交文件。
- [x] 为本计划建立独立 SDD 台账；不读写其他计划台账，不覆盖旧 checklist。

### Task 1：严格契约与单实例边界
- [x] 编写合成失败断言，按计划记录 RED，再实现契约/深快照并记录 GREEN。
- [x] 验证默认 json/ac1、opt-in 词典/AC2、record-only/零 retry、同实例 N/容量/类型/范围拒绝（Review Focus 4）。
- [x] 仅显式列出文件本地提交；新独立审查者通过规格与质量检查。

### Task 2：固定候选与严格等价域
- [x] 记录 RED→GREEN：五候选严格 round-trip、十一字段原样、allowlist/接收能力。
- [x] router 不改 dictionary_version；null/非空域分开，显式词典冲突优先拒绝，JSON 不绕过（Review Focus 1、4）。
- [x] 本地提交并通过独立审查（含 scoped test-only fix re-review）。
- [x] Checkpoint A：Task 1–2 与 codec/AC2/evaluation formats 回归通过，旧 golden/ref 不变。

### Task 3：资源快照、历史与显式确认
- [x] 记录 RED→GREEN：资源身份/epoch、历史去重及冲突、不完整历史为 null。
- [x] proposal 不等于 ready；仅调用方成功确认且 epoch 匹配才生成新快照；ready context 仍需计量（Review Focus 2、3）。
- [x] 本地提交并通过独立审查（Approved，无 Critical / Important / Minor findings）。

### Task 4：离线精确计量与互斥事件账本
- [x] 记录 RED→GREEN：完整请求计数、前缀增量可负、attempt/组件守恒、共享资源/首次 context 不重计（Review Focus 2）。
- [x] 无/坏/替换缓存和 surrogate 不下载、不估算；固定完整文本/LF/profile 并独立复计（Review Focus 5）。
- [x] 本地提交并通过独立审查（Spec Compliance PASS；Task quality APPROVE WITH NITS；无 Critical/Important，1 项 Minor 已登记供最终审查）。

### Task 5：完整重复成本与严格 break-even
- [x] 记录 RED→GREEN：remaining/spent/lifecycle、完整 setup/context/payload、生产 R=F=0。
- [x] 独立整数枚举核对严格 break-even、相等/两侧/无交点/优势消失，N 有效性不足 cannot_compare（Review Focus 3）。
- [x] 本地提交并通过独立审查（Spec Compliance PASS；Task quality APPROVE WITH NITS；无 Critical/Important，1 项 Minor 已登记供最终审查）。
- [x] Checkpoint B：Task 3–5 与旧 benchmark 回归通过，已 provision 词表齐备；缺环境只能报告阻塞。

### Task 6：单消息选择器与无法比较时保留
- [x] 记录 RED→GREEN：同域/同状态/同实例 N 独立枚举最低成本、零阈值、确定性 ties。
- [x] 显式阈值严格大于才切，minimum/机会成本透明；配置错误优先；无法计量仅合法原格式可保留（Review Focus 1、3、4）。
- [x] 固定零发送/零 retry/无自动 fallback/无自动 ready；本地提交并通过独立审查（Approved，无 Critical / Important / Minor）。容量拒绝保留为显式拒绝，不折叠为 cannot_compare。

### Task 7：独立离线固定场景及有界故障模拟
- [x] 记录 RED→GREEN：固定路径/概率、最多三 attempt，失败首次 W 不抹除、响应/context 不漏不重。
- [x] 固定余项继续/停止，资源不满足则拒绝不重路由；单实例与整组次数分开、0 仅 dry-run、非平稳不外推。
- [x] 本地提交并通过独立审查（初审 2 项 Important 已 TDD 修复；scoped re-review 均 ADDRESSED，无新 breakage）。
- [x] Checkpoint C：Task 6–7 与旧 evaluation 回归通过（314 项），无 workflow/global 或生产恢复入口。

### Task 8：独立 adaptive benchmark 与验收矩阵
- [x] 记录 RED→GREEN：新版本报告、source/transport/资源/allowlist 分域，corpora 不合并、默认脱敏。
- [x] 本地固定矩阵覆盖两组各30/两词表/五固定候选与 adaptive/消融/冷热/context/1/N与整组0/1/10/50/固定故障；输入 hash/顺序不变。
- [x] 独立全文复计0差、零阈值 regret0、未知 null、同 revision 两次字节复现；本地提交并通过独立审查（两项 Important 已修复；scoped re-review PASS）。clean gate 固定覆盖检查通过；一条 Minor 留给最终整支审查。

### Task 9：独立 CLI、安全摘要与显式本地输出
- [x] 记录 RED→GREEN：select-encoding/benchmark-adaptive 独立 opt-in、future0/1→总1/2、默认只脱敏摘要。
- [x] 显式本地 payload 文件、严格配置/限额/输出预检、失败清理、不泄漏原文/词条/hash/路径（Review Focus 5）；FIFO/特殊文件在打开前拒绝。
- [x] 本地提交 `7dd8c931` 并通过独立审查（APPROVED WITH MINOR FOLLOW-UP，无 Critical/Important；import-boundary 建议留给最终整支审查）。
- [x] Checkpoint D：Task 8–9 与旧 CLI/evaluation CLI 回归通过（284 项），不更改旧 wire/默认/输出规则。

### Task 10：兼容守卫、文档与最终审查
- [x] 文档验收先 RED，补 README/SECURITY 再 GREEN；保留旧 AC1 负结果和全部旧报告/default/SDK 兼容。
- [x] 完整测试、合成 CLI 示例、真实本地矩阵和同 revision 复现完成；不得安装/联网补齐环境，不以 skip/stub 冒充完成。
- [x] 对应文件和已批准设计/计划/checklist 按计划分开显式本地提交，不 git add .。
- [x] 新独立审查者检查整个基线到最终 HEAD 的分支；复核五项 Review Focus 及设计全条款。唯一 Important（祖先 symlink loop 导致 CLI traceback 泄露调用路径）已 TDD 修复并 scoped re-review 通过；剩余 4 项 Minor 均为非阻塞建议，留档。
- [x] 最终核对原 agentcodec-mvp ref 未改、当前仍 agentcodec-auto-select；保持本地未合并，无 push/发布/部署/API/凭据/原始语料外发。

Task 10 实现证据：5 focused / 1,648 full passed；随后 final-review Important fix 后 1,655 full passed。真实本地矩阵 120 组中 64 个非 dry clean 组通过、15,840 clean 位置全等及 regret 0；同 revision 产物复现。最终全分支独立审查发现并要求修复 symlink-loop path leak，fix commit `7538e2b` 经 7 项 RED→GREEN 回归和 scoped re-review 通过。Final reviewer 留下 4 项非阻塞 Minor。诊断偏差：首轮测试解析错误的 traceback 曾回显局部本地合成 fixture；原详细日志已改为脱敏摘要，后续仅展示固定诊断/计数，完整情况见 `task-10-report.md`；未改动或外发原始语料文件。当前 branch 保持未合并/未推送。
