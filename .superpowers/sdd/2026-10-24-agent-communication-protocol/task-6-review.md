# Task 6 independent review

Review range: `fedf0c422a08721b9241bc4a32eedf243a89d469..e700ad543a560fc244b7ac71c6c05f6b04868878`; package `review-fedf0c4..e700ad5.diff`.

## Spec compliance

基本符合：新增独立 opt-in 命令，旧 CLI/defaults 保持；固定 fixture、参数 allowlist、output directory 的拒绝覆盖保护、按需加载和 tokenizer 本地缓存表现符合 brief。实现者报告的无缓存 `cannot_compare`/离线路径符合预期。

## Important findings

1. **CLI report 输出 input-derived hashes。** `src/agentcodec/cli.py:514-531` 将 scenario/rows 直接输出，其中包含 `source_hash`、`transport_hash`（benchmark 字段：`benchmark.py:208-209,238-243`；hash 函数会包括 namespace 与 dictionary identity digest：`benchmark.py:300-322`）。这违反 brief “不输出输入 hash”。修复：按公开字段 allowlist 构造 JSON，排除哈希及原始标识；加递归回归测试确保 hash key/value 不出现在输出。

2. **CLI 的复用参数只过滤最终报告，不限制实际运行。** `src/agentcodec/cli.py:496-510` 总是为 scenarios 提供全量次数，benchmark 运行后才在 `cli.py:514-524,552-556` 过滤 rows；`--instance-uses 1 --corpus-passes 1` 仍计算全部 1/10/50。它会让明确缩小请求不能缩短昂贵的 tokenizer 运行，尤其 cache 可用的完整报告路径已超时。修复：将两组用户所选次数分别传到对应 reuse scenario，先缩小要运行的条件再调用 benchmark；加测试验证 runner 实际收到所选范围，而非只检查输出。

## Cannot verify

- tokenizer cache 可用时的完整 CLI 报告测试超时无结果，不可称为通过；review 未重跑。
- 未查看 raw fixture 内容，不能独立确认其合成性或 manifest/content 对应性。
- 实现者报告的 CLI 15、旧 CLI 174、全套 1737 passed 未独立复验。

## Strengths

Typer opt-in command 解耦、冷导入无 tokenizer/network、严格参数校验、输出目录拒绝覆盖与独占写入策略合理；测试覆盖了固定输入、输入拒绝、复用单位、路径保护、冷导入与无 tokenizer 场景。

## Verdict

Task quality: With fixes / Needs fixes. Task 7 remains blocked until both Important findings receive TDD fixes and fresh scoped re-review. Full exact-tokenizer report path remains unverified; no Task 7 work should start before Task 6 closure.
